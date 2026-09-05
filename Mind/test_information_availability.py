from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from Conversation_Memory.adapter.models import MemoryContext
from Execution.execution import (
    ReadRequest,
    ScriptedModel as ExecutionScriptedModel,
    ToolCall,
    Wait,
)
from Mind import trace as trace_module
from Mind.directive import prepare_for_execution_decision
from Mind.execution_steering_experiment import decision_advisory_from
from Mind.experiment_a import (
    ActivationFailure,
    ActivationInput,
    Directive,
    ExecutionObservation,
    NoChange,
    run_activation,
)
from Mind.trace import (
    ACTIVATION_FINISHED,
    ACTIVATION_STARTED,
    CAPABILITY_OBSERVED,
    CAPABILITY_REQUESTED,
    MIND_DIRECTIVE_APPLIED,
    MIND_DIRECTIVE_ISSUED,
    MODEL_OUTPUT_RECORDED,
    MindTrace,
    replay_activation,
)
from Mind.behavioral_experiment import (
    E1Config,
    ExperimentIntegrityError,
    FrozenTaskSpec,
    freeze_prefix,
)
from Mind.information_availability_experiment import (
    E2Blocked,
    _real_model_environment,
    build_blinded_directive_audit,
    evaluate_verdict,
    load_registered_campaign,
    merge_audit_reviews,
    run_information_pair,
)


def test_e2_historical_minimax_campaign_cannot_call_a_provider() -> None:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e2" / "manifest.json"
    )

    with pytest.raises(E2Blocked, match="historical_minimax_provider_retired"):
        with _real_model_environment(campaign):
            pytest.fail("retired provider context must not open")


FIXED_TIMESTAMP = "2026-08-30T12:00:00.000000Z"
ACTIVATION = ActivationInput(
    trigger="Mechanical E2 activation after Root decision 1.",
    execution_goal_snapshot="Read mission.txt and carry out its instructions.",
    execution_status="in_progress",
)
SNAPSHOT = ExecutionObservation(
    goal="Read mission.txt and carry out its instructions.",
    status="in_progress",
    recent_outcome="Root read the complete mission.txt instructions.",
    failure=None,
)
SNAPSHOT_PROJECTION = {
    "capability": "inspect_execution",
    "failure": None,
    "goal": SNAPSHOT.goal,
    "recent_outcome": SNAPSHOT.recent_outcome,
    "status": SNAPSHOT.status,
}


class ScriptedModel:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        self.calls.append(
            {
                "recent_context": copy.deepcopy(recent_context),
                "system_prompt": system_prompt,
                "user_message": user_message,
            }
        )
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


class EmptyMemory:
    def recall(self, query: str, policy: object) -> MemoryContext:
        return MemoryContext(query=query)


def _trace(path: Path, activation_id: str = "e2-mechanism") -> MindTrace:
    return MindTrace.create(
        path,
        activation_id=activation_id,
        fixed_timestamp=FIXED_TIMESTAMP,
    )


def _payload(call: dict[str, object]) -> dict[str, object]:
    return json.loads(str(call["user_message"]))


def _run(
    path: Path,
    responses: list[str],
    *,
    visible: bool,
) -> tuple[object, ScriptedModel, MindTrace]:
    model = ScriptedModel(responses)
    trace = _trace(path)
    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=EmptyMemory(),
        execution_observation=SNAPSHOT,
        initial_execution_observation_visible=visible,
        trace=trace,
    )
    return result, model, trace


def test_e2_h1_same_snapshot_is_absent_from_pull_and_the_only_push_delta(
    tmp_path: Path,
) -> None:
    pull_result, pull_model, pull_trace = _run(
        tmp_path / "pull.jsonl",
        ['{"type":"no_change"}'],
        visible=False,
    )
    push_result, push_model, push_trace = _run(
        tmp_path / "push.jsonl",
        ['{"type":"no_change"}'],
        visible=True,
    )

    assert pull_result == push_result == NoChange()
    assert len(pull_model.calls) == len(push_model.calls) == 1
    assert pull_model.calls[0]["system_prompt"] == push_model.calls[0]["system_prompt"]
    assert pull_model.calls[0]["recent_context"] == push_model.calls[0]["recent_context"] == []
    pull_payload = _payload(pull_model.calls[0])
    push_payload = _payload(push_model.calls[0])
    assert "initial_execution_observation" not in pull_payload
    assert push_payload.pop("initial_execution_observation") == SNAPSHOT_PROJECTION
    assert push_payload == pull_payload
    assert [event.event_type for event in pull_trace.events] == [
        ACTIVATION_STARTED,
        MODEL_OUTPUT_RECORDED,
        ACTIVATION_FINISHED,
    ]
    assert [event.event_type for event in push_trace.events] == [
        ACTIVATION_STARTED,
        trace_module.INITIAL_EXECUTION_OBSERVED,
        MODEL_OUTPUT_RECORDED,
        ACTIVATION_FINISHED,
    ]


def test_e2_h2_pull_inspect_second_call_is_the_existing_projection(
    tmp_path: Path,
) -> None:
    result, model, _ = _run(
        tmp_path / "pull-inspect.jsonl",
        [
            '{"type":"capability_request","capability":"inspect_execution"}',
            '{"type":"no_change"}',
        ],
        visible=False,
    )

    assert result == NoChange()
    assert "initial_execution_observation" not in _payload(model.calls[0])
    second = _payload(model.calls[1])
    assert "initial_execution_observation" not in second
    assert second == {
        "activation": {
            "execution_goal_snapshot": ACTIVATION.execution_goal_snapshot,
            "execution_status": ACTIVATION.execution_status,
            "trigger": ACTIVATION.trigger,
        },
        "capability_request": {"capability": "inspect_execution"},
        "further_capability_allowed": False,
        "observation": SNAPSHOT_PROJECTION,
    }


def test_e2_h3_push_inspect_reuses_the_same_frozen_snapshot(
    tmp_path: Path,
) -> None:
    result, model, trace = _run(
        tmp_path / "push-inspect.jsonl",
        [
            '{"type":"capability_request","capability":"inspect_execution"}',
            '{"type":"no_change"}',
        ],
        visible=True,
    )

    assert result == NoChange()
    first = _payload(model.calls[0])
    second = _payload(model.calls[1])
    assert first["initial_execution_observation"] == SNAPSHOT_PROJECTION
    assert second["initial_execution_observation"] == SNAPSHOT_PROJECTION
    assert second["observation"] == SNAPSHOT_PROJECTION
    initial = next(
        event
        for event in trace.events
        if event.event_type == trace_module.INITIAL_EXECUTION_OBSERVED
    )
    observed = next(
        event for event in trace.events if event.event_type == CAPABILITY_OBSERVED
    )
    assert dict(initial.payload) == {
        "failure": None,
        "goal": SNAPSHOT.goal,
        "recent_outcome": SNAPSHOT.recent_outcome,
        "status": SNAPSHOT.status,
    }
    assert dict(observed.payload["observation"]) == SNAPSHOT_PROJECTION


def test_e2_h4_push_no_change_has_no_directive_or_mutation(
    tmp_path: Path,
) -> None:
    activation_before = copy.deepcopy(ACTIVATION)
    snapshot_before = copy.deepcopy(SNAPSHOT)
    result, _, trace = _run(
        tmp_path / "push-no-change.jsonl",
        ['{"type":"no_change"}'],
        visible=True,
    )

    assert result == NoChange()
    assert ACTIVATION == activation_before
    assert SNAPSHOT == snapshot_before
    assert MIND_DIRECTIVE_ISSUED not in {
        event.event_type for event in trace.events
    }
    assert MIND_DIRECTIVE_APPLIED not in {
        event.event_type for event in trace.events
    }
    assert dict(replay_activation(trace.events).final_result or {}) == {
        "type": "no_change"
    }


def test_e2_h5_push_directive_still_uses_d_and_e0(
    tmp_path: Path,
) -> None:
    directive_text = "Keep the already-read mission constraints in view."
    result, _, trace = _run(
        tmp_path / "push-directive.jsonl",
        [json.dumps({"type": "directive", "text": directive_text})],
        visible=True,
    )

    assert result == Directive(directive_text)
    assert [event.event_type for event in trace.events].count(
        MIND_DIRECTIVE_ISSUED
    ) == 1
    delivery = MindTrace.reopen_for_delivery(tmp_path / "push-directive.jsonl")
    application = prepare_for_execution_decision(
        delivery,
        "decision-000002",
        eligible=True,
    )
    assert application is not None
    assert decision_advisory_from(application) == (
        "decision-000002",
        application.as_model_context(),
    )
    assert [event.event_type for event in delivery.events].count(
        MIND_DIRECTIVE_APPLIED
    ) == 1


def test_e2_h6_reopen_reconstructs_exact_push_requests(
    tmp_path: Path,
) -> None:
    path = tmp_path / "push-replay.jsonl"
    result, model, trace = _run(
        path,
        [
            '{"type":"capability_request","capability":"inspect_execution"}',
            '{"type":"no_change"}',
        ],
        visible=True,
    )
    live_calls = copy.deepcopy(model.calls)
    del model, trace

    replay = replay_activation(MindTrace.reopen(path).events)

    assert result == NoChange()
    assert [request.as_model_call() for request in replay.model_requests] == live_calls
    assert len(replay.model_requests) == 2


def test_e2_h6_push_provider_failure_still_replays_the_sent_first_request(
    tmp_path: Path,
) -> None:
    path = tmp_path / "push-provider-failure.jsonl"
    result, model, trace = _run(
        path,
        [RuntimeError("provider secret must not escape")],
        visible=True,
    )
    live_calls = copy.deepcopy(model.calls)
    del model, trace

    replay = replay_activation(MindTrace.reopen(path).events)

    assert result == ActivationFailure("model_failed")
    assert [request.as_model_call() for request in replay.model_requests] == live_calls
    assert replay.failure_code == "model_failed"


def test_e2_h7_omitted_and_explicit_false_preserve_pull_bytes(
    tmp_path: Path,
) -> None:
    omitted_path = tmp_path / "omitted.jsonl"
    explicit_path = tmp_path / "explicit.jsonl"
    omitted_model = ScriptedModel(['{"type":"no_change"}'])
    explicit_model = ScriptedModel(['{"type":"no_change"}'])
    omitted_trace = _trace(omitted_path, activation_id="same-e2-pull")
    explicit_trace = _trace(explicit_path, activation_id="same-e2-pull")

    omitted_result = run_activation(
        ACTIVATION,
        model=omitted_model,
        memory_retriever=EmptyMemory(),
        execution_observation=SNAPSHOT,
        trace=omitted_trace,
    )
    explicit_result = run_activation(
        ACTIVATION,
        model=explicit_model,
        memory_retriever=EmptyMemory(),
        execution_observation=SNAPSHOT,
        initial_execution_observation_visible=False,
        trace=explicit_trace,
    )

    assert omitted_result == explicit_result == NoChange()
    assert omitted_model.calls == explicit_model.calls
    assert omitted_trace.events == explicit_trace.events
    assert omitted_path.read_bytes() == explicit_path.read_bytes()


def test_e2_visibility_flag_requires_a_frozen_snapshot_and_boolean(
    tmp_path: Path,
) -> None:
    missing_model = ScriptedModel(['{"type":"no_change"}'])
    invalid_model = ScriptedModel(['{"type":"no_change"}'])

    missing = run_activation(
        ACTIVATION,
        model=missing_model,
        memory_retriever=EmptyMemory(),
        execution_observation=None,
        initial_execution_observation_visible=True,
        trace=_trace(tmp_path / "missing.jsonl"),
    )
    invalid = run_activation(
        ACTIVATION,
        model=invalid_model,
        memory_retriever=EmptyMemory(),
        execution_observation=SNAPSHOT,
        initial_execution_observation_visible="yes",  # type: ignore[arg-type]
        trace=_trace(tmp_path / "invalid.jsonl"),
    )

    assert missing == ActivationFailure("execution_observation_unavailable")
    assert invalid == ActivationFailure("invalid_activation_input")
    assert missing_model.calls == invalid_model.calls == []


def test_e2_holdout_manifest_is_six_new_frozen_mechanical_tasks() -> None:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e2" / "manifest.json"
    )
    e1_ids = {
        "e1-01-ledger-replay",
        "e1-02-utc-window",
        "e1-03-dependency-lock",
        "e1-04-event-projection",
        "e1-05-constrained-route",
        "e1-06-grid-transform",
    }

    assert campaign.manifest_sha256 == (
        "b20d785ba6d9a5c32b672c03c92f507e121627ecf2cd59ae9f8cf00961f0656b"
    )
    assert len(campaign.tasks) == 6
    assert not ({task.task_id for task in campaign.tasks} & e1_ids)
    assert campaign.arm_order == (
        "pull_first",
        "push_first",
        "pull_first",
        "push_first",
        "pull_first",
        "push_first",
    )
    assert campaign.config.branch_after_root_decisions == 1
    for task in campaign.tasks:
        assert task.expected_content.startswith("~" * 1_100 + "\n")
        assert set(task.expected_content[:1_024]) == {"~"}
        assert task.expected_content.endswith("\n")
        assert task.completion_path not in {path for path, _ in task.files}


class VisibilitySensitiveMind:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, recent_context, user_message, *, system_prompt):
        payload = json.loads(user_message)
        self.calls.append(payload)
        if "initial_execution_observation" in payload:
            return '{"type":"no_change"}'
        return json.dumps(
            {
                "type": "directive",
                "text": "The mission is unavailable; recover it before continuing.",
            }
        )

    def summarize_hot_draft(self, old_summary, moved_turns):
        raise AssertionError("E2 Mind must not summarize Hot Draft")


def _campaign_test_task() -> FrozenTaskSpec:
    return FrozenTaskSpec(
        task_id="e2-test-information-pair",
        description="Exercise the PULL/PUSH paired seam.",
        goal="Read mission.txt and carry out its instructions.",
        files=(("mission.txt", b"Read this complete mission before acting.\n"),),
        completion_path="answer.txt",
        expected_content="~" * 1_100 + "\nDONE\n",
    )


def _campaign_test_config() -> E1Config:
    return E1Config(
        branch_after_root_decisions=1,
        max_additional_root_decisions=2,
        max_child_decisions=1,
        max_context_chars=5_000,
        wall_time_seconds=30.0,
    )


def test_e2_pair_runs_both_real_activation_paths_over_one_snapshot(
    tmp_path: Path,
) -> None:
    prefix = freeze_prefix(
        _campaign_test_task(),
        model=ExecutionScriptedModel([ToolCall(ReadRequest("mission.txt"))]),
        output_dir=tmp_path / "prefix",
        config=_campaign_test_config(),
    )
    mind = VisibilitySensitiveMind()

    paired = run_information_pair(
        prefix,
        pull_execution_model=ExecutionScriptedModel([Wait("e2-test-stop")]),
        push_execution_model=ExecutionScriptedModel([Wait("e2-test-stop")]),
        mind_model=mind,
        output_dir=tmp_path / "pair",
        push_first=True,
    )

    assert paired.snapshot is paired.pull.snapshot is paired.push.snapshot
    assert paired.pull.mind_result == Directive(
        "The mission is unavailable; recover it before continuing."
    )
    assert paired.push.mind_result == NoChange()
    assert paired.pull.directive_application is not None
    assert paired.push.directive_application is None
    assert "mind_supervisor_directive" in json.loads(
        paired.pull.execution.new_frames[0].actual_request.context
    )
    assert "mind_supervisor_directive" not in json.loads(
        paired.push.execution.new_frames[0].actual_request.context
    )
    assert trace_module.INITIAL_EXECUTION_OBSERVED not in {
        event.event_type for event in paired.pull.mind_trace.events
    }
    assert trace_module.INITIAL_EXECUTION_OBSERVED in {
        event.event_type for event in paired.push.mind_trace.events
    }
    assert len(mind.calls) == 2


def test_e2_directive_audit_document_is_blind_to_arm_task_and_outcome() -> None:
    runs = [
        {
            "task_id": "hidden-task",
            "classification": "PUSH_WIN",
            "execution_observation": SNAPSHOT_PROJECTION,
            "pull": {
                "verified_completion": False,
                "mind": {
                    "result": "Directive",
                    "directive_text": "The mission is unavailable.",
                },
                "delivery": {"applied": True},
            },
            "push": {
                "verified_completion": True,
                "mind": {
                    "result": "Directive",
                    "directive_text": "The mission was read; keep its constraints in view.",
                },
                "delivery": {"applied": True},
            },
        }
    ]

    document, private_mapping = build_blinded_directive_audit(runs)

    assert set(document) == {"items", "rubric", "schema"}
    assert len(document["items"]) == 2
    assert len(private_mapping) == 2
    for item in document["items"]:
        assert set(item) == {
            "directive_text",
            "execution_observation",
            "review_id",
        }
        rendered = json.dumps(item, sort_keys=True)
        assert "hidden-task" not in rendered
        assert "PUSH_WIN" not in rendered
        assert '"arm"' not in rendered
        assert "verified_completion" not in rendered


def test_e2_two_reviewer_merge_requires_complete_coverage_and_disagrees_to_uncertain() -> None:
    review_ids = ("directive-001", "directive-002")
    reviewer_a = {
        "directive-001": "SUPPORTED",
        "directive-002": "CONTRADICTED",
    }
    reviewer_b = {
        "directive-001": "UNCERTAIN",
        "directive-002": "CONTRADICTED",
    }

    assert merge_audit_reviews(review_ids, reviewer_a, reviewer_b) == {
        "directive-001": "UNCERTAIN",
        "directive-002": "CONTRADICTED",
    }
    with pytest.raises(ExperimentIntegrityError, match="audit_coverage"):
        merge_audit_reviews(
            review_ids,
            {"directive-001": "SUPPORTED"},
            reviewer_b,
        )


def test_e2_verdict_requires_numeric_and_information_quality_evidence() -> None:
    runs = [
        {
            "task_id": "t1",
            "classification": "PUSH_WIN",
            "pull": {
                "verified_completion": False,
                "mind": {"result": "Directive", "review_id": "directive-001"},
                "delivery": {"applied": True},
            },
            "push": {
                "verified_completion": True,
                "mind": {"result": "NoChange", "review_id": None},
                "delivery": {"applied": False},
            },
        },
        {
            "task_id": "t2",
            "classification": "PUSH_WIN",
            "pull": {
                "verified_completion": False,
                "mind": {"result": "NoChange", "review_id": None},
                "delivery": {"applied": False},
            },
            "push": {
                "verified_completion": True,
                "mind": {"result": "NoChange", "review_id": None},
                "delivery": {"applied": False},
            },
        },
    ]

    assert evaluate_verdict(
        runs,
        {"directive-001": "UNCERTAIN"},
    ) == "EXPERIMENT_E2_INCONCLUSIVE"
    with pytest.raises(ExperimentIntegrityError, match="audit_coverage_mismatch"):
        evaluate_verdict(runs, {})
    assert evaluate_verdict(
        runs,
        {"directive-001": "CONTRADICTED"},
    ) == "EXPERIMENT_E2_SUPPORTED"

    negative = copy.deepcopy(runs)
    for run in negative:
        run["classification"] = "PULL_WIN"
        run["pull"]["verified_completion"] = True
        run["push"]["verified_completion"] = False
    assert evaluate_verdict(
        negative,
        {"directive-001": "CONTRADICTED"},
    ) == "EXPERIMENT_E2_NEGATIVE"
