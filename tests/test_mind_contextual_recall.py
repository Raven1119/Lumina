"""Frozen wiring/protocol controls for contextual Recall; no semantic score claims."""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from Conversation_Memory.adapter.models import RecallPolicy
from core.contracts import ChatRequest
from core.draft_store import JsonlDraftStore
from core.message_runtime import MessageRuntime
from Mind import interfaces
from Mind.constant_gate import ConstantMindGate
from Mind.decision_log import JsonlDecisionLog
from Mind.interfaces import MindDecision
from Mind.llm_gate import LlmMindGate


@pytest.fixture(autouse=True)
def isolate_contextual_state(tmp_path, monkeypatch):
    assert tmp_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    monkeypatch.setenv("LUMINA_MIND_DECISION_LOG_PATH", str(tmp_path / "mind_decisions.jsonl"))


class GateModel:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append(copy.deepcopy((recent_context, user_message, system_prompt)))
        if isinstance(self.output, Exception):
            raise self.output
        return self.output if isinstance(self.output, str) else json.dumps(self.output, ensure_ascii=False)


class RecentContext:
    def __init__(self, items):
        self.items, self.calls = items, 0

    def get_recent_context(self):
        self.calls += 1
        return copy.deepcopy(self.items)


class AnswerModel:
    client_kind = "model"

    def __init__(self):
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append(copy.deepcopy((recent_context, user_message, system_prompt)))
        return "Controlled answer."


class Retriever:
    def __init__(self):
        self.calls = []

    def recall(self, query, policy):
        self.calls.append((query, policy))
        return SimpleNamespace(rendered_text="Read-only fixture evidence.", safe_error_code=None)


def proposal(query="How heavy is Plover-67?", *, index=0, span="Plover-67"):
    return {"recall": True, "query": query, "context_refs": [{"index": index, "span": span}]}


def make_runtime(tmp_path, output, *, context=None, summary=None, logger=True, gate=None):
    class Hot(JsonlDraftStore):
        def read_summary(self):
            return None if summary is None else SimpleNamespace(content=summary)

    hot = Hot(tmp_path / "hot.jsonl")
    provider = RecentContext(context if context is not None else [{"role": "user", "text": "Let's discuss Plover-67."}])
    gate_model, answer, retriever = GateModel(output), AnswerModel(), Retriever()
    effective_gate = gate if gate is not None else LlmMindGate(gate_model, contextual=True)
    log_path = tmp_path / "decisions.jsonl"
    log = JsonlDecisionLog(log_path) if logger is True else (None if logger is False else logger)
    policy = RecallPolicy(top_k=10, max_graph_depth=1, max_nodes=20, max_evidence_items=3,
                          max_chars=5000, final_min_score=0.144)
    runtime = MessageRuntime(hot_store=hot, draft_context_provider=provider, model_client=answer,
        chat_background="Unchanged answer background.", recall_enabled=True, memory_retriever=retriever,
        recall_policy=policy, mind_gate=effective_gate, mind_decision_log=log)
    return SimpleNamespace(runtime=runtime, hot=hot, provider=provider, gate_model=gate_model,
                           gate=effective_gate, answer=answer, retriever=retriever, policy=policy, log_path=log_path)


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("field,message,context,query,span", [
    ("message", "  How heavy is it?  ", "Let's discuss Plover-67.", "How heavy is Plover-67?", "Plover-67"),
    ("text", "它的尺寸是多少？", "接着讨论赤杉-26。", "赤杉-26的尺寸是多少？", "赤杉-26"),
])
def test_rewrite_only_changes_actual_recall_query(field, message, context, query, span, tmp_path):
    view = [{"role": "user", "text": context}]
    bundle = make_runtime(tmp_path, proposal(query, span=span), context=view)
    result = bundle.runtime.handle_chat(ChatRequest(**{field: message}))
    assert bundle.retriever.calls == [(query, bundle.policy)]
    assert bundle.provider.calls == len(bundle.gate_model.calls) == len(bundle.answer.calls) == 1
    assert bundle.answer.calls[0][0] == view and bundle.answer.calls[0][1] == message
    assert bundle.hot.list_recent(2)[0].text == message
    assert query not in bundle.answer.calls[0][2]
    assert result.recent_context == view
    row, = records(bundle.log_path)
    assert row["original_message"] == message
    assert row["candidate_query"] == row["effective_query"] == query
    assert row["context_refs"] == [{"index": 0, "role": "user", "span": span}]
    assert row["prompt_version"] == bundle.gate.prompt_version and row["fallback_reason"] is None


def test_source_indices_keep_summary_position_and_original_answer_view(tmp_path):
    view = [{"role": "assistant", "text": "We were discussing Plover-67."}]
    bundle = make_runtime(tmp_path, proposal(index=1), context=view, summary="Unverified rolling summary about Birch-15.")
    result = bundle.runtime.handle_chat(ChatRequest(message="How heavy is it?"))
    assert bundle.retriever.calls == [("How heavy is Plover-67?", bundle.policy)]
    assert result.recent_context[0]["role"] == "summary" and result.recent_context[1] == view[0]
    assert bundle.answer.calls[0][0] == result.recent_context
    assert records(bundle.log_path)[0]["context_refs"] == [{"index": 1, "role": "assistant", "span": "Plover-67"}]
    assert bundle.provider.calls == len(bundle.gate_model.calls) == 1


INVALID_PROPOSALS = [
    {**proposal(), "recall": "true"},
    {**proposal(), "recall": False},
    {**proposal(), "unexpected": "field"},
    {**proposal(), "query": 17},
    proposal("Plover-67 " + "x" * 247),
    {**proposal(), "context_refs": []},
    {**proposal(), "context_refs": [{"index": 0, "span": "Plover-67"}] * 3},
    proposal(index=True),
    proposal(index=-1),
    proposal(index=2),
    proposal(span="missing source"),
    proposal("What is the color?"),
    {**proposal(), "context_refs": [{"index": 0, "span": "Plover-67", "role": "user"}]},
    "{broken json",
    " " * 2049 + "true",
]


@pytest.mark.parametrize("output", INVALID_PROPOSALS, ids=[
    "strict_bool", "decline_with_query", "unknown_field", "query_type", "query_overflow",
    "missing_ref", "ref_count", "boolean_index", "negative_index", "absent_index",
    "absent_span", "span_not_in_query", "model_cannot_supply_role", "malformed_json", "raw_overflow",
])
def test_invalid_protocol_fails_open_with_an_audited_original_query(tmp_path, output):
    message = "How heavy is it?"
    bundle = make_runtime(tmp_path, output)
    result = bundle.runtime.handle_chat(ChatRequest(message=message))
    assert bundle.retriever.calls == [(message, bundle.policy)]
    assert len(bundle.gate_model.calls) == len(bundle.answer.calls) == 1
    assert bundle.answer.calls[0][1] == bundle.hot.list_recent(2)[0].text == message
    row, = records(bundle.log_path)
    assert row["recall"] is True and row["effective_query"] == row["original_message"] == message
    assert row["fallback_reason"] and row["context_refs"] == []
    assert result.response.status == "ok"


@pytest.mark.parametrize("source", ["summary", "long_span"])
def test_summary_or_overlong_span_cannot_authorize_query(tmp_path, source):
    if source == "summary":
        output = proposal("How heavy is Birch-15?", index=0, span="Birch-15")
        bundle = make_runtime(tmp_path, output, summary="Birch-15 is only present in this rolling summary.")
    else:
        span = "M" * 97
        output = proposal("How heavy is " + span + "?", span=span)
        bundle = make_runtime(tmp_path, output, context=[{"role": "user", "text": span}])
    bundle.runtime.handle_chat(ChatRequest(message="How heavy is it?"))
    assert bundle.retriever.calls == [("How heavy is it?", bundle.policy)]
    row, = records(bundle.log_path)
    assert row["candidate_query"] == output["query"] and row["fallback_reason"]
    assert row["effective_query"] == "How heavy is it?"


def test_provider_failure_records_fallback_without_a_second_gate_call(tmp_path):
    bundle = make_runtime(tmp_path, RuntimeError("controlled provider failure"))
    bundle.runtime.handle_chat(ChatRequest(message="How heavy is it?"))
    assert len(bundle.gate_model.calls) == 1
    assert bundle.retriever.calls == [("How heavy is it?", bundle.policy)]
    row, = records(bundle.log_path)
    assert row["candidate_query"] is None and row["effective_query"] == "How heavy is it?"
    assert row["fallback_reason"] and row["recall"] is True


@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("decline", [False, True])
def test_logging_failure_never_executes_an_unaudited_query_or_decline(tmp_path, after_write, decline):
    class FailingLog(JsonlDecisionLog):
        attempts = 0
        def record(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                if after_write:
                    super().record(*args, **kwargs)
                raise OSError("controlled audit failure")
            super().record(*args, **kwargs)

    path = tmp_path / "decisions.jsonl"
    logger = FailingLog(path)
    output = {"recall": False, "query": None, "context_refs": []} if decline else proposal()
    bundle = make_runtime(tmp_path, output, logger=logger)
    result = bundle.runtime.handle_chat(ChatRequest(message="How heavy is it?"))
    assert bundle.retriever.calls == [("How heavy is it?", bundle.policy)]
    assert len(bundle.gate_model.calls) == 1 and logger.attempts == 2
    assert "mind_decision_log_failed" in result.events
    last = records(path)[-1]
    assert last["recall"] is True and last["effective_query"] == "How heavy is it?"
    assert last["fallback_reason"] and last["context_refs"] == []


def test_absent_logger_disables_only_new_rewrite_not_legacy_boolean_contract(tmp_path):
    rewritten = make_runtime(tmp_path / "rewrite", proposal(), logger=False)
    rewritten.runtime.handle_chat(ChatRequest(message="How heavy is it?"))
    assert rewritten.retriever.calls == [("How heavy is it?", rewritten.policy)]
    declined = make_runtime(tmp_path / "decline", "false", logger=False)
    declined.runtime.handle_chat(ChatRequest(message="hello"))
    assert declined.retriever.calls == []


@pytest.mark.parametrize("guard", ["disabled", "no_retriever", "no_policy"])
def test_recall_guards_keep_one_gate_and_original_answer(guard, tmp_path):
    bundle = make_runtime(tmp_path, proposal())
    if guard == "disabled":
        bundle.runtime._recall_enabled = False
    elif guard == "no_retriever":
        bundle.runtime._memory_retriever = None
    else:
        bundle.runtime._recall_policy = None
    bundle.runtime.handle_chat(ChatRequest(message="How heavy is it?"))
    assert len(bundle.gate_model.calls) == len(bundle.answer.calls) == 1
    assert bundle.retriever.calls == [] and bundle.answer.calls[0][1] == "How heavy is it?"


@pytest.mark.parametrize("raw,allowed", [("true", True), ("  False。\n", False)])
def test_legacy_boolean_outputs_remain_compatible(raw, allowed):
    decision = LlmMindGate(GateModel(raw)).decide("hello", [])
    assert decision == MindDecision(recall=allowed)
    assert getattr(decision, "query", None) is None and getattr(decision, "context_refs", ()) == ()


def test_old_log_rows_and_direct_record_call_remain_readable(tmp_path):
    old = b'{"turn_id":"old","recall":true,"decided_at":"2026-01-01T00:00:00Z"}\n'
    path = tmp_path / "decisions.jsonl"
    path.write_bytes(old)
    log = JsonlDecisionLog(path)
    log.record(MindDecision(recall=True), turn_id="legacy-caller")
    bundle = make_runtime(tmp_path, "unused", gate=ConstantMindGate(), logger=log)
    bundle.runtime.handle_chat(ChatRequest(message="Self-contained question."))
    assert path.read_bytes().startswith(old)
    rows = records(path)
    assert set(rows[1]) == {"turn_id", "recall", "decided_at"}
    assert rows[2]["candidate_query"] is None and rows[2]["effective_query"] == "Self-contained question."
    assert bundle.retriever.calls == [("Self-contained question.", bundle.policy)]


def test_decision_defaults_preserve_old_constructor_and_refs_are_typed():
    decision = MindDecision(recall=True)
    assert decision.query is None and decision.context_refs == ()
    ref = interfaces.MindContextRef(index=0, span="Plover-67")
    rich = MindDecision(recall=True, query="How heavy is Plover-67?", context_refs=(ref,))
    assert rich.context_refs == (ref,)


def test_custom_gate_cannot_bypass_decision_type_validation(tmp_path):
    gate = SimpleNamespace(decide=lambda *_args: SimpleNamespace(recall=False, query=None, context_refs=()))
    bundle = make_runtime(tmp_path, "unused", gate=gate)
    bundle.runtime.handle_chat(ChatRequest(message="How heavy is it?"))
    assert bundle.retriever.calls == [("How heavy is it?", bundle.policy)]
    row, = records(bundle.log_path)
    assert row["fallback_reason"] and row["recall"] is True


def test_custom_gate_rejection_cannot_write_an_unbounded_candidate(tmp_path):
    class Gate:
        def decide(self, *_args):
            raise interfaces.MindDecisionError(
                "controlled_rejection", candidate_query="x" * 5000,
            )

    bundle = make_runtime(tmp_path, "unused", gate=Gate())
    bundle.runtime.handle_chat(ChatRequest(message="How heavy is it?"))
    assert bundle.retriever.calls == [("How heavy is it?", bundle.policy)]
    row, = records(bundle.log_path)
    assert row["candidate_query"] is None
    assert row["fallback_reason"] == "controlled_rejection"
    assert row["effective_query"] == "How heavy is it?"
