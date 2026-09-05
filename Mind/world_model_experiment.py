"""Deterministic W0 prediction/observation experiment.

This module deliberately models one tiny counter domain.  The world model can
predict; the independent environment supplies the observed value; host-owned
code persists and compares the two.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


TRACE_FORMAT_VERSION = "world-model-w0-v1"
MAX_TRACE_BYTES = 64 * 1024
_EVENT_TYPES = (
    "WORLD_MODEL_PREDICTION_MADE",
    "ENVIRONMENT_OBSERVED",
    "WORLD_MODEL_PREDICTION_RESOLVED",
)
_MODEL_VERSION_PATTERN = re.compile(r"wm-[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class WorldModel(Protocol):
    """The complete W0 world-model authority."""

    version: str

    def predict(
        self,
        state: "CounterState",
        action: "CounterAction",
    ) -> "ExpectedObservation": ...


@dataclass(frozen=True)
class CounterState:
    state_ref: str
    value: int


@dataclass(frozen=True)
class CounterAction:
    action_ref: str
    delta: int


@dataclass(frozen=True)
class ExpectedObservation:
    value: int


@dataclass(frozen=True)
class Prediction:
    prediction_id: str
    world_model_version: str
    state_ref: str
    action_ref: str
    expected_observation: ExpectedObservation


@dataclass(frozen=True)
class EnvironmentObservation:
    observation_id: str
    action_ref: str
    observed_value: int | None


class PredictionStatus(str, Enum):
    MATCHED = "MATCHED"
    ERROR = "ERROR"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True)
class PredictionResult:
    status: PredictionStatus
    prediction_id: str
    observation_id: str
    world_model_version: str
    result_event_seq: int
    source_event_seqs: tuple[int, int]


@dataclass(frozen=True)
class PredictionReplay:
    prediction: Prediction
    observation: EnvironmentObservation
    result: PredictionResult


class TraceValidationError(ValueError):
    """The durable W0 history cannot be trusted or replayed."""


@dataclass(frozen=True)
class CounterWorldModel:
    version: str
    bias: int = 0

    def predict(
        self,
        state: CounterState,
        action: CounterAction,
    ) -> ExpectedObservation:
        return ExpectedObservation(value=state.value + action.delta + self.bias)


@dataclass(frozen=True)
class CounterEnvironment:
    evidence_available: bool = True

    def observe(
        self,
        state: CounterState,
        action: CounterAction,
        observation_id: str,
    ) -> EnvironmentObservation:
        value = state.value + action.delta if self.evidence_available else None
        return EnvironmentObservation(
            observation_id=observation_id,
            action_ref=action.action_ref,
            observed_value=value,
        )


def _compare(
    prediction: Prediction,
    observation: EnvironmentObservation,
) -> PredictionStatus:
    if observation.observed_value is None:
        return PredictionStatus.UNVERIFIABLE
    if observation.observed_value == prediction.expected_observation.value:
        return PredictionStatus.MATCHED
    return PredictionStatus.ERROR


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TraceValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_keys(value: object, expected: set[str], subject: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TraceValidationError(f"{subject} must be an object")
    if set(value) != expected:
        raise TraceValidationError(f"{subject} has invalid fields")
    return value


def _identifier(value: object, subject: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise TraceValidationError(f"{subject} must be a non-empty bounded string")
    return value


def _model_version(value: object) -> str:
    version = _identifier(value, "world_model_version")
    if _MODEL_VERSION_PATTERN.fullmatch(version) is None:
        raise TraceValidationError("world_model_version has invalid shape")
    return version


def _integer(value: object, subject: str) -> int:
    if type(value) is not int:
        raise TraceValidationError(f"{subject} must be an integer")
    return value


def _read_documents(path: Path) -> list[dict[str, object]]:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_TRACE_BYTES:
            raise TraceValidationError("trace size is invalid")
        if not raw.endswith(b"\n"):
            raise TraceValidationError("trace has an unterminated final event")
        text = raw.decode("utf-8")
        lines = text.splitlines()
        if not lines or any(not line for line in lines):
            raise TraceValidationError("trace contains an empty event")
        documents = [
            json.loads(line, object_pairs_hook=_strict_json_object)
            for line in lines
        ]
    except TraceValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise TraceValidationError("trace cannot be decoded") from exc
    if any(not isinstance(document, dict) for document in documents):
        raise TraceValidationError("every event must be an object")
    return documents


def _decode_replay(documents: list[dict[str, object]]) -> PredictionReplay:
    if len(documents) != 3:
        raise TraceValidationError("a replayable trace must contain exactly three events")

    expected_sources = ((), (1,), (1, 2))
    payloads: list[dict[str, object]] = []
    for index, document in enumerate(documents, start=1):
        event = _exact_keys(
            document,
            {
                "trace_format_version",
                "seq",
                "event_type",
                "payload",
                "source_event_seqs",
            },
            f"event {index}",
        )
        if event["trace_format_version"] != TRACE_FORMAT_VERSION:
            raise TraceValidationError("unknown trace format version")
        if event["seq"] != index:
            raise TraceValidationError("event sequence is not contiguous")
        if event["event_type"] != _EVENT_TYPES[index - 1]:
            raise TraceValidationError("invalid event ordering")
        sources = event["source_event_seqs"]
        if not isinstance(sources, list) or any(type(seq) is not int for seq in sources):
            raise TraceValidationError("causal refs must be integer event sequences")
        if len(sources) != len(set(sources)) or any(seq >= index for seq in sources):
            raise TraceValidationError("causal refs must be unique earlier events")
        if tuple(sources) != expected_sources[index - 1]:
            raise TraceValidationError("causal refs do not match the W0 lifecycle")
        payload = event["payload"]
        if not isinstance(payload, dict):
            raise TraceValidationError(f"event {index} payload must be an object")
        payloads.append(payload)

    prediction_payload = payloads[0]
    if set(prediction_payload) != {
        "prediction_id",
        "world_model_version",
        "state_ref",
        "action_ref",
        "expected_observation",
    }:
        raise TraceValidationError("prediction payload has invalid fields")
    expected_payload = _exact_keys(
        prediction_payload["expected_observation"],
        {"value"},
        "expected_observation",
    )
    prediction = Prediction(
        prediction_id=_identifier(prediction_payload["prediction_id"], "prediction_id"),
        world_model_version=_model_version(prediction_payload["world_model_version"]),
        state_ref=_identifier(prediction_payload["state_ref"], "state_ref"),
        action_ref=_identifier(prediction_payload["action_ref"], "action_ref"),
        expected_observation=ExpectedObservation(
            value=_integer(expected_payload["value"], "expected value")
        ),
    )

    observation_payload = payloads[1]
    if set(observation_payload) != {"observation_id", "action_ref", "observed_value"}:
        raise TraceValidationError("observation payload has invalid fields")
    observed_value = observation_payload["observed_value"]
    if observed_value is not None:
        observed_value = _integer(observed_value, "observed value")
    observation = EnvironmentObservation(
        observation_id=_identifier(observation_payload["observation_id"], "observation_id"),
        action_ref=_identifier(observation_payload["action_ref"], "observation action_ref"),
        observed_value=observed_value,
    )
    if observation.action_ref != prediction.action_ref:
        raise TraceValidationError("observation action_ref does not match prediction")

    result_payload = payloads[2]
    if set(result_payload) != {
        "status",
        "prediction_id",
        "observation_id",
        "world_model_version",
    }:
        raise TraceValidationError("result payload has invalid fields")
    try:
        status = PredictionStatus(result_payload["status"])
    except (TypeError, ValueError) as exc:
        raise TraceValidationError("result status is invalid") from exc
    if (
        result_payload["prediction_id"] != prediction.prediction_id
        or result_payload["observation_id"] != observation.observation_id
        or result_payload["world_model_version"] != prediction.world_model_version
    ):
        raise TraceValidationError("result provenance does not match its source events")
    if status is not _compare(prediction, observation):
        raise TraceValidationError("result does not match deterministic comparison")

    result = PredictionResult(
        status=status,
        prediction_id=prediction.prediction_id,
        observation_id=observation.observation_id,
        world_model_version=prediction.world_model_version,
        result_event_seq=3,
        source_event_seqs=(1, 2),
    )
    return PredictionReplay(prediction, observation, result)


class WorldModelTrace:
    """One append-only W0 prediction cycle."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._prediction: Prediction | None = None
        self._observation: EnvironmentObservation | None = None
        self._result: PredictionResult | None = None

    @classmethod
    def create(cls, path: str | Path) -> "WorldModelTrace":
        trace_path = Path(path)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("xb"):
            pass
        return cls(trace_path)

    @classmethod
    def reopen(cls, path: str | Path) -> "WorldModelTrace":
        trace = cls(Path(path))
        replay = _decode_replay(_read_documents(trace.path))
        trace._prediction = replay.prediction
        trace._observation = replay.observation
        trace._result = replay.result
        return trace

    def replay(self) -> PredictionReplay:
        if self._prediction is None or self._observation is None or self._result is None:
            raise ValueError("trace is incomplete")
        return PredictionReplay(self._prediction, self._observation, self._result)

    def record_prediction(self, prediction: Prediction) -> None:
        if self._prediction is not None:
            raise ValueError("prediction is already recorded")
        _identifier(prediction.prediction_id, "prediction_id")
        _model_version(prediction.world_model_version)
        _identifier(prediction.state_ref, "state_ref")
        _identifier(prediction.action_ref, "action_ref")
        _integer(prediction.expected_observation.value, "expected value")
        self._append(
            event_type="WORLD_MODEL_PREDICTION_MADE",
            payload={
                "prediction_id": prediction.prediction_id,
                "world_model_version": prediction.world_model_version,
                "state_ref": prediction.state_ref,
                "action_ref": prediction.action_ref,
                "expected_observation": {"value": prediction.expected_observation.value},
            },
            source_event_seqs=(),
        )
        self._prediction = prediction

    def record_observation(self, observation: EnvironmentObservation) -> None:
        if self._prediction is None:
            raise ValueError("prediction must be durable before observation")
        if self._observation is not None:
            raise ValueError("observation is already recorded")
        if observation.action_ref != self._prediction.action_ref:
            raise ValueError("observation action_ref does not match prediction")
        _identifier(observation.observation_id, "observation_id")
        _identifier(observation.action_ref, "observation action_ref")
        if observation.observed_value is not None:
            _integer(observation.observed_value, "observed value")
        self._append(
            event_type="ENVIRONMENT_OBSERVED",
            payload={
                "observation_id": observation.observation_id,
                "action_ref": observation.action_ref,
                "observed_value": observation.observed_value,
            },
            source_event_seqs=(1,),
        )
        self._observation = observation

    def resolve(self) -> PredictionReplay:
        if self._prediction is None or self._observation is None:
            raise ValueError("prediction and observation are required")
        if self._result is not None:
            raise ValueError("prediction is already resolved")

        status = _compare(self._prediction, self._observation)
        result = PredictionResult(
            status=status,
            prediction_id=self._prediction.prediction_id,
            observation_id=self._observation.observation_id,
            world_model_version=self._prediction.world_model_version,
            result_event_seq=3,
            source_event_seqs=(1, 2),
        )
        self._append(
            event_type="WORLD_MODEL_PREDICTION_RESOLVED",
            payload={
                "status": result.status.value,
                "prediction_id": result.prediction_id,
                "observation_id": result.observation_id,
                "world_model_version": result.world_model_version,
            },
            source_event_seqs=result.source_event_seqs,
        )
        self._result = result
        return PredictionReplay(self._prediction, self._observation, result)

    def _append(
        self,
        *,
        event_type: str,
        payload: dict[str, object],
        source_event_seqs: tuple[int, ...],
    ) -> None:
        document = {
            "trace_format_version": TRACE_FORMAT_VERSION,
            "seq": 1 + int(self._prediction is not None) + int(self._observation is not None),
            "event_type": event_type,
            "payload": payload,
            "source_event_seqs": list(source_event_seqs),
        }
        encoded = (
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        with self.path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())


def run_counter_prediction_cycle(
    *,
    trace_path: str | Path,
    world_model: WorldModel,
    environment: CounterEnvironment,
    state: CounterState,
    action: CounterAction,
    prediction_id: str,
    observation_id: str,
) -> PredictionReplay:
    trace = WorldModelTrace.create(trace_path)
    prediction = Prediction(
        prediction_id=prediction_id,
        world_model_version=world_model.version,
        state_ref=state.state_ref,
        action_ref=action.action_ref,
        expected_observation=world_model.predict(state, action),
    )
    trace.record_prediction(prediction)
    observation = environment.observe(state, action, observation_id)
    trace.record_observation(observation)
    return trace.resolve()
