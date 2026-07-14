from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Protocol

from .models import BackendCandidate, RecallPolicy


class MemoryBackend(Protocol):
    def find_memory_id(self, evidence_id: str) -> str | None: ...
    def add_event(self, text: str, timestamp: Any, metadata: dict[str, Any]) -> str: ...
    def create_relationships(self, memory_ids: list[str]) -> None: ...
    def persist(self) -> None: ...
    def recall(self, query: str, policy: RecallPolicy) -> list[BackendCandidate]: ...


class UnavailableMemoryBackend:
    """Safe sink used when MAGMA or its embedding model cannot initialize."""

    @staticmethod
    def _raise() -> None:
        raise RuntimeError("memory_backend_unavailable")

    def find_memory_id(self, evidence_id: str) -> str | None:
        self._raise()

    def add_event(self, text: str, timestamp: Any, metadata: dict[str, Any]) -> str:
        self._raise()

    def create_relationships(self, memory_ids: list[str]) -> None:
        self._raise()

    def persist(self) -> None:
        self._raise()

    def recall(self, query: str, policy: RecallPolicy) -> list[BackendCandidate]:
        self._raise()


class RealMagmaBackend:
    """Private boundary around unmodified MAGMA objects."""

    def __init__(self, persist_dir: str | Path, upstream_dir: str | Path | None = None):
        root = Path(__file__).resolve().parents[1]
        upstream = Path(upstream_dir) if upstream_dir else root / "upstream" / "MAGMA"
        sys.path.insert(0, str(upstream)) if str(upstream) not in sys.path else None
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        previous_key = os.environ.get("OPENAI_API_KEY")
        if previous_key is None:
            os.environ["OPENAI_API_KEY"] = "adapter-import-placeholder-not-a-secret"
        try:
            from memory.graph_db import TraversalConstraints
            from memory.trg_memory import TemporalResonanceGraphMemory
        finally:
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
        self._constraints_type = TraversalConstraints
        self.persist_dir = Path(persist_dir)
        self.trg = TemporalResonanceGraphMemory(
            llm_backend=None,
            embedding_model="minilm",
            persist_dir=str(self.persist_dir),
        )
        graph_path = self.persist_dir / "graph.json"
        if graph_path.exists():
            self.trg.graph_db.load(str(graph_path))

    def find_memory_id(self, evidence_id: str) -> str | None:
        for node_id, node in self.trg.graph_db.nodes.items():
            if getattr(node, "attributes", {}).get("evidence_id") == evidence_id:
                return node_id
        return None

    def add_event(self, text: str, timestamp: Any, metadata: dict[str, Any]) -> str:
        return self.trg.add_event(text, timestamp=timestamp, metadata=metadata)

    def create_relationships(self, memory_ids: list[str]) -> None:
        existing = {
            (link.source_node_id, link.target_node_id, link.link_type.value, link.properties.get("entity"))
            for link in self.trg.graph_db.links.values()
        }
        for memory_id in memory_ids:
            node = self.trg.graph_db.get_node(memory_id)
            if node is None:
                continue
            for link in self.trg._create_entity_edges(node):
                identity = (link.source_node_id, link.target_node_id, link.link_type.value, link.properties.get("entity"))
                if identity not in existing:
                    self.trg.graph_db.add_link(link)
                    existing.add(identity)

    def persist(self) -> None:
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.trg.graph_db.save(str(self.persist_dir / "graph.json"))
        self.trg.vector_db.save(str(self.persist_dir / "vectors"))

    def recall(self, query: str, policy: RecallPolicy) -> list[BackendCandidate]:
        constraints = self._constraints_type(
            max_depth=policy.max_graph_depth,
            max_nodes=policy.max_nodes,
            follow_temporal=True,
            follow_semantic=True,
            follow_causal=True,
        )
        context = self.trg.query(query, max_results=min(policy.top_k, policy.max_nodes), constraints=constraints)
        scores = context.metadata.get("search_scores", [])
        candidates = []
        for index, node in enumerate(context.anchor_nodes):
            timestamp = getattr(node, "timestamp", None)
            candidates.append(BackendCandidate(
                text=getattr(node, "content_narrative", ""),
                timestamp=timestamp.isoformat() if timestamp else None,
                score=float(scores[index]) if index < len(scores) else None,
                metadata=dict(getattr(node, "attributes", {})),
            ))
        return candidates
