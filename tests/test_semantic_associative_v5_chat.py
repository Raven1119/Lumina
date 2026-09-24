"""Actual ASGI Chat invokes the opt-in v5 base lock and lazy graph path."""
import asyncio
import json

import fastapi.routing
import httpx

from Conversation_Memory.adapter._semantic_recall_v5 import (
    lock_base, prepare_base, prepare_graph_supplement,
)
from Conversation_Memory.tests.test_semantic_recall_v5 import _owner
from Mind.evidence_selector import LlmSemanticEvidenceSelectorV5
from core.main import create_app
from core.message_runtime import detect_grounding_risks


class Model:
    client_kind = 'model'
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []
    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        return self.outputs.pop(0)


class Memory:
    def __init__(self, scores, edges):
        self.owner = _owner(scores, edges)
        self.graph_calls = 0
    def prepare_recall(self, query, policy):
        return prepare_base(self.owner, query, policy)
    def lock_semantic_base(self, *args):
        return lock_base(self.owner, *args)
    def prepare_graph_supplement(self, *args):
        self.graph_calls += 1
        return prepare_graph_supplement(self.owner, *args)
    def recall(self, *args):
        raise AssertionError('unselected_panel_bypass')


def _chat(tmp_path, monkeypatch, memory, base, graph=None, answer='answer'):
    monkeypatch.setenv('LUMINA_MEMORY_PROFILE', 'semantic-associative-v5')
    monkeypatch.setenv('LUMINA_MIND_GATE_MODE', 'llm')
    selector = Model(*(row for row in (base, graph) if row is not None))
    model = Model(answer)
    app = create_app(draft_store_path=tmp_path/'hot.jsonl',
                     cold_draft_path=tmp_path/'cold.jsonl',
                     compaction_state_path=tmp_path/'state.json',
                     mind_decision_log_path=tmp_path/'mind.jsonl',
                     execution_root=tmp_path/'execution', model_client=model,
                     memory_retriever=memory,
                     semantic_selector=LlmSemanticEvidenceSelectorV5(selector),
                     recall_enabled=True, env_file_path=None, enable_compaction=False)
    async def inline(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(fastapi.routing, 'run_in_threadpool', inline)
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            return await client.post('/api/chat', json={'message': 'Past event?'})
    response = asyncio.run(invoke())
    audits = [json.loads(line) for line in (tmp_path/'mind.jsonl').read_text().splitlines()]
    return selector, model, response.json(), audits


def _base(ids, seek=True):
    return json.dumps({'ranked': [{'id': n, 'use': 'history', 'relation': 'same_event'}
                                  for n in ids], 'seek_graph': seek,
                       'graph_intent': 'analogy' if seek else 'none',
                       'graph_need': 'Need a different workflow' if seek else ''})


def test_v5_full_base_skips_graph_and_preserves_three_facts(tmp_path, monkeypatch):
    memory = Memory({'a': (.9, 0), 'b': (.8, 0), 'c': (.7, 0)},
                    {'a': [], 'b': [], 'c': []})
    selector, model, response, audits = _chat(tmp_path, monkeypatch, memory,
                                               _base((1, 2, 3)))
    assert len(selector.calls) == 1 and memory.graph_calls == 0
    assert all(f'{eid} only' in model.calls[0][2] for eid in 'abc')
    assert audits[0]['remaining_slots'] == 0
    assert audits[0]['graph_status'] == 'blocked_by_full_base'
    assert audits[0]['graph_requested_but_full_base'] is True
    assert response['response']['text'] == 'answer'


def test_v5_graph_appends_and_need_is_not_answer_evidence(tmp_path, monkeypatch):
    memory = Memory({'a': (.9, 0)}, {'a': [('b', 1.)], 'b': []})
    graph = json.dumps({'ranked': [{'id': 1, 'use': 'analogy',
                                    'relation': 'similar_workflow'}]})
    selector, model, _, audits = _chat(tmp_path, monkeypatch, memory,
                                       _base((1,)), graph)
    assert len(selector.calls) == 2 and memory.graph_calls == 1
    prompt = model.calls[0][2]
    assert 'a only' in prompt and 'b only' in prompt
    assert 'Need a different workflow' not in prompt
    assert audits[0]['selected_evidence_ids'] == ['a']
    assert audits[1]['selected_evidence_ids'] == ['a', 'b']


def test_v5_grounding_detector_does_not_rewrite_answer(tmp_path, monkeypatch):
    memory = Memory({'a': (.9, 0)}, {'a': []})
    answer = '长期记忆里没有记录。'
    _, _, response, audits = _chat(tmp_path, monkeypatch, memory,
                                    _base((1,), False), answer=answer)
    assert response['response']['text'] == answer
    assert audits[-1]['grounding_risks'] == ['global_absence_from_partial_recall']
    assert detect_grounding_risks('这张卡已经公开发布了。',
                                  '用户计划下周公开发布这张卡。') == ('completion_upgrade',)


def test_v5_default_selector_factory_routes_to_v5():
    from core.main import _default_semantic_selector
    from core.model_client import MockModelClient
    assert isinstance(_default_semantic_selector(MockModelClient(), version=5),
                      LlmSemanticEvidenceSelectorV5)
