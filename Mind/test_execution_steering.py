from __future__ import annotations

import ast
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from Execution import ExecutionOrgan, FileContentEquals
from Execution.deepseek_model import DeepSeekModel
from Execution.execution import ClaimComplete, EventLog
from Mind import execution_steering_experiment as steering_module
from Mind.directive import DirectiveApplication, prepare_for_execution_decision
from Mind.execution_steering_experiment import (
    FIXED_DIRECTIVE_TEXT,
    decision_advisory_from,
)
from Mind.trace import (
    ACTIVATION_STARTED,
    MIND_DIRECTIVE_APPLIED,
    MIND_DIRECTIVE_ISSUED,
    MODEL_OUTPUT_RECORDED,
    MindTrace,
    directive_id_for,
)


FIXED_TIMESTAMP = "2026-08-30T12:00:00.000000Z"
ACTIVATION_ID = "experiment-e0-activation"
GOAL = "Verify the real Root decision request."
COMPLETION_SPEC = FileContentEquals(".lumina-complete", "verified")
ROOT_SURFACE = (
    "ipython(code: str)",
    "wait(event_type: str)",
    "claim_complete()",
)


class CaptureModel:
    identifier = "e0-capture-model"
    tool_contracts = ROOT_SURFACE

    def __init__(self, actions: list[object]) -> None:
        self._actions = iter(actions)
        self.received_requests = []

    def decide(self, request):
        self.received_requests.append(request)
        return next(self._actions)


class RaisingCaptureModel:
    identifier = "e0-capture-model"
    tool_contracts = ROOT_SURFACE

    def __init__(self) -> None:
        self.received_requests = []

    def decide(self, request):
        self.received_requests.append(request)
        raise RuntimeError("simulated host failure before decision persistence")


def _write_started_execution(path: Path) -> None:
    EventLog(path).append(
        "EXECUTION_STARTED",
        {
            "goal": GOAL,
            "execution_id": "execution-e0-fixed",
            "root_actor_id": "root-e0-fixed",
            "completion_spec": COMPLETION_SPEC,
            "depth": 0,
            "max_depth": 1,
        },
    )


def _organ(
    tmp_path: Path,
    arm: str,
    model: object,
    *,
    max_decisions: int = 1,
    max_context_chars: int = 2_000,
) -> ExecutionOrgan:
    workspace = tmp_path / arm / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".lumina-complete").write_text("verified", encoding="utf-8")
    event_path = tmp_path / arm / "state" / "execution.jsonl"
    _write_started_execution(event_path)
    return ExecutionOrgan(
        workspace=workspace,
        event_log_path=event_path,
        max_decisions=max_decisions,
        model=model,
        max_context_chars=max_context_chars,
    )


def _issued_trace(path: Path) -> MindTrace:
    trace = MindTrace.create(
        path,
        activation_id=ACTIVATION_ID,
        fixed_timestamp=FIXED_TIMESTAMP,
    )
    started = trace.append(
        ACTIVATION_STARTED,
        {
            "activation": {
                "execution_goal_snapshot": GOAL,
                "execution_status": "running",
                "trigger": "Fixed E0 integration fixture.",
            },
            "information_acquisition_allowed": False,
            "projector_version": "mind-projector-v1",
            "prompt_version": "mind-prompt-v1",
        },
        source_event_seqs=(),
    )
    output = trace.append(
        MODEL_OUTPUT_RECORDED,
        {
            "call_index": 1,
            "text": json.dumps(
                {"type": "directive", "text": FIXED_DIRECTIVE_TEXT},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
        source_event_seqs=(started.seq,),
    )
    trace.append(
        MIND_DIRECTIVE_ISSUED,
        {
            "directive_id": directive_id_for(ACTIVATION_ID, 2),
            "text": FIXED_DIRECTIVE_TEXT,
        },
        source_event_seqs=(output.seq,),
    )
    return trace


def test_e0_1_to_3_candidate_is_exact_baseline_plus_d_directive_block(
    tmp_path: Path,
) -> None:
    baseline_model = CaptureModel([ClaimComplete()])
    baseline_organ = _organ(tmp_path, "baseline", baseline_model)
    try:
        baseline = baseline_organ.run_goal(GOAL, COMPLETION_SPEC)
    finally:
        baseline_organ.shutdown()

    trace = _issued_trace(tmp_path / "mind-directive.jsonl")
    application = prepare_for_execution_decision(
        trace,
        "decision-000001",
        eligible=True,
    )
    advisory = decision_advisory_from(application)
    candidate_model = CaptureModel([ClaimComplete()])
    candidate_organ = _organ(tmp_path, "candidate", candidate_model)
    try:
        candidate = candidate_organ.run_goal(
            GOAL,
            COMPLETION_SPEC,
            decision_advisory=advisory,
        )
    finally:
        candidate_organ.shutdown()

    baseline_frame = baseline.decision_frames[0]
    candidate_frame = candidate.decision_frames[0]
    baseline_request = baseline_model.received_requests[0]
    candidate_request = candidate_model.received_requests[0]

    assert baseline_request is baseline_frame.actual_request
    assert candidate_request is candidate_frame.actual_request
    baseline_context = json.loads(baseline_request.context)
    candidate_context = json.loads(candidate_request.context)
    assert "mind_supervisor_directive" not in baseline_context
    assert candidate_context.pop("mind_supervisor_directive") == (
        application.as_model_context()
    )
    assert candidate_context == baseline_context
    expected_context = dict(baseline_context)
    expected_context["mind_supervisor_directive"] = (
        application.as_model_context()
    )
    assert candidate_request.context == json.dumps(
        expected_context,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert replace(
        candidate_request,
        context=baseline_request.context,
    ) == baseline_request
    assert candidate_request.available_tools == baseline_request.available_tools
    assert candidate_request.source_event_refs == baseline_request.source_event_refs
    assert candidate_request.native_tool_continuation is None
    assert baseline_request.native_tool_continuation is None
    assert candidate_request.model_visible_context_limit == (
        baseline_request.model_visible_context_limit
    )
    assert candidate_frame.decision_id == baseline_frame.decision_id == (
        application.decision_id
    )
    assert candidate_frame.goal == baseline_frame.goal == GOAL
    assert candidate_frame.state_version == baseline_frame.state_version
    assert candidate_frame.model_identifier == baseline_frame.model_identifier
    assert candidate_frame.actual_tools_exposed == (
        baseline_frame.actual_tools_exposed
    )
    assert replace(
        candidate_frame,
        actual_request=baseline_request,
    ) == baseline_frame
    assert candidate.state.goal == baseline.state.goal == GOAL
    assert candidate.state.completion_spec == baseline.state.completion_spec
    assert [event.event_type for event in trace.events].count(
        MIND_DIRECTIVE_APPLIED
    ) == 1


def _tool_response(call_id: str, name: str, arguments: dict[str, object]):
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    arguments,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }


def test_e0_4_next_native_decision_and_provider_request_do_not_replay_directive(
    tmp_path: Path,
) -> None:
    responses = iter(
        [
            _tool_response("call-a", "ipython", {"code": "pass"}),
            _tool_response("call-b", "claim_complete", {}),
        ]
    )
    provider_requests: list[dict[str, object]] = []

    def transport(payload: dict[str, object]) -> object:
        provider_requests.append(payload)
        return next(responses)

    trace = _issued_trace(tmp_path / "mind-directive.jsonl")
    application = prepare_for_execution_decision(
        trace,
        "decision-000001",
        eligible=True,
    )
    organ = _organ(
        tmp_path,
        "native-candidate",
        DeepSeekModel(transport=transport),
        max_decisions=2,
    )
    try:
        result = organ.run_goal(
            GOAL,
            COMPLETION_SPEC,
            decision_advisory=decision_advisory_from(application),
        )
    finally:
        organ.shutdown()

    assert result.status == "completed"
    assert [frame.decision_id for frame in result.decision_frames] == [
        "decision-000001",
        "decision-000002",
    ]
    first, second = result.decision_frames
    assert json.loads(first.actual_request.context)[
        "mind_supervisor_directive"
    ] == application.as_model_context()
    assert "mind_supervisor_directive" not in json.loads(
        second.actual_request.context
    )
    assert second.actual_request.native_tool_continuation is not None
    assert application.as_model_context() not in (
        second.actual_request.native_tool_continuation.previous_model_context
    )
    assert [EventLog._freeze(payload) for payload in provider_requests] == [
        first.provider_wire_request,
        second.provider_wire_request,
    ]
    first_messages = provider_requests[0]["messages"]
    assert isinstance(first_messages, list)
    first_user_context = json.loads(first_messages[1]["content"])
    assert first_user_context["mind_supervisor_directive"] == (
        application.as_model_context()
    )
    assert "[Mind Supervisor Directive]" not in json.dumps(
        provider_requests[1],
        ensure_ascii=False,
    )


def test_e0_5_and_6_same_decision_retry_and_restart_reconstruct_one_application(
    tmp_path: Path,
) -> None:
    mind_path = tmp_path / "mind-directive.jsonl"
    trace = _issued_trace(mind_path)
    application = prepare_for_execution_decision(
        trace,
        "decision-000001",
        eligible=True,
    )
    expected_advisory = decision_advisory_from(application)
    applied_bytes = mind_path.read_bytes()

    del application, trace
    reopened = MindTrace.reopen_for_delivery(mind_path)
    restarted_application = prepare_for_execution_decision(
        reopened,
        "decision-000001",
        eligible=True,
    )
    assert decision_advisory_from(restarted_application) == expected_advisory
    assert mind_path.read_bytes() == applied_bytes

    arm = tmp_path / "execution-retry"
    workspace = arm / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".lumina-complete").write_text("verified", encoding="utf-8")
    event_path = arm / "state" / "execution.jsonl"
    _write_started_execution(event_path)
    raising_model = RaisingCaptureModel()
    first = ExecutionOrgan(
        workspace=workspace,
        event_log_path=event_path,
        max_decisions=1,
        model=raising_model,
    )
    assert first.next_root_decision_id == restarted_application.decision_id
    try:
        with pytest.raises(RuntimeError, match="simulated host failure"):
            first.run_goal(
                GOAL,
                COMPLETION_SPEC,
                decision_advisory=expected_advisory,
            )
    finally:
        first.shutdown()

    assert [event.event_type for event in EventLog.load(event_path).events] == [
        "EXECUTION_STARTED"
    ]
    del reopened, restarted_application
    retry_trace = MindTrace.reopen_for_delivery(mind_path)
    retry_application = prepare_for_execution_decision(
        retry_trace,
        "decision-000001",
        eligible=True,
    )
    retry_model = CaptureModel([ClaimComplete()])
    retry = ExecutionOrgan(
        workspace=workspace,
        event_log_path=event_path,
        max_decisions=1,
        model=retry_model,
    )
    assert retry.next_root_decision_id == retry_application.decision_id
    try:
        result = retry.run_goal(
            GOAL,
            COMPLETION_SPEC,
            decision_advisory=decision_advisory_from(retry_application),
        )
    finally:
        retry.shutdown()

    assert result.decision_frames[0].decision_id == "decision-000001"
    assert retry_model.received_requests[0] == raising_model.received_requests[0]
    assert mind_path.read_bytes() == applied_bytes
    assert [event.event_type for event in retry_trace.events].count(
        MIND_DIRECTIVE_APPLIED
    ) == 1


def test_e0_9_mind_bridge_has_only_inert_data_and_no_execution_authority() -> None:
    source = Path(inspect.getsourcefile(steering_module) or "").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }

    assert not any(
        name == "Execution" or name.startswith("Execution.")
        for name in imported_modules
    )
    assert names.isdisjoint(
        {
            "AgentProcess",
            "ExecutionOrgan",
            "PersistentIPython",
            "Runtime",
            "ToolHost",
            "interrupt",
            "resume",
            "run_goal",
            "shell",
            "spawn_child",
        }
    )
    parameters = inspect.signature(decision_advisory_from).parameters
    assert set(parameters) == {"application", "contract"}
    assert parameters["contract"].kind == inspect.Parameter.KEYWORD_ONLY
    assert parameters["contract"].default is None
    application = DirectiveApplication(
        "experiment-e0-activation:directive:2",
        "decision-000001",
        FIXED_DIRECTIVE_TEXT,
    )
    advisory = decision_advisory_from(application)
    assert type(advisory) is tuple
    assert all(type(value) is str for value in advisory)
    assert "decision_advisory" not in inspect.signature(
        ExecutionOrgan.run_child
    ).parameters
    assert "decision_advisory" not in inspect.signature(
        ExecutionOrgan.open_child
    ).parameters


def test_e0_10_none_advisory_is_byte_exact_existing_execution_path(
    tmp_path: Path,
) -> None:
    implicit_model = CaptureModel([ClaimComplete()])
    implicit_organ = _organ(tmp_path, "implicit-none", implicit_model)
    try:
        implicit = implicit_organ.run_goal(GOAL, COMPLETION_SPEC)
    finally:
        implicit_organ.shutdown()

    explicit_model = CaptureModel([ClaimComplete()])
    explicit_organ = _organ(tmp_path, "explicit-none", explicit_model)
    try:
        explicit = explicit_organ.run_goal(
            GOAL,
            COMPLETION_SPEC,
            decision_advisory=None,
        )
    finally:
        explicit_organ.shutdown()

    assert explicit_model.received_requests == implicit_model.received_requests
    assert explicit.events == implicit.events
    implicit_bytes = (
        tmp_path / "implicit-none" / "state" / "execution.jsonl"
    ).read_bytes()
    explicit_bytes = (
        tmp_path / "explicit-none" / "state" / "execution.jsonl"
    ).read_bytes()
    assert explicit_bytes == implicit_bytes


@pytest.mark.parametrize(
    "malformed",
    [
        ("wrong-decision", "advisory"),
        ("decision-000001", ""),
        ("decision-000001", " advisory "),
        ("decision-000001", "x" * 6_201),
        ["decision-000001", "advisory"],
        object(),
    ],
)
def test_e0_11_malformed_execution_advisory_fails_soft_to_baseline(
    tmp_path: Path,
    malformed: object,
) -> None:
    model = CaptureModel([ClaimComplete()])
    organ = _organ(tmp_path, f"malformed-{id(malformed)}", model)
    try:
        result = organ.run_goal(
            GOAL,
            COMPLETION_SPEC,
            decision_advisory=malformed,  # type: ignore[arg-type]
        )
    finally:
        organ.shutdown()

    assert result.status == "completed"
    assert "mind_supervisor_directive" not in json.loads(
        model.received_requests[0].context
    )


def test_e0_11_unavailable_or_malformed_application_projects_no_authority() -> None:
    assert decision_advisory_from(None) is None
    assert decision_advisory_from(object()) is None
    assert decision_advisory_from(
        DirectiveApplication(
            "experiment-e0-activation:directive:2",
            "decision-000001",
            "",
        )
    ) is None


def test_valid_over_budget_advisory_stops_before_action_and_resumes_without_loss(
    tmp_path: Path,
) -> None:
    application = DirectiveApplication(
        "experiment-e0-activation:directive:2",
        "decision-000001",
        "x" * 1_000,
    )
    candidate_model = CaptureModel([ClaimComplete()])
    candidate_organ = _organ(
        tmp_path,
        "budget-candidate",
        candidate_model,
        max_context_chars=768,
    )
    try:
        before = candidate_organ.state
        with pytest.raises(ValueError, match="guidance exceeds the available decision context"):
            candidate_organ.run_goal(
                GOAL, COMPLETION_SPEC, decision_advisory=decision_advisory_from(application),
            )
        assert candidate_model.received_requests == []
        assert candidate_organ.state == before
        assert candidate_organ.next_root_decision_id == application.decision_id
    finally:
        candidate_organ.shutdown()

    recovered = ExecutionOrgan(
        workspace=tmp_path / "budget-candidate" / "workspace",
        event_log_path=tmp_path / "budget-candidate" / "state" / "execution.jsonl",
        max_decisions=1, model=candidate_model, max_context_chars=4_000,
    )
    try:
        candidate = recovered.run_goal(
            GOAL, COMPLETION_SPEC, decision_advisory=decision_advisory_from(application),
        )
    finally:
        recovered.shutdown()
    assert candidate.status == "completed"
    assert len(candidate_model.received_requests) == 1
    assert json.loads(candidate_model.received_requests[0].context)["mind_supervisor_directive"] == application.as_model_context()


def test_e0_9_child_resume_rejects_root_decision_advisory() -> None:
    child = object.__new__(ExecutionOrgan)
    child._child_ref = object()  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Child execution"):
        child.resume(decision_advisory=("decision-000001", "advisory"))
    assert decision_advisory_from(
        DirectiveApplication(
            "experiment-e0-activation:directive:2",
            " decision-000001 ",
            FIXED_DIRECTIVE_TEXT,
        )
    ) is None


def test_persistent_mind_delivery_binds_run_and_returns_owner_result_event(tmp_path):
    from Execution.execution import IPythonCode
    from Mind.organ import Evidence, MindInput, MindOrgan
    from Mind.test_experiment_a import ScriptedModel

    actor = CaptureModel([IPythonCode('print("checked")'), ClaimComplete()])
    execution = _organ(tmp_path, "cognitive", actor, max_decisions=2)
    event = MindInput(
        event_id="execution-started", trigger="Review the current completion direction.",
        intention_ref="intention-1", intention_revision=1, goal=GOAL,
        execution_ref="execution-e0-fixed", execution_status="running",
    )
    first_model = ScriptedModel([json.dumps({
        "type": "cognitive_step", "updates": [],
        "next": {"type": "directive", "text": "Check the completion evidence before claiming success."},
    })])
    directory = tmp_path / "mind"
    try:
        with MindOrgan(directory=directory, model=first_model, memory_retriever=None) as mind:
            assert mind.activate(event).status == "accepted"
        # The host receives an accepted Mind event, reopens its owner, and routes
        # only inert advisory data to the exact receiving Execution decision.
        with MindOrgan(directory=directory, model=ScriptedModel([]), memory_retriever=None) as mind:
            assert mind.activate(event).status == "duplicate"
            application = mind.prepare_directive(
                event.event_id, execution_ref=event.execution_ref,
                decision_id=execution.next_root_decision_id,
                intention_ref=event.intention_ref, intention_revision=1,
            )
            assert application is not None
            arguments = dict(execution_ref=event.execution_ref,
                             decision_id=execution.next_root_decision_id)
            advisory = steering_module.decision_advisory_for_execution(application, **arguments)
            assert advisory is not None and advisory[0] == execution.next_root_decision_id
            assert steering_module.decision_advisory_for_execution(
                application, **(arguments | {"execution_ref": "different-run"})) is None
            assert steering_module.decision_advisory_for_execution(
                application, **(arguments | {"decision_id": "decision-000002"})) is None
            result = execution.resume(decision_advisory=advisory)
        assert result.status == "completed"
        assert result.state.goal == GOAL and result.state.completion_spec == COMPLETION_SPEC
        contexts = [json.loads(request.context) for request in actor.received_requests]
        assert contexts[0]["mind_supervisor_directive"] == application.as_model_context()
        assert "mind_supervisor_directive" not in contexts[1]
        assert all(request.available_tools == ROOT_SURFACE for request in actor.received_requests)
        owner_result = execution.reality_evidence()[-1]
        text = json.dumps(dict(owner_result.payload), separators=(",", ":"))
        returned = replace(
            event, event_id=owner_result.evidence_ref, trigger="Execution returned its verified result.",
            execution_status="completed", evidence=(Evidence(owner_result.evidence_ref, text, "execution"),),
        )
        final_model = ScriptedModel([json.dumps({
            "type": "cognitive_step", "updates": [{
                "kind": "belief", "id": "new:completion", "status": "supported",
                "claim": "Execution verified completion.", "discriminator": "Any later owner correction.",
                "basis": [{"ref": owner_result.evidence_ref, "quote": '"completion_verified":true'}],
            }], "next": {"type": "no_change"},
        })])
        with MindOrgan(directory=directory, model=final_model, memory_retriever=None) as mind:
            assert mind.activate(returned).revision == 2
            assert mind.activate(returned).status == "duplicate"
            assert len(final_model.calls) == 1
            assert mind.prepare_directive(event.event_id, **arguments,
                                          intention_ref=event.intention_ref, intention_revision=1) is None
    finally:
        execution.shutdown()
