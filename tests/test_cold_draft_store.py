import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import core.cold_draft_store as cold_store_module
from core.cold_draft_store import ColdDraftStore, PendingCount
from core.contracts import MemoryTurn


def _turn(role: str, text: str) -> dict[str, str]:
    return {"role": role, "text": text}


def _native_turn(index: int) -> dict[str, str]:
    turn = MemoryTurn(
        turn_id=f"turn-{index}",
        role="user" if index % 2 == 0 else "assistant",
        text=f"body-{index}",
        created_at=datetime(2026, 7, 14, tzinfo=UTC)
        + timedelta(minutes=index),
        source_timezone="Asia/Shanghai",
        timezone_source="client",
    )
    return turn.storage_turn()


def _records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_append_writes_one_line_per_turn_and_restart_reconstructs_segment(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cold" / "segments.jsonl"
    turns = [_native_turn(index) for index in range(14)]
    segment = ColdDraftStore(path).append_segment(
        turns,
        segment_id="stable-segment",
    )

    records = _records(path)
    assert len(records) == 14
    assert {record["record_type"] for record in records} == {"cold_turn"}
    assert {record["segment_id"] for record in records} == {"stable-segment"}
    assert [record["segment_turn_index"] for record in records] == list(range(14))
    assert {record["segment_turn_count"] for record in records} == {14}
    assert [record["role"] for record in records] == [
        turn["role"] for turn in turns
    ]
    assert [record["text"] for record in records] == [
        turn["text"] for turn in turns
    ]
    assert [
        {
            key: record[key]
            for key in (
                "turn_id",
                "role",
                "text",
                "created_at",
                "source_timezone",
                "timezone_source",
            )
        }
        for record in records
    ] == turns
    assert all("turns" not in record for record in records)

    restarted = ColdDraftStore(path)
    assert restarted.list_pending() == [segment]
    assert restarted.list_pending()[0]["turns"] == turns
    assert restarted.count_pending_bounded(10) == PendingCount(1, False)


def test_two_fourteen_turn_segments_are_28_lines_but_count_as_two(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    first = [_native_turn(index) for index in range(14)]
    second = [_native_turn(index + 14) for index in range(14)]

    store.append_segment(first, segment_id="first")
    store.append_segment(second, segment_id="second")

    assert len(path.read_text(encoding="utf-8").splitlines()) == 28
    assert [record["segment_id"] for record in store.list_pending()] == [
        "first",
        "second",
    ]
    assert store.count_pending_bounded(10) == PendingCount(2, False)


def test_mark_consumed_updates_every_segment_line_and_survives_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    first = store.append_segment(
        [_native_turn(index) for index in range(4)],
        segment_id="first",
    )
    store.append_segment(
        [_native_turn(index + 4) for index in range(2)],
        segment_id="second",
    )

    assert store.mark_consumed(first["segment_id"]) is True
    records = _records(path)
    first_lines = [record for record in records if record["segment_id"] == "first"]
    second_lines = [
        record for record in records if record["segment_id"] == "second"
    ]
    assert {record["state"] for record in first_lines} == {"consumed"}
    assert len({record["consumed_at"] for record in first_lines}) == 1
    assert {record["state"] for record in second_lines} == {"pending_digest"}
    assert all("consumed_at" not in record for record in second_lines)
    assert ColdDraftStore(path).list_pending()[0]["segment_id"] == "second"


def test_mark_consumed_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    segment = store.append_segment([_turn("user", "one")])
    assert store.mark_consumed(segment["segment_id"]) is True
    first = path.read_bytes()
    assert store.mark_consumed(segment["segment_id"]) is True
    assert path.read_bytes() == first


def test_explicit_segment_id_is_idempotent_even_after_consumed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    turns = [_turn("user", "one"), _turn("assistant", "two")]
    first = store.append_segment(turns, segment_id="stable-segment")
    before = path.read_bytes()
    assert store.append_segment(turns, segment_id="stable-segment") == first
    assert path.read_bytes() == before

    assert store.mark_consumed("stable-segment")
    consumed_bytes = path.read_bytes()
    existing = store.append_segment(turns, segment_id="stable-segment")
    assert existing["state"] == "consumed"
    assert path.read_bytes() == consumed_bytes


def test_segment_id_conflict_is_rejected_without_echoing_content(
    tmp_path: Path,
) -> None:
    store = ColdDraftStore(tmp_path / "cold.jsonl")
    store.append_segment([_turn("user", "first")], segment_id="same")
    with pytest.raises(ValueError, match="cold draft segment conflict") as exc_info:
        store.append_segment([_turn("user", "private second")], segment_id="same")
    assert "private second" not in str(exc_info.value)


def test_corrupt_and_legacy_nested_lines_are_not_read_or_rewritten(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cold.jsonl"
    original = (
        b"not-json\n"
        b"{\"segment_id\":\"legacy\",\"turns\":[{\"role\":\"user\","
        b"\"text\":\"kept\"}],\"state\":\"pending_digest\"}\n"
    )
    path.write_bytes(original)

    store = ColdDraftStore(path)
    assert store.list_pending() == []
    assert store.count_pending_bounded(10) == PendingCount(0, False)
    assert store.mark_consumed("legacy") is False
    assert path.read_bytes() == original


def test_invalid_turns_fail_safely(tmp_path: Path) -> None:
    store = ColdDraftStore(tmp_path / "cold.jsonl")
    with pytest.raises(ValueError, match="invalid cold draft turns"):
        store.append_segment([{"role": "system", "text": "private"}])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda records: records.pop(),
        lambda records: records.append(dict(records[0])),
        lambda records: records[1].__setitem__("segment_turn_count", 3),
        lambda records: records[1].__setitem__("segment_turn_index", 0),
        lambda records: records[1].__setitem__("state", "consumed"),
        lambda records: records[1].__setitem__("source", "conflict"),
        lambda records: records[1].__setitem__(
            "segment_created_at", "2026-01-01T00:00:00Z"
        ),
        lambda records: records[0].__setitem__("schema_version", True),
        lambda records: records[0].__setitem__("segment_id", "   "),
        lambda records: records[0].__setitem__("source", "   "),
        lambda records: records[0].__setitem__(
            "segment_created_at", "2026-01-01T00:00:00"
        ),
        lambda records: records[0].__setitem__(
            "consumed_at", "2026-01-01T00:00:00Z"
        ),
    ],
    ids=[
        "missing-index",
        "duplicate-line",
        "count-conflict",
        "duplicate-index",
        "state-conflict",
        "source-conflict",
        "created-at-conflict",
        "boolean-schema",
        "blank-segment-id",
        "blank-source",
        "naive-segment-created-at",
        "pending-with-consumed-at",
    ],
)
def test_incomplete_or_conflicting_segment_is_ignored_and_not_consumed(
    tmp_path: Path,
    mutate,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    store.append_segment(
        [_turn("user", "one"), _turn("assistant", "two")],
        segment_id="broken",
    )
    records = _records(path)
    mutate(records)
    path.write_text(
        "".join(
            json.dumps(record, separators=(",", ":")) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()

    assert store.list_pending() == []
    assert store.count_pending_bounded(10) == PendingCount(0, False)
    assert store.mark_consumed("broken") is False
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="cold draft segment conflict"):
        store.append_segment(
            [_turn("user", "one"), _turn("assistant", "two")],
            segment_id="broken",
        )


def test_append_replace_failure_leaves_old_bytes_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cold.jsonl"
    original = b"unrelated malformed bytes\n"
    path.write_bytes(original)

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(cold_store_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        ColdDraftStore(path).append_segment(
            [_turn("user", "one")],
            segment_id="new",
        )
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".cold.jsonl.*.tmp")) == []


def test_consumed_replace_failure_leaves_old_bytes_and_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    store.append_segment(
        [_turn("user", "one"), _turn("assistant", "two")],
        segment_id="pending",
    )
    before = path.read_bytes()

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(cold_store_module.os, "replace", fail_replace)
    assert store.mark_consumed("pending") is False
    assert path.read_bytes() == before
    assert store.list_pending()[0]["segment_id"] == "pending"
    assert list(tmp_path.glob(".cold.jsonl.*.tmp")) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda records: [
            record.__setitem__("consumed_at", "2026-01-01T00:00:00")
            for record in records
        ],
        lambda records: records[1].__setitem__(
            "consumed_at", "2026-01-02T00:00:00Z"
        ),
    ],
    ids=["naive-consumed-at", "conflicting-consumed-at"],
)
def test_invalid_consumed_timestamp_segment_is_not_accepted(
    tmp_path: Path,
    mutate,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    turns = [_turn("user", "one"), _turn("assistant", "two")]
    store.append_segment(turns, segment_id="consumed")
    assert store.mark_consumed("consumed")
    records = _records(path)
    mutate(records)
    path.write_text(
        "".join(
            json.dumps(record, separators=(",", ":")) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()

    assert store.mark_consumed("consumed") is False
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="cold draft segment conflict"):
        store.append_segment(turns, segment_id="consumed")


def test_append_fsync_failure_leaves_old_bytes_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cold.jsonl"
    original = b"unrelated bytes\n"
    path.write_bytes(original)

    def fail_fsync(file_descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(cold_store_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="fsync failed"):
        ColdDraftStore(path).append_segment(
            [_turn("user", "one")],
            segment_id="new",
        )
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".cold.jsonl.*.tmp")) == []


def test_mark_consumed_preserves_unrelated_raw_lines_byte_for_byte(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    store.append_segment([_turn("user", "first")], segment_id="first")
    with path.open("ab") as file:
        file.write(b"not-json with deliberate spacing  \r\n")
    store.append_segment([_turn("user", "second")], segment_id="second")
    malformed = b"not-json with deliberate spacing  \r\n"

    assert store.mark_consumed("first")
    assert malformed in path.read_bytes()
    assert [record["segment_id"] for record in store.list_pending()] == ["second"]


def test_mark_consumed_preserves_bare_cr_separator_and_unrelated_line(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    store.append_segment([_turn("user", "first")], segment_id="first")
    target = path.read_bytes().rstrip(b"\n")
    unrelated = b"unrelated raw line with spacing  \n"
    path.write_bytes(target + b"\r" + unrelated)

    assert store.mark_consumed("first")
    rewritten = path.read_bytes()
    assert b"\r" + unrelated in rewritten
    assert rewritten.splitlines(keepends=True)[1] == unrelated


@pytest.mark.parametrize(
    ("pending_count", "limit", "expected"),
    [
        (0, 3, PendingCount(0, False)),
        (2, 3, PendingCount(2, False)),
        (2, 2, PendingCount(2, True)),
        (3, 2, PendingCount(2, True)),
    ],
)
def test_count_pending_bounded_semantics(
    tmp_path: Path,
    pending_count: int,
    limit: int,
    expected: PendingCount,
) -> None:
    store = ColdDraftStore(tmp_path / "cold.jsonl")
    for index in range(pending_count):
        store.append_segment(
            [_turn("user", f"private body {index}")],
            segment_id=f"pending-{index}",
        )
    result = store.count_pending_bounded(limit)
    assert result == expected
    assert not hasattr(result, "turns")
    assert "private body" not in repr(result)


def test_count_pending_bounded_marks_read_failure_as_inexact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cold.jsonl"
    path.touch()

    original_open = Path.open

    def failing_open(candidate: Path, *args, **kwargs):
        if candidate == path:
            raise OSError("private read failure")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    assert ColdDraftStore(path).count_pending_bounded(10) == PendingCount(0, True)


@pytest.mark.parametrize("invalid_first", [True, False])
def test_count_pending_rejects_invalid_same_id_before_or_after_valid_group(
    tmp_path: Path,
    invalid_first: bool,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    store.append_segment(
        [_turn("user", "private body")],
        segment_id="candidate",
    )
    valid = _records(path)[0]
    invalid = dict(valid)
    invalid["schema_version"] = True
    ordered = [invalid, valid] if invalid_first else [valid, invalid]
    path.write_text(
        "".join(
            json.dumps(record, separators=(",", ":")) + "\n"
            for record in ordered
        ),
        encoding="utf-8",
    )

    assert store.list_pending() == []
    result = store.count_pending_bounded(10)
    assert result == PendingCount(0, False)
    assert "private body" not in repr(result)


def test_count_pending_bounded_stops_streaming_at_complete_segment_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cold.jsonl"
    store = ColdDraftStore(path)
    store.append_segment(
        [_turn("user", "private body one")],
        segment_id="one",
    )
    store.append_segment(
        [_turn("assistant", "private body two")],
        segment_id="two",
    )
    lines = path.read_bytes().splitlines(keepends=True)

    class GuardedReader:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            yield from lines
            raise AssertionError("stream read continued beyond the segment limit")

    original_open = Path.open

    def guarded_open(candidate: Path, *args, **kwargs):
        if candidate == path:
            return GuardedReader()
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    result = ColdDraftStore(path).count_pending_bounded(2)
    assert result == PendingCount(2, True)
    assert "private body" not in repr(result)
    assert not hasattr(result, "turns")
