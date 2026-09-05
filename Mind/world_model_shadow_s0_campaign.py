"""Deterministic host-owned S0 campaign; no provider or Builder."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from itertools import count
from pathlib import Path
from unittest.mock import patch

from Execution import ExecutionOrgan, FileContentEquals
from Execution.execution import (
    ClaimComplete,
    NativeModelDecision,
    ScriptedModel,
    Wait,
)

from world_model_experiment import PredictionStatus, WorldModelTrace
from world_model_shadow_s0 import (
    ScriptedTerminalPredictor,
    ShadowCommit,
    build_shadow_prediction,
    resolve_shadow_prediction,
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _evidence_bytes(evidence) -> bytes:
    return json.dumps(
        {
            "evidence_ref": evidence.evidence_ref,
            "execution_ref": evidence.execution_ref,
            "source_event_refs": evidence.source_event_refs,
            "kind": evidence.kind,
            "sequence": evidence.sequence,
            "payload": dict(evidence.payload),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _observable(result, steps, workspace: Path) -> dict[str, object]:
    document = {
        "result": {
            "status": result.status,
            "output": result.output,
            "failure": result.failure,
            "decision_count": result.state.decision_count,
        },
        "actions": [type(step.action).__name__ for step in steps],
        "events": [
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "source_event_refs": event.source_event_refs,
            }
            for event in result.events
        ],
        "workspace_effect_sha256": _sha256(
            (workspace / "answer.txt").read_bytes()
        ),
    }
    return document


def _native_completion_actions():
    return [
        NativeModelDecision(
            action=Wait("continue"),
            provider_wire_request={"body": "PROVIDER_BODY_SENTINEL"},
            raw_provider_response={
                "reasoning": "RAW_CODE_SENTINEL",
                "content": "FILE_CONTENT_SENTINEL",
            },
        ),
        NativeModelDecision(
            action=ClaimComplete(),
            provider_wire_request={"body": "PROVIDER_BODY_SENTINEL"},
            raw_provider_response={"content": "FILE_CONTENT_SENTINEL"},
        ),
    ]


def _new_organ(root: Path, actions, *, max_decisions: int):
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "answer.txt").write_text("done", encoding="utf-8")
    model = ScriptedModel(actions)
    organ = ExecutionOrgan(
        workspace=workspace,
        event_log_path=root / "state" / "execution.jsonl",
        max_decisions=max_decisions,
        model=model,
    )
    return organ, model, workspace


def _shadow_case(
    root: Path,
    *,
    name: str,
    expected_verified: bool,
    actions,
    wake_events: tuple[str, ...],
    max_decisions: int,
) -> tuple[dict[str, object], dict[str, object]]:
    organ, model, workspace = _new_organ(
        root,
        actions,
        max_decisions=max_decisions,
    )
    steps = []
    try:
        waiting = organ.run_goal(
            f"Run deterministic S0 case {name}.",
            FileContentEquals("answer.txt", "done"),
        )
        steps.extend(waiting.steps)
        pre_batch = organ.reality_evidence()
        if (
            waiting.status != "waiting"
            or len(pre_batch) != 1
            or pre_batch[0].kind != "PRE_OUTCOME"
        ):
            raise AssertionError("case did not expose one pre-outcome fact")
        pre = pre_batch[0]

        replay_organ = ExecutionOrgan(
            workspace=workspace,
            event_log_path=root / "state" / "execution.jsonl",
            max_decisions=max_decisions,
            model=ScriptedModel([]),
        )
        try:
            pre_restart_equal = replay_organ.reality_evidence() == pre_batch
        finally:
            replay_organ.shutdown()

        prediction = build_shadow_prediction(
            pre,
            ScriptedTerminalPredictor(expected_verified),
        )
        trace = WorldModelTrace.create(root / "shadow.jsonl")
        trace.record_prediction(prediction)
        commit = ShadowCommit(trace, pre, prediction, None)
        if organ.reality_evidence() != pre_batch:
            raise AssertionError("Execution changed during prediction commit")
        trace_before = (root / "shadow.jsonl").read_bytes()
        if trace_before.count(b"\n") != 1:
            raise AssertionError("prediction was not the sole pre-outcome trace fact")

        result = waiting
        for event_type in wake_events:
            result = organ.deliver_event(event_type)
            steps.extend(result.steps)
        outcomes = organ.reality_evidence(after_sequence=pre.sequence)
        if len(outcomes) != 1 or outcomes[0].kind != "OUTCOME":
            raise AssertionError("case did not expose one terminal outcome")
        outcome = outcomes[0]
        resolution = resolve_shadow_prediction(commit, outcome)
        if resolution.error is not None:
            raise AssertionError(f"comparison failed: {resolution.error}")
        final_trace = (root / "shadow.jsonl").read_bytes()
        trace_replay_equal = (
            WorldModelTrace.reopen(root / "shadow.jsonl").replay()
            == resolution.replay
        )
        terminal_projection = organ.reality_evidence()
    finally:
        organ.shutdown()

    reopened = ExecutionOrgan(
        workspace=workspace,
        event_log_path=root / "state" / "execution.jsonl",
        max_decisions=max_decisions,
        model=ScriptedModel([]),
    )
    try:
        outcome_restart_equal = reopened.reality_evidence() == terminal_projection
        pre_replay_after_outcome = any(
            item.evidence_ref == pre.evidence_ref
            for item in reopened.reality_evidence()
        )
    finally:
        reopened.shutdown()

    pre_bytes = _evidence_bytes(pre)
    outcome_bytes = _evidence_bytes(outcome)
    projection_bytes = pre_bytes + outcome_bytes
    redaction_checks = {
        "provider_body_excluded": b"PROVIDER_BODY_SENTINEL" not in projection_bytes,
        "raw_code_excluded": b"RAW_CODE_SENTINEL" not in projection_bytes,
        "file_content_excluded": b"FILE_CONTENT_SENTINEL" not in projection_bytes,
        "absolute_workspace_excluded": (
            str(workspace.resolve()).encode("utf-8") not in projection_bytes
        ),
    }
    case = {
        "name": name,
        "execution_ref": pre.execution_ref,
        "pre_evidence_ref": pre.evidence_ref,
        "prediction_ref": resolution.pairing.prediction_ref,
        "outcome_evidence_ref": outcome.evidence_ref,
        "source_event_refs": outcome.source_event_refs,
        "comparison": resolution.replay.result.status.value,
        "pre_sequence": pre.sequence,
        "outcome_sequence": outcome.sequence,
        "prediction_durable_before_outcome": (
            pre.sequence < outcome.sequence
            and pre.source_event_refs[-1] in outcome.source_event_refs
            and trace_before.count(b"\n") == 1
        ),
        "pre_projection_sha256": _sha256(pre_bytes),
        "outcome_projection_sha256": _sha256(outcome_bytes),
        "prediction_trace_sha256_before_outcome": _sha256(trace_before),
        "final_shadow_trace_sha256": _sha256(final_trace),
        "pre_restart_equal": pre_restart_equal,
        "pre_replay_after_outcome": pre_replay_after_outcome,
        "outcome_restart_equal": outcome_restart_equal,
        "trace_replay_equal": trace_replay_equal,
        "redaction_checks": redaction_checks,
        "scripted_model_decisions": len(model.received_requests),
    }
    return case, _observable(result, steps, workspace)


def _control_case(root: Path) -> dict[str, object]:
    organ, _, workspace = _new_organ(
        root,
        _native_completion_actions(),
        max_decisions=3,
    )
    steps = []
    try:
        waiting = organ.run_goal(
            "Run deterministic S0 case matched.",
            FileContentEquals("answer.txt", "done"),
        )
        steps.extend(waiting.steps)
        completed = organ.deliver_event("continue")
        steps.extend(completed.steps)
        return _observable(completed, steps, workspace)
    finally:
        organ.shutdown()


def _fail_soft_case(root: Path) -> dict[str, object]:
    organ, _, _ = _new_organ(
        root,
        [Wait("continue"), ClaimComplete()],
        max_decisions=3,
    )

    try:
        organ.run_goal(
            "Complete despite a shadow-only failure.",
            FileContentEquals("answer.txt", "done"),
        )
        pre = organ.reality_evidence()[0]
        try:
            build_shadow_prediction(
                pre,
                ScriptedTerminalPredictor(True, fail=True),
            )
        except Exception as exc:
            shadow_error = type(exc).__name__
        else:
            shadow_error = None
        completed = organ.deliver_event("continue")
    finally:
        organ.shutdown()
    return {
        "shadow_error": shadow_error,
        "execution_status": completed.status,
        "execution_failure": completed.failure,
    }


def run_campaign() -> dict[str, object]:
    deterministic_ids = (uuid.UUID(int=value) for value in count(1))
    with patch("Execution.execution.uuid.uuid4", side_effect=deterministic_ids), tempfile.TemporaryDirectory(
        prefix="lumina-shadow-s0-"
    ) as directory:
        root = Path(directory)
        matched, candidate_observable = _shadow_case(
            root / "matched",
            name="matched",
            expected_verified=True,
            actions=_native_completion_actions(),
            wake_events=("continue",),
            max_decisions=3,
        )
        error, _ = _shadow_case(
            root / "error",
            name="error",
            expected_verified=False,
            actions=[Wait("continue"), ClaimComplete()],
            wake_events=("continue",),
            max_decisions=3,
        )
        unverifiable, _ = _shadow_case(
            root / "unverifiable",
            name="unverifiable",
            expected_verified=True,
            actions=[Wait("first"), Wait("second")],
            wake_events=("first", "second"),
            max_decisions=2,
        )
        control_observable = _control_case(root / "control")
        fail_soft = _fail_soft_case(root / "fail-soft")

        redaction_checks = matched["redaction_checks"]
        predictor = ScriptedTerminalPredictor(True)
        forbidden_capabilities = (
            "run_goal",
            "resume",
            "interrupt",
            "deliver_event",
            "open_child",
            "ipython",
            "tool_host",
            "shell",
            "filesystem",
            "network",
            "decision_advisory",
        )
        authority_checks = {
            capability: not hasattr(predictor, capability)
            for capability in forbidden_capabilities
        }
        cases = [matched, error, unverifiable]
        safety_conditions = (
            [case["comparison"] for case in cases]
            == [
                PredictionStatus.MATCHED.value,
                PredictionStatus.ERROR.value,
                PredictionStatus.UNVERIFIABLE.value,
            ]
            and all(case["prediction_durable_before_outcome"] for case in cases)
            and all(case["pre_restart_equal"] for case in cases)
            and all(case["outcome_restart_equal"] for case in cases)
            and all(case["trace_replay_equal"] for case in cases)
            and candidate_observable == control_observable
            and fail_soft["shadow_error"] == "RuntimeError"
            and fail_soft["execution_status"] == "completed"
            and all(redaction_checks.values())
            and all(authority_checks.values())
        )
        pass_conditions = safety_conditions and all(
            case["pre_replay_after_outcome"] for case in cases
        )
        return {
            "artifact_format": "world-model-shadow-s0-v1",
            "single_variable": (
                "synthetic Reality Evidence -> Execution-derived Reality Evidence"
            ),
            "projection_bounds": {
                "max_evidence_items": 8,
                "max_payload_chars": 128,
                "max_source_refs": 16,
                "payload_schema": "fixed scalar fields only",
            },
            "cases": cases,
            "shadow_on_off_identical": candidate_observable == control_observable,
            "shadow_on_observable_sha256": _sha256(
                json.dumps(
                    candidate_observable,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ),
            "shadow_off_observable_sha256": _sha256(
                json.dumps(
                    control_observable,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ),
            "fail_soft": fail_soft,
            "authority_checks": authority_checks,
            "redaction_checks": redaction_checks,
            "provider_request_count": 0,
            "verdict": (
                "S0_PASS"
                if pass_conditions
                else "S0_INCONCLUSIVE"
                if safety_conditions
                else "S0_FAIL"
            ),
        }


def write_artifact(path: str | Path) -> dict[str, object]:
    artifact = run_campaign()
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            artifact,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    result = write_artifact(arguments.output)
    print(result["verdict"])
