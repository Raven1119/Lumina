"""Actual ASGI v4 Chat keeps one base receipt and only adds valid graph facts."""
import asyncio
import json

import fastapi.routing
import httpx
import pytest

from Conversation_Memory.adapter._semantic_recall_v4 import (
    lock_base, prepare_base, prepare_graph_supplement,
)
from Conversation_Memory.tests.test_calibrated_first_hit import adapter
from Mind.evidence_selector import LlmSemanticEvidenceSelectorV4
from core.main import create_app
from core.message_runtime import detect_global_absence_risk


class Model:
    client_kind = 'model'
    def __init__(self, *outputs): self.outputs=list(outputs); self.calls=[]
    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context,user_message,system_prompt))
        return self.outputs.pop(0)


class Memory:
    def __init__(self):
        self.owner=adapter({'a':(.9,.2)},{'a':[('b',1.)],'b':[]})
        self.owner.backend.index.node_ids=('a',)
        self.graph_calls=0
    def prepare_recall(self,query,policy): return prepare_base(self.owner,query,policy)
    def lock_semantic_base(self,*args): return lock_base(self.owner,*args)
    def prepare_graph_supplement(self,*args):
        self.graph_calls+=1
        return prepare_graph_supplement(self.owner,*args)
    def recall(self,*args): raise AssertionError('unselected panel bypass')


def chat(tmp_path,monkeypatch,base_output,graph_output=None,answer_output='answer'):
    import core.main as main
    monkeypatch.setenv('LUMINA_MEMORY_PROFILE','semantic-associative-v4')
    monkeypatch.setenv('LUMINA_MIND_GATE_MODE','llm')
    monkeypatch.setattr(main,'_default_mind_gate',lambda *args: pytest.fail('pre-read gate'))
    selector_model=Model(*(x for x in (base_output,graph_output) if x is not None))
    answer_model=Model(answer_output)
    memory=Memory()
    app=create_app(draft_store_path=tmp_path/'hot.jsonl',
                   cold_draft_path=tmp_path/'cold.jsonl',
                   compaction_state_path=tmp_path/'state.json',
                   mind_decision_log_path=tmp_path/'mind.jsonl',
                   execution_root=tmp_path/'execution',model_client=answer_model,
                   memory_retriever=memory,
                   semantic_selector=LlmSemanticEvidenceSelectorV4(selector_model),
                   recall_enabled=True,env_file_path=None,enable_compaction=False)
    async def inline(func,*args,**kwargs): return func(*args,**kwargs)
    monkeypatch.setattr(fastapi.routing,'run_in_threadpool',inline)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            return await client.post('/api/chat',json={'message':'Past event?'})
    response=asyncio.run(request())
    assert response.status_code==200
    audits=[json.loads(x) for x in (tmp_path/'mind.jsonl').read_text().splitlines()]
    return memory,selector_model,answer_model,response.json(),audits


def test_v4_base_only_has_one_mind_call_no_graph_and_coverage_marker(tmp_path,monkeypatch):
    base='{"ranked":[{"id":1,"use":"history","relation":"same_event"}],"seek_graph":false,"graph_intent":"none"}'
    memory,selector,answer,response,audits=chat(tmp_path,monkeypatch,base)
    assert len(selector.calls)==1 and memory.graph_calls==0 and len(answer.calls)==1
    prompt=answer.calls[0][2]
    assert '[Memory coverage: NON_EXHAUSTIVE_BOUNDED_VIEW]' in prompt
    assert 'a only' in prompt and 'b only' not in prompt
    assert audits[0]['stage']=='locked_base'
    assert audits[-1]['answer_global_absence_risk'] is False


def test_v4_graph_appends_after_same_locked_base_and_malformed_keeps_base(tmp_path,monkeypatch):
    base='{"ranked":[{"id":1,"use":"history","relation":"same_event"}],"seek_graph":true,"graph_intent":"analogy"}'
    graph='{"ranked":[{"id":1,"use":"analogy","relation":"similar_workflow"}]}'
    memory,selector,answer,response,audits=chat(tmp_path/'valid',monkeypatch,base,graph)
    assert memory.graph_calls==1 and len(selector.calls)==2
    assert 'a only' in answer.calls[0][2] and 'b only' in answer.calls[0][2]
    assert audits[0]['selected_evidence_ids']==['a']
    assert audits[1]['selected_evidence_ids']==['a','b']
    memory2,selector2,answer2,_,audits2=chat(tmp_path/'broken',monkeypatch,base,'not json')
    assert memory2.graph_calls==1 and len(selector2.calls)==2
    assert 'a only' in answer2.calls[0][2] and 'b only' not in answer2.calls[0][2]
    assert audits2[1]['fallback_reason']=='graph_supplement_selection_failed'


def test_v4_absence_detector_audits_without_rewriting_answer(tmp_path,monkeypatch):
    base='{"ranked":[],"seek_graph":false,"graph_intent":"none"}'
    text='我没有相关记录，可以再说一下吗？'
    _,_,_,response,audits=chat(tmp_path,monkeypatch,base,answer_output=text)
    assert response['response']['text']==text
    assert audits[-1]['answer_global_absence_risk'] is True
    assert audits[-1]['matched_phrase']=='没有相关记录'
    assert detect_global_absence_risk('我现在不能确认这一点。')==(False,None)
    assert detect_global_absence_risk('目前均无记录可查。')==(True,'无记录可查')
    assert detect_global_absence_risk('长期记忆中也没有相关细节。')==(True,'长期记忆中也没有')


def test_v4_graph_read_failure_keeps_locked_base(tmp_path,monkeypatch):
    base='{"ranked":[{"id":1,"use":"history","relation":"same_event"}],"seek_graph":true,"graph_intent":"analogy"}'
    def fail_graph(self,*args):
        self.graph_calls+=1
        raise RuntimeError('graph unavailable')
    monkeypatch.setattr(Memory,'prepare_graph_supplement',fail_graph)
    memory,selector,answer,_,audits=chat(tmp_path,monkeypatch,base)
    assert memory.graph_calls==1 and len(selector.calls)==1
    assert 'a only' in answer.calls[0][2] and 'b only' not in answer.calls[0][2]
    assert audits[1]['fallback_reason']=='graph_supplement_unavailable'


def test_v4_audit_append_failure_skips_graph_but_keeps_base(tmp_path,monkeypatch):
    from Mind.decision_log import JsonlDecisionLog
    base='{"ranked":[{"id":1,"use":"history","relation":"same_event"}],"seek_graph":true,"graph_intent":"analogy"}'
    def fail_record(self,*args,**kwargs): raise OSError('audit unavailable')
    monkeypatch.setattr(JsonlDecisionLog,'record',fail_record)
    # The audit file cannot be read here; inspect the real Answer boundary.
    monkeypatch.setenv('LUMINA_MEMORY_PROFILE','semantic-associative-v4')
    monkeypatch.setenv('LUMINA_MIND_GATE_MODE','llm')
    answer,selection,memory=Model('answer'),Model(base),Memory()
    app=create_app(draft_store_path=tmp_path/'hot.jsonl',
                   cold_draft_path=tmp_path/'cold.jsonl',
                   compaction_state_path=tmp_path/'state.json',
                   mind_decision_log_path=tmp_path/'mind.jsonl',
                   execution_root=tmp_path/'execution',model_client=answer,
                   memory_retriever=memory,
                   semantic_selector=LlmSemanticEvidenceSelectorV4(selection),
                   recall_enabled=True,env_file_path=None,enable_compaction=False)
    async def inline(func,*args,**kwargs): return func(*args,**kwargs)
    monkeypatch.setattr(fastapi.routing,'run_in_threadpool',inline)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            return await client.post('/api/chat',json={'message':'Past event?'})
    assert asyncio.run(request()).status_code==200
    assert memory.graph_calls==0 and len(selection.calls)==1
    assert 'a only' in answer.calls[0][2] and 'b only' not in answer.calls[0][2]
