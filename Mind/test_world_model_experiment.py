from __future__ import annotations

import inspect
import json

import pytest

from world_model_experiment import (
    CounterAction,
    CounterEnvironment,
    CounterState,
    CounterWorldModel,
    EnvironmentObservation,
    ExpectedObservation,
    Prediction,
    PredictionStatus,
    WorldModel,
    WorldModelTrace,
    run_counter_prediction_cycle,
)


def test_w0_1_correct_prediction_is_durable_and_matched(tmp_path):
    trace_path = tmp_path / "matched.jsonl"

    replay = run_counter_prediction_cycle(
        trace_path=trace_path,
        world_model=CounterWorldModel(version="wm-v1"),
        environment=CounterEnvironment(),
        state=CounterState(state_ref="state-1", value=2),
        action=CounterAction(action_ref="action-1", delta=1),
        prediction_id="prediction-1",
        observation_id="observation-1",
    )

    assert replay.prediction.expected_observation.value == 3
    assert replay.observation.observed_value == 3
    assert replay.result.status is PredictionStatus.MATCHED

    documents = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [document["event_type"] for document in documents] == [
        "WORLD_MODEL_PREDICTION_MADE",
        "ENVIRONMENT_OBSERVED",
        "WORLD_MODEL_PREDICTION_RESOLVED",
    ]


def test_w0_2_incorrect_prediction_is_error_without_changing_observation(tmp_path):
    replay = run_counter_prediction_cycle(
        trace_path=tmp_path / "error.jsonl",
        world_model=CounterWorldModel(version="wm-v1", bias=-1),
        environment=CounterEnvironment(),
        state=CounterState(state_ref="state-2", value=2),
        action=CounterAction(action_ref="action-2", delta=2),
        prediction_id="prediction-2",
        observation_id="observation-2",
    )

    assert replay.prediction.expected_observation.value == 3
    assert replay.observation.observed_value == 4
    assert replay.result.status is PredictionStatus.ERROR


def test_w0_3_incomplete_observation_is_unverifiable(tmp_path):
    replay = run_counter_prediction_cycle(
        trace_path=tmp_path / "unverifiable.jsonl",
        world_model=CounterWorldModel(version="wm-v1"),
        environment=CounterEnvironment(evidence_available=False),
        state=CounterState(state_ref="state-3", value=8),
        action=CounterAction(action_ref="action-3", delta=-2),
        prediction_id="prediction-3",
        observation_id="observation-3",
    )

    assert replay.observation.observed_value is None
    assert replay.result.status is PredictionStatus.UNVERIFIABLE


def test_w0_4_prediction_is_durable_before_observation_and_reverse_is_rejected(tmp_path):
    trace_path = tmp_path / "ordered.jsonl"
    trace = WorldModelTrace.create(trace_path)
    state = CounterState(state_ref="state-4", value=5)
    action = CounterAction(action_ref="action-4", delta=1)
    prediction = Prediction(
        prediction_id="prediction-4",
        world_model_version="wm-v1",
        state_ref=state.state_ref,
        action_ref=action.action_ref,
        expected_observation=ExpectedObservation(value=6),
    )

    trace.record_prediction(prediction)
    durable_prefix = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(durable_prefix) == 1
    assert json.loads(durable_prefix[0])["event_type"] == "WORLD_MODEL_PREDICTION_MADE"

    observation = CounterEnvironment().observe(state, action, "observation-4")
    trace.record_observation(observation)
    assert trace.resolve().result.status is PredictionStatus.MATCHED

    reverse = WorldModelTrace.create(tmp_path / "reverse.jsonl")
    with pytest.raises(ValueError, match="prediction must be durable"):
        reverse.record_observation(observation)


def test_w0_5_wrong_action_linkage_is_not_compared_or_persisted(tmp_path):
    trace_path = tmp_path / "wrong-action.jsonl"
    trace = WorldModelTrace.create(trace_path)
    trace.record_prediction(
        Prediction(
            prediction_id="prediction-5",
            world_model_version="wm-v1",
            state_ref="state-5",
            action_ref="action-a",
            expected_observation=ExpectedObservation(value=1),
        )
    )

    with pytest.raises(ValueError, match="action_ref"):
        trace.record_observation(
            EnvironmentObservation(
                observation_id="observation-5",
                action_ref="action-b",
                observed_value=1,
            )
        )

    assert len(trace_path.read_text(encoding="utf-8").splitlines()) == 1


def test_w0_6_results_retain_the_prediction_model_version(tmp_path):
    inputs = {
        "environment": CounterEnvironment(),
        "state": CounterState(state_ref="same-state", value=10),
        "action": CounterAction(action_ref="same-action", delta=1),
        "observation_id": "same-observation",
    }
    replay_v1 = run_counter_prediction_cycle(
        trace_path=tmp_path / "v1.jsonl",
        world_model=CounterWorldModel(version="wm-v1"),
        prediction_id="prediction-v1",
        **inputs,
    )
    replay_v2 = run_counter_prediction_cycle(
        trace_path=tmp_path / "v2.jsonl",
        world_model=CounterWorldModel(version="wm-v2", bias=1),
        prediction_id="prediction-v2",
        **inputs,
    )

    assert replay_v1.prediction.expected_observation.value == 11
    assert replay_v2.prediction.expected_observation.value == 12
    assert replay_v1.result.world_model_version == "wm-v1"
    assert replay_v2.result.world_model_version == "wm-v2"
    assert replay_v1.result.status is PredictionStatus.MATCHED
    assert replay_v2.result.status is PredictionStatus.ERROR


def test_w0_7_reopen_and_replay_are_deterministic(tmp_path):
    trace_path = tmp_path / "replay.jsonl"
    original = run_counter_prediction_cycle(
        trace_path=trace_path,
        world_model=CounterWorldModel(version="wm-v1"),
        environment=CounterEnvironment(),
        state=CounterState(state_ref="state-7", value=4),
        action=CounterAction(action_ref="action-7", delta=-3),
        prediction_id="prediction-7",
        observation_id="observation-7",
    )

    first = WorldModelTrace.reopen(trace_path).replay()
    second = WorldModelTrace.reopen(trace_path).replay()

    assert first == second == original
    assert first.result.source_event_seqs == (1, 2)


def _documents(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_documents(path, documents):
    path.write_text(
        "".join(json.dumps(document, separators=(",", ":")) + "\n" for document in documents),
        encoding="utf-8",
    )


def _valid_trace(tmp_path):
    path = tmp_path / "valid.jsonl"
    run_counter_prediction_cycle(
        trace_path=path,
        world_model=CounterWorldModel(version="wm-v1"),
        environment=CounterEnvironment(),
        state=CounterState(state_ref="state-8", value=1),
        action=CounterAction(action_ref="action-8", delta=1),
        prediction_id="prediction-8",
        observation_id="observation-8",
    )
    return path


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_prediction",
        "missing_observation",
        "forward_causal_ref",
        "duplicate_result",
        "invalid_model_version_shape",
        "invalid_event_ordering",
        "falsified_result",
    ],
)
def test_w0_8_corrupt_history_fails_conservatively(tmp_path, corruption):
    path = _valid_trace(tmp_path)
    documents = _documents(path)

    if corruption == "missing_prediction":
        documents.pop(0)
    elif corruption == "missing_observation":
        documents.pop(1)
    elif corruption == "forward_causal_ref":
        documents[1]["source_event_seqs"] = [3]
    elif corruption == "duplicate_result":
        duplicate = dict(documents[2])
        duplicate["seq"] = 4
        documents.append(duplicate)
    elif corruption == "invalid_model_version_shape":
        documents[0]["payload"]["world_model_version"] = "v1"
        documents[2]["payload"]["world_model_version"] = "v1"
    elif corruption == "invalid_event_ordering":
        documents[0], documents[1] = documents[1], documents[0]
    elif corruption == "falsified_result":
        documents[2]["payload"]["status"] = "ERROR"

    _write_documents(path, documents)
    with pytest.raises(ValueError):
        WorldModelTrace.reopen(path)


def test_w0_8_duplicate_json_keys_fail_conservatively(tmp_path):
    path = _valid_trace(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"seq":1', '"seq":1,"seq":1')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError):
        WorldModelTrace.reopen(path)


def test_w0_8_non_terminated_final_record_fails_conservatively(tmp_path):
    path = _valid_trace(tmp_path)
    path.write_bytes(path.read_bytes().removesuffix(b"\n"))

    with pytest.raises(ValueError):
        WorldModelTrace.reopen(path)


def test_w0_9_environment_observation_remains_reality_authority(tmp_path):
    model = CounterWorldModel(version="wm-v1", bias=-1)
    replay = run_counter_prediction_cycle(
        trace_path=tmp_path / "environment-authority.jsonl",
        world_model=model,
        environment=CounterEnvironment(),
        state=CounterState(state_ref="state-9", value=2),
        action=CounterAction(action_ref="action-9", delta=2),
        prediction_id="prediction-9",
        observation_id="observation-9",
    )

    assert replay.prediction.expected_observation.value == 3
    assert replay.observation.observed_value == 4
    assert replay.result.status is PredictionStatus.ERROR
    assert model == CounterWorldModel(version="wm-v1", bias=-1)


def test_w0_10_world_model_has_prediction_only_surface():
    def public_methods(subject):
        return {
            name
            for name, member in inspect.getmembers(subject, predicate=inspect.isfunction)
            if not name.startswith("_")
        }

    assert public_methods(WorldModel) == {"predict"}
    assert public_methods(CounterWorldModel) == {"predict"}
    forbidden = {
        "plan",
        "subgoal",
        "heuristic",
        "choose_action",
        "set_intention",
        "directive",
    }
    assert forbidden.isdisjoint(dir(CounterWorldModel))
