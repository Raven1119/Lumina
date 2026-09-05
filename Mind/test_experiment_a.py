from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError, dataclass
from inspect import Parameter, signature
from pathlib import Path

import pytest

from Conversation_Memory.adapter.models import MemoryContext

from Mind.experiment_a import (
    EXPERIMENT_A_RECALL_POLICY,
    MAX_CAPABILITY_CALLS,
    MAX_DECISION_INTENT_CHARS,
    MAX_DIRECTIVE_CHARS,
    MAX_GOAL_CHARS,
    MAX_MEMORY_QUERY_CHARS,
    MAX_MODEL_CALLS,
    MAX_MODEL_OUTPUT_CHARS,
    MAX_OUTCOME_CHARS,
    MAX_STATUS_CHARS,
    MAX_TRIGGER_CHARS,
    ActivationFailure,
    ActivationInput,
    DecisionIntent,
    Directive,
    ExecutionObservation,
    NoChange,
    run_activation,
)


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
                "recent_context": recent_context,
                "user_message": user_message,
                "system_prompt": system_prompt,
            }
        )
        if not self._responses:
            raise AssertionError("unexpected model call")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeMemoryRetriever:
    def __init__(
        self,
        context: MemoryContext | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.context = context or MemoryContext(query="")
        self.error = error
        self.calls: list[tuple[str, object]] = []

    def recall(self, query: str, policy: object) -> MemoryContext:
        self.calls.append((query, policy))
        if self.error is not None:
            raise self.error
        return self.context


class ObservationAwareModel:
    """One deterministic model script used by both baseline and candidate."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        call = {
            "recent_context": recent_context,
            "user_message": user_message,
            "system_prompt": system_prompt,
        }
        self.calls.append(call)
        payload = json.loads(user_message)
        if "observation" in payload:
            assert payload["observation"]["rendered_evidence"] == (
                "The current direction depends on a disproven assumption."
            )
            return (
                '{"type":"directive","text":"Re-evaluate the disproven '
                'assumption before continuing."}'
            )
        if payload["information_acquisition_allowed"]:
            return (
                '{"type":"capability_request","capability":"recall_memory",'
                '"query":"current direction assumption"}'
            )
        return '{"type":"no_change"}'


ACTIVATION = ActivationInput(
    trigger="Execution reported a meaningful transition.",
    execution_goal_snapshot="Investigate the Recall regression.",
    execution_status="running",
)
EXECUTION_SNAPSHOT_SENTINEL = "EXECUTION_SNAPSHOT_SENTINEL"


def test_b1_run_activation_exposes_snapshot_not_execution_callable() -> None:
    parameters = signature(run_activation).parameters

    assert set(parameters) == {
        "activation",
        "model",
        "memory_retriever",
        "execution_observation",
        "initial_execution_observation_visible",
        "allow_information_acquisition",
        "trace",
    }
    assert "execution_observation" in parameters
    assert "inspect_execution" not in parameters
    assert all(
        parameter.kind is not Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def test_a1_direct_no_change_uses_one_model_call_and_no_capability() -> None:
    model = ScriptedModel(['{"type":"no_change"}'])
    memory = FakeMemoryRetriever()

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert result == NoChange()
    assert len(model.calls) == 1
    assert model.calls[0]["recent_context"] == []
    assert memory.calls == []


def test_a2_b9_memory_observation_reaches_the_second_model_call_without_snapshot_leak() -> None:
    model = ScriptedModel(
        [
            '{"type":"capability_request","capability":"recall_memory",'
            '"query":"earlier Recall constraints"}',
            '{"type":"directive","text":"Re-check the historical constraint '
            'before changing direction."}',
        ]
    )
    memory = FakeMemoryRetriever(
        MemoryContext(
            query="earlier Recall constraints",
            rendered_text="Historical evidence: keep Recall bounded.",
            truncated=False,
        )
    )
    execution_observation = ExecutionObservation(
        goal=EXECUTION_SNAPSHOT_SENTINEL,
        status="running",
    )

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=execution_observation,
    )

    assert result == Directive(
        "Re-check the historical constraint before changing direction."
    )
    assert memory.calls == [
        ("earlier Recall constraints", EXPERIMENT_A_RECALL_POLICY)
    ]
    assert len(model.calls) == 2
    continuation = json.loads(str(model.calls[1]["user_message"]))
    assert continuation["observation"] == {
        "capability": "recall_memory",
        "rendered_evidence": "Historical evidence: keep Recall bounded.",
        "safe_error_code": None,
        "status": "available",
        "truncated": False,
    }
    assert continuation["further_capability_allowed"] is False
    assert all(
        EXECUTION_SNAPSHOT_SENTINEL not in str(call["user_message"])
        for call in model.calls
    )


def test_a3_b2_execution_snapshot_preserves_the_cognitive_trajectory() -> None:
    model = ScriptedModel(
        [
            '{"type":"capability_request","capability":"inspect_execution"}',
            '{"type":"directive","text":"Reconsider the shared assumption '
            'behind the repeated failure."}',
        ]
    )
    memory = FakeMemoryRetriever()
    execution_observation = ExecutionObservation(
        goal="Investigate the Recall regression.",
        status="waiting",
        recent_outcome=EXECUTION_SNAPSHOT_SENTINEL,
        failure="evidence_mismatch",
    )

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=execution_observation,
    )

    assert result == Directive(
        "Reconsider the shared assumption behind the repeated failure."
    )
    assert memory.calls == []
    assert len(model.calls) == 2
    continuation_text = str(model.calls[1]["user_message"])
    continuation = json.loads(continuation_text)
    assert continuation["observation"] == {
        "capability": "inspect_execution",
        "failure": "evidence_mismatch",
        "goal": "Investigate the Recall regression.",
        "recent_outcome": EXECUTION_SNAPSHOT_SENTINEL,
        "status": "waiting",
    }
    assert EXECUTION_SNAPSHOT_SENTINEL not in str(model.calls[0]["user_message"])
    for forbidden in (
        "ExecutionOrgan",
        "events",
        "DecisionFrame",
        "provider",
        "run_goal",
        "interrupt",
        "resume",
    ):
        assert forbidden not in continuation_text


def test_b3_execution_request_without_snapshot_fails_safely() -> None:
    model = ScriptedModel(
        ['{"type":"capability_request","capability":"inspect_execution"}']
    )
    memory = FakeMemoryRetriever()

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert result == ActivationFailure("execution_observation_unavailable")
    assert len(model.calls) == 1
    assert memory.calls == []


def test_b4_authority_bearing_object_is_rejected_before_the_model() -> None:
    class AuthorityBearingObject:
        def __init__(self) -> None:
            self.mutation_calls = 0

        def interrupt(self) -> None:
            self.mutation_calls += 1

        def run_goal(self) -> None:
            self.mutation_calls += 1

    authority = AuthorityBearingObject()
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=authority,  # type: ignore[arg-type]
    )

    assert result == ActivationFailure("invalid_execution_observation")
    assert model.calls == []
    assert authority.mutation_calls == 0


def test_b5_malicious_execution_callable_has_no_invocation_path() -> None:
    mutation_calls = 0

    def malicious() -> ExecutionObservation:
        nonlocal mutation_calls
        mutation_calls += 1
        return ExecutionObservation(goal="mutated", status="running")

    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=malicious,  # type: ignore[arg-type]
    )

    assert result == ActivationFailure("invalid_execution_observation")
    assert model.calls == []
    assert mutation_calls == 0


def test_b6_execution_observation_subclass_is_rejected_without_leakage() -> None:
    @dataclass(frozen=True)
    class ExtendedExecutionObservation(ExecutionObservation):
        secret: str = ""
        authority_object: object | None = None

    snapshot = ExtendedExecutionObservation(
        goal="goal",
        status="running",
        secret="DO_NOT_LEAK",
        authority_object=lambda: None,
    )
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=snapshot,
    )

    assert result == ActivationFailure("invalid_execution_observation")
    assert model.calls == []


def test_b7_rejected_object_properties_are_never_read() -> None:
    property_reads = 0

    class HostileObservation:
        @property
        def goal(self) -> str:
            nonlocal property_reads
            property_reads += 1
            return "goal"

    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=HostileObservation(),  # type: ignore[arg-type]
    )

    assert result == ActivationFailure("invalid_execution_observation")
    assert model.calls == []
    assert property_reads == 0


def test_b7_exact_snapshot_rejects_string_subclass_without_calling_methods() -> None:
    method_calls = 0

    class SideEffectString(str):
        def strip(self, chars: str | None = None) -> str:
            nonlocal method_calls
            method_calls += 1
            return super().strip(chars)

        def __len__(self) -> int:
            nonlocal method_calls
            method_calls += 1
            return super().__len__()

    snapshot = ExecutionObservation(
        goal=SideEffectString("goal"),
        status="running",
    )
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=snapshot,
    )

    assert result == ActivationFailure("invalid_execution_observation")
    assert model.calls == []
    assert method_calls == 0


def test_b7_exact_snapshot_rejects_transitive_callable_without_invocation() -> None:
    mutation_calls = 0

    def malicious() -> str:
        nonlocal mutation_calls
        mutation_calls += 1
        return "secret"

    snapshot = ExecutionObservation(
        goal="goal",
        status="running",
        failure=malicious,  # type: ignore[arg-type]
    )
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=snapshot,
    )

    assert result == ActivationFailure("invalid_execution_observation")
    assert model.calls == []
    assert mutation_calls == 0


def test_b8_runner_has_no_execution_dependency_or_dynamic_authority_lookup() -> None:
    source = Path(__file__).with_name("experiment_a.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    imported_symbols: set[str] = set()
    called_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")
            imported_symbols.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)

    assert not any(
        module == "Execution" or module.startswith("Execution.")
        for module in imported_modules
    )
    assert "Callable" not in imported_symbols
    assert "inspect_execution" not in {
        node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)
    }
    assert called_names.isdisjoint(
        {"callable", "dir", "eval", "exec", "getattr", "hasattr", "vars"}
    )
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr
        in {"claim_complete", "interrupt", "resume", "run_goal", "spawn_child"}
        for node in ast.walk(tree)
    )


def test_a4_one_information_round_changes_the_scripted_judgment() -> None:
    model = ObservationAwareModel()
    memory = FakeMemoryRetriever(
        MemoryContext(
            query="current direction assumption",
            rendered_text=(
                "The current direction depends on a disproven assumption."
            ),
        )
    )
    baseline = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
        allow_information_acquisition=False,
    )
    candidate = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
        allow_information_acquisition=True,
    )

    assert baseline == NoChange()
    assert candidate == Directive(
        "Re-evaluate the disproven assumption before continuing."
    )
    assert len(model.calls) == 3
    assert model.calls[0]["system_prompt"] == model.calls[1]["system_prompt"]
    assert memory.calls == [
        ("current direction assumption", EXPERIMENT_A_RECALL_POLICY)
    ]


def test_a5_decision_intent_is_a_direct_semantic_result_only() -> None:
    model = ScriptedModel(
        [
            '{"type":"decision_intent","intent":"Request reconsideration '
            'of the current Intention."}'
        ]
    )
    memory = FakeMemoryRetriever()

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert result == DecisionIntent(
        "Request reconsideration of the current Intention."
    )
    assert len(model.calls) == 1
    assert memory.calls == []


def test_a6_unknown_capability_cannot_reach_any_dispatch() -> None:
    model = ScriptedModel(
        ['{"type":"capability_request","capability":"shell"}']
    )
    memory = FakeMemoryRetriever()

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert isinstance(result, ActivationFailure)
    assert result.code == "invalid_model_output"
    assert len(model.calls) == 1
    assert memory.calls == []


def test_baseline_rejects_even_a_valid_capability_without_dispatch() -> None:
    model = ScriptedModel(
        ['{"type":"capability_request","capability":"inspect_execution"}']
    )
    memory = FakeMemoryRetriever()

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
        allow_information_acquisition=False,
    )

    assert result == ActivationFailure("capability_not_available")
    assert len(model.calls) == 1
    assert memory.calls == []


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '```json\n{"type":"no_change"}\n```',
        '[]',
        '{"type":1}',
        '{"type":"not_a_result"}',
        '{"type":"no_change","extra":true}',
        '{"type":"directive"}',
        '{"type":"capability_request","capability":"recall_memory"}',
        '{"type":"capability_request","capability":"recall_memory",'
        '"query":"   "}',
        json.dumps(
            {
                "type": "capability_request",
                "capability": "recall_memory",
                "query": "q" * (MAX_MEMORY_QUERY_CHARS + 1),
            }
        ),
        json.dumps(
            {
                "type": "directive",
                "text": "d" * (MAX_DIRECTIVE_CHARS + 1),
            }
        ),
        '{"type":"no_change","type":"directive","text":"redirect"}',
        "[" * 999 + "0" + "]" * 999,
    ],
    ids=[
        "invalid-json",
        "markdown-fence",
        "non-object",
        "non-string-type",
        "invalid-type",
        "extra-key",
        "missing-final-field",
        "missing-query",
        "blank-query",
        "oversized-query",
        "oversized-directive",
        "duplicate-key",
        "deeply-nested-json",
    ],
)
def test_a7_malformed_protocol_fails_conservatively(raw: str) -> None:
    model = ScriptedModel([raw])
    memory = FakeMemoryRetriever()

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert isinstance(result, ActivationFailure)
    assert len(model.calls) == 1
    assert memory.calls == []


def test_a8_second_capability_request_fails_without_a_third_model_call() -> None:
    model = ScriptedModel(
        [
            '{"type":"capability_request","capability":"inspect_execution"}',
            '{"type":"capability_request","capability":"recall_memory",'
            '"query":"try again"}',
        ]
    )
    memory = FakeMemoryRetriever()
    execution_observation = ExecutionObservation(
        goal="Investigate the Recall regression.",
        status="waiting",
    )

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=execution_observation,
    )

    assert result == ActivationFailure("capability_limit_exceeded")
    assert len(model.calls) == 2
    assert memory.calls == []


def test_a9_provider_failure_returns_only_a_safe_failure_code() -> None:
    secret_detail = "provider-body=SECRET at C:\\private\\provider.json"
    model = ScriptedModel([RuntimeError(secret_detail)])
    memory = FakeMemoryRetriever()

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert result == ActivationFailure("model_failed")
    assert secret_detail not in repr(result)
    assert len(model.calls) == 1
    assert memory.calls == []


def test_a10_memory_exception_returns_no_detail_or_intervention() -> None:
    secret_detail = "internal=SECRET at C:\\private\\organ-state.json"
    first_response = (
        '{"type":"capability_request","capability":"recall_memory",'
        '"query":"bounded query"}'
    )
    memory = FakeMemoryRetriever(error=RuntimeError(secret_detail))
    model = ScriptedModel([first_response])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert result == ActivationFailure("memory_failed")
    assert secret_detail not in repr(result)
    assert len(model.calls) == 1


def test_a11_memory_safe_unavailable_becomes_a_bounded_observation() -> None:
    model = ScriptedModel(
        [
            '{"type":"capability_request","capability":"recall_memory",'
            '"query":"prior context"}',
            '{"type":"no_change"}',
        ]
    )
    memory = FakeMemoryRetriever(
        MemoryContext(
            query="prior context",
            safe_error_code="recall_unavailable",
        )
    )

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert result == NoChange()
    assert len(model.calls) == 2
    continuation = json.loads(str(model.calls[1]["user_message"]))
    assert continuation["observation"] == {
        "capability": "recall_memory",
        "rendered_evidence": "",
        "safe_error_code": "memory_unavailable",
        "status": "unavailable",
        "truncated": False,
    }


def test_activation_and_execution_observation_inputs_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        ACTIVATION.trigger = "mutated"  # type: ignore[misc]
    observation = ExecutionObservation(goal="goal", status="running")
    with pytest.raises(FrozenInstanceError):
        observation.status = "mutated"  # type: ignore[misc]


def test_activation_input_accepts_the_exact_limits() -> None:
    activation = ActivationInput(
        trigger="t" * MAX_TRIGGER_CHARS,
        execution_goal_snapshot="g" * MAX_GOAL_CHARS,
        execution_status="s" * MAX_STATUS_CHARS,
    )
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        activation,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
    )

    assert result == NoChange()
    assert len(model.calls) == 1


def test_activation_projection_ignores_subclass_fields() -> None:
    @dataclass(frozen=True)
    class ExtendedActivation(ActivationInput):
        extra: str

    activation = ExtendedActivation(
        trigger="trigger",
        execution_goal_snapshot="goal",
        execution_status="running",
        extra="UNBOUNDED_EXTRA",
    )
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        activation,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
    )

    assert result == NoChange()
    model_input = json.loads(str(model.calls[0]["user_message"]))
    assert model_input["activation"] == {
        "execution_goal_snapshot": "goal",
        "execution_status": "running",
        "trigger": "trigger",
    }


@pytest.mark.parametrize(
    "activation",
    [
        ActivationInput(
            trigger="t" * (MAX_TRIGGER_CHARS + 1),
            execution_goal_snapshot="goal",
            execution_status="running",
        ),
        ActivationInput(
            trigger="trigger",
            execution_goal_snapshot="g" * (MAX_GOAL_CHARS + 1),
            execution_status="running",
        ),
        ActivationInput(
            trigger="trigger",
            execution_goal_snapshot="goal",
            execution_status="s" * (MAX_STATUS_CHARS + 1),
        ),
        ActivationInput(
            trigger="   ",
            execution_goal_snapshot="goal",
            execution_status="running",
        ),
    ],
    ids=["trigger-too-long", "goal-too-long", "status-too-long", "blank-trigger"],
)
def test_invalid_activation_input_fails_before_the_model(
    activation: ActivationInput,
) -> None:
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        activation,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
    )

    assert result == ActivationFailure("invalid_activation_input")
    assert model.calls == []


def test_information_acquisition_flag_must_be_boolean() -> None:
    model = ScriptedModel(['{"type":"no_change"}'])

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
        allow_information_acquisition="yes",  # type: ignore[arg-type]
    )

    assert result == ActivationFailure("invalid_activation_input")
    assert model.calls == []


def test_oversized_raw_model_output_fails_before_dispatch() -> None:
    model = ScriptedModel(["x" * (MAX_MODEL_OUTPUT_CHARS + 1)])
    memory = FakeMemoryRetriever()

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert result == ActivationFailure("invalid_model_output")
    assert len(model.calls) == 1
    assert memory.calls == []


def test_oversized_memory_observation_fails_without_a_second_model_call() -> None:
    model = ScriptedModel(
        [
            '{"type":"capability_request","capability":"recall_memory",'
            '"query":"bounded query"}'
        ]
    )
    memory = FakeMemoryRetriever(
        MemoryContext(
            query="bounded query",
            rendered_text="e" * (EXPERIMENT_A_RECALL_POLICY.max_chars + 1),
        )
    )

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=memory,
        execution_observation=None,
    )

    assert result == ActivationFailure("observation_too_large")
    assert len(model.calls) == 1
    assert len(memory.calls) == 1


def test_oversized_execution_observation_fails_without_continuation() -> None:
    model = ScriptedModel(
        ['{"type":"capability_request","capability":"inspect_execution"}']
    )
    execution_observation = ExecutionObservation(
        goal="g" * MAX_GOAL_CHARS,
        status="s" * MAX_STATUS_CHARS,
        recent_outcome="o" * MAX_OUTCOME_CHARS,
        failure="failure",
    )

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=execution_observation,
    )

    assert result == ActivationFailure("observation_too_large")
    assert model.calls == []


def test_oversized_decision_intent_is_rejected() -> None:
    model = ScriptedModel(
        [
            json.dumps(
                {
                    "type": "decision_intent",
                    "intent": "i" * (MAX_DECISION_INTENT_CHARS + 1),
                }
            )
        ]
    )

    result = run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=FakeMemoryRetriever(),
        execution_observation=None,
    )

    assert isinstance(result, ActivationFailure)


def test_a12_b10_repeated_scripted_runs_are_deterministic() -> None:
    def run_once() -> tuple[object, list[dict[str, object]], list[tuple[str, object]]]:
        model = ScriptedModel(
            [
                '{"type":"capability_request","capability":"recall_memory",'
                '"query":"deterministic context"}',
                '{"type":"directive","text":"Keep the current high-level '
                'direction."}',
            ]
        )
        memory = FakeMemoryRetriever(
            MemoryContext(
                query="deterministic context",
                rendered_text="Stable bounded evidence.",
            )
        )
        result = run_activation(
            ACTIVATION,
            model=model,
            memory_retriever=memory,
            execution_observation=None,
        )
        return result, model.calls, memory.calls

    first = run_once()
    second = run_once()

    assert first == second
    assert first[0] == Directive("Keep the current high-level direction.")
    assert len(first[1]) == MAX_MODEL_CALLS
    assert len(first[2]) == MAX_CAPABILITY_CALLS
