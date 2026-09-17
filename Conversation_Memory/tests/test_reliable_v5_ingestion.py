"""v5 body/structure decoupling: durable bodies before optional structure.

Synthetic authorization/recovery contracts over deterministic mock models and
isolated state; these tests prove checkpoint/write ordering, not model
judgment.
"""
from __future__ import annotations

from types import ModuleType, SimpleNamespace
import sys

import pytest

from Conversation_Memory.adapter import reliable_formation as rf
from Conversation_Memory.tests.test_reliable_formation import (
    PROMPTS_V5, NoCalls, StagedModel, borrowing,
)


def borrowing_model(**kwargs):
    seg, model = borrowing(**kwargs)
    model.prompts = PROMPTS_V5
    return seg, model


def configured_v5(tmp_path, backend, model, *, cold=None):
    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from Conversation_Memory.adapter.models import BackendCandidate
    from Conversation_Memory.ingestion.state_store import IngestionStateStore

    memory = MagmaMemoryAdapter(
        backend, IngestionStateStore(tmp_path / "ingestion.json"),
        ingestion_version=rf.FORMATION_RELIABLE_VERSION_V5, formation_model=model,
        first_hit=FirstHitPolicy(), cold_store=cold,
        associative_read_profile="reliable-v1",
    )
    activations = []

    def activate(cue, *, target_entity_refs, exclude_evidence_ids):
        activations.append((cue, target_entity_refs, exclude_evidence_ids))
        candidates = tuple((BackendCandidate("Historical fact " + str(i), None, None,
                                            {"evidence_id": "old-" + str(i)}), 0.5, 0.0)
                           for i in range(2))
        return SimpleNamespace(candidates=candidates, safe_error_code=None)

    memory._activate_first_hit = activate
    return memory, activations


def state_of(memory, seg):
    return memory.state_store.get(memory.state_store.key(
        seg.segment_id, rf.FORMATION_RELIABLE_VERSION_V5))


def association_state(memory, seg):
    from Conversation_Memory.adapter._first_hit_ingestion import _stage_version
    return memory.state_store.get(memory.state_store.key(
        seg.segment_id, _stage_version(rf.FORMATION_RELIABLE_VERSION_V5)))


def test_g_stage_delivery_failure_keeps_durable_projection_free_bodies(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = borrowing_model()
    model.responses["G1"] = TimeoutError("synthetic unknown delivery")
    backend = DurableBackend(tmp_path / "graph.json")
    memory, activated = configured_v5(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "failed"
    assert result.safe_error_code == "reliable_stage_delivery_unknown" and not result.retryable
    assert [stage for stage, _ in model.calls] == ["F1", "F2", "G1"]
    assert activated == []
    state = state_of(memory, seg)
    assert state["status"] == "bodies_persisted"
    assert set(state["bodies"]) == {"memory_ids", "manifest"}
    assert len(result.memory_ids) == 1 and result.memory_ids == tuple(state["memory_ids"])
    mid = result.memory_ids[0]
    assert backend.find_memory_id(mid) == mid and mid in backend.vectors
    event = backend.events[mid]
    assert event["text"].startswith("User stated: ")
    metadata = event["metadata"]
    assert metadata["formation_version"] == rf.FORMATION_RELIABLE_VERSION_V5
    assert set(metadata["formation_receipts"]) == {"F1", "F2"}
    assert metadata["used_source_ids"] == metadata["source_package_ids"] == ["t0"]
    for projection_key in ("subject", "relation", "value", "subject_entity_ref",
                           "object_entity_ref", "mention_entity_refs", "mention_ids",
                           "referenced_time"):
        assert projection_key not in metadata
    assert backend.mentions == {}
    assert backend.semantic_flags == [False] and backend.entity_flags == [False]
    assert association_state(memory, seg)["status"] == "pending"
    resumed, _ = configured_v5(tmp_path, DurableBackend(backend.path), NoCalls())
    again = resumed.ingest(seg)
    assert again.status == "failed"
    assert again.safe_error_code == "reliable_stage_delivery_unknown"
    assert again.memory_ids == result.memory_ids
    assert len(resumed.state_store.get(resumed.state_store.key(
        seg.segment_id, rf.FORMATION_RELIABLE_VERSION_V5))["memory_ids"]) == 1
    assert sum(1 for key in resumed.backend.events if not key.startswith("old-")) == 1


def test_g2_unparseable_failure_keeps_durable_bodies(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = borrowing_model()
    model.responses["G2"] = "This is not JSON."
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured_v5(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "failed"
    assert result.safe_error_code == "reliable_stage_output_invalid"
    assert [stage for stage, _ in model.calls] == ["F1", "F2", "G1", "G2"]
    assert state_of(memory, seg)["status"] == "bodies_persisted"
    assert len(result.memory_ids) == 1 and result.memory_ids[0] in backend.events
    assert backend.mentions == {}


def test_crash_between_phases_recovers_structure_without_duplicates(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = borrowing_model()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured_v5(tmp_path, backend, model)
    original_put = memory.state_store.put
    crashed = {"done": False}

    def flaky_put(key, state):
        if not crashed["done"] and isinstance(state, dict) and state.get("status") == "bodies_persisted":
            crashed["done"] = True
            raise OSError("synthetic crash after durable bodies")
        return original_put(key, state)

    memory.state_store.put = flaky_put
    failed = memory.ingest(seg)
    assert crashed["done"] and failed.status == "failed" and failed.retryable
    assert len(failed.memory_ids) == 1
    assert state_of(memory, seg)["status"] == "in_progress"
    assert backend.find_memory_id(failed.memory_ids[0]) == failed.memory_ids[0]

    resumed_backend = DurableBackend(backend.path)
    resumed, activated = configured_v5(tmp_path, resumed_backend, borrowing_model()[1])
    result = resumed.ingest(seg)
    assert result.status == "completed", result
    assert result.memory_ids == failed.memory_ids
    assert [stage for stage, _ in resumed.formation_model.calls] == ["G1", "G2"]
    assert len(activated) == 1 and activated[0][1]
    assert sum(1 for key in resumed_backend.events if not key.startswith("old-")) == 1
    assert list(resumed_backend.vectors).count(result.memory_ids[0]) == 1
    metadata = resumed_backend.events[result.memory_ids[0]]["metadata"]
    assert metadata["subject"] == "Ada" and metadata["relation"] == "returned"
    assert metadata["subject_entity_ref"] and metadata["object_entity_ref"]
    assert metadata["mention_ids"] and metadata["referenced_time"] == "Tuesday"
    # Body-phase F1/F2 receipts grow to the full four-stage set on completion.
    assert set(metadata["formation_receipts"]) == {"F1", "F2", "G1", "G2"}
    # The deterministic normalizer has no bare-weekday rule (same as v4).
    assert metadata["temporal_mentions"] == metadata["dates_mentioned"] == []
    state = state_of(resumed, seg)
    assert state["status"] == "completed"
    assert state["bodies"]["memory_ids"] == list(result.memory_ids)
    association = association_state(resumed, seg)
    assert association["status"] == "completed"
    assert association["evidence_ids"] == [unit_id for unit_id in
                                           (event["metadata"]["evidence_id"]
                                            for key, event in resumed_backend.events.items()
                                            if not key.startswith("old-"))]
    assert len(resumed_backend.links) == 2 * len(association["link_plan"])
    before = resumed.state_store.path.read_bytes()
    assert resumed.ingest(seg).already_ingested
    assert resumed.state_store.path.read_bytes() == before


def test_completed_v5_replays_without_provider_and_without_mutation(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = borrowing_model()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, activated = configured_v5(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "completed", result
    assert [stage for stage, _ in model.calls] == list(PROMPTS_V5)
    assert len(activated) == 1
    metadata = backend.events[result.memory_ids[0]]["metadata"]
    assert metadata["subject_entity_ref"] and metadata["object_entity_ref"]
    assert metadata["provenance"]["ingestion_version"] == rf.FORMATION_RELIABLE_VERSION_V5
    resumed, calls = configured_v5(tmp_path, DurableBackend(backend.path), NoCalls())
    before = backend.path.read_bytes(), resumed.state_store.path.read_bytes()
    again = resumed.ingest(seg)
    assert again.status == "completed" and again.already_ingested
    assert again.memory_ids == result.memory_ids and calls == []
    assert (backend.path.read_bytes(), resumed.state_store.path.read_bytes()) == before


def test_tampered_bodies_checkpoint_fails_closed(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = borrowing_model()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured_v5(tmp_path, backend, model)
    assert memory.ingest(seg).status == "completed"
    key = memory.state_store.key(seg.segment_id, rf.FORMATION_RELIABLE_VERSION_V5)
    state = memory.state_store.get(key)
    state["bodies"]["manifest"] = "0" * 64
    memory.state_store.put(key, state)
    resumed, _ = configured_v5(tmp_path, DurableBackend(backend.path), NoCalls())
    result = resumed.ingest(seg)
    assert result.status == "failed" and result.safe_error_code == "state_corrupt"


def test_v5_structure_phase_attaches_approved_relative_time_projection(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend
    from Conversation_Memory.tests.test_reliable_formation import (
        fact, projection, segment,
    )
    from datetime import datetime

    seg = segment(("assistant", "Please return the cart tomorrow."),
                  ("user", "Yes, I agree to that deadline."))
    model = StagedModel([fact("The user agreed to return the cart tomorrow.", "t1")],
                        g1={"mentions": [], "projections": [
                            projection(time={"text": "tomorrow", "turn_id": "t0"})]},
                        prompts=PROMPTS_V5)
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured_v5(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "completed", result
    event = backend.events[result.memory_ids[0]]
    metadata = event["metadata"]
    assert event["timestamp"] == seg.turns[1].timestamp
    assert metadata["origin_turn_id"] == metadata["turn_id"] == "t1"
    assert metadata["referenced_time"] == "tomorrow"
    assert metadata["temporal_mentions"] and metadata["dates_mentioned"]
    assert datetime.fromisoformat(
        metadata["temporal_mentions"][0]["reference_timestamp"]) == seg.turns[0].timestamp


def test_zero_fact_window_still_persists_mentions_without_events(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend
    from Conversation_Memory.tests.test_reliable_formation import (
        fact, mention, projection, segment,
    )

    seg = segment(("user", "Ada waved."))
    g1 = {"mentions": [mention("ada", "Ada")], "projections": []}
    model = StagedModel(g1=g1, prompts=PROMPTS_V5)
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured_v5(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "completed", result
    assert result.memory_ids == ()
    assert [stage for stage, _ in model.calls] == ["F1", "G1", "G2"]
    assert [key for key in backend.events if not key.startswith("old-")] == []
    assert len(backend.mentions) == 1
    bodies = state_of(memory, seg)["bodies"]
    assert bodies["memory_ids"] == [] and set(bodies) == {"memory_ids", "manifest"}
    assert association_state(memory, seg)["status"] == "completed"


def stub_real_backend(tmp_path, monkeypatch):
    from Conversation_Memory.adapter import _source_backend
    from Conversation_Memory.adapter.backend import RealMagmaBackend

    graph = ModuleType("memory.graph_db")
    graph.EventNode = type("EventNode", (), {})
    graph.NodeType = SimpleNamespace(EVENT="EVENT", ENTITY="ENTITY")
    graph.TraversalConstraints = object

    class Trg:
        def __init__(self, **kwargs):
            self.graph_db = SimpleNamespace()

        def _extract_event(self, content, metadata=None):
            return SimpleNamespace(content_narrative=content, entities=["Alice"], keywords=[])

    module = ModuleType("memory.trg_memory")
    module.TemporalResonanceGraphMemory = Trg
    package = ModuleType("memory")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "memory", package)
    monkeypatch.setitem(sys.modules, "memory.graph_db", graph)
    monkeypatch.setitem(sys.modules, "memory.trg_memory", module)
    monkeypatch.setattr(RealMagmaBackend, "_rebuild_indexes", lambda *a: None)
    monkeypatch.setattr(RealMagmaBackend, "_rebuild_first_hit_view", lambda *a: None)
    monkeypatch.setattr(_source_backend, "rebuild", lambda *a: None)
    backend = RealMagmaBackend(tmp_path / "backend")
    nodes = {}
    backend.trg.graph_db = SimpleNamespace(nodes=nodes, get_node=nodes.get)
    return backend, nodes


def test_update_event_projection_bounded_idempotent_and_fail_closed(tmp_path, monkeypatch):
    backend, nodes = stub_real_backend(tmp_path, monkeypatch)
    event = SimpleNamespace(node_type="EVENT", attributes={
        "evidence_id": "e1", "formation_version": rf.FORMATION_RELIABLE_VERSION_V5})
    nodes["e1"] = event
    nodes["entity:e_001"] = SimpleNamespace(node_type="ENTITY", attributes={"entity_ref": "E_001"})
    projection = {"subject": "Ada", "relation": "returned", "value": "the cart",
                  "subject_entity_ref": "E_002", "subject_entity_surface": "Ada",
                  "object_entity_ref": "E_003", "object_entity_surface": "cart",
                  "mention_entity_refs": ["E_001"], "mention_entity_surfaces": ["my"],
                  "mention_ids": ["mention_v5:x"], "referenced_time": None,
                  "temporal_mentions": [], "dates_mentioned": []}
    backend.update_event_projection("e1", projection)
    assert event.attributes["subject"] == "Ada"
    assert event.attributes["mention_entity_refs"] == ["E_001"]
    assert event.attributes["temporal_mentions"] == []
    backend.update_event_projection("e1", dict(projection))
    with pytest.raises(ValueError, match="event_projection_conflict"):
        backend.update_event_projection("e1", {**projection, "subject": "Bela"})
    assert event.attributes["subject"] == "Ada"
    with pytest.raises(ValueError, match="event_projection_invalid"):
        backend.update_event_projection("e1", {"unknown_key": {}})
    backend.update_event_projection("e1", {"formation_receipts": {"F1": {"request_digest": "a"}}})
    backend.update_event_projection("e1", {"formation_receipts": {"F1": {"request_digest": "a"},
                                                                   "G2": {"request_digest": "b"}}})
    assert set(event.attributes["formation_receipts"]) == {"F1", "G2"}
    with pytest.raises(ValueError, match="event_projection_conflict"):
        backend.update_event_projection("e1", {"formation_receipts": {"F1": {"request_digest": "changed"}}})
    with pytest.raises(ValueError, match="memory_event_missing"):
        backend.update_event_projection("missing", projection)
    with pytest.raises(ValueError, match="memory_event_missing"):
        backend.update_event_projection("entity:e_001", projection)


def test_real_backend_suppresses_entity_guesses_for_v5_bodies(tmp_path, monkeypatch):
    backend, _ = stub_real_backend(tmp_path, monkeypatch)
    old = backend.trg._extract_event("Ada returned the cart.",
                                     {"formation_version": "grounded-formation-v2"})
    new = backend.trg._extract_event("Ada returned the cart.",
                                     {"formation_version": rf.FORMATION_RELIABLE_VERSION_V5})
    assert old.entities == ["Alice"] and new.entities == []
    assert old.content_narrative == new.content_narrative == "Ada returned the cart."
