import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.contracts import MemoryTurn
from core.draft_store import HotDraftSummary, JsonlDraftStore


def _turn(
    role: str,
    text: str,
    *,
    turn_id: str = "turn-1",
    created_at: datetime = datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
) -> MemoryTurn:
    return MemoryTurn(
        turn_id=turn_id,
        role=role,
        text=text,
        created_at=created_at,
        source_timezone="Asia/Shanghai",
        timezone_source="client",
    )


def test_jsonl_draft_store_appends_safe_record(tmp_path: Path) -> None:
    path = tmp_path / "draft" / "turns.jsonl"
    count = JsonlDraftStore(path).append_turn(_turn("user", "hello"))
    record = json.loads(path.read_text(encoding="utf-8"))
    assert count == 1
    assert record["role"] == "user"
    assert record["text"] == "hello"
    assert record["created_at"] == "2026-07-14T02:00:00.000000Z"
    assert record["turn_id"] == "turn-1"
    assert record["source_timezone"] == "Asia/Shanghai"
    assert record["timezone_source"] == "client"
    assert record["schema_version"] == 2
    assert record["source"] == "chat_draft"
    assert record["safe"] is True


def test_recent_turns_are_chronological_and_bounded(tmp_path: Path) -> None:
    store = JsonlDraftStore(tmp_path / "turns.jsonl")
    one = _turn("user", "one", turn_id="turn-1")
    two = _turn("assistant", "two", turn_id="turn-2")
    three = _turn("user", "three", turn_id="turn-3")
    store.append_turn(one)
    store.append_turn(two)
    store.append_turn(three)
    assert store.list_recent(2) == [
        two,
        three,
    ]


def test_store_survives_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    turn = _turn("user", "persist")
    JsonlDraftStore(path).append_turn(turn)
    assert JsonlDraftStore(path).list_recent() == [turn]


def test_corrupt_and_non_chat_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    path.write_text(
        '{"role":"user","text":"kept","created_at":"2025-01-02T03:04:05+00:00",'
        '"source":"chat_draft","safe":true}\n'
        "not-json\n"
        '{"role":"system","text":"drop"}\n'
        '{"role":"assistant","text":"also kept"}\n',
        encoding="utf-8",
    )
    before = path.read_bytes()
    assert JsonlDraftStore(path).list_recent(10) == [
        MemoryTurn(role="user", text="kept"),
        MemoryTurn(role="assistant", text="also kept"),
    ]
    assert path.read_bytes() == before


def test_append_retry_reuses_native_turn_id_without_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    store = JsonlDraftStore(path)
    turn = _turn("user", "same")
    assert store.append_turn(turn) == 1
    assert store.append_turn(turn) == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_identical_text_with_distinct_ids_remains_two_turns(tmp_path: Path) -> None:
    store = JsonlDraftStore(tmp_path / "turns.jsonl")
    store.append_turn(_turn("user", "same", turn_id="first"))
    store.append_turn(_turn("user", "same", turn_id="second"))
    assert [turn.turn_id for turn in store.list_recent(2)] == ["first", "second"]


def test_new_append_rejects_legacy_or_naive_provenance(tmp_path: Path) -> None:
    store = JsonlDraftStore(tmp_path / "turns.jsonl")
    with pytest.raises(ValueError, match="native turn provenance"):
        store.append_turn(MemoryTurn(role="user", text="legacy"))
    with pytest.raises(ValueError, match="created_at"):
        _turn("user", "naive", created_at=datetime(2026, 7, 14, 2, 0))


def test_atomic_replacement_writes_one_summary_before_recent_raw_turns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hot_drafts.jsonl"
    store = JsonlDraftStore(path)
    old = _turn("user", "old", turn_id="old")
    recent = _turn("assistant", "recent", turn_id="recent")
    store.append_turn(old)
    store.append_turn(recent)

    summary = HotDraftSummary(
        content="rolling facts",
        generation=1,
        source_turn_count=1,
        updated_at="2026-07-14T03:00:00Z",
    )
    store.replace_contents_atomically(summary, [recent])

    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0] == summary.storage_record()
    assert records[1]["turn_id"] == "recent"
    assert store.read_summary() == summary
    assert store.list_all_raw() == [recent]
    assert store.read_context().summary == summary
    assert store.read_context().raw_turns == (recent,)


def test_replacing_summary_replaces_instead_of_accumulating_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hot_drafts.jsonl"
    store = JsonlDraftStore(path)
    recent = _turn("user", "recent", turn_id="recent")
    first = HotDraftSummary("first", 1, 2, "2026-07-14T03:00:00Z")
    second = HotDraftSummary("second", 2, 4, "2026-07-14T04:00:00Z")

    store.replace_contents_atomically(first, [recent])
    store.replace_contents_atomically(second, [recent])

    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record.get("record_type") for record in records].count("summary") == 1
    assert records[0] == second.storage_record()


def test_summary_timestamp_must_be_utc(tmp_path: Path) -> None:
    store = JsonlDraftStore(tmp_path / "hot_drafts.jsonl")
    non_utc = HotDraftSummary(
        "summary",
        1,
        2,
        "2026-07-14T11:00:00+08:00",
    )

    with pytest.raises(ValueError, match="valid hot draft summary"):
        store.replace_contents_atomically(non_utc, [])

    assert not (tmp_path / "hot_drafts.jsonl").exists()


def test_failed_atomic_replace_leaves_old_hot_file_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "hot_drafts.jsonl"
    store = JsonlDraftStore(path)
    turn = _turn("user", "kept", turn_id="kept")
    store.append_turn(turn)
    before = path.read_bytes()
    summary = HotDraftSummary("summary", 1, 1, "2026-07-14T03:00:00Z")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(store, "_replace_file", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        store.replace_contents_atomically(summary, [])

    assert path.read_bytes() == before
    assert list(tmp_path.glob(".hot_drafts.jsonl.*.tmp")) == []


def test_failed_temp_write_leaves_old_hot_file_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "hot_drafts.jsonl"
    store = JsonlDraftStore(path)
    turn = _turn("user", "kept", turn_id="kept")
    store.append_turn(turn)
    before = path.read_bytes()
    summary = HotDraftSummary("summary", 1, 1, "2026-07-14T03:00:00Z")
    calls = 0
    original = store._write_json_line

    def fail_second_write(file, record) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("temp write failed")
        original(file, record)

    monkeypatch.setattr(store, "_write_json_line", fail_second_write)
    with pytest.raises(OSError, match="temp write failed"):
        store.replace_contents_atomically(summary, [turn])

    assert path.read_bytes() == before
    assert list(tmp_path.glob(".hot_drafts.jsonl.*.tmp")) == []
