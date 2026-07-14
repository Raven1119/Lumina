import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.contracts import MemoryTurn
from core.draft_store import JsonlDraftStore


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
