"""v6 grounded formation ingestion: cross-reference bodies, disjoint checkpoints.

Synthetic authorization/recovery contracts over deterministic mock models and
isolated state; these tests prove checkpoint/write ordering, not model
judgment.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from Conversation_Memory.adapter import reliable_formation as rf
from Conversation_Memory.tests.test_reliable_formation import (
    PROMPTS_V6, NoCalls, StagedModel, fact, segment,
)


def configured_v6(tmp_path, backend, model, *, cold=None):
    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from Conversation_Memory.adapter.models import BackendCandidate
    from Conversation_Memory.ingestion.state_store import IngestionStateStore

    memory = MagmaMemoryAdapter(
        backend, IngestionStateStore(tmp_path / "ingestion.json"),
        ingestion_version=rf.FORMATION_RELIABLE_VERSION_V6, formation_model=model,
        first_hit=FirstHitPolicy(), cold_store=cold,
        associative_read_profile="reliable-v2",
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
        seg.segment_id, rf.FORMATION_RELIABLE_VERSION_V6))


def cross_reference_window():
    seg = segment(("user", "Lumina 建议我先做小样。"),
                  ("assistant", "用户刚才说他明天会回来。"))
    model = StagedModel([fact("Lumina 建议我先做小样。", "t0"),
                         fact("用户刚才说他明天会回来。", "t1")], prompts=PROMPTS_V6)
    return seg, model


def test_v6_cross_reference_bodies_ingest_and_window_completes(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = cross_reference_window()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, activated = configured_v6(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "completed", result
    assert [stage for stage, _ in model.calls] == list(PROMPTS_V6)
    assert len(result.memory_ids) == 2 and len(activated) == 2
    events = [backend.events[mid] for mid in result.memory_ids]
    assert [event["text"] for event in events] == [
        "User stated: Lumina 建议我先做小样。",
        "Lumina stated: 用户刚才说他明天会回来。"]
    for event, turn in zip(events, seg.turns):
        metadata = event["metadata"]
        assert metadata["evidence_id"].startswith("grounded_memory_v6:")
        assert metadata["formation_version"] == rf.FORMATION_RELIABLE_VERSION_V6
        assert metadata["provenance"]["ingestion_version"] == rf.FORMATION_RELIABLE_VERSION_V6
        assert metadata["origin_turn_id"] == turn.turn_id
        assert metadata["role"] == turn.role
        assert set(metadata["formation_receipts"]) == set(PROMPTS_V6)
    state = state_of(memory, seg)
    assert state["status"] == "completed"
    assert state["formation"]["schema_version"] == rf.PROGRESS_VERSION_V6
    assert state["bodies"]["memory_ids"] == list(result.memory_ids)
    from Conversation_Memory.adapter._first_hit_ingestion import _stage_version
    association = memory.state_store.get(memory.state_store.key(
        seg.segment_id, _stage_version(rf.FORMATION_RELIABLE_VERSION_V6)))
    assert association["status"] == "completed"
    assert association["evidence_ids"] == [event["metadata"]["evidence_id"] for event in events]


def test_v6_completed_replays_without_provider_and_without_mutation(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = cross_reference_window()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured_v6(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "completed", result
    resumed, calls = configured_v6(tmp_path, DurableBackend(backend.path), NoCalls())
    before = backend.path.read_bytes(), resumed.state_store.path.read_bytes()
    again = resumed.ingest(seg)
    assert again.status == "completed" and again.already_ingested
    assert again.memory_ids == result.memory_ids and calls == []
    assert (backend.path.read_bytes(), resumed.state_store.path.read_bytes()) == before


def test_v6_checkpoint_keys_stay_disjoint_from_v4_and_v5(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = cross_reference_window()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured_v6(tmp_path, backend, model)
    assert memory.ingest(seg).status == "completed"
    store = memory.state_store
    assert store.get(store.key(seg.segment_id, "first-hit-v1:grounded-formation-v6"))
    for other in ("first-hit-v1:grounded-formation-v4",
                  "first-hit-v1:grounded-formation-v5", "first-hit-v1"):
        assert store.get(store.key(seg.segment_id, other)) is None
    assert store.get(store.key(seg.segment_id, rf.FORMATION_RELIABLE_VERSION_V6))
    for other in (rf.FORMATION_RELIABLE_VERSION, rf.FORMATION_RELIABLE_VERSION_V5):
        assert store.get(store.key(seg.segment_id, other)) is None


def test_v6_body_phase_vector_loss_repairs_on_restart_without_duplicates(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = cross_reference_window()
    backend = DurableBackend(tmp_path / "graph.json", crash="graph_before_vector")
    memory, _ = configured_v6(tmp_path, backend, model)
    failed = memory.ingest(seg)
    assert failed.status == "failed" and failed.retryable
    assert failed.safe_error_code == "memory_write_failed" and failed.memory_ids == ()
    assert state_of(memory, seg)["status"] == "in_progress"
    mid = next(key for key in backend.events if not key.startswith("old-"))
    reloaded = DurableBackend(backend.path)
    assert mid in reloaded.events and mid not in reloaded.vectors

    resumed, _ = configured_v6(tmp_path, DurableBackend(backend.path),
                               cross_reference_window()[1])
    result = resumed.ingest(seg)
    assert result.status == "completed", result
    assert len(result.memory_ids) == 2 and result.memory_ids[0] == mid
    assert resumed.backend.repaired == [mid]
    assert [stage for stage, _ in resumed.formation_model.calls] == ["G1", "G2"]
    assert sum(1 for key in resumed.backend.events if not key.startswith("old-")) == 2
    assert all(memory_id in resumed.backend.vectors for memory_id in result.memory_ids)
    state = state_of(resumed, seg)
    assert state["status"] == "completed"
    assert state["bodies"]["memory_ids"] == list(result.memory_ids)


def test_v6_body_vector_repair_failure_stays_retryable_without_advancing(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = cross_reference_window()
    backend = DurableBackend(tmp_path / "graph.json", crash="graph_before_vector")
    memory, _ = configured_v6(tmp_path, backend, model)
    failed = memory.ingest(seg)
    assert failed.status == "failed" and failed.retryable
    mid = next(key for key in backend.events if not key.startswith("old-"))

    resumed_backend = DurableBackend(backend.path)

    def broken(memory_id):
        raise ValueError("memory_event_embedding_missing")

    resumed_backend.ensure_event_persisted = broken
    resumed, _ = configured_v6(tmp_path, resumed_backend, cross_reference_window()[1])
    result = resumed.ingest(seg)
    assert result.status == "failed" and result.retryable
    assert result.safe_error_code == "memory_write_failed"
    assert state_of(resumed, seg)["status"] == "in_progress"
    assert mid not in resumed_backend.vectors
    assert [stage for stage, _ in resumed.formation_model.calls] == []
