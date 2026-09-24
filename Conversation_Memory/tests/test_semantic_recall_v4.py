"""Locked base and lazy graph supplement invariants."""
from dataclasses import FrozenInstanceError

import pytest

from Conversation_Memory.adapter._semantic_recall_v4 import (
    append_graph, lock_base, prepare_base, prepare_graph_supplement,
)
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.tests.test_calibrated_first_hit import adapter


POLICY = RecallPolicy(max_evidence_items=32, max_chars=9000,
                      max_bytes=36000, include_source_context=True)


def memory():
    result = adapter({'a': (.9, .2)}, {'a': [('b', 1.)], 'b': []})
    result.backend.index.node_ids = ('a',)
    return result


def test_base_is_frozen_and_graph_can_only_append_exclusive_fact():
    owner = memory()
    base = prepare_base(owner, 'Past event?', POLICY)
    base_ids = tuple(item.evidence_id for item in base.context.evidence)
    base_cards = tuple(card.encode() for _, card in base.selection_items)
    lock = lock_base(owner, base, (('a', 'history', 'same_event'),), True, 'analogy')
    with pytest.raises(FrozenInstanceError):
        lock.seek_graph = False
    graph = prepare_graph_supplement(owner, lock, POLICY)
    assert graph.context.safe_error_code is None
    assert tuple(item.evidence_id for item in graph.context.evidence) == ('b',)
    assert len(graph.context.evidence) <= 24
    assert tuple(item.evidence_id for item in base.context.evidence) == base_ids
    assert tuple(card.encode() for _, card in base.selection_items) == base_cards
    final, _ = append_graph(lock, graph, (('b', 'analogy', 'similar_workflow'),))
    assert tuple(item.evidence_id for item in final.evidence) == ('a', 'b')
    assert final.rendered_text.startswith(lock.context.rendered_text)
    assert '[OPTIONAL GRAPH SUPPLEMENT]' in final.rendered_text
    assert final.rendered_text.encode().startswith(lock.rendered_blocks[0].encode())


def test_graph_intent_rejects_history_analogy_and_snapshot_changes_preserve_base():
    owner = memory()
    base = prepare_base(owner, 'Past event?', POLICY)
    lock = lock_base(owner, base, (('a', 'history', 'same_event'),), True, 'analogy')
    graph = prepare_graph_supplement(owner, lock, POLICY)
    with pytest.raises(ValueError, match='invalid_graph_supplement_selection'):
        append_graph(lock, graph, (('b', 'history', 'same_event'),))
    with pytest.raises(ValueError, match='invalid_graph_intent'):
        lock_base(owner, base, (), True, 'none')
    owner.backend.view.version += 1
    failed = prepare_graph_supplement(owner, lock, POLICY)
    assert failed.context.safe_error_code == 'graph_supplement_snapshot_mismatch'
    assert lock.context.evidence[0].evidence_id == 'a'


def test_seek_false_does_not_prepare_graph_and_overflow_is_auditable():
    owner = memory()
    base = prepare_base(owner, 'Past event?', POLICY)
    lock = lock_base(owner, base, (('a', 'history', 'same_event'),), False, 'none')
    assert not lock.seek_graph and lock.remaining_slots == 2
    with pytest.raises(ValueError, match='graph_supplement_not_requested'):
        prepare_graph_supplement(owner, lock, POLICY)


def test_public_recall_cannot_bypass_v4_mind_selection():
    owner = object.__new__(MagmaMemoryAdapter)
    owner.associative_read_profile = 'semantic-associative-v4'
    result = owner.recall('Past event?', POLICY)
    assert result.evidence == ()
    assert result.safe_error_code == 'semantic_selection_required'
