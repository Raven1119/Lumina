"""Actual backend/graph/FAISS lifecycle with controlled encoder and TRG calls.

Dense and lexical channels are disabled when isolating entity eligibility.
These controls do not measure MiniLM or BGE semantic quality.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import faiss
import numpy as np
import pytest

from Conversation_Memory.adapter.backend import RealMagmaBackend
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.ingestion.state_store import IngestionStateStore
from Conversation_Memory.tests.test_entity_relation_recall import graph_types
from Conversation_Memory.tests.test_entity_ingestion_v2 import Model, mention, segment, unit
from Conversation_Memory.tests.test_memory_admission import NoCalls


@pytest.fixture(autouse=True)
def isolate_membership_state(tmp_path, monkeypatch):
    assert tmp_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    monkeypatch.setenv("LUMINA_MIND_DECISION_LOG_PATH", str(tmp_path / "mind_decisions.jsonl"))


class Vectors:
    """A single real IndexFlatL2 with a small deterministic persistence double."""
    def __init__(self, path):
        self.path = path
        self.index = faiss.IndexFlatL2(2)
        self.id_to_index, self.index_to_id, self.rows = {}, {}, []
        self.fail_next = False
        self.write_attempts = 0
        if path.exists():
            for vector_id, vector in json.loads(path.read_text(encoding="utf-8")):
                self.add_vector(vector_id=vector_id, vector=np.asarray(vector, dtype=np.float32), metadata={})

    def add_vector(self, *, vector_id, vector, metadata):
        self.write_attempts += 1
        if self.fail_next:
            self.fail_next = False
            raise OSError("controlled graph-before-vector failure")
        assert vector_id not in self.id_to_index
        position = len(self.id_to_index)
        self.id_to_index[vector_id] = position
        self.index_to_id[position] = vector_id
        self.rows.append((vector_id, vector.tolist()))
        self.index.add(np.asarray(vector, dtype=np.float32).reshape(1, -1))
        return True

    def save(self, directory):
        path = Path(directory) / "controlled_vectors.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.rows), encoding="utf-8")


@pytest.fixture
def backend_factory(graph_types, tmp_path, monkeypatch):
    types = graph_types

    class ControlledTrg:
        def __init__(self, *, persist_dir, **_kwargs):
            self.graph_db = types.NetworkXGraphDB()
            self.vector_db = Vectors(Path(persist_dir) / "vectors" / "controlled_vectors.json")
            self.encoder = SimpleNamespace(encode=lambda _query: np.asarray([0.0, 0.0], dtype=np.float32))
            self.keyword_enricher = SimpleNamespace(enrich_query=lambda query: query)
            self.stats = {"links_created": 0}
            self.dense_ids = []

        def _extract_event(self, content, metadata=None):
            return SimpleNamespace(content_narrative=content, keywords=[], entities=[])

        def _create_entity_edges(self, _node):
            return []

        def add_event(self, text, *, timestamp, metadata):
            node_id = "event:" + metadata["evidence_id"]
            node = types.EventNode(
                node_id=node_id, node_type=types.NodeType.EVENT, timestamp=timestamp,
                content_narrative=text, attributes=copy.deepcopy(metadata),
                embedding_vector=list(metadata.get("controlled_vector", [0.0, 0.0])),
            )
            self.graph_db.add_node(node)
            self.vector_db.add_vector(vector_id=node_id, vector=np.asarray(node.embedding_vector, dtype=np.float32), metadata={})
            self._create_temporal_links(node)
            return node_id

        def query(self, _query, *, max_results, constraints):
            nodes = [self.graph_db.get_node(node_id) for node_id in self.dense_ids[:max_results]]
            return SimpleNamespace(anchor_nodes=nodes, metadata={"search_scores": [1.0] * len(nodes)},
                                   traversal_paths=[], narrative_context="")

    module = ModuleType("memory.trg_memory")
    module.TemporalResonanceGraphMemory = ControlledTrg
    monkeypatch.setitem(sys.modules, "memory.trg_memory", module)

    def create(name="backend"):
        backend = RealMagmaBackend(tmp_path / name)
        backend._lexical_index.rank = lambda **_kwargs: []
        return backend

    return create


def add(backend, eid, *, subject=None, obj=None, mentions=(), distance=0.0):
    timestamp = datetime(2026, 9, 13, tzinfo=UTC)
    metadata = {"evidence_id": eid, "controlled_vector": [distance, 0.0],
                "subject_entity_ref": subject, "subject_entity_surface": subject,
                "object_entity_ref": obj, "object_entity_surface": obj,
                "mention_entity_refs": list(mentions), "mention_entity_surfaces": list(mentions),
                "provenance": {"segment_id": "synthetic", "conversation_id": "membership",
                    "turn_id": eid, "source_role": "user", "source_timestamp": timestamp.isoformat(),
                    "source_timezone": "UTC", "timezone_source": "client", "ingestion_version": "test-v2"}}
    return backend.add_event(f"{eid} records a tool setting.", timestamp=timestamp, metadata=metadata)


def policy(*, count=3, depth=0, nodes=20):
    return RecallPolicy(top_k=count, max_graph_depth=depth, max_nodes=nodes,
                        max_evidence_items=count, max_chars=4000, final_min_score=0.144)


def ids(backend, refs, *, count=3, depth=0, nodes=20):
    return [candidate.metadata["evidence_id"] for candidate in backend.recall(
        "tool setting", policy(count=count, depth=depth, nodes=nodes), target_entity_refs=tuple(refs),
    )]


def positions(backend, ref):
    view = backend._entity_membership_for_recall()
    assert view is not None
    record = view.get("entity:" + ref.casefold())
    return () if record is None else tuple(int(item) for item in record.positions)


@pytest.mark.parametrize("reverse", [False, True])
def test_tail_fact_has_entity_search_eligibility_before_candidate_limit(backend_factory, reverse):
    backend = backend_factory()
    events = [add(backend, f"fact-{i:02}", subject="Iris", distance=12 - i) for i in range(12)]
    backend.create_relationships(list(reversed(events)) if reverse else events)
    assert ids(backend, ("Iris",), count=1, depth=1)[0] == "fact-11"
    assert positions(backend, "Iris") == tuple(range(12))
    assert backend.last_recall_stats["entity_adjacency_links_read"] == 0
    assert backend.last_recall_stats["adjacency_links_read"] <= 20


def test_equal_distance_selection_is_independent_of_relationship_order(backend_factory):
    results = []
    for reverse in (False, True):
        backend = backend_factory(str(reverse))
        events = [add(backend, f"fact-{i:02}", subject="Nora", distance=1.0) for i in range(12)]
        backend.create_relationships(list(reversed(events)) if reverse else events)
        results.append(ids(backend, ("Nora",), count=3, depth=1))
    assert len(results[0]) >= 3 and results[0] == results[1]


def test_multiple_refs_union_all_roles_deduplicate_and_exclude_unavailable_vectors(backend_factory, graph_types):
    backend = backend_factory()
    events = [add(backend, "subject", subject="Iris"), add(backend, "object", obj="Nora"),
              add(backend, "shared", subject="Iris", obj="Nora", mentions=("Iris", "Nora", "Nora")),
              add(backend, "mention", mentions=("Iris", "Nora")), add(backend, "unlinked")]
    backend.create_relationships(events)
    graph_only = graph_types.EventNode(node_id="graph-only", node_type=graph_types.NodeType.EVENT,
                                      content_narrative="A vector is unavailable.", attributes={})
    backend.trg.graph_db.add_node(graph_only)
    backend._ensure_entity_refers_to_link("graph-only", ref="Iris", surface="Iris", role="subject")
    # Even corrupt legacy vector membership cannot make an ENTITY an event.
    backend.trg.vector_db.add_vector(vector_id="entity:iris", vector=np.zeros(2, dtype=np.float32), metadata={})
    backend._ensure_entity_refers_to_link("entity:iris", ref="Nora", surface="Nora", role=None)
    backend._rebuild_indexes()
    backend._lexical_index.rank = lambda **_kwargs: []
    assert positions(backend, "Iris") == (0, 2, 3)
    assert positions(backend, "Nora") == (1, 2, 3)
    assert set(ids(backend, ("Iris", "Nora", "Iris"), count=20)) == {"subject", "object", "shared", "mention"}
    record = backend._entity_membership_for_recall()["entity:iris"]
    assert record.positions.dtype == np.dtype("int64") and not record.positions.flags.writeable
    with pytest.raises(ValueError):
        record.positions[0] = 99


def test_new_relationship_updates_an_existing_entity_without_node_count_change(backend_factory):
    backend = backend_factory()
    first = add(backend, "first", subject="Iris")
    backend.create_relationships([first])
    second = add(backend, "second")
    before_count = len(backend.trg.graph_db.nodes)
    assert positions(backend, "Iris") == (0,)
    backend._ensure_entity_refers_to_link(second, ref="Iris", surface="Iris", role="object")
    backend._ensure_entity_refers_to_link(second, ref="Iris", surface="Iris", role=None)
    assert len(backend.trg.graph_db.nodes) == before_count
    assert positions(backend, "Iris") == (0, 1)
    assert set(ids(backend, ("Iris",))) == {"first", "second"}


def formation_adapter(backend, tmp_path, model):
    instance = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "ingestion.json"),
                                 ingestion_version="grounded-formation-v2", formation_model=model)
    instance._bge_reranker = SimpleNamespace(score=lambda _query, texts: tuple(5.0 for _ in texts))
    instance._bge_reranker_load_attempted = True
    return instance


def grounded_source(text, sid):
    cold = segment(text, sid)
    value = "ready" if "ready" in text else "available"
    return cold, {"mentions": [mention(cold, "Iris", "iris")],
                  "units": [unit(cold, text, "Iris", "is", value, "iris")]}


def test_public_ingest_increment_restart_and_read_preserve_sources(backend_factory, tmp_path):
    backend = backend_factory()
    first, output = grounded_source("Iris is ready.", "membership-first")
    initial = formation_adapter(backend, tmp_path, Model(output))
    one = initial.ingest(first)
    assert one.status == "completed"
    second, output = grounded_source("Iris is available.", "membership-second")
    two = formation_adapter(backend, tmp_path, Model(output)).ingest(second)
    assert two.status == "completed"
    ref, = backend.resolve_target_entity_refs("Iris")
    assert len(positions(backend, ref)) == 2
    expected = initial.recall("What about Iris?", policy())
    assert {item.text for item in expected.evidence} == {"Iris is ready.", "Iris is available."}
    frozen = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*.json")}
    restarted_backend = backend_factory()
    no_calls = NoCalls()
    restarted = formation_adapter(restarted_backend, tmp_path, no_calls)
    assert restarted.ingest(first).already_ingested and restarted.ingest(second).already_ingested
    assert restarted.recall("What about Iris?", policy()) == expected
    assert len(positions(restarted_backend, ref)) == 2 and no_calls.calls == []
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*.json")} == frozen


def test_public_graph_before_vector_failure_recovers_membership_without_reforming(backend_factory, tmp_path):
    backend = backend_factory()
    cold, output = grounded_source("Iris is ready.", "membership-vector-failure")
    backend.trg.vector_db.fail_next = True
    model = Model(output)
    initial = formation_adapter(backend, tmp_path, model)
    first = initial.ingest(cold)
    assert first.status == "failed" and first.retryable
    assert backend.trg.vector_db.id_to_index == {}
    key = initial.state_store.key(cold.segment_id, "grounded-formation-v2")
    before = copy.deepcopy(initial.state_store.get(key))
    no_calls = NoCalls()
    repaired = formation_adapter(backend, tmp_path, no_calls)
    recovered = repaired.ingest(cold)
    assert recovered.status == "completed" and len(recovered.memory_ids) == 1
    ref, = backend.resolve_target_entity_refs("Iris")
    assert positions(backend, ref) == (0,) and no_calls.calls == []
    assert backend.trg.vector_db.write_attempts == 2
    state = repaired.state_store.get(key)
    assert all(state[name] == before[name] for name in ("extracted", "verified", "mentions"))
    assert len([node for node in backend.trg.graph_db.nodes.values() if node.node_type == backend._node_type.EVENT]) == 1
    reloaded = backend_factory()
    assert positions(reloaded, ref) == (0,)


def test_missing_cache_is_read_only_fail_soft_until_explicit_rebuild(backend_factory):
    backend = backend_factory()
    event = add(backend, "first", subject="Iris")
    backend.create_relationships([event])
    assert positions(backend, "Iris") == (0,)
    backend._entity_membership = None
    assert backend._entity_membership_for_recall() is None
    assert ids(backend, ("Iris",)) == []
    backend._rebuild_entity_membership()
    assert positions(backend, "Iris") == (0,)
    assert ids(backend, ("Iris",)) == ["first"]


def test_broken_membership_view_keeps_dense_fallback(backend_factory):
    backend = backend_factory()
    event = add(backend, "dense", subject="Iris")
    backend.create_relationships([event])
    backend.trg.dense_ids = [event]

    class BrokenView(dict):
        failures = 0
        def __getitem__(self, _key):
            self.failures += 1
            raise RuntimeError("controlled selector cache failure")

    backend._entity_membership = BrokenView(backend._entity_membership_for_recall())
    assert ids(backend, ("Iris",)) == ["dense"]
    assert backend._entity_membership.failures == 1


def test_warm_entity_query_does_not_iterate_graph_or_adjacency(backend_factory, monkeypatch):
    backend = backend_factory()
    events = [add(backend, f"fact-{i:02}", subject="Iris", distance=12 - i) for i in range(12)]
    backend.create_relationships(events)
    assert positions(backend, "Iris") == tuple(range(12))

    class LookupOnly(dict):
        blocked = False
        def values(self):
            if self.blocked:
                raise AssertionError("query must not scan graph values")
            return super().values()
        def items(self):
            if self.blocked:
                raise AssertionError("query must not scan graph items")
            return super().items()
        def __iter__(self):
            if self.blocked:
                raise AssertionError("query must not iterate graph keys")
            return super().__iter__()

    graph = backend.trg.graph_db
    graph.nodes, graph.links = LookupOnly(graph.nodes), LookupOnly(graph.links)
    backend._rebuild_indexes()
    backend._lexical_index.rank = lambda **_kwargs: []
    graph.nodes.blocked = graph.links.blocked = True
    import Conversation_Memory.adapter._recall_execution as execution
    def forbidden(*_args, **_kwargs):
        raise AssertionError("anchor-only entity query must not read adjacency")
    monkeypatch.setattr(execution, "_iter_adjacent_links", forbidden)
    assert ids(backend, ("Iris",), count=2) == ["fact-11", "fact-10"]
    assert backend.last_recall_stats["entity_adjacency_links_read"] == 0


def test_complete_membership_does_not_remove_candidate_or_expansion_budgets(backend_factory):
    backend = backend_factory()
    events = [add(backend, f"fact-{i:02}", subject="Iris", distance=30 - i) for i in range(30)]
    backend.create_relationships(events)
    selected = ids(backend, ("Iris",), count=3, depth=1, nodes=20)
    assert "fact-29" in selected and len(selected) <= 20
    stats = backend.last_recall_stats
    assert stats["entity_adjacency_links_read"] == 0
    assert stats["adjacency_links_read"] <= 20 and stats["nodes_projected"] <= 20


def test_public_zero_fact_ingest_preserves_existing_membership_and_selectors(backend_factory, tmp_path):
    backend = backend_factory()
    cold, output = grounded_source("Iris is ready.", "membership-existing")
    instance = formation_adapter(backend, tmp_path, Model(output))
    assert instance.ingest(cold).status == "completed"
    ref, = backend.resolve_target_entity_refs("Iris")
    original = backend._entity_membership_for_recall()["entity:" + ref.casefold()]
    cold = segment("Mira said she may visit.", "membership-occurrences-only")
    output = {"mentions": [mention(cold, "Mira", "named"),
                           mention(cold, "she", "pronoun", identity="unresolved")], "units": []}
    result = formation_adapter(backend, tmp_path, Model(output)).ingest(cold)
    assert result.status == "completed" and result.memory_ids == ()
    current = backend._entity_membership_for_recall()["entity:" + ref.casefold()]
    assert current is original
    assert current.positions is original.positions
    assert current.array_selector is original.array_selector and current.batch_selector is original.batch_selector
    assert [item.text for item in instance.recall("Iris status?", policy()).evidence] == ["Iris is ready."]
    other_ref, = backend.resolve_target_entity_refs("Mira")
    assert positions(backend, other_ref) == ()
    assert len(backend.trg.vector_db.id_to_index) == 1


def test_existing_refers_to_edges_gain_membership_after_vector_repair(backend_factory):
    backend = backend_factory()
    first = add(backend, "first", subject="Iris")
    backend.create_relationships([first])
    backend.trg.vector_db.fail_next = True
    with pytest.raises(OSError, match="graph-before-vector"):
        add(backend, "late", subject="Iris")
    backend.create_relationships(["event:late"])
    assert positions(backend, "Iris") == (0,)
    backend.ensure_event_persisted("event:late")
    assert positions(backend, "Iris") == (0, 1)
    writes = backend.trg.vector_db.write_attempts
    backend._entity_membership = None
    backend.ensure_event_persisted("event:late")
    assert positions(backend, "Iris") == (0, 1)
    assert backend.trg.vector_db.write_attempts == writes
    assert set(ids(backend, ("Iris",))) == {"first", "late"}
