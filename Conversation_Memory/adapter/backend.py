from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from numbers import Integral
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from copy import deepcopy
from hashlib import sha256

from ._recall_execution import _execute_fixed_recall
from ._anchor_fusion import LexicalEventIndex
from .entity_consolidation import EntityCandidate
from .models import BackendCandidate, RecallPolicy


@dataclass(frozen=True)
class _EntityMembership:
    """Sparse vector positions; selectors retain this immutable backing array."""

    positions: Any
    array_selector: Any
    batch_selector: Any


_NON_NAME_REFERENCE_SURFACES = frozenset({
    "我", "我们", "咱", "咱们", "你", "您", "你们", "他", "她", "它",
    "他们", "她们", "它们", "自己", "本人", "其", "这", "那", "这个", "那个",
    "这些", "那些", "当前用户", "当前说话者", "current_user", "current user",
    "the current user", "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves",
    "this", "that", "these", "those", "myself", "ours", "ourselves",
})


def _is_persistent_name_surface(surface: str) -> bool:
    # Reuse the established user-role surfaces. This guard only controls a
    # derived name index: source occurrences and supported local bindings are
    # retained, and an explicit user name is still a searchable name.
    from .user_self import (
        _EN_FIRST_PERSON_SUBJECTS, _ZH_FIRST_PERSON_SUBJECTS,
        _is_formation_user_surface,
    )
    normalized = surface.strip().casefold()
    return bool(normalized) and not (
        normalized in _NON_NAME_REFERENCE_SURFACES
        or normalized in _EN_FIRST_PERSON_SUBJECTS
        or normalized in _ZH_FIRST_PERSON_SUBJECTS
        or _is_formation_user_surface(normalized)
    )


class MemoryBackend(Protocol):
    def find_memory_id(self, evidence_id: str) -> str | None: ...
    def add_event(self, text: str, timestamp: Any, metadata: dict[str, Any], *,
                  automatic_semantic: bool = True) -> str: ...
    def create_relationships(self, memory_ids: list[str], *,
                             automatic_entity_links: bool = True) -> None: ...
    def first_hit_view(self) -> Any: ...
    def first_hit_candidate(self, node_id: str) -> BackendCandidate | None: ...
    def endpoint_similarities(self, text: str, memory_ids) -> dict[str, float]: ...
    def apply_first_hit_links(self, plan: list[dict]) -> None: ...
    def list_entity_candidates(
        self, *, limit: int,
    ) -> tuple[EntityCandidate, ...]: ...
    def find_entity_candidates(self, surface: str, *, limit: int) -> tuple[EntityCandidate, ...]: ...
    def resolve_target_entity_refs(self, query: str, *, limit: int = 20) -> tuple[str, ...]: ...
    def upsert_entity_mentions(self, records: list[dict]) -> None: ...
    def list_entity_mentions(self, query: str, *, limit: int) -> tuple[dict, ...]: ...
    def ensure_event_persisted(self, memory_id: str) -> None: ...
    def persist(self) -> None: ...
    def resolve_target_entity_ref(self, query: str) -> str | None: ...
    def recall(
        self,
        query: str,
        policy: RecallPolicy,
        target_entity_ref: str | None = None,
        target_entity_refs: tuple[str, ...] = (),
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

    def find_entity_candidates(self, surface: str, *, limit: int) -> tuple[EntityCandidate, ...]:
        self._raise()

    def resolve_target_entity_refs(self, query: str, *, limit: int = 20) -> tuple[str, ...]:
        return ()

    def upsert_entity_mentions(self, records: list[dict]) -> None:
        self._raise()

    def list_entity_mentions(self, query: str, *, limit: int) -> tuple[dict, ...]:
        self._raise()

    def persist(self) -> None:
        self._raise()

    def resolve_target_entity_ref(self, query: str) -> str | None:
        return None

    def ensure_event_persisted(self, memory_id: str) -> None:
        self._raise()

    def recall(
        self,
        query: str,
        policy: RecallPolicy,
        target_entity_ref: str | None = None,
        target_entity_refs: tuple[str, ...] = (),
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

            def _extract_event(self, content, metadata=None):
                # Lumina supplies already-grounded bounded source facts.
                # Upstream's basic extraction silently takes content[:500],
                # which can discard a negation or exact-value suffix. Keep its
                # keyword/entity metadata but encode and persist the full fact.
                extraction = super()._extract_event(content, metadata)
                extraction.content_narrative = content
                return extraction

            def _create_semantic_links(self, event_node, top_k=3):
                # Instance-local single-write scope; pinned upstream is intact.
                if getattr(self, "_lumina_automatic_semantic", True):
                    return super()._create_semantic_links(event_node, top_k)

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
        self._rebuild_indexes()
        from ._source_backend import rebuild as rebuild_sources
        rebuild_sources(self)
        self._rebuild_first_hit_view()

    def _rebuild_first_hit_view(self) -> None:
        from .first_hit import DerivedFirstHitGraph
        # Existing controlled/legacy read backends need not expose adjacency.
        # Absence is an unavailable first-hit capability, never an empty graph.
        if not hasattr(self.trg.graph_db, "links"):
            self._first_hit_graph = None
            self._first_hit_evidence_ids = {}
            return
        self._first_hit_graph = DerivedFirstHitGraph(self.trg.graph_db)
        self._first_hit_evidence_ids = {
            node.attributes["evidence_id"]: node_id
            for node_id, node in self.trg.graph_db.nodes.items()
            if isinstance(getattr(node, "attributes", None), dict)
            and isinstance(node.attributes.get("evidence_id"), str)
        }

    def _prepare_first_hit_write(self) -> None:
        """Only load/owner writes may repair a derived graph view."""
        from .first_hit import FirstHitUnavailable
        view = getattr(self, "_first_hit_graph", None)
        try:
            if view is None or view.graph is not self.trg.graph_db:
                raise FirstHitUnavailable("first_hit_snapshot_unavailable")
            view.check()
        except FirstHitUnavailable:
            self._rebuild_first_hit_view()

    def _first_hit_add_node(self, node) -> None:
        self._first_hit_graph.add_node(node)
        attributes = getattr(node, "attributes", {})
        evidence_id = attributes.get("evidence_id")
        if isinstance(evidence_id, str):
            self._first_hit_evidence_ids[evidence_id] = node.node_id

    def first_hit_view(self):
        from .first_hit import FirstHitUnavailable
        view = getattr(self, "_first_hit_graph", None)
        if (view is None or view.graph is not self.trg.graph_db
                or getattr(self, "_source_present", False)):
            raise FirstHitUnavailable("first_hit_snapshot_unavailable")
        view.check()
        return view

    def first_hit_candidate(self, node_id: str) -> BackendCandidate | None:
        # Seed fallback remains projectable when only the graph view is stale.
        from .first_hit import _node_kind
        node = self.trg.graph_db.get_node(node_id)
        if getattr(self, "_source_present", False) or _node_kind(node) != "fact":
            return None
        return BackendCandidate(text=node.content_narrative, timestamp=node.timestamp.isoformat(),
                                score=None, metadata=deepcopy(node.attributes))

    def endpoint_similarity(self, text: str, memory_id: str) -> float:
        return self.endpoint_similarities(text, (memory_id,))[memory_id]

    def endpoint_similarities(self, text: str, memory_ids) -> dict[str, float]:
        """Encode a new endpoint once; compare existing full stored vectors."""
        import numpy as np
        from .first_hit import FirstHitUnavailable
        view = self.first_hit_view()
        version = view.version
        ids = tuple(dict.fromkeys(memory_ids))
        if not ids:
            return {}
        first = np.asarray(self.trg.encoder.encode(text), dtype=np.float64).reshape(-1)
        if not first.size or not np.isfinite(first).all():
            raise FirstHitUnavailable("first_hit_endpoint_embedding_invalid")
        first_norm = float(np.linalg.norm(first))
        values = {}
        for memory_id in ids:
            node = self.trg.graph_db.get_node(memory_id)
            if node is None or not view.is_fact(memory_id):
                raise FirstHitUnavailable("first_hit_endpoint_unavailable")
            second = np.asarray(node.embedding_vector, dtype=np.float64)
            if (second.ndim != 1 or first.shape != second.shape or not np.isfinite(second).all()):
                raise FirstHitUnavailable("first_hit_endpoint_embedding_invalid")
            denominator = float(first_norm * np.linalg.norm(second))
            if denominator == 0:
                values[memory_id] = 0.0
                continue
            cosine = float(first @ second / denominator)
            if not np.isfinite(cosine) or abs(cosine) > 1 + 1e-12:
                raise FirstHitUnavailable("first_hit_endpoint_embedding_invalid")
            values[memory_id] = min(1.0, max(0.0, cosine))
        view.check(version)
        return values

    def apply_first_hit_links(self, plan: list[dict]) -> None:
        """Apply an already checkpointed plan using stable evidence endpoints."""
        import math
        from memory.graph_db import Link, LinkSubType, LinkType
        self._prepare_first_hit_write()
        self._prepare_entity_membership_write()
        graph = self.trg.graph_db
        for item in plan:
            source_evidence = item.get("source_evidence_id")
            target_evidence = item.get("target_evidence_id")
            weight = item.get("weight")
            if (item.get("algorithm") != "first-hit-v1"
                    or not isinstance(source_evidence, str) or not source_evidence
                    or not isinstance(target_evidence, str) or not target_evidence
                    or source_evidence == target_evidence or isinstance(weight, bool)
                    or not isinstance(weight, (int, float)) or not math.isfinite(weight)
                    or not 0 < weight <= 1):
                raise ValueError("first_hit_link_plan_invalid")
            source = self._first_hit_evidence_ids.get(source_evidence)
            target = self._first_hit_evidence_ids.get(target_evidence)
            if (not self._first_hit_graph.is_fact(source)
                    or not self._first_hit_graph.is_fact(target)):
                raise ValueError("first_hit_link_endpoint_missing")
            for start, end, start_evidence, end_evidence in (
                (source, target, source_evidence, target_evidence),
                (target, source, target_evidence, source_evidence),
            ):
                digest = sha256(("first-hit-v1\0" + start_evidence + "\0" + end_evidence).encode()).hexdigest()
                link_id = "first-hit:" + digest
                properties = {"sub_type": LinkSubType.RELATED_TO.value,
                              "similarity_score": float(weight), "algorithm": "first-hit-v1",
                              "weight_source": "nonnegative_endpoint_cosine",
                              "source_evidence_id": start_evidence,
                              "target_evidence_id": end_evidence}
                existing = graph.links.get(link_id)
                if existing is not None:
                    if (existing.source_node_id != start or existing.target_node_id != end
                            or existing.link_type != LinkType.SEMANTIC or existing.properties != properties):
                        raise ValueError("first_hit_link_plan_conflict")
                    continue
                link = Link(link_id=link_id, source_node_id=start, target_node_id=end,
                            link_type=LinkType.SEMANTIC, properties=properties)
                graph.add_link(link)
                self._first_hit_graph.add_link(link)
                # Semantic edges change the source signature, not membership.
                self._refresh_entity_membership()

    def _rebuild_indexes(self, *, rebuild_entity_membership: bool = True) -> None:
        """Derived views only: graph.json remains the single durable authority."""
        self._surface_refs: dict[str, dict[str, None]] = {}
        self._surface_mentions: dict[str, dict[str, dict]] = {}
        self._surface_lengths: set[int] = set()
        self._lexical_index = LexicalEventIndex()
        for node in self.trg.graph_db.nodes.values():
            self._index_node(node)
        self._indexed_node_count = len(self.trg.graph_db.nodes)
        if rebuild_entity_membership:
            self._rebuild_entity_membership()

    def _entity_membership_signature(self):
        """Constant-size source check, without iterating nodes, links or members."""
        graph = self.trg.graph_db
        vectors = getattr(self.trg, "vector_db", None)
        index = getattr(vectors, "index", None)
        forward = getattr(vectors, "id_to_index", None)
        reverse = getattr(vectors, "index_to_id", None)
        if (index is None or not isinstance(forward, dict)
                or not isinstance(reverse, dict)):
            return None
        return (id(graph), id(graph.nodes), len(graph.nodes),
                id(graph.links), len(graph.links), id(vectors), id(index),
                int(index.ntotal), id(forward), len(forward), id(reverse), len(reverse))

    def _entity_membership_for_recall(self) -> dict[str, _EntityMembership] | None:
        """Read a prepared view; unexpected source changes disable this channel."""
        try:
            source = self._entity_membership_signature()
            if source is not None and source == getattr(self, "_entity_membership_source", None):
                return self._entity_membership
        except Exception:
            pass
        return None

    def _eligible_entity_vector_position(self, memory_id: str) -> int | None:
        node = self.trg.graph_db.get_node(memory_id)
        if (not isinstance(node, self._event_node_type)
                or getattr(node, "node_type", None) != self._node_type.EVENT):
            return None
        vectors = self.trg.vector_db
        position = vectors.id_to_index.get(memory_id)
        if (not isinstance(position, Integral) or isinstance(position, bool)
                or not 0 <= position < vectors.index.ntotal
                or vectors.index_to_id.get(int(position)) != memory_id):
            return None
        return int(position)

    def _compile_entity_membership(self, entity_id: str) -> None:
        import faiss
        import numpy as np

        positions = sorted({position for memory_id in self._entity_member_events[entity_id]
                            if (position := self._eligible_entity_vector_position(memory_id)) is not None})
        previous = self._entity_membership.pop(entity_id, None)
        self._entity_membership_stats["memberships"] -= len(previous.positions) if previous else 0
        if positions:
            buffer = np.asarray(positions, dtype=np.int64)
            buffer.setflags(write=False)
            member = _EntityMembership(buffer, faiss.IDSelectorArray(buffer),
                                       faiss.IDSelectorBatch(buffer))
            self._entity_membership[entity_id] = member
            self._entity_membership_stats["memberships"] += len(buffer)
            self._entity_membership_stats["selector_builds"] += 1

    def _rebuild_entity_membership(self) -> None:
        """Load/write-time reconstruction; each entity's positions are sorted."""
        started = perf_counter()
        stats = getattr(self, "_entity_membership_stats", None)
        if stats is None:
            stats = self._entity_membership_stats = {
                "rebuilds": 0, "updates": 0, "links_scanned": 0,
                "selector_builds": 0, "memberships": 0, "failures": 0,
                "rebuild_seconds": 0.0, "update_seconds": 0.0,
            }
        self._entity_membership = {}
        self._entity_member_events: dict[str, set[str]] = {}
        self._event_entity_memberships: dict[str, set[str]] = {}
        self._entity_membership_source = None
        stats["memberships"] = 0
        stats["rebuilds"] += 1
        try:
            source = self._entity_membership_signature()
            if source is None:
                return
            from memory.graph_db import LinkSubType, LinkType

            graph = self.trg.graph_db
            for link in graph.links.values():
                stats["links_scanned"] += 1
                target = graph.get_node(link.target_node_id)
                node = graph.get_node(link.source_node_id)
                if (link.link_type != LinkType.ENTITY
                        or link.properties.get("sub_type") != LinkSubType.REFERS_TO.value
                        or target is None
                        or getattr(target, "node_type", None) != getattr(self._node_type, "ENTITY", None)
                        or not isinstance(node, self._event_node_type)
                        or getattr(node, "node_type", None) != self._node_type.EVENT):
                    continue
                self._entity_member_events.setdefault(link.target_node_id, set()).add(link.source_node_id)
                self._event_entity_memberships.setdefault(link.source_node_id, set()).add(link.target_node_id)
            for entity_id in self._entity_member_events:
                self._compile_entity_membership(entity_id)
            self._entity_membership_source = source
        except Exception:
            # A derived selector cannot veto a durable ingestion or other channels.
            self._entity_membership_source = None
            stats["failures"] += 1
        finally:
            stats["rebuild_seconds"] += perf_counter() - started

    def _prepare_entity_membership_write(self) -> None:
        if self._entity_membership_for_recall() is None:
            self._rebuild_entity_membership()

    def _refresh_entity_membership(self, memory_id: str | None = None, entity_id: str | None = None) -> None:
        """Refresh an event, or acknowledge a graph-only occurrence write."""
        started = perf_counter()
        try:
            if getattr(self, "_entity_membership_source", None) is None:
                return
            if entity_id is not None and memory_id is not None:
                self._entity_member_events.setdefault(entity_id, set()).add(memory_id)
                self._event_entity_memberships.setdefault(memory_id, set()).add(entity_id)
            for ref in self._event_entity_memberships.get(memory_id, ()):
                self._compile_entity_membership(ref)
            self._entity_membership_source = self._entity_membership_signature()
        except Exception:
            self._entity_membership_source = None
            self._entity_membership_stats["failures"] += 1
        finally:
            self._entity_membership_stats["updates"] += 1
            self._entity_membership_stats["update_seconds"] += perf_counter() - started

    def _index_node(self, node) -> None:
        if getattr(node, "node_type", None) == self._node_type.EVENT:
            self._lexical_index.add(node)
            return
        if getattr(node, "node_type", None) != getattr(self._node_type, "ENTITY", None):
            return
        metadata = getattr(node, "attributes", {})
        if not isinstance(metadata, dict):
            return
        ref, surface = metadata.get("entity_ref"), metadata.get("canonical_surface")
        if (isinstance(ref, str) and ref.strip() and isinstance(surface, str)
                and _is_persistent_name_surface(surface)):
            self._surface_refs.setdefault(surface.strip(), {})[ref.strip()] = None
            self._surface_lengths.add(len(surface.strip()))
        for record in metadata.get("mentions", ()):
            if not isinstance(record, dict):
                continue
            surface, mention_id = record.get("surface"), record.get("mention_id")
            if not isinstance(surface, str) or not surface or not isinstance(mention_id, str):
                continue
            self._surface_mentions.setdefault(surface, {})[mention_id] = deepcopy(record)
            self._surface_lengths.add(len(surface))
            if isinstance(ref, str) and ref.strip() and _is_persistent_name_surface(surface):
                self._surface_refs.setdefault(surface, {})[ref.strip()] = None

    def _ensure_indexes(self) -> None:
        if (not hasattr(self, "_surface_refs") or
                self._indexed_node_count != len(self.trg.graph_db.nodes)):
            # Name/lexical views retain their compatibility recovery. Entity
            # membership is reconstructed explicitly on load or owner writes.
            self._rebuild_indexes(rebuild_entity_membership=False)

    def _matching_surfaces(self, query: str):
        """Probe query substrings against indexed names, not every stored name."""
        seen = set()
        lengths = sorted(self._surface_lengths, reverse=True)
        start = 0
        while start < len(query):
            matched_length = 0
            for length in lengths:
                if start + length > len(query):
                    continue
                surface = query[start:start + length]
                if (surface and surface[0].isascii() and surface[0].isalnum()
                        and start > 0 and query[start - 1].isascii() and query[start - 1].isalnum()):
                    continue
                if (surface and surface[-1].isascii() and surface[-1].isalnum()
                        and start + length < len(query)
                        and query[start + length].isascii() and query[start + length].isalnum()):
                    continue
                if surface not in seen and (surface in self._surface_refs or surface in self._surface_mentions):
                    seen.add(surface)
                    yield surface
                    matched_length = length
                    break
            start += matched_length or 1

    def find_entity_candidates(self, surface: str, *, limit: int) -> tuple[EntityCandidate, ...]:
        self._ensure_indexes()
        if limit <= 0 or not isinstance(surface, str):
            return ()
        normalized = surface.strip()
        from itertools import islice
        return tuple(EntityCandidate(ref, normalized) for ref in
                     islice(self._surface_refs.get(normalized, {}), limit))

    def resolve_target_entity_refs(self, query: str, *, limit: int = 20) -> tuple[str, ...]:
        self._ensure_indexes()
        if limit <= 0 or not isinstance(query, str):
            return ()
        hits: dict[str, None] = {}
        for surface in self._matching_surfaces(query):
            for ref in self._surface_refs.get(surface, {}):
                hits.setdefault(ref, None)
                if len(hits) >= limit:
                    return tuple(hits)
        return tuple(hits)

    def upsert_entity_mentions(self, records: list[dict]) -> None:
        """Persist source occurrences without adding assertions or vectors."""
        from memory.graph_db import EventNode, NodeType
        self._ensure_indexes()
        self._prepare_entity_membership_write()
        self._prepare_first_hit_write()
        for raw in records:
            record = deepcopy(raw)
            mention_id, surface, ref = record.get("mention_id"), record.get("surface"), record.get("entity_ref")
            if not isinstance(mention_id, str) or not mention_id or not isinstance(surface, str) or not surface:
                raise ValueError("entity_mention_invalid")
            if ref is not None and (not isinstance(ref, str) or not ref.strip()):
                raise ValueError("entity_mention_ref_invalid")
            # One metadata carrier retains unresolved source occurrences. It
            # has no EntityRef and asserts no shared identity among them.
            node_id = f"entity:{ref.casefold()}" if ref else "entity:unresolved_mentions"
            node = self.trg.graph_db.get_node(node_id)
            if node is None:
                node = EventNode(node_id=node_id, node_type=NodeType.ENTITY,
                                 content_narrative="", embedding_vector=None,
                                 attributes={"entity_ref": ref, "canonical_surface": surface if ref else "",
                                             "mentions": []})
                self.trg.graph_db.add_node(node)
            mentions = node.attributes.setdefault("mentions", [])
            existing = next((item for item in mentions if item.get("mention_id") == mention_id), None)
            if existing is None:
                mentions.append(record)
            elif existing != record:
                raise ValueError("entity_mention_provenance_conflict")
            self._index_node(node)
            self._first_hit_add_node(node)
        self._indexed_node_count = len(self.trg.graph_db.nodes)
        self._refresh_entity_membership()

    def list_entity_mentions(self, query: str, *, limit: int) -> tuple[dict, ...]:
        self._ensure_indexes()
        if limit <= 0 or not isinstance(query, str):
            return ()
        records: dict[str, dict] = {}
        for surface in self._matching_surfaces(query):
            for mention_id, record in self._surface_mentions.get(surface, {}).items():
                records.setdefault(mention_id, deepcopy(record))
                if len(records) >= limit:
                    return tuple(records.values())
        return tuple(records.values())

    def find_memory_id(self, evidence_id: str) -> str | None:
        view = getattr(self, "_first_hit_graph", None)
        if view is not None and view._source == view._signature():
            return self._first_hit_evidence_ids.get(evidence_id)
        for node_id, node in self.trg.graph_db.nodes.items():
            if getattr(node, "attributes", {}).get("evidence_id") == evidence_id:
                return node_id
        return None

    def ensure_event_persisted(self, memory_id: str) -> None:
        """Repair upstream's graph-before-vector failure using stored embedding."""
        self._prepare_entity_membership_write()
        self._prepare_first_hit_write()
        node = self.trg.graph_db.get_node(memory_id)
        if node is None or getattr(node, "node_type", None) != self._node_type.EVENT:
            raise ValueError("memory_event_missing")
        vector_db = self.trg.vector_db
        if memory_id in vector_db.id_to_index:
            self._refresh_entity_membership(memory_id)
            return
        import numpy as np
        vector = getattr(node, "embedding_vector", None)
        if not isinstance(vector, list) or not vector:
            raise ValueError("memory_event_embedding_missing")
        metadata = node.attributes
        vector_db.add_vector(vector_id=memory_id, vector=np.asarray(vector, dtype=np.float32),
                             metadata={"timestamp": node.timestamp.isoformat(),
                                       "keywords": metadata.get("keywords", []),
                                        "entities": metadata.get("entities", [])})
        self._refresh_entity_membership(memory_id)

    def add_event(self, text: str, timestamp: Any, metadata: dict[str, Any], *,
                  automatic_semantic: bool = True) -> str:
        if getattr(self, "_source_present", False):
            raise ValueError("source_store_fact_write_forbidden")
        if type(automatic_semantic) is not bool:
            raise ValueError("automatic_semantic_must_be_bool")
        self._ensure_indexes()
        self._prepare_entity_membership_write()
        self._prepare_first_hit_write()
        previous = getattr(self.trg, "_lumina_automatic_semantic", True)
        self.trg._lumina_automatic_semantic = automatic_semantic
        try:
            memory_id = self.trg.add_event(text, timestamp=timestamp, metadata=metadata)
        finally:
            self.trg._lumina_automatic_semantic = previous
        node = self.trg.graph_db.get_node(memory_id)
        self._index_node(node)
        self._first_hit_add_node(node)
        graph = self.trg.graph_db
        incident = getattr(graph, "node_to_links", {}).get(memory_id)
        links = (graph.links[key] for key in incident) if incident is not None else (
            link for link in graph.links.values()
            if memory_id in (link.source_node_id, link.target_node_id))
        for link in links:
            self._first_hit_graph.add_link(link)
        self._indexed_node_count = len(graph.nodes)
        self._refresh_entity_membership(memory_id)
        return memory_id

    def create_relationships(self, memory_ids: list[str], *,
                             automatic_entity_links: bool = True) -> None:
        self._prepare_first_hit_write()
        if automatic_entity_links:
            existing = {
                (link.source_node_id, link.target_node_id, link.link_type.value, link.properties.get("entity"))
                for link in self.trg.graph_db.links.values()
            }
            for memory_id in memory_ids:
                node = self.trg.graph_db.get_node(memory_id)
                if node is None:
                    continue
                for link in self.trg._create_entity_edges(node):
                    identity = (link.source_node_id, link.target_node_id, link.link_type.value,
                                link.properties.get("entity"))
                    if identity not in existing:
                        self.trg.graph_db.add_link(link)
                        self._first_hit_graph.add_link(link)
                        existing.add(identity)
        if not automatic_entity_links:
            self._repair_first_hit_temporal_links(memory_ids)
        self._create_subject_entity_ref_links(memory_ids)
        self._create_object_entity_ref_links(memory_ids)
        self._create_mention_entity_ref_links(memory_ids)
        self._prepare_entity_membership_write()

    def _repair_first_hit_temporal_links(self, memory_ids: list[str]) -> None:
        """Recover graph-before-vector interruptions in the explicit new writer.

        Graph JSON preserves node insertion order. Only EVENTs preceding this
        insertion were present when upstream chose its timestamp predecessor;
        later arrivals must not change the original write obligation.
        """
        from memory.graph_db import Link, LinkType, LinkSubType
        self._prepare_entity_membership_write()
        graph = self.trg.graph_db
        targets = set(memory_ids)
        # Ordinary successful writes already hold both directions; avoid the
        # history scan unless an actual incomplete temporal pair needs repair.
        for memory_id in tuple(targets):
            incident = getattr(graph, "node_to_links", {}).get(memory_id)
            links = (graph.links[key] for key in incident) if incident is not None else graph.links.values()
            temporal = {(edge.source_node_id, edge.target_node_id, edge.properties.get("sub_type"))
                        for edge in links if edge.link_type == LinkType.TEMPORAL}
            if any(start == memory_id and subtype == LinkSubType.SUCCEEDS.value
                   and (end, start, LinkSubType.PRECEDES.value) in temporal
                   for start, end, subtype in temporal):
                targets.remove(memory_id)
        if not targets:
            return
        earlier = []
        for node in graph.nodes.values():
            if node.node_type != self._node_type.EVENT:
                continue
            if node.node_id in targets:
                eligible = (old for old in earlier if old.timestamp <= node.timestamp)
                previous = max(eligible, key=lambda old: old.timestamp, default=None)
                # Equal timestamps follow graph insertion order (stable sort).
                if previous is not None:
                    previous = next(old for old in reversed(earlier)
                                    if old.timestamp == previous.timestamp)
                    delta = (node.timestamp - previous.timestamp).total_seconds()
                    incident = getattr(graph, "node_to_links", {}).get(node.node_id)
                    links = (graph.links[key] for key in incident) if incident is not None else graph.links.values()
                    existing = {(edge.source_node_id, edge.target_node_id, edge.properties.get("sub_type"))
                                for edge in links if edge.link_type == LinkType.TEMPORAL}
                    for start, end, subtype in (
                        (previous.node_id, node.node_id, LinkSubType.PRECEDES.value),
                        (node.node_id, previous.node_id, LinkSubType.SUCCEEDS.value),
                    ):
                        if (start, end, subtype) in existing:
                            continue
                        key = sha256((start + "\0" + end + "\0" + subtype).encode()).hexdigest()
                        edge = Link(link_id="first-hit-temporal:" + key, source_node_id=start,
                                    target_node_id=end, link_type=LinkType.TEMPORAL,
                                    properties={"sub_type": subtype, "time_delta": delta})
                        graph.add_link(edge)
                        self._first_hit_graph.add_link(edge)
                        self._refresh_entity_membership()
            earlier.append(node)

    def _create_object_entity_ref_links(self, memory_ids: list[str]) -> None:
        for memory_id in memory_ids:
            node = self.trg.graph_db.get_node(memory_id)
            metadata = getattr(node, "attributes", {})
            ref = metadata.get("object_entity_ref")
            surface = metadata.get("object_entity_surface")
            if isinstance(ref, str) and ref.strip() and isinstance(surface, str) and surface.strip():
                self._ensure_entity_refers_to_link(memory_id, ref=ref.strip(),
                                                   surface=surface.strip(), role="object")

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
        self._prepare_entity_membership_write()
        self._prepare_first_hit_write()
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
        self._first_hit_add_node(self.trg.graph_db.get_node(entity_node_id))
        properties = {"sub_type": LinkSubType.REFERS_TO.value}
        if role is not None:
            properties["role"] = role
        graph = self.trg.graph_db
        incident = getattr(graph, "node_to_links", {}).get(memory_id)
        candidates = (graph.links[key] for key in incident) if incident is not None else graph.links.values()
        already_linked = any(
            link.source_node_id == memory_id
            and link.target_node_id == entity_node_id
            and link.link_type == LinkType.ENTITY
            and link.properties.get("sub_type") == LinkSubType.REFERS_TO.value
            and link.properties.get("role") == role
            for link in candidates
        )
        if not already_linked:
            link = Link(
                source_node_id=memory_id,
                target_node_id=entity_node_id,
                link_type=LinkType.ENTITY,
                properties=properties,
            )
            self.trg.graph_db.add_link(link)
            self._first_hit_graph.add_link(link)
        if hasattr(self, "_surface_refs"):
            self._index_node(self.trg.graph_db.get_node(entity_node_id))
            self._indexed_node_count = len(self.trg.graph_db.nodes)
        self._refresh_entity_membership(memory_id, entity_node_id)

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

    def source_text_fits(self, text: str) -> bool:
        from ._source_backend import text_fits
        return text_fits(self, text)

    def add_source(self, text: str, timestamp: Any, metadata: dict[str, Any]) -> str:
        from ._source_backend import add
        return add(self, text, timestamp, metadata)

    def ensure_source_persisted(self, memory_id: str) -> None:
        from ._source_backend import ensure_persisted
        ensure_persisted(self, memory_id)

    def source_candidates(self, query: str, policy: RecallPolicy) -> list[BackendCandidate]:
        from ._source_backend import candidates
        return candidates(self, query, policy)

    def source_neighbors(self, candidate: BackendCandidate, *, before: int = 2,
                         after: int = 2, limit: int = 5, known_candidates=None,
                         max_nodes: int | None = None) -> list[BackendCandidate]:
        from ._source_backend import neighbors
        return neighbors(self, candidate, before=before, after=after, limit=limit,
                         known_candidates=known_candidates, max_nodes=max_nodes)

    def source_locate(self, refs, *, limit: int, known_candidates=None,
                      max_nodes: int | None = None, max_refs: int | None = None) -> list[BackendCandidate]:
        from ._source_backend import locate
        return locate(self, refs, limit=limit, known_candidates=known_candidates,
                      max_nodes=max_nodes, max_refs=max_refs)

    def source_parent(self, candidate: BackendCandidate, *, limit: int,
                      known_candidates=None, max_nodes: int | None = None) -> list[BackendCandidate]:
        from ._source_backend import parent
        return parent(self, candidate, limit=limit, known_candidates=known_candidates, max_nodes=max_nodes)

    def source_read_range(self, segment_id: str, start_turn: int, end_turn: int, *,
                          start_char: int = 0, end_char: int | None = None,
                          limit: int, max_chars: int, known_candidates=None) -> list[BackendCandidate]:
        from ._source_backend import read_range
        return read_range(self, segment_id, start_turn, end_turn, start_char=start_char,
                          end_char=end_char, limit=limit, max_chars=max_chars,
                          known_candidates=known_candidates)

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
        hits = self.resolve_target_entity_refs(query, limit=2)
        return hits[0] if len(hits) == 1 else None

    def recall(
        self,
        query: str,
        policy: RecallPolicy,
        target_entity_ref: str | None = None,
        target_entity_refs: tuple[str, ...] = (),
    ) -> list[BackendCandidate]:
        if getattr(self, "_source_present", False):
            raise RuntimeError("source_store_requires_source_recall")
        self._ensure_indexes()
        context = _execute_fixed_recall(
            trg=self.trg,
            constraints_type=self._constraints_type,
            event_node_type=self._event_node_type,
            node_type=self._node_type,
            query=query,
            policy=policy,
            target_entity_ref=target_entity_ref,
            target_entity_refs=target_entity_refs,
            lexical_index=self._lexical_index,
            entity_surfaces=tuple(self._matching_surfaces(query)),
            entity_membership=self._entity_membership_for_recall(),
        )
        self.last_recall_stats = dict(context.metadata.get("bounded_recall_stats", {}))
        scores = context.metadata.get("search_scores", [])

        def to_candidate(node: Any, score: float | None) -> BackendCandidate:
            timestamp = getattr(node, "timestamp", None)
            return BackendCandidate(
                text=getattr(node, "content_narrative", ""),
                timestamp=timestamp.isoformat() if timestamp else None,
                score=score,
                metadata={**dict(getattr(node, "attributes", {})),
                          **context.metadata.get("association_metadata", {}).get(node.node_id, {})},
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
