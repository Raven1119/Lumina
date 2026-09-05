from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from Execution.deepseek_model import DeepSeekModel
from Execution.execution import (
    Checkpoint,
    ClaimComplete,
    EventLog,
    ReadRequest,
    ScriptedModel,
    ToolCall,
    Wait,
    WriteRequest,
    restore_execution_state,
)
from Mind.experiment_a import Directive, NoChange
from Mind.trace import MIND_DIRECTIVE_APPLIED, MIND_DIRECTIVE_ISSUED

from Mind.behavioral_experiment import (
    E1Blocked,
    E1Config,
    ExperimentIntegrityError,
    FrozenTaskSpec,
    _real_model_environment,
    fork_prefix,
    freeze_prefix,
    load_registered_campaign,
    run_pair,
)


def test_e1_historical_minimax_campaign_cannot_call_a_provider() -> None:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e1" / "manifest.json"
    )

    with pytest.raises(E1Blocked, match="historical_minimax_provider_retired"):
        with _real_model_environment(campaign):
            pytest.fail("retired provider context must not open")


def _task() -> FrozenTaskSpec:
    return FrozenTaskSpec(
        task_id="e1-test-prefix",
        description="Exercise a durable one-decision prefix.",
        goal="Read mission.txt and carry out its instructions.",
        files=(("mission.txt", b"Read the input before writing answer.txt.\n"),),
        completion_path="answer.txt",
        expected_content="~" * 1_100 + "\nDONE\n",
    )


class _ScriptedMindModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = iter(responses)
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        return next(self._responses)

    def summarize_hot_draft(self, old_summary, moved_turns):
        raise AssertionError("E1 Mind must not summarize Hot Draft")


def _config() -> E1Config:
    return E1Config(
        branch_after_root_decisions=1,
        max_additional_root_decisions=3,
        max_child_decisions=2,
        max_context_chars=5_000,
        wall_time_seconds=30.0,
    )


def test_registered_holdout_manifest_is_exactly_six_frozen_nonleaking_tasks():
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e1" / "manifest.json"
    )

    assert campaign.manifest_sha256 == (
        "6a9c915a52c31d209a9ba51e1f14be8497ac12e5fd82ee88a73e8ef7be90fb4f"
    )
    assert len(campaign.tasks) == 6
    assert len({task.task_id for task in campaign.tasks}) == 6
    assert campaign.config.branch_after_root_decisions == 1
    assert campaign.config.max_context_chars == 5_000
    assert campaign.arm_order.count("baseline_first") == 3
    assert campaign.arm_order.count("candidate_first") == 3
    for task in campaign.tasks:
        assert task.expected_content.startswith("~" * 1_100 + "\n")
        assert set(task.expected_content[:1_024]) == {"~"}
        assert task.expected_content.endswith("\n")
        assert task.completion_path not in {path for path, _ in task.files}


def _prefix(tmp_path):
    return freeze_prefix(
        _task(),
        model=ScriptedModel([ToolCall(ReadRequest("mission.txt"))]),
        output_dir=tmp_path / "frozen",
        config=_config(),
    )


def test_e1_h1_h2_fork_has_exact_state_workspace_log_and_request_prefix(
    tmp_path,
):
    delegate = ScriptedModel([ToolCall(ReadRequest("mission.txt"))])
    prefix = freeze_prefix(
        _task(),
        model=delegate,
        output_dir=tmp_path / "frozen",
        config=_config(),
    )

    branches = fork_prefix(prefix, tmp_path / "pair")

    assert tuple(delegate.received_requests) == prefix.request_history
    assert len(delegate.received_requests) == 1
    assert prefix.evidence.task_sha256 == (
        "a5d5de4f8d6b0a50ab1b1dac3cb7b513239f47f04808a6ede4a4c7ee8ce5a9dd"
    )
    assert prefix.evidence.workspace_sha256 == (
        "54a85fba20e19067ff9a85db4007e3f514564811b91061e90435bf84d18ff4de"
    )
    assert [path.relative_to(prefix.workspace).as_posix() for path in prefix.workspace.rglob("*")] == ["mission.txt"]
    assert (prefix.workspace / "mission.txt").read_bytes() == (
        b"Read the input before writing answer.txt.\n"
    )
    event_types = [
        event.event_type for event in EventLog.load(prefix.event_log_path).events
    ]
    assert event_types == [
        "EXECUTION_STARTED",
        "MODEL_DECISION",
        "TOOL_CALL_STARTED",
        "TOOL_RESULT",
        "INTERRUPT_REQUESTED",
        "ACTOR_SUSPENDED",
    ]
    assert prefix.state == restore_execution_state(
        EventLog.load(prefix.event_log_path).events,
        prefix.checkpoint_path,
    )
    assert Checkpoint.load(prefix.checkpoint_path).state == prefix.state
    assert branches.equivalent is True
    assert branches.baseline.evidence == branches.candidate.evidence
    assert branches.baseline.evidence == prefix.evidence
    assert branches.baseline.state == branches.candidate.state
    assert branches.baseline.state.decision_count == 1
    assert branches.baseline.state.status == "suspended"
    assert branches.baseline.next_decision_id == "decision-000002"
    assert branches.baseline.event_log_path.read_bytes() == (
        branches.candidate.event_log_path.read_bytes()
    )
    assert branches.baseline.checkpoint_path.read_bytes() == (
        branches.candidate.checkpoint_path.read_bytes()
    )
    assert branches.baseline.event_log_path.read_bytes() == (
        prefix.event_log_path.read_bytes()
    )
    assert branches.baseline.checkpoint_path.read_bytes() == (
        prefix.checkpoint_path.read_bytes()
    )
    assert branches.baseline.request_history == branches.candidate.request_history
    assert len(branches.baseline.request_history) == 1
    assert branches.baseline.evidence.event_log_sha256 == hashlib.sha256(
        branches.baseline.event_log_path.read_bytes()
    ).hexdigest()
    assert branches.baseline.evidence.checkpoint_sha256 == hashlib.sha256(
        branches.baseline.checkpoint_path.read_bytes()
    ).hexdigest()
    assert not branches.baseline.event_log_path.samefile(
        branches.candidate.event_log_path
    )
    assert not (branches.baseline.workspace / "mission.txt").samefile(
        branches.candidate.workspace / "mission.txt"
    )
    assert prefix.event_log_path.parent not in prefix.workspace.parents
    assert branches.baseline.event_log_path.parent not in (
        branches.baseline.workspace.parents
    )
    assert branches.candidate.event_log_path.parent not in (
        branches.candidate.workspace.parents
    )
    assert branches.baseline.workspace.parent != branches.candidate.workspace.parent
    assert not prefix.state.child_refs


def test_fork_rejects_a_frozen_workspace_changed_after_evidence_capture(tmp_path):
    prefix = _prefix(tmp_path)
    (prefix.workspace / "mission.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(ExperimentIntegrityError, match="frozen_prefix_changed"):
        fork_prefix(prefix, tmp_path / "pair")


def test_second_arm_is_reverified_after_first_arm_finishes(tmp_path):
    prefix = _prefix(tmp_path)
    branches = fork_prefix(prefix, tmp_path / "pair")
    delegate = ScriptedModel([Wait("e1-test-stop")])

    class MutatingModel:
        identifier = delegate.identifier

        def decide(self, request):
            (branches.candidate.workspace / "mission.txt").write_text(
                "cross-arm mutation",
                encoding="utf-8",
            )
            return delegate.decide(request)

    with pytest.raises(
        ExperimentIntegrityError,
        match="branch_changed_before_resume",
    ):
        run_pair(
            prefix,
            baseline_model=MutatingModel(),
            candidate_model=ScriptedModel([Wait("e1-test-stop")]),
            mind_model=_ScriptedMindModel(['{"type":"no_change"}']),
            output_dir=tmp_path / "pair",
            branches=branches,
        )


def test_e1_h3_real_no_change_leaves_next_execution_request_exactly_baseline(
    tmp_path,
):
    prefix = _prefix(tmp_path)
    paired = run_pair(
        prefix,
        baseline_model=ScriptedModel([Wait("e1-test-stop")]),
        candidate_model=ScriptedModel([Wait("e1-test-stop")]),
        mind_model=_ScriptedMindModel(['{"type":"no_change"}']),
        output_dir=tmp_path / "pair",
    )

    assert paired.mind_result == NoChange()
    assert paired.directive_application is None
    assert paired.baseline.new_frames == paired.candidate.new_frames
    assert paired.baseline.event_log_bytes == paired.candidate.event_log_bytes
    assert len(paired.baseline.new_frames) == 1


def test_e1_h4_real_directive_changes_exactly_one_next_request_field(
    tmp_path,
):
    prefix = _prefix(tmp_path)
    directive_text = "Keep the task's stated constraints jointly in view."
    paired = run_pair(
        prefix,
        baseline_model=ScriptedModel([Wait("e1-test-stop")]),
        candidate_model=ScriptedModel([Wait("e1-test-stop")]),
        mind_model=_ScriptedMindModel(
            [
                '{"type":"capability_request","capability":"inspect_execution"}',
                json.dumps(
                    {"type": "directive", "text": directive_text},
                    separators=(",", ":"),
                ),
            ]
        ),
        output_dir=tmp_path / "pair",
    )

    assert paired.mind_result == Directive(directive_text)
    assert paired.directive_application is not None
    baseline_request = paired.baseline.new_frames[0].actual_request
    candidate_request = paired.candidate.new_frames[0].actual_request
    baseline_context = json.loads(baseline_request.context)
    candidate_context = json.loads(candidate_request.context)
    assert candidate_context.pop("mind_supervisor_directive") == (
        paired.directive_application.as_model_context()
    )
    assert candidate_context == baseline_context
    assert candidate_request.available_tools == baseline_request.available_tools
    assert candidate_request.source_event_refs == baseline_request.source_event_refs
    event_types = [event.event_type for event in paired.mind_trace.events]
    assert event_types.count(MIND_DIRECTIVE_ISSUED) == 1
    assert event_types.count(MIND_DIRECTIVE_APPLIED) == 1


def test_e1_h5_directive_is_absent_and_requests_reconverge_next_decision(
    tmp_path,
):
    prefix = _prefix(tmp_path)
    directive_text = "Keep the task's stated constraints jointly in view."
    paired = run_pair(
        prefix,
        baseline_model=ScriptedModel(
            [ToolCall(ReadRequest("mission.txt")), Wait("e1-test-stop")]
        ),
        candidate_model=ScriptedModel(
            [ToolCall(ReadRequest("mission.txt")), Wait("e1-test-stop")]
        ),
        mind_model=_ScriptedMindModel(
            [json.dumps({"type": "directive", "text": directive_text})]
        ),
        output_dir=tmp_path / "pair",
    )

    assert len(paired.baseline.new_frames) == 2
    assert len(paired.candidate.new_frames) == 2
    assert "mind_supervisor_directive" in json.loads(
        paired.candidate.new_frames[0].actual_request.context
    )
    assert "mind_supervisor_directive" not in json.loads(
        paired.candidate.new_frames[1].actual_request.context
    )
    assert (
        paired.candidate.new_frames[1].actual_request
        == paired.baseline.new_frames[1].actual_request
    )


def test_e1_h5_native_provider_payload_reconverges_after_one_shot_directive(
    tmp_path,
):
    prefix_responses = iter(
        [
            _tool_response(
                "prefix-call",
                "ipython",
                {"code": "print(open('mission.txt').read())"},
            )
        ]
    )
    prefix = freeze_prefix(
        _task(),
        model=DeepSeekModel(transport=lambda _: next(prefix_responses)),
        output_dir=tmp_path / "frozen",
        config=_config(),
    )
    baseline_payloads = []
    candidate_payloads = []
    baseline_responses = iter(
        [
            _tool_response("call-a", "ipython", {"code": "pass"}),
            _tool_response("call-b", "wait", {"event_type": "e1-test-stop"}),
        ]
    )
    candidate_responses = iter(
        [
            _tool_response("call-a", "ipython", {"code": "pass"}),
            _tool_response("call-b", "wait", {"event_type": "e1-test-stop"}),
        ]
    )

    def baseline_transport(payload):
        baseline_payloads.append(payload)
        return next(baseline_responses)

    def candidate_transport(payload):
        candidate_payloads.append(payload)
        return next(candidate_responses)

    paired = run_pair(
        prefix,
        baseline_model=DeepSeekModel(transport=baseline_transport),
        candidate_model=DeepSeekModel(transport=candidate_transport),
        mind_model=_ScriptedMindModel(
            [
                json.dumps(
                    {
                        "type": "directive",
                        "text": "Keep the task's stated constraints jointly in view.",
                    }
                )
            ]
        ),
        output_dir=tmp_path / "pair",
    )

    assert len(baseline_payloads) == len(candidate_payloads) == 2
    assert baseline_payloads[1] == candidate_payloads[1]
    assert (
        paired.baseline.new_frames[1].provider_wire_request
        == paired.candidate.new_frames[1].provider_wire_request
    )
    assert "[Mind Supervisor Directive]" not in json.dumps(
        candidate_payloads[1]
    )


def test_e1_h6_same_budget_verifier_environment_and_verified_completion(
    tmp_path,
):
    prefix = _prefix(tmp_path)
    actions = [
        ToolCall(WriteRequest("answer.txt", _task().expected_content)),
        ClaimComplete(),
    ]
    paired = run_pair(
        prefix,
        baseline_model=ScriptedModel(list(actions)),
        candidate_model=ScriptedModel(list(actions)),
        mind_model=_ScriptedMindModel(['{"type":"no_change"}']),
        output_dir=tmp_path / "pair",
    )

    assert paired.budgets_equal is True
    assert paired.verifier_equal is True
    assert paired.environment_equal is True
    assert paired.baseline.verified_completion is True
    assert paired.candidate.verified_completion is True
    assert paired.baseline.additional_root_decisions == 2
    assert paired.candidate.additional_root_decisions == 2


def _tool_response(call_id, name, arguments):
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
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
            }
        ]
    }
