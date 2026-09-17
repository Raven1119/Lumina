from __future__ import annotations

import ast
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import IngestionResult
from core.cold_draft_store import ColdDraftStore
from core.contracts import MemoryTurn
from core.main import create_app
from core.model_client import MockModelClient
from Dream.cold_draft_digest import (
    ColdDraftConversionError,
    ColdDraftDigestionTask,
    ColdDraftSegmentConverter,
)
from Dream.models import DreamRunPolicy
from Dream.runner import (
    DreamRunner,
    RealMemoryIngestorProvider,
    build_default_runner,
    main,
)
from ingestion.state_store import IngestionStateStore


ROOT = Path(__file__).resolve().parents[2]
LEGACY_FIXTURE = (
    ROOT / "Conversation_Memory" / "fixtures" / "cold_draft_segment_legacy.json"
)


def make_record(segment_id: str, *texts: str) -> dict:
    turns = []
    for index, text in enumerate(texts or ("private text",)):
        turns.append({"role": "user" if index % 2 == 0 else "assistant", "text": text})
    return {
        "segment_id": segment_id,
        "turns": turns,
        "created_at": "2026-07-14T10:00:00+08:00",
        "source": "hot_draft_precompression",
        "state": "pending_digest",
    }


class FakeOwner:
    def __init__(self, records=()):
        self.records = [dict(item) for item in records]
        self.fail_consume_once: set[str] = set()
        self.list_limits: list[int | None] = []
        self.consume_calls: list[str] = []
        self.raise_on_list = False
        self.cursor = None

    def list_pending(self, limit=None):
        if self.raise_on_list:
            raise OSError("C:\\private\\cold.jsonl")
        self.list_limits.append(limit)
        pending = [item for item in self.records if item.get("state") == "pending_digest"]
        return pending if limit is None else pending[:limit]

    def mark_consumed(self, segment_id):
        self.consume_calls.append(segment_id)
        if segment_id in self.fail_consume_once:
            self.fail_consume_once.remove(segment_id)
            return False
        for item in self.records:
            if item.get("segment_id") != segment_id:
                continue
            if item.get("state") == "consumed":
                return True
            if item.get("state") == "pending_digest":
                item["state"] = "consumed"
                return True
        return False

    def list_pending_page(self, limit):
        records = self.list_pending(limit=None)
        self.list_limits[-1] = limit
        ids = [record.get("segment_id") for record in self.records]
        start = ids.index(self.cursor) + 1 if self.cursor in ids else 0
        order = ids[start:] + ids[:start]
        return sorted(records, key=lambda item: order.index(item["segment_id"]))[:limit]

    def advance_pending_cursor(self, segment_id):
        self.cursor = segment_id
        return True


class FakeMemorySystem:
    def __init__(self):
        self.events: dict[tuple[str, str], tuple[str, ...]] = {}
        self.calls: list[tuple[str, str]] = []
        self.ingest_order: list[str] = []
        self.fail_codes: dict[str, str] = {}
        self.raise_for: set[str] = set()

    @property
    def event_count(self):
        return sum(len(value) for value in self.events.values())


class FakeIngestor:
    def __init__(self, system: FakeMemorySystem, version: str):
        self.system = system
        self.version = version

    def ingest(self, segment):
        key = (segment.segment_id, self.version)
        self.system.calls.append(key)
        self.system.ingest_order.append(segment.segment_id)
        if segment.segment_id in self.system.raise_for:
            raise RuntimeError("OPENAI_API_KEY=secret C:\\private\\traceback")
        code = self.system.fail_codes.get(segment.segment_id)
        if code:
            return IngestionResult(
                segment.segment_id,
                self.version,
                "failed",
                retryable=True,
                safe_error_code=code,
            )
        if key in self.system.events:
            return IngestionResult(
                segment.segment_id,
                self.version,
                "completed",
                self.system.events[key],
                already_ingested=True,
            )
        memory_ids = tuple(
            f"private-node-{segment.segment_id}-{index}"
            for index, _ in enumerate(segment.turns)
        )
        self.system.events[key] = memory_ids
        return IngestionResult(
            segment.segment_id,
            self.version,
            "completed",
            memory_ids,
        )


class FakeProvider:
    def __init__(self, system=None):
        self.system = system or FakeMemorySystem()
        self.instances = {}

    def get(self, ingestion_version):
        return self.instances.setdefault(
            ingestion_version,
            FakeIngestor(self.system, ingestion_version),
        )


class StaticProvider:
    def __init__(self, ingestor):
        self.ingestor = ingestor

    def get(self, ingestion_version):
        return self.ingestor


def make_runner(records=(), system=None):
    owner = FakeOwner(records)
    provider = FakeProvider(system)
    task = ColdDraftDigestionTask(owner, provider)
    return DreamRunner(owner, task), owner, provider.system


@pytest.mark.parametrize("value", [0, -1, True])
def test_policy_requires_positive_bound(value):
    with pytest.raises(ValueError, match="max_segments"):
        DreamRunPolicy(max_segments=value)


def test_no_pending_segments_returns_empty_success_report():
    runner, owner, _ = make_runner()
    report = runner.run_once(DreamRunPolicy(max_segments=3))
    assert report.attempted == report.failed == report.consumed == 0
    assert report.results == ()
    assert owner.list_limits == [3]


def test_default_policy_uses_grounded_production_version():
    assert DreamRunPolicy().ingestion_version == "grounded-span-v2"


def test_real_owner_reads_production_record_ingests_and_consumes(tmp_path):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    turns = [
        MemoryTurn(
            turn_id="production-user",
            role="user",
            text="hello",
            created_at=datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
            source_timezone="Asia/Shanghai",
            timezone_source="client",
        ).storage_turn(),
        MemoryTurn(
            turn_id="production-assistant",
            role="assistant",
            text="hi",
            created_at=datetime(2026, 7, 14, 2, 1, tzinfo=UTC),
            source_timezone="Asia/Shanghai",
            timezone_source="client",
        ).storage_turn(),
    ]
    record = owner.append_segment(
        turns,
        segment_id="production-segment",
    )
    before = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(before) == 2
    assert {item["record_type"] for item in before} == {"cold_turn"}
    assert {item["segment_id"] for item in before} == {record["segment_id"]}
    assert [item["segment_turn_index"] for item in before] == [0, 1]
    assert {item["segment_turn_count"] for item in before} == {2}
    assert [
        {
            key: item[key]
            for key in (
                "turn_id",
                "role",
                "text",
                "created_at",
                "source_timezone",
                "timezone_source",
            )
        }
        for item in before
    ] == turns
    provider = FakeProvider()
    report = DreamRunner(
        owner,
        ColdDraftDigestionTask(owner, provider),
    ).run_once(DreamRunPolicy(max_segments=1))
    assert report.attempted == report.ingested == report.consumed == 1
    assert report.results[0].segment_id == record["segment_id"]
    assert owner.list_pending() == []
    after = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(item["record_type"] == "dream_selection_cursor" for item in after) == 1
    after = [item for item in after if item["record_type"] == "cold_turn"]
    assert {item["state"] for item in after} == {"consumed"}
    assert len({item["consumed_at"] for item in after}) == 1


def test_multiple_segments_use_owner_order():
    runner, _, system = make_runner([make_record("b"), make_record("a"), make_record("c")])
    report = runner.run_once(DreamRunPolicy(max_segments=3))
    assert [item.segment_id for item in report.results] == ["b", "a", "c"]
    assert system.ingest_order == ["b", "a", "c"]


def test_max_segments_is_enforced_even_if_owner_over_returns():
    runner, owner, _ = make_runner([make_record("a"), make_record("b"), make_record("c")])
    owner.list_pending_page = lambda limit: owner.records
    report = runner.run_once(DreamRunPolicy(max_segments=2))
    assert [item.segment_id for item in report.results] == ["a", "b"]


def test_failure_does_not_block_later_segment_by_default():
    system = FakeMemorySystem()
    system.fail_codes["bad"] = "memory_write_failed"
    runner, _, _ = make_runner([make_record("bad"), make_record("good")], system)
    report = runner.run_once(DreamRunPolicy(max_segments=2))
    assert [item.status for item in report.results] == ["failed", "consumed"]
    assert report.failed == 1 and report.consumed == 1


def test_stop_on_error_stops_after_first_failure():
    system = FakeMemorySystem()
    system.fail_codes["bad"] = "memory_write_failed"
    runner, _, _ = make_runner([make_record("bad"), make_record("later")], system)
    report = runner.run_once(DreamRunPolicy(max_segments=2, stop_on_error=True))
    assert [item.segment_id for item in report.results] == ["bad"]


@pytest.mark.parametrize("code", ["memory_write_failed", "state_corrupt"])
def test_memory_failure_leaves_segment_pending(code):
    system = FakeMemorySystem()
    system.fail_codes["retry-me"] = code
    runner, owner, _ = make_runner([make_record("retry-me")], system)
    report = runner.run_once(DreamRunPolicy(max_segments=1))
    assert report.results[0].error_code == code
    assert owner.records[0]["state"] == "pending_digest"
    assert owner.consume_calls == []


def test_completed_memory_then_consume_failure_recovers_without_duplicate_events():
    runner, owner, system = make_runner([make_record("recover", "one", "two")])
    owner.fail_consume_once.add("recover")
    first = runner.run_once(DreamRunPolicy(max_segments=1, ingestion_version="v7"))
    assert first.results[0].error_code == "cold_draft_consume_failed"
    assert owner.records[0]["state"] == "pending_digest"
    assert system.event_count == 2
    second = runner.run_once(DreamRunPolicy(max_segments=1, ingestion_version="v7"))
    assert second.results[0].status == "consumed"
    assert second.results[0].already_ingested is True
    assert second.ingested == 0 and second.consumed == 1
    assert system.event_count == 2


def test_duplicate_run_after_consumed_does_not_call_memory_again():
    runner, _, system = make_runner([make_record("once")])
    assert runner.run_once(DreamRunPolicy(max_segments=1)).consumed == 1
    calls = list(system.calls)
    second = runner.run_once(DreamRunPolicy(max_segments=1))
    assert second.attempted == 0 and system.calls == calls


def test_already_consumed_record_is_skipped_without_ingest():
    record = make_record("done")
    record["state"] = "consumed"
    owner = FakeOwner([record])
    provider = FakeProvider()
    result = ColdDraftDigestionTask(owner, provider).digest(record, "v1")
    assert result.status == "skipped" and result.consumed
    assert provider.system.calls == []


def test_non_pending_record_is_skipped_without_ingest():
    record = make_record("not-pending")
    record["state"] = "archived"
    owner = FakeOwner([record])
    provider = FakeProvider()
    result = ColdDraftDigestionTask(owner, provider).digest(record, "v1")
    assert result.status == "skipped"
    assert result.error_code == "segment_not_pending"
    assert provider.system.calls == []


def test_legacy_nested_physical_record_is_not_rebuilt_or_consumed(tmp_path):
    path = tmp_path / "cold.jsonl"
    raw = {"segment_id": "malformed", "turns": [{"role": "user", "text": "kept"}], "state": "pending_digest"}
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    owner = ColdDraftStore(path)
    provider = FakeProvider()
    report = DreamRunner(owner, ColdDraftDigestionTask(owner, provider)).run_once(DreamRunPolicy(max_segments=1))
    assert owner.list_pending() == []
    assert report.attempted == report.consumed == 0
    assert report.results == ()
    assert provider.system.calls == []
    assert owner.mark_consumed("malformed") is False
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "pending_digest"


def test_converter_preserves_turn_order_role_and_complete_text():
    record = make_record("ordered", "第一段完整原文", "second complete text")
    segment = ColdDraftSegmentConverter().convert(record, "v1")
    assert [(turn.role, turn.content) for turn in segment.turns] == [
        ("user", "第一段完整原文"),
        ("assistant", "second complete text"),
    ]


def test_converter_uses_source_aware_timestamp_timezone_and_stable_fallback_ids():
    record = make_record("time-source", "today")
    first = ColdDraftSegmentConverter().convert(record, "v1")
    second = ColdDraftSegmentConverter().convert(record, "v1")
    assert first.conversation_id == "cold-draft:time-source"
    assert first.turns[0].turn_id == "time-source:turn:0000"
    assert first.turns[0].turn_id == second.turns[0].turn_id
    assert first.turns[0].timestamp == datetime.fromisoformat("2026-07-14T10:00:00+08:00")
    assert first.source_timezone == "+08:00"
    assert first.turns[0].source_timezone == "+08:00"
    assert first.turns[0].timezone_source == "legacy_segment_fallback"


def test_committed_legacy_fixture_uses_truthful_segment_fallback():
    record = json.loads(LEGACY_FIXTURE.read_text(encoding="utf-8"))
    segment = ColdDraftSegmentConverter().convert(record, "v1")
    assert segment.schema_version == "1"
    assert [turn.turn_id for turn in segment.turns] == [
        "fixture-legacy-segment-001:turn:0000",
        "fixture-legacy-segment-001:turn:0001",
    ]
    assert all(
        turn.timestamp == datetime.fromisoformat(record["created_at"])
        for turn in segment.turns
    )
    assert all(
        turn.timezone_source == "legacy_segment_fallback"
        for turn in segment.turns
    )


def test_converter_prioritizes_complete_native_v2_turn_provenance():
    record = make_record("explicit", "text")
    record["schema_version"] = 2
    record["conversation_id"] = "conversation-7"
    record["turns"][0].update({
        "turn_id": "turn-9",
        "created_at": "2026-07-13T01:00:00Z",
        "source_timezone": "Asia/Shanghai",
        "timezone_source": "client",
    })
    segment = ColdDraftSegmentConverter().convert(record, "v1")
    assert segment.conversation_id == "conversation-7"
    assert segment.source_timezone == "Asia/Shanghai"
    assert segment.schema_version == "2"
    assert segment.turns[0].turn_id == "turn-9"
    assert segment.turns[0].timestamp.isoformat() == "2026-07-13T01:00:00+00:00"
    assert segment.turns[0].source_timezone == "Asia/Shanghai"
    assert segment.turns[0].timezone_source == "client"


def test_converter_rejects_partial_v2_provenance_instead_of_inventing_values():
    record = make_record("partial", "text")
    record["schema_version"] = 2
    record["turns"][0]["turn_id"] = "only-one-field"
    with pytest.raises(ColdDraftConversionError, match="incomplete_turn_provenance"):
        ColdDraftSegmentConverter().convert(record, "v1")


def test_converter_handles_mixed_transition_segment_per_turn():
    record = make_record("mixed", "legacy", "native")
    record["schema_version"] = 2
    record["turns"][1].update({
        "turn_id": "native-assistant",
        "created_at": "2026-07-15T04:05:00Z",
        "source_timezone": "America/New_York",
        "timezone_source": "configured_default",
    })
    segment = ColdDraftSegmentConverter().convert(record, "v1")
    assert segment.turns[0].turn_id == "mixed:turn:0000"
    assert segment.turns[0].timezone_source == "legacy_segment_fallback"
    assert segment.turns[1].turn_id == "native-assistant"
    assert segment.turns[1].timestamp.isoformat() == "2026-07-15T04:05:00+00:00"
    assert segment.turns[1].timezone_source == "configured_default"


def test_v2_dream_preserves_both_source_times_and_roles(tmp_path):
    store = ColdDraftStore(tmp_path / "cold.jsonl")
    turns = [
        MemoryTurn(
            turn_id="v2-user",
            role="user",
            text="completed yesterday",
            created_at=datetime(2026, 7, 15, 3, 55, tzinfo=UTC),
            source_timezone="America/New_York",
            timezone_source="client",
        ),
        MemoryTurn(
            turn_id="v2-assistant",
            role="assistant",
            text="recorded today",
            created_at=datetime(2026, 7, 15, 4, 5, tzinfo=UTC),
            source_timezone="America/New_York",
            timezone_source="client",
        ),
    ]
    store.append_segment(
        [turn.storage_turn() for turn in turns],
        segment_id="native-v2",
    )
    backend = AdapterFakeBackend()
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
        ingestion_version="v2",
    )
    report = DreamRunner(
        store,
        ColdDraftDigestionTask(store, StaticProvider(adapter)),
    ).run_once(DreamRunPolicy(max_segments=1, ingestion_version="v2"))
    assert report.consumed == 1
    events = list(backend.events.values())
    assert [event["timestamp"] for event in events] == [
        turns[0].created_at,
        turns[1].created_at,
    ]
    assert [event["metadata"]["provenance"]["turn_id"] for event in events] == [
        "v2-user",
        "v2-assistant",
    ]
    assert [
        event["metadata"]["provenance"]["source_role"]
        for event in events
    ] == [
        "user",
        "assistant",
    ]
    assert all(
        event["metadata"]["provenance"]["timezone_source"] == "client"
        for event in events
    )


def test_consumed_transition_changes_only_state_metadata_not_raw_turns(tmp_path):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    owner.append_segment(
        [{"role": "user", "text": "do not alter"}, {"role": "assistant", "text": "verbatim"}],
        segment_id="immutable-raw",
    )
    before = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    provider = FakeProvider()
    DreamRunner(owner, ColdDraftDigestionTask(owner, provider)).run_once(DreamRunPolicy(max_segments=1))
    after = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(item["record_type"] == "dream_selection_cursor" for item in after) == 1
    after = [item for item in after if item["record_type"] == "cold_turn"]
    assert len(before) == len(after) == 2
    assert {item["state"] for item in before} == {"pending_digest"}
    assert {item["state"] for item in after} == {"consumed"}
    assert len({item["consumed_at"] for item in after}) == 1
    for original, consumed in zip(before, after, strict=True):
        assert "consumed_at" not in original
        unchanged = dict(consumed)
        unchanged.pop("consumed_at")
        unchanged["state"] = "pending_digest"
        assert unchanged == original


def test_dream_does_not_import_upstream_magma_networkx_or_faiss():
    forbidden_roots = {"memory", "networkx", "faiss"}
    for path in (ROOT / "Dream").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in forbidden_roots for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_roots


def test_failure_report_does_not_leak_exception_path_secret_or_content():
    record = make_record("safe-id", "raw private conversation")
    system = FakeMemorySystem()
    system.raise_for.add("safe-id")
    runner, _, _ = make_runner([record], system)
    rendered = repr(runner.run_once(DreamRunPolicy(max_segments=1)))
    for forbidden in ("raw private conversation", "OPENAI_API_KEY", "secret", "C:\\private", "traceback", "private-node"):
        assert forbidden not in rendered


def test_completed_zero_output_is_valid_and_consumes():
    class ZeroOutputIngestor:
        def ingest(self, segment):
            return IngestionResult(segment.segment_id, "v1", "completed")

    owner = FakeOwner([make_record("zero-output")])
    result = ColdDraftDigestionTask(
        owner,
        StaticProvider(ZeroOutputIngestor()),
    ).digest(owner.records[0], "v1")
    assert result.status == "consumed"
    assert result.error_code is None
    assert owner.records[0]["state"] == "consumed"


def test_invalid_memory_ids_do_not_consume():
    class BrokenIngestor:
        def ingest(self, segment):
            return IngestionResult(
                segment.segment_id,
                "v1",
                "completed",
                ("",),
            )

    owner = FakeOwner([make_record("not-durable")])
    result = ColdDraftDigestionTask(
        owner,
        StaticProvider(BrokenIngestor()),
    ).digest(owner.records[0], "v1")
    assert result.error_code == "memory_completion_unconfirmed"
    assert owner.records[0]["state"] == "pending_digest"

class AdapterFakeBackend:
    def __init__(self):
        self.events = {}

    def find_memory_id(self, evidence_id):
        for memory_id, event in self.events.items():
            if event["metadata"]["evidence_id"] == evidence_id:
                return memory_id
        return None

    def add_event(self, text, timestamp, metadata):
        memory_id = f"node-{len(self.events)}"
        self.events[memory_id] = {"text": text, "timestamp": timestamp, "metadata": metadata}
        return memory_id

    def create_relationships(self, memory_ids):
        return None

    def persist(self):
        return None

    def resolve_target_entity_ref(self, query):
        return None

    def recall(self, query, policy):
        return []


def test_grounded_assistant_only_output_is_durable_and_consumed(tmp_path):
    store = ColdDraftStore(tmp_path / "cold.jsonl")
    assistant = MemoryTurn(
        turn_id="assistant-only",
        role="assistant",
        text="You still have a laboratory meeting tomorrow.",
        created_at=datetime(2026, 8, 9, 2, 0, tzinfo=UTC),
        source_timezone="Asia/Shanghai",
        timezone_source="client",
    )
    store.append_segment(
        [assistant.storage_turn()],
        segment_id="assistant-only-segment",
    )
    backend = AdapterFakeBackend()
    state_store = IngestionStateStore(tmp_path / "state.json")
    adapter = MagmaMemoryAdapter(backend, state_store)
    policy = DreamRunPolicy(max_segments=1)

    report = DreamRunner(
        store,
        ColdDraftDigestionTask(store, StaticProvider(adapter)),
    ).run_once(policy)

    assert report.consumed == 1 and report.failed == 0
    assert len(backend.events) == 1
    event = next(iter(backend.events.values()))
    assert event["text"] == assistant.text
    assert event["metadata"]["role"] == "assistant"
    assert event["metadata"]["provenance"]["source_role"] == "assistant"
    assert store.list_pending() == []
    state = state_store.read_all()[
        f"assistant-only-segment:{policy.ingestion_version}"
    ]
    assert state["status"] == "completed"
    assert len(state["unit_ids"]) == len(state["memory_ids"]) == 1

class FailConsumeOwner:
    def __init__(self, delegate):
        self.delegate = delegate

    def list_pending(self, limit=None):
        return self.delegate.list_pending(limit)

    def list_pending_page(self, limit):
        return self.delegate.list_pending_page(limit)

    def advance_pending_cursor(self, segment_id):
        return self.delegate.advance_pending_cursor(segment_id)

    def mark_consumed(self, segment_id):
        return False


def test_restart_recovers_completed_adapter_state_and_only_retries_consume(tmp_path):
    store = ColdDraftStore(tmp_path / "cold.jsonl")
    store.append_segment([{"role": "user", "text": "restart"}], segment_id="restart-segment")
    backend = AdapterFakeBackend()
    state_path = tmp_path / "ingestion-state.json"
    first_adapter = MagmaMemoryAdapter(backend, IngestionStateStore(state_path), ingestion_version="v1")
    failing_owner = FailConsumeOwner(store)
    first = DreamRunner(
        failing_owner,
        ColdDraftDigestionTask(failing_owner, StaticProvider(first_adapter)),
    ).run_once(DreamRunPolicy(max_segments=1, ingestion_version="v1"))
    assert first.failed == 1 and len(backend.events) == 1

    restarted_adapter = MagmaMemoryAdapter(backend, IngestionStateStore(state_path), ingestion_version="v1")
    second = DreamRunner(
        store,
        ColdDraftDigestionTask(store, StaticProvider(restarted_adapter)),
    ).run_once(DreamRunPolicy(max_segments=1, ingestion_version="v1"))
    assert second.results[0].already_ingested is True
    assert second.consumed == 1 and len(backend.events) == 1


def test_owner_read_failure_is_structured_and_safe():
    runner, owner, _ = make_runner()
    owner.raise_on_list = True
    report = runner.run_once(DreamRunPolicy(max_segments=1))
    assert report.results[0].error_code == "cold_draft_read_failed"
    assert "private" not in repr(report).lower()


def test_manual_cli_empty_run_outputs_only_structured_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LUMINA_DREAM_COLD_DRAFT_PATH", str(tmp_path / "cold.jsonl"))
    monkeypatch.setenv("LUMINA_DREAM_INGESTION_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("LUMINA_DREAM_MAGMA_PERSIST_DIR", str(tmp_path / "magma"))
    assert main(["--max-segments", "2"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["attempted"] == 0
    assert str(tmp_path) not in output


def test_default_dream_runner_requests_formation_output_budget(tmp_path, monkeypatch):
    import Dream.runner as runner_module

    captured = {}
    model = SimpleNamespace(client_kind="model")

    def build_model(**kwargs):
        captured.update(kwargs)
        return model

    monkeypatch.setattr(runner_module, "build_model_client_from_env", build_model)
    monkeypatch.setenv("LUMINA_DREAM_COLD_DRAFT_PATH", str(tmp_path / "cold.jsonl"))
    monkeypatch.setenv("LUMINA_DREAM_INGESTION_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("LUMINA_DREAM_MAGMA_PERSIST_DIR", str(tmp_path / "magma"))

    runner = build_default_runner()

    assert isinstance(runner, DreamRunner)
    assert captured == {
        "max_tokens_override": 8192,
    }


def test_manual_dream_cli_requests_formation_output_budget(monkeypatch, capsys):
    import Dream.runner as runner_module

    captured = {}
    model = SimpleNamespace(client_kind="model")
    report = runner_module.DreamRunReport.from_results(())

    def build_model(**kwargs):
        captured.update(kwargs)
        return model

    monkeypatch.setattr(runner_module, "build_model_client_from_env", build_model)
    monkeypatch.setattr(
        runner_module,
        "build_default_runner",
        lambda injected, **kwargs: SimpleNamespace(run_once=lambda _policy: report),
    )

    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["attempted"] == 0
    assert captured == {
        "max_tokens_override": 8192,
    }


def test_dream_runner_is_only_triggered_by_explicit_http_request(tmp_path):
    class DreamRunnerSpy:
        def __init__(self):
            self.calls = []

        def run_once(self, policy):
            self.calls.append(policy)
            return SimpleNamespace(
                attempted=1,
                ingested=1,
                consumed=1,
                skipped=0,
                failed=0,
                progress_saved=True,
            )

    app = create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "compaction.json",
        model_client=MockModelClient(),
        env_file_path=None,
        enable_compaction=False,
        recall_enabled=False,
        memory_retriever=object(),
    )
    runner = DreamRunnerSpy()
    app.state.dream_runner = runner
    client = TestClient(app)

    status = client.get("/api/status")
    chat = client.post("/api/chat", json={"message": "ordinary chat"})

    assert status.status_code == 200
    assert status.json()["dream"] == {
        "available": True,
        "running": False,
        "pending_segments": 0,
        "pending_truncated": False,
    }
    assert chat.status_code == 200
    assert runner.calls == []
    assert app.state.writer_lock is not None
    assert app.state.dream_running is False

    dream = client.post("/api/dream/run")

    assert dream.status_code == 200
    assert dream.json() == {
        "attempted": 1,
        "ingested": 1,
        "consumed": 1,
        "skipped": 0,
        "failed": 0,
    }
    assert len(runner.calls) == 1


@pytest.mark.skipif(
    Path(sys.executable).resolve()
    != (ROOT / "Conversation_Memory" / ".venv" / "Scripts" / "python.exe").resolve(),
    reason="real MAGMA test runs in the isolated Conversation Memory environment",
)
def test_real_magma_manual_dream_ingestion_consumes_production_segment(tmp_path):
    store = ColdDraftStore(tmp_path / "cold.jsonl")
    store.append_segment(
        [{"role": "user", "text": "Raven prepared the membrane."}, {"role": "assistant", "text": "Raven recorded the result."}],
        segment_id="dream-real-magma",
    )
    provider = RealMemoryIngestorProvider(tmp_path / "magma", tmp_path / "state.json")
    report = DreamRunner(store, ColdDraftDigestionTask(store, provider)).run_once(
        DreamRunPolicy(max_segments=1)
    )
    assert report.consumed == 1 and report.failed == 0
    assert store.list_pending() == []
    state = IngestionStateStore(tmp_path / "state.json").read_all()[
        "dream-real-magma:grounded-span-v2"
    ]
    assert state["status"] == "completed"
    assert len(state["unit_ids"]) == len(state["memory_ids"]) == 2
