"""Explicit Mind / Nervous dispatch. No Chat, Memory or Execution integration."""
from __future__ import annotations

import hashlib

from Nervous.organ import Event, NervousOrgan
from Mind.experiment_a import ActivationFailure, ExecutionObservation, _parse_output, _resolve_capability
from Mind.organ import Evidence, MindInput, MindOrgan, MindResultEvent, ModelFeedback, ModelProbe, _input_document
from Mind.trace import _canonical_json, _thaw


def activation_event(value: MindInput, *, source: str = "host") -> Event:
    return Event(value.event_id, source, "mind", "mind.activate", _input_document(value))


def _input(document) -> MindInput:
    value = _thaw(document)
    value["evidence"] = tuple(Evidence(**item) for item in value["evidence"])
    if value.get("execution_observation") is not None:
        value["execution_observation"] = ExecutionObservation(**value["execution_observation"])
    if value.get("model_probe") is not None:
        probe = value["model_probe"]
        probe["actions"] = tuple(probe["actions"])
        value["model_probe"] = ModelProbe(**probe)
    if value.get("model_feedback") is not None:
        feedback = value["model_feedback"]
        for name in ("observed_steps", "observed_outcomes", "source_refs"):
            feedback[name] = tuple(feedback[name])
        value["model_feedback"] = ModelFeedback(**feedback)
    return MindInput(**value)


def result_event(request: Event, observation=None, *, error: str | None = None) -> Event:
    """Construct a data reply; the producer must atomically complete its request."""
    if (type(request) is not Event or request.target != "mind.requests"
            or request.kind != "mind.request" or request.event_id != request.data["request_ref"]):
        raise ValueError("invalid_mind_request_event")
    return Event(request.event_id + ":result", request.target, "mind.results", "mind.result",
                 {"request_ref": request.event_id, "observation": _thaw(observation), "error": error}, request.event_id)


def run_mind_once(nervous: NervousOrgan, mind: MindOrgan):
    """Handle at most one event; a busy activation remains pending in Nervous.

    Results have a separate mailbox so queued new activations cannot obstruct
    the event needed to finish the current activation. No background loop runs.
    """
    pending = nervous.pending("mind.results", 1) or nervous.pending("mind", 1)
    if not pending:
        return None
    event, = pending
    if event.target == "mind.results":
        if (event.kind != "mind.result" or event.source != "mind.requests"
                or set(event.data) != {"request_ref", "observation", "error"}
                or event.causation_id != event.data["request_ref"]):
            raise ValueError("invalid_mind_result_event")
        receipt = mind.accept_result(MindResultEvent(**_thaw(event.data)))
    else:
        if event.kind != "mind.activate":
            raise ValueError("invalid_mind_activation_event")
        value = _input(event.data)
        if value.event_id != event.event_id:
            raise ValueError("activation_event_identity_conflict")
        receipt = mind.activate(value)
    if receipt.status == "busy":
        return receipt
    if receipt.request is not None:
        request = receipt.request
        emitted = Event(request.request_ref, event.target, "mind.requests", "mind.request",
            {"request_ref": request.request_ref, "event_id": request.event_id,
             "payload": _thaw(request.payload), "model_probe": _thaw(request.model_probe)}, event.event_id)
    else:
        # A retry after Mind committed but before Nervous committed returns
        # duplicate. The durable receipt event represents the same acceptance.
        status = "accepted" if receipt.status == "duplicate" else receipt.status
        identity = "mind-receipt-" + hashlib.sha256(receipt.event_id.encode("utf-8")).hexdigest()[:24]
        emitted = Event(identity, event.target, "host", "mind.receipt",
            {"event_id": receipt.event_id, "status": status, "revision": receipt.revision,
             "output": _thaw(receipt.output), "error": receipt.error}, event.event_id)
    nervous.complete(event.event_id, event.target, emitted=(emitted,))
    return receipt


def run_computation_once(nervous: NervousOrgan) -> bool:
    """Run only Mind's opt-in isolated pure computation; other requests wait.

    No Memory/Chat/Execution handler is registered or called. Pure computation
    may be repeated after a pre-commit host crash; it has no reality authority.
    """
    pending = nervous.pending("mind.requests", 1)
    if not pending:
        return False
    request, = pending
    if request.kind != "mind.request" or request.source != "mind":
        raise ValueError("invalid_computation_request_event")
    payload = _thaw(request.data["payload"])
    if payload["capability"] not in {"run_world_model", "evaluate_model"}:
        return False
    parsed = _parse_output(_canonical_json({"type": "capability_request", **payload}), compute=True)
    observation = (parsed if isinstance(parsed, ActivationFailure) else _resolve_capability(
        parsed, memory_retriever=None, prepared_execution_observation=None,
        probe=_thaw(request.data["model_probe"]), allow_model_computation=True))
    reply = (result_event(request, error=observation.code) if isinstance(observation, ActivationFailure)
             else result_event(request, observation))
    nervous.complete(request.event_id, request.target, emitted=(reply,))
    return True
