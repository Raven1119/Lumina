"""Opt-in semantic Recall crosses the real Chat boundary without a front gate."""
import asyncio
import json

import fastapi.routing
import httpx
import pytest
from Conversation_Memory.adapter.models import MemoryContext, MemoryEvidence, PreparedRecall, SourceProvenance
from Mind.evidence_selector import LlmSemanticEvidenceSelector, _parse_semantic_selection
from core.main import create_app


class Answer:
    client_kind = "model"

    def __init__(self):
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        return "answer"


class SelectionModel(Answer):
    def __init__(self, raw):
        super().__init__()
        self.raw = raw

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw


def prepared():
    provenance = SourceProvenance("s", "c", "t", "user", "2026-09-24T00:00:00+00:00",
                                  "UTC", "grounded-formation-v6")
    items = (MemoryEvidence("first", "User preferred a cue card for the earlier recording.", None, provenance),
             MemoryEvidence("other", "User attended a different reading group.", None, provenance))
    blocks = tuple(f"[M{i} USER]\n{item.text}" for i, item in enumerate(items, 1))
    return PreparedRecall(MemoryContext("question", items, "\n".join(blocks)), blocks,
                          (("first", ()), ("other", ())), (3, 5000, 20000))


class Memory:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def prepare_recall(self, query, policy):
        self.calls.append(query)
        return self.result

    def recall(self, *args):
        raise AssertionError("second or legacy read")


def chat(tmp_path, monkeypatch, raw, *, memory=None, choice_calls=1):
    import core.main as main
    monkeypatch.setattr(main, "_default_mind_gate", lambda *args: pytest.fail("boolean gate constructed"))
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "semantic-associative-v1")
    monkeypatch.delenv("LUMINA_MIND_GATE_MODE", raising=False)
    answer = Answer()
    choice = SelectionModel(raw)
    memory = memory or Memory(prepared())
    app = create_app(draft_store_path=tmp_path / "hot.jsonl", cold_draft_path=tmp_path / "cold.jsonl",
                     compaction_state_path=tmp_path / "state.json", mind_decision_log_path=tmp_path / "mind.jsonl",
                     execution_root=tmp_path / "execution", model_client=answer, env_file_path=None,
                     enable_compaction=False, recall_enabled=True, memory_retriever=memory,
                     semantic_selector=LlmSemanticEvidenceSelector(choice))
    async def inline_worker(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", inline_worker)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/chat", json={"message": "I changed my plan; does the old recording help?"})
    response = asyncio.run(request())
    assert response.status_code == 200 and response.json()["response"]["text"] == "answer"
    assert len(answer.calls) == 1 and len(choice.calls) == choice_calls
    return answer.calls[0][2], json.loads((tmp_path / "mind.jsonl").read_text()), response


def test_chat_passes_only_selected_canonical_fact_with_analogy_role(tmp_path, monkeypatch):
    prompt, audit, _ = chat(tmp_path, monkeypatch, '{"selected":[{"id":1,"use":"analogy"}]}')
    assert "User preferred a cue card" in prompt
    assert "different reading group" not in prompt
    assert "not a record of the current event" in prompt
    assert audit["selected_evidence_ids"] == ["first"]


@pytest.mark.parametrize("raw", ["broken", '{"selected":[{"id":3,"use":"history"}]}',
                                  RuntimeError("timeout")])
def test_chat_selection_failure_never_restores_panel(tmp_path, monkeypatch, raw):
    prompt, audit, _ = chat(tmp_path, monkeypatch, raw)
    assert "User preferred a cue card" not in prompt
    assert "different reading group" not in prompt
    assert audit["fallback_reason"] == "semantic_selection_failed"


def test_chat_log_failure_keeps_only_legal_subset(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "semantic-associative-v1")
    monkeypatch.delenv("LUMINA_MIND_GATE_MODE", raising=False)
    answer, choice = Answer(), SelectionModel('{"selected":[{"id":1,"use":"analogy"}]}')
    app = create_app(draft_store_path=tmp_path / "hot.jsonl", cold_draft_path=tmp_path / "cold.jsonl",
                     compaction_state_path=tmp_path / "state.json", mind_decision_log_path=tmp_path / "mind.jsonl",
                     execution_root=tmp_path / "execution", model_client=answer, env_file_path=None,
                     enable_compaction=False, recall_enabled=True, memory_retriever=Memory(prepared()),
                     semantic_selector=LlmSemanticEvidenceSelector(choice))
    class FailingLog:
        def record(self, *args, **kwargs): raise OSError("audit disk unavailable")
    app.state.message_runtime._mind_decision_log = FailingLog()
    async def inline_worker(func, *args, **kwargs): return func(*args, **kwargs)
    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", inline_worker)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/chat", json={"message": "Can the old recording help?"})
    response = asyncio.run(request())
    assert response.status_code == 200 and len(choice.calls) == 1
    assert "cue card" in answer.calls[0][2]
    assert "different reading group" not in answer.calls[0][2]


def test_chat_missing_preparation_does_not_use_legacy_full_context(tmp_path, monkeypatch):
    class LegacyMemory:
        calls = ()
        def recall(self, *args):
            raise AssertionError("semantic route must not call legacy recall")
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "semantic-associative-v1")
    monkeypatch.delenv("LUMINA_MIND_GATE_MODE", raising=False)
    answer = Answer()
    choice = SelectionModel('{"selected":[{"id":1,"use":"history"}]}')
    app = create_app(draft_store_path=tmp_path / "hot.jsonl", cold_draft_path=tmp_path / "cold.jsonl",
                     compaction_state_path=tmp_path / "state.json", mind_decision_log_path=tmp_path / "mind.jsonl",
                     execution_root=tmp_path / "execution", model_client=answer, env_file_path=None,
                     enable_compaction=False, recall_enabled=True, memory_retriever=LegacyMemory(),
                     semantic_selector=LlmSemanticEvidenceSelector(choice))
    async def inline_worker(func, *args, **kwargs): return func(*args, **kwargs)
    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", inline_worker)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/chat", json={"message": "What happened last time?"})
    response = asyncio.run(request())
    assert response.status_code == 200 and len(answer.calls) == 1 and not choice.calls
    assert "<BEGIN_EXACT_GROUNDED_SPANS>" not in answer.calls[0][2]


def test_semantic_subset_rejects_over_budget_and_foreign_ids():
    result = prepared()
    assert result.semantic_subset(()).rendered_text == ""
    with pytest.raises(ValueError):
        result.semantic_subset((("foreign", "history"),))
    with pytest.raises(ValueError):
        result.semantic_subset((("first", "history"), ("first", "analogy")))
    with pytest.raises(ValueError):
        result.semantic_subset((("first", "unsupported"),))
    with pytest.raises(ValueError):
        result.semantic_subset((("first", []),))


def test_semantic_protocol_rejects_free_text_and_invalid_uses():
    assert _parse_semantic_selection('{"selected":[{"id":2,"use":"history"}]}', 2) == ((2, "history"),)
    for raw in ('[1]', '{"selected":[{"id":1,"use":"history","reason":"guess"}]}',
                '{"selected":[{"id":1,"use":"other"}]}',
                '{"selected":[{"id":1,"use":[]}]}'):
        with pytest.raises(ValueError):
            _parse_semantic_selection(raw, 2)


def test_semantic_profile_rejects_stacked_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "semantic-associative-v1")
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "select")
    with pytest.raises(ValueError, match="semantic_memory_gate_profile_conflict"):
        create_app(draft_store_path=tmp_path / "hot.jsonl", model_client=Answer(),
                   env_file_path=None, recall_enabled=False, memory_retriever=Memory(prepared()))
