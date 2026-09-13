"""Actual selector/Chat contracts; model semantics are measured separately."""
import json
from pathlib import Path

import pytest

from Conversation_Memory.adapter.models import MemoryContext, MemoryEvidence, RecallPolicy, SourceProvenance
from core.contracts import ChatRequest
from core.draft_store import JsonlDraftStore
from core.message_runtime import MessageRuntime, _SELECTED_EVIDENCE_GUIDANCE
from Mind.constant_gate import ConstantMindGate
from Mind.decision_log import JsonlDecisionLog
from Mind.evidence_selector import LlmEvidenceSelector, _parse_selection


QUESTION = "Which recorder does that engineer use?"
NEAR = [{"role": "user", "text": "I mean Mira, the engineer."},
        {"role": "assistant", "text": "My unverified guess is Wren."}]


class Model:
    client_kind = "model"

    def __init__(self, text="answer"):
        self.text, self.calls = text, []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


class Prepared:
    def __init__(self):
        evidence = tuple(MemoryEvidence(eid, text, None, SourceProvenance(
            "segment", "conversation", eid, "user", "2026-09-01T00:00:00+00:00", "UTC", "v1",
        )) for eid, text in (("occupation", "Mira is an engineer."),
                            ("device", "Mira uses Fable."),
                            ("distractor", "Another Mira uses Wren.")))
        self.selection_items = tuple((item.evidence_id, f"[USER | subject_binding=I{3 if i < 2 else 1}]\n{item.text}")
                                     for i, item in enumerate(evidence))
        self.context = MemoryContext(QUESTION, evidence, "\n".join(block for _, block in self.selection_items), True)
        self.selected = MemoryContext(QUESTION, evidence[:2], "\n".join(block for _, block in self.selection_items[:2]), True)
        self.calls = []

    def subset(self, ids):
        self.calls.append(ids)
        if ids == ("occupation", "device"):
            return self.selected
        if ids == ():
            return MemoryContext(QUESTION, truncated=True)
        raise ValueError("invalid selection")


class Memory:
    def __init__(self, prepared):
        self.prepared, self.calls = prepared, []

    def prepare_recall(self, query, policy):
        self.calls.append((query, policy))
        if isinstance(self.prepared, Exception):
            raise self.prepared
        return self.prepared

    def recall(self, query, policy):
        raise AssertionError("must not perform a second read")


class Context:
    def get_recent_context(self):
        return [dict(item) for item in NEAR]


def runtime(tmp_path, *, prepared=None, raw="[1,2]", enabled=True, log=None):
    prepared = prepared if prepared is not None else Prepared()
    memory, answer, selector_model = Memory(prepared), Model(), Model(raw)
    hot = JsonlDraftStore(tmp_path / "hot.jsonl")
    instance = MessageRuntime(
        hot_store=hot, draft_context_provider=Context(), model_client=answer,
        chat_background="fixed background", recall_enabled=enabled,
        memory_retriever=memory, recall_policy=RecallPolicy(max_nodes=20, max_chars=5000, include_source_context=True),
        evidence_selector=LlmEvidenceSelector(selector_model), mind_decision_log=log,
    )
    return instance, memory, answer, selector_model, hot


def test_original_conversation_one_read_and_owner_subset_are_preserved(tmp_path):
    prepared = Prepared()
    log_path = tmp_path / "decisions.jsonl"
    instance, memory, answer, selector, hot = runtime(tmp_path, prepared=prepared, log=JsonlDecisionLog(log_path))
    result = instance.handle_chat(ChatRequest(message=QUESTION))
    assert len(memory.calls) == len(answer.calls) == len(selector.calls) == 1
    assert memory.calls[0][0] == QUESTION
    assert prepared.calls == [("occupation", "device")]
    payload = json.loads(selector.calls[0][1])
    assert payload["original_message"] == QUESTION and payload["recent_context"] == NEAR
    assert payload["evidence_items"] == [{"id": i, "evidence": block} for i, (_, block) in enumerate(prepared.selection_items, 1)]
    assert "evidence_id" not in selector.calls[0][1]
    assert answer.calls[0][:2] == (NEAR, QUESTION)
    prompt = answer.calls[0][2]
    assert prepared.selected.rendered_text in prompt
    assert "Another Mira" not in prompt and "subject_binding=I3" in prompt
    assert _SELECTED_EVIDENCE_GUIDANCE in prompt
    assert result.recent_context == NEAR and "memory_evidence_selected" in result.events
    assert [turn.text for turn in hot.list_all_raw()] == [QUESTION, "answer"]
    row = json.loads(log_path.read_text(encoding="utf-8"))
    assert row["selected_evidence_ids"] == ["occupation", "device"]
    assert row["original_message"] == row["effective_query"] == QUESTION
    assert row["fallback_reason"] is None


@pytest.mark.parametrize("raw", ["[4]", "[1,1]", "[true]", "null", "not json", RuntimeError("private provider details")])
def test_bad_selector_falls_back_to_the_same_complete_context(tmp_path, raw):
    prepared = Prepared()
    instance, memory, answer, selector, _ = runtime(tmp_path, prepared=prepared, raw=raw)
    result = instance.handle_chat(ChatRequest(message=QUESTION))
    assert len(memory.calls) == len(selector.calls) == len(answer.calls) == 1
    assert prepared.context.rendered_text in answer.calls[0][2]
    assert "memory_evidence_selection_failed" in result.events
    assert "private provider details" not in str(result.response)


def test_unknown_owner_id_is_selection_failure_not_empty_success(tmp_path):
    class ForeignSelector:
        def select(self, *args): return ("from-another-prepared-view",)
    prepared = Prepared()
    instance, memory, answer, selector, _ = runtime(tmp_path, prepared=prepared)
    instance._evidence_selector = ForeignSelector()
    result = instance.handle_chat(ChatRequest(message=QUESTION))
    assert len(memory.calls) == 1 and not selector.calls
    assert prepared.context.rendered_text in answer.calls[0][2]
    assert "memory_evidence_selection_failed" in result.events


def test_selection_log_failure_keeps_original_context_without_another_read(tmp_path):
    class FailingLog:
        def __init__(self): self.calls = []
        def record(self, decision, **kwargs):
            self.calls.append(kwargs)
            raise OSError("private log path")
    log = FailingLog()
    prepared = Prepared()
    instance, memory, answer, _, _ = runtime(tmp_path, prepared=prepared, log=log)
    result = instance.handle_chat(ChatRequest(message=QUESTION))
    assert len(log.calls) == 2 and len(memory.calls) == 1
    assert log.calls[-1]["query_audit"]["fallback_reason"] == "decision_log_failed"
    assert prepared.context.rendered_text in answer.calls[0][2]
    assert "mind_decision_log_failed" in result.events


def test_explicit_empty_selection_uses_original_question_without_historical_block(tmp_path):
    instance, memory, answer, selector, _ = runtime(tmp_path, raw="[]")
    result = instance.handle_chat(ChatRequest(message=QUESTION))
    assert len(memory.calls) == len(selector.calls) == 1
    assert answer.calls[0] == (NEAR, QUESTION, "fixed background")
    assert "memory_evidence_selected" in result.events


@pytest.mark.parametrize("state", ["disabled", "empty", "error", "exception"])
def test_unavailable_or_disabled_memory_does_not_call_selector(tmp_path, state):
    prepared = Prepared()
    if state == "empty": prepared.context = MemoryContext(QUESTION)
    if state == "error": prepared.context = MemoryContext(QUESTION, safe_error_code="recall_unavailable")
    if state == "exception": prepared = RuntimeError("private backend path")
    instance, memory, answer, selector, _ = runtime(tmp_path, prepared=prepared, enabled=state != "disabled")
    result = instance.handle_chat(ChatRequest(message=QUESTION))
    assert not selector.calls
    assert len(memory.calls) == (0 if state == "disabled" else 1)
    assert answer.calls[0] == (NEAR, QUESTION, "fixed background")
    if state in {"error", "exception"}: assert "memory_recall_failed" in result.events


def test_pre_read_gate_cannot_be_stacked_with_selector(tmp_path):
    with pytest.raises(ValueError, match="one memory decision stage"):
        MessageRuntime(hot_store=JsonlDraftStore(tmp_path / "hot.jsonl"), draft_context_provider=Context(),
                       model_client=Model(), chat_background="background", mind_gate=ConstantMindGate(),
                       evidence_selector=LlmEvidenceSelector(Model("[]")))


@pytest.mark.parametrize("raw,expected", [("[]", ()), ("[2,1]", (2,1)), ("```json\n[1]\n```", (1,))])
def test_complete_selection_protocol(raw, expected):
    assert _parse_selection(raw, 2) == expected


def test_selector_refuses_unbounded_input_before_model_call():
    model = Model("[]")
    selector = LlmEvidenceSelector(model)
    with pytest.raises(ValueError, match="bounds"):
        selector.select(QUESTION, NEAR, tuple((str(i), "x") for i in range(21)))
    with pytest.raises(ValueError, match="bounds"):
        selector.select(QUESTION, NEAR, (("one", "x" * 5001),))
    assert not model.calls


def test_unavailable_prepared_metadata_keeps_the_original_read(tmp_path):
    class Unavailable:
        context = Prepared().context
        @property
        def selection_items(self):
            raise ValueError("prepared_recall_unavailable")
    prepared = Unavailable()
    instance, memory, answer, selector, _ = runtime(tmp_path, prepared=prepared)
    result = instance.handle_chat(ChatRequest(message=QUESTION))
    assert len(memory.calls) == 1 and not selector.calls
    assert prepared.context.rendered_text in answer.calls[0][2]
    assert "memory_evidence_selection_failed" in result.events


def test_legacy_facade_falls_back_without_attempting_selector(tmp_path):
    class Legacy:
        calls = 0
        def recall(self, query, policy):
            self.calls += 1
            return Prepared().context
    instance, _, answer, selector, _ = runtime(tmp_path)
    memory = Legacy()
    instance._memory_retriever = memory
    result = instance.handle_chat(ChatRequest(message=QUESTION))
    assert memory.calls == 1 and not selector.calls
    assert Prepared().context.rendered_text in answer.calls[0][2]
    assert "memory_selection_unavailable" in result.events


@pytest.mark.parametrize("enabled", [True, False])
def test_select_mode_uses_one_bounded_model_after_read_and_ignores_gate(tmp_path, monkeypatch, enabled):
    from fastapi.testclient import TestClient
    import core.main as main
    class ForbiddenGate:
        def decide(self, *args): raise AssertionError("no preread gate")
    selector_model, calls = Model("[1,2]"), []
    def build(**kwargs):
        calls.append(kwargs)
        assert enabled, "disabled Recall must not construct the selector client"
        return selector_model
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "select")
    monkeypatch.setattr(main, "build_model_client_from_env", build)
    monkeypatch.setattr(main, "_default_mind_gate", lambda *a: pytest.fail("preread gate constructed"))
    memory, answer = Memory(Prepared()), Model()
    app = main.create_app(
        draft_store_path=tmp_path / "hot.jsonl", cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json", mind_decision_log_path=tmp_path / "decisions.jsonl",
        execution_root=tmp_path / "execution", model_client=answer, env_file_path=None,
        enable_compaction=False, recall_enabled=enabled, memory_retriever=memory, mind_gate=ForbiddenGate(),
    )
    result = TestClient(app).post("/api/chat", json={"message": QUESTION})
    assert result.status_code == 200 and result.json()["response"]["text"] == "answer"
    assert len(memory.calls) == len(selector_model.calls) == int(enabled)
    if enabled:
        assert calls == [{"max_tokens_override": 1024, "temperature_override": 0.0}]
        policy = memory.calls[0][1]
        assert policy.max_nodes == policy.max_evidence_items == 20
        assert policy.max_graph_depth == 1 and policy.max_chars == 5000
        assert policy.final_min_score is None and policy.include_source_context
    else:
        assert not calls
