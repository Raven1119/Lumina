from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Protocol, TypeAlias

from Execution_lab2.ipython_control import IPythonResult, PersistentIPython


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
class IPythonCode:
    code: str


@dataclass(frozen=True)
class ClaimComplete:
    pass


@dataclass(frozen=True)
class SpawnChild:
    goal: str
    provider_tool_call_id: str | None = None


@dataclass(frozen=True)
class Return:
    local_result: str


@dataclass(frozen=True)
class ChildRef:
    child_execution_id: str
    child_actor_id: str
    parent_actor_id: str
    local_goal: str
    event_log_path: str | None
    provider_tool_call_id: str | None = None


@dataclass(frozen=True)
class Wait:
    event_type: str


@dataclass(frozen=True)
class ExternalEvent:
    event_type: str
    data: str = ""


Action: TypeAlias = (
    ToolCall | IPythonCode | Wait | ClaimComplete | SpawnChild | Return
)


_MAX_CHILDREN_PER_ROOT = 3


@dataclass(frozen=True)
class NativeModelDecision:
    action: Action | tuple[Action, ...] | None
    provider_wire_request: object
    raw_provider_response: object | None
    provider_tool_call_id: str | tuple[str, ...] | None = None
    failure: str | None = None


@dataclass(frozen=True)
class NativeToolContinuation:
    previous_model_context: str
    raw_provider_response: object
    provider_tool_call_id: str | tuple[str, ...]


def _single_structured_action(value: object) -> Action | None:
    if isinstance(value, ToolCall):
        return value
    if isinstance(value, IPythonCode):
        return value if isinstance(value.code, str) and value.code else None
    if isinstance(value, Wait):
        return value if isinstance(value.event_type, str) and value.event_type else None
    if isinstance(value, ClaimComplete):
        return value
    if isinstance(value, SpawnChild):
        return (
            value
            if isinstance(value.goal, str)
            and 0 < len(value.goal) <= 1_024
            else None
        )
    if isinstance(value, Return):
        return (
            value
            if isinstance(value.local_result, str)
            and len(value.local_result) <= 1_024
            else None
        )
    return None


def _structured_action(
    value: object,
) -> Action | tuple[Action, ...] | None:
    if isinstance(value, NativeModelDecision):
        if value.failure is not None:
            return None
        if isinstance(value.action, tuple):
            actions = tuple(
                _single_structured_action(item) for item in value.action
            )
            if not actions or any(action is None for action in actions):
                return None
            return actions
        return _single_structured_action(value.action)
    return _single_structured_action(value)


def _action_sequence(value: object) -> tuple[Action, ...]:
    if isinstance(value, tuple):
        actions = tuple(_single_structured_action(item) for item in value)
        if actions and all(action is not None for action in actions):
            return actions
        return ()
    action = _structured_action(value)
    if isinstance(action, tuple):
        return action
    return (action,) if action is not None else ()


def _provider_call_ids(
    value: object,
    action_count: int,
) -> tuple[str | None, ...]:
    if isinstance(value, str) and value:
        return (value,) if action_count == 1 else ()
    if (
        isinstance(value, tuple)
        and len(value) == action_count
        and all(isinstance(item, str) and item for item in value)
    ):
        return value
    if value is None:
        return (None,) * action_count
    return ()


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


@dataclass(frozen=True)
class ChildObservation:
    status: Literal["returned", "failed"]
    child_ref: ChildRef
    local_result: str | None = None
    failure: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "returned"

    @property
    def provider_tool_call_id(self) -> str | None:
        return self.child_ref.provider_tool_call_id


@dataclass(frozen=True)
class IPythonObservation:
    result: IPythonResult
    provider_tool_call_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.result.ok


RuntimeObservation: TypeAlias = (
    Observation | IPythonObservation | CompletionObservation | ChildObservation
)


TOOL_CONTRACTS = (
    "read(path: str) -> ToolResult",
    "write(path: str, content: str) -> ToolResult",
    "shell(argv: tuple[str, ...]) -> ToolResult",
    "wait(event_type: str)",
    "claim_complete()",
)

IPYTHON_TOOL_CONTRACTS = (
    "ipython(code: str)",
    "wait(event_type: str)",
    "claim_complete()",
)

ROOT_TOOL_CONTRACTS = (
    *TOOL_CONTRACTS[:-1],
    "spawn_child(goal: str) -> ChildRef",
    TOOL_CONTRACTS[-1],
)

ROOT_IPYTHON_TOOL_CONTRACTS = (
    *IPYTHON_TOOL_CONTRACTS[:-1],
    "spawn_child(goal: str) -> ChildRef",
    IPYTHON_TOOL_CONTRACTS[-1],
)

CHILD_TOOL_CONTRACTS = (
    "read(path: str) -> ToolResult",
    "write(path: str, content: str) -> ToolResult",
    "shell(argv: tuple[str, ...]) -> ToolResult",
    "wait(event_type: str)",
    "return(local_result: str)",
)

CHILD_IPYTHON_TOOL_CONTRACTS = (
    "ipython(code: str)",
    "wait(event_type: str)",
    "return(local_result: str)",
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


def _text_sha256(content: str) -> str:
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
                "intended_content_sha256": _text_sha256(request.content),
                "observed_content_sha256": _text_sha256(observed_content),
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
    "IPYTHON_EXECUTION_STARTED",
    "IPYTHON_EXECUTION_RESULT",
    "IPYTHON_EXECUTION_FAILED",
    "ACTION_RECONCILED",
    "COMPLETION_CLAIMED",
    "COMPLETION_VERIFIED",
    "COMPLETION_REJECTED",
    "ROOT_WAITING",
    "EXTERNAL_EVENT_RECEIVED",
    "ROOT_WOKEN",
    "INTERRUPT_REQUESTED",
    "ACTOR_SUSPENDED",
    "ACTOR_RESUMED",
    "CHILD_SPAWNED",
    "CHILD_RETURNED",
    "CHILD_FAILED",
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


_SIBLING_SETTLED_EVENT_TYPES = (
    "TOOL_RESULT",
    "TOOL_FAILED",
    "IPYTHON_EXECUTION_RESULT",
    "IPYTHON_EXECUTION_FAILED",
    "ACTION_RECONCILED",
)


def _previous_sibling_is_settled(
    events: tuple[ExecutionEvent, ...],
    previous: ExecutionEvent,
    decision_event: ExecutionEvent,
    call_index: int,
) -> bool:
    by_id = {event.event_id: event for event in events}

    def cause(event: ExecutionEvent) -> ExecutionEvent | None:
        if len(event.source_event_refs) != 1:
            return None
        return by_id.get(event.source_event_refs[0])

    while previous.event_type in (
        "ACTOR_RESUMED",
        "ACTOR_SUSPENDED",
        "EXTERNAL_EVENT_RECEIVED",
    ):
        previous = cause(previous)
        if previous is None:
            return False
    if previous.event_type == "INTERRUPT_REQUESTED":
        previous = cause(previous)
        if previous is None:
            return False
    if call_index == 0:
        return previous.event_id == decision_event.event_id
    if previous.event_type not in _SIBLING_SETTLED_EVENT_TYPES:
        return False
    previous_start = cause(previous)
    if (
        previous_start is not None
        and previous_start.event_type == "INTERRUPT_REQUESTED"
    ):
        previous_start = cause(previous_start)
    return (
        previous_start is not None
        and previous_start.source_event_refs == (decision_event.event_id,)
    )


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
    resulting_action: Action | tuple[Action, ...] | None
    provider_wire_request: object | None = None
    raw_provider_response: object | None = None
    provider_tool_call_id: str | tuple[str, ...] | None = None


_SERIALIZABLE_TYPES = {
    value.__name__: value
    for value in (
        ReadRequest,
        WriteRequest,
        ShellRequest,
        FileContentEquals,
        ToolResult,
        ToolCall,
        IPythonCode,
        IPythonResult,
        Wait,
        ExternalEvent,
        ClaimComplete,
        SpawnChild,
        Return,
        ChildRef,
        ChildObservation,
        Observation,
        IPythonObservation,
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
        self._lock = threading.RLock()
        self._path = Path(path) if path is not None else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not _loading and self._path.exists() and self._path.stat().st_size:
                raise ValueError("durable event log already exists; load it explicitly")

    @property
    def events(self) -> tuple[ExecutionEvent, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def path(self) -> Path | None:
        return self._path

    def append(
        self,
        event_type: EventType,
        payload: Mapping[str, object],
        source_event_refs: tuple[str, ...] = (),
    ) -> ExecutionEvent:
        with self._lock:
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
            "IPYTHON_EXECUTION_STARTED": {"action", "code_sha256"},
            "IPYTHON_EXECUTION_RESULT": {"observation"},
            "IPYTHON_EXECUTION_FAILED": {"observation"},
            "ACTION_RECONCILED": {"status", "evidence"},
            "COMPLETION_CLAIMED": {"claim"},
            "COMPLETION_VERIFIED": {"evidence"},
            "COMPLETION_REJECTED": {"observation"},
            "ROOT_WAITING": {"condition"},
            "EXTERNAL_EVENT_RECEIVED": {"event"},
            "ROOT_WOKEN": {"event"},
            "INTERRUPT_REQUESTED": {"status"},
            "ACTOR_SUSPENDED": {"status"},
            "ACTOR_RESUMED": {"status"},
            "CHILD_SPAWNED": {"child_ref"},
            "CHILD_RETURNED": {
                "child_actor_id",
                "parent_actor_id",
                "local_result",
            },
            "CHILD_FAILED": {
                "child_actor_id",
                "parent_actor_id",
                "failure",
            },
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
            if payload.get("actor_role") == "child":
                start_schema = {
                    "goal",
                    "execution_id",
                    "completion_spec",
                    "actor_role",
                    "actor_id",
                    "parent_actor_id",
                }
            elif self._path is None and set(payload) == {"goal", "completion_spec"}:
                start_schema = {"goal", "completion_spec"}
        elif event_type not in schemas:
            raise ValueError(f"unknown execution event type: {event_type}")
        expected_schema = (
            start_schema if event_type == "EXECUTION_STARTED" else schemas[event_type]
        )
        if (
            event_type == "TOOL_CALL_STARTED"
            and set(payload) == {"request", "provider_tool_call_id"}
        ):
            expected_schema = set(payload)
        if (
            event_type == "IPYTHON_EXECUTION_STARTED"
            and set(payload)
            == {"action", "code_sha256", "provider_tool_call_id"}
        ):
            expected_schema = set(payload)
        if set(payload) != expected_schema:
            raise ValueError(f"invalid payload for {event_type}")
        if self._events and (
            self._events[-1].event_type
            in ("EXECUTION_COMPLETED", "EXECUTION_FAILED")
            or (
                self._events[-1].event_type == "CHILD_RETURNED"
                and self._events[0].payload.get("actor_role") == "child"
            )
        ):
            raise ValueError("terminal execution cannot accept more events")
        if event_type == "EXECUTION_STARTED":
            if payload.get("actor_role") == "child":
                if (
                    self._events
                    or source_event_refs
                    or not isinstance(payload["goal"], str)
                    or not payload["goal"]
                    or len(payload["goal"]) > 1_024
                    or payload["completion_spec"] is not None
                    or any(
                        not isinstance(payload[field], str)
                        or not payload[field]
                        for field in (
                            "execution_id",
                            "actor_id",
                            "parent_actor_id",
                        )
                    )
                    or payload["actor_id"] == payload["parent_actor_id"]
                ):
                    raise ValueError(
                        "Child execution must start once with direct lineage"
                    )
                return
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
        decision_caused_event = event_type in (
            "TOOL_CALL_STARTED",
            "IPYTHON_EXECUTION_STARTED",
            "CHILD_SPAWNED",
        )
        if (
            not self._events
            or (decision_caused_event and len(source_event_refs) != 1)
            or (
                not decision_caused_event
                and source_event_refs != (self._events[-1].event_id,)
            )
        ):
            raise ValueError("event must cite the immediately preceding cause")

        previous = self._events[-1]
        if event_type == "INTERRUPT_REQUESTED":
            state = fold_execution_state(self.events)
            if state.status != "running" or payload["status"] != "requested":
                raise ValueError("only a running Root can be interrupted")
        elif event_type == "ACTOR_SUSPENDED":
            state = fold_execution_state(self.events)
            interrupt_event = previous
            if previous.event_type in (
                "TOOL_RESULT",
                "TOOL_FAILED",
                "IPYTHON_EXECUTION_RESULT",
                "IPYTHON_EXECUTION_FAILED",
                "ACTION_RECONCILED",
            ):
                interrupt_event = next(
                    (
                        event
                        for event in self._events
                        if previous.source_event_refs == (event.event_id,)
                    ),
                    None,
                )
            if (
                state.status != "running"
                or interrupt_event is None
                or interrupt_event.event_type != "INTERRUPT_REQUESTED"
                or payload["status"] != "suspended"
            ):
                raise ValueError("suspension requires an interrupt request")
        elif event_type == "ACTOR_RESUMED":
            state = fold_execution_state(self.events)
            if state.status != "suspended" or payload["status"] != "running":
                raise ValueError("resume requires a suspended Root")
        elif event_type == "CHILD_SPAWNED":
            decision_event = next(
                (
                    event
                    for event in self._events
                    if event.event_id == source_event_refs[0]
                ),
                None,
            )
            frame = (
                decision_event.payload.get("frame")
                if decision_event is not None
                and decision_event.event_type == "MODEL_DECISION"
                else None
            )
            child_ref = payload["child_ref"]
            actions = (
                _action_sequence(frame.resulting_action)
                if isinstance(frame, DecisionFrame)
                else ()
            )
            spawn_index = sum(
                event.event_type == "CHILD_SPAWNED"
                and event.source_event_refs == source_event_refs
                for event in self._events
            )
            expected_action = (
                actions[spawn_index] if spawn_index < len(actions) else None
            )
            call_ids = (
                _provider_call_ids(frame.provider_tool_call_id, len(actions))
                if isinstance(frame, DecisionFrame)
                else ()
            )
            expected_call_id = (
                call_ids[spawn_index]
                if spawn_index < len(call_ids)
                else None
            )
            previous_is_valid = (
                previous.event_id == decision_event.event_id
                if spawn_index == 0 and decision_event is not None
                else (
                    previous.event_type == "CHILD_SPAWNED"
                    and previous.source_event_refs == source_event_refs
                )
            )
            prior_child_refs = tuple(
                event.payload.get("child_ref")
                for event in self._events
                if event.event_type == "CHILD_SPAWNED"
            )
            if (
                not previous_is_valid
                or not isinstance(frame, DecisionFrame)
                or not actions
                or any(not isinstance(action, SpawnChild) for action in actions)
                or not isinstance(expected_action, SpawnChild)
                or not isinstance(child_ref, ChildRef)
                or child_ref.local_goal != expected_action.goal
                or expected_action.provider_tool_call_id != expected_call_id
                or child_ref.provider_tool_call_id != expected_call_id
                or child_ref.parent_actor_id
                != self._events[0].payload.get("root_actor_id")
                or child_ref.child_actor_id == child_ref.parent_actor_id
                or not child_ref.child_execution_id
                or not child_ref.child_actor_id
                or not isinstance(child_ref.event_log_path, str)
                or not child_ref.event_log_path
                or len(prior_child_refs) >= _MAX_CHILDREN_PER_ROOT
                or any(
                    isinstance(prior, ChildRef)
                    and (
                        prior.child_actor_id == child_ref.child_actor_id
                        or prior.child_execution_id
                        == child_ref.child_execution_id
                        or prior.event_log_path == child_ref.event_log_path
                    )
                    for prior in prior_child_refs
                )
            ):
                raise ValueError(
                    "Child spawn must match one Root decision and direct lineage"
                )
        elif event_type == "CHILD_RETURNED":
            frame = previous.payload.get("frame")
            state = fold_execution_state(self.events)
            if state.actor_role == "child":
                if (
                    previous.event_type != "MODEL_DECISION"
                    or not isinstance(frame, DecisionFrame)
                    or not isinstance(frame.resulting_action, Return)
                    or payload["local_result"]
                    != frame.resulting_action.local_result
                    or payload["child_actor_id"] != state.actor_id
                    or payload["parent_actor_id"] != state.parent_actor_id
                ):
                    raise ValueError(
                        "Child return must match its own decision and lineage"
                    )
            else:
                child_ref = next(
                    (
                        child
                        for child in state.pending_child_refs
                        if child.child_actor_id
                        == payload["child_actor_id"]
                    ),
                    None,
                )
                if (
                    state.status not in ("child_pending", "waiting")
                    or (
                        state.status == "waiting"
                        and state.waiting_for != "CHILD_RESULT"
                    )
                    or not isinstance(child_ref, ChildRef)
                    or payload["child_actor_id"]
                    != child_ref.child_actor_id
                    or payload["parent_actor_id"]
                    != child_ref.parent_actor_id
                    or not isinstance(payload["local_result"], str)
                    or len(payload["local_result"]) > 1_024
                ):
                    raise ValueError(
                        "Root Child return must match its pending handle"
                    )
        elif event_type == "CHILD_FAILED":
            state = fold_execution_state(self.events)
            child_ref = next(
                (
                    child
                    for child in state.pending_child_refs
                    if child.child_actor_id == payload["child_actor_id"]
                ),
                None,
            )
            if (
                state.actor_role != "root"
                or state.status not in ("child_pending", "waiting")
                or (
                    state.status == "waiting"
                    and state.waiting_for != "CHILD_RESULT"
                )
                or not isinstance(child_ref, ChildRef)
                or payload["child_actor_id"]
                != child_ref.child_actor_id
                or payload["parent_actor_id"]
                != child_ref.parent_actor_id
                or not isinstance(payload["failure"], str)
                or not payload["failure"]
                or len(payload["failure"]) > 1_024
            ):
                raise ValueError(
                    "Root Child failure must match its pending handle"
                )
        elif event_type == "MODEL_DECISION":
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
                    "IPYTHON_EXECUTION_RESULT",
                    "IPYTHON_EXECUTION_FAILED",
                    "ACTION_RECONCILED",
                    "COMPLETION_REJECTED",
                    "ROOT_WOKEN",
                    "ACTOR_RESUMED",
                    "CHILD_SPAWNED",
                    "CHILD_RETURNED",
                    "CHILD_FAILED",
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
                state.status not in ("waiting", "suspended")
                or not isinstance(external_event, ExternalEvent)
                or not isinstance(external_event.event_type, str)
                or not external_event.event_type
                or not isinstance(external_event.data, str)
            ):
                raise ValueError("external events require a waiting or suspended Root")
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
            decision_event = next(
                (
                    event
                    for event in self._events
                    if event.event_id == source_event_refs[0]
                ),
                None,
            )
            frame = (
                decision_event.payload.get("frame")
                if decision_event is not None
                and decision_event.event_type == "MODEL_DECISION"
                else None
            )
            if not isinstance(frame, DecisionFrame):
                raise ValueError("tool call must match its model decision")
            request = payload["request"]
            actions = (
                _action_sequence(frame.resulting_action)
                if isinstance(frame, DecisionFrame)
                else ()
            )
            call_index = sum(
                event.event_type
                in ("TOOL_CALL_STARTED", "IPYTHON_EXECUTION_STARTED")
                and event.source_event_refs == source_event_refs
                for event in self._events
            )
            if not _previous_sibling_is_settled(
                self.events,
                previous,
                decision_event,
                call_index,
            ):
                raise ValueError(
                    "previous sibling must settle before next sibling starts"
                )
            expected_action = (
                actions[call_index] if call_index < len(actions) else None
            )
            call_ids = (
                _provider_call_ids(frame.provider_tool_call_id, len(actions))
                if isinstance(frame, DecisionFrame)
                else ()
            )
            expected_call_id = (
                call_ids[call_index] if call_index < len(call_ids) else None
            )
            if (
                not isinstance(expected_action, ToolCall)
                or expected_action.request != self._freeze(request)
                or (
                    "provider_tool_call_id" in payload
                    and payload["provider_tool_call_id"] != expected_call_id
                )
            ):
                raise ValueError("tool call must match its model decision")
        elif event_type == "IPYTHON_EXECUTION_STARTED":
            decision_event = next(
                (
                    event
                    for event in self._events
                    if event.event_id == source_event_refs[0]
                ),
                None,
            )
            frame = (
                decision_event.payload.get("frame")
                if decision_event is not None
                and decision_event.event_type == "MODEL_DECISION"
                else None
            )
            if not isinstance(frame, DecisionFrame):
                raise ValueError("IPython execution must match its model decision")
            action = payload["action"]
            actions = (
                _action_sequence(frame.resulting_action)
                if isinstance(frame, DecisionFrame)
                else ()
            )
            call_index = sum(
                event.event_type
                in ("TOOL_CALL_STARTED", "IPYTHON_EXECUTION_STARTED")
                and event.source_event_refs == source_event_refs
                for event in self._events
            )
            if not _previous_sibling_is_settled(
                self.events,
                previous,
                decision_event,
                call_index,
            ):
                raise ValueError(
                    "previous sibling must settle before next sibling starts"
                )
            expected_action = (
                actions[call_index] if call_index < len(actions) else None
            )
            call_ids = (
                _provider_call_ids(frame.provider_tool_call_id, len(actions))
                if isinstance(frame, DecisionFrame)
                else ()
            )
            expected_call_id = (
                call_ids[call_index] if call_index < len(call_ids) else None
            )
            if (
                not isinstance(expected_action, IPythonCode)
                or not isinstance(action, IPythonCode)
                or expected_action != self._freeze(action)
                or payload["code_sha256"] != _text_sha256(action.code)
                or (
                    "provider_tool_call_id" in payload
                    and payload["provider_tool_call_id"] != expected_call_id
                )
            ):
                raise ValueError("IPython execution must match its model decision")
        elif event_type in ("TOOL_RESULT", "TOOL_FAILED"):
            observation = payload["observation"]
            tool_start = previous
            if previous.event_type == "INTERRUPT_REQUESTED":
                tool_start = next(
                    (
                        event
                        for event in self._events
                        if previous.source_event_refs == (event.event_id,)
                    ),
                    None,
                )
            decision_event = next(
                (
                    event
                    for event in self._events
                    if tool_start is not None
                    and tool_start.source_event_refs == (event.event_id,)
                ),
                None,
            )
            decision_frame = (
                decision_event.payload.get("frame")
                if decision_event is not None
                and decision_event.event_type == "MODEL_DECISION"
                else None
            )
            actions = (
                _action_sequence(decision_frame.resulting_action)
                if isinstance(decision_frame, DecisionFrame)
                else ()
            )
            call_ids = (
                _provider_call_ids(
                    decision_frame.provider_tool_call_id,
                    len(actions),
                )
                if isinstance(decision_frame, DecisionFrame)
                else ()
            )
            call_index = (
                sum(
                    event.event_type
                    in ("TOOL_CALL_STARTED", "IPYTHON_EXECUTION_STARTED")
                    and tool_start is not None
                    and event.source_event_refs == tool_start.source_event_refs
                    for event in self._events
                )
                - 1
            )
            expected_provider_tool_call_id = (
                tool_start.payload["provider_tool_call_id"]
                if tool_start is not None
                and "provider_tool_call_id" in tool_start.payload
                else (
                    call_ids[call_index]
                    if 0 <= call_index < len(call_ids)
                    else None
                )
            )
            if (
                tool_start is None
                or tool_start.event_type != "TOOL_CALL_STARTED"
                or not isinstance(observation, Observation)
                or self._freeze(observation.request)
                != tool_start.payload.get("request")
                or observation.result.ok != (event_type == "TOOL_RESULT")
                or observation.provider_tool_call_id
                != expected_provider_tool_call_id
            ):
                raise ValueError("tool result must match its tool call and outcome")
        elif event_type in (
            "IPYTHON_EXECUTION_RESULT",
            "IPYTHON_EXECUTION_FAILED",
        ):
            observation = payload["observation"]
            ipython_start = previous
            if previous.event_type == "INTERRUPT_REQUESTED":
                ipython_start = next(
                    (
                        event
                        for event in self._events
                        if previous.source_event_refs == (event.event_id,)
                    ),
                    None,
                )
            decision_event = next(
                (
                    event
                    for event in self._events
                    if ipython_start is not None
                    and ipython_start.source_event_refs == (event.event_id,)
                ),
                None,
            )
            decision_frame = (
                decision_event.payload.get("frame")
                if decision_event is not None
                and decision_event.event_type == "MODEL_DECISION"
                else None
            )
            actions = (
                _action_sequence(decision_frame.resulting_action)
                if isinstance(decision_frame, DecisionFrame)
                else ()
            )
            call_ids = (
                _provider_call_ids(
                    decision_frame.provider_tool_call_id,
                    len(actions),
                )
                if isinstance(decision_frame, DecisionFrame)
                else ()
            )
            call_index = (
                sum(
                    event.event_type
                    in ("TOOL_CALL_STARTED", "IPYTHON_EXECUTION_STARTED")
                    and ipython_start is not None
                    and event.source_event_refs == ipython_start.source_event_refs
                    for event in self._events
                )
                - 1
            )
            expected_provider_tool_call_id = (
                ipython_start.payload["provider_tool_call_id"]
                if ipython_start is not None
                and "provider_tool_call_id" in ipython_start.payload
                else (
                    call_ids[call_index]
                    if 0 <= call_index < len(call_ids)
                    else None
                )
            )
            if (
                ipython_start is None
                or ipython_start.event_type != "IPYTHON_EXECUTION_STARTED"
                or not isinstance(observation, IPythonObservation)
                or observation.result.ok
                != (event_type == "IPYTHON_EXECUTION_RESULT")
                or observation.provider_tool_call_id
                != expected_provider_tool_call_id
            ):
                raise ValueError("IPython result must match its execution")
        elif event_type == "ACTION_RECONCILED":
            reconciliation_start = previous
            if previous.event_type == "INTERRUPT_REQUESTED":
                reconciliation_start = next(
                    (
                        event
                        for event in self._events
                        if previous.source_event_refs == (event.event_id,)
                    ),
                    previous,
                )
            request = reconciliation_start.payload.get("request")
            evidence = payload["evidence"]
            if (
                reconciliation_start.event_type != "TOOL_CALL_STARTED"
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
                != _text_sha256(request.content)
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
                "IPYTHON_EXECUTION_RESULT",
                "IPYTHON_EXECUTION_FAILED",
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
        if isinstance(value, IPythonCode):
            code = cls._freeze(value.code)
            return value if code is value.code else IPythonCode(code)
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
        if isinstance(value, SpawnChild):
            goal = cls._freeze(value.goal)
            return value if goal is value.goal else SpawnChild(goal)
        if isinstance(value, Return):
            local_result = cls._freeze(value.local_result)
            return (
                value
                if local_result is value.local_result
                else Return(local_result)
            )
        if isinstance(value, ChildRef):
            values = tuple(
                cls._freeze(item)
                for item in (
                    value.child_execution_id,
                    value.child_actor_id,
                    value.parent_actor_id,
                    value.local_goal,
                    value.event_log_path,
                )
            )
            if all(
                item is original
                for item, original in zip(
                    values,
                    (
                        value.child_execution_id,
                        value.child_actor_id,
                        value.parent_actor_id,
                        value.local_goal,
                        value.event_log_path,
                    ),
                )
            ):
                return value
            return ChildRef(*values)
        if isinstance(value, ChildObservation):
            return ChildObservation(
                status=cls._freeze(value.status),
                child_ref=cls._freeze(value.child_ref),
                local_result=cls._freeze(value.local_result),
                failure=cls._freeze(value.failure),
            )
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
        if isinstance(value, IPythonResult):
            return IPythonResult(
                ok=cls._freeze(value.ok),
                output=cls._freeze(value.output),
                error_code=cls._freeze(value.error_code),
                error=cls._freeze(value.error),
                truncated=cls._freeze(value.truncated),
                original_output_chars=cls._freeze(value.original_output_chars),
            )
        if isinstance(value, IPythonObservation):
            return IPythonObservation(
                result=cls._freeze(value.result),
                provider_tool_call_id=cls._freeze(value.provider_tool_call_id),
            )
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
    status: Literal[
        "running",
        "waiting",
        "suspended",
        "child_pending",
        "completed",
        "failed",
    ]
    goal: str
    completion_spec: CompletionSpec | None = None
    execution_id: str | None = None
    root_actor_id: str | None = None
    decision_count: int = 0
    latest_observation: RuntimeObservation | None = None
    last_action: object | None = None
    last_result: ToolResult | IPythonResult | None = None
    completion: str | None = None
    failure: str | None = None
    waiting_for: str | None = None
    latest_external_event: ExternalEvent | None = None
    last_provider_tool_call_id: str | tuple[str, ...] | None = None
    lifecycle_notice: Literal["interrupt_requested", "suspended", "resumed"] | None = None
    child_refs: tuple[ChildRef, ...] = ()
    child_outcomes: tuple[ChildObservation, ...] = ()
    actor_role: Literal["root", "child"] = "root"
    actor_id: str | None = None
    parent_actor_id: str | None = None

    @property
    def child_ref(self) -> ChildRef | None:
        return self.child_refs[-1] if self.child_refs else None

    @property
    def pending_child_refs(self) -> tuple[ChildRef, ...]:
        settled_ids = {
            outcome.child_ref.child_actor_id for outcome in self.child_outcomes
        }
        return tuple(
            child
            for child in self.child_refs
            if child.child_actor_id not in settled_ids
        )


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
                actor_role=(
                    "child"
                    if event.payload.get("actor_role") == "child"
                    else "root"
                ),
                actor_id=(
                    event.payload.get("actor_id")
                    if event.payload.get("actor_role") == "child"
                    else event.payload.get("root_actor_id")
                ),
                parent_actor_id=event.payload.get("parent_actor_id"),
            )
            continue
        if state is None:
            raise ValueError("execution must begin with EXECUTION_STARTED")
        if state.status in ("completed", "failed"):
            raise ValueError("terminal execution state cannot accept more events")
        waiting_event_types = ("EXTERNAL_EVENT_RECEIVED", "ROOT_WOKEN")
        child_wake = (
            state.status == "waiting"
            and state.waiting_for == "CHILD_RESULT"
            and event.event_type in ("CHILD_RETURNED", "CHILD_FAILED")
        )
        if (
            state.status == "waiting"
            and event.event_type not in waiting_event_types
            and not child_wake
        ):
            raise ValueError("waiting execution accepts only external wake events")
        if state.status == "running" and event.event_type in waiting_event_types:
            raise ValueError("running execution cannot receive a wait-only event")
        if state.status == "suspended" and event.event_type not in (
            "EXTERNAL_EVENT_RECEIVED",
            "ACTOR_RESUMED",
        ):
            raise ValueError(
                "suspended execution accepts only durable events or explicit resume"
            )
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
            "lifecycle_notice": state.lifecycle_notice,
            "child_refs": state.child_refs,
            "child_outcomes": state.child_outcomes,
            "actor_role": state.actor_role,
            "actor_id": state.actor_id,
            "parent_actor_id": state.parent_actor_id,
        }
        if event.event_type == "MODEL_DECISION":
            values["decision_count"] = state.decision_count + 1
            values["lifecycle_notice"] = None
            values["last_action"] = event.payload.get("action")
            frame = event.payload.get("frame")
            values["last_provider_tool_call_id"] = (
                frame.provider_tool_call_id
                if isinstance(frame, DecisionFrame)
                else None
            )
        elif event.event_type == "TOOL_CALL_STARTED":
            request = event.payload.get("request")
            if not isinstance(request, (ReadRequest, WriteRequest, ShellRequest)):
                raise ValueError("tool start events require a typed request")
            values["last_action"] = ToolCall(request)
            if "provider_tool_call_id" in event.payload:
                values["last_provider_tool_call_id"] = event.payload[
                    "provider_tool_call_id"
                ]
        elif event.event_type == "IPYTHON_EXECUTION_STARTED":
            action = event.payload.get("action")
            if not isinstance(action, IPythonCode):
                raise ValueError("IPython start events require a typed action")
            values["last_action"] = action
            if "provider_tool_call_id" in event.payload:
                values["last_provider_tool_call_id"] = event.payload[
                    "provider_tool_call_id"
                ]
        elif event.event_type in ("TOOL_RESULT", "TOOL_FAILED"):
            observation = event.payload.get("observation")
            if not isinstance(observation, Observation):
                raise ValueError("tool result events require an observation")
            values["latest_observation"] = observation
            values["last_result"] = observation.result
        elif event.event_type in (
            "IPYTHON_EXECUTION_RESULT",
            "IPYTHON_EXECUTION_FAILED",
        ):
            observation = event.payload.get("observation")
            if not isinstance(observation, IPythonObservation):
                raise ValueError("IPython result events require an observation")
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
        elif event.event_type == "INTERRUPT_REQUESTED":
            values["lifecycle_notice"] = "interrupt_requested"
        elif event.event_type == "ACTOR_SUSPENDED":
            values["status"] = "suspended"
            values["lifecycle_notice"] = "suspended"
        elif event.event_type == "ACTOR_RESUMED":
            values["status"] = "running"
            values["lifecycle_notice"] = "resumed"
        elif event.event_type == "CHILD_SPAWNED":
            child_ref = event.payload.get("child_ref")
            if not isinstance(child_ref, ChildRef):
                raise ValueError("Child spawn events require a typed handle")
            values["status"] = "child_pending"
            values["child_refs"] = (*state.child_refs, child_ref)
        elif event.event_type == "CHILD_RETURNED":
            local_result = event.payload.get("local_result")
            if (
                not isinstance(local_result, str)
                or len(local_result) > 1_024
            ):
                raise ValueError(
                    "Child return requires one bounded local result"
                )
            if state.actor_role == "child":
                values["status"] = "completed"
                values["completion"] = local_result
            else:
                child_ref = next(
                    (
                        child
                        for child in state.pending_child_refs
                        if child.child_actor_id
                        == event.payload.get("child_actor_id")
                    ),
                    None,
                )
                if not isinstance(child_ref, ChildRef):
                    raise ValueError(
                        "Root Child return requires a pending handle"
                    )
                observation = ChildObservation(
                    status="returned",
                    child_ref=child_ref,
                    local_result=local_result,
                )
                values["child_outcomes"] = (*state.child_outcomes, observation)
                values["latest_observation"] = observation
                values["status"] = (
                    "child_pending"
                    if len(state.pending_child_refs) > 1
                    else "running"
                )
                values["waiting_for"] = None
        elif event.event_type == "CHILD_FAILED":
            failure = event.payload.get("failure")
            child_ref = next(
                (
                    child
                    for child in state.pending_child_refs
                    if child.child_actor_id
                    == event.payload.get("child_actor_id")
                ),
                None,
            )
            if (
                state.actor_role != "root"
                or not isinstance(child_ref, ChildRef)
                or not isinstance(failure, str)
                or not failure
                or len(failure) > 1_024
            ):
                raise ValueError(
                    "Root Child failure requires one bounded outcome"
                )
            observation = ChildObservation(
                status="failed",
                child_ref=child_ref,
                failure=failure,
            )
            values["child_outcomes"] = (*state.child_outcomes, observation)
            values["latest_observation"] = observation
            values["status"] = (
                "child_pending"
                if len(state.pending_child_refs) > 1
                else "running"
            )
            values["waiting_for"] = None
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
    if isinstance(action, tuple):
        return {
            "type": "sibling_actions",
            "actions": [_action_projection(item) for item in action],
        }
    if isinstance(action, ToolCall):
        tool = _request_projection(action.request)["tool"]
        return {"type": "tool_call", "tool": tool}
    if isinstance(action, IPythonCode):
        return {"type": "ipython", "code": _text_projection(action.code)}
    if isinstance(action, Wait):
        return {"type": "wait", "event_type": _text_projection(action.event_type)}
    if isinstance(action, ClaimComplete):
        return {"type": "claim_complete"}
    if isinstance(action, SpawnChild):
        return {"type": "spawn_child", "goal": _text_projection(action.goal)}
    if isinstance(action, Return):
        return {
            "type": "return",
            "local_result": _text_projection(action.local_result),
        }
    if action is None:
        return None
    return {"type": type(action).__name__}


def _observation_projection(
    observation: RuntimeObservation | None,
) -> dict[str, object] | None:
    if observation is None:
        return None
    if isinstance(observation, ChildObservation):
        return {
            "type": "child_outcome",
            "status": observation.status,
            "child_actor_id": observation.child_ref.child_actor_id,
            "parent_actor_id": observation.child_ref.parent_actor_id,
            "local_result": (
                _text_projection(observation.local_result)
                if observation.local_result is not None
                else None
            ),
            "failure": (
                _text_projection(observation.failure)
                if observation.failure is not None
                else None
            ),
        }
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
    if isinstance(observation, IPythonObservation):
        result = observation.result
        return {
            "type": "ipython_execution",
            "result": {
                "ok": result.ok,
                "output": _text_projection(result.output),
                "error_code": result.error_code,
                "error": (
                    _text_projection(result.error)
                    if result.error is not None
                    else None
                ),
                "canonical_truncated": result.truncated,
                "original_output_chars": result.original_output_chars,
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


def _sibling_observation_projection(
    observation: Observation | IPythonObservation | ChildObservation,
) -> dict[str, object]:
    if isinstance(observation, ChildObservation):
        projection: dict[str, object] = {
            "ok": observation.ok,
            "status": observation.status,
            "child_actor_id": observation.child_ref.child_actor_id,
            "parent_actor_id": observation.child_ref.parent_actor_id,
        }
        if observation.local_result is not None:
            projection["local_result"] = _text_projection(
                observation.local_result
            )
        if observation.failure is not None:
            projection["failure"] = _text_projection(observation.failure)
        return {"result": projection}
    result = observation.result
    projection: dict[str, object] = {
        "ok": result.ok,
    }
    if result.output:
        projection["output"] = _text_projection(result.output)
    if result.error_code is not None:
        projection["error_code"] = result.error_code
    if result.error is not None:
        projection["error"] = _text_projection(result.error)
    if isinstance(observation, Observation) and result.exit_code is not None:
        projection["exit_code"] = result.exit_code
    if result.truncated:
        projection["canonical_truncated"] = True
    if (
        isinstance(observation, IPythonObservation)
        and result.original_output_chars != len(result.output)
    ):
        projection["original_output_chars"] = result.original_output_chars
    return {"result": projection}


def _bounded_context(
    state: ExecutionState,
    max_chars: int,
    sibling_observations: tuple[RuntimeObservation, ...] = (),
) -> str:
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
        "observation": (
            None
            if sibling_observations
            else _observation_projection(state.latest_observation)
        ),
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
        "lifecycle": state.lifecycle_notice,
    }
    if state.actor_role == "child":
        state_projection = document["state"]
        assert isinstance(state_projection, dict)
        state_projection.update(
            {
                "actor_role": state.actor_role,
                "actor_id": state.actor_id,
                "parent_actor_id": state.parent_actor_id,
            }
        )
    elif state.child_refs:
        outcomes_by_actor = {
            item.child_ref.child_actor_id: item for item in state.child_outcomes
        }
        if len(state.child_refs) == 1:
            child = state.child_refs[0]
            outcome = outcomes_by_actor.get(child.child_actor_id)
            document["children"] = [
                {
                    "child_actor_id": child.child_actor_id,
                    "parent_actor_id": child.parent_actor_id,
                    "local_goal": _text_projection(child.local_goal),
                    "status": outcome.status if outcome is not None else "pending",
                    "local_result": (
                        _text_projection(outcome.local_result)
                        if outcome is not None
                        and outcome.local_result is not None
                        else None
                    ),
                    "failure": (
                        _text_projection(outcome.failure)
                        if outcome is not None
                        and outcome.failure is not None
                        else None
                    ),
                }
            ]
        else:
            if isinstance(state.latest_observation, ChildObservation):
                document["observation"] = None
            elif isinstance(
                state.latest_observation,
                (Observation, IPythonObservation),
            ):
                observation = _sibling_observation_projection(
                    state.latest_observation
                )
                if isinstance(state.latest_observation, IPythonObservation):
                    observation["type"] = "ipython"
                else:
                    observation["request"] = _request_projection(
                        state.latest_observation.request
                    )
                document["observation"] = observation
            elif isinstance(state.latest_observation, CompletionObservation):
                evidence = state.latest_observation.evidence
                document["observation"] = {
                    "type": "completion",
                    "status": state.latest_observation.status,
                    "path": _text_projection(evidence.observed_path),
                    "matched": evidence.matched,
                    "reason": evidence.reason,
                }
            child_facts = []
            for index, child in enumerate(state.child_refs):
                outcome = outcomes_by_actor.get(child.child_actor_id)
                if outcome is None:
                    child_facts.append(f"{index} pending {child.local_goal}")
                elif outcome.status == "returned":
                    detail = outcome.local_result or ""
                    child_facts.append(f"{index} returned {detail}")
                else:
                    detail = outcome.failure or ""
                    child_facts.append(f"{index} failed {detail}")
            document["children"] = {
                "actor_ids": [
                    child.child_actor_id for child in state.child_refs
                ],
                "facts": _text_projection("\n".join(child_facts)),
            }
            if document["observation"] is None:
                document.pop("observation")
            for key in ("incoming_event", "lifecycle"):
                if document[key] is None:
                    document.pop(key)
            state_projection = document["state"]
            assert isinstance(state_projection, dict)
            for key in (
                "version",
                "execution_id",
                "root_actor_id",
                "decision_count",
            ):
                state_projection.pop(key)
            for key in ("waiting_for", "completion", "failure"):
                if state_projection[key] is None:
                    state_projection.pop(key)
    if sibling_observations:
        document.clear()
        document["observations"] = [
            _sibling_observation_projection(observation)
            for observation in sibling_observations
            if isinstance(
                observation,
                (Observation, IPythonObservation, ChildObservation),
            )
        ]
        document["lifecycle"] = state.lifecycle_notice

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


def _sibling_observations_after(
    events: tuple[ExecutionEvent, ...],
    decision_index: int,
) -> tuple[Observation | IPythonObservation | ChildObservation, ...]:
    observations = []
    for event_index in range(decision_index + 1, len(events)):
        event = events[event_index]
        observation = event.payload.get("observation")
        if event.event_type == "ACTION_RECONCILED":
            observation = fold_execution_state(
                events[: event_index + 1]
            ).latest_observation
        elif event.event_type in ("CHILD_RETURNED", "CHILD_FAILED"):
            observation = fold_execution_state(
                events[: event_index + 1]
            ).latest_observation
        if isinstance(
            observation,
            (Observation, IPythonObservation, ChildObservation),
        ):
            observations.append(observation)
    return tuple(observations)


def _has_pending_spawn_batch(
    state: ExecutionState,
    events: tuple[ExecutionEvent, ...],
) -> bool:
    pending_actor_ids = {
        child.child_actor_id for child in state.pending_child_refs
    }
    if not pending_actor_ids:
        return False
    decision_event = next(
        (
            event
            for event in reversed(events)
            if event.event_type == "MODEL_DECISION"
        ),
        None,
    )
    if decision_event is None:
        return False
    frame = decision_event.payload.get("frame")
    actions = (
        _action_sequence(frame.resulting_action)
        if isinstance(frame, DecisionFrame)
        else ()
    )
    if len(actions) < 2 or any(
        not isinstance(action, SpawnChild) for action in actions
    ):
        return False
    return any(
        event.event_type == "CHILD_SPAWNED"
        and event.source_event_refs == (decision_event.event_id,)
        and isinstance(event.payload.get("child_ref"), ChildRef)
        and event.payload["child_ref"].child_actor_id in pending_actor_ids
        for event in events
    )


def _build_model_request(
    state: ExecutionState,
    source_event_refs: tuple[str, ...],
    max_context_chars: int,
    available_tools: tuple[str, ...],
    events: tuple[ExecutionEvent, ...] = (),
) -> ModelRequest:
    continuation = None
    sibling_observations: tuple[RuntimeObservation, ...] = ()
    for event_index in range(len(events) - 1, -1, -1):
        event = events[event_index]
        if event.event_type != "MODEL_DECISION":
            continue
        frame = event.payload.get("frame")
        call_ids = (
            _provider_call_ids(
                frame.provider_tool_call_id,
                len(_action_sequence(frame.resulting_action)),
            )
            if isinstance(frame, DecisionFrame)
            else ()
        )
        if (
            isinstance(frame, DecisionFrame)
            and call_ids
            and all(call_id is not None for call_id in call_ids)
            and frame.raw_provider_response is not None
        ):
            if len(call_ids) > 1:
                observations = _sibling_observations_after(
                    events,
                    event_index,
                )
                if tuple(
                    observation.provider_tool_call_id
                    for observation in observations
                ) != call_ids:
                    raise ValueError(
                        "native sibling results do not match their model order"
                    )
                sibling_observations = observations
            continuation = NativeToolContinuation(
                frame.actual_request.context,
                frame.raw_provider_response,
                frame.provider_tool_call_id,
            )
        break
    return ModelRequest(
        context=_bounded_context(
            state,
            max_context_chars,
            sibling_observations,
        ),
        available_tools=available_tools,
        source_event_refs=source_event_refs,
        native_tool_continuation=continuation,
        model_visible_context_limit=max_context_chars,
    )


@dataclass(frozen=True)
class ExecutionResult:
    status: Literal[
        "waiting",
        "suspended",
        "child_pending",
        "completed",
        "failed",
    ]
    output: str | None
    failure: str | None
    steps: tuple[ExecutionStep, ...]
    events: tuple[ExecutionEvent, ...]
    state: ExecutionState
    decision_frames: tuple[DecisionFrame, ...]

    @property
    def child_ref(self) -> ChildRef | None:
        return self.state.child_ref


class RootAgentProcess:
    _max_children_per_root = 0

    def __init__(
        self,
        model: Model,
        tools: ToolHost,
        max_decisions: int,
        *,
        max_context_chars: int = 2_000,
        event_log: EventLog | None = None,
        checkpoint_path: str | Path | None = None,
        ipython_control: PersistentIPython | None = None,
        _child_ref: ChildRef | None = None,
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
        self._child_ref = _child_ref
        self._actor_role: Literal["root", "child"] = (
            "child" if _child_ref is not None else "root"
        )
        default_contracts = (
            CHILD_TOOL_CONTRACTS
            if self._actor_role == "child"
            else (
                ROOT_TOOL_CONTRACTS
                if self._max_children_per_root
                else TOOL_CONTRACTS
            )
        )
        model_contracts = tuple(
            getattr(model, "tool_contracts", default_contracts)
        )
        allowed_names = (
            {"read", "write", "shell", "ipython", "wait", "return"}
            if self._actor_role == "child"
            else (
                {"read", "write", "shell", "ipython", "wait", "claim_complete"}
                | ({"spawn_child"} if self._max_children_per_root else set())
            )
        )
        self._available_tools = tuple(
            contract
            for contract in model_contracts
            if contract.partition("(")[0] in allowed_names
        )
        self._ipython_control = ipython_control
        self._checkpoint_path = (
            Path(checkpoint_path) if checkpoint_path is not None else None
        )
        self._lifecycle = threading.Condition(threading.RLock())
        self._interrupt_requested = False
        self._active_phase: Literal["model", "tool", "ipython"] | None = None
        if self._event_log.events:
            restored_state = self._current_state()
            self._interrupt_requested = (
                restored_state.lifecycle_notice == "interrupt_requested"
            )
            if self._event_log.events[-1].event_type not in (
                "EXECUTION_STARTED",
                "TOOL_RESULT",
                "TOOL_FAILED",
                "IPYTHON_EXECUTION_STARTED",
                "IPYTHON_EXECUTION_RESULT",
                "IPYTHON_EXECUTION_FAILED",
                "TOOL_CALL_STARTED",
                "ACTION_RECONCILED",
                "COMPLETION_REJECTED",
                "ROOT_WAITING",
                "EXTERNAL_EVENT_RECEIVED",
                "ROOT_WOKEN",
                "INTERRUPT_REQUESTED",
                "ACTOR_SUSPENDED",
                "ACTOR_RESUMED",
                "CHILD_SPAWNED",
                "CHILD_RETURNED",
                "CHILD_FAILED",
                "EXECUTION_COMPLETED",
                "EXECUTION_FAILED",
            ):
                raise ValueError(
                    "unsettled action recovery is not implemented for this event tail"
                )

    @classmethod
    def for_child(
        cls,
        child_ref: ChildRef,
        *,
        model: Model,
        tools: ToolHost,
        max_decisions: int,
        max_context_chars: int = 2_000,
        event_log: EventLog | None = None,
        checkpoint_path: str | Path | None = None,
        ipython_control: PersistentIPython | None = None,
    ) -> RootAgentProcess:
        if not isinstance(child_ref, ChildRef):
            raise TypeError("child_ref must be a ChildRef")
        if event_log is None and child_ref.event_log_path is not None:
            child_path = Path(child_ref.event_log_path)
            event_log = (
                EventLog.load(child_path)
                if child_path.exists() and child_path.stat().st_size
                else EventLog(child_path)
            )
        return cls(
            model=model,
            tools=tools,
            max_decisions=max_decisions,
            max_context_chars=max_context_chars,
            event_log=event_log,
            checkpoint_path=checkpoint_path,
            ipython_control=ipython_control,
            _child_ref=child_ref,
        )

    def run(
        self, goal: str, completion_spec: CompletionSpec
    ) -> ExecutionResult:
        if self._actor_role != "root":
            raise ValueError("Child execution must use run_child")
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

    def run_child(self) -> ExecutionResult:
        if self._actor_role != "child" or self._child_ref is None:
            raise ValueError("only a Child AgentProcess can use run_child")
        if self._event_log.events:
            raise ValueError("Child execution has already started; use resume")
        self._event_log.append(
            "EXECUTION_STARTED",
            {
                "goal": self._child_ref.local_goal,
                "execution_id": self._child_ref.child_execution_id,
                "completion_spec": None,
                "actor_role": "child",
                "actor_id": self._child_ref.child_actor_id,
                "parent_actor_id": self._child_ref.parent_actor_id,
            },
        )
        return self._drive()

    def accept_child(self, child_result: ExecutionResult) -> ExecutionResult:
        if self._actor_role != "root" or not self._max_children_per_root:
            raise ValueError("only Root can accept a Child outcome")
        state = self._current_state()
        child_ref = next(
            (
                child
                for child in state.pending_child_refs
                if child.child_actor_id == child_result.state.actor_id
            ),
            None,
        )
        if (
            state.status not in ("child_pending", "waiting")
            or (
                state.status == "waiting"
                and state.waiting_for != "CHILD_RESULT"
            )
            or not isinstance(child_ref, ChildRef)
        ):
            raise ValueError("Root has no pending Child")
        if not child_result.events:
            raise ValueError("Child result has no canonical history")
        canonical_events = child_result.events
        if child_ref.event_log_path is not None:
            canonical_events = EventLog.load(
                child_ref.event_log_path
            ).events
            if canonical_events != child_result.events:
                raise ValueError("Child result is not its durable EventLog truth")
        canonical_state = fold_execution_state(canonical_events)
        if (
            canonical_state.actor_role != "child"
            or canonical_state.actor_id != child_ref.child_actor_id
            or canonical_state.parent_actor_id != child_ref.parent_actor_id
            or canonical_state.execution_id
            != child_ref.child_execution_id
            or canonical_state.goal != child_ref.local_goal
            or child_result.state != canonical_state
            or child_result.status != canonical_state.status
            or child_result.output != canonical_state.completion
            or child_result.failure != canonical_state.failure
        ):
            raise ValueError(
                "Child result does not match canonical Child history"
            )
        if (
            canonical_state.status == "completed"
            and canonical_events[-1].event_type == "CHILD_RETURNED"
            and isinstance(canonical_state.completion, str)
        ):
            event_type: Literal["CHILD_RETURNED", "CHILD_FAILED"] = (
                "CHILD_RETURNED"
            )
            payload = {
                "child_actor_id": child_ref.child_actor_id,
                "parent_actor_id": child_ref.parent_actor_id,
                "local_result": canonical_state.completion,
            }
        elif (
            canonical_state.status == "failed"
            and canonical_events[-1].event_type == "EXECUTION_FAILED"
            and isinstance(canonical_state.failure, str)
            and canonical_state.failure
        ):
            event_type = "CHILD_FAILED"
            payload = {
                "child_actor_id": child_ref.child_actor_id,
                "parent_actor_id": child_ref.parent_actor_id,
                "failure": canonical_state.failure[:1_024],
            }
        else:
            raise ValueError("Child terminal outcome is not deliverable")
        self._event_log.append(
            event_type,
            payload,
            (self._event_log.events[-1].event_id,),
        )
        state = self._current_state()
        if _has_pending_spawn_batch(state, self._event_log.events):
            return self._result([])
        return self._drive()

    def resume(self) -> ExecutionResult:
        if not self._event_log.events:
            raise ValueError("execution has not started")
        steps: list[ExecutionStep] = []
        state = self._current_state()
        if state.status != "suspended" and self._interrupt_requested:
            return self._settle_recovered_interrupt(steps)
        if state.status == "suspended":
            with self._lifecycle:
                self._event_log.append(
                    "ACTOR_RESUMED",
                    {"status": "running"},
                    (self._event_log.events[-1].event_id,),
                )
                self._interrupt_requested = False
                self._lifecycle.notify_all()
        last_event = self._event_log.events[-1]
        if last_event.event_type == "IPYTHON_EXECUTION_STARTED":
            raise ValueError("interrupted IPython execution recovery is unsupported")
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
        self._resume_spawn_suffix(steps)
        state = self._current_state()
        if state.status not in ("running", "child_pending"):
            return self._result(steps)
        if _has_pending_spawn_batch(state, self._event_log.events):
            return self._result(steps)
        self._resume_sibling_suffix(steps)
        return self._drive(steps)

    def _settle_recovered_interrupt(
        self, steps: list[ExecutionStep]
    ) -> ExecutionResult:
        events = self._event_log.events
        interrupt_event = next(
            event
            for event in reversed(events)
            if event.event_type == "INTERRUPT_REQUESTED"
        )
        if events[-1].event_id == interrupt_event.event_id:
            interrupted_work = next(
                (
                    event
                    for event in events
                    if interrupt_event.source_event_refs == (event.event_id,)
                ),
                None,
            )
            if (
                interrupted_work is not None
                and interrupted_work.event_type == "TOOL_CALL_STARTED"
            ):
                self._reconcile_interrupted_call(interrupted_work)
            elif (
                interrupted_work is not None
                and interrupted_work.event_type == "IPYTHON_EXECUTION_STARTED"
            ):
                raise ValueError(
                    "interrupted IPython execution recovery is unsupported"
                )
        with self._lifecycle:
            self._suspend_locked()
            self._lifecycle.notify_all()
        return self._result(steps)

    def interrupt(self) -> ExecutionResult:
        if self._actor_role != "root":
            raise ValueError("only Root supports explicit interrupt")
        ipython_control = None
        with self._lifecycle:
            state = self._current_state()
            if state.status != "running":
                raise ValueError("only a running Root can be interrupted")
            if self._interrupt_requested:
                raise ValueError("interrupt is already requested")
            self._interrupt_requested = True
            self._event_log.append(
                "INTERRUPT_REQUESTED",
                {"status": "requested"},
                (self._event_log.events[-1].event_id,),
            )
            if self._active_phase is None:
                self._suspend_locked()
            elif self._active_phase == "ipython":
                ipython_control = self._ipython_control
            self._lifecycle.notify_all()
        if ipython_control is not None:
            ipython_control.interrupt()
        with self._lifecycle:
            while self._current_state().status != "suspended":
                self._lifecycle.wait()
            return self._result([])

    def _suspend_locked(self) -> None:
        self._event_log.append(
            "ACTOR_SUSPENDED",
            {"status": "suspended"},
            (self._event_log.events[-1].event_id,),
        )
        if self._checkpoint_path is not None:
            Checkpoint.capture(self._event_log.events).save(
                self._checkpoint_path
            )

    def _suspend_if_requested_locked(self) -> bool:
        if not self._interrupt_requested:
            return False
        if self._current_state().status != "suspended":
            self._suspend_locked()
        self._lifecycle.notify_all()
        return True

    def _settle_phase(self) -> bool:
        with self._lifecycle:
            self._active_phase = None
            if self._suspend_if_requested_locked():
                return True
            self._lifecycle.notify_all()
            return False

    def _append_action_outcome(
        self,
        event_type: Literal[
            "TOOL_RESULT",
            "TOOL_FAILED",
            "IPYTHON_EXECUTION_RESULT",
            "IPYTHON_EXECUTION_FAILED",
        ],
        observation: Observation | IPythonObservation,
        start_event: ExecutionEvent,
    ) -> None:
        cause = self._event_log.events[-1]
        source_refs = (
            (cause.event_id,)
            if cause.event_type == "INTERRUPT_REQUESTED"
            and cause.source_event_refs == (start_event.event_id,)
            else (start_event.event_id,)
        )
        try:
            self._event_log.append(
                event_type,
                {"observation": observation},
                source_refs,
            )
        except ValueError:
            cause = self._event_log.events[-1]
            if (
                source_refs == (start_event.event_id,)
                and cause.event_type == "INTERRUPT_REQUESTED"
                and cause.source_event_refs == (start_event.event_id,)
            ):
                self._event_log.append(
                    event_type,
                    {"observation": observation},
                    (cause.event_id,),
                )
                return
            raise

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
        cause = self._event_log.events[-1]
        source_refs = (
            (cause.event_id,)
            if cause.event_type == "INTERRUPT_REQUESTED"
            and cause.source_event_refs == (call_event.event_id,)
            else (call_event.event_id,)
        )
        self._event_log.append(
            "ACTION_RECONCILED",
            {"status": "confirmed_applied", "evidence": evidence},
            source_refs,
        )

    def deliver_event(self, event_type: str, data: str = "") -> ExecutionResult:
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type must be a non-empty string")
        if not isinstance(data, str):
            raise ValueError("event data must be a string")
        if not self._event_log.events:
            raise ValueError("execution has not started")
        state = self._current_state()
        if state.status not in ("waiting", "suspended"):
            raise ValueError(
                "external events can only be delivered to a waiting or suspended Root"
            )
        external_event = ExternalEvent(event_type, data)
        received = self._event_log.append(
            "EXTERNAL_EVENT_RECEIVED",
            {"event": external_event},
            (self._event_log.events[-1].event_id,),
        )
        if state.status == "suspended":
            return self._result([])
        if state.waiting_for != event_type:
            return self._result([])
        self._event_log.append(
            "ROOT_WOKEN",
            {"event": external_event},
            (received.event_id,),
        )
        return self._drive()

    def _resume_spawn_suffix(self, steps: list[ExecutionStep]) -> None:
        events = self._event_log.events
        decision_event = next(
            (
                event
                for event in reversed(events)
                if event.event_type == "MODEL_DECISION"
            ),
            None,
        )
        frame = (
            decision_event.payload.get("frame")
            if decision_event is not None
            else None
        )
        actions = (
            _action_sequence(frame.resulting_action)
            if isinstance(frame, DecisionFrame)
            else ()
        )
        if len(actions) < 2 or any(
            not isinstance(action, SpawnChild) for action in actions
        ):
            return
        committed = tuple(
            event
            for event in events
            if event.event_type == "CHILD_SPAWNED"
            and event.source_event_refs == (decision_event.event_id,)
        )
        if len(committed) >= len(actions):
            return
        call_ids = _provider_call_ids(
            frame.provider_tool_call_id,
            len(actions),
        )
        state = self._current_state()
        non_null_call_ids = tuple(
            call_id for call_id in call_ids if call_id is not None
        )
        if (
            self._actor_role != "root"
            or self._event_log.path is None
            or len(call_ids) != len(actions)
            or len(non_null_call_ids) != len(call_ids)
            or len(non_null_call_ids) != len(set(non_null_call_ids))
            or len(state.child_refs) + len(actions) - len(committed)
            > self._max_children_per_root
            or any(
                action.provider_tool_call_id != call_id
                for action, call_id in zip(actions, call_ids, strict=True)
            )
        ):
            raise ValueError("partial Spawn batch recovery is invalid")
        for action, provider_call_id in zip(
            actions[len(committed) :],
            call_ids[len(committed) :],
            strict=True,
        ):
            child_actor_id = f"child-{uuid.uuid4().hex}"
            child_log_path = self._event_log.path.with_name(
                f"{self._event_log.path.stem}.{child_actor_id}.jsonl"
            )
            child_ref = ChildRef(
                child_execution_id=f"execution-{uuid.uuid4().hex}",
                child_actor_id=child_actor_id,
                parent_actor_id=state.actor_id or "",
                local_goal=action.goal,
                event_log_path=str(child_log_path),
                provider_tool_call_id=provider_call_id,
            )
            steps.append(
                ExecutionStep(state.decision_count, action, None)
            )
            self._event_log.append(
                "CHILD_SPAWNED",
                {"child_ref": child_ref},
                (decision_event.event_id,),
            )

    def _resume_sibling_suffix(self, steps: list[ExecutionStep]) -> None:
        events = self._event_log.events
        decision_event = next(
            (
                event
                for event in reversed(events)
                if event.event_type == "MODEL_DECISION"
            ),
            None,
        )
        frame = (
            decision_event.payload.get("frame")
            if decision_event is not None
            else None
        )
        actions = (
            _action_sequence(frame.resulting_action)
            if isinstance(frame, DecisionFrame)
            else ()
        )
        if not actions or any(
            not isinstance(action, (ToolCall, IPythonCode))
            for action in actions
        ):
            return
        starts = tuple(
            event
            for event in events
            if event.event_type
            in ("TOOL_CALL_STARTED", "IPYTHON_EXECUTION_STARTED")
            and event.source_event_refs == (decision_event.event_id,)
        )
        if len(starts) >= len(actions):
            return
        if starts and any(isinstance(action, IPythonCode) for action in actions):
            raise ValueError(
                "partial IPython sibling recovery is unsupported"
            )
        call_ids = _provider_call_ids(
            frame.provider_tool_call_id,
            len(actions),
        )
        if len(call_ids) != len(actions):
            raise ValueError("sibling call identity is incomplete")
        self._execute_actions(
            self._current_state().decision_count,
            decision_event,
            actions[len(starts) :],
            call_ids[len(starts) :],
            steps,
        )

    def _execute_actions(
        self,
        decision: int,
        decision_event: ExecutionEvent,
        actions: tuple[Action, ...],
        provider_call_ids: tuple[str | None, ...],
        steps: list[ExecutionStep],
    ) -> bool:
        for action, provider_call_id in zip(
            actions,
            provider_call_ids,
            strict=True,
        ):
            if isinstance(action, IPythonCode):
                with self._lifecycle:
                    if self._interrupt_requested:
                        if self._current_state().status != "suspended":
                            self._suspend_locked()
                        self._lifecycle.notify_all()
                        return True
                    execution_event = self._event_log.append(
                        "IPYTHON_EXECUTION_STARTED",
                        {
                            "action": action,
                            "code_sha256": _text_sha256(action.code),
                            "provider_tool_call_id": provider_call_id,
                        },
                        (decision_event.event_id,),
                    )
                    if self._ipython_control is None:
                        self._ipython_control = PersistentIPython(
                            self._tools.environment.workspace
                        )
                    self._active_phase = "ipython"
                ipython_result = self._ipython_control.execute(action.code)
                observation = IPythonObservation(
                    ipython_result,
                    provider_call_id,
                )
                self._append_action_outcome(
                    (
                        "IPYTHON_EXECUTION_RESULT"
                        if ipython_result.ok
                        else "IPYTHON_EXECUTION_FAILED"
                    ),
                    observation,
                    execution_event,
                )
                steps.append(ExecutionStep(decision, action, observation))
                if self._settle_phase():
                    return True
                continue
            if not isinstance(action, ToolCall):
                raise ValueError(
                    "sibling batches may contain only ordinary actions"
                )
            with self._lifecycle:
                if self._interrupt_requested:
                    if self._current_state().status != "suspended":
                        self._suspend_locked()
                    self._lifecycle.notify_all()
                    return True
                call_event = self._event_log.append(
                    "TOOL_CALL_STARTED",
                    {
                        "request": action.request,
                        "provider_tool_call_id": provider_call_id,
                    },
                    (decision_event.event_id,),
                )
                self._active_phase = "tool"
            result = self._tools.execute(action.request)
            observation = Observation(
                action.request,
                result,
                provider_call_id,
            )
            self._append_action_outcome(
                "TOOL_RESULT" if result.ok else "TOOL_FAILED",
                observation,
                call_event,
            )
            steps.append(ExecutionStep(decision, action, observation))
            if self._settle_phase():
                return True
        return False

    def _drive(
        self,
        steps: list[ExecutionStep] | None = None,
    ) -> ExecutionResult:
        if steps is None:
            steps = []
        while True:
            state = self._current_state()
            if state.status == "suspended":
                return self._result(steps)
            if state.decision_count >= self._max_decisions:
                with self._lifecycle:
                    if self._suspend_if_requested_locked():
                        return self._result(steps)
                    self._event_log.append(
                        "EXECUTION_FAILED",
                        {"failure": "decision_limit_reached"},
                        (self._event_log.events[-1].event_id,),
                    )
                return self._finish(steps)
            decision = state.decision_count + 1
            source_refs = (self._event_log.events[-1].event_id,)
            request = _build_model_request(
                state,
                source_refs,
                self._max_context_chars,
                self._available_tools,
                self._event_log.events,
            )
            with self._lifecycle:
                if self._suspend_if_requested_locked():
                    return self._result(steps)
                self._active_phase = "model"
            raw_response = self._model.decide(request)
            raw_response_snapshot = EventLog._freeze(raw_response)
            structured_action = _structured_action(raw_response)
            action_snapshot = EventLog._freeze(structured_action)
            actions = _action_sequence(action_snapshot)
            native_response = (
                raw_response_snapshot
                if isinstance(raw_response_snapshot, NativeModelDecision)
                else None
            )
            provider_call_ids = (
                _provider_call_ids(
                    native_response.provider_tool_call_id,
                    len(actions),
                )
                if native_response is not None
                else (None,) * len(actions)
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
            with self._lifecycle:
                if self._suspend_if_requested_locked():
                    self._active_phase = None
                    return self._result(steps)
                decision_event = self._event_log.append(
                    "MODEL_DECISION",
                    {"action": action_snapshot, "frame": frame},
                    source_refs,
                )
                self._active_phase = None
                self._lifecycle.notify_all()
            if native_response is not None and native_response.failure is not None:
                with self._lifecycle:
                    if self._suspend_if_requested_locked():
                        return self._result(steps)
                    steps.append(ExecutionStep(decision, raw_response, None))
                    self._event_log.append(
                        "EXECUTION_FAILED",
                        {"failure": native_response.failure},
                        (decision_event.event_id,),
                    )
                return self._finish(steps)
            control_actions = (Wait, ClaimComplete, SpawnChild, Return)
            if (
                len(actions) > 1
                and any(isinstance(item, control_actions) for item in actions)
                and not all(isinstance(item, SpawnChild) for item in actions)
            ):
                steps.append(ExecutionStep(decision, action_snapshot, None))
                self._event_log.append(
                    "EXECUTION_FAILED",
                    {"failure": "model_protocol:mixed_control_tool_calls"},
                    (decision_event.event_id,),
                )
                return self._finish(steps)
            action = actions[0] if len(actions) == 1 else None
            if isinstance(action, Return):
                if self._actor_role != "child":
                    steps.append(ExecutionStep(decision, action, None))
                    self._event_log.append(
                        "EXECUTION_FAILED",
                        {"failure": "unauthorized_action:Return"},
                        (decision_event.event_id,),
                    )
                    return self._finish(steps)
                steps.append(ExecutionStep(decision, action, None))
                self._event_log.append(
                    "CHILD_RETURNED",
                    {
                        "child_actor_id": state.actor_id or "",
                        "parent_actor_id": state.parent_actor_id or "",
                        "local_result": action.local_result,
                    },
                    (decision_event.event_id,),
                )
                return self._finish(steps)
            spawn_actions = (
                actions
                if actions
                and all(isinstance(item, SpawnChild) for item in actions)
                else ()
            )
            if spawn_actions:
                if self._actor_role != "root" or not self._max_children_per_root:
                    steps.append(ExecutionStep(decision, action_snapshot, None))
                    self._event_log.append(
                        "EXECUTION_FAILED",
                        {"failure": "unauthorized_action:SpawnChild"},
                        (decision_event.event_id,),
                    )
                    return self._finish(steps)
                if (
                    len(state.child_refs) + len(spawn_actions)
                    > self._max_children_per_root
                ):
                    steps.append(ExecutionStep(decision, action_snapshot, None))
                    self._event_log.append(
                        "EXECUTION_FAILED",
                        {"failure": "child_limit_reached"},
                        (decision_event.event_id,),
                    )
                    return self._finish(steps)
                if self._event_log.path is None:
                    steps.append(ExecutionStep(decision, action_snapshot, None))
                    self._event_log.append(
                        "EXECUTION_FAILED",
                        {"failure": "child_persistence_required"},
                        (decision_event.event_id,),
                    )
                    return self._finish(steps)
                non_null_call_ids = tuple(
                    call_id
                    for call_id in provider_call_ids
                    if call_id is not None
                )
                if (
                    len(provider_call_ids) != len(spawn_actions)
                    or (
                        len(spawn_actions) > 1
                        and (
                            len(non_null_call_ids)
                            != len(provider_call_ids)
                            or len(non_null_call_ids)
                            != len(set(non_null_call_ids))
                        )
                    )
                    or any(
                        spawn.provider_tool_call_id != call_id
                        for spawn, call_id in zip(
                            spawn_actions,
                            provider_call_ids,
                            strict=True,
                        )
                    )
                ):
                    steps.append(ExecutionStep(decision, action_snapshot, None))
                    self._event_log.append(
                        "EXECUTION_FAILED",
                        {"failure": "model_protocol:invalid_spawn_batch"},
                        (decision_event.event_id,),
                    )
                    return self._finish(steps)
                with self._lifecycle:
                    if self._suspend_if_requested_locked():
                        return self._result(steps)
                    for spawn, provider_call_id in zip(
                        spawn_actions,
                        provider_call_ids,
                        strict=True,
                    ):
                        child_actor_id = f"child-{uuid.uuid4().hex}"
                        child_log_path = (
                            self._event_log.path.with_name(
                                f"{self._event_log.path.stem}.{child_actor_id}.jsonl"
                            )
                            if self._event_log.path is not None
                            else None
                        )
                        child_ref = ChildRef(
                            child_execution_id=f"execution-{uuid.uuid4().hex}",
                            child_actor_id=child_actor_id,
                            parent_actor_id=state.actor_id or "",
                            local_goal=spawn.goal,
                            event_log_path=(
                                str(child_log_path)
                                if child_log_path is not None
                                else None
                            ),
                            provider_tool_call_id=provider_call_id,
                        )
                        steps.append(ExecutionStep(decision, spawn, None))
                        self._event_log.append(
                            "CHILD_SPAWNED",
                            {"child_ref": child_ref},
                            (decision_event.event_id,),
                        )
                return self._result(steps)
            if isinstance(action, ClaimComplete):
                if self._actor_role != "root":
                    steps.append(ExecutionStep(decision, action, None))
                    self._event_log.append(
                        "EXECUTION_FAILED",
                        {"failure": "unauthorized_action:ClaimComplete"},
                        (decision_event.event_id,),
                    )
                    return self._finish(steps)
                with self._lifecycle:
                    if self._suspend_if_requested_locked():
                        return self._result(steps)
                    claim_event = self._event_log.append(
                        "COMPLETION_CLAIMED",
                        {"claim": action},
                        (decision_event.event_id,),
                    )
                    state = self._current_state()
                    if state.completion_spec is None:
                        raise ValueError(
                            "execution is missing its completion spec"
                        )
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
                        return self._finish(steps)
                    observation = CompletionObservation("rejected", evidence)
                    self._event_log.append(
                        "COMPLETION_REJECTED",
                        {"observation": observation},
                        (claim_event.event_id,),
                    )
                    steps.append(ExecutionStep(decision, action, observation))
                continue
            if isinstance(action, Wait):
                with self._lifecycle:
                    if self._suspend_if_requested_locked():
                        return self._result(steps)
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
            if not actions or len(provider_call_ids) != len(actions):
                with self._lifecycle:
                    if self._suspend_if_requested_locked():
                        return self._result(steps)
                    steps.append(ExecutionStep(decision, raw_response, None))
                    self._event_log.append(
                        "EXECUTION_FAILED",
                        {
                            "failure": (
                                f"unknown_action:{type(raw_response).__name__}"
                            )
                        },
                        (decision_event.event_id,),
                    )
                return self._finish(steps)
            suspended = self._execute_actions(
                decision,
                decision_event,
                actions,
                provider_call_ids,
                steps,
            )
            if suspended:
                return self._result(steps)

    def close(self) -> None:
        if self._ipython_control is not None:
            self._ipython_control.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _finish(self, steps: list[ExecutionStep]) -> ExecutionResult:
        self.close()
        return self._result(steps)

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


class AgentProcess(RootAgentProcess):
    _max_children_per_root = _MAX_CHILDREN_PER_ROOT
