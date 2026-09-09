"""A bounded read/analyze/submit activity, without action or owner handles."""
from __future__ import annotations

from dataclasses import asdict

from Mind.contracts import (ActivationInput, ActivationFailure, CapabilityRequest,
    NoChange, Directive, DecisionIntent, parse_output, request_payload)
from Mind.trace import (MindTrace, TraceError, ACTIVATION_STARTED, INITIAL_EXECUTION_OBSERVED,
    MODEL_OUTPUT_RECORDED, CAPABILITY_REQUESTED, CAPABILITY_OBSERVED, ACTIVATION_FAILED,
    ACTIVATION_FINISHED, MIND_DIRECTIVE_ISSUED, NATIVE_PROTOCOL_VERSION,
    project_model_request, cognitive_phase, model_dependencies, consultation_allowed,
    directive_id_for)
from Mind.task_view import output_limit


def _append_event(trace, kind, payload, sources):
    try:
        return trace.append(kind, payload, source_event_seqs=sources)
    except TraceError:
        return None


def record_failure(trace, failure, sources):
    if failure.code != 'trace_failed' and _append_event(
            trace, ACTIVATION_FAILED, {'code': failure.code}, sources) is None:
        return ActivationFailure('trace_failed')
    return failure


def record_terminal(trace, result, sources):
    if isinstance(result, ActivationFailure):
        return record_failure(trace, result, sources)
    if isinstance(result, Directive):
        payload = {'directive_id': directive_id_for(trace.events[0].activation_id, len(trace.events),
                                                  events=trace.events), 'text': result.text}
        kind = MIND_DIRECTIVE_ISSUED
    else:
        payload = {'type': 'no_change'} if isinstance(result, NoChange) else {
            'type': 'decision_intent', 'intent': result.intent}
        kind = ACTIVATION_FINISHED
    if _append_event(trace, kind, payload, sources) is None:
        return ActivationFailure('trace_failed')
    return result


def resume_native_activity(trace, model):
    """Also recovers a known response whose logical output was not yet appended."""
    dependencies = model_dependencies(trace.events)
    try:
        projection = project_model_request(trace.events)
        raw = model.generate_from_trace(trace, projection)
    except TraceError:
        return ActivationFailure('trace_failed')
    except Exception:
        return record_failure(trace, ActivationFailure('model_failed'), dependencies)
    if not isinstance(raw, str) or len(raw) > output_limit():
        return record_failure(trace, ActivationFailure('invalid_model_output'), dependencies)
    event = _append_event(trace, MODEL_OUTPUT_RECORDED,
        {'call_index': cognitive_phase(trace.events), 'text': raw}, dependencies)
    if event is None:
        return ActivationFailure('trace_failed')
    result = parse_output(raw)
    if isinstance(result, CapabilityRequest):
        if not consultation_allowed(trace.events) or result.capability not in trace.events[0].payload['available_capabilities']:
            return record_failure(trace, ActivationFailure('capability_limit_exceeded'), (event.seq,))
        if _append_event(trace, CAPABILITY_REQUESTED, request_payload(result), (event.seq,)) is None:
            return ActivationFailure('trace_failed')
        return result
    return record_terminal(trace, result, (event.seq,))


def start_activity(activation: ActivationInput, *, model, trace: MindTrace,
                   cognitive_context, execution_observation=None, capabilities=()):
    started = _append_event(trace, ACTIVATION_STARTED, {
        'activation': asdict(activation), 'cognitive_context': cognitive_context,
        'available_capabilities': list(capabilities), 'native_protocol': NATIVE_PROTOCOL_VERSION}, ())
    if started is None:
        return ActivationFailure('trace_failed')
    if execution_observation is not None:
        if _append_event(trace, INITIAL_EXECUTION_OBSERVED,
                         asdict(execution_observation), (started.seq,)) is None:
            return ActivationFailure('trace_failed')
    return resume_native_activity(trace, model)


def continue_activity(*, trace, model, observation):
    request = trace.events[-1]
    if request.event_type != CAPABILITY_REQUESTED:
        return ActivationFailure('invalid_trace')
    if isinstance(observation, ActivationFailure):
        return record_failure(trace, observation, (request.seq,))
    initial = next((event for event in trace.events if event.event_type == INITIAL_EXECUTION_OBSERVED), None)
    sources = (initial.seq, request.seq) if initial and request.payload['capability'] == 'inspect_execution' else (request.seq,)
    if _append_event(trace, CAPABILITY_OBSERVED, {
            'capability': request.payload['capability'], 'observation': observation}, sources) is None:
        return ActivationFailure('trace_failed')
    return resume_native_activity(trace, model)
