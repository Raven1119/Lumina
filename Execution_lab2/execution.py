from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Protocol, TypeAlias


@dataclass(frozen=True)
class ReadRequest:
    path: str


@dataclass(frozen=True)
class WriteRequest:
    path: str
    content: str


@dataclass(frozen=True)
class ShellRequest:
    argv: tuple[str, ...]


ToolRequest: TypeAlias = ReadRequest | WriteRequest | ShellRequest


@dataclass(frozen=True)
class FileContentEquals:
    path: str
    expected_content: str


CompletionSpec: TypeAlias = FileContentEquals


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str = ""
    error_code: str | None = None
    error: str | None = None
    exit_code: int | None = None
    truncated: bool = False


@dataclass(frozen=True)
class ToolCall:
    request: ToolRequest


@dataclass(frozen=True)
class ClaimComplete:
    pass


@dataclass(frozen=True)
class Wait:
    event_type: str


@dataclass(frozen=True)
class ExternalEvent:
    event_type: str
    data: str = ""


Action: TypeAlias = ToolCall | Wait | ClaimComplete


@dataclass(frozen=True)
class NativeModelDecision:
    action: Action | None
    provider_wire_request: object
    raw_provider_response: object | None
    provider_tool_call_id: str | None = None
    failure: str | None = None


@dataclass(frozen=True)
class NativeToolContinuation:
    previous_model_context: str
    raw_provider_response: object
    provider_tool_call_id: str


def _structured_action(value: object) -> Action | None:
    if isinstance(value, NativeModelDecision):
        return None if value.failure is not None else _structured_action(value.action)
    if isinstance(value, ToolCall):
        return value
    if isinstance(value, Wait):
        return value if isinstance(value.event_type, str) and value.event_type else None
    if isinstance(value, ClaimComplete):
        return value
    return None


@dataclass(frozen=True)
class Observation:
    request: ToolRequest
    result: ToolResult
    provider_tool_call_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.result.ok


@dataclass(frozen=True)
class CompletionEvidence:
    spec_type: Literal["file_content_equals"]
    spec_fingerprint: str
    observed_path: str
    matched: bool
    reason: Literal["matched", "missing", "content_mismatch", "unreadable"]


@dataclass(frozen=True)
class CompletionObservation:
    status: Literal["rejected"]
    evidence: CompletionEvidence

    @property
    def ok(self) -> bool:
        return False


RuntimeObservation: TypeAlias = Observation | CompletionObservation


TOOL_CONTRACTS = (
    "read(path: str) -> ToolResult",
    "write(path: str, content: str) -> ToolResult",
    "shell(argv: tuple[str, ...]) -> ToolResult",
    "wait(event_type: str)",
    "claim_complete()",
)


@dataclass(frozen=True)
class ModelRequest:
    context: str
    available_tools: tuple[str, ...]
    source_event_refs: tuple[str, ...]
    native_tool_continuation: NativeToolContinuation | None = None
    model_visible_context_limit: int | None = None

    @property
    def context_size_chars(self) -> int:
        return len(self.context)


class Model(Protocol):
    identifier: str

    def decide(self, request: ModelRequest) -> object: ...


class ScriptedModel:
    def __init__(
        self, actions: list[object], *, identifier: str = "scripted-model"
    ) -> None:
        self._actions = iter(actions)
        self.identifier = identifier
        self.received_requests: list[ModelRequest] = []

    def decide(self, request: ModelRequest) -> object:
        self.received_requests.append(request)
        return next(self._actions)


class SharedEnvironment:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")


def _write_content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _completion_spec_fingerprint(spec: CompletionSpec) -> str:
    material = json.dumps(
        {
            "expected_content": spec.expected_content,
            "path": spec.path,
            "type": "file_content_equals",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _verify_completion(
    spec: CompletionSpec, environment: SharedEnvironment
) -> CompletionEvidence:
    fingerprint = _completion_spec_fingerprint(spec)

    def evidence(
        matched: bool,
        reason: Literal["matched", "missing", "content_mismatch", "unreadable"],
    ) -> CompletionEvidence:
        return CompletionEvidence(
            "file_content_equals",
            fingerprint,
            spec.path,
            matched,
            reason,
        )

    relative_path = Path(spec.path)
    if relative_path.is_absolute():
        return evidence(False, "unreadable")
    try:
        path = (environment.workspace / relative_path).resolve()
        path.relative_to(environment.workspace)
        with path.open(encoding="utf-8") as source:
            observed_content = source.read(len(spec.expected_content) + 1)
    except FileNotFoundError:
        return evidence(False, "missing")
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return evidence(False, "unreadable")
    if observed_content != spec.expected_content:
        return evidence(False, "content_mismatch")
    return evidence(True, "matched")


def _evidence_matches_spec(
    evidence: object, spec: object, *, matched: bool
) -> bool:
    return (
        isinstance(evidence, CompletionEvidence)
        and isinstance(spec, FileContentEquals)
        and evidence.spec_type == "file_content_equals"
        and evidence.spec_fingerprint == _completion_spec_fingerprint(spec)
        and evidence.observed_path == spec.path
        and evidence.matched is matched
        and evidence.reason
        in (("matched",) if matched else ("missing", "content_mismatch", "unreadable"))
    )


class ToolHost:
    def __init__(
        self,
        environment: SharedEnvironment,
        *,
        shell_timeout_seconds: float = 5.0,
        max_output_chars: int = 10_000,
    ) -> None:
        if shell_timeout_seconds <= 0:
            raise ValueError("shell_timeout_seconds must be positive")
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive")
        self._environment = environment
        self._shell_timeout_seconds = shell_timeout_seconds
        self._max_output_chars = max_output_chars

    @property
    def environment(self) -> SharedEnvironment:
        return self._environment

    def execute(self, request: ToolRequest) -> ToolResult:
        try:
            if isinstance(request, ShellRequest):
                if not isinstance(request.argv, tuple) or not request.argv or not all(
                    isinstance(argument, str) and argument
                    for argument in request.argv
                ):
                    return ToolResult(
                        ok=False,
                        error_code="invalid_request",
                        error="shell argv must contain non-empty strings",
                    )
                completed = subprocess.run(
                    request.argv,
                    cwd=self._environment.workspace,
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self._shell_timeout_seconds,
                    check=False,
                )
                output, output_truncated = self._bounded(completed.stdout)
                error, error_truncated = self._bounded(completed.stderr)
                if completed.returncode == 0:
                    return ToolResult(
                        ok=True,
                        output=output,
                        exit_code=0,
                        truncated=output_truncated or error_truncated,
                    )
                return ToolResult(
                    ok=False,
                    output=output,
                    error_code="process_failed",
                    error=error or f"process exited with code {completed.returncode}",
                    exit_code=completed.returncode,
                    truncated=output_truncated or error_truncated,
                )
            if not isinstance(request, (ReadRequest, WriteRequest)):
                return ToolResult(
                    ok=False,
                    error_code="unknown_tool",
                    error=f"unsupported request type: {type(request).__name__}",
                )
            if not isinstance(request.path, str) or not request.path:
                return ToolResult(
                    ok=False,
                    error_code="invalid_request",
                    error="path must be a non-empty string",
                )
            if isinstance(request, WriteRequest) and not isinstance(request.content, str):
                return ToolResult(
                    ok=False,
                    error_code="invalid_request",
                    error="write content must be a string",
                )
            relative_path = Path(request.path)
            if relative_path.is_absolute():
                return ToolResult(
                    ok=False,
                    error_code="workspace_boundary",
                    error="absolute paths are not allowed",
                )
            path = (self._environment.workspace / relative_path).resolve()
            try:
                path.relative_to(self._environment.workspace)
            except ValueError:
                return ToolResult(
                    ok=False,
                    error_code="workspace_boundary",
                    error="path resolves outside the workspace",
                )
            if isinstance(request, ReadRequest):
                with path.open(encoding="utf-8") as source:
                    output = source.read(self._max_output_chars + 1)
                output, truncated = self._bounded(output)
                return ToolResult(ok=True, output=output, truncated=truncated)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(request.content, encoding="utf-8")
            return ToolResult(ok=True, output=f"wrote {len(request.content)} characters")
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            output, truncated = self._bounded(output)
            return ToolResult(
                ok=False,
                output=output,
                error_code="shell_timeout",
                error=f"shell exceeded {self._shell_timeout_seconds} seconds",
                truncated=truncated,
            )
        except FileNotFoundError as exc:
            error, truncated = self._bounded(f"{type(exc).__name__}: {exc}")
            return ToolResult(
                ok=False,
                error_code="not_found",
                error=error,
                truncated=truncated,
            )
        except Exception as exc:
            error, truncated = self._bounded(f"{type(exc).__name__}: {exc}")
            return ToolResult(
                ok=False,
                error_code="tool_error",
                error=error,
                truncated=truncated,
            )

    def inspect_write(self, request: WriteRequest) -> Mapping[str, object] | None:
        if (
            not isinstance(request.path, str)
            or not request.path
            or not isinstance(request.content, str)
        ):
            return None
        relative_path = Path(request.path)
        if relative_path.is_absolute():
            return None
        try:
            path = (self._environment.workspace / relative_path).resolve()
            path.relative_to(self._environment.workspace)
            with path.open(encoding="utf-8") as source:
                observed_content = source.read(len(request.content) + 1)
        except (OSError, RuntimeError, UnicodeError, ValueError):
            return None
        if observed_content != request.content:
            return None
        return MappingProxyType(
            {
                "path": request.path,
                "intended_content_sha256": _write_content_sha256(request.content),
                "observed_content_sha256": _write_content_sha256(observed_content),
                "observed_chars": len(observed_content),
            }
        )

    def _bounded(self, text: str) -> tuple[str, bool]:
        truncated = len(text) > self._max_output_chars
        return text[: self._max_output_chars], truncated


@dataclass(frozen=True)
class ExecutionStep:
    decision: int
    action: Action
    observation: RuntimeObservation | None


EventType: TypeAlias = Literal[
    "EXECUTION_STARTED",
    "MODEL_DECISION",
    "TOOL_CALL_STARTED",
    "TOOL_RESULT",
    "TOOL_FAILED",
    "ACTION_RECONCILED",
    "COMPLETION_CLAIMED",
    "COMPLETION_VERIFIED",
    "COMPLETION_REJECTED",
    "ROOT_WAITING",
    "EXTERNAL_EVENT_RECEIVED",
    "ROOT_WOKEN",
    "EXECUTION_COMPLETED",
    "EXECUTION_FAILED",
]


@dataclass(frozen=True)
class ExecutionEvent:
    event_id: str
    sequence: int
    event_type: EventType
    payload: Mapping[str, object]
    source_event_refs: tuple[str, ...]


@dataclass(frozen=True)
class DecisionFrame:
    decision_id: str
    model_identifier: str
    goal: str
    state_version: int
    source_event_refs: tuple[str, ...]
    actual_request: ModelRequest
    actual_tools_exposed: tuple[str, ...]
    raw_model_response: object
    resulting_action: Action | None
    provider_wire_request: object | None = None
    raw_provider_response: object | None = None
    provider_tool_call_id: str | None = None


_SERIALIZABLE_TYPES = {
    value.__name__: value
    for value in (
        ReadRequest,
        WriteRequest,
        ShellRequest,
        FileContentEquals,
        ToolResult,
        ToolCall,
        Wait,
        ExternalEvent,
        ClaimComplete,
        Observation,
        CompletionEvidence,
        CompletionObservation,
        NativeModelDecision,
        NativeToolContinuation,
        ModelRequest,
        DecisionFrame,
    )
}


def _encode_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"$type": "bytes", "hex": value.hex()}
    if isinstance(value, frozenset):
        encoded_items = [_encode_value(item) for item in value]
        return {
            "$type": "frozenset",
            "items": sorted(
                encoded_items,
                key=lambda item: json.dumps(
                    item, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                ),
            ),
        }
    if isinstance(value, Mapping):
        return {
            "$type": "mapping",
            "items": [
                [_encode_value(key), _encode_value(item)]
                for key, item in value.items()
            ],
        }
    if isinstance(value, (list, tuple)):
        return {"$type": "tuple", "items": [_encode_value(item) for item in value]}
    if is_dataclass(value) and type(value).__name__ in _SERIALIZABLE_TYPES:
        return {
            "$type": type(value).__name__,
            "fields": {
                field.name: _encode_value(getattr(value, field.name))
                for field in fields(value)
            },
        }
    raise TypeError(f"unsupported durable event value: {type(value).__name__}")


def _decode_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if not isinstance(value, dict) or not isinstance(value.get("$type"), str):
        raise ValueError("malformed durable event value")
    value_type = value["$type"]
    if value_type == "bytes":
        if set(value) != {"$type", "hex"} or not isinstance(value["hex"], str):
            raise ValueError("malformed durable bytes")
        try:
            return bytes.fromhex(value["hex"])
        except ValueError as exc:
            raise ValueError("malformed durable bytes") from exc
    if value_type == "frozenset":
        if set(value) != {"$type", "items"} or not isinstance(value["items"], list):
            raise ValueError("malformed durable frozenset")
        try:
            return frozenset(_decode_value(item) for item in value["items"])
        except TypeError as exc:
            raise ValueError("malformed durable frozenset") from exc
    if value_type == "mapping":
        if set(value) != {"$type", "items"} or not isinstance(value["items"], list):
            raise ValueError("malformed durable mapping")
        decoded: dict[object, object] = {}
        for item in value["items"]:
            if not isinstance(item, list) or len(item) != 2:
                raise ValueError("malformed durable mapping entry")
            key = _decode_value(item[0])
            try:
                if key in decoded:
                    raise ValueError("malformed durable mapping entry")
                decoded[key] = _decode_value(item[1])
            except TypeError as exc:
                raise ValueError("malformed durable mapping key") from exc
        return decoded
    if value_type == "tuple":
        if set(value) != {"$type", "items"} or not isinstance(value["items"], list):
            raise ValueError("malformed durable tuple")
        return tuple(_decode_value(item) for item in value["items"])
    value_class = _SERIALIZABLE_TYPES.get(value_type)
    if value_class is None or set(value) != {"$type", "fields"}:
        raise ValueError(f"unknown durable event value type: {value_type}")
    encoded_fields = value["fields"]
    if not isinstance(encoded_fields, dict):
        raise ValueError("malformed durable dataclass")
    expected_fields = {field.name for field in fields(value_class)}
    if set(encoded_fields) != expected_fields:
        raise ValueError(f"invalid fields for durable {value_type}")
    return value_class(
        **{key: _decode_value(item) for key, item in encoded_fields.items()}
    )


class EventLog:
    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path | None = None, *, _loading: bool = False) -> None:
        self._events: list[ExecutionEvent] = []
        self._path = Path(path) if path is not None else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not _loading and self._path.exists() and self._path.stat().st_size:
                raise ValueError("durable event log already exists; load it explicitly")

    @property
    def events(self) -> tuple[ExecutionEvent, ...]:
        return tuple(self._events)

    def append(
        self,
        event_type: EventType,
        payload: Mapping[str, object],
        source_event_refs: tuple[str, ...] = (),
    ) -> ExecutionEvent:
        self._validate_append(event_type, payload, source_event_refs)
        known_ids = {event.event_id for event in self._events}
        if any(reference not in known_ids for reference in source_event_refs):
            raise ValueError("source event references must already exist")
        sequence = len(self._events) + 1
        event = ExecutionEvent(
            event_id=f"event-{sequence:06d}",
            sequence=sequence,
            event_type=event_type,
            payload=MappingProxyType(
                {key: self._freeze(value) for key, value in payload.items()}
            ),
            source_event_refs=tuple(source_event_refs),
        )
        if self._path is not None:
            self._persist(event)
        self._events.append(event)
        return event

    @classmethod
    def load(cls, path: str | Path) -> EventLog:
        event_log = cls(path, _loading=True)
        if not event_log._path.exists():
            raise FileNotFoundError(event_log._path)
        with event_log._path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise ValueError(f"malformed event log at line {line_number}")
                try:
                    event = event_log._event_from_record(json.loads(line))
                    event_log._accept_loaded(event)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"malformed event log at line {line_number}: {exc}"
                    ) from exc
        return event_log

    def _persist(self, event: ExecutionEvent) -> None:
        record = {
            "schema_version": self.SCHEMA_VERSION,
            "event_id": event.event_id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "payload": _encode_value(event.payload),
            "source_event_refs": _encode_value(event.source_event_refs),
        }
        serialized = json.dumps(
            record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        with self._path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @classmethod
    def _event_from_record(cls, record: object) -> ExecutionEvent:
        if not isinstance(record, dict) or set(record) != {
            "schema_version",
            "event_id",
            "sequence",
            "event_type",
            "payload",
            "source_event_refs",
        }:
            raise ValueError("invalid durable event record")
        if (
            type(record["schema_version"]) is not int
            or record["schema_version"] != cls.SCHEMA_VERSION
        ):
            raise ValueError("unsupported durable event schema")
        payload = _decode_value(record["payload"])
        source_refs = _decode_value(record["source_event_refs"])
        if not isinstance(payload, Mapping) or not isinstance(source_refs, tuple):
            raise ValueError("invalid durable event payload")
        if (
            not isinstance(record["event_id"], str)
            or type(record["sequence"]) is not int
        ):
            raise ValueError("invalid durable event identity")
        return ExecutionEvent(
            event_id=record["event_id"],
            sequence=record["sequence"],
            event_type=record["event_type"],
            payload=MappingProxyType(
                {key: cls._freeze(value) for key, value in payload.items()}
            ),
            source_event_refs=source_refs,
        )

    def _accept_loaded(self, event: ExecutionEvent) -> None:
        self._validate_append(
            event.event_type, event.payload, event.source_event_refs
        )
        sequence = len(self._events) + 1
        if event.sequence != sequence or event.event_id != f"event-{sequence:06d}":
            raise ValueError("durable event sequence or id is not contiguous")
        self._events.append(event)

    def _validate_append(
        self,
        event_type: EventType,
        payload: Mapping[str, object],
        source_event_refs: tuple[str, ...],
    ) -> None:
        schemas = {
            "MODEL_DECISION": {"action", "frame"},
            "TOOL_CALL_STARTED": {"request"},
            "TOOL_RESULT": {"observation"},
            "TOOL_FAILED": {"observation"},
            "ACTION_RECONCILED": {"status", "evidence"},
            "COMPLETION_CLAIMED": {"claim"},
            "COMPLETION_VERIFIED": {"evidence"},
            "COMPLETION_REJECTED": {"observation"},
            "ROOT_WAITING": {"condition"},
            "EXTERNAL_EVENT_RECEIVED": {"event"},
            "ROOT_WOKEN": {"event"},
            "EXECUTION_COMPLETED": {"status"},
            "EXECUTION_FAILED": {"failure"},
        }
        start_schema = {
            "goal",
            "execution_id",
            "root_actor_id",
            "completion_spec",
        }
        if event_type == "EXECUTION_STARTED":
            if self._path is None and set(payload) == {"goal", "completion_spec"}:
                start_schema = {"goal", "completion_spec"}
        elif event_type not in schemas:
            raise ValueError(f"unknown execution event type: {event_type}")
        expected_schema = (
            start_schema if event_type == "EXECUTION_STARTED" else schemas[event_type]
        )
        if set(payload) != expected_schema:
            raise ValueError(f"invalid payload for {event_type}")
        if self._events and self._events[-1].event_type in (
            "EXECUTION_COMPLETED",
            "EXECUTION_FAILED",
        ):
            raise ValueError("terminal execution cannot accept more events")
        if event_type == "EXECUTION_STARTED":
            if (
                self._events
                or source_event_refs
                or not isinstance(payload["goal"], str)
                or not isinstance(payload["completion_spec"], FileContentEquals)
                or not isinstance(payload["completion_spec"].path, str)
                or not payload["completion_spec"].path
                or not isinstance(
                    payload["completion_spec"].expected_content, str
                )
                or any(
                    not isinstance(payload[field], str) or not payload[field]
                    for field in ("execution_id", "root_actor_id")
                    if field in payload
                )
            ):
                raise ValueError("execution must start once with an uncaused goal")
            return
        if not self._events or source_event_refs != (self._events[-1].event_id,):
            raise ValueError("event must cite the immediately preceding cause")

        previous = self._events[-1]
        if event_type == "MODEL_DECISION":
            frame = payload["frame"]
            raw_action = None
            provider_fidelity = True
            if isinstance(frame, DecisionFrame):
                raw_action = _structured_action(frame.raw_model_response)
                if isinstance(frame.raw_model_response, NativeModelDecision):
                    native = frame.raw_model_response
                    provider_fidelity = (
                        frame.provider_wire_request
                        == native.provider_wire_request
                        and frame.raw_provider_response
                        == native.raw_provider_response
                        and frame.provider_tool_call_id
                        == native.provider_tool_call_id
                    )
                else:
                    provider_fidelity = (
                        frame.provider_wire_request is None
                        and frame.raw_provider_response is None
                        and frame.provider_tool_call_id is None
                    )
            if (
                previous.event_type
                not in (
                    "EXECUTION_STARTED",
                    "TOOL_RESULT",
                    "TOOL_FAILED",
                    "ACTION_RECONCILED",
                    "COMPLETION_REJECTED",
                    "ROOT_WOKEN",
                )
                or not isinstance(frame, DecisionFrame)
                or payload["action"] != frame.resulting_action
                or raw_action != frame.resulting_action
                or frame.state_version != previous.sequence
                or frame.source_event_refs != source_event_refs
                or frame.actual_request.source_event_refs != source_event_refs
                or frame.actual_tools_exposed != frame.actual_request.available_tools
                or frame.goal != self._events[0].payload["goal"]
                or not provider_fidelity
            ):
                raise ValueError("model decision requires current execution state")
        elif event_type == "ROOT_WAITING":
            frame = previous.payload.get("frame")
            condition = payload["condition"]
            if (
                previous.event_type != "MODEL_DECISION"
                or not isinstance(frame, DecisionFrame)
                or not isinstance(frame.resulting_action, Wait)
                or frame.resulting_action != self._freeze(condition)
                or not isinstance(condition.event_type, str)
                or not condition.event_type
            ):
                raise ValueError("wait condition must match its model decision")
        elif event_type == "EXTERNAL_EVENT_RECEIVED":
            external_event = payload["event"]
            state = fold_execution_state(self.events)
            if (
                state.status != "waiting"
                or not isinstance(external_event, ExternalEvent)
                or not isinstance(external_event.event_type, str)
                or not external_event.event_type
                or not isinstance(external_event.data, str)
            ):
                raise ValueError("external events require a waiting Root")
        elif event_type == "ROOT_WOKEN":
            external_event = payload["event"]
            state = fold_execution_state(self.events)
            if (
                previous.event_type != "EXTERNAL_EVENT_RECEIVED"
                or not isinstance(external_event, ExternalEvent)
                or previous.payload.get("event") != self._freeze(external_event)
                or state.status != "waiting"
                or state.waiting_for != external_event.event_type
            ):
                raise ValueError("Root wakes only for its matching external event")
        elif event_type == "TOOL_CALL_STARTED":
            frame = previous.payload.get("frame")
            request = payload["request"]
            if (
                previous.event_type != "MODEL_DECISION"
                or not isinstance(frame, DecisionFrame)
                or not isinstance(frame.resulting_action, ToolCall)
                or frame.resulting_action.request != self._freeze(request)
            ):
                raise ValueError("tool call must match its model decision")
        elif event_type in ("TOOL_RESULT", "TOOL_FAILED"):
            observation = payload["observation"]
            decision_event = self._events[-2] if len(self._events) >= 2 else None
            decision_frame = (
                decision_event.payload.get("frame")
                if decision_event is not None
                and decision_event.event_type == "MODEL_DECISION"
                else None
            )
            expected_provider_tool_call_id = (
                decision_frame.provider_tool_call_id
                if isinstance(decision_frame, DecisionFrame)
                else None
            )
            if (
                previous.event_type != "TOOL_CALL_STARTED"
                or not isinstance(observation, Observation)
                or self._freeze(observation.request)
                != previous.payload.get("request")
                or observation.result.ok != (event_type == "TOOL_RESULT")
                or observation.provider_tool_call_id
                != expected_provider_tool_call_id
            ):
                raise ValueError("tool result must match its tool call and outcome")
        elif event_type == "ACTION_RECONCILED":
            request = previous.payload.get("request")
            evidence = payload["evidence"]
            if (
                previous.event_type != "TOOL_CALL_STARTED"
                or not isinstance(request, WriteRequest)
                or payload["status"] != "confirmed_applied"
                or not isinstance(evidence, Mapping)
                or set(evidence)
                != {
                    "path",
                    "intended_content_sha256",
                    "observed_content_sha256",
                    "observed_chars",
                }
                or evidence["path"] != request.path
                or evidence["intended_content_sha256"]
                != _write_content_sha256(request.content)
                or evidence["observed_content_sha256"]
                != evidence["intended_content_sha256"]
                or type(evidence["observed_chars"]) is not int
                or evidence["observed_chars"] != len(request.content)
            ):
                raise ValueError(
                    "reconciliation must exactly confirm its interrupted Write"
                )
        elif event_type == "COMPLETION_CLAIMED":
            frame = previous.payload.get("frame")
            claim = payload["claim"]
            if (
                previous.event_type != "MODEL_DECISION"
                or not isinstance(frame, DecisionFrame)
                or not isinstance(frame.resulting_action, ClaimComplete)
                or not isinstance(claim, ClaimComplete)
                or frame.resulting_action != claim
            ):
                raise ValueError("completion claim must match its model decision")
        elif event_type == "COMPLETION_VERIFIED":
            if (
                previous.event_type != "COMPLETION_CLAIMED"
                or not _evidence_matches_spec(
                    payload["evidence"],
                    self._events[0].payload.get("completion_spec"),
                    matched=True,
                )
            ):
                raise ValueError(
                    "completion verification must match the execution-start spec"
                )
        elif event_type == "COMPLETION_REJECTED":
            observation = payload["observation"]
            if (
                previous.event_type != "COMPLETION_CLAIMED"
                or not isinstance(observation, CompletionObservation)
                or observation.status != "rejected"
                or not _evidence_matches_spec(
                    observation.evidence,
                    self._events[0].payload.get("completion_spec"),
                    matched=False,
                )
            ):
                raise ValueError(
                    "completion rejection must match the execution-start spec"
                )
        elif event_type == "EXECUTION_COMPLETED":
            if (
                previous.event_type != "COMPLETION_VERIFIED"
                or payload["status"] != "verified"
            ):
                raise ValueError("completion requires verified environment evidence")
        elif event_type == "EXECUTION_FAILED":
            if not isinstance(payload["failure"], str) or previous.event_type not in (
                "MODEL_DECISION",
                "TOOL_RESULT",
                "TOOL_FAILED",
                "ACTION_RECONCILED",
                "COMPLETION_REJECTED",
                "ROOT_WOKEN",
            ):
                raise ValueError("failure requires the latest execution cause")

    @classmethod
    def _freeze(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return MappingProxyType(
                {key: cls._freeze(item) for key, item in value.items()}
            )
        if isinstance(value, (list, tuple)):
            frozen = tuple(cls._freeze(item) for item in value)
            if isinstance(value, tuple) and all(
                item is original for item, original in zip(frozen, value)
            ):
                return value
            return frozen
        if isinstance(value, set):
            return frozenset(cls._freeze(item) for item in value)
        if isinstance(value, ReadRequest):
            path = cls._freeze(value.path)
            return value if path is value.path else ReadRequest(path)
        if isinstance(value, WriteRequest):
            path = cls._freeze(value.path)
            content = cls._freeze(value.content)
            if path is value.path and content is value.content:
                return value
            return WriteRequest(path, content)
        if isinstance(value, ShellRequest):
            argv = cls._freeze(value.argv)
            return value if argv is value.argv else ShellRequest(argv)
        if isinstance(value, FileContentEquals):
            path = cls._freeze(value.path)
            expected_content = cls._freeze(value.expected_content)
            if path is value.path and expected_content is value.expected_content:
                return value
            return FileContentEquals(path, expected_content)
        if isinstance(value, ToolResult):
            fields = tuple(
                cls._freeze(field)
                for field in (
                    value.ok,
                    value.output,
                    value.error_code,
                    value.error,
                    value.exit_code,
                    value.truncated,
                )
            )
            if all(
                field is original
                for field, original in zip(
                    fields,
                    (
                        value.ok,
                        value.output,
                        value.error_code,
                        value.error,
                        value.exit_code,
                        value.truncated,
                    ),
                )
            ):
                return value
            return ToolResult(*fields)
        if isinstance(value, ToolCall):
            request = cls._freeze(value.request)
            return value if request is value.request else ToolCall(request)
        if isinstance(value, Wait):
            event_type = cls._freeze(value.event_type)
            return value if event_type is value.event_type else Wait(event_type)
        if isinstance(value, ExternalEvent):
            event_type = cls._freeze(value.event_type)
            data = cls._freeze(value.data)
            if event_type is value.event_type and data is value.data:
                return value
            return ExternalEvent(event_type, data)
        if isinstance(value, ClaimComplete):
            return value
        if isinstance(value, Observation):
            request = cls._freeze(value.request)
            result = cls._freeze(value.result)
            provider_tool_call_id = cls._freeze(value.provider_tool_call_id)
            if (
                request is value.request
                and result is value.result
                and provider_tool_call_id is value.provider_tool_call_id
            ):
                return value
            return Observation(request, result, provider_tool_call_id)
        if isinstance(value, CompletionEvidence):
            values = tuple(
                cls._freeze(item)
                for item in (
                    value.spec_type,
                    value.spec_fingerprint,
                    value.observed_path,
                    value.matched,
                    value.reason,
                )
            )
            if all(
                item is original
                for item, original in zip(
                    values,
                    (
                        value.spec_type,
                        value.spec_fingerprint,
                        value.observed_path,
                        value.matched,
                        value.reason,
                    ),
                )
            ):
                return value
            return CompletionEvidence(*values)
        if isinstance(value, CompletionObservation):
            status = cls._freeze(value.status)
            evidence = cls._freeze(value.evidence)
            if status is value.status and evidence is value.evidence:
                return value
            return CompletionObservation(status, evidence)
        if isinstance(value, NativeModelDecision):
            return NativeModelDecision(
                action=cls._freeze(value.action),
                provider_wire_request=cls._freeze(value.provider_wire_request),
                raw_provider_response=cls._freeze(value.raw_provider_response),
                provider_tool_call_id=cls._freeze(value.provider_tool_call_id),
                failure=cls._freeze(value.failure),
            )
        if isinstance(value, NativeToolContinuation):
            return NativeToolContinuation(
                previous_model_context=cls._freeze(value.previous_model_context),
                raw_provider_response=cls._freeze(value.raw_provider_response),
                provider_tool_call_id=cls._freeze(value.provider_tool_call_id),
            )
        if isinstance(value, ModelRequest):
            context = cls._freeze(value.context)
            available_tools = cls._freeze(value.available_tools)
            source_event_refs = cls._freeze(value.source_event_refs)
            native_tool_continuation = cls._freeze(
                value.native_tool_continuation
            )
            model_visible_context_limit = cls._freeze(
                value.model_visible_context_limit
            )
            if (
                context is value.context
                and available_tools is value.available_tools
                and source_event_refs is value.source_event_refs
                and native_tool_continuation is value.native_tool_continuation
                and model_visible_context_limit is value.model_visible_context_limit
            ):
                return value
            return ModelRequest(
                context,
                available_tools,
                source_event_refs,
                native_tool_continuation,
                model_visible_context_limit,
            )
        if isinstance(value, DecisionFrame):
            return DecisionFrame(
                decision_id=cls._freeze(value.decision_id),
                model_identifier=cls._freeze(value.model_identifier),
                goal=cls._freeze(value.goal),
                state_version=cls._freeze(value.state_version),
                source_event_refs=cls._freeze(value.source_event_refs),
                actual_request=cls._freeze(value.actual_request),
                actual_tools_exposed=cls._freeze(value.actual_tools_exposed),
                raw_model_response=cls._freeze(value.raw_model_response),
                resulting_action=cls._freeze(value.resulting_action),
                provider_wire_request=cls._freeze(value.provider_wire_request),
                raw_provider_response=cls._freeze(value.raw_provider_response),
                provider_tool_call_id=cls._freeze(value.provider_tool_call_id),
            )
        if isinstance(
            value,
            (
                str,
                bytes,
                int,
                float,
                bool,
                type(None),
            ),
        ):
            return value
        return repr(value)


@dataclass(frozen=True)
class ExecutionState:
    version: int
    status: Literal["running", "waiting", "completed", "failed"]
    goal: str
    completion_spec: CompletionSpec | None = None
    execution_id: str | None = None
    root_actor_id: str | None = None
    decision_count: int = 0
    latest_observation: RuntimeObservation | None = None
    last_action: object | None = None
    last_result: ToolResult | None = None
    completion: str | None = None
    failure: str | None = None
    waiting_for: str | None = None
    latest_external_event: ExternalEvent | None = None
    last_provider_tool_call_id: str | None = None


def fold_execution_state(
    events: Iterable[ExecutionEvent],
    *,
    initial_state: ExecutionState | None = None,
    prior_event_ids: Iterable[str] = (),
) -> ExecutionState:
    state = initial_state
    seen_ids = set(prior_event_ids)
    start_sequence = state.version + 1 if state is not None else 1
    for expected_sequence, event in enumerate(events, start=start_sequence):
        if event.sequence != expected_sequence:
            raise ValueError("event sequences must be contiguous and monotonic")
        if event.event_id in seen_ids:
            raise ValueError("event ids must be unique")
        if any(reference not in seen_ids for reference in event.source_event_refs):
            raise ValueError("source event references must point to the immutable past")
        seen_ids.add(event.event_id)

        if event.event_type == "EXECUTION_STARTED":
            if state is not None or not isinstance(event.payload.get("goal"), str):
                raise ValueError("execution must begin once with a goal")
            state = ExecutionState(
                version=event.sequence,
                status="running",
                goal=event.payload["goal"],
                completion_spec=event.payload.get("completion_spec"),
                execution_id=event.payload.get("execution_id"),
                root_actor_id=event.payload.get("root_actor_id"),
            )
            continue
        if state is None:
            raise ValueError("execution must begin with EXECUTION_STARTED")
        if state.status in ("completed", "failed"):
            raise ValueError("terminal execution state cannot accept more events")
        waiting_event_types = ("EXTERNAL_EVENT_RECEIVED", "ROOT_WOKEN")
        if state.status == "waiting" and event.event_type not in waiting_event_types:
            raise ValueError("waiting execution accepts only external wake events")
        if state.status == "running" and event.event_type in waiting_event_types:
            raise ValueError("running execution cannot receive a wait-only event")

        values = {
            "version": event.sequence,
            "status": state.status,
            "goal": state.goal,
            "completion_spec": state.completion_spec,
            "execution_id": state.execution_id,
            "root_actor_id": state.root_actor_id,
            "decision_count": state.decision_count,
            "latest_observation": state.latest_observation,
            "last_action": state.last_action,
            "last_result": state.last_result,
            "completion": state.completion,
            "failure": state.failure,
            "waiting_for": state.waiting_for,
            "latest_external_event": state.latest_external_event,
            "last_provider_tool_call_id": state.last_provider_tool_call_id,
        }
        if event.event_type == "MODEL_DECISION":
            values["decision_count"] = state.decision_count + 1
            values["last_action"] = event.payload.get("action")
            frame = event.payload.get("frame")
            values["last_provider_tool_call_id"] = (
                frame.provider_tool_call_id
                if isinstance(frame, DecisionFrame)
                else None
            )
        elif event.event_type in ("TOOL_RESULT", "TOOL_FAILED"):
            observation = event.payload.get("observation")
            if not isinstance(observation, Observation):
                raise ValueError("tool result events require an observation")
            values["latest_observation"] = observation
            values["last_result"] = observation.result
        elif event.event_type == "ACTION_RECONCILED":
            action = state.last_action
            if (
                event.payload.get("status") != "confirmed_applied"
                or not isinstance(action, ToolCall)
                or not isinstance(action.request, WriteRequest)
            ):
                raise ValueError(
                    "reconciliation requires the interrupted Write action"
                )
            result = ToolResult(
                ok=True,
                output=(
                    "confirmed previously applied write of "
                    f"{len(action.request.content)} characters"
                ),
            )
            values["latest_observation"] = Observation(
                action.request,
                result,
                state.last_provider_tool_call_id,
            )
            values["last_result"] = result
        elif event.event_type == "COMPLETION_REJECTED":
            observation = event.payload.get("observation")
            if not isinstance(observation, CompletionObservation):
                raise ValueError(
                    "completion rejection events require an observation"
                )
            values["latest_observation"] = observation
        elif event.event_type == "ROOT_WAITING":
            condition = event.payload.get("condition")
            if not isinstance(condition, Wait):
                raise ValueError("wait events require a typed condition")
            values["status"] = "waiting"
            values["waiting_for"] = condition.event_type
        elif event.event_type == "EXTERNAL_EVENT_RECEIVED":
            external_event = event.payload.get("event")
            if not isinstance(external_event, ExternalEvent):
                raise ValueError("external event receipt requires a typed event")
            values["latest_external_event"] = external_event
        elif event.event_type == "ROOT_WOKEN":
            external_event = event.payload.get("event")
            if (
                not isinstance(external_event, ExternalEvent)
                or state.waiting_for != external_event.event_type
            ):
                raise ValueError("wake event must match the current wait condition")
            values["status"] = "running"
            values["waiting_for"] = None
            values["latest_external_event"] = external_event
        elif event.event_type == "EXECUTION_COMPLETED":
            if event.payload.get("status") != "verified":
                raise ValueError("completion events require verified status")
            values["status"] = "completed"
            values["completion"] = "verified"
        elif event.event_type == "EXECUTION_FAILED":
            failure = event.payload.get("failure")
            if not isinstance(failure, str):
                raise ValueError("failure events require a reason")
            values["status"] = "failed"
            values["failure"] = failure
        state = ExecutionState(**values)
    if state is None:
        raise ValueError("event log cannot be empty")
    return state


_SERIALIZABLE_TYPES["ExecutionState"] = ExecutionState


def _checkpoint_validation_digest(
    events: tuple[ExecutionEvent, ...], state: ExecutionState
) -> str:
    material = {
        "events": [
            {
                "event_id": event.event_id,
                "sequence": event.sequence,
                "event_type": event.event_type,
                "payload": _encode_value(event.payload),
                "source_event_refs": _encode_value(event.source_event_refs),
            }
            for event in events
        ],
        "state": _encode_value(state),
    }
    encoded = json.dumps(
        material, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Checkpoint:
    schema_version: int
    last_applied_event_sequence: int
    state: ExecutionState
    validation_digest: str

    SCHEMA_VERSION = 1

    @classmethod
    def capture(cls, events: tuple[ExecutionEvent, ...]) -> Checkpoint:
        state = fold_execution_state(events)
        prefix = events[: state.version]
        if len(prefix) != state.version:
            raise ValueError("checkpoint state exceeds the durable event prefix")
        return cls(
            cls.SCHEMA_VERSION,
            state.version,
            state,
            _checkpoint_validation_digest(prefix, state),
        )

    def save(self, path: str | Path) -> None:
        checkpoint_path = Path(path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
        record = {
            "schema_version": self.schema_version,
            "last_applied_event_sequence": self.last_applied_event_sequence,
            "state": _encode_value(self.state),
            "validation_digest": self.validation_digest,
        }
        with temporary_path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                record,
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, checkpoint_path)

    @classmethod
    def load(cls, path: str | Path) -> Checkpoint:
        try:
            with Path(path).open("r", encoding="utf-8") as stream:
                record = json.load(stream)
            if not isinstance(record, dict) or set(record) != {
                "schema_version",
                "last_applied_event_sequence",
                "state",
                "validation_digest",
            }:
                raise ValueError("invalid checkpoint record")
            state = _decode_value(record["state"])
            if (
                type(record["schema_version"]) is not int
                or record["schema_version"] != cls.SCHEMA_VERSION
                or type(record["last_applied_event_sequence"]) is not int
                or not isinstance(state, ExecutionState)
                or not isinstance(record["validation_digest"], str)
                or len(record["validation_digest"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in record["validation_digest"]
                )
            ):
                raise ValueError("invalid checkpoint metadata")
            return cls(
                record["schema_version"],
                record["last_applied_event_sequence"],
                state,
                record["validation_digest"],
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed checkpoint: {exc}") from exc


def restore_execution_state(
    events: tuple[ExecutionEvent, ...], checkpoint_path: str | Path | None
) -> ExecutionState:
    if checkpoint_path is None or not Path(checkpoint_path).exists():
        return fold_execution_state(events)
    checkpoint = Checkpoint.load(checkpoint_path)
    sequence = checkpoint.last_applied_event_sequence
    if (
        sequence < 1
        or sequence > len(events)
        or checkpoint.state.version != sequence
    ):
        raise ValueError("checkpoint sequence is inconsistent with the event log")
    if checkpoint.validation_digest != _checkpoint_validation_digest(
        events[:sequence], checkpoint.state
    ):
        raise ValueError("checkpoint is inconsistent with the durable event prefix")
    restored = fold_execution_state(
        events[sequence:],
        initial_state=checkpoint.state,
        prior_event_ids=(event.event_id for event in events[:sequence]),
    )
    return restored


def _text_projection(value: str, limit: int = 1_024) -> dict[str, object]:
    return {
        "text": value[:limit],
        "truncated": len(value) > limit,
        "original_chars": len(value),
    }


def _request_projection(request: ToolRequest) -> dict[str, object]:
    if isinstance(request, ReadRequest):
        return {"tool": "read", "path": _text_projection(request.path)}
    if isinstance(request, WriteRequest):
        return {
            "tool": "write",
            "path": _text_projection(request.path),
            "content": _text_projection(request.content),
        }
    if isinstance(request, ShellRequest):
        return {
            "tool": "shell",
            "argv": _text_projection(
                json.dumps(request.argv, ensure_ascii=False, separators=(",", ":"))
            ),
            "original_arg_count": len(request.argv),
        }
    return {"tool": type(request).__name__}


def _completion_spec_projection(
    spec: CompletionSpec,
) -> dict[str, object]:
    return {
        "type": "file_content_equals",
        "path": _text_projection(spec.path),
        "expected": _text_projection(spec.expected_content),
    }


def _action_projection(action: object | None) -> dict[str, object] | None:
    if isinstance(action, ToolCall):
        tool = _request_projection(action.request)["tool"]
        return {"type": "tool_call", "tool": tool}
    if isinstance(action, Wait):
        return {"type": "wait", "event_type": _text_projection(action.event_type)}
    if isinstance(action, ClaimComplete):
        return {"type": "claim_complete"}
    if action is None:
        return None
    return {"type": type(action).__name__}


def _observation_projection(
    observation: RuntimeObservation | None,
) -> dict[str, object] | None:
    if observation is None:
        return None
    if isinstance(observation, CompletionObservation):
        evidence = observation.evidence
        return {
            "type": "completion_verification",
            "status": observation.status,
            "evidence": {
                "observed_path": _text_projection(evidence.observed_path),
                "matched": evidence.matched,
                "reason": evidence.reason,
            },
        }
    result = observation.result
    return {
        "request": _request_projection(observation.request),
        "result": {
            "ok": result.ok,
            "output": _text_projection(result.output),
            "error_code": result.error_code,
            "error": (
                _text_projection(result.error) if result.error is not None else None
            ),
            "exit_code": result.exit_code,
            "canonical_truncated": result.truncated,
        },
    }


def _bounded_context(state: ExecutionState, max_chars: int) -> str:
    document: dict[str, object] = {
        "goal": _text_projection(state.goal),
        "completion_spec": (
            _completion_spec_projection(state.completion_spec)
            if state.completion_spec is not None
            else None
        ),
        "state": {
            "version": state.version,
            "status": state.status,
            "execution_id": state.execution_id,
            "root_actor_id": state.root_actor_id,
            "decision_count": state.decision_count,
            "waiting_for": (
                _text_projection(state.waiting_for)
                if state.waiting_for is not None
                else None
            ),
            "completion": (
                _text_projection(state.completion)
                if state.completion is not None
                else None
            ),
            "failure": (
                _text_projection(state.failure) if state.failure is not None else None
            ),
        },
        "observation": _observation_projection(state.latest_observation),
        "incoming_event": (
            {
                "event_type": _text_projection(
                    state.latest_external_event.event_type
                ),
                "data": _text_projection(state.latest_external_event.data),
            }
            if state.latest_external_event is not None
            else None
        ),
    }

    def render() -> str:
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"))

    def text_fields(value: object) -> list[dict[str, object]]:
        fields: list[dict[str, object]] = []
        if isinstance(value, dict):
            if set(("text", "truncated", "original_chars")) <= value.keys():
                fields.append(value)
            else:
                for item in value.values():
                    fields.extend(text_fields(item))
        elif isinstance(value, list):
            for item in value:
                fields.extend(text_fields(item))
        return fields

    context = render()
    fields = text_fields(document)
    while len(context) > max_chars:
        populated = [field for field in fields if field["text"]]
        if not populated:
            raise ValueError("max_context_chars is too small for context metadata")
        field = max(populated, key=lambda item: len(str(item["text"])))
        text = str(field["text"])
        field["text"] = text[: max(0, len(text) - (len(context) - max_chars))]
        field["truncated"] = True
        context = render()
    return context


def _build_model_request(
    state: ExecutionState,
    source_event_refs: tuple[str, ...],
    max_context_chars: int,
    events: tuple[ExecutionEvent, ...] = (),
) -> ModelRequest:
    continuation = None
    for event in reversed(events):
        if event.event_type != "MODEL_DECISION":
            continue
        frame = event.payload.get("frame")
        if (
            isinstance(frame, DecisionFrame)
            and isinstance(frame.provider_tool_call_id, str)
            and frame.provider_tool_call_id
            and frame.raw_provider_response is not None
        ):
            continuation = NativeToolContinuation(
                frame.actual_request.context,
                frame.raw_provider_response,
                frame.provider_tool_call_id,
            )
        break
    return ModelRequest(
        context=_bounded_context(state, max_context_chars),
        available_tools=TOOL_CONTRACTS,
        source_event_refs=source_event_refs,
        native_tool_continuation=continuation,
        model_visible_context_limit=max_context_chars,
    )


@dataclass(frozen=True)
class ExecutionResult:
    status: Literal["waiting", "completed", "failed"]
    output: str | None
    failure: str | None
    steps: tuple[ExecutionStep, ...]
    events: tuple[ExecutionEvent, ...]
    state: ExecutionState
    decision_frames: tuple[DecisionFrame, ...]


class RootAgentProcess:
    def __init__(
        self,
        model: Model,
        tools: ToolHost,
        max_decisions: int,
        *,
        max_context_chars: int = 2_000,
        event_log: EventLog | None = None,
        checkpoint_path: str | Path | None = None,
    ) -> None:
        if max_decisions < 1:
            raise ValueError("max_decisions must be positive")
        if max_context_chars < 768:
            raise ValueError("max_context_chars must be at least 768")
        self._model = model
        self._tools = tools
        self._max_decisions = max_decisions
        self._max_context_chars = max_context_chars
        self._event_log = event_log or EventLog()
        self._checkpoint_path = (
            Path(checkpoint_path) if checkpoint_path is not None else None
        )
        if self._event_log.events:
            self._current_state()
            if self._event_log.events[-1].event_type not in (
                "EXECUTION_STARTED",
                "TOOL_RESULT",
                "TOOL_FAILED",
                "TOOL_CALL_STARTED",
                "ACTION_RECONCILED",
                "COMPLETION_REJECTED",
                "ROOT_WAITING",
                "EXTERNAL_EVENT_RECEIVED",
                "ROOT_WOKEN",
                "EXECUTION_COMPLETED",
                "EXECUTION_FAILED",
            ):
                raise ValueError(
                    "unsettled action recovery is not implemented for this event tail"
                )

    def run(
        self, goal: str, completion_spec: CompletionSpec
    ) -> ExecutionResult:
        if self._event_log.events:
            raise ValueError("execution has already started; use resume")
        self._event_log.append(
            "EXECUTION_STARTED",
            {
                "goal": goal,
                "execution_id": f"execution-{uuid.uuid4().hex}",
                "root_actor_id": f"root-{uuid.uuid4().hex}",
                "completion_spec": completion_spec,
            },
        )
        return self._drive()

    def resume(self) -> ExecutionResult:
        if not self._event_log.events:
            raise ValueError("execution has not started")
        last_event = self._event_log.events[-1]
        if last_event.event_type == "TOOL_CALL_STARTED":
            self._reconcile_interrupted_call(last_event)
        state = self._current_state()
        last_event = self._event_log.events[-1]
        if state.status == "waiting" and last_event.event_type == "EXTERNAL_EVENT_RECEIVED":
            external_event = last_event.payload.get("event")
            if (
                isinstance(external_event, ExternalEvent)
                and state.waiting_for == external_event.event_type
            ):
                self._event_log.append(
                    "ROOT_WOKEN",
                    {"event": external_event},
                    (last_event.event_id,),
                )
                return self._drive()
        if state.status != "running":
            return self._result([])
        return self._drive()

    def _reconcile_interrupted_call(self, call_event: ExecutionEvent) -> None:
        request = call_event.payload.get("request")
        if isinstance(request, ShellRequest):
            raise ValueError("interrupted ShellRequest recovery is unsupported")
        if not isinstance(request, WriteRequest):
            raise ValueError(
                f"interrupted {type(request).__name__} recovery is unsupported"
            )
        evidence = self._tools.inspect_write(request)
        if evidence is None:
            raise ValueError("interrupted write outcome is unresolved")
        self._event_log.append(
            "ACTION_RECONCILED",
            {"status": "confirmed_applied", "evidence": evidence},
            (call_event.event_id,),
        )

    def deliver_event(self, event_type: str, data: str = "") -> ExecutionResult:
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type must be a non-empty string")
        if not isinstance(data, str):
            raise ValueError("event data must be a string")
        if not self._event_log.events:
            raise ValueError("execution has not started")
        state = self._current_state()
        if state.status != "waiting":
            raise ValueError("external events can only be delivered to a waiting Root")
        external_event = ExternalEvent(event_type, data)
        received = self._event_log.append(
            "EXTERNAL_EVENT_RECEIVED",
            {"event": external_event},
            (self._event_log.events[-1].event_id,),
        )
        if state.waiting_for != event_type:
            return self._result([])
        self._event_log.append(
            "ROOT_WOKEN",
            {"event": external_event},
            (received.event_id,),
        )
        return self._drive()

    def _drive(self) -> ExecutionResult:
        steps: list[ExecutionStep] = []
        while True:
            state = self._current_state()
            if state.decision_count >= self._max_decisions:
                self._event_log.append(
                    "EXECUTION_FAILED",
                    {"failure": "decision_limit_reached"},
                    (self._event_log.events[-1].event_id,),
                )
                return self._result(steps)
            decision = state.decision_count + 1
            source_refs = (self._event_log.events[-1].event_id,)
            request = _build_model_request(
                state,
                source_refs,
                self._max_context_chars,
                self._event_log.events,
            )
            raw_response = self._model.decide(request)
            raw_response_snapshot = EventLog._freeze(raw_response)
            action = _structured_action(raw_response)
            action_snapshot = EventLog._freeze(action)
            native_response = (
                raw_response_snapshot
                if isinstance(raw_response_snapshot, NativeModelDecision)
                else None
            )
            frame = DecisionFrame(
                decision_id=f"decision-{decision:06d}",
                model_identifier=self._model.identifier,
                goal=state.goal,
                state_version=state.version,
                source_event_refs=source_refs,
                actual_request=request,
                actual_tools_exposed=request.available_tools,
                raw_model_response=raw_response_snapshot,
                resulting_action=action_snapshot,
                provider_wire_request=(
                    native_response.provider_wire_request
                    if native_response is not None
                    else None
                ),
                raw_provider_response=(
                    native_response.raw_provider_response
                    if native_response is not None
                    else None
                ),
                provider_tool_call_id=(
                    native_response.provider_tool_call_id
                    if native_response is not None
                    else None
                ),
            )
            decision_event = self._event_log.append(
                "MODEL_DECISION",
                {"action": action_snapshot, "frame": frame},
                source_refs,
            )
            if native_response is not None and native_response.failure is not None:
                steps.append(ExecutionStep(decision, raw_response, None))
                self._event_log.append(
                    "EXECUTION_FAILED",
                    {"failure": native_response.failure},
                    (decision_event.event_id,),
                )
                return self._result(steps)
            if isinstance(action, ClaimComplete):
                claim_event = self._event_log.append(
                    "COMPLETION_CLAIMED",
                    {"claim": action},
                    (decision_event.event_id,),
                )
                state = self._current_state()
                if state.completion_spec is None:
                    raise ValueError("execution is missing its completion spec")
                evidence = _verify_completion(
                    state.completion_spec,
                    self._tools.environment,
                )
                if evidence.matched:
                    verified_event = self._event_log.append(
                        "COMPLETION_VERIFIED",
                        {"evidence": evidence},
                        (claim_event.event_id,),
                    )
                    steps.append(ExecutionStep(decision, action, None))
                    self._event_log.append(
                        "EXECUTION_COMPLETED",
                        {"status": "verified"},
                        (verified_event.event_id,),
                    )
                    return self._result(steps)
                observation = CompletionObservation("rejected", evidence)
                self._event_log.append(
                    "COMPLETION_REJECTED",
                    {"observation": observation},
                    (claim_event.event_id,),
                )
                steps.append(ExecutionStep(decision, action, observation))
                continue
            if isinstance(action, Wait):
                steps.append(ExecutionStep(decision, action, None))
                self._event_log.append(
                    "ROOT_WAITING",
                    {"condition": action},
                    (decision_event.event_id,),
                )
                if self._checkpoint_path is not None:
                    Checkpoint.capture(self._event_log.events).save(
                        self._checkpoint_path
                    )
                return self._result(steps)
            if action is None:
                steps.append(ExecutionStep(decision, raw_response, None))
                self._event_log.append(
                    "EXECUTION_FAILED",
                    {"failure": f"unknown_action:{type(raw_response).__name__}"},
                    (decision_event.event_id,),
                )
                return self._result(steps)
            call_event = self._event_log.append(
                "TOOL_CALL_STARTED",
                {"request": action.request},
                (decision_event.event_id,),
            )
            result = self._tools.execute(action.request)
            observation = Observation(
                action.request,
                result,
                (
                    native_response.provider_tool_call_id
                    if native_response is not None
                    else None
                ),
            )
            self._event_log.append(
                "TOOL_RESULT" if result.ok else "TOOL_FAILED",
                {"observation": observation},
                (call_event.event_id,),
            )
            steps.append(ExecutionStep(decision, action, observation))

    def _current_state(self) -> ExecutionState:
        return restore_execution_state(
            self._event_log.events, self._checkpoint_path
        )

    def _result(self, steps: list[ExecutionStep]) -> ExecutionResult:
        events = self._event_log.events
        state = self._current_state()
        return ExecutionResult(
            status=state.status,
            output=state.completion,
            failure=state.failure,
            steps=tuple(steps),
            events=events,
            state=state,
            decision_frames=tuple(
                event.payload["frame"]
                for event in events
                if event.event_type == "MODEL_DECISION"
            ),
        )
