from __future__ import annotations

import hashlib
import inspect
import json

import pytest

from Execution import ExecutionOrgan, FileContentEquals, RealityEvidence
from Execution.execution import ClaimComplete, ScriptedModel, Wait

from world_model_experiment import (
    ExpectedObservation,
    PredictionStatus,
    WorldModelTrace,
)
import world_model_shadow_s0
from world_model_shadow_s0 import (
    ScriptedTerminalPredictor,
    ShadowCommit,
    build_shadow_prediction,
    resolve_shadow_prediction,
)
from world_model_shadow_s0_campaign import run_campaign


def _organ(tmp_path, actions, *, max_decisions=3):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "answer.txt").write_text("done", encoding="utf-8")
    model = ScriptedModel(actions)
    organ = ExecutionOrgan(
        workspace=workspace,
        event_log_path=tmp_path / "state" / "execution.jsonl",
        max_decisions=max_decisions,
        model=model,
    )
    return organ, model


def _canonical_evidence(evidence):
    return json.dumps(
        {
            "evidence_ref": evidence.evidence_ref,
            "execution_ref": evidence.execution_ref,
            "source_event_refs": evidence.source_event_refs,
            "kind": evidence.kind,
            "sequence": evidence.sequence,
            "payload": dict(evidence.payload),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _observable(result, steps, workspace):
    return {
        "result": (
            result.status,
            result.output,
            result.failure,
            result.state.status,
            result.state.completion,
            result.state.failure,
            result.state.decision_count,
        ),
        "actions": tuple(type(step.action).__name__ for step in steps),
        "events": tuple(
            (event.sequence, event.event_type, event.source_event_refs)
            for event in result.events
        ),
        "workspace_sha256": hashlib.sha256(
            (workspace / "answer.txt").read_bytes()
        ).hexdigest(),
    }


def _record_prediction(trace_path, pre, predictor):
    prediction = build_shadow_prediction(pre, predictor)
    trace = WorldModelTrace.create(trace_path)
    trace.record_prediction(prediction)
    return ShadowCommit(trace, pre, prediction, None)


def test_prediction_is_durable_before_execution_outcome_and_resolves_matched(
    tmp_path,
):
    organ, _ = _organ(tmp_path, [Wait("continue"), ClaimComplete()])
    trace_path = tmp_path / "shadow" / "trace.jsonl"

    try:
        waiting = organ.run_goal(
            "Complete after the wake event.",
            FileContentEquals("answer.txt", "done"),
        )
        pre = organ.reality_evidence()[0]
        assert waiting.status == "waiting"
        assert pre.kind == "PRE_OUTCOME"
        assert not any(
            item.kind == "OUTCOME" for item in organ.reality_evidence()
        )

        commit = _record_prediction(
            trace_path,
            pre,
            ScriptedTerminalPredictor(expected_completion_verified=True),
        )
        assert commit.error is None
        assert trace_path.read_text(encoding="utf-8").count("\n") == 1

        completed = organ.deliver_event("continue")
        outcomes = tuple(
            item
            for item in organ.reality_evidence(after_sequence=pre.sequence)
            if item.kind == "OUTCOME"
        )
        resolution = resolve_shadow_prediction(commit, outcomes[0])
    finally:
        organ.shutdown()

    assert completed.status == "completed"
    assert resolution.error is None
    assert resolution.replay.result.status is PredictionStatus.MATCHED
    assert resolution.pairing.pre_evidence_ref == pre.evidence_ref
    assert resolution.pairing.outcome_evidence_ref == outcomes[0].evidence_ref
    assert pre.source_event_refs[-1] in outcomes[0].source_event_refs
    assert outcomes[0].sequence > pre.sequence

    reopened = type(commit.trace).reopen(trace_path).replay()
    assert reopened == resolution.replay
    assert _canonical_evidence(pre)


def test_hindsight_attempt_cannot_construct_pre_evidence_after_outcome(tmp_path):
    organ, _ = _organ(tmp_path, [Wait("continue"), ClaimComplete()])

    try:
        organ.run_goal(
            "Complete after wake.",
            FileContentEquals("answer.txt", "done"),
        )
        organ.deliver_event("continue")
        terminal_projection = organ.reality_evidence()
    finally:
        organ.shutdown()

    assert terminal_projection
    assert all(item.kind == "OUTCOME" for item in terminal_projection)
    with pytest.raises(ValueError):
        build_shadow_prediction(
            terminal_projection[0],
            ScriptedTerminalPredictor(True),
        )
    assert not (tmp_path / "hindsight.jsonl").exists()
    assert not hasattr(world_model_shadow_s0, "commit_shadow_prediction")


def test_execution_evidence_resolves_error_and_unverifiable(tmp_path):
    error_organ, _ = _organ(
        tmp_path / "error",
        [Wait("continue"), ClaimComplete()],
    )
    try:
        error_waiting = error_organ.run_goal(
            "Complete after wake.",
            FileContentEquals("answer.txt", "done"),
        )
        error_pre = error_organ.reality_evidence()[0]
        error_commit = _record_prediction(
            tmp_path / "error-shadow.jsonl",
            error_pre,
            ScriptedTerminalPredictor(expected_completion_verified=False),
        )
        error_result = error_organ.deliver_event("continue")
        error_outcome = error_organ.reality_evidence(
            after_sequence=error_pre.sequence
        )[-1]
        error_resolution = resolve_shadow_prediction(
            error_commit,
            error_outcome,
        )
    finally:
        error_organ.shutdown()

    unverifiable_organ, _ = _organ(
        tmp_path / "unverifiable",
        [Wait("first"), Wait("second")],
        max_decisions=2,
    )
    try:
        unverifiable_organ.run_goal(
            "Reach the deterministic decision limit.",
            FileContentEquals("answer.txt", "done"),
        )
        unverifiable_pre = unverifiable_organ.reality_evidence()[0]
        unverifiable_commit = _record_prediction(
            tmp_path / "unverifiable-shadow.jsonl",
            unverifiable_pre,
            ScriptedTerminalPredictor(expected_completion_verified=True),
        )
        second_wait = unverifiable_organ.deliver_event("first")
        unverifiable_result = unverifiable_organ.deliver_event("second")
        unverifiable_outcome = unverifiable_organ.reality_evidence(
            after_sequence=unverifiable_pre.sequence
        )[-1]
        unverifiable_resolution = resolve_shadow_prediction(
            unverifiable_commit,
            unverifiable_outcome,
        )
    finally:
        unverifiable_organ.shutdown()

    assert error_waiting.status == "waiting"
    assert error_result.status == "completed"
    assert error_resolution.replay.result.status is PredictionStatus.ERROR
    assert second_wait.status == "waiting"
    assert unverifiable_result.status == "failed"
    assert unverifiable_result.failure == "decision_limit_reached"
    assert (
        unverifiable_resolution.replay.result.status
        is PredictionStatus.UNVERIFIABLE
    )


def test_shadow_predictor_has_zero_execution_authority(tmp_path):
    organ, _ = _organ(tmp_path, [Wait("continue"), ClaimComplete()])

    class AuthorityLeakingPredictor:
        version = "wm-shadow-s0-authority-leak"

        def __init__(self, owner):
            self.owner = owner

        def predict(self, evidence):
            self.owner.deliver_event("continue")
            return ExpectedObservation(value=1)

    try:
        organ.run_goal(
            "Wait before completion.",
            FileContentEquals("answer.txt", "done"),
        )
        pre = organ.reality_evidence()[0]
        with pytest.raises(TypeError):
            build_shadow_prediction(pre, AuthorityLeakingPredictor(organ))
        assert organ.state.status == "waiting"
        completed = organ.deliver_event("continue")
    finally:
        organ.shutdown()

    assert completed.status == "completed"
    predictor = ScriptedTerminalPredictor(True)
    for capability in (
        "run_goal",
        "resume",
        "interrupt",
        "deliver_event",
        "open_child",
        "ipython",
        "tool_host",
        "shell",
        "filesystem",
        "decision_advisory",
    ):
        assert not hasattr(predictor, capability)
    module_source = inspect.getsource(world_model_shadow_s0)
    for forbidden_import in (
        "ExecutionOrgan",
        "DecisionFrame",
        "EventLog",
        "PersistentIPython",
        "ToolHost",
    ):
        assert forbidden_import not in module_source


def test_shadow_on_and_off_have_identical_execution_behavior(tmp_path):
    def run(root, *, shadow_enabled):
        organ, model = _organ(root, [Wait("continue"), ClaimComplete()])
        trace_path = root / "shadow.jsonl"
        try:
            waiting = organ.run_goal(
                "Complete after wake.",
                FileContentEquals("answer.txt", "done"),
            )
            pre = organ.reality_evidence()[0]
            commit = (
                _record_prediction(
                    trace_path,
                    pre,
                    ScriptedTerminalPredictor(True),
                )
                if shadow_enabled
                else None
            )
            completed = organ.deliver_event("continue")
            if commit is not None:
                outcome = organ.reality_evidence(
                    after_sequence=pre.sequence
                )[-1]
                assert resolve_shadow_prediction(commit, outcome).error is None
            return (
                _observable(
                    completed,
                    (*waiting.steps, *completed.steps),
                    root / "workspace",
                ),
                len(model.received_requests),
            )
        finally:
            organ.shutdown()

    control = run(tmp_path / "control", shadow_enabled=False)
    candidate = run(tmp_path / "candidate", shadow_enabled=True)

    assert candidate == control


def test_shadow_failures_do_not_block_or_reclassify_execution(tmp_path):
    organ, _ = _organ(tmp_path, [Wait("continue"), ClaimComplete()])

    try:
        waiting = organ.run_goal(
            "Complete despite shadow failure.",
            FileContentEquals("answer.txt", "done"),
        )
        pre = organ.reality_evidence()[0]
        with pytest.raises(ValueError):
            organ.reality_evidence(after_sequence=-1)
        with pytest.raises(RuntimeError):
            build_shadow_prediction(
                pre,
                ScriptedTerminalPredictor(True, fail=True),
            )
        prediction = build_shadow_prediction(
            pre,
            ScriptedTerminalPredictor(True),
        )
        with pytest.raises((FileExistsError, PermissionError)):
            trace = WorldModelTrace.create(tmp_path)
            trace.record_prediction(prediction)
        comparison_commit = _record_prediction(
            tmp_path / "comparison-shadow.jsonl",
            pre,
            ScriptedTerminalPredictor(True),
        )
        completed = organ.deliver_event("continue")
        failed_resolution = resolve_shadow_prediction(
            comparison_commit,
            pre,
        )
    finally:
        organ.shutdown()

    assert waiting.status == "waiting"
    assert completed.status == "completed"
    assert completed.failure is None
    assert failed_resolution.error == "ValueError"


def test_long_causal_lineage_keeps_the_projected_pre_reference(tmp_path):
    actions = [Wait(f"wake-{index}") for index in range(5)] + [ClaimComplete()]
    organ, _ = _organ(tmp_path, actions, max_decisions=6)

    try:
        organ.run_goal(
            "Complete after five wakes.",
            FileContentEquals("answer.txt", "done"),
        )
        pre = organ.reality_evidence()[0]
        commit = _record_prediction(
            tmp_path / "long-shadow.jsonl",
            pre,
            ScriptedTerminalPredictor(True),
        )
        for index in range(5):
            result = organ.deliver_event(f"wake-{index}")
        outcome = organ.reality_evidence(after_sequence=pre.sequence)[-1]
        resolution = resolve_shadow_prediction(commit, outcome)
    finally:
        organ.shutdown()

    assert result.status == "completed"
    assert len(outcome.source_event_refs) == 16
    assert pre.source_event_refs[-1] in outcome.source_event_refs
    assert resolution.error is None
    assert resolution.replay.result.status is PredictionStatus.MATCHED


def test_campaign_is_deterministic_across_fresh_runs():
    first = json.dumps(run_campaign(), separators=(",", ":"), sort_keys=True)
    second = json.dumps(run_campaign(), separators=(",", ":"), sort_keys=True)

    assert first == second


def test_no_provider_call_occurs(tmp_path, monkeypatch):
    def provider_forbidden():
        raise AssertionError("provider construction is forbidden in S0")

    monkeypatch.setattr("Execution.organ.DeepSeekModel", provider_forbidden)
    organ, model = _organ(tmp_path, [Wait("continue"), ClaimComplete()])
    try:
        organ.run_goal(
            "Use only the scripted model.",
            FileContentEquals("answer.txt", "done"),
        )
        completed = organ.deliver_event("continue")
    finally:
        organ.shutdown()

    assert completed.status == "completed"
    assert model.identifier == "scripted-model"
    assert len(model.received_requests) == 2
