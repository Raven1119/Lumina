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
    def __init__(self, output=None, *, fail_verify=False):
        self.output = output
        self.fail_verify = fail_verify
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        payload = json.loads(user_message)
        if system_prompt.startswith("Extract source-grounded"):
            self.calls.append("extract")
            assert self.output is not None, "completed extraction was repeated"
            return json.dumps(self.output, ensure_ascii=False)
        self.calls.append("verify")
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


def test_new_identity_same_as_reuses_creation_ref_after_public_ingest_restart(tmp_path):
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
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "completed", result
    assert model.calls == ["extract", "verify"]
    assert len(backend.mentions) == 2 and len(backend.events) == 2
    refs = {record["entity_ref"] for record in backend.mentions.values()}
    assert len(refs) == 1 and None not in refs
    assert {event["metadata"]["subject_entity_ref"]
            for event in backend.events.values()} == refs
    before = memory.recall_mentions("Alex")
    assert len(before.mentions) == 2
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
