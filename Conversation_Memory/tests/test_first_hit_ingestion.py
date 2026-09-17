from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from Conversation_Memory.adapter._first_hit_ingestion import FIRST_HIT_VERSION
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.grounded_formation import FORMATION_ENTITY_VERSION
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import BackendCandidate
from Conversation_Memory.ingestion.state_store import IngestionStateStore
from Conversation_Memory.tests.test_entity_ingestion_v2 import (
    Backend, Model, fixture, repair_fixture, segment, mention,
)


class DurableBackend(Backend):
    """Isolated graph/vector durability fixture, with real restart serialization."""
    def __init__(self, path, *, crash=None):
        super().__init__()
        self.path = path
        self.crash = crash
        self.links = {}
        self.vectors = set()
        self.semantic_flags = []
        self.entity_flags = []
        self.repaired = []
        self.before_mutation = lambda: None
        if path.exists():
            raw = json.loads(path.read_text())
            self.events, self.mentions = raw["events"], raw["mentions"]
            self.vectors = set(raw["vectors"])
            self.links = {tuple(link[:2]): link[2] for link in raw["links"]}
        else:
            for i in range(5):
                mid = "old-" + str(i)
                self.events[mid] = {"text": "Historical fact " + str(i),
                                    "metadata": {"evidence_id": mid}}
                self.vectors.add(mid)
            self._save()

    def _save(self):
        self.path.write_text(json.dumps({
            "events": self.events, "mentions": self.mentions,
            "vectors": sorted(self.vectors),
            "links": [[*pair, weight] for pair, weight in sorted(self.links.items())],
        }, default=str), encoding="utf-8")

    def upsert_entity_mentions(self, records):
        self.before_mutation()
        super().upsert_entity_mentions(records)

    def add_event(self, text, timestamp, metadata, *, automatic_semantic=True):
        self.before_mutation()
        self.semantic_flags.append(automatic_semantic)
        mid = super().add_event(text, timestamp, metadata)
        if self.crash == "graph_before_vector":
            self.crash = None
            self._save()
            raise OSError("synthetic graph-before-vector interruption")
        self.vectors.add(mid)
        return mid

    def ensure_event_persisted(self, mid):
        if mid in self.vectors:
            return False
        self.vectors.add(mid)
        self.repaired.append(mid)
        return True

    def create_relationships(self, ids, *, automatic_entity_links=True):
        self.entity_flags.append(automatic_entity_links)
        super().create_relationships(ids)

    def endpoint_similarities(self, text, memory_ids):
        return {mid: [0.1, 0.8, 0.9, 0.95, 0.0][int(mid.split("-")[1])]
                for mid in memory_ids}

    def apply_first_hit_links(self, plan):
        self.before_mutation()
        for link in plan:
            source, target = link["source_evidence_id"], link["target_evidence_id"]
            assert source in self.events and target in self.events
            for pair in ((source, target), (target, source)):
                self.links[pair] = link["weight"]
                if self.crash == "partial_edge":
                    self.crash = None
                    self._save()
                    raise OSError("synthetic partial edge interruption")

    def persist(self):
        super().persist()
        if self.crash == "links_persist" and self.links:
            self.crash = None
            raise OSError("synthetic links persistence interruption")
        self._save()


def configured(tmp_path, backend, model, *, policy=None, activation=None):
    memory = MagmaMemoryAdapter(
        backend, IngestionStateStore(tmp_path / "state.json"),
        ingestion_version=FORMATION_ENTITY_VERSION, formation_model=model,
        first_hit=policy or FirstHitPolicy(),
    )
    calls = []
    def activate(cue, *, target_entity_refs, exclude_evidence_ids):
        calls.append((cue, target_entity_refs, exclude_evidence_ids))
        if activation is not None:
            return activation(cue, target_entity_refs, exclude_evidence_ids)
        items = []
        for i, h in enumerate([0.9, 0.6, 0.4, 0.2, 0.7]):
            mid = "old-" + str(i)
            items.append((BackendCandidate("Historical fact " + str(i), None, None,
                                           {"evidence_id": mid}), h, 0.0))
        # Even an incorrectly returned current-batch candidate cannot become an edge.
        if exclude_evidence_ids:
            items.append((BackendCandidate("Current", None, None,
                                           {"evidence_id": exclude_evidence_ids[0]}), 1.0, 1.0))
        return SimpleNamespace(candidates=tuple(items), safe_error_code=None)
    memory._activate_first_hit = activate
    return memory, calls


def stage(memory, seg):
    return memory.state_store.get(memory.state_store.key(seg.segment_id, FIRST_HIT_VERSION))


def test_complete_batch_plan_precedes_graph_and_uses_first_hit_times_cosine(tmp_path):
    seg, output = fixture()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, calls = configured(tmp_path, backend, Model(output))
    def frozen_before_mutation():
        saved = stage(memory, seg)
        assert saved["status"] in {"planned", "completed"}
        assert len(saved["evidence_ids"]) == 3 and len(saved["link_plan"]) == 9
    backend.before_mutation = frozen_before_mutation
    result = memory.ingest(seg)
    assert result.status == "completed", result
    saved = stage(memory, seg)
    assert saved["status"] == "completed"
    assert all(link["target_evidence_id"] in {"old-1", "old-2", "old-3"}
               for link in saved["link_plan"])
    assert len(backend.links) == 18 and backend.semantic_flags == [False] * 3
    assert backend.entity_flags == [False]
    assert len(calls) == 3 and all(call[1] for call in calls)
    assert all(call[2] == tuple(saved["evidence_ids"]) for call in calls)
    assert set(result.memory_ids) <= backend.vectors
    before = memory.state_store.path.read_bytes()
    restarted = DurableBackend(backend.path)
    again, calls = configured(tmp_path, restarted, Model())
    assert again.ingest(seg).already_ingested and calls == []
    assert before == memory.state_store.path.read_bytes()
    assert restarted.links == backend.links


@pytest.mark.parametrize("crash", ["graph_before_vector", "partial_edge", "links_persist"])
def test_restart_reuses_plan_and_repairs_graph_vector_or_links(tmp_path, crash):
    seg, output = fixture()
    backend = DurableBackend(tmp_path / "graph.json", crash=crash)
    memory, _ = configured(tmp_path, backend, Model(output))
    failed = memory.ingest(seg)
    assert failed.status == "failed" and failed.retryable
    saved = stage(memory, seg)
    assert saved["status"] == "planned"
    restarted = DurableBackend(backend.path)
    next_model = Model()
    again, calls = configured(tmp_path, restarted, next_model)
    result = again.ingest(seg)
    assert result.status == "completed", result
    assert calls == [] and next_model.calls == []
    assert stage(again, seg)["link_plan"] == saved["link_plan"]
    assert len(restarted.events) == 8 and len(restarted.links) == 18
    assert set(result.memory_ids) <= restarted.vectors
    assert bool(restarted.repaired) == (crash == "graph_before_vector")


@pytest.mark.parametrize("checkpoint_status", ["pending", "planned", "completed", "formation_completed"])
@pytest.mark.parametrize("durable", [False, True])
def test_first_hit_state_write_failure_keeps_completion_responsibility(tmp_path, checkpoint_status, durable):
    seg, output = fixture()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, first_calls = configured(tmp_path, backend, Model(output))
    original_put = memory.state_store.put
    def crash(key, value):
        association_failure = (key.endswith(":" + FIRST_HIT_VERSION)
                               and value["status"] == checkpoint_status)
        formation_failure = (checkpoint_status == "formation_completed"
                             and key.endswith(":" + FORMATION_ENTITY_VERSION)
                             and value["status"] == "completed")
        if association_failure or formation_failure:
            if durable:
                original_put(key, value)
            raise OSError("synthetic checkpoint interruption")
        original_put(key, value)
    memory.state_store.put = crash
    failed = memory.ingest(seg)
    assert failed.status == "failed" and failed.retryable
    if checkpoint_status in {"pending", "planned"}:
        assert len(backend.events) == 5 and not backend.mentions
    next_model = Model(output if checkpoint_status == "pending" else None)
    again, calls = configured(tmp_path, DurableBackend(backend.path), next_model)
    result = again.ingest(seg)
    assert result.status == "completed", result
    assert stage(again, seg)["status"] == "completed"
    if checkpoint_status in {"completed", "formation_completed"} or checkpoint_status == "planned" and durable:
        assert calls == []
    elif checkpoint_status == "planned":
        assert len(first_calls) == len(calls) == 3


def test_failed_activation_retains_frozen_formation_and_never_mutates_graph(tmp_path):
    seg, output = fixture()
    backend = DurableBackend(tmp_path / "graph.json")
    model = Model(output)
    memory, _ = configured(tmp_path, backend, model, activation=lambda *_: SimpleNamespace(
        candidates=(), safe_error_code="first_hit_view_unavailable"))
    result = memory.ingest(seg)
    assert result.safe_error_code == "first_hit_activation_unavailable" and result.retryable
    assert not backend.mentions and len(backend.events) == 5
    assert stage(memory, seg)["status"] == "pending"
    again, calls = configured(tmp_path, backend, Model())
    assert again.ingest(seg).status == "completed" and len(calls) == 3


def test_repaired_batch_extends_plan_without_reselecting_initial_facts(tmp_path):
    seg, output, repairs = repair_fixture()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured(tmp_path, backend, Model(output))
    first = memory.ingest(seg)
    assert first.safe_error_code == "formation_processing_incomplete"
    saved = stage(memory, seg)
    assert saved["status"] == "completed" and len(saved["evidence_ids"]) == 2
    restarted = DurableBackend(backend.path)
    again, calls = configured(tmp_path, restarted, Model(repairs=repairs))
    result = again.ingest(seg)
    assert result.status == "completed", result
    extended = stage(again, seg)
    assert extended["evidence_ids"][:2] == saved["evidence_ids"]
    assert extended["link_plan"][:6] == saved["link_plan"]
    assert len(calls) == 1 and calls[0][2] == tuple(extended["evidence_ids"])
    assert len(extended["evidence_ids"]) == 3 and len(restarted.links) == 18


@pytest.mark.parametrize("legacy_state", ["completed", "partial", "pending"])
def test_enabling_first_hit_never_migrates_legacy_v2_state(tmp_path, legacy_state):
    if legacy_state == "partial":
        seg, output, repairs = repair_fixture()
    else:
        seg, output = fixture()
        repairs = None
    backend = DurableBackend(tmp_path / "graph.json")
    state_store = IngestionStateStore(tmp_path / "state.json")
    legacy = MagmaMemoryAdapter(backend, state_store, ingestion_version=FORMATION_ENTITY_VERSION,
                               formation_model=Model(output, fail_verify=legacy_state == "pending"))
    legacy.ingest(seg)
    assert not state_store.get(state_store.key(seg.segment_id, FIRST_HIT_VERSION))
    again, calls = configured(tmp_path, backend, Model(repairs=repairs))
    assert again.ingest(seg).status == "completed"
    assert calls == [] and stage(again, seg) is None and not backend.links
    assert backend.semantic_flags and all(backend.semantic_flags)
    assert all(backend.entity_flags)


@pytest.mark.parametrize("change", ["profile", "plan", "status", "configuration"])
def test_incompatible_or_corrupt_association_checkpoint_fails_without_graph_write(tmp_path, change):
    seg, output = fixture()
    backend = DurableBackend(tmp_path / "graph.json", crash="graph_before_vector")
    memory, _ = configured(tmp_path, backend, Model(output))
    assert memory.ingest(seg).status == "failed"
    saved = stage(memory, seg)
    if change in {"plan", "status"}:
        if change == "plan":
            saved["link_plan"][0]["weight"] = 0.5
        else:
            saved["status"] = []
        memory.state_store.put(memory.state_store.key(seg.segment_id, FIRST_HIT_VERSION), saved)
    again, calls = configured(tmp_path, backend, Model(), policy=(
        replace(FirstHitPolicy(), decay=0.5) if change == "profile" else None))
    if change == "configuration":
        again.first_hit = None
    before = backend.path.read_bytes()
    result = again.ingest(seg)
    assert result.status == "failed" and not result.retryable and calls == []
    assert result.safe_error_code == ("first_hit_configuration_required" if change == "configuration"
                                      else "first_hit_checkpoint_invalid")
    assert backend.path.read_bytes() == before


def test_zero_fact_window_completes_association_without_activation(tmp_path):
    seg = segment("Have you heard of Library Y?")
    output = {"mentions": [mention(seg, "Library Y", "library")], "units": []}
    backend = DurableBackend(tmp_path / "graph.json")
    memory, calls = configured(tmp_path, backend, Model(output))
    result = memory.ingest(seg)
    assert result.status == "completed" and result.memory_ids == ()
    assert calls == [] and len(backend.mentions) == 1 and not backend.links
    assert stage(memory, seg)["status"] == "completed"


def test_manual_dream_consumes_only_after_durable_association_recovery(tmp_path):
    from core.cold_draft_store import ColdDraftStore
    from Dream.cold_draft_digest import ColdDraftDigestionTask
    from Dream.models import DreamRunPolicy
    from Dream.runner import DreamRunner

    seg, output = fixture()
    owner = ColdDraftStore(tmp_path / "cold.jsonl")
    owner.append_segment([{
        "turn_id": turn.turn_id, "role": turn.role, "text": turn.content,
        "created_at": turn.timestamp.isoformat(), "source_timezone": turn.source_timezone,
        "timezone_source": turn.timezone_source,
    } for turn in seg.turns], segment_id=seg.segment_id)
    backend = DurableBackend(tmp_path / "graph.json", crash="partial_edge")
    memory, _ = configured(tmp_path, backend, Model(output))

    class Provider:
        def get(self, version):
            return memory

    runner = DreamRunner(owner, ColdDraftDigestionTask(owner, Provider()))
    policy = DreamRunPolicy(ingestion_version=FORMATION_ENTITY_VERSION)
    original = owner.list_pending()[0]
    failed = runner.run_once(policy)
    assert failed.failed == 1 and failed.consumed == 0
    assert owner.list_pending()[0] == original
    assert stage(memory, seg)["status"] == "planned"
    memory, calls = configured(tmp_path, DurableBackend(backend.path), Model())
    completed = runner.run_once(policy)
    assert completed.consumed == 1 and not owner.list_pending()
    assert calls == [] and stage(memory, seg)["status"] == "completed"
    assert len(memory.backend.links) == 18


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("association_status", ["pending", "planned"])
def test_terminal_formation_rejects_impossible_association_gap(tmp_path, partial, association_status):
    from Conversation_Memory.adapter._first_hit_ingestion import _plan_digest

    if partial:
        seg, output, _ = repair_fixture()
    else:
        seg, output = fixture()
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured(tmp_path, backend, Model(output))
    memory.ingest(seg)
    saved = stage(memory, seg)
    assert saved["status"] == "completed"
    saved["status"] = association_status
    if association_status == "pending":
        saved["evidence_ids"] = []
        saved["link_plan"] = []
        saved["plan_digest"] = _plan_digest([], [])
    memory.state_store.put(memory.state_store.key(seg.segment_id, FIRST_HIT_VERSION), saved)
    before = backend.path.read_bytes()
    again, calls = configured(tmp_path, backend, Model())
    result = again.ingest(seg)
    assert result.safe_error_code == "first_hit_checkpoint_invalid" and not result.retryable
    assert calls == [] and backend.path.read_bytes() == before
