"""Isolated real-MAGMA body/vector/graph boundaries, deterministic Formation."""
from dataclasses import replace

import pytest

from Conversation_Memory.adapter.backend import RealMagmaBackend
from Conversation_Memory.adapter.body_payload import BodyPayloadStore, FORMATION_BODY_VERSION
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.ingestion.state_store import IngestionStateStore
from Conversation_Memory.tests.test_body_memory import window, grouped_model
from Conversation_Memory.tests.test_reliable_formation import append_cold, NoCalls
from core.cold_draft_store import ColdDraftStore
from Dream.cold_draft_digest import ColdDraftDigestionTask, ColdDraftSegmentConverter
from Dream.models import DreamRunPolicy
from Dream.runner import DreamRunner


@pytest.mark.parametrize("boundary", ["payload", "event_vector", "projection"])
def test_real_body_restart_preserves_ids_payloads_and_cold_responsibility(tmp_path, monkeypatch, boundary):
    owner = ColdDraftStore(tmp_path / "cold.jsonl", source_window_segments=1, source_window_bytes=65536)
    append_cold(owner, window())
    seg = ColdDraftSegmentConverter().convert(owner.list_pending()[0], FORMATION_BODY_VERSION)
    def make(model):
        return MagmaMemoryAdapter(RealMagmaBackend(tmp_path / "magma"), IngestionStateStore(tmp_path / "state.json"),
            ingestion_version=FORMATION_BODY_VERSION, formation_model=model, first_hit=FirstHitPolicy(),
            cold_store=owner, associative_read_profile="body-recall-v1")
    def run(memory):
        class Provider:
            def get(self, version):
                assert version == FORMATION_BODY_VERSION
                return memory
        return DreamRunner(owner, ColdDraftDigestionTask(owner, Provider())).run_once(
            DreamRunPolicy(ingestion_version=FORMATION_BODY_VERSION))
    memory = make(grouped_model(seg))
    def fail(*args, **kwargs):
        memory.backend.persist()
        raise OSError("synthetic durable boundary interruption")
    if boundary == "payload":
        monkeypatch.setattr(memory.backend, "add_event", fail)
    elif boundary == "event_vector":
        monkeypatch.setattr(memory.backend.trg.vector_db, "add_vector", fail)
    else:
        monkeypatch.setattr(memory.backend, "update_event_projection", fail)
    interrupted = run(memory)
    assert interrupted.failed == 1 and interrupted.consumed == 0 and owner.list_pending()
    paths = list((tmp_path / "magma/bodies/v1").glob("*/*.json"))
    assert len(paths) == 1
    payload_bytes = paths[0].read_bytes()
    old_ids = {n.node_id for n in memory.backend.trg.graph_db.nodes.values() if n.attributes.get("evidence_id")}
    next_model = NoCalls() if boundary == "projection" else grouped_model(seg)
    resumed = make(next_model)
    completed = run(resumed)
    assert completed.failed == 0 and completed.consumed == 1 and not owner.list_pending()
    events = [n for n in resumed.backend.trg.graph_db.nodes.values() if n.attributes.get("evidence_id")]
    assert len(events) == len(resumed.backend.trg.vector_db.entries) == 3
    assert old_ids <= {n.node_id for n in events}
    assert paths[0].read_bytes() == payload_bytes
    if boundary != "projection":
        assert [stage for stage, _ in next_model.calls] == ["G1", "G2"]
    # Old checkpoint meaning and namespace are untouched; all v7 stages are now frozen.
    assert resumed.state_store.get(resumed.state_store.key(seg.segment_id, "grounded-formation-v6")) is None
    # Missing and corrupt body payloads fall back during read, repaired only by ingestion.
    for damaged in (None, b"corrupt"):
        if damaged is None:
            paths[0].unlink()
        else:
            paths[0].write_bytes(damaged)
        fresh = make(NoCalls())
        with monkeypatch.context() as patch:
            patch.setattr(fresh.state_store, "read_all", lambda: (_ for _ in ()).throw(AssertionError("read scanned checkpoints")))
            result = fresh.recall("cracked gauge inspector authorization", RecallPolicy(max_evidence_items=3, max_chars=5000))
        assert result.evidence and "已核验转述正文" not in result.rendered_text
        assert (not paths[0].exists()) if damaged is None else paths[0].read_bytes() == damaged
        restored = fresh.ingest(seg)
        assert restored.status == "completed" and restored.already_ingested
        assert paths[0].read_bytes() == payload_bytes
    # Expire the original segment from the bounded Cold source window.
    other = replace(window(), segment_id="later-unrelated", turns=tuple(replace(t, turn_id="later-"+t.turn_id) for t in window().turns))
    append_cold(owner, other)
    final = make(NoCalls())
    with monkeypatch.context() as patch:
        patch.setattr(final.state_store, "read_all", lambda: (_ for _ in ()).throw(AssertionError("read scanned checkpoints")))
        result = final.recall("cracked gauge inspector authorization", RecallPolicy(max_evidence_items=3, max_chars=5000))
    assert len(result.evidence) == 3 and "已核验转述正文" in result.rendered_text
    assert "not authorized operation" in result.rendered_text
    assert len(final.backend.trg.vector_db.entries) == 3
