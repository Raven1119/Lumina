"""V6 additive FirstHit and capacity-control contracts without provider calls."""
import json
from dataclasses import replace

import pytest

from Conversation_Memory.adapter._semantic_recall_v6 import (
    FINAL_BYTES, FINAL_CHARS, append_supplement, lock_base, prepare_base,
    prepare_supplements,
)
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.adapter.semantic_protocol import (
    ANALOGY_RELATIONS, HISTORY_RELATIONS, USES, semantic_schema_guidance,
)
from Conversation_Memory.tests.test_semantic_recall_v5 import _owner
from Mind.evidence_selector import (
    LlmSemanticEvidenceSelectorV6, _SEMANTIC_V6_BASE_PROMPT,
    _SEMANTIC_V6_SUPPLEMENT_PROMPT, _parse_v6_ranked,
)

POLICY = RecallPolicy(max_evidence_items=32, max_chars=9000, max_bytes=36000,
                      include_source_context=True)


def test_always_explores_with_full_base_and_disjoint_pools():
    owner = _owner({'a': (.9, 0), 'c': (.8, 0), 'd': (.7, 0)},
                   {'a': [('b', 1.)], 'b': [], 'c': [], 'd': []})
    prepared = prepare_base(owner, 'Past event?', POLICY)
    locked = lock_base(owner, prepared,
                       tuple((eid, 'history', 'same_event') for eid in ('a', 'c', 'd')))
    assert locked.selected_ids == ('a', 'c', 'd')
    direct, graph = prepare_supplements(owner, locked, POLICY)
    diagnostic = owner._last_semantic_graph_diagnostics
    assert diagnostic['graph_traversals'] == 1
    assert 'b' in diagnostic['graph_exclusive_pool_ids']
    assert 'b' not in diagnostic['direct_pool_ids']
    assert not set(diagnostic['graph_exclusive_pool_ids']) & set(locked.base_panel_ids)
    final, _ = append_supplement(locked, graph, (('b', 'analogy', 'similar_workflow'),))
    assert len(final.evidence) == 4
    assert tuple(item.evidence_id for item in final.evidence[:3]) == locked.selected_ids
    assert final.rendered_text.encode().startswith(locked.context.rendered_text.encode())
    assert len(final.rendered_text) <= FINAL_CHARS
    assert len(final.rendered_text.encode()) <= FINAL_BYTES
    assert '[M4 USER' in final.rendered_text
    assert not direct.context.safe_error_code


def test_direct_panel_can_add_unselected_base_panel_fact_and_graph_cannot():
    owner = _owner({'a': (.9, 0), 'c': (.8, 0)}, {'a': [('b', 1.)], 'b': [], 'c': []})
    locked = lock_base(owner, prepare_base(owner, 'Past event?', POLICY),
                       (('a', 'history', 'same_event'),))
    direct, graph = prepare_supplements(owner, locked, POLICY)
    assert tuple(x.evidence_id for x in direct.context.evidence) == ('c',)
    assert tuple(x.evidence_id for x in graph.context.evidence) == ('b',)
    d, _ = append_supplement(locked, direct, (('c', 'history', 'same_event'),))
    g, _ = append_supplement(locked, graph, (('b', 'analogy', 'similar_workflow'),))
    assert d.evidence[0] == g.evidence[0] == locked.context.evidence[0]
    assert d.rendered_text[:len(locked.context.rendered_text)] == locked.context.rendered_text
    assert g.rendered_text[:len(locked.context.rendered_text)] == locked.context.rendered_text
    later_lock = lock_base(owner, prepare_base(owner, 'Past event?', POLICY),
                           (('c', 'history', 'same_event'),))
    _, later_graph = prepare_supplements(owner, later_lock, POLICY)
    later, _ = append_supplement(later_lock, later_graph,
                                  (('b', 'analogy', 'similar_workflow'),))
    assert '[M2 USER' in later_lock.context.rendered_text
    assert '[M3 USER' in later.rendered_text


def test_supplement_failure_and_budget_leave_complete_base():
    owner = _owner({'a': (.9, 0)}, {'a': [('b', 1.)], 'b': []})
    locked = lock_base(owner, prepare_base(owner, 'Past event?', POLICY),
                       (('a', 'history', 'same_event'),))
    _, graph = prepare_supplements(owner, locked, POLICY)
    assert append_supplement(locked, graph, ())[0] == locked.context
    with pytest.raises(ValueError, match='invalid_supplement_selection'):
        append_supplement(locked, graph, (('b', 'history', 'similar_workflow'),))
    huge = replace(locked, rendered_blocks=('x' * (FINAL_CHARS-1),))
    assert append_supplement(huge, graph, (('b', 'analogy', 'similar_workflow'),))[0] == huge.context
    owner.backend.view.version += 1
    direct, failed = prepare_supplements(owner, locked, POLICY)
    assert direct.context.safe_error_code == failed.context.safe_error_code
    assert locked.context.evidence


def test_v6_schema_and_source_blind_supplement_payload():
    assert semantic_schema_guidance(base=False) in _SEMANTIC_V6_BASE_PROMPT
    assert semantic_schema_guidance(base=False) in _SEMANTIC_V6_SUPPLEMENT_PROMPT
    for value in (*USES, *HISTORY_RELATIONS, *ANALOGY_RELATIONS):
        assert value in _SEMANTIC_V6_BASE_PROMPT + _SEMANTIC_V6_SUPPLEMENT_PROMPT
    row = {'ranked': [{'id': 1, 'use': 'history', 'relation': 'same_event'}]}
    assert _parse_v6_ranked(json.dumps(row), 1, 12) == ((1, 'history', 'same_event'),)
    for invalid in ({**row, 'seek_graph': False},
                    {'ranked': row['ranked'] * 2},
                    {'ranked': [{'id': 1, 'use': 'history',
                                 'relation': 'similar_workflow'}]}):
        with pytest.raises(ValueError):
            _parse_v6_ranked(json.dumps(invalid), 1, 12)

    class Model:
        def __init__(self):
            self.calls = []
        def generate(self, history, payload, *, system_prompt):
            self.calls.append((json.loads(payload), system_prompt))
            return '{"ranked":[]}'
    model = Model()
    selector = LlmSemanticEvidenceSelectorV6(model)
    assert selector.select_supplement('Q', [], ('base',), (('a', 'card'),)) == ()
    assert selector.select_supplement('Q', [], ('base',), (('b', 'card'),)) == ()
    assert model.calls[0][1] == model.calls[1][1]
    assert set(model.calls[0][0]) == {'original_message', 'recent_context',
                                      'locked_base_items', 'candidate_items'}
    assert 'graph' not in json.dumps(model.calls[0][0]).lower()


def test_public_recall_requires_v6_semantic_selection():
    owner = object.__new__(MagmaMemoryAdapter)
    owner.associative_read_profile = 'semantic-associative-v6'
    result = owner.recall('Past event?', POLICY)
    assert result.evidence == ()
    assert result.safe_error_code == 'semantic_selection_required'
