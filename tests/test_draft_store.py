import json
from pathlib import Path

from core.contracts import MemoryTurn
from core.draft_store import JsonlDraftStore


def test_jsonl_draft_store_appends_safe_record(tmp_path: Path) -> None:
    path = tmp_path / "draft" / "turns.jsonl"
    count = JsonlDraftStore(path).append_turn(MemoryTurn(role="user", text="hello"))
    record = json.loads(path.read_text(encoding="utf-8"))
    assert count == 1
    assert record["role"] == "user"
    assert record["text"] == "hello"
    assert isinstance(record["created_at"], str)
    assert record["source"] == "chat_draft"
    assert record["safe"] is True


def test_recent_turns_are_chronological_and_bounded(tmp_path: Path) -> None:
    store = JsonlDraftStore(tmp_path / "turns.jsonl")
    store.append_turn(MemoryTurn(role="user", text="one"))
    store.append_turn(MemoryTurn(role="assistant", text="two"))
    store.append_turn(MemoryTurn(role="user", text="three"))
    assert store.list_recent(2) == [
        MemoryTurn(role="assistant", text="two"),
        MemoryTurn(role="user", text="three"),
    ]


def test_store_survives_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    JsonlDraftStore(path).append_turn(MemoryTurn(role="user", text="persist"))
    assert JsonlDraftStore(path).list_recent() == [
        MemoryTurn(role="user", text="persist")
    ]


def test_corrupt_and_non_chat_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    path.write_text(
        '{"role":"user","text":"kept"}\n'
        "not-json\n"
        '{"role":"system","text":"drop"}\n'
        '{"role":"assistant","text":"also kept"}\n',
        encoding="utf-8",
    )
    assert JsonlDraftStore(path).list_recent(10) == [
        MemoryTurn(role="user", text="kept"),
        MemoryTurn(role="assistant", text="also kept"),
    ]
