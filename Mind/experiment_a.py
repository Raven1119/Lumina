"""Isolated Experiment A host for one bounded cognitive activation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from Conversation_Memory.adapter.interfaces import MemoryRetriever
from Conversation_Memory.adapter.models import MemoryContext, RecallPolicy
from core.model_client import ModelClient
from Mind.task_view import output_limit
from Mind.trace import (
    COGNITIVE_PROBE_PROJECTOR_VERSION,
    COGNITIVE_PROBE_PROMPT_VERSION,
    COGNITIVE_COMPACT_PROJECTOR_VERSION,
    COGNITIVE_COMPACT_PROMPT_VERSION,
    COGNITIVE_STEP_PROJECTOR_VERSION,
    COGNITIVE_STEP_PROMPT_VERSION,
    ACTIVATION_FAILED,
    ACTIVATION_FINISHED,
    ACTIVATION_STARTED,
    CAPABILITY_OBSERVED,
    CAPABILITY_REQUESTED,
    INITIAL_EXECUTION_OBSERVED,
    SUPERVISOR_EVIDENCE_OBSERVED,
    MIND_DIRECTIVE_ISSUED,
    MODEL_OUTPUT_RECORDED,
    MAX_DECISION_INTENT_CHARS,
    MAX_DIRECTIVE_CHARS,
    MAX_FAILURE_CHARS,
    MAX_GOAL_CHARS,
    MAX_MEMORY_QUERY_CHARS,
    MAX_MODEL_OUTPUT_CHARS,
    MAX_OBSERVATION_CHARS,
    MAX_OUTCOME_CHARS,
    MAX_SAFE_ERROR_CODE_CHARS,
    MAX_STATUS_CHARS,
    MAX_SUPERVISOR_BUNDLE_CHARS,
    MAX_SUPERVISOR_EVIDENCE_CHARS,
    MAX_SUPERVISOR_EVIDENCE_ITEMS,
    MAX_SUPERVISOR_SOURCE_REF_CHARS,
    MAX_SUPERVISOR_SOURCE_REFS,
    MAX_SUPERVISOR_TRIGGER_CHARS,
    MAX_TRIGGER_CHARS,
    PROJECTOR_VERSION,
    PROMPT_VERSION,
    SUPERVISOR_EVIDENCE_KINDS,
    SUPERVISOR_PROJECTOR_VERSION,
    SUPERVISOR_TRIGGER_TYPES,
    MindEvent,
    MindTrace,
    ModelRequestProjection,
    TraceError,
    directive_id_for,
    project_model_request,
)
from Mind.world_model import ModelComputationError, _request as _model_request, run_model


MAX_MODEL_CALLS = 2
MAX_CAPABILITY_CALLS = 1

EXPERIMENT_A_RECALL_POLICY = RecallPolicy(
    top_k=5,
    max_chars=2_000,
    max_evidence_items=3,
    max_graph_depth=1,
    max_nodes=20,
    final_min_score=0.144,
)

@dataclass(frozen=True)
class ActivationInput:
    trigger: str
    execution_goal_snapshot: str
    execution_status: str


@dataclass(frozen=True)
class NoChange:
    pass


@dataclass(frozen=True)
class Directive:
    text: str


@dataclass(frozen=True)
class DecisionIntent:
    intent: str


@dataclass(frozen=True)
class ExecutionObservation:
    goal: str
    status: str
    recent_outcome: str | None = None
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class SupervisorTrigger:
    trigger_type: str
    summary: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupervisorEvidence:
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class SupervisorEvidenceBundle:
    execution_observation: ExecutionObservation
    trigger: SupervisorTrigger
    recent_evidence: tuple[SupervisorEvidence, ...]


@dataclass(frozen=True)
class ActivationFailure:
    code: str


@dataclass(frozen=True)
class _CapabilityRequest:
    capability: str
    query: str | None = None
    model_request: dict | None = None


def run_activation(
    activation: ActivationInput,
    *,
    model: ModelClient,
    memory_retriever: MemoryRetriever,
    execution_observation: ExecutionObservation | None,
    initial_execution_observation_visible: bool = False,
    allow_information_acquisition: bool = True,
    trace: MindTrace | None = None,
) -> NoChange | Directive | DecisionIntent | ActivationFailure:
    """Run one isolated activation without applying its semantic result."""
    return _run_activation(
        activation,
        model=model,
        memory_retriever=memory_retriever,
        execution_observation=execution_observation,
        initial_execution_observation_visible=(
            initial_execution_observation_visible
        ),
        allow_information_acquisition=allow_information_acquisition,
        trace=trace,
        supervisor_evidence=None,
    )


def run_activation_with_supervisor_evidence(
    activation: ActivationInput,
    *,
    model: ModelClient,
    memory_retriever: MemoryRetriever,
    execution_observation: ExecutionObservation,
    supervisor_evidence: SupervisorEvidenceBundle,
    trace: MindTrace | None = None,
) -> NoChange | Directive | DecisionIntent | ActivationFailure:
    """Run the one-call E4 view candidate through the existing activation host."""
    return _run_activation(
        activation,
        model=model,
        memory_retriever=memory_retriever,
        execution_observation=execution_observation,
        initial_execution_observation_visible=True,
        allow_information_acquisition=False,
        trace=trace,
        supervisor_evidence=supervisor_evidence,
    )


def _run_activation(
    activation: ActivationInput,
    *,
    model: ModelClient,
    memory_retriever: MemoryRetriever,
    execution_observation: ExecutionObservation | None,
    initial_execution_observation_visible: bool,
    allow_information_acquisition: bool,
    trace: MindTrace | None,
    supervisor_evidence: SupervisorEvidenceBundle | None,
    cognitive_context: dict[str, object] | None = None,
    allow_model_computation: bool = False,
    defer_capabilities: tuple[str, ...] | None = None,
    native_protocol: str | None = None,
) -> NoChange | Directive | DecisionIntent | ActivationFailure:
    if (
        not _valid_activation(activation)
        or type(allow_information_acquisition) is not bool
        or type(initial_execution_observation_visible) is not bool
        or type(allow_model_computation) is not bool
        or (allow_model_computation and cognitive_context is None)
        or (cognitive_context is not None and (
            type(cognitive_context) is not dict or supervisor_evidence is not None
        ))
    ):
        return ActivationFailure("invalid_activation_input")
    prepared_execution_observation = None
    if execution_observation is not None:
        prepared = _execution_observation(execution_observation)
        if isinstance(prepared, ActivationFailure):
            return prepared
        prepared_execution_observation = prepared
    if (
        initial_execution_observation_visible
        and prepared_execution_observation is None
    ):
        return ActivationFailure("execution_observation_unavailable")
    prepared_supervisor_evidence = None
    if supervisor_evidence is not None:
        if (
            not initial_execution_observation_visible
            or allow_information_acquisition
            or prepared_execution_observation is None
        ):
            return ActivationFailure("invalid_supervisor_evidence")
        prepared_supervisor_evidence = _supervisor_evidence_payload(
            supervisor_evidence,
            execution_observation,
            prepared_execution_observation,
        )
        if isinstance(prepared_supervisor_evidence, ActivationFailure):
            return prepared_supervisor_evidence

    if trace is not None and type(trace) is not MindTrace:
        return ActivationFailure("invalid_trace")
    if trace is not None:
        if trace.events:
            return ActivationFailure("invalid_trace")
        return _run_valid_activation(
            activation,
            model=model,
            memory_retriever=memory_retriever,
            prepared_execution_observation=prepared_execution_observation,
            initial_execution_observation_visible=(
                initial_execution_observation_visible
            ),
            allow_information_acquisition=allow_information_acquisition,
            prepared_supervisor_evidence=prepared_supervisor_evidence,
            trace=trace,
            cognitive_context=cognitive_context,
            allow_model_computation=allow_model_computation,
            defer_capabilities=defer_capabilities,
            native_protocol=native_protocol,
        )

    try:
        with TemporaryDirectory(prefix="lumina-mind-") as directory:
            temporary_trace = MindTrace.create(
                Path(directory) / "activation.jsonl",
                activation_id="ephemeral-activation",
            )
            return _run_valid_activation(
                activation,
                model=model,
                memory_retriever=memory_retriever,
                prepared_execution_observation=prepared_execution_observation,
                initial_execution_observation_visible=(
                    initial_execution_observation_visible
                ),
                allow_information_acquisition=allow_information_acquisition,
                prepared_supervisor_evidence=prepared_supervisor_evidence,
                trace=temporary_trace,
                cognitive_context=cognitive_context,
                allow_model_computation=allow_model_computation,
                defer_capabilities=defer_capabilities,
                native_protocol=native_protocol,
            )
    except (OSError, TraceError):
        return ActivationFailure("trace_failed")


def _run_valid_activation(
    activation: ActivationInput,
    *,
    model: ModelClient,
    memory_retriever: MemoryRetriever,
    prepared_execution_observation: dict[str, object] | None,
    initial_execution_observation_visible: bool,
    allow_information_acquisition: bool,
    prepared_supervisor_evidence: dict[str, object] | None,
    trace: MindTrace,
    cognitive_context: dict[str, object] | None = None,
    allow_model_computation: bool = False,
    defer_capabilities: tuple[str, ...] | None = None,
    native_protocol: str | None = None,
) -> NoChange | Directive | DecisionIntent | ActivationFailure:
    probe = cognitive_context.get("model_probe") if cognitive_context else None
    started = _append_event(
        trace,
        ACTIVATION_STARTED,
        {
            "activation": _activation_payload(activation),
            "information_acquisition_allowed": allow_information_acquisition,
            "projector_version": (
                COGNITIVE_PROBE_PROJECTOR_VERSION if cognitive_context is not None
                and (probe is not None or "model_feedback" in cognitive_context)
                else COGNITIVE_COMPACT_PROJECTOR_VERSION if allow_model_computation
                else COGNITIVE_STEP_PROJECTOR_VERSION if cognitive_context is not None
                else SUPERVISOR_PROJECTOR_VERSION
                if prepared_supervisor_evidence is not None
                else PROJECTOR_VERSION
            ),
            "prompt_version": (
                COGNITIVE_PROBE_PROMPT_VERSION if cognitive_context is not None
                and (probe is not None or "model_feedback" in cognitive_context)
                else COGNITIVE_COMPACT_PROMPT_VERSION if allow_model_computation
                else COGNITIVE_STEP_PROMPT_VERSION if cognitive_context is not None else PROMPT_VERSION
            ),
            **({"cognitive_context": cognitive_context,
                "available_capabilities": (list(defer_capabilities) if defer_capabilities is not None else [
                    *(["recall_memory"] if memory_retriever is not None else []),
                    *(["inspect_execution"] if prepared_execution_observation is not None else []),
                    *(["evaluate_model" if probe is not None else "run_world_model"]
                      if allow_model_computation else []),
                ] if allow_information_acquisition else [])}
               if cognitive_context is not None else {}),
            **({"native_protocol": native_protocol} if native_protocol else {}),
        },
        (),
    )
    if started is None:
        return ActivationFailure("trace_failed")

    initial_execution = None
    if initial_execution_observation_visible:
        assert prepared_execution_observation is not None
        initial_execution = _append_event(
            trace,
            INITIAL_EXECUTION_OBSERVED,
            {
                "failure": prepared_execution_observation["failure"],
                "goal": prepared_execution_observation["goal"],
                "recent_outcome": prepared_execution_observation[
                    "recent_outcome"
                ],
                "status": prepared_execution_observation["status"],
            },
            (started.seq,),
        )
        if initial_execution is None:
            return ActivationFailure("trace_failed")

    supervisor_evidence_event = None
    if prepared_supervisor_evidence is not None:
        assert initial_execution is not None
        supervisor_evidence_event = _append_event(
            trace,
            SUPERVISOR_EVIDENCE_OBSERVED,
            prepared_supervisor_evidence,
            (started.seq, initial_execution.seq),
        )
        if supervisor_evidence_event is None:
            return ActivationFailure("trace_failed")

    first_dependencies = (
        (started.seq,)
        if initial_execution is None
        else (
            started.seq,
            initial_execution.seq,
            *(
                (supervisor_evidence_event.seq,)
                if supervisor_evidence_event is not None
                else ()
            ),
        )
    )

    first = _project_and_call(model, trace)
    if isinstance(first, ActivationFailure):
        return _record_failure(trace, first, first_dependencies)
    first_output = _append_event(
        trace,
        MODEL_OUTPUT_RECORDED,
        {"call_index": 1, "text": first},
        first_dependencies,
    )
    if first_output is None:
        return ActivationFailure("trace_failed")
    parsed = _parse_output(first, unified=cognitive_context is not None, compute=allow_model_computation)
    if not isinstance(parsed, _CapabilityRequest):
        return _record_terminal(trace, parsed, (first_output.seq,))

    request = _append_event(
        trace,
        CAPABILITY_REQUESTED,
        _request_payload(parsed),
        (first_output.seq,),
    )
    if request is None:
        return ActivationFailure("trace_failed")
    if (not allow_information_acquisition or (cognitive_context is not None
            and parsed.capability not in started.payload["available_capabilities"])):
        return _record_failure(
            trace,
            ActivationFailure("capability_not_available"),
            (request.seq,),
        )
    if defer_capabilities is not None:
        return parsed  # Durable request; the organ returns control to Nervous.
    observation = _resolve_capability(
        parsed, memory_retriever=memory_retriever,
        prepared_execution_observation=prepared_execution_observation,
        probe=probe, allow_model_computation=allow_model_computation,
    )
    return _continue_activation(trace=trace, model=model, observation=observation,
                                cognitive=cognitive_context is not None, compute=allow_model_computation)


def _resolve_capability(parsed, *, memory_retriever, prepared_execution_observation,
                        probe, allow_model_computation):
    """Explicit host dispatch; current Mind emits data instead of calling this."""
    if parsed.capability == "recall_memory":
        if parsed.query is None:
            return ActivationFailure("invalid_model_output")
        try:
            context = memory_retriever.recall(
                parsed.query,
                EXPERIMENT_A_RECALL_POLICY,
            )
        except Exception:
            return ActivationFailure("memory_failed")
        observation = _memory_observation(context)
    elif parsed.capability == "inspect_execution":
        if prepared_execution_observation is None:
            return ActivationFailure("execution_observation_unavailable")
        observation = prepared_execution_observation
    elif parsed.capability in {"run_world_model", "evaluate_model"} and allow_model_computation:
        try:
            request_data = parsed.model_request
            if parsed.capability == "evaluate_model":
                if probe is None or request_data["probe_ref"] != probe["ref"]:
                    raise ValueError("unknown_model_probe")
                request_data = {"source": request_data["source"], "initial_observation": probe["initial_observation"],
                                "actions": list(probe["actions"])}
            computed = run_model(**request_data)
        except ModelComputationError as exc:
            code = str(exc)
            allowed = {"container_cleanup_failed", "container_output_incomplete", "computation_timeout",
                       "container_unavailable", "computation_output_limit", "computation_failed",
                       "invalid_computation_output", "model_load_error", "model_initialization_error",
                       "model_render_error", "model_outcome_error", "model_transition_error", "model_serialization_error"}
            observation = {"capability": parsed.capability, "status": "failed", "result": None,
                           "safe_error_code": code if code in allowed else "computation_failed"}
        except ValueError:
            observation = {"capability": parsed.capability, "status": "failed", "result": None,
                           "safe_error_code": "invalid_model_request"}
        else:
            observation = {"capability": parsed.capability, "status": "computed", "result": computed,
                           "safe_error_code": None}
        if len(_json_message(observation)) > MAX_OBSERVATION_CHARS:
            observation = ActivationFailure("observation_too_large")
    else:
        return ActivationFailure("invalid_model_output")
    return observation



def _continue_activation(*, trace, model, observation, cognitive, compute):
    """Continue exactly once from an already durable capability request."""
    started = trace.events[0]
    request = trace.events[-1]
    if request.event_type != CAPABILITY_REQUESTED:
        return ActivationFailure("invalid_trace")
    if isinstance(observation, ActivationFailure):
        return _record_failure(trace, observation, (request.seq,))
    initial_execution = next((event for event in trace.events if event.event_type == INITIAL_EXECUTION_OBSERVED), None)
    supervisor_evidence_event = next((event for event in trace.events if event.event_type == SUPERVISOR_EVIDENCE_OBSERVED), None)
    observation_dependencies = (
        (request.seq,)
        if initial_execution is None or request.payload["capability"] != "inspect_execution"
        else (initial_execution.seq, request.seq)
    )
    observed = _append_event(
        trace,
        CAPABILITY_OBSERVED,
        {"capability": request.payload["capability"], "observation": observation},
        observation_dependencies,
    )
    if observed is None:
        return ActivationFailure("trace_failed")

    second_dependencies = (
        (started.seq, request.seq, observed.seq)
        if initial_execution is None
        else (
            started.seq,
            initial_execution.seq,
            *(
                (supervisor_evidence_event.seq,)
                if supervisor_evidence_event is not None
                else ()
            ),
            request.seq,
            observed.seq,
        )
    )
    second = _project_and_call(model, trace)
    if isinstance(second, ActivationFailure):
        return _record_failure(trace, second, second_dependencies)
    second_output = _append_event(
        trace,
        MODEL_OUTPUT_RECORDED,
        {"call_index": 2, "text": second},
        second_dependencies,
    )
    if second_output is None:
        return ActivationFailure("trace_failed")
    final = _parse_output(second, unified=cognitive, compute=compute)
    if isinstance(final, _CapabilityRequest):
        final = ActivationFailure("capability_limit_exceeded")
    return _record_terminal(trace, final, (second_output.seq,))


def _project_and_call(
    model: ModelClient,
    trace: MindTrace,
) -> str | ActivationFailure:
    try:
        projection = project_model_request(trace.events)
    except TraceError:
        return ActivationFailure("trace_failed")
    return _call_model(model, projection, trace=trace)


def _call_model(
    model: ModelClient,
    projection: ModelRequestProjection,
    *, trace: MindTrace | None = None,
) -> str | ActivationFailure:
    call = projection.as_model_call()
    try:
        raw = model.generate_from_trace(trace, projection) if trace and trace.events[0].payload.get("native_protocol") else model.generate(
            call["recent_context"],  # type: ignore[arg-type]
            call["user_message"],  # type: ignore[arg-type]
            system_prompt=call["system_prompt"],  # type: ignore[arg-type]
        )
    except TraceError:
        return ActivationFailure("trace_failed")
    except Exception:
        return ActivationFailure("model_failed")
    limit = output_limit(trace.events[0].payload.get("cognitive_context", {})) if trace else MAX_MODEL_OUTPUT_CHARS
    if not isinstance(raw, str) or len(raw) > limit:
        return ActivationFailure("invalid_model_output")
    return raw


def _resume_native_activation(trace, model):
    """Finish an uncommitted logical step from its durable native annex."""
    first = not any(e.event_type == CAPABILITY_OBSERVED for e in trace.events)
    initial = next((e for e in trace.events if e.event_type == INITIAL_EXECUTION_OBSERVED), None)
    dependencies = (0, *([initial.seq] if initial else []), *(
        [e.seq for e in trace.events if e.event_type in {CAPABILITY_REQUESTED, CAPABILITY_OBSERVED}]
        if not first else []))
    raw = _project_and_call(model, trace)
    if isinstance(raw, ActivationFailure):
        return _record_failure(trace, raw, dependencies)
    event = _append_event(trace, MODEL_OUTPUT_RECORDED, {"call_index": 1 if first else 2, "text": raw}, dependencies)
    if event is None:
        return ActivationFailure("trace_failed")
    parsed = _parse_output(raw, unified=True)
    if isinstance(parsed, _CapabilityRequest):
        if not first or parsed.capability not in trace.events[0].payload["available_capabilities"]:
            return _record_failure(trace, ActivationFailure("capability_limit_exceeded"), (event.seq,))
        if _append_event(trace, CAPABILITY_REQUESTED, _request_payload(parsed), (event.seq,)) is None:
            return ActivationFailure("trace_failed")
        return parsed
    return _record_terminal(trace, parsed, (event.seq,))


def _append_event(
    trace: MindTrace,
    event_type: str,
    payload: dict[str, object],
    source_event_seqs: tuple[int, ...],
) -> MindEvent | None:
    try:
        return trace.append(
            event_type,
            payload,
            source_event_seqs=source_event_seqs,
        )
    except TraceError:
        return None


def _record_terminal(
    trace: MindTrace,
    result: NoChange | Directive | DecisionIntent | ActivationFailure,
    source_event_seqs: tuple[int, ...],
) -> NoChange | Directive | DecisionIntent | ActivationFailure:
    if isinstance(result, ActivationFailure):
        return _record_failure(trace, result, source_event_seqs)
    if isinstance(result, NoChange):
        payload: dict[str, object] = {"type": "no_change"}
    elif isinstance(result, Directive):
        issuing_seq = len(trace.events)
        payload = {
            "directive_id": directive_id_for(
                trace.events[0].activation_id,
                issuing_seq,
            ),
            "text": result.text,
        }
        if _append_event(
            trace,
            MIND_DIRECTIVE_ISSUED,
            payload,
            source_event_seqs,
        ) is None:
            return ActivationFailure("trace_failed")
        return result
    else:
        payload = {"type": "decision_intent", "intent": result.intent}
    if _append_event(
        trace,
        ACTIVATION_FINISHED,
        payload,
        source_event_seqs,
    ) is None:
        return ActivationFailure("trace_failed")
    return result


def _record_failure(
    trace: MindTrace,
    failure: ActivationFailure,
    source_event_seqs: tuple[int, ...],
) -> ActivationFailure:
    if failure.code == "trace_failed":
        return failure
    if _append_event(
        trace,
        ACTIVATION_FAILED,
        {"code": failure.code},
        source_event_seqs,
    ) is None:
        return ActivationFailure("trace_failed")
    return failure


def _parse_output(
    raw: str,
    *, cognitive: bool = False, unified: bool = False, compute: bool = False,
) -> NoChange | Directive | DecisionIntent | _CapabilityRequest | ActivationFailure:
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (RecursionError, TypeError, ValueError):
        return ActivationFailure("invalid_model_output")
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        return ActivationFailure("invalid_model_output")

    output_type = payload["type"]
    if unified:
        if (output_type != "cognitive_step" or set(payload) != {"type", "updates", "next"}
                or type(payload["updates"]) is not list or len(payload["updates"]) > 4
                or any(type(update) is not dict for update in payload["updates"])
                or type(payload["next"]) is not dict):
            return ActivationFailure("invalid_model_output")
        return _parse_output(_json_message(payload["next"]), compute=compute)
    if cognitive and output_type != "capability_request":
        if (
            output_type != "cognitive_commit"
            or set(payload) != {"type", "updates", "output"}
            or type(payload["updates"]) is not list or len(payload["updates"]) > 4
            or type(payload["output"]) is not dict
            or payload["output"].get("type") not in {"no_change", "directive", "decision_intent"}
        ):
            return ActivationFailure("invalid_model_output")
        return _parse_output(_json_message(payload["output"]))
    if output_type == "no_change":
        return NoChange() if set(payload) == {"type"} else ActivationFailure(
            "invalid_model_output"
        )
    if output_type == "directive":
        if set(payload) != {"type", "text"}:
            return ActivationFailure("invalid_model_output")
        text = payload["text"]
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > MAX_DIRECTIVE_CHARS
        ):
            return ActivationFailure("invalid_model_output")
        return Directive(text.strip())
    if output_type == "decision_intent":
        if set(payload) != {"type", "intent"}:
            return ActivationFailure("invalid_model_output")
        intent = payload["intent"]
        if (
            not isinstance(intent, str)
            or not intent.strip()
            or len(intent) > MAX_DECISION_INTENT_CHARS
        ):
            return ActivationFailure("invalid_model_output")
        return DecisionIntent(intent.strip())
    if output_type == "capability_request":
        capability = payload.get("capability")
        if capability == "evaluate_model" and compute:
            if (set(payload) != {"type", "capability", "source", "probe_ref"}
                    or not _valid_normalized_text(payload["probe_ref"], 128)
                    or type(payload["source"]) is not str or not payload["source"].strip()
                    or len(payload["source"]) > MAX_MODEL_OUTPUT_CHARS):
                return ActivationFailure("invalid_model_output")
            return _CapabilityRequest(capability, model_request={"source": payload["source"], "probe_ref": payload["probe_ref"]})
        if capability == "run_world_model" and compute:
            if set(payload) != {"type", "capability", "source", "initial_observation", "actions"}:
                return ActivationFailure("invalid_model_output")
            try:
                request = _model_request(payload["source"], payload["initial_observation"], payload["actions"])
            except (TypeError, ValueError):
                return ActivationFailure("invalid_model_output")
            return _CapabilityRequest(capability, model_request=request)
        if capability == "inspect_execution":
            if set(payload) != {"type", "capability"}:
                return ActivationFailure("invalid_model_output")
            return _CapabilityRequest("inspect_execution")
        if capability != "recall_memory" or set(payload) != {
            "type",
            "capability",
            "query",
        }:
            return ActivationFailure("invalid_model_output")
        query = payload["query"]
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_MEMORY_QUERY_CHARS
        ):
            return ActivationFailure("invalid_model_output")
        return _CapabilityRequest("recall_memory", query.strip())
    return ActivationFailure("invalid_model_output")


def _memory_observation(
    context: object,
) -> dict[str, object] | ActivationFailure:
    if (
        not isinstance(context, MemoryContext)
        or not isinstance(context.rendered_text, str)
        or not isinstance(context.truncated, bool)
    ):
        return ActivationFailure("invalid_memory_observation")
    if context.safe_error_code is not None:
        if (
            not isinstance(context.safe_error_code, str)
            or not context.safe_error_code.strip()
            or len(context.safe_error_code) > MAX_SAFE_ERROR_CODE_CHARS
        ):
            return ActivationFailure("invalid_memory_observation")
        observation: dict[str, object] = {
            "capability": "recall_memory",
            "rendered_evidence": "",
            "safe_error_code": "memory_unavailable",
            "status": "unavailable",
            "truncated": False,
        }
    else:
        if len(context.rendered_text) > EXPERIMENT_A_RECALL_POLICY.max_chars:
            return ActivationFailure("observation_too_large")
        observation = {
            "capability": "recall_memory",
            "rendered_evidence": context.rendered_text,
            "safe_error_code": None,
            "status": "available" if context.rendered_text else "empty",
            "truncated": context.truncated,
        }
    if len(_json_message(observation)) > MAX_OBSERVATION_CHARS:
        return ActivationFailure("observation_too_large")
    return observation


def _execution_observation(
    view: object,
) -> dict[str, object] | ActivationFailure:
    if type(view) is not ExecutionObservation:
        return ActivationFailure("invalid_execution_observation")

    goal = view.goal
    status = view.status
    recent_outcome = view.recent_outcome
    failure = view.failure
    fields = (
        (goal, MAX_GOAL_CHARS, False),
        (status, MAX_STATUS_CHARS, False),
        (recent_outcome, MAX_OUTCOME_CHARS, True),
        (failure, MAX_FAILURE_CHARS, True),
    )
    for value, limit, optional in fields:
        if value is None and optional:
            continue
        if (
            type(value) is not str
            or not value.strip()
            or len(value) > limit
        ):
            return ActivationFailure("invalid_execution_observation")
    observation: dict[str, object] = {
        "capability": "inspect_execution",
        "failure": failure,
        "goal": goal,
        "recent_outcome": recent_outcome,
        "status": status,
    }
    if len(_json_message(observation)) > MAX_OBSERVATION_CHARS:
        return ActivationFailure("observation_too_large")
    return observation


def _supervisor_evidence_payload(
    bundle: object,
    execution_observation: object,
    prepared_execution_observation: dict[str, object],
) -> dict[str, object] | ActivationFailure:
    if (
        type(bundle) is not SupervisorEvidenceBundle
        or type(bundle.execution_observation) is not ExecutionObservation
        or bundle.execution_observation != execution_observation
        or type(bundle.trigger) is not SupervisorTrigger
        or type(bundle.recent_evidence) is not tuple
        or not 1 <= len(bundle.recent_evidence) <= MAX_SUPERVISOR_EVIDENCE_ITEMS
    ):
        return ActivationFailure("invalid_supervisor_evidence")

    trigger = bundle.trigger
    if (
        type(trigger.trigger_type) is not str
        or trigger.trigger_type not in SUPERVISOR_TRIGGER_TYPES
        or not _valid_normalized_text(
            trigger.summary,
            MAX_SUPERVISOR_TRIGGER_CHARS,
        )
        or type(trigger.source_refs) is not tuple
        or not 1 <= len(trigger.source_refs) <= MAX_SUPERVISOR_SOURCE_REFS
        or len(set(trigger.source_refs)) != len(trigger.source_refs)
        or any(
            not _valid_normalized_text(ref, MAX_SUPERVISOR_SOURCE_REF_CHARS)
            for ref in trigger.source_refs
        )
    ):
        return ActivationFailure("invalid_supervisor_evidence")

    evidence_payload: list[dict[str, str]] = []
    for item in bundle.recent_evidence:
        if (
            type(item) is not SupervisorEvidence
            or type(item.kind) is not str
            or item.kind not in SUPERVISOR_EVIDENCE_KINDS
            or not _valid_normalized_text(
                item.text,
                MAX_SUPERVISOR_EVIDENCE_CHARS,
            )
        ):
            return ActivationFailure("invalid_supervisor_evidence")
        evidence_payload.append({"kind": item.kind, "text": item.text})

    payload: dict[str, object] = {
        "execution_observation": {
            "failure": prepared_execution_observation["failure"],
            "goal": prepared_execution_observation["goal"],
            "recent_outcome": prepared_execution_observation["recent_outcome"],
            "status": prepared_execution_observation["status"],
        },
        "recent_evidence": evidence_payload,
        "trigger": {
            "source_refs": list(trigger.source_refs),
            "summary": trigger.summary,
            "type": trigger.trigger_type,
        },
    }
    if len(_json_message(payload)) > MAX_SUPERVISOR_BUNDLE_CHARS:
        return ActivationFailure("invalid_supervisor_evidence")
    return payload


def _valid_normalized_text(value: object, limit: int) -> bool:
    return (
        type(value) is str
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= limit
    )


def _request_payload(request: _CapabilityRequest) -> dict[str, object]:
    payload = {"capability": request.capability}
    if request.query is not None:
        payload["query"] = request.query
    if request.model_request is not None:
        payload.update(request.model_request)
    return payload


def _activation_payload(activation: ActivationInput) -> dict[str, str]:
    return {
        "execution_goal_snapshot": activation.execution_goal_snapshot,
        "execution_status": activation.execution_status,
        "trigger": activation.trigger,
    }


def _json_message(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON key")
    return dict(pairs)


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-standard JSON constant")


def _valid_activation(activation: object) -> bool:
    if not isinstance(activation, ActivationInput):
        return False
    fields = (
        (activation.trigger, MAX_TRIGGER_CHARS),
        (activation.execution_goal_snapshot, MAX_GOAL_CHARS),
        (activation.execution_status, MAX_STATUS_CHARS),
    )
    return all(
        isinstance(value, str) and bool(value.strip()) and len(value) <= limit
        for value, limit in fields
    )
