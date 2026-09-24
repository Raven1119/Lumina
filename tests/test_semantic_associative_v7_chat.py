"""V7's real Chat route uses one memory-use contract and V6 read ownership."""
import asyncio
import json

import fastapi.routing
import httpx
import pytest

from Mind.evidence_selector import LlmSemanticEvidenceSelectorV7
from core.main import create_app, _default_semantic_selector
from core.message_runtime import (
    _SEMANTIC_V3_ABSENCE_GUIDANCE, _V5_ANSWER_CONTRAST,
    _V6_ANSWER_GROUNDING, _SEMANTIC_V7_MEMORY_USE_GUIDANCE,
)
from core.model_client import MockModelClient
from tests.test_semantic_associative_v6_chat import Memory, Model


def test_actual_asgi_v7_single_contract_and_bounded_fact(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "semantic-associative-v7")
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "llm")
    memory = Memory()
    selector = Model(json.dumps({"ranked": [{"id": 1, "use": "history",
                                             "relation": "same_event"}]}),
                     json.dumps({"ranked": [{"id": 1, "use": "analogy",
                                             "relation": "similar_workflow"}]}))
    answer = Model("这件事仍是计划，不能说已经完成。")
    app = create_app(draft_store_path=tmp_path / "hot.jsonl",
                     cold_draft_path=tmp_path / "cold.jsonl",
                     compaction_state_path=tmp_path / "state.json",
                     mind_decision_log_path=tmp_path / "mind.jsonl",
                     execution_root=tmp_path / "execution", model_client=answer,
                     memory_retriever=memory,
                     semantic_selector=LlmSemanticEvidenceSelectorV7(selector),
                     recall_enabled=True, env_file_path=None, enable_compaction=False)

    async def inline(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", inline)

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            return await client.post("/api/chat", json={"message": "What should I do now?"})

    assert asyncio.run(invoke()).status_code == 200
    assert memory.traversals == 1 and len(selector.calls) == 2
    prompt = answer.calls[0][2]
    assert prompt.count(_SEMANTIC_V7_MEMORY_USE_GUIDANCE) == 1
    assert _SEMANTIC_V3_ABSENCE_GUIDANCE not in prompt
    assert _V5_ANSWER_CONTRAST not in prompt
    assert _V6_ANSWER_GROUNDING not in prompt
    assert "[Memory coverage: NON_EXHAUSTIVE_BOUNDED_VIEW]" in prompt
    assert "a only" in prompt and "b only" in prompt
    audits = [json.loads(line) for line in (tmp_path / "mind.jsonl").read_text().splitlines()]
    assert audits[0]["prompt_version"] == LlmSemanticEvidenceSelectorV7.prompt_version
    assert audits[1]["selected_evidence_ids"] == ["a", "b"]


def test_v7_factory():
    assert isinstance(_default_semantic_selector(MockModelClient(), version=7),
                      LlmSemanticEvidenceSelectorV7)


@pytest.mark.parametrize(("message", "bad_answer", "risk"), (
    ("那件旧事后来怎样？", "此前没有后续记录。", "global_absence_from_partial_recall"),
    ("我计划今天发布新卡片。", "新卡片已经公开发布了。", "completion_upgrade"),
))
def test_v7_real_wrapper_audits_but_does_not_rewrite_grounding_errors(
        tmp_path, monkeypatch, message, bad_answer, risk):
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "semantic-associative-v7")
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "llm")
    selector = Model(json.dumps({"ranked": [{"id": 1, "use": "history",
                                             "relation": "same_event"}]}),
                     '{"ranked":[]}')
    app = create_app(draft_store_path=tmp_path / "hot.jsonl",
                     cold_draft_path=tmp_path / "cold.jsonl",
                     compaction_state_path=tmp_path / "state.json",
                     mind_decision_log_path=tmp_path / "mind.jsonl",
                     execution_root=tmp_path / "execution", model_client=Model(bad_answer),
                     memory_retriever=Memory(),
                     semantic_selector=LlmSemanticEvidenceSelectorV7(selector),
                     recall_enabled=True, env_file_path=None, enable_compaction=False)

    async def inline(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", inline)

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            return await client.post("/api/chat", json={"message": message})

    assert asyncio.run(invoke()).json()["response"]["text"] == bad_answer
    audits = [json.loads(line) for line in (tmp_path / "mind.jsonl").read_text().splitlines()]
    assert risk in audits[-1]["grounding_risks"]
