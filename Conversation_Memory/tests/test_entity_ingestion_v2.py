from __future__ import annotations

import copy
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest

from Conversation_Memory.adapter.entity_consolidation import EntityCandidate
from Conversation_Memory.adapter.grounded_formation import FORMATION_ENTITY_VERSION
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import ColdDraftSegment, ColdDraftTurn
from Conversation_Memory.ingestion.state_store import IngestionStateStore


def segment(text, sid="s1"):
    now = datetime(2026, 9, 13, tzinfo=UTC)
    return ColdDraftSegment(sid, "synthetic", "pending_digest", (
        ColdDraftTurn(sid + "-u1", "user", text, now, "UTC", "client"),
    ), now, "UTC", "2")


def mention(seg, surface, handle, *, start=None, **identity):
    turn = seg.turns[0]
    start = turn.content.index(surface) if start is None else start
    return {"handle": handle, "surface": surface, "turn_id": turn.turn_id,
            "source_start": start, "source_end": start + len(surface),
            "identity": "named", **identity}


def unit(seg, text, subject, relation, value, s, o=None):
    return {"text": text, "subject": subject, "relation": relation, "value": value,
            "source_refs": [{"turn_id": seg.turns[0].turn_id, "supporting_span": text}],
            "referenced_time": None, "subject_mention": s, "object_mention": o,
            "mentions": [v for v in (s, o) if v]}


class Model:
    def __init__(self, output=None, *, fail_verify=False, repairs=None):
        self.output = output
        self.fail_verify = fail_verify
        self.repairs = repairs
        self.calls = []
        self.verifications = []

    def generate(self, recent_context, user_message, *, system_prompt):
        payload = json.loads(user_message)
        if system_prompt.startswith("Extract source-grounded"):
            self.calls.append("extract")
            assert self.output is not None, "completed extraction was repeated"
            return json.dumps(self.output, ensure_ascii=False)
        if system_prompt.startswith("Repair ONLY invalid source_refs"):
            self.calls.append("repair")
            return json.dumps(self.repairs if self.repairs is not None else {"repairs": []})
        self.calls.append("verify")
        self.verifications.append(payload)
        if self.fail_verify:
            raise RuntimeError("synthetic provider failure")
        return json.dumps({
            "units": [{"supported": True} for _ in payload["units"]],
            "mentions": [{"supported": True, "identity_supported": True}
                         for _ in payload["mentions"]],
        })


class Backend:
    def __init__(self, fail_persist=0):
        self.events = {}
        self.mentions = {}
        self.candidates = []
        self.persist_calls = 0
        self.fail_persist = fail_persist
        self.linked = []

    def find_entity_candidates(self, surface, *, limit):
        refs = {c.entity_ref: c for c in self.candidates if c.canonical_surface == surface}
        refs.update({r["entity_ref"]: EntityCandidate(r["entity_ref"], r["surface"])
                     for r in self.mentions.values()
                     if r["entity_ref"] and r["surface"] == surface})
        return tuple(refs.values())[:limit]

    def upsert_entity_mentions(self, records):
        for record in records:
            self.mentions[record["mention_id"]] = copy.deepcopy(record)

    def list_entity_mentions(self, query, *, limit):
        return tuple(r for r in self.mentions.values() if r["surface"] in query)[:limit]

    def find_memory_id(self, evidence_id):
        return evidence_id if evidence_id in self.events else None

    def add_event(self, text, timestamp, metadata):
        mid = metadata["evidence_id"]
        assert mid not in self.events
        self.events[mid] = {"text": text, "timestamp": timestamp, "metadata": metadata}
        return mid

    def persist(self):
        self.persist_calls += 1
        if self.persist_calls == self.fail_persist:
            raise OSError("synthetic graph persistence failure")

    def create_relationships(self, ids):
        self.linked = list(ids)


def adapter(tmp_path, backend, model):
    return MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "state.json"),
                              ingestion_version=FORMATION_ENTITY_VERSION, formation_model=model)


def fixture():
    seg = segment("小周负责工程甲。小周研究聚合物乙。聚合物乙厚度为250 nm。听过软件丙吗？")
    mentions = [mention(seg, name, handle) for name, handle in (
        ("小周", "person"), ("工程甲", "project"), ("聚合物乙", "material"),
        ("软件丙", "software"),
    )]
    units = [unit(seg, *args) for args in (
        ("小周负责工程甲。", "小周", "负责", "工程甲", "person", "project"),
        ("小周研究聚合物乙。", "小周", "研究", "聚合物乙", "person", "material"),
        ("聚合物乙厚度为250 nm。", "聚合物乙", "厚度为", "250 nm", "material"),
    )]
    return seg, {"mentions": mentions, "units": units}


def test_v2_roles_share_binding_and_orphan_has_public_occurrence(tmp_path):
    seg, output = fixture()
    before = asdict(seg)
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "completed", result
    assert model.calls == ["extract", "verify"]
    events = list(backend.events.values())
    assert len(events) == 3
    assert events[0]["metadata"]["subject_entity_ref"] == events[1]["metadata"]["subject_entity_ref"]
    assert events[1]["metadata"]["object_entity_ref"] == events[2]["metadata"]["subject_entity_ref"]
    assert events[2]["metadata"]["object_entity_ref"] is None
    assert events[2]["metadata"]["value"] == "250 nm"
    context = memory.recall_mentions("软件丙")
    assert context.safe_error_code is None
    assert len(context.mentions) == 1
    assert context.mentions[0].surface == "软件丙"
    assert context.mentions[0].provenance.source_role == "user"
    assert "entity_ref" not in json.dumps(asdict(context))
    assert asdict(seg) == before
    restarted = adapter(tmp_path, backend, Model())
    assert restarted.ingest(seg).already_ingested
    assert restarted.recall_mentions("软件丙") == context


@pytest.mark.parametrize("failure", [1, 2, 3, 4, 5])
def test_each_persistence_failure_reuses_both_model_stages_and_bindings(tmp_path, failure):
    seg, output = fixture()
    backend, model = Backend(failure), Model(output)
    memory = adapter(tmp_path, backend, model)
    first = memory.ingest(seg)
    assert first.status == "failed"
    state = memory.state_store.get(memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION))
    assert {"extracted", "verified", "mentions"} <= state.keys()
    frozen = copy.deepcopy(state["mentions"])
    next_model = Model()
    second = adapter(tmp_path, backend, next_model).ingest(seg)
    assert second.status == "completed", second
    assert next_model.calls == []
    assert model.calls == ["extract", "verify"]
    assert len(backend.events) == 3
    assert list(backend.mentions.values()) == frozen
    assert set(backend.linked) == set(backend.events)


def test_verifier_failure_preserves_extraction_for_retry(tmp_path):
    seg, output = fixture()
    backend, model = Backend(), Model(output, fail_verify=True)
    memory = adapter(tmp_path, backend, model)
    first = memory.ingest(seg)
    assert first.safe_error_code == "formation_verification_failed"
    assert not backend.events and not backend.mentions
    next_model = Model()
    second = adapter(tmp_path, backend, next_model).ingest(seg)
    assert second.status == "completed", second
    assert next_model.calls == ["verify"]


def test_invalid_citation_preserves_independent_facts_and_failed_checkpoint(tmp_path):
    seg = segment("Plate R is 180 nm thick. Plate R has mass 14 mg. Tool S is available.")
    output = {"mentions": [mention(seg, "Plate R", "plate"), mention(seg, "Tool S", "tool")],
              "units": [
                  unit(seg, "Plate R is 180 nm thick.", "Plate R", "thickness", "180 nm", "plate"),
                  unit(seg, "Plate R has mass 14 mg.", "Plate R", "mass", "14 mg", "plate"),
                  unit(seg, "Plate R melts at 600 K.", "Plate R", "melting point", "600 K", "plate"),
              ]}
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "failed" and result.safe_error_code == "formation_processing_incomplete"
    assert len(backend.events) == 2 and len(result.memory_ids) == 2
    assert model.calls == ["extract", "verify"]
    assert len(memory.recall_mentions("Tool S").mentions) == 1
    state = memory.state_store.get(memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION))
    assert state["status"] != "completed"
    assert any(issue["candidate"] == "unit" and issue["index"] == 2
               and issue["status"] == "pending" for issue in state["verified"]["issues"])
    frozen = copy.deepcopy((backend.events, backend.mentions))
    restarted_model = Model()
    restarted = adapter(tmp_path, backend, restarted_model)
    repeated = restarted.ingest(seg)
    assert repeated.status == "failed" and repeated.memory_ids == result.memory_ids
    assert restarted_model.calls == []
    assert (backend.events, backend.mentions) == frozen
    after = restarted.state_store.get(restarted.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION))
    assert all(after[key] == state[key] for key in ("extracted", "verified", "mentions", "memory_ids"))
    # Even the complete source turn lacks 600 K, so no citation-only repair
    # can help this candidate. Do not spend a repair call or authorize it.
    assert "repair" not in after
    assert restarted.ingest(seg) == repeated
    assert restarted_model.calls == []


def test_invalid_mention_isolates_only_its_dependent_unit(tmp_path):
    seg = segment("Sample V weighs 8 mg. Sample W weighs 9 mg.")
    invalid = mention(seg, "Sample W", "bad")
    invalid["occurrence"] = 4
    output = {"mentions": [mention(seg, "Sample V", "valid"), invalid], "units": [
        unit(seg, "Sample V weighs 8 mg.", "Sample V", "mass", "8 mg", "valid"),
        unit(seg, "Sample W weighs 9 mg.", "Sample W", "mass", "9 mg", "bad"),
    ]}
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "failed" and result.safe_error_code == "formation_processing_incomplete"
    assert [event["metadata"]["value"] for event in backend.events.values()] == ["8 mg"]
    assert len(memory.recall_mentions("Sample V").mentions) == 1
    assert memory.recall_mentions("Sample W").mentions == ()
    assert model.calls == ["extract", "verify"]


def test_unparseable_response_is_checkpointed_without_implicit_resampling(tmp_path):
    seg = segment("Module N is mentioned.")
    class BrokenJson:
        calls = 0
        def generate(self, recent_context, user_message, *, system_prompt):
            self.calls += 1
            return '{"mentions": ['
    model, backend = BrokenJson(), Backend()
    memory = adapter(tmp_path, backend, model)
    assert memory.ingest(seg).status == "failed"
    state = memory.state_store.get(memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION))
    assert state and state["extracted"]["response"] == '{"mentions": ['
    assert adapter(tmp_path, backend, model).ingest(seg).status == "failed"
    assert model.calls == 1
    assert not backend.events and not backend.mentions


@pytest.mark.parametrize("failure", [1, 2, 3, 4, 5])
def test_partial_window_recovers_each_graph_persistence_boundary(tmp_path, failure):
    seg, output = fixture()
    broken = copy.deepcopy(output["units"][0])
    broken["source_refs"][0]["supporting_span"] = "absent source citation"
    output["units"].append(broken)
    backend, model = Backend(failure), Model(output)
    first = adapter(tmp_path, backend, model).ingest(seg)
    assert first.status == "failed" and first.safe_error_code == "memory_write_failed"
    restart_model = Model()
    memory = adapter(tmp_path, backend, restart_model)
    second = memory.ingest(seg)
    assert second.status == "failed" and second.safe_error_code == "formation_processing_incomplete"
    assert len(backend.events) == 3 and len(backend.mentions) == 4
    assert restart_model.calls == [] and model.calls == ["extract", "verify"]
    persisted_calls = backend.persist_calls
    assert memory.ingest(seg).memory_ids == second.memory_ids
    assert backend.persist_calls == persisted_calls


def test_partial_window_verification_retry_keeps_original_extraction(tmp_path):
    seg, output = fixture()
    bad = copy.deepcopy(output["units"][0])
    bad["source_refs"][0]["supporting_span"] = "absent source citation"
    output["units"].append(bad)
    backend, first_model = Backend(), Model(output, fail_verify=True)
    first = adapter(tmp_path, backend, first_model).ingest(seg)
    assert first.safe_error_code == "formation_verification_failed"
    assert not backend.events
    next_model = Model()
    second = adapter(tmp_path, backend, next_model).ingest(seg)
    assert second.safe_error_code == "formation_processing_incomplete"
    assert next_model.calls == ["verify"] and len(backend.events) == 3


@pytest.mark.parametrize("checkpoint_number", [2, 3, 4, 5])
def test_restart_after_durable_stage_boundary_does_not_repeat_success(tmp_path, checkpoint_number):
    seg, output = fixture()
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    original_put = memory.state_store.put
    calls = 0
    def crash_after_put(key, value):
        nonlocal calls
        calls += 1
        original_put(key, value)
        if calls == checkpoint_number:
            raise OSError("synthetic interruption after durable replacement")
    memory.state_store.put = crash_after_put
    assert memory.ingest(seg).status == "failed"
    next_model = Model()
    repeated = adapter(tmp_path, backend, next_model).ingest(seg)
    assert repeated.status == "completed"
    assert next_model.calls == (["verify"] if checkpoint_number == 2 else [])
    assert len(backend.events) == 3 and len(backend.mentions) == 4


@pytest.mark.parametrize("completed", [True, False])
def test_legacy_v2_checkpoint_preserves_ids_without_reextracting(tmp_path, completed):
    seg, output = fixture()
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    original = memory.ingest(seg)
    assert original.status == "completed"
    key = memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION)
    state = memory.state_store.get(key)
    # Exact old-v2 schema: parsed output, four-field verified batch, no issues.
    state["extracted"] = {"schema_version": FORMATION_ENTITY_VERSION,
                          "source_digest": state["extracted"]["source_digest"],
                          "output": copy.deepcopy(output)}
    state["verified"]["schema_version"] = FORMATION_ENTITY_VERSION
    state["verified"].pop("issues", None)
    if not completed:
        state["status"] = "pending"
        state["memory_ids"] = []
        del state["verified"], state["mentions"]
        backend = Backend()
    memory.state_store.put(key, state)
    before = memory.state_store.path.read_bytes()
    next_model = Model()
    repeated = adapter(tmp_path, backend, next_model).ingest(seg)
    assert repeated.status == "completed" and repeated.memory_ids == original.memory_ids
    assert next_model.calls == ([] if completed else ["verify"])
    if completed:
        assert repeated.already_ingested
        assert memory.state_store.path.read_bytes() == before


def test_partial_window_never_consumes_cold_after_retry(tmp_path):
    from core.cold_draft_store import ColdDraftStore
    from Dream.cold_draft_digest import ColdDraftDigestionTask
    from Dream.models import DreamRunPolicy
    from Dream.runner import DreamRunner
    seg, output = fixture()
    bad = copy.deepcopy(output["units"][0])
    bad["source_refs"][0]["supporting_span"] = "absent source citation"
    output["units"].append(bad)
    owner = ColdDraftStore(tmp_path / "cold.jsonl")
    owner.append_segment([{
        "turn_id": turn.turn_id, "role": turn.role, "text": turn.content,
        "created_at": turn.timestamp.isoformat(), "source_timezone": turn.source_timezone,
        "timezone_source": turn.timezone_source,
    } for turn in seg.turns], segment_id=seg.segment_id)
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    class Provider:
        def get(self, version):
            return memory
    runner = DreamRunner(owner, ColdDraftDigestionTask(owner, Provider()))
    policy = DreamRunPolicy(ingestion_version=FORMATION_ENTITY_VERSION)
    original = owner.list_pending()[0]
    for _ in range(2):
        report = runner.run_once(policy)
        assert report.failed == 1 and report.consumed == 0
        assert owner.list_pending()[0] == original
    assert len(backend.events) == 3 and len(backend.mentions) == 4
    assert model.calls == ["extract", "verify", "repair"]


def test_zero_fact_partial_window_retains_valid_occurrence_after_restart(tmp_path):
    seg = segment("Have you heard of Library Y?")
    output = {"mentions": [mention(seg, "Library Y", "library")], "units": [
        unit(seg, "Library Y costs 99 dollars.", "Library Y", "cost", "99 dollars", "library"),
    ]}
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    first = memory.ingest(seg)
    assert first.status == "failed" and first.memory_ids == ()
    assert len(memory.recall_mentions("Library Y").mentions) == 1
    restarted_model = Model()
    repeated = adapter(tmp_path, backend, restarted_model).ingest(seg)
    assert repeated.status == "failed" and repeated.memory_ids == ()
    assert restarted_model.calls == [] and not backend.events


@pytest.mark.parametrize("response,error", [
    ('{"mentions": [], "units": "not an array"}', "formation_output_invalid"),
    ('{"error": "formation_output_too_large"}', "formation_output_too_large"),
    ("x" * 160001, "formation_output_too_large"),
], ids=["invalid_schema", "declared_overflow", "oversize_response"])
def test_bad_top_level_or_oversize_response_is_bounded_and_not_resampled(tmp_path, response, error):
    seg = segment("Module N is mentioned.")
    class RawModel:
        calls = 0
        def generate(self, *args, **kwargs):
            self.calls += 1
            return response
    model, backend = RawModel(), Backend()
    memory = adapter(tmp_path, backend, model)
    assert memory.ingest(seg).safe_error_code == error
    state = memory.state_store.get(memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION))
    assert len(state["extracted"]["response"]) <= 160000
    assert adapter(tmp_path, backend, model).ingest(seg).safe_error_code == error
    assert model.calls == 1 and not backend.events


def test_partial_checkpoint_cannot_lose_its_processing_issues(tmp_path):
    seg, output = fixture()
    bad = copy.deepcopy(output["units"][0])
    bad["source_refs"][0]["supporting_span"] = "absent source citation"
    output["units"].append(bad)
    backend = Backend()
    memory = adapter(tmp_path, backend, Model(output))
    assert memory.ingest(seg).safe_error_code == "formation_processing_incomplete"
    key = memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION)
    state = memory.state_store.get(key)
    del state["verified"]["issues"]
    memory.state_store.put(key, state)
    retry_model = Model()
    assert adapter(tmp_path, backend, retry_model).ingest(seg).safe_error_code == "formation_checkpoint_invalid"
    assert retry_model.calls == []
    assert memory.state_store.get(key)["status"] == "partial"


def repair_fixture():
    seg, output = fixture()
    refs = copy.deepcopy(output["units"][2]["source_refs"])
    output["units"][2]["source_refs"][0]["supporting_span"] = "absent citation"
    return seg, output, {"repairs": [{"index": 2, "source_refs": refs}]}


def test_local_citation_repair_appends_only_new_verified_fact_and_preserves_history(tmp_path):
    seg, output, repairs = repair_fixture()
    backend, initial_model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, initial_model)
    partial = memory.ingest(seg)
    assert partial.safe_error_code == "formation_processing_incomplete"
    key = memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION)
    initial = memory.state_store.get(key)
    old_events = copy.deepcopy(backend.events)
    assert len(old_events) == 2
    next_model = Model(repairs=repairs)
    result = adapter(tmp_path, backend, next_model).ingest(seg)
    assert result.status == "completed", result
    assert initial_model.calls == ["extract", "verify"]
    assert next_model.calls == ["repair", "verify"]
    assert len(next_model.verifications[0]["units"]) == 1
    assert next_model.verifications[0]["units"][0]["candidate"]["text"] == output["units"][2]["text"]
    assert result.memory_ids[:2] == partial.memory_ids
    assert all(backend.events[mid] == event for mid, event in old_events.items())
    state = memory.state_store.get(key)
    assert all(state[name] == initial[name] for name in ("extracted", "verified", "mentions"))
    assert state["repair_verified"]["issues"][0]["status"] == "repaired"
    repeat_model = Model()
    assert adapter(tmp_path, backend, repeat_model).ingest(seg).already_ingested
    assert repeat_model.calls == [] and len(backend.events) == 3


def test_local_repair_verifier_failure_reuses_repair_response_on_restart(tmp_path):
    seg, output, repairs = repair_fixture()
    backend = Backend()
    assert adapter(tmp_path, backend, Model(output)).ingest(seg).status == "failed"
    repair_model = Model(repairs=repairs, fail_verify=True)
    failed = adapter(tmp_path, backend, repair_model).ingest(seg)
    assert failed.safe_error_code == "formation_verification_failed", failed
    assert repair_model.calls == ["repair", "verify"] and len(backend.events) == 2
    verify_model = Model()
    assert adapter(tmp_path, backend, verify_model).ingest(seg).status == "completed"
    assert verify_model.calls == ["verify"] and len(backend.events) == 3


@pytest.mark.parametrize("stage", ["repair", "repair_verified", "event"])
def test_local_repair_durable_checkpoint_interruptions_resume_once(tmp_path, stage):
    seg, output, repairs = repair_fixture()
    backend = Backend()
    partial = adapter(tmp_path, backend, Model(output)).ingest(seg)
    repair_model = Model(repairs=repairs)
    memory = adapter(tmp_path, backend, repair_model)
    original_put = memory.state_store.put
    def crash_after_put(key, state):
        original_put(key, state)
        if (stage == "repair" and "repair" in state
                or stage == "repair_verified" and "repair_verified" in state
                or stage == "event" and len(state["memory_ids"]) == 3):
            raise OSError("synthetic interruption after durable repair checkpoint")
    memory.state_store.put = crash_after_put
    assert memory.ingest(seg).status == "failed"
    restarted_model = Model()
    restored = adapter(tmp_path, backend, restarted_model).ingest(seg)
    assert restored.status == "completed", restored
    assert restored.memory_ids[:2] == partial.memory_ids
    assert repair_model.calls.count("repair") == 1
    assert restarted_model.calls == (["verify"] if stage == "repair" else [])
    assert len(backend.events) == 3 and len(backend.mentions) == 4


@pytest.mark.parametrize("repair_kind", ["missing", "extra", "changed_text", "unknown_turn", "unlocated"])
def test_local_repair_cannot_rewrite_or_resample_a_failed_candidate(tmp_path, repair_kind):
    seg, output, repairs = repair_fixture()
    if repair_kind == "missing":
        repairs["repairs"] = []
    elif repair_kind == "extra":
        repairs["repairs"].append({"index": 0, "source_refs": output["units"][0]["source_refs"]})
    elif repair_kind == "changed_text":
        repairs["repairs"][0]["text"] = "invented new proposition"
    elif repair_kind == "unknown_turn":
        repairs["repairs"][0]["source_refs"][0]["turn_id"] = "missing"
    else:
        repairs["repairs"][0]["source_refs"] = []
    backend = Backend()
    partial = adapter(tmp_path, backend, Model(output)).ingest(seg)
    model = Model(repairs=repairs)
    memory = adapter(tmp_path, backend, model)
    first = memory.ingest(seg)
    assert first.status == "failed" and first.memory_ids == partial.memory_ids
    assert memory.ingest(seg).status == "failed"
    assert model.calls == ["repair"] and len(backend.events) == 2


def test_local_repair_semantic_rejection_never_creates_fact(tmp_path):
    seg, output, repairs = repair_fixture()
    backend = Backend()
    adapter(tmp_path, backend, Model(output)).ingest(seg)
    class RejectRepair(Model):
        def generate(self, recent_context, user_message, *, system_prompt):
            result = super().generate(recent_context, user_message, system_prompt=system_prompt)
            if system_prompt.startswith("Verify ONLY"):
                decisions = json.loads(result)
                decisions["units"][0]["supported"] = False
                return json.dumps(decisions)
            return result
    model = RejectRepair(repairs=repairs)
    memory = adapter(tmp_path, backend, model)
    assert memory.ingest(seg).status == "completed"
    assert len(backend.events) == 2 and model.calls == ["repair", "verify"]
    state = memory.state_store.get(memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION))
    assert any(issue["status"] == "rejected" for issue in state["repair_verified"]["issues"])
    assert memory.ingest(seg).already_ingested and model.calls == ["repair", "verify"]


@pytest.mark.parametrize("failure", [1, 2, 3])
def test_repaired_batch_graph_failure_keeps_old_ids_and_never_repeats_models(tmp_path, failure):
    seg, output, repairs = repair_fixture()
    backend = Backend()
    partial = adapter(tmp_path, backend, Model(output)).ingest(seg)
    backend.fail_persist = backend.persist_calls + failure
    repair_model = Model(repairs=repairs)
    result = adapter(tmp_path, backend, repair_model).ingest(seg)
    assert result.safe_error_code == "memory_write_failed", result
    assert repair_model.calls == ["repair", "verify"]
    restarted_model = Model()
    result = adapter(tmp_path, backend, restarted_model).ingest(seg)
    assert result.status == "completed" and result.memory_ids[:2] == partial.memory_ids
    assert restarted_model.calls == [] and len(backend.events) == 3


def test_cold_consumes_only_after_explicit_retry_finishes_local_repair(tmp_path):
    from core.cold_draft_store import ColdDraftStore
    from Dream.cold_draft_digest import ColdDraftDigestionTask
    from Dream.models import DreamRunPolicy
    from Dream.runner import DreamRunner
    seg, output, repairs = repair_fixture()
    owner = ColdDraftStore(tmp_path / "cold.jsonl")
    owner.append_segment([{
        "turn_id": turn.turn_id, "role": turn.role, "text": turn.content,
        "created_at": turn.timestamp.isoformat(), "source_timezone": turn.source_timezone,
        "timezone_source": turn.timezone_source,
    } for turn in seg.turns], segment_id=seg.segment_id)
    original = owner.list_pending()[0]
    original_turns = owner.list_all_turns()
    backend, model = Backend(), Model(output, repairs=repairs)
    memory = adapter(tmp_path, backend, model)
    class Provider:
        def get(self, version):
            return memory
    runner = DreamRunner(owner, ColdDraftDigestionTask(owner, Provider()))
    policy = DreamRunPolicy(ingestion_version=FORMATION_ENTITY_VERSION)
    first = runner.run_once(policy)
    assert first.failed == 1 and first.consumed == 0
    assert owner.list_pending()[0] == original and len(backend.events) == 2
    second = runner.run_once(policy)
    assert second.failed == 0 and second.consumed == 1
    assert not owner.list_pending() and len(backend.events) == 3
    assert model.calls == ["extract", "verify", "repair", "verify"]
    assert owner.list_all_turns() == original_turns


def test_zero_fact_window_is_durable_and_failure_does_not_complete(tmp_path):
    seg = segment("听过工具丁吗？")
    output = {"mentions": [mention(seg, "工具丁", "tool")], "units": []}
    backend = Backend(1)
    memory = adapter(tmp_path, backend, Model(output))
    assert memory.ingest(seg).status == "failed"
    assert adapter(tmp_path, backend, Model()).ingest(seg).status == "completed"
    assert not backend.events
    assert len(memory.recall_mentions("工具丁").mentions) == 1


def test_name_lookup_beyond_twenty_and_explicit_new_identity(tmp_path):
    seg = segment("鲁青负责站点戊。")
    backend = Backend()
    backend.candidates = [EntityCandidate(f"E_{i:03}", f"other{i}") for i in range(25)]
    backend.candidates.append(EntityCandidate("E_EXISTING", "鲁青"))
    output = {"mentions": [mention(seg, "鲁青", "person")], "units": []}
    assert adapter(tmp_path, backend, Model(output)).ingest(seg).status == "completed"
    assert next(iter(backend.mentions.values()))["entity_ref"] == "E_EXISTING"
    another = segment("另一个同名的鲁青来了。", "s2")
    output2 = {"mentions": [mention(another, "鲁青", "new", identity="new",
        identity_source_refs=[{"turn_id": another.turns[0].turn_id,
                               "supporting_span": another.turns[0].content}])], "units": []}
    assert adapter(tmp_path, backend, Model(output2)).ingest(another).status == "completed"
    assert len({r["entity_ref"] for r in backend.mentions.values()}) == 2
    ambiguous = segment("鲁青最近如何？", "s3")
    output3 = {"mentions": [mention(ambiguous, "鲁青", "amb")], "units": []}
    memory = adapter(tmp_path, backend, Model(output3))
    assert memory.ingest(ambiguous).status == "completed"
    records = list(backend.mentions.values())
    assert records[-1]["entity_ref"] is None
    assert len(records[-1]["candidate_entity_refs"]) == 2
    context = memory.recall_mentions("鲁青", limit=2)
    assert len(context.mentions) == 2 and context.truncated


def test_changed_cold_source_fails_without_reusing_or_reextracting(tmp_path):
    seg, output = fixture()
    backend = Backend()
    memory = adapter(tmp_path, backend, Model(output))
    assert memory.ingest(seg).status == "completed"
    changed = replace(seg, turns=(replace(seg.turns[0], content=seg.turns[0].content + "更改"),))
    result = adapter(tmp_path, backend, Model()).ingest(changed)
    assert result.safe_error_code == "state_corrupt"
    assert len(backend.events) == 3


@pytest.mark.parametrize("missing", ["extracted", "verified", "mentions"])
def test_completed_checkpoint_cannot_regenerate_a_missing_stage(tmp_path, missing):
    seg, output = fixture()
    backend = Backend()
    memory = adapter(tmp_path, backend, Model(output))
    assert memory.ingest(seg).status == "completed"
    key = memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION)
    state = memory.state_store.get(key)
    del state[missing]
    memory.state_store.put(key, state)
    model = Model()
    result = adapter(tmp_path, backend, model).ingest(seg)
    assert result.safe_error_code == "state_corrupt"
    assert not model.calls


def test_unresolved_distinct_mention_does_not_allocate_identity(tmp_path):
    seg = segment("李青来了。另一个人也来了。")
    output = {"units": [], "mentions": [
        mention(seg, "李青", "known"),
        mention(seg, "另一个人", "unknown", identity="unresolved", distinct_from=["known"],
                identity_source_refs=[{"turn_id": seg.turns[0].turn_id,
                                       "supporting_span": "另一个人也来了。"}]),
    ]}
    backend = Backend()
    memory = adapter(tmp_path, backend, Model(output))
    assert memory.ingest(seg).status == "completed"
    unknown = next(r for r in backend.mentions.values() if r["surface"] == "另一个人")
    assert unknown["entity_ref"] is None


def test_current_user_checkpoint_binding_cannot_change_identity(tmp_path):
    seg = segment("我叫宁竹。")
    output = {"units": [], "mentions": [mention(
        seg, "宁竹", "name", identity="current_user",
        identity_source_refs=[{"turn_id": seg.turns[0].turn_id,
                               "supporting_span": seg.turns[0].content}],
    )]}
    backend = Backend()
    memory = adapter(tmp_path, backend, Model(output))
    assert memory.ingest(seg).status == "completed"
    key = memory.state_store.key(seg.segment_id, FORMATION_ENTITY_VERSION)
    state = memory.state_store.get(key)
    state["mentions"][0]["entity_ref"] = "E_WRONG"
    memory.state_store.put(key, state)
    result = adapter(tmp_path, backend, Model()).ingest(seg)
    assert result.status == "failed"
    assert result.safe_error_code == "entity_consolidation_failed"


@pytest.mark.parametrize("existing_namesake", [False, True])
def test_new_identity_same_as_reuses_creation_ref_after_public_ingest_restart(tmp_path, existing_namesake):
    seg = segment("Another Alex likes tea. That Alex studies ceramics.")
    identity_refs = [{"turn_id": seg.turns[0].turn_id,
                      "supporting_span": seg.turns[0].content}]
    output = {"mentions": [
        mention(seg, "Alex", "first", identity="new",
                identity_source_refs=identity_refs),
        mention(seg, "Alex", "second", start=seg.turns[0].content.rindex("Alex"),
                identity="new", same_as="first", identity_source_refs=identity_refs),
    ], "units": [
        unit(seg, "Another Alex likes tea.", "Alex", "likes", "tea", "first"),
        unit(seg, "That Alex studies ceramics.", "Alex", "studies", "ceramics", "second"),
    ]}
    backend, model = Backend(), Model(output)
    if existing_namesake:
        old = segment("Alex develops software.", sid="old-alex")
        old_output = {"mentions": [mention(old, "Alex", "old")], "units": [
            unit(old, old.turns[0].content, "Alex", "develops", "software", "old"),
        ]}
        assert adapter(tmp_path, backend, Model(old_output)).ingest(old).status == "completed"
    old_events, old_mentions = copy.deepcopy((backend.events, backend.mentions))
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "completed", result
    assert model.calls == ["extract", "verify"]
    assert len(backend.mentions) == 2 + len(old_mentions) and len(backend.events) == 2 + len(old_events)
    refs = {record["entity_ref"] for mid, record in backend.mentions.items() if mid not in old_mentions}
    assert len(refs) == 1 and None not in refs
    assert {event["metadata"]["subject_entity_ref"]
            for mid, event in backend.events.items() if mid not in old_events} == refs
    assert all(backend.events[mid] == event for mid, event in old_events.items())
    assert all(backend.mentions[mid] == item for mid, item in old_mentions.items())
    assert not refs.intersection(item["entity_ref"] for item in old_mentions.values())
    before = memory.recall_mentions("Alex")
    assert len(before.mentions) == 2 + len(old_mentions)
    assert all(mention.resolved for mention in before.mentions)

    restored_backend = Backend()
    restored_backend.events = copy.deepcopy(backend.events)
    restored_backend.mentions = copy.deepcopy(backend.mentions)
    restored_model = Model()
    restarted = adapter(tmp_path, restored_backend, restored_model)
    repeated = restarted.ingest(seg)
    assert repeated.status == "completed" and repeated.already_ingested
    assert repeated.memory_ids == result.memory_ids
    assert restored_model.calls == []
    assert restarted.recall_mentions("Alex") == before
    assert restored_backend.events == backend.events
    assert restored_backend.mentions == backend.mentions
    if existing_namesake:
        ambiguous = segment("Alex is mentioned again.", sid="ambiguous-alex")
        ambiguous_model = Model({"units": [], "mentions": [mention(ambiguous, "Alex", "ambiguous")]})
        ambiguous_memory = adapter(tmp_path, restored_backend, ambiguous_model)
        assert ambiguous_memory.ingest(ambiguous).status == "completed"
        record = next(item for item in restored_backend.mentions.values()
                      if item["provenance"]["segment_id"] == ambiguous.segment_id)
        assert record["entity_ref"] is None
        assert set(record["candidate_entity_refs"]) == refs | {
            item["entity_ref"] for item in old_mentions.values()
        }
        assert restored_backend.events == backend.events


def test_unverified_same_as_remains_unresolved_with_existing_namesake(tmp_path):
    seg = segment("Another Alex likes tea. That Alex studies ceramics.")
    identity_refs = [{"turn_id": seg.turns[0].turn_id, "supporting_span": seg.turns[0].content}]
    output = {"units": [], "mentions": [
        mention(seg, "Alex", "first", identity="new", identity_source_refs=identity_refs),
        mention(seg, "Alex", "second", start=seg.turns[0].content.rindex("Alex"),
                same_as="first", identity_source_refs=identity_refs),
    ]}
    class UnverifiedIdentity(Model):
        def generate(self, recent_context, user_message, *, system_prompt):
            response = super().generate(recent_context, user_message, system_prompt=system_prompt)
            if system_prompt.startswith("Verify ONLY"):
                decisions = json.loads(response)
                decisions["mentions"][1]["identity_supported"] = False
                return json.dumps(decisions)
            return response
    backend = Backend()
    backend.candidates = [EntityCandidate("E_OLD", "Alex")]
    memory = adapter(tmp_path, backend, UnverifiedIdentity(output))
    assert memory.ingest(seg).status == "completed"
    first, second = backend.mentions.values()
    assert first["entity_ref"] not in {None, "E_OLD"}
    assert second["identity"] == "unresolved" and second["entity_ref"] is None
    assert set(second["candidate_entity_refs"]) == {"E_OLD", first["entity_ref"]}
    assert not backend.events


@pytest.mark.parametrize("only_mentions", [False, True])
def test_manual_dream_consumes_after_complete_v2_persistence(tmp_path, only_mentions):
    from core.cold_draft_store import ColdDraftStore
    from Dream.cold_draft_digest import ColdDraftDigestionTask
    from Dream.models import DreamRunPolicy
    from Dream.runner import DreamRunner

    seg, output = fixture()
    if only_mentions:
        output["units"] = []
    owner = ColdDraftStore(tmp_path / "cold.jsonl")
    record = owner.append_segment([{
        "turn_id": turn.turn_id, "role": turn.role, "text": turn.content,
        "created_at": turn.timestamp.isoformat(), "source_timezone": turn.source_timezone,
        "timezone_source": turn.timezone_source,
    } for turn in seg.turns], segment_id=seg.segment_id)
    backend, model = Backend(fail_persist=1), Model(output)
    memory = adapter(tmp_path, backend, model)

    class Provider:
        def get(self, version):
            assert version == FORMATION_ENTITY_VERSION
            return memory

    runner = DreamRunner(owner, ColdDraftDigestionTask(owner, Provider()))
    policy = DreamRunPolicy(ingestion_version=FORMATION_ENTITY_VERSION)
    before = owner.list_pending()[0]["turns"]
    first = runner.run_once(policy)
    assert first.failed == 1 and first.consumed == 0
    assert owner.list_pending()[0]["turns"] == before
    second = runner.run_once(policy)
    assert second.failed == 0 and second.consumed == 1
    assert model.calls == ["extract", "verify"]
    assert len(backend.mentions) == 4
    assert len(backend.events) == (0 if only_mentions else 3)
    assert runner.run_once(policy).attempted == 0
    # Re-append is an owner-mediated source-preservation read, not a rewrite.
    consumed = owner.append_segment(record["turns"], segment_id=seg.segment_id)
    assert consumed["state"] == "consumed" and consumed["turns"] == before
