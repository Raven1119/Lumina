"""Bounded, persistent cognition for one intention; explicitly invoked by a host.

Reuses the A–D activation/trace/directive implementation. This is not Chat
routing, a scheduler, a second factual memory store, or an Execution Actor.
"""
from __future__ import annotations

import hashlib
import copy
import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from jsonschema import Draft202012Validator

from Mind.directive import DirectiveApplication, prepare_for_execution_decision
from Mind.experiment_a import (
    ActivationFailure, ActivationInput, ExecutionObservation,
    _CapabilityRequest, _continue_activation, _parse_output, _record_terminal, _request_payload, _run_activation,
)
from Mind.task_view import (EXPRESSION_CONTRACT_VERSIONS, CONTINUITY_CONTRACT_VERSION,
                           context_limit, mind_task_view, project_observation)
from Mind.trace import (
    COGNITIVE_PROBE_PROJECTOR_VERSION,
    COGNITIVE_COMPACT_PROJECTOR_VERSION,
    COGNITIVE_MODEL_PROJECTOR_VERSION,
    COGNITIVE_STEP_PROJECTOR_VERSION,
    CAPABILITY_OBSERVED, CAPABILITY_REQUESTED, MODEL_OUTPUT_RECORDED, MindTrace, TraceError,
    _canonical_json, _freeze, _reject_json_constant, _strict_json_object,
    _thaw, replay_activation, MAX_MODEL_OUTPUT_CHARS, MAX_DIRECTIVE_CHARS, MAX_DECISION_INTENT_CHARS,
)
from Mind.world_model import _fields as _model_fields, _request as _model_request, verify_run

MAX_ACTIVATIONS = 64
MAX_ACTIVE_ITEMS = 8
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
BUDGET_VERSION = "cognition-minimal-v1:2-model-calls:1-read:2000-output-chars"
MODEL_BUDGET_VERSION = "cognition-minimal-v2:2-model-calls:1-capability:2000-output-chars"
EVENT_BUDGET_VERSION = "cognition-events-v1:2-model-calls:1-result:2000-output-chars"
EXPRESSION_EVENT_BUDGET_VERSION = "cognition-events-d7:2-steps:3-calls:1-result:6000-output-chars"
CONTINUITY_EVENT_BUDGET_VERSION = "cognition-events-d7-v3:2-steps:3-calls:1-result:6000-output-chars:16000-context-chars"
EVENT_BUDGET_VERSIONS = (EVENT_BUDGET_VERSION, EXPRESSION_EVENT_BUDGET_VERSION,
                       CONTINUITY_EVENT_BUDGET_VERSION)
CHAIN_CONTRACT_VERSION = "cognitive-submit-d6-v1"
REVISED_CHAIN_CONTRACT_VERSION = "cognitive-submit-d6-v2"
CHAIN_CONTRACT_VERSIONS = (CHAIN_CONTRACT_VERSION, REVISED_CHAIN_CONTRACT_VERSION, *EXPRESSION_CONTRACT_VERSIONS)
MAX_UPDATES = 4
MAX_EVIDENCE_CHARS = 1000


def _schema_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _schema_text(limit):
    return {"type": "string", "minLength": 1, "maxLength": limit, "pattern": r"\S"}


_BASIS_SCHEMA = {"type": "array", "maxItems": 3, "items": _schema_object({
    "ref": _schema_text(128), "quote": _schema_text(300)})}
_UPDATE_SCHEMAS = {
    "belief": _schema_object({"kind": {"enum": ["belief"]}, "id": _schema_text(64),
        "claim": _schema_text(400), "status": {"enum": ["open", "supported", "contradicted", "archived"]},
        "basis": _BASIS_SCHEMA, "discriminator": _schema_text(300)}),
    "question": _schema_object({"kind": {"enum": ["question"]}, "id": _schema_text(64),
        "text": _schema_text(300), "status": {"enum": ["open", "closed", "archived"]}, "basis": _BASIS_SCHEMA}),
    "scenario": _schema_object({"kind": {"enum": ["scenario"]}, "id": _schema_text(64),
        "status": {"enum": ["active", "archived"]},
        "assumptions": {"type": "array", "minItems": 1, "maxItems": 3, "items": _schema_text(64)},
        "steps": {"type": "array", "minItems": 1, "maxItems": 3, "items": _schema_object({
            name: _schema_text(200) for name in ("state", "actors", "action", "external", "outcome")})},
        "unknowns": {"type": "array", "maxItems": 3, "items": _schema_text(200)}}),
}
_UPDATE_SCHEMAS["belief"]["allOf"] = [{
    "if": {"properties": {"status": {"enum": ["supported", "contradicted"]}}},
    "then": {"properties": {"basis": {"minItems": 1}}}}]
_UPDATE_VALIDATORS = {kind: Draft202012Validator(schema) for kind, schema in _UPDATE_SCHEMAS.items()}


def cognitive_step_schema(sources, items=(), capabilities=(), *, contract=CHAIN_CONTRACT_VERSION):
    """Active native shape shares the reducer definitions; grounding remains owner-checked."""
    variants = copy.deepcopy(list(_UPDATE_SCHEMAS.values()))
    for variant in variants:
        props = variant["properties"]
        if contract in {REVISED_CHAIN_CONTRACT_VERSION, *EXPRESSION_CONTRACT_VERSIONS} and "claim" in props:
            props["claim"]["description"] = "The literal assertion, including relevant policy and time. A statement that an artifact is defective can itself be true."
            props["status"]["description"] = "Evaluate the literal claim: supported = evidence warrants this sentence; contradicted = evidence warrants its negation; open = undecided. This is NOT an artifact pass/fail label or a record that an old belief changed."
            props["discriminator"]["description"] = "A conditional observation that would distinguish this claim from its negation, under its stated scope. It is unobserved, not an additional rule. Re-derive it from sources rather than copying a prior test."
        if contract in EXPRESSION_CONTRACT_VERSIONS and "discriminator" in props:
            variant["required"].remove("discriminator")
            props["discriminator"]["description"] = "Optional: a useful unobserved test of an unresolved claim, not a condition to invent for a sourced rule. Omission on a revised item explicitly retires its prior test; prior versions remain in history."
        props["id"]["anyOf"] = [{"pattern": r"^new:[A-Za-z0-9_-]{1,32}$"}]
        if items:
            props["id"]["anyOf"].append({"enum": [item["id"] for item in items]})
        if "basis" in props:
            if sources:
                props["basis"]["items"]["properties"]["ref"]["enum"] = list(sources)
            else:
                props["basis"]["maxItems"] = 0
    next_options = [_schema_object({"type": {"enum": ["no_change"]}}),
        _schema_object({"type": {"enum": ["directive"]}, "text": _schema_text(MAX_DIRECTIVE_CHARS)}),
        _schema_object({"type": {"enum": ["decision_intent"]}, "intent": _schema_text(MAX_DECISION_INTENT_CHARS)})]
    if "inspect_execution" in capabilities:
        next_options.append(_schema_object({"type": {"enum": ["capability_request"]},
            "capability": {"enum": ["inspect_execution"]}}))
    return _schema_object({"type": {"enum": ["cognitive_step"]},
        "updates": {"type": "array", "maxItems": MAX_UPDATES, "items": {"oneOf": variants}},
        "next": {"oneOf": next_options}})


def observation_source_text(observation, contract=None):
    """One owner-authorized logical text for both citation catalogue and grounding."""
    if observation["capability"] == "recall_memory":
        return observation["rendered_evidence"]
    if contract in CHAIN_CONTRACT_VERSIONS:
        return "\n".join(name + ":\n" + ("[absent]" if value is None else value)
            for name, value in observation.items())
    return _canonical_json(observation)  # Historical source identity is unchanged.


@dataclass(frozen=True)
class Evidence:
    ref: str
    text: str
    origin: str  # Host-authorized Memory or Execution projection.


@dataclass(frozen=True)
class ModelProbe:
    """One host-selected computation input; future observed results stay outside it."""
    ref: str
    initial_observation: dict
    actions: tuple[dict, ...]


@dataclass(frozen=True)
class ModelFeedback:
    """Host-observed data to check an earlier artifact, with reality source refs.

    Construct only from authorized owner observations. Mind computes the report;
    neither the runtime model nor this DTO supplies a self-certified verdict.
    """
    artifact_ref: str
    observed_steps: tuple[dict | None, ...]
    observed_outcomes: tuple[str | None, ...]
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class MindInput:
    event_id: str
    trigger: str
    intention_ref: str
    intention_revision: int
    goal: str
    execution_ref: str
    execution_status: str
    evidence: tuple[Evidence, ...] = ()
    execution_observation: ExecutionObservation | None = None
    model_probe: ModelProbe | None = None
    model_feedback: ModelFeedback | None = None
    owner_task: dict | None = None


@dataclass(frozen=True)
class MindView:
    revision: int
    intention_ref: str | None
    intention_revision: int | None
    items: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class MindRequest:
    request_ref: str
    event_id: str
    payload: Mapping[str, object]
    model_probe: Mapping[str, object] | None = None


@dataclass(frozen=True)
class MindResultEvent:
    request_ref: str
    observation: Mapping[str, object] | None = None
    error: str | None = None


@dataclass(frozen=True)
class MindReceipt:
    event_id: str
    status: str
    revision: int
    output: Mapping[str, object] | None = None
    error: str | None = None
    request: MindRequest | None = None


def _json(text: str):
    return json.loads(text, object_pairs_hook=_strict_json_object,
                      parse_constant=_reject_json_constant)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: object, limit: int) -> None:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError("invalid_bounded_text")


def _input_document(value: MindInput) -> dict:
    if type(value) is not MindInput or type(value.evidence) is not tuple:
        raise ValueError("invalid_input")
    for field, limit in (
        (value.event_id, 128), (value.trigger, 1000), (value.intention_ref, 128),
        (value.goal, 2000), (value.execution_ref, 128), (value.execution_status, 200),
    ):
        _text(field, limit)
    if type(value.intention_revision) is not int or value.intention_revision < 0:
        raise ValueError("invalid_intention_revision")
    if len(value.evidence) > 3:
        raise ValueError("evidence_budget_exceeded")
    seen = set()
    for evidence in value.evidence:
        if type(evidence) is not Evidence or evidence.origin not in {"memory", "execution"}:
            raise ValueError("invalid_evidence")
        _text(evidence.ref, 128)
        _text(evidence.text, MAX_EVIDENCE_CHARS)
        if evidence.ref in seen or evidence.ref == "activation:observation":
            raise ValueError("evidence_identity_conflict")
        seen.add(evidence.ref)
    if value.execution_observation is not None:
        if type(value.execution_observation) is not ExecutionObservation:
            raise ValueError("invalid_execution_observation")
        if (
            value.execution_observation.goal != value.goal
            or value.execution_observation.status != value.execution_status
        ):
            raise ValueError("execution_snapshot_conflict")
    if value.model_probe is not None:
        probe = value.model_probe
        if type(probe) is not ModelProbe or type(probe.actions) is not tuple:
            raise ValueError("invalid_model_probe")
        _text(probe.ref, 128)
        _model_request("pass", probe.initial_observation, list(probe.actions))
    if value.model_feedback is not None:
        feedback = value.model_feedback
        if (type(feedback) is not ModelFeedback or type(feedback.observed_steps) is not tuple
                or len(feedback.observed_steps) > 16 or type(feedback.observed_outcomes) is not tuple
                or len(feedback.observed_outcomes) != len(feedback.observed_steps) + 1
                or type(feedback.source_refs) is not tuple or not 1 <= len(feedback.source_refs) <= 3
                or any(type(ref) is not str or ref not in seen for ref in feedback.source_refs)):
            raise ValueError("invalid_model_feedback")
        _text(feedback.artifact_ref, 128)
        for observed in feedback.observed_steps:
            if observed is not None:
                _model_fields(observed)
        for outcome in feedback.observed_outcomes:
            if outcome is not None and (type(outcome) is not str or outcome not in {"ongoing", "complete", "failed", "unknown"}):
                raise ValueError("invalid_model_feedback")
    document = asdict(value)
    if value.owner_task is None:
        document.pop("owner_task")
    else:
        mind_task_view(value.owner_task, value.goal)
    # Preserve the canonical identity of historical events without these fields.
    return _json(_canonical_json({key: item for key, item in document.items()
            if key not in {"model_probe", "model_feedback"} or item is not None}))


def _basis(basis: object, sources: dict[str, dict]) -> None:
    if type(basis) is not list or len(basis) > 3:
        raise ValueError("invalid_basis")
    for quote in basis:
        if type(quote) is not dict or set(quote) != {"ref", "quote"}:
            raise ValueError("invalid_basis")
        _text(quote["ref"], 128)
        _text(quote["quote"], 300)
        if quote["ref"] not in sources or quote["quote"] not in sources[quote["ref"]]["text"]:
            raise ValueError("ungrounded_basis")


def _apply_updates(items: dict, updates: object, sources: dict, event_id: str,
                   artifact: dict | None = None, *, contract=None) -> dict:
    if type(updates) is not list or len(updates) > MAX_UPDATES:
        raise ValueError("update_budget_exceeded")
    candidate = _json(_canonical_json(items))
    labels: dict[str, str] = {}
    for update in updates:
        if type(update) is not dict:
            raise ValueError("invalid_update")
        identity = update.get("id")
        _text(identity, 64)
        if identity in labels:
            raise ValueError("duplicate_item_update")
        if identity.startswith("new:"):
            if re.fullmatch(r"new:[A-Za-z0-9_-]{1,32}", identity) is None:
                raise ValueError("invalid_local_label")
            labels[identity] = "item-" + _digest([event_id, identity])[:20]
        elif identity in items:
            labels[identity] = identity
        else:
            raise ValueError("unknown_item")
    for update in updates:
        value = _json(_canonical_json(update))
        identity = labels[value["id"]]
        value["id"] = identity
        kind = value.get("kind")
        if identity in items and kind != items[identity]["kind"]:
            raise ValueError("item_kind_conflict")
        common = {"kind", "id", "status"}
        validator = _UPDATE_VALIDATORS.get(kind)
        if kind == "belief" and contract in EXPRESSION_CONTRACT_VERSIONS:
            schema = copy.deepcopy(_UPDATE_SCHEMAS[kind])
            schema["required"].remove("discriminator")
            validator = Draft202012Validator(schema)
        if validator is not None and not validator.is_valid(value):
            if kind == "belief" and value.get("status") in {"supported", "contradicted"} and value.get("basis") == []:
                raise ValueError("assessment_needs_evidence")
            raise ValueError("invalid_" + kind)
        if kind == "belief":
            _basis(value["basis"], sources)
        elif kind == "question":
            _basis(value["basis"], sources)
        elif kind == "scenario":
            assumptions = value["assumptions"]
            value["assumptions"] = [labels.get(ref, ref) for ref in assumptions]
            value["analysis_status"] = "QUALITATIVE"
        elif kind == "model":
            if set(value) != common | {"scope", "basis", "artifact_ref", "unknowns"}:
                raise ValueError("invalid_model_item")
            if value["status"] not in {"active", "archived"}:
                raise ValueError("invalid_model_status")
            _text(value["scope"], 300)
            _text(value["artifact_ref"], 128)
            _basis(value["basis"], sources)
            if not value["basis"]:
                raise ValueError("assessment_needs_evidence")
            if type(value["unknowns"]) is not list or len(value["unknowns"]) > 3:
                raise ValueError("invalid_model_unknowns")
            for unknown in value["unknowns"]:
                _text(unknown, 200)
            if value["artifact_ref"] == "activation:model" and artifact is not None:
                attached = artifact
            else:
                attached = next((item["artifact"] for item in items.values()
                                 if item["kind"] == "model" and item["artifact_ref"] == value["artifact_ref"]), None)
            if attached is None:
                raise ValueError("unknown_model_artifact")
            value["artifact_ref"] = attached["ref"]
            value["artifact"] = attached
            value["analysis_status"] = "COMPUTED"
        else:
            raise ValueError("unsupported_update_kind")
        candidate[identity] = value
    active = {key: value for key, value in candidate.items() if value["status"] != "archived"}
    if len(active) > MAX_ACTIVE_ITEMS:
        raise ValueError("active_item_budget_exceeded")
    for item in active.values():
        if item["kind"] == "scenario":
            for ref in item["assumptions"]:
                if ref not in active or active[ref]["kind"] != "belief":
                    raise ValueError("unknown_scenario_assumption")
    return active  # Archived versions remain in the immutable accepted history.


class MindOrgan:
    """One intention, one native writer lock, one bounded acceptance history."""

    def __init__(self, *, directory: str | Path, model, memory_retriever=None,
                 allow_model_computation: bool = False, available_capabilities: tuple[str, ...] = ()):
        if type(allow_model_computation) is not bool:
            raise ValueError("invalid_computation_flag")
        if memory_retriever is not None:
            raise ValueError("memory_requires_event_dispatch")
        if (type(available_capabilities) is not tuple
                or any(type(name) is not str or name not in {"recall_memory", "inspect_execution"}
                       for name in available_capabilities)
                or len(set(available_capabilities)) != len(available_capabilities)):
            raise ValueError("invalid_capability_inventory")
        self._directory = Path(directory).resolve()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._path = self._directory / "cognition.json"
        self._model = model
        self._capabilities = available_capabilities
        self._allow_model_computation = allow_model_computation
        self._lock = threading.Lock()
        self._writer = (self._directory / "writer.lock").open("a+b")
        self._writer.seek(0, os.SEEK_END)
        if self._writer.tell() == 0:
            self._writer.write(b"0")
            self._writer.flush()
        self._writer.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._writer.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._load()
        except Exception:
            self._writer.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._writer.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _trace_path(self, start: dict) -> Path:
        return self._directory / (start["activation_id"] + ".jsonl")

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        if self._path.stat().st_size > MAX_JOURNAL_BYTES:
            raise ValueError("cognition_history_too_large")
        document = _json(self._path.read_text(encoding="utf-8"))
        if (
            type(document) is not dict or set(document) != {"version", "records", "sha256"}
            or type(document["version"]) is not int or document["version"] not in {1, 2}
            or type(document["records"]) is not list
            or len(document["records"]) > 3 * MAX_ACTIVATIONS
            or document["sha256"] != _digest(document["records"])
        ):
            raise ValueError("invalid_cognition_history")
        return document["records"]

    def _append(self, record: dict) -> None:
        records = [*self._load(), record]
        if len(records) > 3 * MAX_ACTIVATIONS:
            raise ValueError("activation_budget_exceeded")
        encoded = _canonical_json({"version": 2, "records": records, "sha256": _digest(records)})
        if len(encoded.encode("utf-8")) > MAX_JOURNAL_BYTES:
            raise ValueError("cognition_history_too_large")
        # ponytail: rewrite the bounded immutable prefix atomically; move to
        # segmented append records if more than 64 activations are justified.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._directory,
                                             prefix=".cognition-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def _sources(self, start: dict, trace: MindTrace | None = None) -> dict:
        sources = {item["ref"]: item for item in start["context"]["evidence"]}
        if trace is not None:
            for event in trace.events:
                if event.event_type == CAPABILITY_OBSERVED:
                    observation = _thaw(event.payload["observation"])
                    if observation["capability"] in {"run_world_model", "evaluate_model"}:
                        continue  # Computed predictions are never admitted as reality evidence.
                    observation = project_observation(observation, start["context"].get("task_view"))
                    text = observation_source_text(observation, start["context"].get("contract_version"))
                    if text:
                        sources["activation:observation"] = {
                            "ref": "activation:observation", "text": text,
                            "origin": "memory" if observation["capability"] == "recall_memory" else "execution",
                        }
        return sources

    def _model_artifact(self, trace: MindTrace) -> dict | None:
        for event in trace.events:
            if event.event_type == CAPABILITY_OBSERVED:
                observation = _thaw(event.payload["observation"])
                if observation["capability"] in {"run_world_model", "evaluate_model"} and observation["status"] == "computed":
                    return {"ref": trace.events[0].activation_id + ":model", "run": observation["result"]}
        return None

    def _artifact_by_ref(self, starts: dict, ends: dict, ref: str) -> dict:
        # ponytail: at most 64 activations; retain historical artifacts in their
        # existing traces instead of introducing a model store or index.
        for event_id, start in starts.items():
            if (ref == start["activation_id"] + ":model" and event_id in ends
                    and ends[event_id]["kind"] == "accepted"):
                artifact = self._model_artifact(MindTrace.reopen(self._trace_path(start)))
                if artifact is not None:
                    return artifact
        raise ValueError("unknown_model_artifact")

    def _fold(self, records: list[dict]):
        state = {"revision": 0, "intention": None, "items": {}, "sources": {}, "execution_ref": None,
                 "results": {}, "owner_task": None}
        starts: dict[str, dict] = {}
        ends: dict[str, dict] = {}
        for record in records:
            kind = record.get("kind")
            event_id = record.get("event_id")
            if kind == "started":
                if set(record) != {"kind", "event_id", "input", "request_digest", "activation_id", "base_revision", "context", "budget_version"}:
                    raise ValueError("invalid_cognition_start")
                value = record["input"]
                if (event_id in starts or event_id != value["event_id"]
                    or record["request_digest"] != _digest(value)
                    or record["activation_id"] != "activation-" + _digest([event_id, value])[:24]
                    or record["base_revision"] != state["revision"]
                    or record["budget_version"] not in {BUDGET_VERSION, MODEL_BUDGET_VERSION, *EVENT_BUDGET_VERSIONS}):
                    raise ValueError("invalid_cognition_start")
                if (record["budget_version"] == EXPRESSION_EVENT_BUDGET_VERSION
                        and record["context"].get("contract_version") not in EXPRESSION_CONTRACT_VERSIONS):
                    raise ValueError("budget_contract_conflict")
                if (record["budget_version"] == CONTINUITY_EVENT_BUDGET_VERSION
                        and record["context"].get("contract_version") != CONTINUITY_CONTRACT_VERSION):
                    raise ValueError("budget_contract_conflict")
                intention = [value["intention_ref"], value["intention_revision"], value["goal"]]
                if state["intention"] is not None and intention != state["intention"]:
                    raise ValueError("intention_conflict")
                task = value.get("owner_task")
                if state["owner_task"] is not None and task != state["owner_task"]:
                    raise ValueError("owner_task_identity_conflict")
                if task is not None:
                    expected = mind_task_view(task, value["goal"])
                    if record["context"].get("task_view") != expected:
                        raise ValueError("task_view_context_mismatch")
                elif "task_view" in record["context"]:
                    raise ValueError("task_view_without_owner")
                state["owner_task"] = task
                state["intention"] = intention
                starts[event_id] = record
            elif kind == "result_received":
                if (set(record) != {"kind", "event_id", "result", "result_digest"}
                        or event_id not in starts or event_id in ends or event_id in state["results"]
                        or starts[event_id]["budget_version"] not in EVENT_BUDGET_VERSIONS
                        or record["result_digest"] != _digest(record["result"])):
                    raise ValueError("invalid_result_receipt")
                trace = MindTrace.reopen(self._trace_path(starts[event_id]))
                request = self._request(starts[event_id], trace)
                result = record["result"]
                if set(result) != {"request_ref", "observation", "error"} or result["request_ref"] != request.request_ref:
                    raise ValueError("result_request_conflict")
                preview = trace.preview_capability_result(result["observation"], result["error"])
                if len(trace.events) > preview.seq:
                    actual = trace.events[preview.seq]
                    if (actual.event_type != preview.event_type
                            or _canonical_json(_thaw(actual.payload)) != _canonical_json(_thaw(preview.payload))):
                        raise ValueError("result_trace_conflict")
                state["results"][event_id] = record
            elif kind in {"accepted", "failed"}:
                if event_id not in starts or event_id in ends:
                    raise ValueError("invalid_cognition_end")
                if kind == "accepted":
                    if set(record) != {"kind", "event_id", "revision", "updates"}:
                        raise ValueError("invalid_cognition_commit")
                    start = starts[event_id]
                    if record["revision"] != state["revision"] + 1 or start["base_revision"] != state["revision"]:
                        raise ValueError("stale_cognition_commit")
                    trace = MindTrace.reopen(self._trace_path(start))
                    if (start["budget_version"] in EVENT_BUDGET_VERSIONS
                            and any(event.event_type == CAPABILITY_OBSERVED for event in trace.events)
                            and event_id not in state["results"]):
                        raise ValueError("missing_result_receipt")
                    if (_canonical_json(_thaw(trace.events[0].payload.get("cognitive_context")))
                            != _canonical_json(start["context"])):
                        raise ValueError("cognition_context_mismatch")
                    replay = replay_activation(trace.events)
                    raw = _json(replay.model_outputs[-1])
                    if replay.failure_code is not None or raw.get("updates") != record["updates"]:
                        raise ValueError("cognition_trace_mismatch")
                    sources = self._sources(start, trace)
                    items = _apply_updates(state["items"], record["updates"], sources, event_id,
                                           self._model_artifact(trace), contract=start["context"].get("contract_version"))
                    # Local observation aliases must not collide across activations.
                    durable_ref = start["activation_id"] + ":observation"
                    for item in items.values():
                        for basis in item.get("basis", []):
                            if basis["ref"] == "activation:observation":
                                basis["ref"] = durable_ref
                    if "activation:observation" in sources:
                        sources[durable_ref] = {**sources["activation:observation"], "ref": durable_ref}
                    used = {basis["ref"] for item in items.values() for basis in item.get("basis", [])}
                    state.update(revision=record["revision"], items=items,
                                 sources={ref: sources[ref] for ref in used}, execution_ref=start["input"]["execution_ref"])
                elif set(record) != {"kind", "event_id", "error"}:
                    raise ValueError("invalid_cognition_failure")
                ends[event_id] = record
            else:
                raise ValueError("invalid_cognition_record")
        return state, starts, ends

    def inspect(self) -> MindView:
        with self._lock:
            state, _, _ = self._fold(self._load())
            intention = state["intention"]
            return MindView(state["revision"], intention[0] if intention else None,
                            intention[1] if intention else None,
                            tuple(_freeze(item) for item in state["items"].values()))

    def _receipt(self, start: dict, end: dict, *, duplicate: bool = False) -> MindReceipt:
        if end["kind"] == "failed":
            return MindReceipt(start["event_id"], "failed", start["base_revision"], error=end["error"])
        replay = replay_activation(MindTrace.reopen(self._trace_path(start)).events)
        return MindReceipt(start["event_id"], "duplicate" if duplicate else "accepted",
                           end["revision"], replay.final_result)

    def _request(self, start: dict, trace: MindTrace) -> MindRequest:
        event = next((event for event in trace.events if event.event_type == CAPABILITY_REQUESTED), None)
        if event is None:
            raise ValueError("unknown_capability_request")
        return MindRequest(f"{start['activation_id']}:request:{event.seq}", start["event_id"],
                           event.payload, _freeze(start["context"].get("model_probe")))

    def _waiting(self, start: dict) -> MindReceipt:
        try:
            trace = MindTrace.reopen_for_result(self._trace_path(start))
        except TraceError:
            # A persisted first output already determines its request. Recover
            # that fact without resampling if the process died before dispatch.
            trace = MindTrace.reopen_for_finalization(self._trace_path(start))
            last = trace.events[-1]
            available = trace.events[0].payload["available_capabilities"]
            parsed = _parse_output(last.payload["text"], unified=True,
                                  compute=any(name in {"run_world_model", "evaluate_model"} for name in available))
            if (last.payload["call_index"] != 1 or not isinstance(parsed, _CapabilityRequest)
                    or parsed.capability not in available):
                raise TraceError("trace_not_waiting_for_result")
            trace.append(CAPABILITY_REQUESTED, _request_payload(parsed), source_event_seqs=(last.seq,))
        return MindReceipt(start["event_id"], "waiting", start["base_revision"],
                           request=self._request(start, trace))

    def activate(self, value: MindInput) -> MindReceipt:
        document = _input_document(value)
        if self._writer.closed:
            raise ValueError("mind_is_closed")
        if not self._lock.acquire(blocking=False):
            return MindReceipt(value.event_id, "busy", 0, error="activation_in_progress")
        try:
            if self._writer.closed:
                raise ValueError("mind_is_closed")
            state, starts, ends = self._fold(self._load())
            if value.event_id in starts:
                start = starts[value.event_id]
                if start["request_digest"] != _digest(document):
                    raise ValueError("event_identity_conflict")
                if value.event_id in ends:
                    return self._receipt(start, ends[value.event_id], duplicate=True)
                if start["budget_version"] in EVENT_BUDGET_VERSIONS and value.event_id not in state["results"]:
                    try:
                        return self._waiting(start)
                    except TraceError:
                        pass  # An uncertain model call is never resampled on retry.
                return self._finish(start, state, recovering=True)
            if any(event_id not in ends for event_id in starts):
                return MindReceipt(value.event_id, "busy", state["revision"], error="prior_event_pending")
            intention = [value.intention_ref, value.intention_revision, value.goal]
            if state["intention"] is not None and intention != state["intention"]:
                raise ValueError("intention_conflict")
            if state["owner_task"] is not None and document.get("owner_task") != state["owner_task"]:
                raise ValueError("owner_task_identity_conflict")
            if len(starts) >= MAX_ACTIVATIONS:
                return MindReceipt(value.event_id, "failed", state["revision"], error="activation_budget_exceeded")
            sources = dict(state["sources"])
            for evidence in document["evidence"]:
                for previous in starts.values():
                    for prior in previous["input"]["evidence"]:
                        if prior["ref"] == evidence["ref"] and prior != evidence:
                            raise ValueError("evidence_identity_conflict")
                if evidence["ref"] in sources and sources[evidence["ref"]] != evidence:
                    raise ValueError("evidence_identity_conflict")
                sources[evidence["ref"]] = evidence
            context = {"revision": state["revision"], "event_id": value.event_id,
                       "intention_ref": value.intention_ref, "intention_revision": value.intention_revision,
                       "items": list(state["items"].values()), "evidence": list(sources.values())}
            if "owner_task" in document:
                context["task_view"] = mind_task_view(document["owner_task"], document["goal"])
                task_source = {"ref": "owner-task:" + context["task_view"]["owner_task_sha256"],
                    "text": document["owner_task"]["business_goal"], "origin": "execution"}
                if task_source["ref"] not in sources:
                    context["evidence"].append(task_source)
            contract = getattr(self._model, "cognitive_contract_version", None)
            if contract is not None:
                if contract not in CHAIN_CONTRACT_VERSIONS:
                    raise ValueError("unsupported_cognitive_contract")
                context["contract_version"] = contract
            if "model_probe" in document:
                context["model_probe"] = document["model_probe"]
                for previous in starts.values():
                    prior = previous["input"].get("model_probe")
                    if (prior and prior["ref"] == document["model_probe"]["ref"]
                            and _digest(prior) != _digest(document["model_probe"])):
                        raise ValueError("model_probe_identity_conflict")
            if "model_feedback" in document:
                feedback = document["model_feedback"]
                artifact = self._artifact_by_ref(starts, ends, feedback["artifact_ref"])
                report = verify_run(artifact["run"], list(feedback["observed_steps"]),
                                    observed_outcomes=list(feedback["observed_outcomes"]))
                # The report is recomputed from persisted predictions and new
                # owner data. It is not admitted to the factual quote source map.
                context["model_feedback"] = {**report, "artifact_ref": artifact["ref"],
                                             "source_refs": list(feedback["source_refs"])}
            if len(_canonical_json(context)) > context_limit(context):
                return MindReceipt(value.event_id, "failed", state["revision"], error="context_budget_exceeded")
            start = {"kind": "started", "event_id": value.event_id, "input": document,
                     "request_digest": _digest(document), "base_revision": state["revision"],
                     "activation_id": "activation-" + _digest([value.event_id, document])[:24],
                     "context": context, "budget_version": (CONTINUITY_EVENT_BUDGET_VERSION
                         if contract == CONTINUITY_CONTRACT_VERSION else EXPRESSION_EVENT_BUDGET_VERSION
                         if contract in EXPRESSION_CONTRACT_VERSIONS else EVENT_BUDGET_VERSION)}
            self._append(start)  # No model call before durable event identity.
            trace = MindTrace.create(self._trace_path(start), activation_id=start["activation_id"])
            result = _run_activation(
                ActivationInput(value.trigger, value.goal, value.execution_status),
                model=self._model, memory_retriever=None,
                execution_observation=value.execution_observation,
                initial_execution_observation_visible=value.execution_observation is not None,
                allow_information_acquisition=True, trace=trace,
                supervisor_evidence=None, cognitive_context=context,
                allow_model_computation=self._allow_model_computation,
                native_protocol=getattr(self._model, "native_protocol_version", None),
                defer_capabilities=(*self._capabilities,
                    *(["evaluate_model" if value.model_probe is not None else "run_world_model"]
                      if self._allow_model_computation else [])),
            )
            if isinstance(result, _CapabilityRequest):
                return self._waiting(start)
            if isinstance(result, ActivationFailure):
                end = {"kind": "failed", "event_id": value.event_id, "error": result.code}
                self._append(end)
                return self._receipt(start, end)
            return self._finish(start, state)
        finally:
            self._lock.release()

    def accept_result(self, value: MindResultEvent) -> MindReceipt:
        """Consume one correlated data event. No external owner is called here."""
        if type(value) is not MindResultEvent:
            raise ValueError("invalid_result_event")
        _text(value.request_ref, 128)
        document = _json(_canonical_json({"request_ref": value.request_ref,
                       "observation": _thaw(value.observation), "error": value.error}))
        if len(_canonical_json(document)) > 3500:
            raise ValueError("result_too_large")
        with self._lock:
            if self._writer.closed:
                raise ValueError("mind_is_closed")
            state, starts, ends = self._fold(self._load())
            start = next((start for start in starts.values()
                          if value.request_ref.startswith(start["activation_id"] + ":request:")), None)
            if start is None or start["budget_version"] not in EVENT_BUDGET_VERSIONS:
                raise ValueError("unknown_result_request")
            event_id = start["event_id"]
            if event_id in state["results"]:
                if state["results"][event_id]["result_digest"] != _digest(document):
                    raise ValueError("result_identity_conflict")
                if event_id in ends:
                    return self._receipt(start, ends[event_id], duplicate=True)
                return self._finish(start, state, recovering=True)
            if event_id in ends:
                raise ValueError("activation_already_ended")
            if document["observation"] is not None:
                project_observation(document["observation"], start["context"].get("task_view"))
            trace = MindTrace.reopen_for_result(self._trace_path(start))
            request = self._request(start, trace)
            if value.request_ref != request.request_ref:
                raise ValueError("result_request_conflict")
            trace.preview_capability_result(document["observation"], document["error"])
            # Durable identity before continuing inference: a lost provider
            # response may fail conservatively, but cannot cause a third call.
            self._append({"kind": "result_received", "event_id": event_id,
                          "result": document, "result_digest": _digest(document)})
            observation = (ActivationFailure(document["error"]) if document["error"] is not None
                           else document["observation"])
            result = _continue_activation(trace=trace, model=self._model, observation=observation,
                cognitive=True, compute=any(name in {"run_world_model", "evaluate_model"}
                    for name in trace.events[0].payload["available_capabilities"]))
            if isinstance(result, ActivationFailure):
                end = {"kind": "failed", "event_id": event_id, "error": result.code}
                self._append(end)
                return self._receipt(start, end)
            return self._finish(start, state)

    def _finish(self, start: dict, state: dict, *, recovering: bool = False) -> MindReceipt:
        if recovering and getattr(self._model, "native_protocol_version", None):
            try:
                native_trace = MindTrace.reopen_for_native(self._trace_path(start))
            except TraceError as error:
                if str(error) not in {'native_protocol_not_enabled', 'event_prefix_has_no_model_request', 'native_result_unknown'}:
                    end = {"kind": "failed", "event_id": start["event_id"], "error": "trace_failed"}
                    self._append(end)
                    return self._receipt(start, end)
                # No resampling of a call whose outcome is unknown.
            else:
                from Mind.experiment_a import _resume_native_activation
                result = _resume_native_activation(native_trace, self._model)
                if isinstance(result, _CapabilityRequest):
                    return self._waiting(start)
                if isinstance(result, ActivationFailure):
                    end = {"kind": "failed", "event_id": start["event_id"], "error": result.code}
                    self._append(end)
                    return self._receipt(start, end)
        try:
            trace = MindTrace.reopen(self._trace_path(start))
            try:
                replay = replay_activation(trace.events)
            except TraceError:
                if not recovering:
                    raise
                trace = MindTrace.reopen_for_finalization(self._trace_path(start))
                raw = trace.events[-1].payload["text"]
                version = trace.events[0].payload["projector_version"]
                unified = version in {COGNITIVE_STEP_PROJECTOR_VERSION, COGNITIVE_MODEL_PROJECTOR_VERSION,
                                      COGNITIVE_COMPACT_PROJECTOR_VERSION, COGNITIVE_PROBE_PROJECTOR_VERSION}
                result = _parse_output(raw, cognitive=not unified, unified=unified,
                                       compute=version in {COGNITIVE_MODEL_PROJECTOR_VERSION, COGNITIVE_COMPACT_PROJECTOR_VERSION,
                                                           COGNITIVE_PROBE_PROJECTOR_VERSION})
                payload = _json(raw)
                output = payload.get("next" if unified else "output", {})
                if (not isinstance(result, ActivationFailure) and type(output) is dict
                        and output.get("type") in {"no_change", "directive", "decision_intent"}):
                    _record_terminal(trace, result, (trace.events[-1].seq,))
                replay = replay_activation(trace.events)
            if replay.failure_code:
                raise ValueError("activation_failed")
            updates = _json(replay.model_outputs[-1])["updates"]
            sources = self._sources(start, trace)
            items = _apply_updates(state["items"], updates, sources, start["event_id"], self._model_artifact(trace),
                                   contract=start["context"].get("contract_version"))
            used = {basis["ref"] for item in items.values() for basis in item.get("basis", [])}
            next_context = {**start["context"], "items": list(items.values()),
                            "evidence": [sources[ref] for ref in sorted(used)]}
            if len(_canonical_json(next_context)) > context_limit(next_context):
                raise ValueError("context_budget_exceeded")
        except (KeyError, TypeError, ValueError) as exc:
            safe = {"ungrounded_basis", "unknown_item", "unknown_scenario_assumption",
                    "active_item_budget_exceeded", "assessment_needs_evidence",
                    "unknown_model_artifact", "context_budget_exceeded"}
            error = str(exc) if str(exc) in safe else ("interrupted" if recovering else "invalid_cognitive_update")
            end = {"kind": "failed", "event_id": start["event_id"], "error": error}
        else:
            end = {"kind": "accepted", "event_id": start["event_id"],
                   "revision": state["revision"] + 1, "updates": updates}
        self._append(end)
        return self._receipt(start, end)

    def verify_model(self, model_id: str, observed_steps: list[dict | None],
                     *, observed_outcomes: list[str | None] | None = None) -> dict:
        """Trusted-host check of an accepted artifact; never a model capability.

        The returned check neither mutates accepted cognition nor attests when
        the prediction was made. Prospective eligibility needs an owner receipt.
        """
        with self._lock:
            state, _, _ = self._fold(self._load())
            item = state["items"].get(model_id)
            if item is None or item["kind"] != "model":
                raise ValueError("unknown_model_item")
            checked = verify_run(item["artifact"]["run"], observed_steps, observed_outcomes=observed_outcomes)
            return {**checked, "artifact_ref": item["artifact_ref"]}

    def prepare_directive(self, event_id: str, *, execution_ref: str,
                          decision_id: str, intention_ref: str,
                          intention_revision: int) -> DirectiveApplication | None:
        """Trusted-host delivery only; never exposed as a model capability."""
        with self._lock:
            if self._writer.closed:
                raise ValueError("mind_is_closed")
            _text(event_id, 128)
            _text(execution_ref, 128)
            _text(decision_id, 128)
            _text(intention_ref, 128)
            if type(intention_revision) is not int or intention_revision < 0:
                raise ValueError("invalid_intention_revision")
            state, starts, ends = self._fold(self._load())
            if event_id not in ends or ends[event_id]["kind"] != "accepted":
                return None
            if ends[event_id]["revision"] != state["revision"]:
                return None
            start = starts[event_id]
            value = start["input"]
            if (
                execution_ref != value["execution_ref"] or execution_ref != state["execution_ref"]
                or [intention_ref, intention_revision] != [value["intention_ref"], value["intention_revision"]]
            ):
                return None
            trace = MindTrace.reopen(self._trace_path(start))
            replay = replay_activation(trace.events)
            if replay.final_result is None or replay.final_result.get("type") != "directive":
                return None
            # Bind an Actor-local decision to its actual run and Root identity.
            target = f"{execution_ref}:root:{decision_id}"
            _text(target, 128)
            return prepare_for_execution_decision(
                MindTrace.reopen_for_delivery(self._trace_path(start)), target,
            )
