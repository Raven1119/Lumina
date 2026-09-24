"""Full locked base, graph gap and schema invariants without provider calls."""
import json

import numpy as np
import pytest

from Conversation_Memory.adapter._semantic_recall_v5 import (
    append_graph, lock_base, prepare_base, prepare_graph_supplement,
)
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.adapter.semantic_protocol import (
    ANALOGY_RELATIONS, GRAPH_INTENTS, HISTORY_RELATIONS, USES,
    semantic_schema_guidance,
)
from Conversation_Memory.tests.test_calibrated_first_hit import adapter
from Mind.evidence_selector import (
    _SEMANTIC_V5_BASE_PROMPT, _SEMANTIC_V5_GRAPH_PROMPT, _parse_v5_base,
)

POLICY = RecallPolicy(max_evidence_items=32, max_chars=9000,
                      max_bytes=36000, include_source_context=True)


def _owner(scores, edges):
    owner = adapter(scores, edges)
    owner.backend.index.node_ids = tuple(scores)
    owner.backend.index.encode_query = lambda text: (np.array([1.], dtype=np.float32), False)
    owner.backend.index.cosine_nodes = lambda vector, ids: {
        node_id: {"b": .9, "c": .5}.get(node_id, .1) for node_id in ids}
    return owner


def test_base_lock_ignores_seek_and_graph_is_blocked_when_all_slots_used():
    owner = _owner({'a': (.9, 0), 'b': (.8, 0), 'c': (.7, 0)},
                   {'a': [], 'b': [], 'c': []})
    base = prepare_base(owner, 'Past event?', POLICY)
    ranked = tuple((eid, 'history', 'same_event') for eid in ('a', 'b', 'c'))
    plain = lock_base(owner, base, ranked, False, 'none', '')
    seeking = lock_base(owner, base, ranked, True, 'analogy', 'Need another case')
    assert plain.selected_ids == seeking.selected_ids == ('a', 'b', 'c')
    assert plain.rendered_blocks == seeking.rendered_blocks
    assert plain.context.rendered_text.encode() == seeking.context.rendered_text.encode()
    assert seeking.remaining_slots == 0
    with pytest.raises(ValueError, match='graph_supplement_not_requested'):
        prepare_graph_supplement(owner, seeking, POLICY)
    for n in (1, 2):
        partial = lock_base(owner, base, ranked[:n], True, 'analogy', 'Need another case')
        assert partial.remaining_slots == 3-n


def test_graph_need_scores_only_visited_pool_and_append_preserves_base():
    owner = _owner({'a': (.9, 0)}, {'a': [('b', 1.)], 'b': [], 'c': []})
    base = prepare_base(owner, 'Past event?', POLICY)
    lock = lock_base(owner, base, (('a', 'history', 'same_event'),),
                     True, 'analogy', 'Need a different workflow')
    graph = prepare_graph_supplement(owner, lock, POLICY)
    assert tuple(item.evidence_id for item in graph.context.evidence) == ('b',)
    diagnostic = owner._last_semantic_graph_diagnostics
    assert diagnostic['candidate_scores']['b']['need_cosine'] == .9
    assert 'c' not in diagnostic['graph_exclusive_pool_ids']
    final, _ = append_graph(lock, graph, (('b', 'analogy', 'similar_workflow'),))
    assert final.rendered_text.startswith(lock.context.rendered_text)
    assert final.evidence[:len(lock.selected_evidence)] == lock.selected_evidence
    with pytest.raises(ValueError, match='invalid_graph_supplement_selection'):
        append_graph(lock, graph, (('b', 'history', 'same_event'),))


def test_need_reorders_only_visited_candidates_and_failure_keeps_lock():
    owner = _owner({'a': (.9, 0)}, {'a': [('b', .8), ('c', .2)], 'b': [], 'c': []})
    base = prepare_base(owner, 'Past event?', POLICY)
    lock = lock_base(owner, base, (('a', 'history', 'same_event'),),
                     True, 'analogy', 'Need another workflow')
    graph = prepare_graph_supplement(owner, lock, POLICY)
    assert tuple(item.evidence_id for item in graph.context.evidence)[:2] == ('b', 'c')
    owner.backend.index.cosine_nodes = lambda vector, ids: {
        node_id: {'b': .1, 'c': .9}[node_id] for node_id in ids}
    reranked = prepare_graph_supplement(owner, lock, POLICY)
    assert tuple(item.evidence_id for item in reranked.context.evidence)[:2] == ('c', 'b')
    owner.backend.view.version += 1
    failed = prepare_graph_supplement(owner, lock, POLICY)
    assert failed.context.safe_error_code == 'graph_supplement_snapshot_mismatch'
    assert lock.context.rendered_text and lock.selected_ids == ('a',)


def test_v5_schema_prompt_parser_share_enums_and_reject_invalid_gap():
    prompt = _SEMANTIC_V5_BASE_PROMPT + _SEMANTIC_V5_GRAPH_PROMPT
    assert semantic_schema_guidance(base=True) in _SEMANTIC_V5_BASE_PROMPT
    assert semantic_schema_guidance(base=False) in _SEMANTIC_V5_GRAPH_PROMPT
    for value in (*USES, *HISTORY_RELATIONS, *ANALOGY_RELATIONS, *GRAPH_INTENTS):
        assert value in prompt
    row = {'ranked': [{'id': 1, 'use': 'history', 'relation': 'same_event'}],
           'seek_graph': True, 'graph_intent': 'analogy', 'graph_need': 'Need prior workflow'}
    assert _parse_v5_base(json.dumps(row), 1)[1:] == (True, 'analogy', 'Need prior workflow')
    for invalid in ({**row, 'graph_need': ''}, {**row, 'graph_need': 'x'*241},
                    {**row, 'unknown': True}, {**row, 'ranked': [{'id': 1,
                     'use': 'history', 'relation': 'similar_workflow'}]}):
        with pytest.raises(ValueError):
            _parse_v5_base(json.dumps(invalid), 1)


def test_public_recall_requires_v5_selection():
    owner = object.__new__(MagmaMemoryAdapter)
    owner.associative_read_profile = 'semantic-associative-v5'
    result = owner.recall('Past event?', POLICY)
    assert result.evidence == ()
    assert result.safe_error_code == 'semantic_selection_required'
