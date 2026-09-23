"""Explicit Chat profile routing and actual request boundary, without a model."""
import pytest

from core import main as app_module
from Conversation_Memory.adapter.models import MemoryContext, MemoryEvidence, SourceProvenance
from Mind.constant_gate import ConstantMindGate
from tests.test_query_mind_chat import Answer, post_chat


def test_calibrated_profile_is_explicit_and_keeps_v6_writer(tmp_path, monkeypatch):
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    seen=[]
    monkeypatch.setenv('LUMINA_DREAM_MAGMA_PERSIST_DIR',str(tmp_path/'magma'))
    monkeypatch.setattr(MagmaMemoryAdapter,'create_real',lambda *a,**kw: seen.append(kw) or kw)
    model,cold=object(),object()
    monkeypatch.delenv('LUMINA_MEMORY_PROFILE',raising=False)
    app_module._build_memory_retriever(model,cold)
    assert seen[-1]['associative_read_profile']=='reliable-v2'
    monkeypatch.setenv('LUMINA_MEMORY_PROFILE','calibrated-first-hit-v1')
    app_module._build_memory_retriever(model,cold)
    assert seen[-1]['associative_read_profile']=='calibrated-first-hit-v1'
    assert seen[-1]['ingestion_version']=='grounded-formation-v6'
    assert seen[-1]['formation_model'] is model and seen[-1]['cold_store'] is cold
    monkeypatch.setenv('LUMINA_MIND_GATE_MODE','graph-read-v2')
    with pytest.raises(ValueError,match='calibrated_memory_gate_profile_conflict'):
        app_module._build_memory_retriever(model,cold)


def test_natural_chat_delivers_candidate_fact_once(tmp_path,monkeypatch):
    class Memory:
        def __init__(self):self.calls=[]
        def recall(self,query,policy):
            self.calls.append(query)
            p=SourceProvenance('s','s','t','user','2026-01-01T00:00:00+00:00',
                               'UTC','grounded-formation-v6','client')
            fact=MemoryEvidence('f','User stated: the blue key was retained.',None,p)
            return MemoryContext(query,(fact,), '[M1 USER]\n'+fact.text)
    monkeypatch.setenv('LUMINA_MIND_GATE_MODE','constant')
    monkeypatch.setenv('LUMINA_MEMORY_PROFILE','calibrated-first-hit-v1')
    memory,answer=Memory(),Answer()
    app=app_module.create_app(draft_store_path=tmp_path/'hot.jsonl',
         cold_draft_path=tmp_path/'cold.jsonl',compaction_state_path=tmp_path/'compact.json',
         mind_decision_log_path=tmp_path/'decisions.jsonl',model_client=answer,
         memory_retriever=memory,mind_gate=ConstantMindGate(),recall_enabled=True,
         env_file_path=None,enable_compaction=False)
    question='上次那把蓝钥匙后来怎样了？'
    response=post_chat(app,question)
    assert response.status_code==200
    assert memory.calls==[question]
    assert len(answer.calls)==1 and answer.calls[0][1]==question
    assert 'the blue key was retained' in answer.calls[0][2]
