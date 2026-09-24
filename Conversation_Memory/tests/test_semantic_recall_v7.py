"""V7 changes only selection/use; its read is exactly the V6 read."""
import json

import pytest

from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.adapter.semantic_protocol import semantic_schema_guidance
from Conversation_Memory.tests.test_semantic_recall_v5 import _owner
from Mind.evidence_selector import (
    LlmSemanticEvidenceSelectorV6, LlmSemanticEvidenceSelectorV7,
    _SEMANTIC_V7_BASE_PROMPT, _SEMANTIC_V7_SUPPLEMENT_PROMPT,
)


class Model:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def generate(self, history, payload, *, system_prompt):
        self.calls.append((payload, system_prompt))
        return self.output


def test_v7_uses_v6_read_methods_and_diagnostics():
    assert "current message being answerable on its own is NOT a reason" in _SEMANTIC_V7_BASE_PROMPT
    assert "It need not be strictly necessary" in _SEMANTIC_V7_SUPPLEMENT_PROMPT
    assert semantic_schema_guidance(base=False) in _SEMANTIC_V7_BASE_PROMPT
    assert semantic_schema_guidance(base=False) in _SEMANTIC_V7_SUPPLEMENT_PROMPT

    owner = object.__new__(MagmaMemoryAdapter)
    owner.associative_read_profile = "semantic-associative-v7"
    assert owner.recall("query", RecallPolicy()).safe_error_code == "semantic_selection_required"


def test_v7_facade_prepares_byte_identical_v6_base_and_supplement_panels():
    fake = _owner({'a': (.9, 0), 'c': (.8, 0)},
                  {'a': [('b', 1.)], 'b': [], 'c': []})
    owner = object.__new__(MagmaMemoryAdapter)
    owner.__dict__.update(fake.__dict__)
    policy = RecallPolicy(max_evidence_items=32, max_chars=9000,
                          max_bytes=36000, include_source_context=True)

    def read(profile):
        owner.associative_read_profile = profile
        base = owner.prepare_recall('Past event?', policy)
        lock = owner.lock_semantic_base(base, (('a', 'history', 'same_event'),),
                                        False, 'none')
        direct, graph = owner.prepare_semantic_supplements(lock, policy)
        return (tuple(base.selection_items), base.context.rendered_text.encode(),
                tuple(direct.selection_items), direct.context.rendered_text.encode(),
                tuple(graph.selection_items), graph.context.rendered_text.encode(),
                tuple(owner._last_semantic_graph_diagnostics['graph_exclusive_pool_ids']))

    assert read('semantic-associative-v6') == read('semantic-associative-v7')


def test_v7_schema_payload_and_history_analogy_boundary():
    items = (("a", "a concrete prior workflow"), ("b", "another person's same name"))
    response = json.dumps({"ranked": [{"id": 1, "use": "analogy",
                                      "relation": "similar_workflow"}]})
    v6 = Model(response)
    v7 = Model(response)
    assert LlmSemanticEvidenceSelectorV6(v6).select_base("current can be answered", [], items) == (
        ("a", "analogy", "similar_workflow"),)
    assert LlmSemanticEvidenceSelectorV7(v7).select_base("current can be answered", [], items) == (
        ("a", "analogy", "similar_workflow"),)
    assert v6.calls[0][0] == v7.calls[0][0]
    assert v6.calls[0][1] != v7.calls[0][1]
    assert "same name" in v7.calls[0][0]

    supplement = Model(response)
    chosen = LlmSemanticEvidenceSelectorV7(supplement).select_supplement(
        "current", [], ("locked Fact",), items)
    assert chosen == (("a", "analogy", "similar_workflow"),)
    assert "graph" not in supplement.calls[0][0].lower()
    for bad in ('{"ranked":[{"id":2,"use":"history","relation":"similar_workflow"}]}',
                '{"ranked":[{"id":3,"use":"history","relation":"same_event"}]}'):
        with pytest.raises(ValueError, match="invalid_semantic_selection"):
            LlmSemanticEvidenceSelectorV7(Model(bad)).select_base("q", [], items)
