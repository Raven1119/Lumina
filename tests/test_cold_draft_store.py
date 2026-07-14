import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.cold_draft_store import ColdDraftStore
from core.contracts import MemoryTurn


def _turn(role: str, text: str) -> dict[str, str]:
    return {"role": role, "text": text}


def test_append_and_restart_pending_segment(tmp_path: Path) -> None:
    path = tmp_path / "cold" / "segments.jsonl"
    first = ColdDraftStore(path)
    segment = first.append_segment(
        [_turn("user", "hello"), _turn("assistant", "hi")]
    )
    assert set(segment) == {
        "schema_version", "segment_id", "turns", "created_at", "source", "state"
    }
    assert segment["schema_version"] == 1
    assert segment["state"] == "pending_digest"
    assert segment["source"] == "hot_draft_precompression"

    second = ColdDraftStore(path)
    assert second.list_pending() == [segment]


def test_mark_consumed_records_time_and_removes_from_pending(tmp_path: Path) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    segment = store.append_segment([_turn("user", "one")])
    assert store.mark_consumed(segment["segment_id"]) is True
    assert store.list_pending() == []
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["state"] == "consumed"
    assert isinstance(record["consumed_at"], str)


def test_mark_consumed_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    segment = store.append_segment([_turn("user", "one")])
    assert store.mark_consumed(segment["segment_id"]) is True
    first = path.read_text(encoding="utf-8")
    assert store.mark_consumed(segment["segment_id"]) is True
    assert path.read_text(encoding="utf-8") == first


def test_explicit_segment_id_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    turns = [_turn("user", "one"), _turn("assistant", "two")]
    first = store.append_segment(turns, segment_id="stable-segment")
    second = store.append_segment(turns, segment_id="stable-segment")
    assert second == first
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_segment_id_conflict_is_rejected_without_echoing_content(tmp_path: Path) -> None:
    store = ColdDraftStore(tmp_path / "cold.jsonl")
    store.append_segment([_turn("user", "first")], segment_id="same")
    with pytest.raises(ValueError, match="cold draft segment conflict") as exc_info:
        store.append_segment([_turn("user", "private second")], segment_id="same")
    assert "private second" not in str(exc_info.value)


def test_corrupt_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "cold.jsonl"
    path.write_text("not-json\n{}\n", encoding="utf-8")
    assert ColdDraftStore(path).list_pending() == []


def test_legacy_pending_record_is_read_without_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "cold.jsonl"
    legacy = (
        '{"segment_id":"legacy","turns":[{"role":"user","text":"kept"}],'
        '"created_at":"2026-07-14T10:00:00+08:00",'
        '"source":"hot_draft_precompression","state":"pending_digest"}\n'
    ).encode()
    path.write_bytes(legacy)
    assert ColdDraftStore(path).list_pending()[0]["segment_id"] == "legacy"
    assert path.read_bytes() == legacy


def test_invalid_turns_fail_safely(tmp_path: Path) -> None:
    store = ColdDraftStore(tmp_path / "cold.jsonl")
    with pytest.raises(ValueError, match="invalid cold draft turns"):
        store.append_segment([{"role": "system", "text": "private"}])


def test_v2_segment_preserves_complete_turn_fields(tmp_path: Path) -> None:
    turn = MemoryTurn(
        turn_id="opaque-1",
        role="user",
        text="yesterday",
        created_at=datetime(2026, 7, 14, 2, tzinfo=UTC),
        source_timezone="Asia/Shanghai",
        timezone_source="client",
    )
    segment = ColdDraftStore(tmp_path / "cold.jsonl").append_segment(
        [turn.storage_turn()]
    )
    assert segment["schema_version"] == 2
    assert segment["turns"] == [turn.storage_turn()]
