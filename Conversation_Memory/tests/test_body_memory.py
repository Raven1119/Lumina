"""Body authorization, durable references and visible unit budgets (no models)."""
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from Conversation_Memory.adapter import reliable_formation as rf
from Conversation_Memory.adapter import _reliable_projection as rp
from Conversation_Memory.adapter._body_formation import F1_BODY_PROMPT, F2_BODY_SUFFIX, body_payloads, parse_body_f1
from Conversation_Memory.adapter.body_payload import BodyPayloadStore, FORMATION_BODY_VERSION, validate_body
from Conversation_Memory.adapter._body_recall import BodyRecallPolicy, pack_bodies
from Conversation_Memory.adapter._associative_recall import Activation
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import BackendCandidate, RecallPolicy
from Conversation_Memory.ingestion.state_store import IngestionStateStore
from Conversation_Memory.tests.test_reliable_formation import StagedModel, NoCalls, fact, segment, approve_bodies
from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

PROMPTS = {"F1": F1_BODY_PROMPT, "F2": rf.F2_PROMPT + F2_BODY_SUFFIX,
           "G1": rp.G1_PROMPT_V5, "G2": rp.G2_PROMPT_V5}


def window():
    return segment(("user", "Only replace the cracked gauge; retain all other fixtures."),
                   ("assistant", "I propose replacing the whole panel."),
                   ("user", "No, do not replace the panel. The inspector has not authorized operation."))


def grouped_model(seg=None, *, verdicts=None, overrides=None):
    seg = seg or window()
    def f2(payload):
        out = approve_bodies(payload)
        for row in out["decisions"]:
            row["verdict"] = (verdicts or {}).get(row["fact_id"], row["verdict"])
        return out
    return StagedModel(prompts=PROMPTS, f2=f2, overrides={
        "F1": {"blocks": [{"units": [fact(t.content, t.turn_id, [x.turn_id for x in seg.turns[:i+1]])
                                      for i, t in enumerate(seg.turns)]}]}, **(overrides or {})})


def formed_payload(seg=None, *, verdicts=None):
    seg = seg or window()
    accepted, f1, f2, progress = rf.form_reliable_bodies(seg, grouped_model(seg, verdicts=verdicts),
        checkpoint=lambda *_: None, version=FORMATION_BODY_VERSION)
    bodies, manifest = body_payloads(seg, f1, f2, progress)
    return bodies[0], manifest, accepted


class BodyBackend(DurableBackend):
    def __init__(self, path, **kwargs):
        super().__init__(path, **kwargs)
        self.body_store = BodyPayloadStore(path.parent / "bodies")

    def put_memory_body(self, payload, *, repair=False):
        return self.body_store.put(payload, repair=repair)

    def read_memory_body(self, ref, *, max_bytes):
        return self.body_store.read(ref, max_bytes=max_bytes)

    def ensure_event_body_reference(self, mid, eid, text, refs):
        event = self.events[mid]
        assert event["text"] == text and event["metadata"]["evidence_id"] == eid
        old = {k: event["metadata"].get(k) for k in refs}
        event["metadata"].update(refs)
        return old != refs


def configured(tmp_path, backend=None, model=None):
    backend = backend or BodyBackend(tmp_path / "graph.json")
    adapter = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "state.json"),
        ingestion_version=FORMATION_BODY_VERSION, formation_model=model or grouped_model(),
        first_hit=FirstHitPolicy(), associative_read_profile="body-recall-v1")
    adapter._activate_first_hit = lambda *a, **kw: Activation()
    return adapter


def read_fixture(tmp_path, *, verdicts=None, body_policy=None):
    body, manifest, _ = formed_payload(verdicts=verdicts)
    store = BodyPayloadStore(tmp_path / "bodies"); store.put(body)
    reads = []
    def read(ref, *, max_bytes):
        reads.append((ref, max_bytes))
        return store.read(ref, max_bytes=max_bytes)
    adapter = SimpleNamespace(backend=SimpleNamespace(read_memory_body=read),
                             body_recall_policy=body_policy or BodyRecallPolicy(), first_hit=FirstHitPolicy())
    facts = []
    for slot in body["slots"]:
        if slot["status"] != "supported":
            continue
        eid = slot["unit_id"]
        facts.append(BackendCandidate(slot["text"], slot["provenance"]["source_timestamp"], None,
            {"evidence_id": eid, "provenance": slot["provenance"], "formation_receipts": body["receipts"],
             **manifest["references"][eid]}))
    return adapter, body, facts, reads, store


def test_direct_dialogue_grouping_keeps_frozen_text_and_only_declared_sources():
    seg = window(); model = grouped_model(seg)
    accepted, f1, f2, progress = rf.form_reliable_bodies(seg, model, checkpoint=lambda *_: None, version=FORMATION_BODY_VERSION)
    body, _, _ = formed_payload(seg)
    assert [s["text"] for s in body["slots"]] == [f["text"] for f in accepted]
    assert [f["text"] for f in model.calls[1][1]["candidates"]] == [f["text"] for f in accepted]
    assert [s["provenance"]["source_role"] for s in body["slots"]] == ["user", "assistant", "user"]
    assert model.calls[0][1] == {"turns": list(rf._sources(seg).values())}
    assert all("supporting_span" not in ref for s in body["slots"] for ref in s["source_refs"])


def test_f1_does_not_silently_borrow_previous_or_future_source():
    seg = window()
    raw = {"blocks": [{"units": [fact("short response", "t1", ["t1"]), fact("future claim", "t1", ["t1", "t2"])]}]}
    parsed, _ = parse_body_f1(raw, seg)
    assert parsed["facts"][0]["allowed_source_ids"] == ["t1"]
    assert parsed["groups"] == [["f0", None]] and parsed["issues"][0]["status"] == "pending"


def test_f2_rejected_text_never_enters_body_and_gap_keeps_position(tmp_path):
    body, _, _ = formed_payload(verdicts={"f1": "rejected"})
    assert not body["complete"] and body["slots"][1] == {"position": 1, "status": "rejected"}
    assert "whole panel" not in str(body)
    adapter, body, facts, _, _ = read_fixture(tmp_path, verdicts={"f1": "rejected"})
    act = Activation(((facts[0], 1, 0),), seed_fact_ids=(facts[0].metadata["evidence_id"],))
    result = pack_bodies(adapter, "panel", RecallPolicy(max_chars=2000, max_bytes=4000, max_evidence_items=3), act)
    assert "非完整经过" in result.rendered_text and "缺口=2" in result.rendered_text
    assert "whole panel" not in result.rendered_text and "not authorized" in result.rendered_text


def test_v7_payload_precedes_event_and_completed_restart_reuses_receipts(tmp_path):
    adapter = configured(tmp_path)
    backend = adapter.backend; original = backend.add_event
    def add(text, time, metadata, **kwargs):
        body = backend.read_memory_body(metadata["body_ref"], max_bytes=65536).payload
        assert body and any(s.get("text") == text for s in body["slots"])
        assert "slots" not in metadata
        return original(text, time, metadata, **kwargs)
    backend.add_event = add
    result = adapter.ingest(window())
    assert result.status == "completed", result
    assert len(result.memory_ids) == 3
    resumed = configured(tmp_path, BodyBackend(backend.path), NoCalls())
    before = backend.path.read_bytes(), resumed.state_store.path.read_bytes()
    again = resumed.ingest(window())
    assert again.status == "completed" and again.already_ingested
    assert again.memory_ids == result.memory_ids
    assert before == (backend.path.read_bytes(), resumed.state_store.path.read_bytes())
    assert resumed.state_store.get(resumed.state_store.key(window().segment_id, rf.FORMATION_RELIABLE_VERSION_V6)) is None


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_ingestion_repairs_lost_body_from_receipts_without_new_generation(tmp_path, damage):
    adapter = configured(tmp_path); result = adapter.ingest(window()); assert result.status == "completed"
    ref = adapter.backend.events[result.memory_ids[0]]["metadata"]["body_ref"]
    path = adapter.backend.body_store._path(ref)
    original = path.read_bytes()
    if damage == "missing": path.unlink()
    else: path.write_text('{"invalid":true}')
    resumed = configured(tmp_path, BodyBackend(adapter.backend.path), NoCalls())
    assert resumed.ingest(window()).status == "completed"
    assert path.read_bytes() == original


def test_g_delivery_failure_keeps_body_readable_and_pending(tmp_path):
    adapter = configured(tmp_path, model=grouped_model(overrides={"G1": TimeoutError("synthetic")}))
    result = adapter.ingest(window())
    assert result.status == "failed" and result.safe_error_code == "reliable_stage_delivery_unknown"
    state = adapter.state_store.get(adapter.state_store.key(window().segment_id, FORMATION_BODY_VERSION))
    assert state["status"] == "bodies_persisted" and len(result.memory_ids) == 3
    for mid in result.memory_ids:
        ref = adapter.backend.events[mid]["metadata"]["body_ref"]
        assert adapter.backend.read_memory_body(ref, max_bytes=65536).payload
    resumed = configured(tmp_path, BodyBackend(adapter.backend.path), NoCalls())
    assert resumed.ingest(window()).safe_error_code == "reliable_stage_delivery_unknown"


def test_vector_gap_remains_repairable_with_same_ids(tmp_path):
    backend = BodyBackend(tmp_path / "graph.json", crash="graph_before_vector")
    adapter = configured(tmp_path, backend)
    result = adapter.ingest(window()); assert result.status == "failed"
    mid = next(mid for mid in backend.events if mid.startswith("grounded_memory_v7:"))
    resumed = configured(tmp_path, BodyBackend(backend.path), grouped_model())
    done = resumed.ingest(window())
    assert done.status == "completed" and mid in done.memory_ids
    assert mid in resumed.backend.vectors
    assert [s for s, _ in resumed.formation_model.calls] == ["G1", "G2"]


def test_one_zero_attention_trigger_restores_siblings_without_relation_filter(tmp_path):
    adapter, body, facts, reads, _ = read_fixture(tmp_path)
    eid = facts[0].metadata["evidence_id"]
    activation = Activation(((facts[0], 0.4, 0.0),), seed_fact_ids=(eid,))
    result = pack_bodies(adapter, "gauge", RecallPolicy(max_chars=2000, max_bytes=4000,
        max_evidence_items=3, relation_surfaces=("unsupported relation",)), activation)
    assert len(reads) == 1 and len(result.facts.evidence) == 3
    assert all(f.text in result.rendered_text for f in facts)
    assert "已核验转述正文" in result.rendered_text and "[M2 LUMINA]" in result.rendered_text
    assert adapter._last_body_recall_diagnostics["unit_routes"][facts[1].metadata["evidence_id"]]["route"] == "body_expansion"


def test_duplicate_activation_uses_max_score_and_one_payload(tmp_path):
    adapter, body, facts, reads, _ = read_fixture(tmp_path)
    rows = tuple((f, h, 0) for f, h in zip(facts, [0.2, 0.7, 0.4]))
    result = pack_bodies(adapter, "gauge", RecallPolicy(max_chars=2000, max_bytes=4000, max_evidence_items=3), Activation(rows, seed_fact_ids=(facts[0].metadata["evidence_id"],)))
    assert len(reads) == 1 and adapter._last_body_recall_diagnostics["groups"][body["body_ref"]]["score"] == 0.7
    assert len(result.facts.evidence) == 3 and all(result.rendered_text.count(f.text) == 1 for f in facts)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_ref", "digest", "unit", "receipt", "list_ref", "null_receipt"])
def test_bad_body_reference_falls_back_to_whole_fact_without_writing(tmp_path, damage):
    adapter, body, facts, reads, store = read_fixture(tmp_path)
    if damage in {"missing", "corrupt"}:
        path = store._path(body["body_ref"])
        if damage == "missing": path.unlink()
        else: path.write_text('bad')
    else:
        key = {"wrong_ref": "body_ref", "digest": "body_digest", "unit": "body_unit_ref", "receipt": "formation_receipts",
               "list_ref": "body_ref", "null_receipt": "formation_receipts"}[damage]
        facts[0].metadata[key] = {"receipt": {}, "list_ref": [], "null_receipt": None}.get(damage, "invalid")
    before = {p: p.read_bytes() for p in tmp_path.rglob('*.json')}
    result = pack_bodies(adapter, "gauge", RecallPolicy(max_chars=2000, max_bytes=4000), Activation(((facts[0], 1, 0),), seed_fact_ids=(facts[0].metadata["evidence_id"],)))
    assert [e.text for e in result.facts.evidence] == [facts[0].text]
    assert "已核验转述正文" not in result.rendered_text and result.safe_error_code == "body_payload_partial"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*.json')}


def test_real_visible_unit_and_byte_caps_do_not_count_a_body_as_one_fact(tmp_path):
    adapter, body, facts, _, _ = read_fixture(tmp_path)
    act = Activation(((facts[0], 1, 0),), seed_fact_ids=(facts[0].metadata["evidence_id"],))
    result = pack_bodies(adapter, "gauge", RecallPolicy(max_chars=900, max_bytes=900, max_evidence_items=1), act)
    assert len(result.facts.evidence) == 1 and result.facts.evidence[0].text == facts[0].text
    assert "非完整经过" in result.rendered_text and len(result.rendered_text.encode()) <= 900


def test_header_overhead_cannot_remove_a_whole_fact_that_fits(tmp_path):
    adapter, body, facts, _, _ = read_fixture(tmp_path)
    text = "[M1 USER]\n" + facts[0].text
    act = Activation(((facts[0], 1, 0),), seed_fact_ids=(facts[0].metadata["evidence_id"],))
    result = pack_bodies(adapter, "gauge", RecallPolicy(max_chars=len(text), max_bytes=len(text.encode()), max_evidence_items=1), act)
    assert result.rendered_text == text and len(result.facts.evidence) == 1


def test_reserved_bare_anchor_upgrades_to_one_complete_body(tmp_path):
    seg = segment(("user", "Do not publish."), ("user", "Await consent."))
    body, manifest, _ = formed_payload(seg)
    store = BodyPayloadStore(tmp_path / "bodies"); store.put(body)
    slot = body["slots"][0]; eid = slot["unit_id"]
    fact = BackendCandidate(slot["text"], slot["provenance"]["source_timestamp"], None,
        {"evidence_id": eid, "provenance": slot["provenance"], "formation_receipts": body["receipts"], **manifest["references"][eid]})
    adapter = SimpleNamespace(backend=SimpleNamespace(read_memory_body=store.read), body_recall_policy=BodyRecallPolicy(max_visible_blocks=1))
    full = "[B1 已核验转述正文]\n[M1 USER]\nUser stated: Do not publish.\n[M2 USER]\nUser stated: Await consent."
    result = pack_bodies(adapter, "publication", RecallPolicy(max_evidence_items=2, max_chars=len(full), max_bytes=1000),
        Activation(((fact, 1, 0),), seed_fact_ids=(eid,)))
    assert result.rendered_text == full
    assert adapter._last_body_recall_diagnostics["visible_blocks"] == 1


def test_payload_io_budget_stops_loading_and_keeps_legal_fact(tmp_path):
    adapter, body, facts, reads, _ = read_fixture(tmp_path, body_policy=BodyRecallPolicy(max_payload_bytes=8))
    act = Activation(((facts[0], 1, 0),), seed_fact_ids=(facts[0].metadata["evidence_id"],))
    result = pack_bodies(adapter, "gauge", RecallPolicy(max_chars=900, max_bytes=900), act)
    assert len(reads) == 1 and adapter._last_body_recall_diagnostics["payload_bytes"] <= 8
    assert facts[0].text in result.rendered_text and len(result.facts.evidence) == 1


def test_body_ids_keep_source_identity_and_file_paths_cannot_be_chosen(tmp_path):
    first, _, _ = formed_payload()
    second, _, _ = formed_payload(replace(window(), conversation_id="another-conversation"))
    assert first["body_ref"] != second["body_ref"]
    store = BodyPayloadStore(tmp_path)
    assert store.read('../../secrets').payload is None
    broken = deepcopy(first); broken["slots"][0]["text"] = "forged"
    with pytest.raises(ValueError): store.put(broken)
    assert not list(tmp_path.iterdir())
