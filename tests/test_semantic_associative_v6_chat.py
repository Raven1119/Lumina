"""The explicit V6 Chat route locks base before local graph exploration."""
import asyncio
import json

import fastapi.routing
import httpx

from Conversation_Memory.adapter._semantic_recall_v6 import (
    lock_base, prepare_base, prepare_supplements,
)
from Conversation_Memory.tests.test_semantic_recall_v5 import _owner
from Mind.evidence_selector import LlmSemanticEvidenceSelectorV6
from core.main import create_app, _default_semantic_selector
from core.message_runtime import detect_grounding_risks, detect_v6_grounding_risks
from core.model_client import MockModelClient


class Model:
    client_kind = 'model'
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []
    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        return self.outputs.pop(0)


class Memory:
    def __init__(self):
        self.owner = _owner({'a': (.9, 0), 'c': (.8, 0), 'd': (.7, 0)},
                            {'a': [('b', 1.)], 'b': [], 'c': [], 'd': []})
        self.traversals = 0
    def prepare_recall(self, query, policy):
        return prepare_base(self.owner, query, policy)
    def lock_semantic_base(self, prepared, ranked, *_):
        return lock_base(self.owner, prepared, ranked)
    def prepare_semantic_supplements(self, locked, policy):
        self.traversals += 1
        return prepare_supplements(self.owner, locked, policy)
    def recall(self, *_):
        raise AssertionError('unselected_panel_bypass')


def test_actual_asgi_v6_appends_fourth_fact_and_audits(tmp_path, monkeypatch):
    monkeypatch.setenv('LUMINA_MEMORY_PROFILE', 'semantic-associative-v6')
    monkeypatch.setenv('LUMINA_MIND_GATE_MODE', 'llm')
    memory = Memory()
    selector_model = Model(
        json.dumps({'ranked': [{'id': n, 'use': 'history', 'relation': 'same_event'}
                               for n in (1, 2, 3)]}),
        json.dumps({'ranked': [{'id': 1, 'use': 'analogy',
                               'relation': 'similar_workflow'}]}),
    )
    answer = Model('长期记忆中没有相关记录。')
    app = create_app(draft_store_path=tmp_path/'hot.jsonl',
                     cold_draft_path=tmp_path/'cold.jsonl',
                     compaction_state_path=tmp_path/'state.json',
                     mind_decision_log_path=tmp_path/'mind.jsonl',
                     execution_root=tmp_path/'execution', model_client=answer,
                     memory_retriever=memory,
                     semantic_selector=LlmSemanticEvidenceSelectorV6(selector_model),
                     recall_enabled=True, env_file_path=None, enable_compaction=False)
    async def inline(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(fastapi.routing, 'run_in_threadpool', inline)
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            return await client.post('/api/chat', json={'message': 'Past event?'})
    response = asyncio.run(invoke())
    assert response.status_code == 200
    assert memory.traversals == 1 and len(selector_model.calls) == 2
    prompt = answer.calls[0][2]
    assert all(f'{eid} only' in prompt for eid in 'abcd')
    assert 'NON_EXHAUSTIVE_BOUNDED_VIEW' in prompt
    assert 'current user message' in prompt
    audits = [json.loads(line) for line in (tmp_path/'mind.jsonl').read_text().splitlines()]
    assert audits[0]['selected_evidence_ids'] == ['a', 'c', 'd']
    assert audits[1]['selected_evidence_ids'] == ['a', 'c', 'd', 'b']
    assert audits[-1]['grounding_risks'] == ['global_absence_from_partial_recall']
    assert response.json()['response']['text'] == '长期记忆中没有相关记录。'


def test_v6_factory_and_current_plan_detector():
    assert isinstance(_default_semantic_selector(MockModelClient(), version=6),
                      LlmSemanticEvidenceSelectorV6)
    assert detect_grounding_risks('这张卡已经公开发布了。',
                                  '旧边界与公开发布时间无关。',
                                  '我计划今天发布这张卡。') == ('completion_upgrade',)
    assert detect_v6_grounding_risks('可见材料里没有后续记录。',
                                     'USER stated: The lowered shield protected the notch.') == (
        'global_absence_from_partial_recall',)


def test_base_selector_failure_still_explores_but_injects_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv('LUMINA_MEMORY_PROFILE', 'semantic-associative-v6')
    monkeypatch.setenv('LUMINA_MIND_GATE_MODE', 'llm')
    memory = Memory()
    selector_model = Model('not valid JSON')
    answer = Model('Only the current message is available.')
    app = create_app(draft_store_path=tmp_path/'hot.jsonl',
                     cold_draft_path=tmp_path/'cold.jsonl',
                     compaction_state_path=tmp_path/'state.json',
                     mind_decision_log_path=tmp_path/'mind.jsonl',
                     execution_root=tmp_path/'execution', model_client=answer,
                     memory_retriever=memory,
                     semantic_selector=LlmSemanticEvidenceSelectorV6(selector_model),
                     recall_enabled=True, env_file_path=None, enable_compaction=False)
    async def inline(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(fastapi.routing, 'run_in_threadpool', inline)
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            return await client.post('/api/chat', json={'message': 'Past event?'})
    assert asyncio.run(invoke()).status_code == 200
    assert memory.traversals == 1
    assert '<BEGIN_EXACT_GROUNDED_SPANS>' not in answer.calls[0][2]
