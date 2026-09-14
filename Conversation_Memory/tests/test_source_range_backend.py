"""Literal source-range contracts using the existing graph stub and real FAISS."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta, timezone

import faiss
import numpy as np
import pytest

from Conversation_Memory.adapter import _source_backend as source
from Conversation_Memory.adapter.backend import RealMagmaBackend
from Conversation_Memory.adapter.source_memory import _source_rows
from Conversation_Memory.tests.test_source_backend_views import (
    add_segment, backend, restart, segment,
)


def test_long_partial_range_preserves_unicode_roles_timestamps_and_source():
    raw = "起点🙂  α\n" * 85 + "The final observation remains provisional."
    seg = segment(texts=[("user", raw), ("assistant", "A suggestion, not confirmation.")])
    seg = replace(seg, turns=tuple(replace(
        turn, timestamp=turn.timestamp.astimezone(timezone(timedelta(hours=8))),
        source_timezone="Asia/Shanghai",
    ) for turn in seg.turns))
    obj = backend()
    add_segment(obj, seg, window=23)
    original = asdict(seg)
    nodes = deepcopy(obj.trg.graph_db.nodes)
    vectors = faiss.serialize_index(obj.trg.vector_db.index).copy()
    embeddings = list(obj.trg.encoder.calls)
    obj.trg.graph_db.read_only = obj.trg.vector_db.read_only = True

    hits = RealMagmaBackend.source_read_range(
        obj, seg.segment_id, 0, 1, start_char=7, end_char=12,
        limit=100, max_chars=2000,
    )

    by_turn = {i: [hit for hit in hits if hit.metadata["turn_index"] == i] for i in (0, 1)}
    assert "".join(hit.text for hit in by_turn[0]) == raw[7:]
    assert "".join(hit.text for hit in by_turn[1]) == seg.turns[1].content[:12]
    assert len(by_turn[0]) > 10
    for hit in hits:
        meta = hit.metadata
        turn = seg.turns[meta["turn_index"]]
        assert hit.text == turn.content[meta["source_start"]:meta["source_end"]]
        assert meta["turn_length"] == len(turn.content)
        assert meta["provenance"]["turn_id"] == turn.turn_id
        assert meta["provenance"]["source_role"] == turn.role
        assert meta["provenance"]["source_timestamp"] == turn.timestamp.isoformat()
        assert meta["provenance"]["source_timezone"] == "Asia/Shanghai"
        assert meta["provenance"]["timezone_source"] == "client"
    assert obj.last_source_stats["last_range"]["requested_complete"]
    assert obj.last_source_stats["last_range"]["next_range"] is None
    assert asdict(seg) == original
    assert obj.trg.graph_db.nodes == nodes
    np.testing.assert_array_equal(faiss.serialize_index(obj.trg.vector_db.index), vectors)
    assert obj.trg.encoder.calls == embeddings


def test_character_cursor_continues_across_blocks_and_turns_without_loss():
    seg = segment(texts=[("user", "before  中文🙂\nafter " * 7),
                         ("assistant", "Keep this separate."),
                         ("user", "A later confirmation, with a final condition.")])
    obj = backend()
    rows = add_segment(obj, seg, window=11)
    cursor = dict(segment_id=seg.segment_id, start_turn=0, end_turn=2,
                  start_char=3, end_char=28)
    known, seen, charges = {}, [], []
    for _ in range(30):
        hits = source.read_range(obj, **cursor, limit=100, max_chars=17,
                                 known_candidates=known)
        stats = deepcopy(obj.last_source_stats["last_range"])
        assert 0 < sum(len(hit.text) for hit in hits) <= 17
        seen.extend(hits)
        charges.append(stats["new_node_reads"])
        if stats["requested_complete"]:
            assert stats["next_range"] is None
            break
        cursor = stats["next_range"]
        last = hits[-1].metadata
        assert (cursor["start_turn"], cursor["start_char"]) >= (
            last["turn_index"], last["source_end"])
    else:
        pytest.fail("bounded continuation did not finish")
    assert "".join(hit.text for hit in seen) == (
        seg.turns[0].content[3:] + seg.turns[1].content + seg.turns[2].content[:28]
    )
    assert sum(charges) == len(known) <= len(rows)


def test_overlap_uses_validated_cached_blocks_and_node_budget_has_exact_cursor():
    obj = backend()
    seg = segment(texts=[("user", "0123456789abcdefghijklmnopqrstuvwxyz")])
    add_segment(obj, seg, window=8)
    known = {}
    first = source.read_range(obj, seg.segment_id, 0, 0, limit=1, max_chars=100,
                              known_candidates=known)
    stats = deepcopy(obj.last_source_stats["last_range"])
    assert "".join(hit.text for hit in first) == seg.turns[0].content[:8]
    assert not stats["requested_complete"]
    assert stats["reason"] == "source_range_node_budget"
    assert stats["next_range"]["start_char"] == 8

    replay = source.read_range(obj, seg.segment_id, 0, 0, start_char=2, end_char=7,
                               limit=0, max_chars=100, known_candidates=known)
    assert "".join(hit.text for hit in replay) == seg.turns[0].content[2:7]
    assert obj.last_source_stats["last_range"]["new_node_reads"] == 0
    before = len(known)
    overlap = source.read_range(obj, seg.segment_id, 0, 0, start_char=4, end_char=20,
                                limit=2, max_chars=100, known_candidates=known)
    assert "".join(hit.text for hit in overlap) == seg.turns[0].content[4:20]
    assert obj.last_source_stats["last_range"]["new_node_reads"] == len(known) - before == 2
    eid = next(iter(known))
    known[eid] = replace(known[eid], text="X" * len(known[eid].text))
    with pytest.raises(ValueError, match="source_known_candidate_mismatch"):
        source.read_range(obj, seg.segment_id, 0, 0, end_char=4,
                          limit=0, max_chars=100, known_candidates=known)


def test_missing_block_never_bridges_gap_but_valid_prefix_is_still_readable():
    obj = backend()
    seg = segment(texts=[("user", "abcdefghijklmnopqrstuvwx"),
                         ("assistant", "Another original turn.")])
    rows = _source_rows(seg, lambda text: len(text) <= 8)
    for row in (rows[0], rows[2]):
        source.add(obj, **row)
    prefix = source.read_range(obj, seg.segment_id, 0, 0, end_char=8,
                               limit=3, max_chars=100)
    assert "".join(hit.text for hit in prefix) == "abcdefgh"
    prefix_stats = obj.last_source_stats["last_range"]
    assert prefix_stats["requested_complete"]
    assert prefix_stats["segment_turn_count"] is None
    assert prefix_stats["indexed_turn_count"] == 1
    assert prefix_stats["indexed_turn_extent"] == 1
    with pytest.raises(ValueError, match="source_range_missing"):
        source.read_range(obj, seg.segment_id, 0, 0, limit=3, max_chars=100)
    stats = obj.last_source_stats["last_range"]
    assert not stats["requested_complete"]
    assert stats["next_range"]["start_char"] == 8


def test_restart_range_read_is_identical_and_never_combines_local_turn_ids():
    obj = backend()
    a = segment("one", conversation="same-session", texts=[("user", "Original account.")])
    b = segment("two", conversation="same-session", texts=[("assistant", "Unadopted proposal.")], start=2)
    a = replace(a, turns=(replace(a.turns[0], turn_id="local-zero"),))
    b = replace(b, turns=(replace(b.turns[0], turn_id="local-zero"),))
    for seg in (a, b):
        add_segment(obj, seg, window=7)
    rebuilt = restart(obj)
    for seg in (a, b):
        before = source.read_range(obj, seg.segment_id, 0, 0, limit=10, max_chars=100)
        after = RealMagmaBackend.source_read_range(rebuilt, seg.segment_id, 0, 0,
                                                  limit=10, max_chars=100)
        assert after == before
        assert "".join(hit.text for hit in after) == seg.turns[0].content
        assert {hit.metadata["provenance"]["segment_id"] for hit in after} == {seg.segment_id}
        assert rebuilt.last_source_stats["last_range"]["segment_turn_count"] == 1


@pytest.mark.parametrize("changes", [
    {"segment_id": None}, {"start_turn": True}, {"end_turn": 10**9},
    {"start_char": -1}, {"start_char": 9, "end_char": 3},
    {"end_char": 10**9}, {"limit": -1}, {"max_chars": 0},
    {"known_candidates": []}, {"segment_id": "missing-segment"},
])
def test_invalid_model_ranges_do_not_project_graph_nodes(monkeypatch, changes):
    obj = backend()
    seg = segment(texts=[("user", "Small original.")])
    add_segment(obj, seg)
    monkeypatch.setattr(obj.trg.graph_db, "get_node",
                        lambda *_: pytest.fail("invalid range read the graph"))
    request = dict(segment_id=seg.segment_id, start_turn=0, end_turn=0,
                   start_char=0, end_char=None, limit=5, max_chars=100)
    request.update(changes)
    with pytest.raises(ValueError, match="source_(range_(invalid|missing)|known_candidates_invalid)") as caught:
        source.read_range(obj, **request)
    assert type(caught.value) is source.SourceRangePreflightError


def test_partial_range_failure_never_claims_zero_projection(monkeypatch):
    obj = backend()
    seg = segment(texts=[("user", "The first literal turn."),
                         ("assistant", "A separate later turn.")])
    add_segment(obj, seg)
    original = obj.trg.graph_db.get_node
    projected = []

    def counted(node_id):
        projected.append(node_id)
        return original(node_id)

    monkeypatch.setattr(obj.trg.graph_db, "get_node", counted)
    with pytest.raises(ValueError, match="source_range_invalid") as caught:
        source.read_range(obj, seg.segment_id, 0, 1,
                          end_char=len(seg.turns[1].content) + 1,
                          limit=6, max_chars=1000)
    assert not isinstance(caught.value, source.SourceRangePreflightError)
    assert len(projected) == obj.last_source_stats["last_range"]["new_node_reads"] == 1
