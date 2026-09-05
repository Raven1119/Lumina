from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from Conversation_Memory.adapter.models import MemoryContext
from Mind import experiment_a
from Mind.experiment_a import (
    ActivationFailure,
    ActivationInput,
    Directive,
    ExecutionObservation,
    run_activation,
)
from Mind.trace import (
    ACTIVATION_FAILED,
    ACTIVATION_FINISHED,
    ACTIVATION_STARTED,
    CAPABILITY_OBSERVED,
    CAPABILITY_REQUESTED,
    MODEL_OUTPUT_RECORDED,
    PROJECTOR_VERSION,
    PROMPT_VERSION,
    TRACE_FORMAT_VERSION,
    ActivationReplay,
    MindTrace,
    ModelRequestProjection,
    TraceError,
    project_model_request,
    replay_activation,
)


FIXED_TIMESTAMP = "2026-08-30T12:00:00.000000Z"
ACTIVATION_ID = "experiment-c-activation"
ACTIVATION = ActivationInput(
    trigger="Execution reported a meaningful transition.",
    execution_goal_snapshot="Investigate the Recall regression.",
    execution_status="running",
)
EXPECTED_SYSTEM_PROMPT = """You are Lumina's bounded cognitive Mind experiment.
Return exactly one JSON object and no other text.
Allowed envelopes are:
{"type":"capability_request","capability":"recall_memory","query":"..."}
{"type":"capability_request","capability":"inspect_execution"}
{"type":"no_change"}
{"type":"directive","text":"..."}
{"type":"decision_intent","intent":"..."}
NoChange is valid when no intervention is warranted. A Directive must be
high-level advisory steering, never steps, commands, code, or a tool plan.
Only request information when the input says information acquisition is allowed."""


class ScriptedModel:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
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
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeMemoryRetriever:
    def __init__(self, context: MemoryContext | None = None) -> None:
        self.context = context or MemoryContext(query="")
        self.calls: list[tuple[str, object]] = []

    def recall(self, query: str, policy: object) -> MemoryContext:
        self.calls.append((query, policy))
        return self.context


def _canonical(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _start_payload() -> dict[str, object]:
    return {
        "activation": {
            "execution_goal_snapshot": ACTIVATION.execution_goal_snapshot,
            "execution_status": ACTIVATION.execution_status,
            "trigger": ACTIVATION.trigger,
        },
        "information_acquisition_allowed": True,
        "projector_version": PROJECTOR_VERSION,
        "prompt_version": PROMPT_VERSION,
    }


def _new_trace(path: Path) -> MindTrace:
    return MindTrace.create(
        path,
        activation_id=ACTIVATION_ID,
        fixed_timestamp=FIXED_TIMESTAMP,
    )


def _append_start(trace: MindTrace) -> None:
    trace.append(ACTIVATION_STARTED, _start_payload(), source_event_seqs=())


def _run_information_trajectory(
    path: Path,
) -> tuple[object, ScriptedModel, FakeMemoryRetriever, MindTrace]:
    trace = _new_trace(path)
    model = ScriptedModel(
        [
            '{"type":"capability_request","capability":"recall_memory",'
            '"query":"current direction assumption"}',
            '{"type":"directive","text":"Re-evaluate the disproven '
            'assumption before continuing."}',
        ]
    )
    memory = FakeMemoryRetriever(
        MemoryContext(
            query="current direction assumption",
            rendered_text="The current direction depends on a disproven assumption.",
            truncated=False,
        )
    )
    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
        trace=trace,
    )
    return result, model, memory, trace


def _calls(replay: ActivationReplay) -> list[dict[str, object]]:
    return [request.as_model_call() for request in replay.model_requests]


def test_c1_first_request_is_reconstructed_from_activation_started(
    tmp_path: Path,
) -> None:
    trace = _new_trace(tmp_path / "c1.jsonl")
    _append_start(trace)

    projection = project_model_request(trace.events)

    assert projection.as_model_call() == {
        "recent_context": [],
        "system_prompt": EXPECTED_SYSTEM_PROMPT,
        "user_message": _canonical(
            {
                "activation": _start_payload()["activation"],
                "information_acquisition_allowed": True,
            }
        ),
    }


def test_c2_information_continuation_is_reconstructed_from_event_prefix(
    tmp_path: Path,
) -> None:
    trace = _new_trace(tmp_path / "c2.jsonl")
    _append_start(trace)
    first_output = (
        '{"type":"capability_request","capability":"recall_memory",'
        '"query":"current direction assumption"}'
    )
    trace.append(
        MODEL_OUTPUT_RECORDED,
        {"call_index": 1, "text": first_output},
        source_event_seqs=(0,),
    )
    request = {
        "capability": "recall_memory",
        "query": "current direction assumption",
    }
    trace.append(
        CAPABILITY_REQUESTED,
        request,
        source_event_seqs=(1,),
    )
    observation = {
        "capability": "recall_memory",
        "rendered_evidence": (
            "The current direction depends on a disproven assumption."
        ),
        "safe_error_code": None,
        "status": "available",
        "truncated": False,
    }
    trace.append(
        CAPABILITY_OBSERVED,
        {"capability": "recall_memory", "observation": observation},
        source_event_seqs=(2,),
    )

    projection = project_model_request(trace.events)

    assert projection.as_model_call() == {
        "recent_context": [],
        "system_prompt": EXPECTED_SYSTEM_PROMPT,
        "user_message": _canonical(
            {
                "activation": _start_payload()["activation"],
                "capability_request": request,
                "further_capability_allowed": False,
                "observation": observation,
            }
        ),
    }


def test_c3_runner_delivers_the_projector_output_to_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_projector = experiment_a.project_model_request
    projected: list[ModelRequestProjection] = []

    def marked_projector(events: object) -> ModelRequestProjection:
        original = original_projector(events)
        marked = ModelRequestProjection(
            recent_context=original.recent_context,
            system_prompt=original.system_prompt + "\nPROJECTOR_SENTINEL",
            user_message=original.user_message,
        )
        projected.append(marked)
        return marked

    monkeypatch.setattr(experiment_a, "project_model_request", marked_projector)
    trace = _new_trace(tmp_path / "trace.jsonl")
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
        trace=trace,
    )

    assert result.__class__.__name__ == "NoChange"
    assert model.calls == [projected[0].as_model_call()]
    assert model.calls[0]["system_prompt"].endswith("PROJECTOR_SENTINEL")


def test_c4_durable_reopen_reconstructs_exact_requests_and_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "activation.jsonl"
    result, model, memory, trace = _run_information_trajectory(path)
    original_calls = copy.deepcopy(model.calls)
    original_bytes = path.read_bytes()

    assert result == Directive(
        "Re-evaluate the disproven assumption before continuing."
    )
    assert [query for query, _ in memory.calls] == ["current direction assumption"]
    assert len(original_bytes.splitlines()) == 6
    del model, memory, trace

    reopened = MindTrace.reopen(path)
    replay = replay_activation(reopened.events)

    assert _calls(replay) == original_calls
    assert dict(replay.final_result or {}) == {
        "text": "Re-evaluate the disproven assumption before continuing.",
        "type": "directive",
    }
    assert replay.failure_code is None
    assert path.read_bytes() == original_bytes


def test_c5_causal_provenance_connects_request_observation_and_result(
    tmp_path: Path,
) -> None:
    _, _, _, trace = _run_information_trajectory(tmp_path / "trace.jsonl")

    replay = replay_activation(trace.events)

    assert replay.causal_chain == (
        (0, ()),
        (1, (0,)),
        (2, (1,)),
        (3, (2,)),
        (4, (0, 2, 3)),
        (5, (4,)),
    )
    assert dict(replay.capability_request or {}) == {
        "capability": "recall_memory",
        "query": "current direction assumption",
    }
    assert dict(replay.capability_observation or {})["rendered_evidence"] == (
        "The current direction depends on a disproven assumption."
    )


def test_c6_failed_activation_replays_only_the_same_safe_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "failed.jsonl"
    trace = _new_trace(path)
    model = ScriptedModel(["not JSON"])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
        trace=trace,
    )

    assert result == ActivationFailure("invalid_model_output")
    assert [event.event_type for event in trace.events] == [
        ACTIVATION_STARTED,
        MODEL_OUTPUT_RECORDED,
        ACTIVATION_FAILED,
    ]
    reopened = MindTrace.reopen(path)
    replay = replay_activation(reopened.events)
    assert replay.final_result is None
    assert replay.failure_code == "invalid_model_output"
    assert _calls(replay) == model.calls
    assert "not JSON" in replay.model_outputs


def _write_corrupt_case(path: Path, case: str) -> None:
    trace = _new_trace(path)
    model = ScriptedModel(['{"type":"no_change"}'])
    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
        trace=trace,
    )
    assert result.__class__.__name__ == "NoChange"
    lines = path.read_text(encoding="utf-8").splitlines()
    documents = [json.loads(line) for line in lines]

    if case == "invalid-json":
        lines[1] = "{not-json"
    elif case == "seq-gap":
        documents[1]["seq"] = 2
        lines[1] = _canonical(documents[1])
    elif case == "duplicate-seq":
        documents[1]["seq"] = 0
        lines[1] = _canonical(documents[1])
    elif case == "unknown-event-type":
        documents[1]["event_type"] = "FUTURE_EVENT"
        lines[1] = _canonical(documents[1])
    elif case == "unknown-trace-version":
        documents[0]["trace_format_version"] = 999
        lines[0] = _canonical(documents[0])
    elif case == "unknown-projector-version":
        documents[0]["payload"]["projector_version"] = "future-projector"
        lines[0] = _canonical(documents[0])
    elif case == "unknown-prompt-version":
        documents[0]["payload"]["prompt_version"] = "future-prompt"
        lines[0] = _canonical(documents[0])
    elif case == "invalid-source-ref":
        documents[1]["source_event_seqs"] = [-1]
        lines[1] = _canonical(documents[1])
    elif case == "forward-source-ref":
        documents[1]["source_event_seqs"] = [2]
        lines[1] = _canonical(documents[1])
    elif case == "duplicate-source-ref":
        documents[1]["source_event_seqs"] = [0, 0]
        lines[1] = _canonical(documents[1])
    elif case == "mixed-activation-id":
        documents[1]["activation_id"] = "different-activation"
        lines[1] = _canonical(documents[1])
    elif case == "unknown-envelope-field":
        documents[1]["future"] = True
        lines[1] = _canonical(documents[1])
    elif case == "duplicate-json-key":
        lines[0] = lines[0].replace('"seq":0', '"seq":0,"seq":0', 1)
    elif case == "missing-final-newline":
        path.write_text("\n".join(lines), encoding="utf-8")
        return
    else:
        raise AssertionError(case)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "case",
    [
        "invalid-json",
        "seq-gap",
        "duplicate-seq",
        "unknown-event-type",
        "unknown-trace-version",
        "unknown-projector-version",
        "unknown-prompt-version",
        "invalid-source-ref",
        "forward-source-ref",
        "duplicate-source-ref",
        "mixed-activation-id",
        "unknown-envelope-field",
        "duplicate-json-key",
        "missing-final-newline",
    ],
)
def test_c7_corrupt_trace_fails_conservative_without_repair(
    tmp_path: Path,
    case: str,
) -> None:
    path = tmp_path / f"{case}.jsonl"
    _write_corrupt_case(path, case)
    corrupt_bytes = path.read_bytes()

    with pytest.raises(TraceError):
        MindTrace.reopen(path)

    assert path.read_bytes() == corrupt_bytes


def test_c7_incomplete_trace_cannot_be_replayed(tmp_path: Path) -> None:
    trace = _new_trace(tmp_path / "incomplete.jsonl")
    _append_start(trace)

    with pytest.raises(TraceError, match="incomplete"):
        replay_activation(trace.events)


def test_c8_event_snapshots_are_deeply_immutable_and_do_not_rewrite_disk(
    tmp_path: Path,
) -> None:
    path = tmp_path / "immutable.jsonl"
    _, _, _, trace = _run_information_trajectory(path)
    original_bytes = path.read_bytes()
    events = trace.events

    with pytest.raises(FrozenInstanceError):
        events[0].seq = 99  # type: ignore[misc]
    with pytest.raises(TypeError):
        events[0].payload["prompt_version"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        events[0].payload["activation"]["trigger"] = "mutated"  # type: ignore[index]

    assert MindTrace.reopen(path).events == events
    assert path.read_bytes() == original_bytes


def test_c9_same_durable_trace_replays_deterministically_twice(
    tmp_path: Path,
) -> None:
    path = tmp_path / "deterministic.jsonl"
    _, model, _, _ = _run_information_trajectory(path)
    reopened = MindTrace.reopen(path)

    first = replay_activation(reopened.events)
    second = replay_activation(reopened.events)

    assert first == second
    assert _calls(first) == _calls(second) == model.calls


def test_c10_candidate_matches_the_frozen_experiment_b_trajectory(
    tmp_path: Path,
) -> None:
    model = ScriptedModel(
        [
            '{"type":"capability_request","capability":"inspect_execution"}',
            '{"type":"directive","text":"Reconsider the shared '
            'assumption behind the repeated failure."}',
        ]
    )
    observation = {
        "capability": "inspect_execution",
        "failure": "evidence_mismatch",
        "goal": "Investigate the Recall regression.",
        "recent_outcome": "The same assumption failed again.",
        "status": "waiting",
    }

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=ExecutionObservation(
            goal="Investigate the Recall regression.",
            status="waiting",
            recent_outcome="The same assumption failed again.",
            failure="evidence_mismatch",
        ),
        trace=_new_trace(tmp_path / "candidate.jsonl"),
    )

    frozen_b_calls = [
        {
            "recent_context": [],
            "system_prompt": EXPECTED_SYSTEM_PROMPT,
            "user_message": _canonical(
                {
                    "activation": _start_payload()["activation"],
                    "information_acquisition_allowed": True,
                }
            ),
        },
        {
            "recent_context": [],
            "system_prompt": EXPECTED_SYSTEM_PROMPT,
            "user_message": _canonical(
                {
                    "activation": _start_payload()["activation"],
                    "capability_request": {"capability": "inspect_execution"},
                    "further_capability_allowed": False,
                    "observation": observation,
                }
            ),
        },
    ]
    assert model.calls == frozen_b_calls
    assert result == Directive(
        "Reconsider the shared assumption behind the repeated failure."
    )


def test_trace_versions_are_explicit_and_persisted(tmp_path: Path) -> None:
    path = tmp_path / "versions.jsonl"
    trace = _new_trace(path)
    _append_start(trace)
    persisted = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert persisted["trace_format_version"] == TRACE_FORMAT_VERSION
    assert persisted["payload"]["projector_version"] == PROJECTOR_VERSION
    assert persisted["payload"]["prompt_version"] == PROMPT_VERSION


def test_arbitrary_trace_like_object_is_not_invoked() -> None:
    class HostileTrace:
        invoked = False

        def append(self, *_: object, **__: object) -> None:
            self.invoked = True

    hostile = HostileTrace()
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
        trace=hostile,  # type: ignore[arg-type]
    )

    assert result == ActivationFailure("invalid_trace")
    assert hostile.invoked is False
    assert model.calls == []
