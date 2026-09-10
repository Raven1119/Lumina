"""Inert cognitive decisions and the current bounded submission parser."""
from __future__ import annotations

import json
from dataclasses import dataclass

from Mind.task_view import directive_limit


@dataclass(frozen=True)
class ActivationInput:
    trigger: str
    execution_goal_snapshot: str
    execution_status: str | None


@dataclass(frozen=True)
class ExecutionObservation:
    goal: str
    status: str
    recent_outcome: str | None = None
    failure: str | None = None


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
class ActivationFailure:
    code: str


@dataclass(frozen=True)
class CapabilityRequest:
    capability: str
    model_request: dict | None = None


def request_payload(request):
    return {"capability": request.capability, **(request.model_request or {})}


def parse_output(raw, *, submission=True):
    from Mind.trace import _strict_json_object, _reject_json_constant, _validate_capability_request
    try:
        value = json.loads(raw, object_pairs_hook=_strict_json_object, parse_constant=_reject_json_constant)
        if not isinstance(value, dict):
            raise ValueError()
        if submission:
            if (set(value) - {'current', 'effects'} != {'type', 'updates', 'next'}
                    or value['type'] != 'cognitive_step'
                    or type(value['updates']) is not list or len(value['updates']) > 16
                    or any(type(item) is not dict for item in value['updates'])
                    or type(value['next']) is not dict):
                raise ValueError()
            if 'effects' in value and type(value['effects']) is not dict:
                raise ValueError()
            if 'current' in value and (type(value['current']) is not list
                    or any(type(ref) is not str for ref in value['current'])
                    or len(set(value['current'])) != len(value['current'])):
                raise ValueError()
            return parse_output(json.dumps(value['next']), submission=False)
        kind = value.get('type')
        if kind == 'no_change' and set(value) == {'type'}:
            return NoChange()
        if kind in {'directive', 'decision_intent'}:
            field = 'text' if kind == 'directive' else 'intent'
            limit = directive_limit() if kind == 'directive' else 1000
            text = value.get(field)
            if (set(value) != {'type', field} or not isinstance(text, str)
                    or not text.strip() or len(text) > limit):
                raise ValueError()
            return Directive(text) if kind == 'directive' else DecisionIntent(text)
        if kind == 'capability_request':
            request = {key: item for key, item in value.items() if key != 'type'}
            _validate_capability_request(request)
            return CapabilityRequest(request['capability'], {key: item for key, item in request.items()
                                                          if key != 'capability'})
    except (RecursionError, KeyError, TypeError, ValueError):
        pass
    return ActivationFailure('invalid_model_output')
