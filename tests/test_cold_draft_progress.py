"""Frozen mechanical progress cases using the real Cold owner and Dream runner."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

import core.cold_draft_store as cold_module
from core.cold_draft_store import ColdDraftStore, PendingCount
from Dream.cold_draft_digest import ColdDraftDigestionTask
from Dream.models import DreamRunPolicy
from Dream.runner import DreamRunner
from Conversation_Memory.adapter.models import IngestionResult


CURSOR_TYPE = "dream_selection_cursor"
BAD_IDS = tuple(f"blocked-{index:02d}" for index in range(10))
HEALTHY_ID = "healthy-eleventh"
SOURCE_ORDER = (("z-last", "岑雨"), ("a-first", "Maple"), ("m-middle", "Ember"), ("tail", "Juniper"))


def _append(owner, segment_id, text):
    return owner.append_segment([{
        "turn_id": f"{segment_id}:u", "role": "user", "text": text,
        "created_at": "2026-09-13T00:00:00Z", "source_timezone": "UTC",
        "timezone_source": "client",
    }, {
        "turn_id": f"{segment_id}:a", "role": "assistant", "text": "Recorded for this controlled test.",
        "created_at": "2026-09-13T00:00:01Z", "source_timezone": "UTC",
        "timezone_source": "client",
    }], segment_id=segment_id)


def seed_blocked_prefix(path):
    owner = ColdDraftStore(path)
    for index, segment_id in enumerate((*BAD_IDS, HEALTHY_ID)):
        _append(owner, segment_id, f"Specimen-{index:02d} has a recorded observation.")
    return owner


class ControlledIngestor:
    def __init__(self):
        self.attempted_ids = []

    def ingest(self, segment):
        self.attempted_ids.append(segment.segment_id)
        if segment.segment_id in BAD_IDS:
            return IngestionResult(
                segment.segment_id, "grounded-span-v2", "failed", retryable=False,
                safe_error_code="formation_processing_incomplete",
            )
        return IngestionResult(segment.segment_id, "grounded-span-v2", "completed", ("controlled-evidence",))


class ControlledProvider:
    def __init__(self, ingestor):
        self.ingestor = ingestor

    def get(self, ingestion_version):
        assert ingestion_version == "grounded-span-v2"
        return self.ingestor


def controlled_run_once(path):
    """Construct a fresh owner, ingestor, task and real runner for one run."""
    owner = ColdDraftStore(path)
    ingestor = ControlledIngestor()
    report = DreamRunner(owner, ColdDraftDigestionTask(owner, ControlledProvider(ingestor))).run_once(DreamRunPolicy())
    return {
        "report": asdict(report), "attempted_ids": ingestor.attempted_ids,
        "pending_ids": [item["segment_id"] for item in owner.list_pending()],
    }


def _ids(records):
    return [item["segment_id"] for item in records]


def _cursor_records(path):
    records = []
    for line in path.read_bytes().splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(value, dict) and value.get("record_type") == CURSOR_TYPE:
            records.append(value)
    return records


@pytest.mark.parametrize("restart_process", [False, True])
def test_second_explicit_run_reaches_healthy_eleventh_after_restart(tmp_path, restart_process):
    path = tmp_path / "cold.jsonl"
    seed_blocked_prefix(path)
    original_bad_lines = [
        line for line in path.read_bytes().splitlines(keepends=True)
        if json.loads(line)["segment_id"] in BAD_IDS
    ]
    first = controlled_run_once(path)
    if restart_process:
        code = (
            "import json,runpy,sys; "
            "module=runpy.run_path(sys.argv[1]); "
            "print(json.dumps(module['controlled_run_once'](sys.argv[2])))"
        )
        completed = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", "-c", code, __file__, str(path)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            encoding="utf-8", check=True,
        )
        second = json.loads(completed.stdout)
    else:
        second = controlled_run_once(path)

    assert first["attempted_ids"] == list(BAD_IDS)
    assert HEALTHY_ID in second["attempted_ids"]
    assert all(item["report"]["attempted"] <= 10 for item in (first, second))
    assert all(len(item["attempted_ids"]) == len(set(item["attempted_ids"])) for item in (first, second))
    assert set(second["pending_ids"]) == set(BAD_IDS)
    final_lines = path.read_bytes().splitlines(keepends=True)
    assert all(line in final_lines for line in original_bad_lines)


@pytest.mark.parametrize("order", [SOURCE_ORDER, tuple(reversed(SOURCE_ORDER))])
def test_owner_page_follows_file_order_and_preserves_legacy_prefix(tmp_path, order):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    for segment_id, name in order:
        _append(owner, segment_id, f"{name} studies ceramics.")
    before = path.read_bytes()
    assert _ids(owner.list_pending_page(2)) == [item[0] for item in order[:2]]
    assert path.read_bytes() == before
    assert owner.advance_pending_cursor(order[1][0]) is True
    restarted = ColdDraftStore(path)
    assert _ids(restarted.list_pending_page(2)) == [item[0] for item in order[2:]]
    assert _ids(restarted.list_pending(2)) == [item[0] for item in order[:2]]
    assert restarted.count_pending_bounded(10) == PendingCount(4, False)


def test_consumed_cursor_anchors_all_segments_and_wraps_only_once(tmp_path):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    for segment_id, name in SOURCE_ORDER:
        _append(owner, segment_id, name)
    assert owner.mark_consumed("a-first") is True
    assert owner.advance_pending_cursor("a-first") is True
    assert _ids(ColdDraftStore(path).list_pending_page(10)) == ["m-middle", "tail", "z-last"]
    assert owner.advance_pending_cursor("tail") is True
    assert _ids(ColdDraftStore(path).list_pending_page(10)) == ["z-last", "m-middle", "tail"]
    for segment_id in ("z-last", "m-middle", "tail"):
        assert owner.mark_consumed(segment_id) is True
    assert ColdDraftStore(path).list_pending_page(10) == []


def test_single_cursor_updates_preserve_source_and_foreign_raw_lines(tmp_path):
    path = tmp_path / "cold.jsonl"
    foreign = b'  {"record_type":"foreign_metadata","value":7}\r\nnot-json  \r\n'
    path.write_bytes(foreign)
    owner = ColdDraftStore(path)
    _append(owner, "z-last", "岑雨研究陶瓷。")
    _append(owner, "a-first", "Maple studies glass.")
    original = path.read_bytes()
    assert owner.advance_pending_cursor("z-last") is True
    assert path.read_bytes().startswith(original)
    _append(owner, "m-middle", "Ember studies copper.")
    before = path.read_bytes().splitlines(keepends=True)
    old_cursor_lines = [line for line in before if b'"record_type":"dream_selection_cursor"' in line]
    assert owner.advance_pending_cursor("a-first") is True
    expected_source = b"".join(line for line in before if line not in old_cursor_lines)
    assert path.read_bytes().startswith(expected_source)
    assert _cursor_records(path) == [{
        "record_type": CURSOR_TYPE, "schema_version": 1, "after_segment_id": "a-first",
    }]
    assert _ids(ColdDraftStore(path).list_pending_page(3)) == ["m-middle", "z-last", "a-first"]
    assert owner.count_pending_bounded(10) == PendingCount(3, False)
    assert len(owner.list_all_turns()) == 6


def test_owner_page_uses_one_file_snapshot_and_returns_detached_data(tmp_path, monkeypatch):
    path = tmp_path / "cold.jsonl"
    owner = seed_blocked_prefix(path)
    assert owner.advance_pending_cursor(BAD_IDS[-1]) is True
    original_read = owner._read_bytes
    reads = []
    def tracked_read():
        reads.append(True)
        return original_read()
    monkeypatch.setattr(owner, "_read_bytes", tracked_read)
    page = owner.list_pending_page(2)
    assert reads == [True]
    assert _ids(page) == [HEALTHY_ID, BAD_IDS[0]]
    page[0]["turns"][0]["text"] = "mutated returned projection"
    assert "mutated returned projection" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("limit", [0, -1, True, "2", None])
def test_owner_page_rejects_invalid_bounds(tmp_path, limit):
    with pytest.raises(ValueError):
        ColdDraftStore(tmp_path / "cold.jsonl").list_pending_page(limit)


def test_page_read_error_is_visible_but_legacy_read_semantics_are_unchanged(tmp_path, monkeypatch):
    owner = ColdDraftStore(tmp_path / "cold.jsonl")
    def unreadable():
        raise OSError("synthetic read error")
    monkeypatch.setattr(owner, "_read_bytes", unreadable)
    with pytest.raises(OSError):
        owner.list_pending_page(10)
    assert owner.list_pending(10) == []
    assert owner.advance_pending_cursor("known") is False


@pytest.mark.parametrize("failure", ["replace", "fsync"])
def test_cursor_write_failure_preserves_all_bytes_and_cleans_temporary_file(tmp_path, monkeypatch, failure):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    _append(owner, "known", "Aster grows moss.")
    before = path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("synthetic cursor write failure")
    monkeypatch.setattr(cold_module.os, failure, fail)
    assert owner.advance_pending_cursor("known") is False
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["cold.jsonl"]


@pytest.mark.parametrize("corruption", ["unknown_target", "duplicate", "wrong_version", "extra_field"])
def test_corrupt_cursor_cannot_reset_progress_or_rewrite_source(tmp_path, corruption):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    _append(owner, "known", "Aster grows moss.")
    cursor = {"record_type": CURSOR_TYPE, "schema_version": 1, "after_segment_id": "known"}
    if corruption == "unknown_target":
        cursor["after_segment_id"] = "absent"
    elif corruption == "wrong_version":
        cursor["schema_version"] = 2
    elif corruption == "extra_field":
        cursor["segment_id"] = "not-a-source-segment"
    encoded = json.dumps(cursor).encode("utf-8") + b"\n"
    path.write_bytes(path.read_bytes() + encoded * (2 if corruption == "duplicate" else 1))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        owner.list_pending_page(10)
    assert owner.advance_pending_cursor("known") is False
    assert path.read_bytes() == before
    assert _ids(owner.list_pending()) == ["known"]


def test_cursor_does_not_advance_to_an_unknown_segment(tmp_path):
    path = tmp_path / "cold.jsonl"
    owner = ColdDraftStore(path)
    _append(owner, "known", "Aster grows moss.")
    before = path.read_bytes()
    assert owner.advance_pending_cursor("missing") is False
    assert path.read_bytes() == before
