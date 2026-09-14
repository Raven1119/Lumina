"""Bounded reader behavior; no provider, model download, or real user state."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import faiss
import numpy as np
import pytest

from Conversation_Memory.adapter import _source_backend as source
from Conversation_Memory.adapter.models import MemoryContext, RecallPolicy
from Conversation_Memory.adapter.source_memory import _excerpt
from Conversation_Memory.adapter.source_reader import (
    SourceReadLimits, merge_ranges, render_ranges,
)
from Conversation_Memory.tests.test_source_backend_views import add_segment, backend, segment
from Conversation_Memory.tests.test_source_memory import SourceBackend, adapter


class RangeBackend(SourceBackend):
    """Reuse source-owner views/FAISS and the established no-provider fact stub."""

    def __init__(self, segments, *, window=200):
        super().__init__(window=window)
        self.owner = backend()
        self.range_reads = []
        for item in segments:
            add_segment(self.owner, item, window=window)

    @property
    def last_source_stats(self):
        return self.owner.last_source_stats

    def source_candidates(self, query, policy):
        self.source_reads.append((query, policy))
        return source.candidates(self.owner, query, policy)

    def source_read_range(self, *args, **kwargs):
        self.range_reads.append((args, dict(kwargs)))
        return source.read_range(self.owner, *args, **kwargs)


def memory(tmp_path, *segments, window=200, **kwargs):
    return adapter(tmp_path, RangeBackend(segments, window=window), **kwargs)


def test_search_replay_and_overlap_reuse_blocks_without_losing_seen_evidence(tmp_path):
    seg = segment(texts=[
        ("user", "The recorded setting stays OFF until the second check completes."),
        ("assistant", "Could we try changing the setting after another check?"),
    ])
    obj = memory(tmp_path, seg)
    reader = obj.open_source_reader("What was actually decided?", SourceReadLimits(
        search_candidates=2, search_results=1, snippet_chars=9,
    ))

    found = reader.search("recorded setting")
    assert "error" not in found and found["sources"]
    assert found["segment_turn_counts"][seg.segment_id] == len(seg.turns)
    first = deepcopy(found["sources"][0])
    charged = reader.node_reads
    calls = len(obj._bge_reranker.scored)
    found["sources"][0]["text"] = "caller mutation must not corrupt cache"
    replay = reader.search("  recorded setting  ")
    assert replay["cached"] and replay["sources"][0] == first
    assert len(obj.backend.source_reads) == 1
    assert len(obj._bge_reranker.scored) == calls
    assert reader.searches == 1 and reader.node_reads == charged

    index = first["turn"]
    reader.read(seg.segment_id, index, index, end_char=18)
    reader.read(seg.segment_id, index, index, start_char=8, end_char=28)
    context = reader.context()
    assert reader.reads == 2
    assert reader.node_reads == charged
    assert len(context.evidence) == 1
    assert context.evidence[0].text == seg.turns[index].content[:28]
    assert (context.evidence[0].source_start, context.evidence[0].source_end) == (0, 28)
    assert first["text"] in context.evidence[0].text
    assert all(entry["backend_stats"]["new_node_reads"] == 0
               for entry in reader.trace if entry["operation"] == "read")
    assert context.query == "What was actually decided?"


def test_global_rendered_budget_keeps_prefix_and_returns_exact_continuation(tmp_path):
    raw = "Long account with exact spaces.\n" * 9
    seg = segment(texts=[("user", raw)])
    obj = memory(tmp_path, seg, window=100)
    candidate = source.read_range(obj.backend.owner, seg.segment_id, 0, 0,
                                  limit=10, max_chars=1000)[0]
    prefix = replace(_excerpt(candidate), text=raw[:41], source_start=0, source_end=41)
    allowance = len(render_ranges((prefix,)))
    reader = obj.open_source_reader("What was recorded?", SourceReadLimits(
        max_chars=allowance, read_chars=1000,
    ))

    result = reader.read(seg.segment_id, 0, 0)

    assert not result["requested_complete"]
    assert result["sources"][0]["text"] == raw[:41]
    assert result["next_range"]["start_char"] == 41
    before = reader.context()
    assert before.evidence[0].source_end == 41
    assert len(before.rendered_text) == allowance
    assert reader.remaining()["rendered_chars"] == 0
    followup = reader.read(**result["next_range"])
    assert followup["sources"] == []
    assert followup["next_range"]["start_char"] == 41
    assert not followup["requested_complete"]
    assert reader.context() == before


def test_unread_gap_stays_visible_and_conflicting_overlap_cannot_replace_it(tmp_path):
    seg = segment(texts=[("user", "left----UNREAD----right")])
    obj = memory(tmp_path, seg)
    reader = obj.open_source_reader("q", SourceReadLimits())
    reader.read(seg.segment_id, 0, 0, end_char=4)
    start = seg.turns[0].content.index("right")
    reader.read(seg.segment_id, 0, 0, start_char=start)
    context = reader.context()
    assert [item.text for item in context.evidence] == ["left", "right"]
    assert [(item.source_start, item.source_end) for item in context.evidence] == [(0, 4), (start, len(seg.turns[0].content))]
    assert "UNREAD" not in context.rendered_text
    assert "unshown ranges and other history may exist" in context.rendered_text
    assert context.truncated
    first = context.evidence[0]
    with pytest.raises(ValueError, match="source_range_conflict"):
        merge_ranges((first, replace(first, text="fake")))
    assert reader.context() == context


def test_identical_local_turn_ids_in_distinct_segments_keep_roles_and_boundaries(tmp_path):
    a = segment("a", conversation="one-continuous-session", texts=[("user", "I declined.")])
    b = segment("b", conversation="one-continuous-session", texts=[("assistant", "I suggested it.")], start=2)
    a = replace(a, turns=(replace(a.turns[0], turn_id="local-zero"),))
    b = replace(b, turns=(replace(b.turns[0], turn_id="local-zero"),))
    obj = memory(tmp_path, a, b)
    reader = obj.open_source_reader("Who said what?", SourceReadLimits())
    reader.read(a.segment_id, 0, 0)
    reader.read(b.segment_id, 0, 0)
    evidence = reader.context().evidence
    assert len(evidence) == 2
    assert [item.provenance.segment_id for item in evidence] == ["a", "b"]
    assert [item.provenance.source_role for item in evidence] == ["user", "assistant"]
    assert [item.provenance.source_timestamp for item in evidence] == [
        a.turns[0].timestamp.isoformat(), b.turns[0].timestamp.isoformat(),
    ]
    rendered = reader.context().rendered_text
    assert rendered.count("[SOURCE {") == 2
    assert "[USER " in rendered and "[LUMINA " in rendered


def test_partial_index_reports_only_known_bounds_without_hiding_valid_text(tmp_path):
    seg = segment(texts=[("user", "Known prefix."), ("assistant", "Missing middle turn."),
                         ("user", "Later indexed tail.")])
    obj = memory(tmp_path)
    from Conversation_Memory.adapter.source_memory import _source_rows
    rows = _source_rows(seg, lambda _: True)
    source.add(obj.backend.owner, **rows[0])
    source.add(obj.backend.owner, **rows[2])
    reader = obj.open_source_reader("prefix?", SourceReadLimits())
    found = reader.search("Known prefix")
    assert found["segment_turn_counts"][seg.segment_id] is None
    result = reader.read(seg.segment_id, 0, 0)
    assert result["requested_complete"]
    assert result["segment_turn_count"] is None
    assert result["indexed_turn_count"] == 2
    assert result["indexed_turn_extent"] == 3
    assert result["sources"][0]["text"] == "Known prefix."
    before = reader.context()
    assert before.truncated
    missing = reader.read(seg.segment_id, 0, 2)
    assert missing["error"] == "source_range_unavailable"
    assert missing["sources"] == []
    assert reader.context() == before


def test_invalid_queries_do_no_search_work_and_bad_reads_stop_at_operation_budget(tmp_path):
    seg = segment(texts=[("user", "Only this original record.")])
    obj = memory(tmp_path, seg)
    reader = obj.open_source_reader("q", SourceReadLimits(max_reads=3, max_node_reads=6))
    for invalid in (None, {}, "", "   ", "x" * 2001):
        result = reader.search(invalid)
        assert result["error"] == "invalid_query"
    assert obj.backend.source_reads == []
    assert reader.searches == reader.node_reads == 0
    for _ in range(25):
        result = reader.read(seg.segment_id, True, 0)
        assert result["sources"] == []
    assert result["error"] == "read_budget_exhausted"
    assert len(obj.backend.range_reads) == reader.reads == 3
    assert reader.node_reads == 0
    assert not reader.context().evidence


def test_backend_failures_are_sanitized_and_failed_work_retains_budget(tmp_path, monkeypatch):
    obj = memory(tmp_path, segment(texts=[("user", "Original record.")]))
    reader = obj.open_source_reader("q", SourceReadLimits(max_searches=2, max_node_reads=6))
    calls = []

    def unavailable(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("private runtime body / credential-bearing traceback")

    monkeypatch.setattr(obj.backend, "source_candidates", unavailable)
    result = reader.search("first")
    assert result["error"] == "source_search_unavailable"
    assert "private runtime" not in repr(result)
    assert reader.node_reads == 6
    assert reader.search("second")["error"] == "node_budget_exhausted"
    assert reader.search("third")["error"] == "search_budget_exhausted"
    assert len(calls) == 1
    assert reader.remaining()["searches"] == 0
    assert reader.remaining()["node_reads"] == 0
    assert reader.context().evidence == ()


def test_explicit_reader_is_read_only_and_independent_of_configured_v2(tmp_path, monkeypatch):
    class NoFormation:
        def generate(self, *args, **kwargs):
            pytest.fail("reader attempted fact formation")

    seg = segment(texts=[("user", "The original statement."),
                         ("assistant", "Only a proposal.")])
    obj = memory(tmp_path, seg, ingestion_version="grounded-formation-v2",
                 formation_model=NoFormation())
    obj.state_store.put("existing:grounded-formation-v2", {"status": "completed"})
    checkpoint = obj.state_store.path.read_bytes()
    owner = obj.backend.owner
    nodes = deepcopy(owner.trg.graph_db.nodes)
    vectors = faiss.serialize_index(owner.trg.vector_db.index).copy()
    owner.trg.graph_db.read_only = owner.trg.vector_db.read_only = True
    obj.backend.read_only = True
    monkeypatch.setattr(obj.state_store, "put", lambda *_: pytest.fail("reader wrote a checkpoint"))

    ordinary = obj.recall("statement", RecallPolicy())
    assert isinstance(ordinary, MemoryContext) and ordinary.evidence == ()
    assert obj.backend.fact_reads == 1 and obj.backend.source_reads == []
    reader = obj.open_source_reader("source question", SourceReadLimits())
    assert reader.read(seg.segment_id, 0, 1)["requested_complete"]
    assert reader.context().evidence
    assert obj.backend.fact_reads == 1
    fresh = obj.open_source_reader("different question", SourceReadLimits())
    assert fresh.context().evidence == ()
    assert fresh.searches == fresh.reads == fresh.node_reads == 0
    assert obj.ingestion_version == "grounded-formation-v2"
    assert obj.state_store.path.read_bytes() == checkpoint
    assert owner.trg.graph_db.nodes == nodes
    np.testing.assert_array_equal(faiss.serialize_index(owner.trg.vector_db.index), vectors)
    assert obj.backend.writes == []


@pytest.mark.parametrize("changes", [
    {"max_reads": 0}, {"max_node_reads": True}, {"read_chars": 1.5},
    {"search_candidates": 2, "search_results": 3},
])
def test_invalid_limits_are_rejected_before_opening_reader(changes):
    with pytest.raises(ValueError, match="invalid_source_read_limits"):
        SourceReadLimits(**changes)


@pytest.mark.parametrize("changes", [
    {"end_turn": 1}, {"segment_id": "missing-segment"},
    {"start_turn": True}, {"start_char": 10**6}, {"end_char": 10**6},
])
def test_zero_projection_validation_can_be_corrected_with_remaining_nodes(tmp_path, monkeypatch, changes):
    seg = segment(texts=[("user", "A bounded original record.")])
    obj = memory(tmp_path, seg)
    reader = obj.open_source_reader("What was recorded?", SourceReadLimits(max_reads=3, max_node_reads=6))
    original = obj.backend.owner.trg.graph_db.get_node
    projected = []

    def counted(node_id):
        projected.append(node_id)
        return original(node_id)

    monkeypatch.setattr(obj.backend.owner.trg.graph_db, "get_node", counted)
    request = dict(segment_id=seg.segment_id, start_turn=0, end_turn=0)
    request.update(changes)
    invalid = reader.read(**request)
    assert invalid["error"] == "source_range_unavailable"
    assert projected == []
    assert reader.reads == 1 and reader.node_reads == 0
    assert reader.remaining()["node_reads"] == 6
    corrected = reader.read(seg.segment_id, 0, 0)
    assert corrected["requested_complete"]
    assert corrected["sources"][0]["text"] == seg.turns[0].content
    assert reader.reads == 2 and reader.node_reads == len(projected) == 1


def test_partial_projection_failure_retains_reservation_and_prior_evidence(tmp_path, monkeypatch):
    seg = segment(texts=[("user", "A first original turn."),
                         ("assistant", "A later separate proposal."),
                         ("user", "An already retained source.")])
    obj = memory(tmp_path, seg)
    reader = obj.open_source_reader("What was said?", SourceReadLimits(max_reads=4, max_node_reads=6))
    assert reader.read(seg.segment_id, 2, 2)["requested_complete"]
    before = reader.context()
    original = obj.backend.owner.trg.graph_db.get_node
    projected = []

    def counted(node_id):
        projected.append(node_id)
        return original(node_id)

    monkeypatch.setattr(obj.backend.owner.trg.graph_db, "get_node", counted)
    failed = reader.read(seg.segment_id, 0, 1, end_char=len(seg.turns[1].content) + 1)
    assert failed["error"] == "source_range_unavailable"
    assert len(projected) == 1
    assert reader.node_reads == reader.limits.max_node_reads
    assert reader.context() == before
    later = reader.read(seg.segment_id, 1, 1)
    assert later["sources"] == [] and len(projected) == 1
    assert reader.context() == before


@pytest.mark.parametrize("failure", [OSError("synthetic private failure"), ValueError("source_range_invalid")])
def test_unknown_read_failure_does_not_refund_from_stale_success_stats(tmp_path, monkeypatch, failure):
    seg = segment(texts=[("user", "An earlier observed statement.")])
    obj = memory(tmp_path, seg)
    reader = obj.open_source_reader("What was said?", SourceReadLimits(max_reads=3, max_node_reads=6))
    assert reader.read(seg.segment_id, 0, 0)["requested_complete"]
    before = reader.context()
    assert obj.backend.last_source_stats["last_range"]["new_node_reads"] == 1

    def unavailable(*args, **kwargs):
        raise failure

    monkeypatch.setattr(obj.backend, "source_read_range", unavailable)
    failed = reader.read(seg.segment_id, 0, 0)
    assert failed["error"] == "source_range_unavailable"
    assert "private" not in repr(failed)
    assert reader.reads == 2 and reader.node_reads == 6
    assert reader.context() == before


def test_missing_initial_indexed_turn_is_zero_projection_and_later_turn_remains_readable(tmp_path):
    seg = segment(texts=[("user", "An unindexed first turn."),
                         ("assistant", "A retained later proposal.")])
    obj = memory(tmp_path)
    from Conversation_Memory.adapter.source_memory import _source_rows
    rows = _source_rows(seg, lambda _: True)
    source.add(obj.backend.owner, **rows[1])
    reader = obj.open_source_reader("What remains available?", SourceReadLimits(max_reads=3, max_node_reads=6))
    unavailable = reader.read(seg.segment_id, 0, 1)
    assert unavailable["error"] == "source_range_unavailable"
    assert reader.reads == 1 and reader.node_reads == 0 and reader.known == {}
    later = reader.read(seg.segment_id, 1, 1)
    assert later["requested_complete"] and later["segment_turn_count"] is None
    assert later["sources"][0]["text"] == seg.turns[1].content
    assert reader.reads == 2 and reader.node_reads == 1
