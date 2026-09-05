"""S0 adapter from immutable Execution evidence to the existing W0 trace."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from Execution import RealityEvidence

from world_model_experiment import (
    EnvironmentObservation,
    ExpectedObservation,
    Prediction,
    PredictionReplay,
    WorldModelTrace,
)


@dataclass(frozen=True)
class ScriptedTerminalPredictor:
    """Deterministic S0 predictor with no Execution or I/O capability."""

    expected_completion_verified: bool
    version: str = "wm-shadow-s0-v1"
    fail: bool = False

    def __post_init__(self) -> None:
        if type(self.expected_completion_verified) is not bool:
            raise TypeError("expected_completion_verified must be a boolean")
        if type(self.version) is not str or not self.version:
            raise TypeError("version must be a non-empty string")
        if type(self.fail) is not bool:
            raise TypeError("fail must be a boolean")

    def predict(self, evidence: RealityEvidence) -> ExpectedObservation:
        if self.fail:
            raise RuntimeError("injected S0 shadow failure")
        if evidence.kind != "PRE_OUTCOME":
            raise ValueError("prediction requires PRE_OUTCOME evidence")
        return ExpectedObservation(
            value=int(self.expected_completion_verified)
        )


@dataclass(frozen=True)
class ShadowCommit:
    trace: WorldModelTrace | None
    pre_evidence: RealityEvidence | None
    prediction: Prediction | None
    error: str | None


@dataclass(frozen=True)
class ShadowPairing:
    prediction_ref: str
    pre_evidence_ref: str
    outcome_evidence_ref: str
    source_event_refs: tuple[str, ...]
    comparison: str


@dataclass(frozen=True)
class ShadowResolution:
    replay: PredictionReplay | None
    pairing: ShadowPairing | None
    error: str | None


def _stable_ref(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def build_shadow_prediction(
    pre_evidence: RealityEvidence,
    predictor: ScriptedTerminalPredictor,
) -> Prediction:
    """Build one prediction while exposing only immutable evidence."""
    if type(pre_evidence) is not RealityEvidence:
        raise TypeError("pre_evidence must be RealityEvidence")
    if type(predictor) is not ScriptedTerminalPredictor:
        raise TypeError("predictor must be ScriptedTerminalPredictor")
    return Prediction(
        prediction_id=_stable_ref("s0-prediction", pre_evidence.evidence_ref),
        world_model_version=predictor.version,
        state_ref=pre_evidence.evidence_ref,
        action_ref=_stable_ref("s0-terminal", pre_evidence.execution_ref),
        expected_observation=predictor.predict(pre_evidence),
    )


def resolve_shadow_prediction(
    commit: ShadowCommit,
    outcome_evidence: RealityEvidence,
) -> ShadowResolution:
    """Fail-soft comparison against a later Execution-owned outcome."""
    try:
        if (
            commit.error is not None
            or commit.trace is None
            or commit.pre_evidence is None
            or commit.prediction is None
        ):
            raise ValueError("prediction is unavailable")
        if type(outcome_evidence) is not RealityEvidence:
            raise TypeError("outcome_evidence must be RealityEvidence")
        if outcome_evidence.kind != "OUTCOME":
            raise ValueError("comparison requires OUTCOME evidence")
        if outcome_evidence.execution_ref != commit.pre_evidence.execution_ref:
            raise ValueError("outcome belongs to another execution")
        if (
            commit.pre_evidence.source_event_refs[-1]
            not in outcome_evidence.source_event_refs
        ):
            raise ValueError("outcome does not descend from pre-outcome evidence")
        verified = outcome_evidence.payload.get("completion_verified")
        if type(verified) is bool:
            observed_value: int | None = int(verified)
        elif verified is None:
            observed_value = None
        else:
            raise ValueError("outcome completion value is malformed")
        observation = EnvironmentObservation(
            observation_id=outcome_evidence.evidence_ref,
            action_ref=commit.prediction.action_ref,
            observed_value=observed_value,
        )
        commit.trace.record_observation(observation)
        replay = commit.trace.resolve()
        return ShadowResolution(
            replay=replay,
            pairing=ShadowPairing(
                prediction_ref=commit.prediction.prediction_id,
                pre_evidence_ref=commit.pre_evidence.evidence_ref,
                outcome_evidence_ref=outcome_evidence.evidence_ref,
                source_event_refs=outcome_evidence.source_event_refs,
                comparison=replay.result.status.value,
            ),
            error=None,
        )
    except Exception as exc:
        return ShadowResolution(None, None, type(exc).__name__)
