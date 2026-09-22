"""Literal views over existing source recall; synthetic data and zero generation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import timedelta, timezone

import faiss
import numpy as np
import pytest

from Conversation_Memory.adapter import _source_backend as source
from Conversation_Memory.adapter import source_experiences as views_module
from Conversation_Memory.adapter.models import ExperienceContext, MemoryContext, RecallPolicy
from Conversation_Memory.adapter.source_memory import _source_rows
from Conversation_Memory.adapter.source_reader import SourceReadLimits, merge_ranges
from Conversation_Memory.tests.test_source_backend_views import segment
from Conversation_Memory.tests.test_source_memory import adapter
from Conversation_Memory.tests.test_source_reader import RangeBackend
from Conversation_Memory.recall.bge_reranker import BGE_MAX_LENGTH, BGE_MODEL, BGE_REVISION, BgeReranker


class CheckedScorer:
    def __init__(self, limit=10000, tokenizer=None, logit=0.8):
        self.limit, self._tokenizer, self.logit = limit, tokenizer, logit
        self.checked, self.scored = [], []

    def fits_pair(self, cue, text):
        fits = (BgeReranker.fits_pair(self, cue, text) if self._tokenizer is not None
                else len(cue) + len(text) <= self.limit)
        self.checked.append((cue, text, fits))
        return fits

    def score(self, cue, texts):
        assert all((cue, text, True) in self.checked for text in texts)
        self.scored.append((cue, tuple(texts)))
        return tuple(self.logit for _ in texts)


class ExperienceBackend(RangeBackend):
    """Real source-index/range methods with optional controlled anchor order."""
    forced_positions = None

    def source_candidates(self, cue, policy):
        found = super().source_candidates(cue, policy)
        if self.forced_positions is None:
            return found
        by_position = {(node.attributes["segment_id"], node.attributes["block_index"]): node
                       for node in self.owner._source_nodes.values()}
        chosen = [source._candidate(by_position[key]) for key in self.forced_positions]
        chosen = chosen[:min(policy.top_k, policy.max_nodes)]
        self.owner.last_source_stats["segment_turn_counts"] = {
            row.metadata["segment_id"]: (max(slot["turns"]) + 1 if len(slot["ids"]) == slot["count"] else None)
            for row in chosen for slot in [self.owner._source_segments[row.metadata["segment_id"]]]}
        return chosen

    def source_neighbors(self, anchor, **kwargs):
        self.neighbor_reads.append((anchor.metadata["evidence_id"], dict(kwargs)))
        return source.neighbors(self.owner, anchor, **kwargs)


def memory(tmp_path, *segments, anchors=None, window=200, scorer=None):
    backend = ExperienceBackend(segments, window=window)
    backend.forced_positions = anchors
    return adapter(tmp_path, backend, reranker=scorer or CheckedScorer())


def policy(**changes):
    return replace(RecallPolicy(top_k=10, max_nodes=20, max_evidence_items=20,
                                max_chars=5000, max_graph_depth=0), **changes)


def source_characters(context):
    return {(e.provenance.segment_id, e.provenance.turn_id, i): e.text[i-e.source_start]
            for e in context.evidence for i in range(e.source_start, e.source_end)}


def assert_literal(context, segments):
    originals = {(seg.segment_id, turn.turn_id): turn for seg in segments for turn in seg.turns}
    for excerpt in context.evidence:
        p = excerpt.provenance
        turn = originals[p.segment_id, p.turn_id]
        assert excerpt.text == turn.content[excerpt.source_start:excerpt.source_end]
        assert p.source_role == turn.role
        assert p.source_timestamp == turn.timestamp.isoformat()
        assert p.source_timezone == turn.source_timezone and p.timezone_source == turn.timezone_source
    assert len(source_characters(context)) == sum(len(e.text) for e in context.evidence)


def paired_read(obj, cue, limits):
    """Compare actual A execution and the facade, including every scoring input."""
    original = obj.recall_sources(cue, limits)
    expected = deepcopy((obj.backend.source_reads, obj.backend.neighbor_reads,
                         obj._bge_reranker.checked, obj._bge_reranker.scored,
                         obj.last_source_read))
    for recorder in (obj.backend.source_reads, obj.backend.neighbor_reads,
                     obj._bge_reranker.checked, obj._bge_reranker.scored):
        recorder.clear()
    calls = []
    recall = obj.recall_sources
    def tracked(query, selected_policy):
        calls.append((query, selected_policy))
        return recall(query, selected_policy)
    obj.recall_sources = tracked
    try:
        result = obj.recall_experiences(cue, limits)
    finally:
        obj.recall_sources = recall
    assert calls == [(cue, limits)]
    actual = (obj.backend.source_reads, obj.backend.neighbor_reads,
              obj._bge_reranker.checked, obj._bge_reranker.scored, obj.last_source_read)
    assert actual == expected
    assert result.query == original.query and result.safe_error_code == original.safe_error_code
    assert source_characters(result) == source_characters(original)
    assert result.evidence == merge_ranges(original.evidence)
    assert len(result.rendered_text) <= len(original.rendered_text) <= limits.max_chars
    if result.evidence:
        assert obj.last_experience_read["source_read"] == obj.last_source_read
        assert obj.last_experience_read["selected_source_chars"] == len(source_characters(original))
        assert obj.last_experience_read["generated_calls"] == 0
    return original, result


@pytest.mark.parametrize("limits", [policy(), policy(max_nodes=3, top_k=1),
    policy(max_evidence_items=3), policy(max_chars=1), policy(final_min_score=1.0)])
def test_same_query_candidates_scores_and_source_character_union_as_source_recall(tmp_path, limits):
    seg = segment(texts=[("user" if i % 2 == 0 else "assistant", f"Original observation {i}.")
                         for i in range(8)])
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, i) for i in (6, 0, 2)])
    original, result = paired_read(obj, "  original observation  ", limits)
    assert_literal(result, [seg])
    assert len(obj.backend.source_reads) == 1
    assert result.truncated or not original.truncated


def test_original_dialogue_roles_times_order_and_dtos_are_preserved(tmp_path):
    seg = segment(texts=[("user", "First record: the lid stays open."),
                         ("assistant", "Closing it is only a suggestion."),
                         ("user", "Later record: it remains open for this check.")])
    zone = timezone(timedelta(hours=8))
    seg = replace(seg, turns=tuple(replace(t, timestamp=t.timestamp.astimezone(zone),
                                          source_timezone="Asia/Shanghai") for t in seg.turns))
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, 2)])
    original = asdict(seg)
    _, context = paired_read(obj, "the open lid", policy())
    assert isinstance(context, ExperienceContext) and context.safe_error_code is None
    assert_literal(context, [seg])
    assert [e.turn_index for e in context.evidence] == [0, 1, 2]
    assert '"role":"USER"' in context.rendered_text and '"role":"LUMINA"' in context.rendered_text
    assert context.rendered_text.index(seg.turns[0].content) < context.rendered_text.index(seg.turns[2].content)
    assert not context.truncated and len(context.experiences) == 1
    with pytest.raises(FrozenInstanceError):
        context.evidence[0].text = "Changed source"
    with pytest.raises(FrozenInstanceError):
        context.experiences[0].reference.start_turn = 9
    assert asdict(seg) == original


def test_overlapping_groups_keep_original_score_count_and_do_not_repeat_source(tmp_path):
    seg = segment(texts=[("user", "First original observation."),
                         ("assistant", "A possible adjustment."),
                         ("user", "The adjustment is not yet adopted.")])
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, i) for i in (2, 0, 1)])
    _, context = paired_read(obj, "the adjustment", policy())
    assert_literal(context, [seg])
    assert len(obj._bge_reranker.scored) == 1
    assert len(obj._bge_reranker.scored[0][1]) == 3
    assert len(context.evidence) == 3


def test_long_unicode_turn_merges_only_selected_tail_without_extra_expansion(tmp_path):
    text = "\u8bb0\u5f55\U0001f642  \u03b1\n" * 30 + "A final unconfirmed observation."
    seg = segment(texts=[("user", text)])
    obj = memory(tmp_path, seg, window=17)
    last = max(n.attributes["block_index"] for n in obj.backend.owner._source_nodes.values())
    obj.backend.forced_positions = [(seg.segment_id, last)]
    _, context = paired_read(obj, "final observation", policy(max_nodes=40, max_evidence_items=40))
    assert_literal(context, [seg])
    assert len(context.evidence) == 1
    item = context.evidence[0]
    assert item.source_start > 0 and item.source_end == len(text)
    assert item.text == text[item.source_start:]
    assert context.truncated and len(obj.last_source_read["candidates"]) == 3


def test_original_whole_group_budget_is_not_replaced_by_prefix_clipping(tmp_path):
    seg = segment(texts=[("user", "A literal account with spaces. " * 7)])
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, 0)], window=250)
    full = obj.recall_sources("literal account", policy())
    for recorder in (obj.backend.source_reads, obj.backend.neighbor_reads,
                     obj._bge_reranker.checked, obj._bge_reranker.scored):
        recorder.clear()
    original, cut = paired_read(obj, "literal account", policy(max_chars=len(full.rendered_text)-1))
    assert not original.evidence and not cut.evidence and cut.truncated
    assert cut.rendered_text == ""


def test_materialized_node_budget_and_neighbor_cache_are_enforced(tmp_path, monkeypatch):
    seg = segment(texts=[("user", f"original observation number {i}") for i in range(8)])
    obj = memory(tmp_path, seg)
    projected = []
    graph = obj.backend.owner.trg.graph_db
    original = graph.get_node
    def counted(node_id):
        projected.append(node_id)
        return original(node_id)
    monkeypatch.setattr(graph, "get_node", counted)
    context = obj.recall_experiences("original observation", policy(top_k=1, max_nodes=3))
    assert context.safe_error_code is None
    assert len(obj.last_source_read["candidates"]) <= 3
    assert len(projected) == len(set(projected)) <= 2
    assert obj.backend.last_source_stats["dense_node_reads"] <= 1
    assert obj.backend.last_source_stats["lexical_node_reads"] <= 3
    assert_literal(context, [seg])


def test_unindexed_turn_gap_stays_separate_and_references_expand_only_known_ranges(tmp_path):
    seg = segment(texts=[("user", "First available original."),
                         ("assistant", "An unindexed proposal."),
                         ("user", "Later available original.")])
    obj = memory(tmp_path, anchors=[(seg.segment_id, 0), (seg.segment_id, 2)])
    rows = _source_rows(seg, lambda _: True)
    for row in (rows[0], rows[2]):
        source.add(obj.backend.owner, **row)
    _, context = paired_read(obj, "available original", policy())
    assert_literal(context, [seg])
    assert len(context.experiences) == 2 and context.truncated
    assert [e.turn_index for e in context.evidence] == [0, 2]
    assert seg.turns[1].content not in context.rendered_text
    assert [(v.reference.start_turn, v.reference.end_turn) for v in context.experiences] == [(0, 0), (2, 2)]
    for view in context.experiences:
        reader = obj.open_source_reader("expand this original", SourceReadLimits())
        expanded = reader.read(**asdict(view.reference))
        assert expanded["requested_complete"] and expanded["segment_turn_count"] is None
        assert [s["text"] for s in expanded["sources"]] == [e.text for e in view.evidence]


def test_character_gap_is_not_hidden_and_unknown_extent_uses_exact_coordinates(tmp_path):
    seg = segment(texts=[("user", "0123456789" * 8)])
    obj = memory(tmp_path, anchors=[(seg.segment_id, 1), (seg.segment_id, 3)])
    rows = _source_rows(seg, lambda text: len(text) <= 20)
    for row in (rows[1], rows[3]):
        source.add(obj.backend.owner, **row)
    _, context = paired_read(obj, "original ranges", policy())
    assert_literal(context, [seg])
    assert len(context.experiences) == 2 and context.truncated
    assert [(e.source_start, e.source_end) for e in context.evidence] == [(20, 40), (60, 80)]
    for view in context.experiences:
        ref = view.reference
        assert ref.start_turn == ref.end_turn == 0
        assert (ref.start_char, ref.end_char) == (view.evidence[0].source_start, view.evidence[0].source_end)
        expanded = obj.open_source_reader("expand exact range", SourceReadLimits()).read(**asdict(ref))
        assert expanded["requested_complete"]
        assert [s["text"] for s in expanded["sources"]] == [view.evidence[0].text]


def test_distinct_segments_with_same_local_turn_id_never_merge(tmp_path):
    a = segment("a", conversation="shared", texts=[("user", "One original account.")])
    b = segment("b", conversation="shared", texts=[("assistant", "Another original proposal.")], start=1)
    a = replace(a, turns=(replace(a.turns[0], turn_id="local-zero"),))
    b = replace(b, turns=(replace(b.turns[0], turn_id="local-zero"),))
    obj = memory(tmp_path, a, b, anchors=[("b", 0), ("a", 0)])
    _, context = paired_read(obj, "original", policy())
    assert len(context.experiences) == 2
    assert {v.reference.segment_id for v in context.experiences} == {"a", "b"}
    assert_literal(context, [a, b])
    assert '"role":"USER"' in context.rendered_text and '"role":"LUMINA"' in context.rendered_text


def test_known_segment_reference_expands_unshown_original_using_existing_reader(tmp_path):
    seg = segment(texts=[("user" if i % 2 == 0 else "assistant", f"Original statement number {i}.")
                         for i in range(7)])
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, 0)])
    _, context = paired_read(obj, "statement", policy())
    assert len(context.evidence) == 3 and context.truncated
    view = context.experiences[0]
    assert view.reference.start_turn == 0 and view.reference.end_turn == 6
    assert obj.backend.range_reads == []
    expanded = obj.open_source_reader("expand original", SourceReadLimits(read_chars=4000)).read(**asdict(view.reference))
    assert expanded["requested_complete"]
    assert [s["text"] for s in expanded["sources"]] == [turn.content for turn in seg.turns]
    assert all(s["role"] == ("USER" if t.role == "user" else "LUMINA")
               for s, t in zip(expanded["sources"], seg.turns))


def test_actual_fixed_tokenizer_and_anchor_fallback_are_identical_to_source_recall(tmp_path):
    transformers = pytest.importorskip("transformers")
    try:
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            BGE_MODEL, revision=BGE_REVISION, local_files_only=True)
    except OSError:
        pytest.skip("fixed BGE tokenizer is not present in the local cache")
    scorer = CheckedScorer(tokenizer=tokenizer)
    seg = segment(texts=[("user" if i % 2 == 0 else "assistant",
        f"Original line {i}: " + "\u8bb0\u5f55\u4ecd\u7136\u672a\u786e\u8ba4\u3002" * 16) for i in range(7)])
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, 3)], scorer=scorer)
    _, context = paired_read(obj, "Which observation remains unconfirmed?", policy())
    assert context.evidence and len(scorer.scored) == 1
    assert obj.last_source_read["bge_context_omitted"] == 1
    for cue, texts in scorer.scored:
        for text in texts:
            encoded = tokenizer(cue, text, add_special_tokens=True, truncation=False)
            assert len(encoded["input_ids"]) <= BGE_MAX_LENGTH
    assert_literal(context, [seg])


@pytest.mark.parametrize("cue", [None, {}, "   "])
def test_invalid_cue_does_not_read_and_clears_previous_trace(tmp_path, cue):
    seg = segment(texts=[("user", "A record.")])
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, 0)])
    assert obj.recall_experiences("record", policy()).evidence
    reads = len(obj.backend.source_reads)
    context = obj.recall_experiences(cue, policy())
    assert context.safe_error_code == "invalid_query" and not context.evidence
    assert len(obj.backend.source_reads) == reads and obj.last_experience_read == {}
    assert obj.last_source_read == {}


def test_empty_index_never_loads_or_calls_the_scorer(tmp_path, monkeypatch):
    obj = memory(tmp_path)
    monkeypatch.setattr(obj, "_get_bge_reranker", lambda: pytest.fail("empty read loaded a scorer"))
    context = obj.recall_experiences("no matching history", policy())
    assert context.evidence == () and context.safe_error_code is None
    assert obj.last_experience_read == {}


@pytest.mark.parametrize("stage", ["search", "neighbors", "fits", "score", "missing_scorer", "oversized_anchor"])
def test_source_errors_propagate_without_new_error_contract_or_generation(tmp_path, monkeypatch, stage):
    seg = segment(texts=[("user", "An original record."), ("assistant", "A proposal.")])
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, 0)])
    def unavailable(*args, **kwargs):
        raise OSError("synthetic private detail and credential-bearing path")
    target = {"search": (obj.backend, "source_candidates"),
              "neighbors": (obj.backend, "source_neighbors"),
              "fits": (obj._bge_reranker, "fits_pair"),
              "score": (obj._bge_reranker, "score")}
    if stage in target:
        monkeypatch.setattr(*target[stage], unavailable)
    elif stage == "missing_scorer":
        monkeypatch.setattr(obj, "_get_bge_reranker", lambda: None)
    else:
        obj._bge_reranker.limit = 1
    context = obj.recall_experiences("original", policy())
    assert context.safe_error_code == "source_recall_unavailable"
    assert context.evidence == () and context.rendered_text == ""
    assert "private" not in repr(context) and "credential" not in repr(context)
    assert obj.last_experience_read == {} and obj.backend.fact_reads == 0


def test_view_failure_is_safe_without_changing_successful_source_trace(tmp_path, monkeypatch):
    seg = segment(texts=[("user", "An original record.")])
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, 0)])
    def broken(*args):
        raise OSError("synthetic private path")
    monkeypatch.setattr(views_module, "_views", broken)
    context = obj.recall_experiences("record", policy())
    assert context.safe_error_code == "experience_view_unavailable" and not context.evidence
    assert obj.last_source_read["selected_source_chars"] == len(seg.turns[0].content)
    assert obj.last_experience_read == {} and len(obj.backend.source_reads) == 1
    assert "private" not in repr(context)


def test_reads_are_immutable_zero_generation_and_default_fact_entry_is_unchanged(tmp_path, monkeypatch):
    seg = segment(texts=[("assistant", "Only a possible plan."), ("user", "No confirmation yet.")])
    obj = memory(tmp_path, seg, anchors=[(seg.segment_id, 0)])
    obj.state_store.put("completed:synthetic", {"status": "completed"})
    checkpoint = obj.state_store.path.read_bytes()
    nodes = deepcopy(obj.backend.owner.trg.graph_db.nodes)
    vectors = faiss.serialize_index(obj.backend.owner.trg.vector_db.index).copy()
    obj.backend.owner.trg.graph_db.read_only = obj.backend.owner.trg.vector_db.read_only = True
    obj.backend.read_only = True
    monkeypatch.setattr(obj.state_store, "put", lambda *_: pytest.fail("read mutated checkpoint"))
    class NoGeneration:
        def generate(self, *args, **kwargs):
            pytest.fail("experience recall attempted generation")
    obj.formation_model = NoGeneration()
    ordinary = obj.recall("plan", policy())
    assert isinstance(ordinary, MemoryContext) and obj.backend.fact_reads == 1
    assert obj.backend.source_reads == []
    _, context = paired_read(obj, "plan", policy())
    assert context.evidence and obj.backend.fact_reads == 1
    assert obj.last_experience_read["generated_calls"] == 0
    assert obj.state_store.path.read_bytes() == checkpoint
    assert obj.backend.owner.trg.graph_db.nodes == nodes
    np.testing.assert_array_equal(faiss.serialize_index(obj.backend.owner.trg.vector_db.index), vectors)