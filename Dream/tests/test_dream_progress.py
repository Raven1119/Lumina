"""Real Cold owner and Dream orchestration with deterministic memory outcomes."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from core.cold_draft_store import ColdDraftStore
from Dream.cold_draft_digest import ColdDraftDigestionTask
from Dream.models import DreamRunPolicy
from Dream.runner import DreamRunner
from adapter.models import IngestionResult


_BLOCKED = tuple(f"dream-blocked-{index:02d}" for index in range(10))
_HEALTHY = "dream-healthy-eleventh"


def _append(owner, segment_id):
    owner.append_segment([{
        "turn_id": segment_id + ":turn", "role": "user",
        "text": "A synthetic observation for " + segment_id,
        "created_at": "2026-09-13T00:00:00Z", "source_timezone": "UTC",
        "timezone_source": "client",
    }], segment_id=segment_id)


class _Memory:
    def __init__(self, blocked=()):
        self.blocked = set(blocked)
        self.calls = []
        self.writes = []
        self.completed = set()

    def ingest(self, segment):
        sid = segment.segment_id
        self.calls.append(sid)
        if sid in self.blocked:
            return IngestionResult(sid, "grounded-span-v2", "failed",
                                   retryable=False, safe_error_code="formation_processing_incomplete")
        already = sid in self.completed
        if not already:
            self.completed.add(sid)
            self.writes.append(sid)
        return IngestionResult(sid, "grounded-span-v2", "completed",
                               ("synthetic-evidence:" + sid,), already_ingested=already)


class _Provider:
    def __init__(self, memory):
        self.memory = memory

    def get(self, ingestion_version):
        assert ingestion_version == "grounded-span-v2"
        return self.memory


def _run(owner, memory, policy=None):
    task = ColdDraftDigestionTask(owner, _Provider(memory))
    return DreamRunner(owner, task).run_once(policy or DreamRunPolicy())


def _prefix_run(path):
    owner, memory = ColdDraftStore(Path(path)), _Memory(_BLOCKED)
    report = _run(owner, memory)
    return {"report": asdict(report), "calls": memory.calls,
            "pending": [r["segment_id"] for r in owner.list_pending()]}


@pytest.mark.parametrize("restart_process", [False, True])
def test_second_run_reaches_eleventh_segment_and_preserves_bad_raw(tmp_path, restart_process):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    for sid in (*_BLOCKED, _HEALTHY):
        _append(owner, sid)
    bad_raw = [line for line in path.read_bytes().splitlines(keepends=True)
               if json.loads(line).get("segment_id") in _BLOCKED]
    first = _prefix_run(path)
    if restart_process:
        code = "import json,runpy,sys; module=runpy.run_path(sys.argv[1]); print(json.dumps(module['_prefix_run'](sys.argv[2])))"
        process = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", "-c", code, __file__, str(path)],
            cwd=Path(__file__).resolve().parents[2], capture_output=True,
            text=True, encoding="utf-8", check=True,
        )
        second = json.loads(process.stdout)
    else:
        second = _prefix_run(path)
    assert first["calls"] == list(_BLOCKED)
    assert second["calls"][0] == _HEALTHY
    assert set(second["pending"]) == set(_BLOCKED)
    for run in (first, second):
        assert run["report"]["progress_saved"] is True
        assert run["report"]["attempted"] <= 10
        assert len(run["calls"]) == len(set(run["calls"]))
    assert all(result["retryable"] is False for result in first["report"]["results"])
    final_raw = path.read_bytes().splitlines(keepends=True)
    assert all(line in final_raw for line in bad_raw)


def test_stop_on_error_repeats_head_without_advancing_cursor(tmp_path):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    _append(owner, _BLOCKED[0])
    _append(owner, _HEALTHY)
    # An existing default-mode cursor must not change explicit stop-on-error's
    # legacy prefix semantics or be mutated by that mode.
    assert owner.advance_pending_cursor(_BLOCKED[0]) is True
    original = path.read_bytes()
    memory = _Memory(_BLOCKED)
    for _ in range(2):
        report = _run(ColdDraftStore(path), memory, DreamRunPolicy(stop_on_error=True))
        assert report.attempted == report.failed == 1
        assert report.progress_saved is True
        assert path.read_bytes() == original
    assert memory.calls == [_BLOCKED[0], _BLOCKED[0]]
    assert memory.writes == []


def test_consume_failure_rotates_then_recovers_completed_memory_without_new_write(tmp_path, monkeypatch):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    _append(owner, "consume-first")
    _append(owner, "healthy-second")
    memory = _Memory()
    monkeypatch.setattr(owner, "mark_consumed", lambda sid: False)
    policy = DreamRunPolicy(max_segments=1)
    first = _run(owner, memory, policy)
    assert first.results[0].error_code == "cold_draft_consume_failed"
    assert first.results[0].retryable is True
    assert first.progress_saved is True
    second = _run(ColdDraftStore(path), memory, policy)
    assert second.results[0].segment_id == "healthy-second"
    assert second.consumed == 1
    third = _run(ColdDraftStore(path), memory, policy)
    assert third.results[0].segment_id == "consume-first"
    assert third.results[0].already_ingested is True
    assert third.consumed == 1
    assert memory.calls == ["consume-first", "healthy-second", "consume-first"]
    assert memory.writes == ["consume-first", "healthy-second"]
    assert ColdDraftStore(path).list_pending() == []


@pytest.mark.parametrize("raises", [False, True])
def test_cursor_save_failure_keeps_digest_result_and_stops_before_next_attempt(tmp_path, monkeypatch, raises):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    _append(owner, "first")
    _append(owner, "second")
    memory = _Memory()

    def fail_progress(segment_id):
        if raises:
            raise OSError("synthetic progress write failure")
        return False

    monkeypatch.setattr(owner, "advance_pending_cursor", fail_progress)
    report = _run(owner, memory, DreamRunPolicy(max_segments=2))
    assert report.progress_saved is False
    assert report.attempted == report.ingested == report.consumed == 1
    assert report.failed == 0
    assert report.results[0].segment_id == "first"
    assert memory.calls == ["first"]
    assert [r["segment_id"] for r in owner.list_pending()] == ["second"]
    resumed = _run(ColdDraftStore(path), memory, DreamRunPolicy(max_segments=1))
    assert resumed.consumed == 1
    assert memory.calls == ["first", "second"]


def test_invalid_identifier_uses_raw_owner_key_for_progress_not_safe_report_id(tmp_path, monkeypatch):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    _append(owner, "raw key with spaces")
    _append(owner, "valid-next")
    memory = _Memory()
    advanced = []
    original_advance = owner.advance_pending_cursor

    def tracked_advance(segment_id):
        advanced.append(segment_id)
        return original_advance(segment_id)

    monkeypatch.setattr(owner, "advance_pending_cursor", tracked_advance)
    first = _run(owner, memory, DreamRunPolicy(max_segments=1))
    assert first.results[0].segment_id == "invalid-segment"
    assert first.results[0].error_code == "invalid_segment_id"
    assert first.results[0].retryable is False
    assert first.progress_saved is True
    assert advanced == ["raw key with spaces"]
    assert memory.calls == []
    second = _run(ColdDraftStore(path), memory, DreamRunPolicy(max_segments=1))
    assert second.results[0].segment_id == "valid-next"
    assert second.consumed == 1
    assert [r["segment_id"] for r in owner.list_pending()] == ["raw key with spaces"]


def test_http_cursor_failure_is_safe_and_releases_writer_after_durable_digest(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from core.model_client import MockModelClient

    # Isolated execution can import core.main for the first time here. Its
    # module-level app must not initialize the production memory backend.
    with monkeypatch.context() as import_scope:
        import_scope.setattr(MagmaMemoryAdapter, "create_real", lambda *a, **kw: object())
        from core.main import create_app

    app = create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "compaction.json",
        mind_decision_log_path=tmp_path / "decisions.jsonl",
        model_client=MockModelClient(), env_file_path=None,
        enable_compaction=False, recall_enabled=False, memory_retriever=object(),
    )
    owner, memory = app.state.cold_draft_store, _Memory()
    _append(owner, "http-first")
    _append(owner, "http-second")
    app.state.dream_runner = DreamRunner(owner, ColdDraftDigestionTask(owner, _Provider(memory)))
    original_advance = owner.advance_pending_cursor
    monkeypatch.setattr(owner, "advance_pending_cursor", lambda sid: False)
    client = TestClient(app)
    failed = client.post("/api/dream/run")
    assert failed.status_code == 500
    assert failed.json() == {"detail": {"code": "dream_failed", "message": "Dream could not complete"}}
    assert app.state.dream_running is False
    assert app.state.writer_lock.acquire(blocking=False) is True
    app.state.writer_lock.release()
    assert memory.calls == ["http-first"]
    assert [r["segment_id"] for r in owner.list_pending()] == ["http-second"]
    monkeypatch.setattr(owner, "advance_pending_cursor", original_advance)
    resumed = client.post("/api/dream/run")
    assert resumed.status_code == 200
    assert resumed.json()["consumed"] == 1
    assert memory.calls == memory.writes == ["http-first", "http-second"]
