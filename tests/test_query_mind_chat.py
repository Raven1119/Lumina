"""Explicit Chat wiring uses one structured gate and one public Memory read."""
import asyncio
import json

import httpx
import pytest

from Conversation_Memory.adapter.graph_read_query import GraphReadQuery
from Conversation_Memory.adapter.models import MemoryContext
from core import main as app_module
from core.model_client import MockModelClient
from Mind.llm_gate import LlmQueryMindGate
from tests.test_query_mind_gate import CannedModel, interpreted


class Answer:
    client_kind = "model"

    def __init__(self):
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        return "recorded answer"


class Memory:
    def __init__(self):
        self.calls = []

    def recall(self, query, policy):
        self.calls.append((query, policy))
        return MemoryContext(query.text if isinstance(query, GraphReadQuery) else query,
                             rendered_text="User uses recorder Alpha. Recorder Alpha weighs 380 grams.")


def app(tmp_path, monkeypatch, *, gate_response=None, memory=None, answer=None):
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "graph-read-v2")
    gate_model = CannedModel(json.dumps(interpreted(), ensure_ascii=False) if gate_response is None else gate_response)
    builds = []
    def build(**kwargs):
        builds.append(kwargs)
        return gate_model
    monkeypatch.setattr(app_module, "build_model_client_from_env", build)
    memory, answer = memory or Memory(), answer or Answer()
    instance = app_module.create_app(
        draft_store_path=tmp_path / "hot.jsonl", cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "compact.json", mind_decision_log_path=tmp_path / "decisions.jsonl",
        model_client=answer, memory_retriever=memory, recall_enabled=True,
        env_file_path=None, enable_compaction=False,
    )
    return instance, gate_model, memory, answer, builds


def post_chat(instance, message):
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=instance), base_url="http://synthetic") as client:
            return await client.post("/api/chat", json={"message": message})
    return asyncio.run(invoke())


def test_candidate_api_has_fixed_budget_one_gate_and_keeps_original_inputs(tmp_path, monkeypatch):
    instance, gate, memory, answer, builds = app(tmp_path, monkeypatch)
    question = "我用的录音机多重？"
    response = post_chat(instance, question)
    assert response.status_code == 200 and response.json()["response"]["text"] == "recorded answer"
    assert builds == [{"max_tokens_override": 768, "temperature_override": 0.0}]
    assert len(gate.calls) == len(memory.calls) == len(answer.calls) == 1
    query, policy = memory.calls[0]
    assert isinstance(query, GraphReadQuery) and query.text == question
    assert query.intent.mode == "precise" and len(query.intent.relations) == 2
    assert policy.max_evidence_items == 3 and policy.max_chars == 5000
    assert answer.calls[0][1] == question and "380 grams" in answer.calls[0][2]
    assert "?entity" not in answer.calls[0][2] and "source_check" not in answer.calls[0][2]
    hot = [json.loads(line) for line in (tmp_path / "hot.jsonl").read_text().splitlines()]
    assert hot[0]["text"] == question
    audit = json.loads((tmp_path / "decisions.jsonl").read_text())
    assert audit["original_message"] == audit["effective_query"] == question
    assert audit["structured_query"]["intent"]["mode"] == "precise"
    assert audit["query_interpretation"]["provider_call_attempts"] == 1


def test_candidate_api_passes_actual_memory_joined_facts_to_answer(tmp_path, monkeypatch):
    from Conversation_Memory.tests.test_query_graph_read import query_adapter

    adapter, searches = query_adapter(tmp_path, monkeypatch)
    memory_calls = []
    original_recall = adapter.recall

    def record_recall(query, policy):
        result = original_recall(query, policy)
        memory_calls.append((query, policy, result))
        return result

    monkeypatch.setattr(adapter, "recall", record_recall)
    instance, gate, _, answer, _ = app(tmp_path, monkeypatch, memory=adapter)
    question = "我用的录音机多重？"
    response = post_chat(instance, question)
    assert response.status_code == 200 and response.json()["response"]["text"] == "recorded answer"
    assert len(gate.calls) == len(memory_calls) == len(answer.calls) == 1
    query, policy, result = memory_calls[0]
    assert query.text == question and query.intent.mode == "precise"
    assert len(query.intent.relations) == 2
    assert policy.max_evidence_items == 3 and policy.max_chars == 5000
    for item in (*query.intent.clues, *query.intent.relations):
        for source in item.sources:
            assert question[source.start:source.end] == source.quote
    assert {item.evidence_id for item in result.evidence} == {"use", "mass"}
    prompt = answer.calls[0][2]
    assert answer.calls[0][1] == question
    assert "我用录音机 R1。" in prompt and "录音机 R1 的质量是 240 g。" in prompt
    assert "?entity" not in prompt and "source_check" not in prompt and "RECORDER_1" not in prompt
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    assert adapter._last_first_hit_diagnostics["visible_bundles"] == (("use", "mass"),)
    assert searches[0][0] == question and 1 <= len(searches) <= 3
    assert not (tmp_path / "never-written.json").exists()
    hot = [json.loads(line) for line in (tmp_path / "hot.jsonl").read_text().splitlines()]
    assert hot[0]["text"] == question


def test_decline_and_invalid_outputs_respect_single_read_boundary(tmp_path, monkeypatch):
    instance, gate, memory, answer, _ = app(tmp_path / "decline", monkeypatch,
        gate_response=json.dumps(dict(recall=False, mode="open", clues=[], relations=[], unresolved=[])))
    assert post_chat(instance, "你好").status_code == 200
    assert len(gate.calls) == 1 and not memory.calls and len(answer.calls) == 1
    instance, gate, memory, answer, _ = app(tmp_path / "invalid", monkeypatch, gate_response="not JSON")
    assert post_chat(instance, "我用的录音机多重？").status_code == 200
    assert len(gate.calls) == len(memory.calls) == len(answer.calls) == 1
    query = memory.calls[0][0]
    assert query.intent.mode == "open" and query.text == "我用的录音机多重？"
    audit = json.loads((tmp_path / "invalid" / "decisions.jsonl").read_text())
    assert audit["fallback_reason"] and audit["query_interpretation"]["interpretation_status"] == "open_fallback"


def test_reader_factory_matches_candidate_and_default_writer_stays_v6(monkeypatch):
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    calls = []
    monkeypatch.setattr(MagmaMemoryAdapter, "create_real", lambda *args, **kwargs: calls.append(kwargs) or object())
    formation, cold = Answer(), object()
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "graph-read-v2")
    app_module._build_memory_retriever(formation, cold)
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "llm")
    app_module._build_memory_retriever(formation, cold)
    assert [call["associative_read_profile"] for call in calls] == ["graph-read-v2", "reliable-v2"]
    assert all(call["formation_model"] is formation and call["cold_store"] is cold
               and call["ingestion_version"] == "grounded-formation-v6" for call in calls)
    assert calls[0]["first_hit"] == calls[1]["first_hit"]


def test_candidate_mock_or_missing_client_has_audited_open_fallback(monkeypatch):
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "graph-read-v2")
    gate = app_module._default_mind_gate(MockModelClient())
    assert isinstance(gate, LlmQueryMindGate)
    assert gate.decide("question", []).audit["provider_call_attempts"] == 0
    def fail(**kwargs):
        raise RuntimeError("private configuration error")
    monkeypatch.setattr(app_module, "build_model_client_from_env", fail)
    gate = app_module._default_mind_gate(Answer())
    assert gate.decide("question", []).audit["fallback_reason"] == "query_gate_client_unavailable"


def test_candidate_rejects_an_extra_semantic_selector(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "graph-read-v2")
    with pytest.raises(ValueError, match="cannot also select"):
        app_module.create_app(model_client=Answer(), memory_retriever=Memory(),
                              evidence_selector=object(), env_file_path=None)


@pytest.mark.parametrize("recall", [True, False])
def test_candidate_audit_failure_preserves_accepted_conditions_or_decline(tmp_path, monkeypatch, recall):
    response = interpreted() if recall else dict(recall=False, mode="open", clues=[], relations=[], unresolved=[])
    instance, gate, memory, answer, _ = app(tmp_path, monkeypatch,
        gate_response=json.dumps(response, ensure_ascii=False))
    class FailingLog:
        def __init__(self):
            self.calls = []
        def record(self, decision, **kwargs):
            self.calls.append((decision, kwargs))
            raise OSError("synthetic audit append failure")
    log = FailingLog()
    instance.state.message_runtime._mind_decision_log = log
    assert post_chat(instance, "我用的录音机多重？").status_code == 200
    assert len(gate.calls) == len(answer.calls) == 1
    assert len(log.calls) == 2
    assert all(decision.recall is recall for decision, _ in log.calls)
    if recall:
        assert len(memory.calls) == 1
        assert memory.calls[0][0].intent.mode == "precise"
        assert len(memory.calls[0][0].intent.relations) == 2
        assert memory.calls[0][0] is log.calls[0][0].query
    else:
        assert not memory.calls
