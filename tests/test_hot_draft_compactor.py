import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import core.hot_draft_compactor as compactor_module
from core.cold_draft_store import ColdDraftStore
from core.contracts import MemoryTurn
from core.draft_store import JsonlDraftStore
from core.hot_draft_compactor import HotDraftCompactor


def _turn(index: int, role: str) -> MemoryTurn:
    offset = 0 if role == "user" else 1
    return MemoryTurn(
        turn_id=f"turn-{index}-{role}",
        role=role,
        text=f"{role}-{index}",
        created_at=datetime(2026, 7, 14, tzinfo=UTC)
        + timedelta(minutes=index * 2 + offset),
        source_timezone="America/New_York",
        timezone_source="client",
    )


def _populate(store: JsonlDraftStore, pairs: int, *, start: int = 0) -> None:
    for index in range(start, start + pairs):
        store.append_turn(_turn(index, "user"))
        store.append_turn(_turn(index, "assistant"))


class _RecordingSummarizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, list[MemoryTurn]]] = []

    def __call__(
        self,
        old_summary: str | None,
        moved_turns: list[MemoryTurn],
    ) -> str:
        self.calls.append((old_summary, list(moved_turns)))
        facts = ",".join(turn.text for turn in moved_turns)
        return f"{old_summary + '|' if old_summary else ''}{facts}"


def _build(
    tmp_path: Path,
    *,
    summarizer=None,
    hot_store: JsonlDraftStore | None = None,
    cold_store=None,
):
    hot = hot_store or JsonlDraftStore(tmp_path / "hot_drafts.jsonl")
    cold = cold_store or ColdDraftStore(tmp_path / "cold_drafts.jsonl")
    state = tmp_path / "hot_draft_compaction_state.json"
    recorder = summarizer or _RecordingSummarizer()
    compactor = HotDraftCompactor(
        hot,
        cold,
        state,
        summarizer=recorder,
    )
    return compactor, hot, cold, state, recorder


def test_24_raw_turns_do_not_compact(tmp_path: Path) -> None:
    compactor, hot, cold, state, summarizer = _build(tmp_path)
    _populate(hot, 12)
    before = (tmp_path / "hot_drafts.jsonl").read_bytes()
    assert compactor.is_running is False

    result = compactor.maybe_compact()

    assert compactor.is_running is False
    assert result.status == "not_needed"
    assert result.archived_turns == 0
    assert result.summary_updated is False
    assert (tmp_path / "hot_drafts.jsonl").read_bytes() == before
    assert hot.read_summary() is None
    assert cold.list_pending() == []
    assert not state.exists()
    assert summarizer.calls == []


def test_first_26_turn_compaction_archives_14_then_keeps_summary_and_12_raw(
    tmp_path: Path,
) -> None:
    compactor, hot, cold, state, summarizer = _build(tmp_path)
    _populate(hot, 13)

    result = compactor.maybe_compact()

    assert compactor.is_running is False
    assert result.status == "completed"
    assert result.archived_turns == 14
    assert result.summary_updated is True
    assert len(summarizer.calls) == 1
    assert summarizer.calls[0][0] is None
    assert [turn.text for turn in summarizer.calls[0][1]] == [
        value
        for index in range(7)
        for value in (f"user-{index}", f"assistant-{index}")
    ]

    pending = cold.list_pending()
    assert len(pending) == 1
    assert len(pending[0]["turns"]) == 14
    assert all(turn.get("record_type") != "summary" for turn in pending[0]["turns"])
    cold_lines = [
        json.loads(line)
        for line in (tmp_path / "cold_drafts.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert len(cold_lines) == 14
    assert {line["record_type"] for line in cold_lines} == {"cold_turn"}
    assert {line["segment_id"] for line in cold_lines} == {
        pending[0]["segment_id"]
    }
    assert [line["segment_turn_index"] for line in cold_lines] == list(range(14))
    assert {line["segment_turn_count"] for line in cold_lines} == {14}
    assert [
        {
            key: line[key]
            for key in (
                "turn_id",
                "role",
                "text",
                "created_at",
                "source_timezone",
                "timezone_source",
            )
        }
        for line in cold_lines
    ] == pending[0]["turns"]
    assert [turn.text for turn in hot.list_all_raw()] == [
        value
        for index in range(7, 13)
        for value in (f"user-{index}", f"assistant-{index}")
    ]
    summary = hot.read_summary()
    assert summary is not None
    assert summary.generation == 1
    assert summary.source_turn_count == 14

    records = [
        json.loads(line)
        for line in (tmp_path / "hot_drafts.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert records[0]["record_type"] == "summary"
    assert len(records) == 13
    assert sum(record.get("record_type") == "summary" for record in records) == 1

    state_record = json.loads(state.read_text(encoding="utf-8"))
    assert set(state_record) == {
        "schema_version",
        "generation",
        "last_compaction_id",
        "last_archived_segment_id",
    }
    assert state_record["schema_version"] == 2
    assert state_record["generation"] == 1
    assert state_record["last_archived_segment_id"] == pending[0]["segment_id"]
    assert "compressed_until_count" not in state_record


def test_second_pass_replaces_summary_and_does_not_resubmit_removed_raw(
    tmp_path: Path,
) -> None:
    summarizer = _RecordingSummarizer()
    compactor, hot, cold, _, _ = _build(tmp_path, summarizer=summarizer)
    _populate(hot, 13)
    assert compactor.maybe_compact().status == "completed"
    first_summary = hot.read_summary()
    assert first_summary is not None

    _populate(hot, 7, start=13)
    result = compactor.maybe_compact()

    assert compactor.is_running is False
    assert result.status == "completed"
    assert result.archived_turns == 14
    assert len(summarizer.calls) == 2
    old_summary, second_moved = summarizer.calls[1]
    assert old_summary == first_summary.content
    assert [turn.text for turn in second_moved] == [
        value
        for index in range(7, 14)
        for value in (f"user-{index}", f"assistant-{index}")
    ]
    assert "user-0" not in [turn.text for turn in second_moved]

    summary = hot.read_summary()
    assert summary is not None
    assert summary.generation == 2
    assert summary.source_turn_count == 28
    assert first_summary.content in summary.content
    assert len(hot.list_all_raw()) == 12
    pending = cold.list_pending()
    assert len(pending) == 2
    cold_lines = [
        json.loads(line)
        for line in (tmp_path / "cold_drafts.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert len(cold_lines) == 28
    assert [line["segment_id"] for line in cold_lines[:14]] == [
        pending[0]["segment_id"]
    ] * 14
    assert [line["segment_id"] for line in cold_lines[14:]] == [
        pending[1]["segment_id"]
    ] * 14
    assert [
        line["segment_turn_index"] for line in cold_lines[:14]
    ] == list(range(14))
    assert [
        line["segment_turn_index"] for line in cold_lines[14:]
    ] == list(range(14))
    assert {line["segment_turn_count"] for line in cold_lines} == {14}
    records = [
        json.loads(line)
        for line in (tmp_path / "hot_drafts.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert sum(record.get("record_type") == "summary" for record in records) == 1


@pytest.mark.parametrize(
    ("raw_count", "expected_archived", "expected_remaining"),
    [
        (24, 0, 24),
        (25, 12, 13),
        (26, 14, 12),
    ],
)
def test_pair_safe_trigger_boundaries(
    tmp_path: Path,
    raw_count: int,
    expected_archived: int,
    expected_remaining: int,
) -> None:
    compactor, hot, cold, _, _ = _build(tmp_path)
    _populate(hot, raw_count // 2)
    if raw_count % 2:
        hot.append_turn(_turn(raw_count // 2, "user"))

    result = compactor.maybe_compact()

    assert result.archived_turns == expected_archived
    assert len(hot.list_all_raw()) == expected_remaining
    if expected_archived:
        roles = [turn["role"] for turn in cold.list_pending()[0]["turns"]]
        assert roles == ["user", "assistant"] * (expected_archived // 2)
    else:
        assert cold.list_pending() == []


def test_non_contiguous_prefix_is_not_split_or_archived(tmp_path: Path) -> None:
    compactor, hot, cold, _, summarizer = _build(tmp_path)
    hot.append_turn(_turn(0, "assistant"))
    _populate(hot, 13, start=1)

    result = compactor.maybe_compact()

    assert result.status == "not_needed"
    assert len(hot.list_all_raw()) == 27
    assert cold.list_pending() == []
    assert summarizer.calls == []


def test_summarizer_failure_writes_neither_cold_nor_hot(tmp_path: Path) -> None:
    def fail_summary(old_summary, moved_turns):
        raise RuntimeError("provider secret")

    compactor, _, cold, state, _ = _build(tmp_path, summarizer=fail_summary)
    _populate(compactor._hot_store, 13)
    hot_path = tmp_path / "hot_drafts.jsonl"
    before = hot_path.read_bytes()

    result = compactor.maybe_compact()

    assert compactor.is_running is False
    assert result.status == "failed"
    assert result.archived_turns == 0
    assert result.summary_updated is False
    assert hot_path.read_bytes() == before
    assert cold.list_pending() == []
    assert not state.exists()


@pytest.mark.parametrize("invalid_summary", [None, 42, "", "   "])
def test_invalid_summarizer_output_writes_neither_cold_nor_hot(
    tmp_path: Path,
    invalid_summary,
) -> None:
    def invalid_result(old_summary, moved_turns):
        return invalid_summary

    compactor, hot, cold, state, _ = _build(
        tmp_path,
        summarizer=invalid_result,
    )
    _populate(hot, 13)
    hot_path = tmp_path / "hot_drafts.jsonl"
    before = hot_path.read_bytes()

    result = compactor.maybe_compact()

    assert compactor.is_running is False
    assert result.status == "failed"
    assert result.archived_turns == 0
    assert result.summary_updated is False
    assert hot_path.read_bytes() == before
    assert cold.list_pending() == []
    assert not state.exists()


class _FailingColdStore:
    def append_segment(self, turns, source, *, segment_id):
        raise OSError("private path")


def test_cold_failure_does_not_modify_hot(tmp_path: Path) -> None:
    compactor, hot, _, state, summarizer = _build(
        tmp_path,
        cold_store=_FailingColdStore(),
    )
    _populate(hot, 13)
    hot_path = tmp_path / "hot_drafts.jsonl"
    before = hot_path.read_bytes()

    result = compactor.maybe_compact()

    assert compactor.is_running is False
    assert result.status == "failed"
    assert hot_path.read_bytes() == before
    assert len(summarizer.calls) == 1
    assert not state.exists()


class _FailOnceHotStore(JsonlDraftStore):
    def __init__(self, path: Path, *, fail_stage: str) -> None:
        super().__init__(path)
        self._fail_stage = fail_stage
        self._failed = False
        self._armed = False

    def arm(self) -> None:
        self._armed = True

    def _write_json_line(self, file, record) -> None:
        if (
            self._fail_stage == "write"
            and self._armed
            and not self._failed
        ):
            self._failed = True
            raise OSError("temp write failed")
        return super()._write_json_line(file, record)

    def _replace_file(self, source: Path, destination: Path) -> None:
        if self._fail_stage == "replace" and self._armed and not self._failed:
            self._failed = True
            raise OSError("replace failed")
        return super()._replace_file(source, destination)


@pytest.mark.parametrize("fail_stage", ["write", "replace"])
def test_hot_failure_preserves_old_file_and_cold_retry_is_idempotent(
    tmp_path: Path,
    fail_stage: str,
) -> None:
    hot = _FailOnceHotStore(
        tmp_path / "hot_drafts.jsonl",
        fail_stage=fail_stage,
    )
    compactor, _, cold, state, _ = _build(tmp_path, hot_store=hot)
    _populate(hot, 13)
    hot.arm()
    hot_path = tmp_path / "hot_drafts.jsonl"
    before = hot_path.read_bytes()

    first = compactor.maybe_compact()
    assert compactor.is_running is False
    assert first.status == "failed"
    assert hot_path.read_bytes() == before
    assert len(cold.list_pending()) == 1
    assert not state.exists()

    second = compactor.maybe_compact()
    assert second.status == "completed"
    assert second.archived_turns == 14
    assert len(cold.list_pending()) == 1
    assert len(hot.list_all_raw()) == 12
    assert hot.read_summary() is not None


def test_state_replace_failure_keeps_new_hot_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hot = JsonlDraftStore(tmp_path / "hot_drafts.jsonl")
    cold = ColdDraftStore(tmp_path / "cold_drafts.jsonl")
    summarizer = _RecordingSummarizer()
    state = tmp_path / "hot_draft_compaction_state.json"
    compactor = HotDraftCompactor(
        hot,
        cold,
        state,
        summarizer=summarizer,
    )
    _populate(hot, 13)
    original_replace = compactor_module.os.replace

    def fail_state_replace(source: Path, destination: Path) -> None:
        if Path(destination) == state:
            raise OSError("state replace failed")
        original_replace(source, destination)

    monkeypatch.setattr(compactor_module.os, "replace", fail_state_replace)

    result = compactor.maybe_compact()

    assert compactor.is_running is False
    assert result.status == "completed"
    assert result.archived_turns == 14
    assert result.summary_updated is True
    assert hot.read_summary() is not None
    assert len(hot.list_all_raw()) == 12
    assert len(cold.list_pending()) == 1
    assert not state.exists()
    assert list(tmp_path.glob(".hot_draft_compaction_state.json.*.tmp")) == []
    assert compactor.maybe_compact().status == "not_needed"
    assert len(cold.list_pending()) == 1


def test_unexpected_exception_after_start_restores_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compactor, hot, _, _, _ = _build(tmp_path)
    _populate(hot, 13)

    def explode(turns):
        raise RuntimeError("unexpected internal failure")

    monkeypatch.setattr(compactor, "_stable_compaction_id", explode)
    with pytest.raises(RuntimeError, match="unexpected internal failure"):
        compactor.maybe_compact()
    assert compactor.is_running is False
