from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ._recall_execution import _execute_fixed_recall
from .entity_consolidation import EntityCandidate
from .models import BackendCandidate, RecallPolicy


class MemoryBackend(Protocol):
    def find_memory_id(self, evidence_id: str) -> str | None: ...
    def add_event(self, text: str, timestamp: Any, metadata: dict[str, Any]) -> str: ...
    def create_relationships(self, memory_ids: list[str]) -> None: ...
    def list_entity_candidates(
        self, *, limit: int,
    ) -> tuple[EntityCandidate, ...]: ...
    def persist(self) -> None: ...
    def resolve_target_entity_ref(self, query: str) -> str | None: ...
    def recall(
        self,
        query: str,
        policy: RecallPolicy,
        target_entity_ref: str | None = None,
    ) -> list[BackendCandidate]: ...


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

    def list_entity_candidates(
        self, *, limit: int,
    ) -> tuple[EntityCandidate, ...]:
        self._raise()

    def persist(self) -> None:
        self._raise()

    def resolve_target_entity_ref(self, query: str) -> str | None:
        return None

    def recall(
        self,
        query: str,
        policy: RecallPolicy,
        target_entity_ref: str | None = None,
    ) -> list[BackendCandidate]:
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
            from memory.graph_db import EventNode, NodeType, TraversalConstraints
            from memory.trg_memory import TemporalResonanceGraphMemory
        finally:
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)

        class _EventOnlyTemporalTrgMemory(TemporalResonanceGraphMemory):
            """Lumina-owned boundary: temporal linking consumes EVENT nodes only.

            Pinned upstream ``_create_temporal_links`` sorts every graph node
            by timestamp, so a non-temporal entity node would steal an event's
            temporal predecessor (shadow evidence:
            ``docs/experiments/entity_temporal_boundary/``). With the current
            all-EVENT production graph this override is behavior-identical to
            upstream. The pinned upstream source stays unmodified.
            """

            def _create_temporal_links(self, event_node):
                # Imported lazily so controlled test doubles that stub
                # ``memory.graph_db`` with event-only symbols stay valid.
                from memory.graph_db import Link, LinkSubType, LinkType

                all_nodes = [
                    node
                    for node in self.graph_db.nodes.values()
                    if node.node_type == NodeType.EVENT
                ]
                all_nodes.sort(
                    key=lambda n: n.timestamp if n.timestamp else datetime.min
                )
                for i, node in enumerate(all_nodes):
                    if node.node_id == event_node.node_id and i > 0:
                        prev_node = all_nodes[i - 1]
                        precedes_link = Link(
                            source_node_id=prev_node.node_id,
                            target_node_id=event_node.node_id,
                            link_type=LinkType.TEMPORAL,
                            properties={
                                'sub_type': LinkSubType.PRECEDES.value,
                                'time_delta': (event_node.timestamp - prev_node.timestamp).total_seconds()
                            }
                        )
                        self.graph_db.add_link(precedes_link)

                        succeeds_link = Link(
                            source_node_id=event_node.node_id,
                            target_node_id=prev_node.node_id,
                            link_type=LinkType.TEMPORAL,
                            properties={
                                'sub_type': LinkSubType.SUCCEEDS.value,
                                'time_delta': (event_node.timestamp - prev_node.timestamp).total_seconds()
                            }
                        )
                        self.graph_db.add_link(succeeds_link)

                        self.stats['links_created'] += 2
                        break

        self._constraints_type = TraversalConstraints
        self._event_node_type = EventNode
        self._node_type = NodeType
        self.persist_dir = Path(persist_dir)
        self.trg = _EventOnlyTemporalTrgMemory(
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
        self._create_subject_entity_ref_links(memory_ids)
        self._create_mention_entity_ref_links(memory_ids)

    def _create_subject_entity_ref_links(self, memory_ids: list[str]) -> None:
        """Find-or-create graph-only EntityNodes and canonical REFERS_TO edges.

        For each event carrying a non-null ``subject_entity_ref`` (written by
        the unchanged write-side classifier), ensure one EntityNode with
        deterministic id ``entity:<ref>`` exists and add a single
        ``Event --ENTITY/REFERS_TO(role=subject)--> EntityNode`` edge. The
        EntityNode is created directly in the graph — never through
        ``add_event`` — so it is not vector-indexed and, per the EVENT-only
        temporal boundary, never joins the temporal chain. Both the node and
        the edge are deduplicated, so ingestion retry converges without
        duplicates. Shadow evidence:
        ``docs/experiments/entity_composite_e2e/``,
        ``docs/experiments/context_role_entityref/``.
        """
        for memory_id in memory_ids:
            node = self.trg.graph_db.get_node(memory_id)
            if node is None:
                continue
            attributes = getattr(node, "attributes", None)
            if not isinstance(attributes, dict):
                continue
            ref = attributes.get("subject_entity_ref")
            if not isinstance(ref, str) or not ref.strip():
                continue
            ref = ref.strip()
            surface = attributes.get("subject_entity_surface")
            surface = surface.strip() if isinstance(surface, str) else ref
            self._ensure_entity_refers_to_link(
                memory_id, ref=ref, surface=surface, role="subject",
            )

    def _ensure_entity_refers_to_link(
        self,
        memory_id: str,
        *,
        ref: str,
        surface: str,
        role: str | None,
    ) -> None:
        """Find-or-create the graph-only EntityNode for ``ref`` and add the
        ``Event --ENTITY/REFERS_TO--> EntityNode`` edge exactly once.

        ``role="subject"`` marks the canonical subject edge; ``role=None``
        writes a generic mention edge with no ``role`` key, keeping the two
        disjoint. The EntityNode is created directly in the graph — never
        through ``add_event`` — so it is not vector-indexed and, per the
        EVENT-only temporal boundary, never joins the temporal chain. Both
        node and edge are deduplicated, so ingestion retry converges.
        """
        # Imported lazily so controlled test doubles that stub
        # ``memory.graph_db`` with event-only symbols stay valid.
        from memory.graph_db import (
            EventNode,
            Link,
            LinkSubType,
            LinkType,
            NodeType,
        )

        entity_node_id = f"entity:{ref.casefold()}"
        if self.trg.graph_db.get_node(entity_node_id) is None:
            self.trg.graph_db.add_node(EventNode(
                node_id=entity_node_id,
                node_type=NodeType.ENTITY,
                content_narrative="",
                attributes={
                    "entity_ref": ref,
                    "canonical_surface": surface,
                },
                embedding_vector=None,
            ))
        properties = {"sub_type": LinkSubType.REFERS_TO.value}
        if role is not None:
            properties["role"] = role
        already_linked = any(
            link.source_node_id == memory_id
            and link.target_node_id == entity_node_id
            and link.link_type == LinkType.ENTITY
            and link.properties.get("sub_type") == LinkSubType.REFERS_TO.value
            and link.properties.get("role") == role
            for link in self.trg.graph_db.links.values()
        )
        if not already_linked:
            self.trg.graph_db.add_link(Link(
                source_node_id=memory_id,
                target_node_id=entity_node_id,
                link_type=LinkType.ENTITY,
                properties=properties,
            ))

    def _create_mention_entity_ref_links(self, memory_ids: list[str]) -> None:
        """Find-or-create graph-only EntityNodes and generic REFERS_TO edges
        for event mention refs.

        For each event carrying ``mention_entity_refs`` (always written by
        ``_formed_event_metadata`` for every grounded-formation event), add one
        role-less ``Event --ENTITY/REFERS_TO--> EntityNode`` edge per
        mentioned ref. A mention ref equal to the event's
        ``subject_entity_ref`` is skipped: the role=subject edge already
        covers that pair. Mention edges carry exactly
        ``{"sub_type": LinkSubType.REFERS_TO.value}`` — no ``role`` key — so
        they stay disjoint from the subject edge and from
        ``list_entity_candidates``' role=subject projection. EntityNodes are
        graph-only (never vector-indexed, never on the temporal chain), and
        both node and edge are deduplicated, so ingestion retry converges.
        Shadow evidence: ``docs/experiments/multi_entity_recall_gain/``.
        """
        for memory_id in memory_ids:
            node = self.trg.graph_db.get_node(memory_id)
            if node is None:
                continue
            attributes = getattr(node, "attributes", None)
            if not isinstance(attributes, dict):
                continue
            mention_refs = attributes.get("mention_entity_refs")
            if not isinstance(mention_refs, list):
                continue
            mention_surfaces = attributes.get("mention_entity_surfaces")
            if not isinstance(mention_surfaces, list):
                mention_surfaces = []
            subject_ref = attributes.get("subject_entity_ref")
            subject_ref = (
                subject_ref.strip()
                if isinstance(subject_ref, str) and subject_ref.strip()
                else None
            )
            for index, raw_ref in enumerate(mention_refs):
                if not isinstance(raw_ref, str) or not raw_ref.strip():
                    continue
                ref = raw_ref.strip()
                if subject_ref is not None and ref == subject_ref:
                    continue
                raw_surface = (
                    mention_surfaces[index] if index < len(mention_surfaces) else None
                )
                surface = (
                    raw_surface.strip()
                    if isinstance(raw_surface, str) and raw_surface.strip()
                    else ref
                )
                self._ensure_entity_refers_to_link(
                    memory_id, ref=ref, surface=surface, role=None,
                )

    def list_entity_candidates(
        self, *, limit: int,
    ) -> tuple[EntityCandidate, ...]:
        """Project bounded persisted entity state for write-side consolidation."""
        if limit <= 0:
            return ()
        from memory.graph_db import NodeType

        candidates: list[EntityCandidate] = []
        entity_nodes = sorted(
            (
                node for node in self.trg.graph_db.nodes.values()
                if getattr(node, "node_type", None) == NodeType.ENTITY
            ),
            key=lambda node: getattr(node, "node_id", ""),
        )
        for entity_node in entity_nodes:
            attributes = getattr(entity_node, "attributes", {})
            if not isinstance(attributes, dict):
                continue
            ref = attributes.get("entity_ref")
            surface = attributes.get("canonical_surface")
            if (
                not isinstance(ref, str) or not ref.strip()
                or not isinstance(surface, str) or not surface.strip()
            ):
                continue
            candidates.append(EntityCandidate(ref.strip(), surface.strip()))
            if len(candidates) == limit:
                break
        return tuple(candidates)

    def persist(self) -> None:
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.trg.graph_db.save(str(self.persist_dir / "graph.json"))
        self.trg.vector_db.save(str(self.persist_dir / "vectors"))

    def resolve_target_entity_ref(self, query: str) -> str | None:
        """Deterministic exact-surface lookup over persisted EntityNodes.

        Returns the single distinct ``entity_ref`` whose graph-only
        EntityNode ``canonical_surface`` is contained in the query; 0 hits or
        hits mapping to more than one distinct ref (two Alexes) return None —
        never guess. No LLM, embedding, fuzzy, alias, or coreference
        matching. Several nodes may share one ref defensively (find-or-create
        dedups by ``entity:{ref.casefold()}``); the distinct-ref set handles
        both shapes.
        """
        if not isinstance(query, str) or not query.strip():
            return None
        # Imported lazily so controlled test doubles that stub
        # ``memory.graph_db`` with event-only symbols stay valid.
        from memory.graph_db import NodeType

        hits: set[str] = set()
        for node in self.trg.graph_db.nodes.values():
            if getattr(node, "node_type", None) != NodeType.ENTITY:
                continue
            attributes = getattr(node, "attributes", None)
            if not isinstance(attributes, dict):
                continue
            surface = attributes.get("canonical_surface")
            ref = attributes.get("entity_ref")
            if not isinstance(surface, str) or not surface.strip():
                continue
            if not isinstance(ref, str) or not ref.strip():
                continue
            if surface.strip() in query:
                hits.add(ref.strip())
        if len(hits) == 1:
            return next(iter(hits))
        return None

    def recall(
        self,
        query: str,
        policy: RecallPolicy,
        target_entity_ref: str | None = None,
    ) -> list[BackendCandidate]:
        context = _execute_fixed_recall(
            trg=self.trg,
            constraints_type=self._constraints_type,
            event_node_type=self._event_node_type,
            node_type=self._node_type,
            query=query,
            policy=policy,
            target_entity_ref=target_entity_ref,
        )
        scores = context.metadata.get("search_scores", [])

        def to_candidate(node: Any, score: float | None) -> BackendCandidate:
            timestamp = getattr(node, "timestamp", None)
            return BackendCandidate(
                text=getattr(node, "content_narrative", ""),
                timestamp=timestamp.isoformat() if timestamp else None,
                score=score,
                metadata=dict(getattr(node, "attributes", {})),
            )

        candidates: list[BackendCandidate] = []
        anchor_ids: set[str] = set()
        for index, node in enumerate(context.anchor_nodes):
            node_id = getattr(node, "node_id", None)
            if isinstance(node_id, str) and node_id:
                if node_id in anchor_ids:
                    continue
                anchor_ids.add(node_id)
            score = float(scores[index]) if index < len(scores) else None
            candidates.append(to_candidate(node, score))

        expansion_hops: dict[str, int] = {}
        for path in getattr(context, "traversal_paths", ()) or ():
            if (
                not isinstance(path, (list, tuple))
                or not path
                or not all(
                    isinstance(node_id, str) and bool(node_id.strip())
                    for node_id in path
                )
                or path[0] not in anchor_ids
            ):
                continue
            for hop, node_id in enumerate(path[1:], start=1):
                if hop > policy.max_graph_depth:
                    break
                if node_id in anchor_ids:
                    continue
                previous_hop = expansion_hops.get(node_id)
                if previous_hop is None or hop < previous_hop:
                    expansion_hops[node_id] = hop

        anchor_floor = min(
            (
                candidate.score
                if candidate.score is not None
                else -1.0
                for candidate in candidates
            ),
            default=0.0,
        )
        expansions: list[tuple[int, str, str, str, BackendCandidate]] = []
        for node_id, hop in expansion_hops.items():
            try:
                node = self.trg.graph_db.get_node(node_id)
                if (
                    not isinstance(node, self._event_node_type)
                    or getattr(node, "node_type", None) != self._node_type.EVENT
                ):
                    continue
                text = getattr(node, "content_narrative", None)
                timestamp = getattr(node, "timestamp", None)
                metadata = getattr(node, "attributes", None)
                if (
                    not isinstance(text, str)
                    or not text.strip()
                    or not isinstance(timestamp, datetime)
                    or timestamp.tzinfo is None
                    or timestamp.utcoffset() is None
                    or not isinstance(metadata, dict)
                ):
                    continue
                timestamp_text = timestamp.isoformat()
                evidence_id = metadata.get("evidence_id")
                provenance = metadata.get("provenance")
                provenance_fields = (
                    "segment_id",
                    "conversation_id",
                    "turn_id",
                    "source_role",
                    "source_timestamp",
                    "source_timezone",
                    "ingestion_version",
                )
                if (
                    not isinstance(timestamp_text, str)
                    or not timestamp_text
                    or not isinstance(evidence_id, str)
                    or not evidence_id.strip()
                    or not isinstance(provenance, dict)
                    or not all(
                        isinstance(provenance.get(field), str)
                        and bool(provenance[field].strip())
                        for field in provenance_fields
                    )
                    or provenance.get("source_role") not in {
                        "user",
                        "assistant",
                    }
                ):
                    continue
                timezone_source = provenance.get(
                    "timezone_source",
                    "legacy_segment_fallback",
                )
                if timezone_source not in {
                    "client",
                    "configured_default",
                    "legacy_segment_fallback",
                }:
                    continue
                source_timestamp = datetime.fromisoformat(
                    provenance["source_timestamp"].strip()
                )
                if (
                    source_timestamp.tzinfo is None
                    or source_timestamp.utcoffset() is None
                ):
                    continue

                # This deterministic value preserves anchor-first and fixed
                # traversal ordering through the adapter sort. It is not a
                # relevance score and is never projected into public evidence.
                ordering_rank = hop
                ordering_score = anchor_floor - float(ordering_rank)
                candidate = to_candidate(node, ordering_score)
                expansions.append(
                    (
                        ordering_rank,
                        timestamp_text,
                        evidence_id,
                        node_id,
                        candidate,
                    )
                )
            except Exception:
                continue

        expansions.sort(key=lambda item: item[:4])
        remaining_nodes = max(policy.max_nodes - len(candidates), 0)
        candidates.extend(
            item[4] for item in expansions[:remaining_nodes]
        )
        return candidates
