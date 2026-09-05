from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError
from inspect import signature
from pathlib import Path

import pytest

from Conversation_Memory.adapter.models import MemoryContext
from Mind import directive as directive_module
from Mind.directive import (
    DirectiveApplication,
    DirectiveState,
    prepare_for_execution_decision,
    project_directive_state,
)
from Mind.experiment_a import (
    ActivationFailure,
    ActivationInput,
    DecisionIntent,
    Directive,
    NoChange,
    run_activation,
)
from Mind.trace import (
    ACTIVATION_FINISHED,
    ACTIVATION_STARTED,
    MIND_DIRECTIVE_APPLIED,
    MIND_DIRECTIVE_ISSUED,
    MODEL_OUTPUT_RECORDED,
    MindTrace,
    TraceError,
    replay_activation,
)


FIXED_TIMESTAMP = "2026-08-30T12:00:00.000000Z"
ACTIVATION_ID = "experiment-d-activation"
DIRECTIVE_TEXT = "Re-evaluate the disproven assumption before continuing."
ACTIVATION = ActivationInput(
    trigger="Execution reported a meaningful transition.",
    execution_goal_snapshot="Investigate the Recall regression.",
    execution_status="running",
)


class ScriptedModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        del recent_context, user_message, system_prompt
        return self._responses.pop(0)


class FakeMemoryRetriever:
    def __init__(self, context: MemoryContext | None = None) -> None:
        self._context = context or MemoryContext(query="")

    def recall(self, query: str, policy: object) -> MemoryContext:
        del query, policy
        return self._context


def _new_trace(path: Path) -> MindTrace:
    return MindTrace.create(
        path,
        activation_id=ACTIVATION_ID,
        fixed_timestamp=FIXED_TIMESTAMP,
    )


def _run_result(path: Path, raw: str) -> tuple[object, MindTrace]:
    trace = _new_trace(path)
    result = run_activation(
        ACTIVATION,
        model=ScriptedModel([raw]),
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
        trace=trace,
    )
    return result, trace


def _run_directive(path: Path) -> tuple[Directive, MindTrace]:
    result, trace = _run_result(
        path,
        json.dumps({"type": "directive", "text": DIRECTIVE_TEXT}),
    )
    assert result == Directive(DIRECTIVE_TEXT)
    return result, trace


def _documents(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _write_documents(path: Path, documents: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(
            json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for document in documents
        ),
        encoding="utf-8",
    )


def test_d1_directive_issue_is_the_single_durable_semantic_terminal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pending.jsonl"
    result, trace = _run_directive(path)

    assert result == Directive(DIRECTIVE_TEXT)
    assert [event.event_type for event in trace.events] == [
        ACTIVATION_STARTED,
        MODEL_OUTPUT_RECORDED,
        MIND_DIRECTIVE_ISSUED,
    ]
    assert ACTIVATION_FINISHED not in {
        event.event_type for event in trace.events
    }
    issue = trace.events[-1]
    assert dict(issue.payload) == {
        "directive_id": f"{ACTIVATION_ID}:directive:2",
        "text": DIRECTIVE_TEXT,
    }
    assert issue.source_event_seqs == (1,)

    original_bytes = path.read_bytes()
    reopened = MindTrace.reopen(path)
    replay = replay_activation(reopened.events)

    assert project_directive_state(reopened.events) is DirectiveState.PENDING
    assert dict(replay.final_result or {}) == {
        "text": DIRECTIVE_TEXT,
        "type": "directive",
    }
    assert path.read_bytes() == original_bytes


def test_d1_directive_is_not_returned_if_issue_cannot_be_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = _new_trace(tmp_path / "issue-failure.jsonl")
    original_append = MindTrace.append

    def fail_issue(
        self: MindTrace,
        event_type: str,
        payload: object,
        *,
        source_event_seqs: object,
    ) -> object:
        if event_type == MIND_DIRECTIVE_ISSUED:
            raise TraceError("trace_append_failed")
        return original_append(
            self,
            event_type,
            payload,  # type: ignore[arg-type]
            source_event_seqs=source_event_seqs,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(MindTrace, "append", fail_issue)

    result = run_activation(
        ACTIVATION,
        model=ScriptedModel(
            [json.dumps({"type": "directive", "text": DIRECTIVE_TEXT})]
        ),
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
        trace=trace,
    )

    assert result == ActivationFailure("trace_failed")
    assert [event.event_type for event in trace.events] == [
        ACTIVATION_STARTED,
        MODEL_OUTPUT_RECORDED,
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"type":"no_change"}', NoChange()),
        (
            '{"type":"decision_intent","intent":"Revisit the Intention."}',
            DecisionIntent("Revisit the Intention."),
        ),
    ],
    ids=["no-change", "decision-intent"],
)
def test_d2_non_directive_results_create_no_delivery_state(
    tmp_path: Path,
    raw: str,
    expected: object,
) -> None:
    result, trace = _run_result(tmp_path / f"{type(expected).__name__}.jsonl", raw)

    assert result == expected
    assert trace.events[-1].event_type == ACTIVATION_FINISHED
    assert project_directive_state(trace.events) is None


def test_d3_ineligible_decision_leaves_directive_pending_without_append(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ineligible.jsonl"
    _run_directive(path)
    trace = MindTrace.reopen_for_delivery(path)
    original_bytes = path.read_bytes()

    application = prepare_for_execution_decision(
        trace,
        "decision-ineligible",
        eligible=False,
    )

    assert application is None
    assert project_directive_state(trace.events) is DirectiveState.PENDING
    assert path.read_bytes() == original_bytes


def test_d4_first_eligible_decision_is_durably_bound_before_return(
    tmp_path: Path,
) -> None:
    path = tmp_path / "first-eligible.jsonl"
    _, trace = _run_directive(path)

    application = prepare_for_execution_decision(trace, "decision-A")

    assert application == DirectiveApplication(
        directive_id=f"{ACTIVATION_ID}:directive:2",
        decision_id="decision-A",
        text=DIRECTIVE_TEXT,
    )
    with pytest.raises(FrozenInstanceError):
        application.text = "mutated"  # type: ignore[union-attr,misc]
    assert project_directive_state(trace.events) is DirectiveState.CONSUMED
    applied = trace.events[-1]
    assert applied.event_type == MIND_DIRECTIVE_APPLIED
    assert dict(applied.payload) == {
        "decision_id": "decision-A",
        "directive_id": f"{ACTIVATION_ID}:directive:2",
    }
    assert applied.source_event_seqs == (2,)
    context = application.as_model_context()  # type: ignore[union-attr]
    assert context.startswith("[Mind Supervisor Directive]\n")
    assert "high-level guidance" in context
    assert "strong advisory prior" in context
    assert "not an order" in context
    assert "execution plan" in context
    assert context.endswith(DIRECTIVE_TEXT)


def test_d5_consumed_directive_is_not_delivered_to_a_different_decision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "different-decision.jsonl"
    _, trace = _run_directive(path)
    assert prepare_for_execution_decision(trace, "decision-A") is not None
    applied_bytes = path.read_bytes()

    later = prepare_for_execution_decision(trace, "decision-B")

    assert later is None
    assert path.read_bytes() == applied_bytes
    assert [
        event.event_type for event in trace.events
    ].count(MIND_DIRECTIVE_APPLIED) == 1


def test_d6_same_decision_retry_reconstructs_same_application_without_append(
    tmp_path: Path,
) -> None:
    path = tmp_path / "same-decision.jsonl"
    _, trace = _run_directive(path)
    first = prepare_for_execution_decision(trace, "decision-A")
    applied_bytes = path.read_bytes()

    retry = prepare_for_execution_decision(trace, "decision-A")

    assert retry == first
    assert path.read_bytes() == applied_bytes
    assert [
        event.event_type for event in trace.events
    ].count(MIND_DIRECTIVE_APPLIED) == 1


def test_d6_preopened_stale_handle_cannot_bind_a_second_decision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stale-handle.jsonl"
    _run_directive(path)
    first_handle = MindTrace.reopen_for_delivery(path)
    stale_second_handle = MindTrace.reopen_for_delivery(path)

    first = prepare_for_execution_decision(first_handle, "decision-A")
    applied_bytes = path.read_bytes()
    second = prepare_for_execution_decision(
        stale_second_handle,
        "decision-B",
    )

    assert first is not None
    assert second is None
    assert path.read_bytes() == applied_bytes
    reopened = MindTrace.reopen(path)
    assert [
        event.event_type for event in reopened.events
    ].count(MIND_DIRECTIVE_APPLIED) == 1


def test_d7_restart_before_application_preserves_pending_delivery(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart-pending.jsonl"
    _run_directive(path)

    resumed = MindTrace.reopen_for_delivery(path)
    application = prepare_for_execution_decision(resumed, "decision-A")

    assert application == DirectiveApplication(
        directive_id=f"{ACTIVATION_ID}:directive:2",
        decision_id="decision-A",
        text=DIRECTIVE_TEXT,
    )
    assert project_directive_state(resumed.events) is DirectiveState.CONSUMED


def test_d7_crash_after_applied_before_dto_return_is_restart_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "crash-after-applied.jsonl"
    _run_directive(path)
    resumed = MindTrace.reopen_for_delivery(path)

    class SimulatedCrash(RuntimeError):
        pass

    def crash_before_return(*_: object, **__: object) -> object:
        raise SimulatedCrash

    monkeypatch.setattr(
        directive_module,
        "DirectiveApplication",
        crash_before_return,
    )
    with pytest.raises(SimulatedCrash):
        prepare_for_execution_decision(resumed, "decision-A")

    assert resumed.events[-1].event_type == MIND_DIRECTIVE_APPLIED
    applied_bytes = path.read_bytes()
    monkeypatch.setattr(
        directive_module,
        "DirectiveApplication",
        DirectiveApplication,
    )

    restarted = MindTrace.reopen_for_delivery(path)
    application = prepare_for_execution_decision(restarted, "decision-A")

    assert application == DirectiveApplication(
        directive_id=f"{ACTIVATION_ID}:directive:2",
        decision_id="decision-A",
        text=DIRECTIVE_TEXT,
    )
    assert path.read_bytes() == applied_bytes


def test_d8_restart_after_application_replays_same_binding_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart-consumed.jsonl"
    _, trace = _run_directive(path)
    first = prepare_for_execution_decision(trace, "decision-A")
    original_bytes = path.read_bytes()

    restarted_for_same = MindTrace.reopen_for_delivery(path)
    same = prepare_for_execution_decision(restarted_for_same, "decision-A")
    restarted_for_other = MindTrace.reopen_for_delivery(path)
    other = prepare_for_execution_decision(restarted_for_other, "decision-B")

    assert same == first
    assert other is None
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "case",
    [
        "applied-without-issue",
        "wrong-issued-id",
        "wrong-directive-id",
        "wrong-issue-ref",
        "duplicate-applied-different-decision",
        "duplicate-issue",
        "finished-directive-without-issue",
        "invalid-decision-id",
        "extra-issue-key",
        "extra-applied-key",
        "unknown-event-version",
        "unknown-event-type",
    ],
)
def test_d9_corrupt_directive_lifecycle_fails_closed_without_repair(
    tmp_path: Path,
    case: str,
) -> None:
    path = tmp_path / f"{case}.jsonl"
    _, trace = _run_directive(path)
    if case in {
        "wrong-directive-id",
        "wrong-issue-ref",
        "duplicate-applied-different-decision",
        "invalid-decision-id",
        "extra-applied-key",
    }:
        prepare_for_execution_decision(trace, "decision-A")
    documents = _documents(path)

    if case == "applied-without-issue":
        issue = documents[-1]
        issue["event_type"] = MIND_DIRECTIVE_APPLIED
        issue["payload"] = {
            "decision_id": "decision-A",
            "directive_id": issue["payload"]["directive_id"],  # type: ignore[index]
        }
    elif case == "wrong-issued-id":
        documents[-1]["payload"]["directive_id"] = "wrong"  # type: ignore[index]
    elif case == "wrong-directive-id":
        documents[-1]["payload"]["directive_id"] = "wrong"  # type: ignore[index]
    elif case == "wrong-issue-ref":
        documents[-1]["source_event_seqs"] = [1]
    elif case == "duplicate-applied-different-decision":
        duplicate = dict(documents[-1])
        duplicate["seq"] = len(documents)
        duplicate["payload"] = dict(duplicate["payload"])  # type: ignore[arg-type]
        duplicate["payload"]["decision_id"] = "decision-B"  # type: ignore[index]
        documents.append(duplicate)
    elif case == "duplicate-issue":
        duplicate = dict(documents[-1])
        duplicate["seq"] = len(documents)
        duplicate["payload"] = dict(duplicate["payload"])  # type: ignore[arg-type]
        documents.append(duplicate)
    elif case == "finished-directive-without-issue":
        documents[-1]["event_type"] = ACTIVATION_FINISHED
        documents[-1]["payload"] = {
            "text": DIRECTIVE_TEXT,
            "type": "directive",
        }
    elif case == "invalid-decision-id":
        documents[-1]["payload"]["decision_id"] = " "  # type: ignore[index]
    elif case == "extra-issue-key":
        documents[-1]["payload"]["extra"] = True  # type: ignore[index]
    elif case == "extra-applied-key":
        documents[-1]["payload"]["extra"] = True  # type: ignore[index]
    elif case == "unknown-event-version":
        documents[-1]["trace_format_version"] = 999
    elif case == "unknown-event-type":
        documents[-1]["event_type"] = "FUTURE_DIRECTIVE_EVENT"
    else:
        raise AssertionError(case)
    _write_documents(path, documents)
    corrupt_bytes = path.read_bytes()

    with pytest.raises(TraceError):
        MindTrace.reopen(path)

    assert path.read_bytes() == corrupt_bytes


@pytest.mark.parametrize(
    ("decision_id", "eligible"),
    [
        ("", True),
        (" decision-A", True),
        ("decision-A ", True),
        ("d" * 129, True),
        (1, True),
        ("decision-A", "yes"),
    ],
    ids=[
        "blank",
        "leading-space",
        "trailing-space",
        "oversized",
        "non-string",
        "non-boolean-eligibility",
    ],
)
def test_d9_invalid_caller_input_fails_before_any_append(
    tmp_path: Path,
    decision_id: object,
    eligible: object,
) -> None:
    path = tmp_path / "invalid-caller.jsonl"
    _, trace = _run_directive(path)
    original_bytes = path.read_bytes()

    with pytest.raises(TraceError):
        prepare_for_execution_decision(
            trace,
            decision_id,  # type: ignore[arg-type]
            eligible=eligible,  # type: ignore[arg-type]
        )

    assert path.read_bytes() == original_bytes
    assert project_directive_state(trace.events) is DirectiveState.PENDING


def test_d10_model_issue_application_and_decision_have_one_causal_chain(
    tmp_path: Path,
) -> None:
    path = tmp_path / "causal.jsonl"
    _, trace = _run_directive(path)
    application = prepare_for_execution_decision(trace, "decision-A")

    replay = replay_activation(trace.events)

    assert application is not None
    assert application.decision_id == "decision-A"
    assert replay.causal_chain == (
        (0, ()),
        (1, (0,)),
        (2, (1,)),
        (3, (2,)),
    )
    assert dict(replay.final_result or {}) == {
        "text": DIRECTIVE_TEXT,
        "type": "directive",
    }


def test_d10_applied_event_does_not_change_two_call_cognitive_replay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "two-call-replay.jsonl"
    trace = _new_trace(path)
    model = ScriptedModel(
        [
            json.dumps(
                {
                    "type": "capability_request",
                    "capability": "recall_memory",
                    "query": "current direction assumption",
                }
            ),
            json.dumps({"type": "directive", "text": DIRECTIVE_TEXT}),
        ]
    )
    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(
            MemoryContext(
                query="current direction assumption",
                rendered_text="The assumption failed in prior evidence.",
            )
        ),
        execution_observation=None,
        trace=trace,
    )
    assert result == Directive(DIRECTIVE_TEXT)
    before = replay_activation(trace.events)

    assert prepare_for_execution_decision(trace, "decision-A") is not None
    after = replay_activation(trace.events)

    assert after.model_requests == before.model_requests
    assert after.model_outputs == before.model_outputs
    assert after.capability_request == before.capability_request
    assert after.capability_observation == before.capability_observation
    assert after.final_result == before.final_result
    assert after.failure_code == before.failure_code
    assert len(after.causal_chain) == len(before.causal_chain) + 1
    assert after.causal_chain[-1] == (6, (5,))


def test_d11_delivery_surface_has_no_intention_or_execution_mutation_authority() -> None:
    parameters = signature(prepare_for_execution_decision).parameters
    sources = [
        Path(directive_module.__file__).read_text(encoding="utf-8"),
        Path(__file__).with_name("trace.py").read_text(encoding="utf-8"),
    ]
    trees = [ast.parse(source) for source in sources]
    imported_modules = {
        node.module or ""
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    called_attributes = {
        node.func.attr
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    defined_functions = {
        node.name
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert set(parameters) == {"trace", "decision_id", "eligible"}
    assert not any(
        module == "Execution" or module.startswith("Execution.")
        for module in imported_modules
    )
    assert called_attributes.isdisjoint(
        {
            "claim_complete",
            "interrupt",
            "pause",
            "resume",
            "resume_execution",
            "revise_intention",
            "run_goal",
            "set_intention",
            "spawn_child",
        }
    )
    assert defined_functions.isdisjoint(
        {
            "interrupt",
            "pause",
            "resume",
            "revise_intention",
            "run_goal",
            "set_intention",
        }
    )
    for forbidden_method in (
        "apply",
        "execute",
        "mutate_intention",
        "set_intention",
    ):
        assert not hasattr(DirectiveApplication, forbidden_method)
