"""Synthetic owner-only checks for the opt-in Cold source window."""
import json
from pathlib import Path

import pytest

from core.cold_draft_store import ColdDraftStore


def _store(path, **kwargs):
    return ColdDraftStore(path, source_window_segments=kwargs.pop("segments", 3),
                          source_window_bytes=kwargs.pop("bytes", 100_000), **kwargs)


def _native(text, turn_id="native-turn", role="user"):
    return {"role": role, "text": text, "turn_id": turn_id,
            "created_at": "2026-09-16T12:00:00+00:00", "source_timezone": "Asia/Shanghai",
            "timezone_source": "client"}


def _ref(segment, index, start, end, **kwargs):
    return {"segment_id": segment, "turn_id": f"{segment}:turn:{index:04d}",
            "source_start": start, "source_end": end, **kwargs}


def test_default_construction_does_not_read_archive(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("default Cold should not initialize a source view")
    monkeypatch.setattr(ColdDraftStore, "_read_bytes", forbidden)
    owner = ColdDraftStore(tmp_path / "cold.jsonl")
    assert owner.source_window_status["safe_error_code"] == "cold_source_window_disabled"
    assert owner.read_source_refs([]).safe_error_code == "cold_source_window_disabled"


def test_exact_native_refs_remain_readable_after_consume_and_restart(tmp_path):
    path = tmp_path / "cold.jsonl"
    owner = _store(path)
    original = _native("alpha \u4e2d\u6587 omega", role="assistant")
    owner.append_segment([original], segment_id="native")
    assert owner.mark_consumed("native")
    refs = [{"segment_id": "native", "turn_id": "native-turn", "source_start": 6,
             "source_end": 8, "supporting_span": "\u4e2d\u6587", "source_role": "assistant",
             "source_timestamp": original["created_at"], "source_timezone": "Asia/Shanghai",
             "timezone_source": "client", "ingestion_version": "first-hit-v1"}]
    snapshot = path.read_bytes()
    first = owner.read_source_refs(refs)
    restarted = _store(path).read_source_refs(refs)
    assert first == restarted
    assert not first.truncated and first.safe_error_code is None
    assert first.evidence[0].text == "\u4e2d\u6587"
    assert first.evidence[0].provenance.ingestion_version == "first-hit-v1"
    assert first.evidence[0].provenance.source_role == "assistant"
    assert "LUMINA" in first.rendered_text
    assert path.read_bytes() == snapshot


def test_legacy_projection_matches_existing_converter_without_calling_it_on_consumed(tmp_path, monkeypatch):
    from Dream.cold_draft_digest import ColdDraftSegmentConverter
    owner = _store(tmp_path / "cold.jsonl")
    raw = owner.append_segment([{"role": "user", "text": "legacy source"}], segment_id="legacy")
    projected = ColdDraftSegmentConverter().convert(raw, "first-hit-v1")
    assert owner.mark_consumed("legacy")
    monkeypatch.setattr(ColdDraftSegmentConverter, "convert", lambda *args: pytest.fail("pending-only converter called"))
    result = owner.read_source_refs([_ref("legacy", 0, 0, 6)])
    source = result.evidence[0].provenance
    assert source.turn_id == projected.turns[0].turn_id
    assert source.source_timestamp == projected.turns[0].timestamp.isoformat()
    assert source.source_timezone == projected.turns[0].source_timezone
    assert source.timezone_source == projected.turns[0].timezone_source
    assert source.conversation_id == projected.conversation_id


def test_count_expiry_uses_append_order_not_consume_retry_or_cursor(tmp_path):
    path = tmp_path / "cold.jsonl"
    owner = _store(path, segments=2)
    for segment in ("old", "middle", "latest"):
        owner.append_segment([{"role": "user", "text": segment}], segment_id=segment)
    bytes_before = owner.source_window_status["bytes"]
    assert owner.mark_consumed("old")
    assert owner.mark_consumed("middle")
    owner.append_segment([{"role": "user", "text": "old"}], segment_id="old")
    assert owner.advance_pending_cursor("old")
    assert owner.source_window_status["bytes"] == bytes_before
    assert owner.source_window_status["segments"] == 2
    result = owner.read_source_refs([_ref("old", 0, 0, 3), _ref("middle", 0, 0, 6)])
    assert result.safe_error_code == "cold_source_partial"
    assert [item.text for item in result.evidence] == ["middle"]
    assert _store(path, segments=2).read_source_refs([_ref("old", 0, 0, 3)]).safe_error_code == "cold_source_unavailable"
    assert len(owner.list_all_turns()) == 3


def test_utf8_byte_bound_counts_immutable_payload_and_keeps_complete_suffix(tmp_path):
    path = tmp_path / "cold.jsonl"
    initial = _store(path)
    raw = initial.append_segment([{"role": "user", "text": "\u4e2d\u6587"}], segment_id="s")
    immutable = {key: value for key, value in raw.items() if key not in {"state", "consumed_at"}}
    charge = len(json.dumps(immutable, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    owner = _store(path, bytes=charge)
    assert owner.source_window_status["bytes"] == charge
    assert owner.read_source_refs([_ref("s", 0, 0, 2)]).evidence[0].text == "\u4e2d\u6587"
    assert _store(path, bytes=charge-1).source_window_status["segments"] == 0
    owner.append_segment([{"role": "user", "text": "x" * 1000}], segment_id="oversized")
    assert owner.source_window_status["segments"] == 0
    assert owner.source_window_status["bytes"] == 0
    assert len(owner.list_all_turns()) == 2


def test_repeated_query_and_lexical_search_never_read_archive(tmp_path, monkeypatch):
    path = tmp_path / "cold.jsonl"
    owner = _store(path)
    owner.append_segment([{"role": "user", "text": "this unformed conversation mentions zebras"}], segment_id="s")
    snapshot = path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("query read archive")
    monkeypatch.setattr(owner, "_read_bytes", forbidden)
    monkeypatch.setattr(owner, "list_all_turns", forbidden)
    monkeypatch.setattr(owner, "_physical_lines", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    for _ in range(3):
        result = owner.read_source_refs([_ref("s", 0, 5, 13)])
        assert result.evidence[0].text == "unformed"
        lexical = owner.search_recent_sources("zebras")
        assert "zebras" in lexical.rendered_text
    monkeypatch.undo()
    assert path.read_bytes() == snapshot


@pytest.mark.parametrize("change", ["replace", "append", "remove"])
def test_external_archive_change_invalidates_without_query_rebuild(tmp_path, monkeypatch, change):
    path = tmp_path / "cold.jsonl"
    owner = _store(path)
    owner.append_segment([{"role": "user", "text": "original"}], segment_id="s")
    if change == "replace":
        other = tmp_path / "other.jsonl"
        other.write_bytes(path.read_bytes().replace(b"original", b"modified"))
        other.replace(path)
    elif change == "append":
        with path.open("ab") as stream:
            stream.write(b"external bytes\n")
    else:
        path.unlink()
    monkeypatch.setattr(owner, "_read_bytes", lambda: pytest.fail("query rebuilt archive"))
    assert owner.read_source_refs([_ref("s", 0, 0, 8)]).safe_error_code == "cold_source_window_stale"
    assert owner.search_recent_sources("original").safe_error_code == "cold_source_window_stale"
    assert owner.source_window_status["segments"] == 0


def test_failed_atomic_append_preserves_source_window(tmp_path, monkeypatch):
    owner = _store(tmp_path / "cold.jsonl")
    owner.append_segment([{"role": "user", "text": "first"}], segment_id="s")
    def fail(_):
        raise OSError("synthetic write failure")
    monkeypatch.setattr(owner, "_replace_bytes", fail)
    with pytest.raises(OSError):
        owner.append_segment([{"role": "user", "text": "second"}], segment_id="new")
    assert owner.read_source_refs([_ref("s", 0, 0, 5)]).evidence[0].text == "first"
    assert owner.read_source_refs([_ref("new", 0, 0, 6)]).safe_error_code == "cold_source_unavailable"


def test_overlap_deduplicates_without_filling_unread_gaps_and_retains_source_order(tmp_path):
    owner = _store(tmp_path / "cold.jsonl")
    owner.append_segment([{"role": "user", "text": "0123456789abcdef"}], segment_id="s")
    result = owner.read_source_refs([_ref("s", 0, 12, 15), _ref("s", 0, 2, 5),
                                     _ref("s", 0, 1, 3), _ref("s", 0, 1, 3)])
    assert [(item.source_start, item.source_end, item.text) for item in result.evidence] == [
        (1, 5, "1234"), (12, 15, "cde")]
    assert "56789ab" not in result.rendered_text
    assert not result.truncated


def test_bounded_neighbors_keep_native_roles_times_and_segment_boundary(tmp_path):
    owner = _store(tmp_path / "cold.jsonl")
    owner.append_segment([_native("before", "t0"), _native("exact anchor", "t1", "assistant"),
                          _native("after", "t2")], segment_id="s")
    owner.append_segment([_native("other segment", "other")], segment_id="other")
    result = owner.read_source_refs([{"segment_id": "s", "turn_id": "t1", "source_start": 6,
                                      "source_end": 12}], before=1, after=10)
    assert [item.text for item in result.evidence] == ["before", "anchor", "after"]
    assert [item.provenance.source_role for item in result.evidence] == ["user", "assistant", "user"]
    assert all(item.provenance.source_timestamp == "2026-09-16T12:00:00+00:00" for item in result.evidence)


@pytest.mark.parametrize("field,value", [
    ("supporting_span", "invented"), ("source_role", "assistant"),
    ("source_timestamp", "2025-09-16T12:00:00+00:00"), ("source_timezone", "UTC"),
    ("timezone_source", "configured_default"), ("conversation_id", "other-session"),
    ("source_start", True), ("source_end", 999), ("turn_id", "missing"),
])
def test_source_ref_mismatch_is_rejected(tmp_path, field, value):
    owner = _store(tmp_path / "cold.jsonl")
    owner.append_segment([_native("source")], segment_id="s")
    ref = {"segment_id": "s", "turn_id": "native-turn", "source_start": 0,
           "source_end": 6, field: value}
    result = owner.read_source_refs([ref])
    assert not result.evidence
    assert result.truncated and result.safe_error_code == "cold_source_unavailable"


def test_chars_utf8_bytes_items_and_ref_budget_include_headers(tmp_path):
    owner = _store(tmp_path / "cold.jsonl")
    owner.append_segment([{"role": "user", "text": "\u4e2d\u6587\u7b2c\u4e00"}, {"role": "assistant", "text": "second"}], segment_id="s")
    refs = [_ref("s", 0, 0, 4), _ref("s", 1, 0, 6)]
    whole = owner.read_source_refs(refs)
    first = owner.read_source_refs(refs[:1])
    exact = owner.read_source_refs(refs, max_chars=len(first.rendered_text))
    assert exact.evidence == first.evidence and exact.safe_error_code == "cold_source_partial"
    assert owner.read_source_refs(refs, max_items=1).evidence == first.evidence
    assert owner.read_source_refs(refs, max_refs=1).evidence == first.evidence
    assert not owner.read_source_refs(refs[:1], max_bytes=len(first.rendered_text.encode("utf-8"))-1).evidence
    assert len(whole.rendered_text) <= 8000
    assert len(whole.rendered_text.encode("utf-8")) <= 32768


def test_no_fact_lexical_search_reads_long_turn_tail_and_chinese(tmp_path):
    owner = _store(tmp_path / "cold.jsonl")
    text = "padding " * 300 + "\u6591\u9a6c\u5728\u6cb3\u8fb9\u559d\u6c34 unusualzebra"
    owner.append_segment([{"role": "assistant", "text": text}], segment_id="no-fact")
    for query in ("\u6cb3\u8fb9\u559d\u6c34", "unusualzebra"):
        result = owner.search_recent_sources(query, snippet_chars=80)
        assert result.safe_error_code is None
        assert query in result.rendered_text
        item = result.evidence[0]
        assert item.text == text[item.source_start:item.source_end]
        assert item.source_start > 2000
        assert item.provenance.source_role == "assistant"


def test_duplicate_native_turn_id_cannot_be_dereferenced(tmp_path):
    owner = _store(tmp_path / "cold.jsonl")
    owner.append_segment([_native("one"), _native("two")], segment_id="s")
    result = owner.read_source_refs([{"segment_id": "s", "turn_id": "native-turn",
                                      "source_start": 0, "source_end": 3}])
    assert result.safe_error_code == "cold_source_unavailable"
    assert not result.evidence


def test_corrupt_segment_is_excluded_on_restart_without_rewriting_bytes(tmp_path):
    path = tmp_path / "cold.jsonl"
    owner = _store(path)
    owner.append_segment([{"role": "user", "text": "original"}], segment_id="s")
    with path.open("ab") as stream:
        stream.write(path.read_bytes())
    original = path.read_bytes()
    owner = _store(path)
    assert owner.read_source_refs([_ref("s", 0, 0, 8)]).safe_error_code == "cold_source_unavailable"
    assert path.read_bytes() == original


def test_optional_neighbors_do_not_displace_exact_support_under_budget(tmp_path):
    owner = _store(tmp_path / "cold.jsonl")
    owner.append_segment([{"role": "user", "text": "earlier context"},
                          {"role": "assistant", "text": "the cited support"}], segment_id="s")
    refs = [_ref("s", 1, 10, 17)]
    exact = owner.read_source_refs(refs)
    with_context = owner.read_source_refs(refs, before=1, max_chars=len(exact.rendered_text))
    assert with_context.evidence == exact.evidence
    assert with_context.safe_error_code == "cold_source_partial"



def _replace_original_with_modified(path):
    # A separate inode makes the external signature change deterministic even
    # when Windows reports the same timestamp for rapid same-length writes.
    original_identity = (path.stat().st_dev, path.stat().st_ino)
    replacement = path.with_name(path.name + ".replacement")
    replacement.write_bytes(path.read_bytes().replace(b"original", b"modified"))
    replacement.replace(path)
    assert (path.stat().st_dev, path.stat().st_ino) != original_identity


def test_snapshot_changed_during_initialization_is_not_marked_current(tmp_path, monkeypatch):
    path = tmp_path / "cold.jsonl"
    ColdDraftStore(path).append_segment([{"role": "user", "text": "original"}], segment_id="s")
    features = ColdDraftStore._source_features
    changed = False
    def change_during_index(text):
        nonlocal changed
        if not changed:
            changed = True
            _replace_original_with_modified(path)
        return features(text)
    monkeypatch.setattr(ColdDraftStore, "_source_features", staticmethod(change_during_index))
    owner = _store(path)
    assert owner.read_source_refs([_ref("s", 0, 0, 8)]).safe_error_code == "cold_source_window_stale"
    assert owner.source_window_status["segments"] == 0


def test_archive_changed_while_references_are_consumed_is_rejected(tmp_path):
    path = tmp_path / "cold.jsonl"
    owner = _store(path)
    owner.append_segment([{"role": "user", "text": "original"}], segment_id="s")
    def references():
        _replace_original_with_modified(path)
        yield _ref("s", 0, 0, 8)
    result = owner.read_source_refs(references())
    assert result.safe_error_code == "cold_source_window_stale"
    assert not result.evidence and result.rendered_text == ""


def test_unchanged_metadata_signature_needs_restart_after_out_of_band_edit(tmp_path, monkeypatch):
    path = tmp_path / "cold.jsonl"
    owner = _store(path)
    owner.append_segment([{"role": "user", "text": "original"}], segment_id="s")
    unchanged_stamp = owner._source_file_stamp()
    # Model a filesystem reporting identical identity/size/timestamps despite
    # an out-of-band same-length edit. Queries do not fingerprint the archive.
    monkeypatch.setattr(owner, "_source_file_stamp", lambda: unchanged_stamp)
    path.write_bytes(path.read_bytes().replace(b"original", b"modified"))
    ref = _ref("s", 0, 0, 8)
    cached = owner.read_source_refs([ref])
    assert cached.safe_error_code is None
    assert cached.evidence[0].text == "original"
    restarted = _store(path).read_source_refs([ref])
    assert restarted.safe_error_code is None
    assert restarted.evidence[0].text == "modified"
