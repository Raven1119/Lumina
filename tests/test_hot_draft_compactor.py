from pathlib import Path

from core.cold_draft_store import ColdDraftStore
from core.contracts import MemoryTurn
from core.draft_store import JsonlDraftStore
from core.hot_draft_compactor import HotDraftCompactor


def _populate(store: JsonlDraftStore, pairs: int) -> None:
    for index in range(pairs):
        store.append_turn(MemoryTurn(role="user", text=f"user-{index}"))
        store.append_turn(MemoryTurn(role="assistant", text=f"assistant-{index}"))


def _build(tmp_path: Path, *, retain: int = 4, maximum: int = 6):
    hot = JsonlDraftStore(tmp_path / "hot.jsonl")
    cold = ColdDraftStore(tmp_path / "cold.jsonl")
    state = tmp_path / "state.json"
    compactor = HotDraftCompactor(
        hot,
        cold,
        state,
        retain_recent_raw_turns=retain,
        max_raw_turns_before_compression=maximum,
    )
    return compactor, hot, cold, state


def test_below_threshold_is_unchanged(tmp_path: Path) -> None:
    compactor, hot, cold, _ = _build(tmp_path)
    _populate(hot, 3)
    before = compactor.get_context_turns()
    result = compactor.maybe_compact()
    assert result.status == "skipped"
    assert compactor.get_context_turns() == before
    assert cold.list_pending() == []


def test_threshold_creates_cold_segment_and_bounded_context(tmp_path: Path) -> None:
    compactor, hot, cold, _ = _build(tmp_path)
    _populate(hot, 4)
    result = compactor.maybe_compact()
    assert result.compacted is True
    assert result.compressed_turn_count == 4
    pending = cold.list_pending()
    assert len(pending) == 1
    assert pending[0]["turns"] == [
        {"role": "user", "text": "user-0"},
        {"role": "assistant", "text": "assistant-0"},
        {"role": "user", "text": "user-1"},
        {"role": "assistant", "text": "assistant-1"},
    ]
    view = compactor.get_context_turns()
    assert view[0]["text"].startswith("[Compressed conversation segment")
    assert view[-4:] == [
        {"role": "user", "text": "user-2"},
        {"role": "assistant", "text": "assistant-2"},
        {"role": "user", "text": "user-3"},
        {"role": "assistant", "text": "assistant-3"},
    ]
    assert len(hot.list_recent(100)) == 8


def test_only_complete_user_assistant_pairs_are_compacted(tmp_path: Path) -> None:
    compactor, hot, cold, _ = _build(tmp_path, retain=3, maximum=4)
    _populate(hot, 3)
    result = compactor.maybe_compact()
    assert result.compressed_turn_count == 2
    assert [turn["role"] for turn in cold.list_pending()[0]["turns"]] == [
        "user",
        "assistant",
    ]


class _FailingColdStore:
    def append_segment(self, turns, source, *, segment_id):
        raise OSError("private path")


def test_cold_failure_does_not_advance_hot_context(tmp_path: Path) -> None:
    hot = JsonlDraftStore(tmp_path / "hot.jsonl")
    _populate(hot, 4)
    compactor = HotDraftCompactor(
        hot,
        _FailingColdStore(),
        tmp_path / "state.json",
        retain_recent_raw_turns=4,
        max_raw_turns_before_compression=6,
    )
    before = compactor.get_context_turns()
    result = compactor.maybe_compact()
    assert result.status == "cold_draft_failed"
    assert compactor.get_context_turns() == before
    assert not (tmp_path / "state.json").exists()


class _FailFirstStateWrite(HotDraftCompactor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_once = True

    def _write_state(self, *, summaries, compressed_until_count):
        if self.fail_once:
            self.fail_once = False
            raise OSError("state write failed")
        return super()._write_state(
            summaries=summaries,
            compressed_until_count=compressed_until_count,
        )


def test_state_failure_retry_reuses_cold_segment_instead_of_duplicating(
    tmp_path: Path,
) -> None:
    hot = JsonlDraftStore(tmp_path / "hot.jsonl")
    cold = ColdDraftStore(tmp_path / "cold.jsonl")
    _populate(hot, 4)
    compactor = _FailFirstStateWrite(
        hot,
        cold,
        tmp_path / "state.json",
        retain_recent_raw_turns=4,
        max_raw_turns_before_compression=6,
    )
    assert compactor.maybe_compact().status == "hot_state_failed"
    assert len(cold.list_pending()) == 1
    assert compactor.maybe_compact().status == "compacted"
    assert len(cold.list_pending()) == 1
    assert compactor.maybe_compact().status == "skipped"
    assert len(cold.list_pending()) == 1


def test_restart_restores_logical_context_and_checkpoint(tmp_path: Path) -> None:
    compactor, hot, cold, state = _build(tmp_path)
    _populate(hot, 4)
    compactor.maybe_compact()
    before = compactor.get_context_turns()
    restarted = HotDraftCompactor(
        JsonlDraftStore(tmp_path / "hot.jsonl"),
        ColdDraftStore(tmp_path / "cold.jsonl"),
        state,
        retain_recent_raw_turns=4,
        max_raw_turns_before_compression=6,
    )
    assert restarted.get_context_turns() == before
    assert restarted.maybe_compact().status == "skipped"
    assert len(cold.list_pending()) == 1
