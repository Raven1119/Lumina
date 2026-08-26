from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
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
class Complete:
    output: str


Action: TypeAlias = ToolCall | Complete


@dataclass(frozen=True)
class Observation:
    request: ToolRequest
    result: ToolResult

    @property
    def ok(self) -> bool:
        return self.result.ok


TOOL_CONTRACTS = (
    "read(path: str) -> ToolResult",
    "write(path: str, content: str) -> ToolResult",
    "shell(argv: tuple[str, ...]) -> ToolResult",
)


@dataclass(frozen=True)
class ModelRequest:
    context: str
    available_tools: tuple[str, ...]
    source_event_refs: tuple[str, ...]

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

    def _bounded(self, text: str) -> tuple[str, bool]:
        truncated = len(text) > self._max_output_chars
        return text[: self._max_output_chars], truncated


@dataclass(frozen=True)
class ExecutionStep:
    decision: int
    action: Action
    observation: Observation | None


EventType: TypeAlias = Literal[
    "EXECUTION_STARTED",
    "MODEL_DECISION",
    "TOOL_CALL_STARTED",
    "TOOL_RESULT",
    "TOOL_FAILED",
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


class EventLog:
    def __init__(self) -> None:
        self._events: list[ExecutionEvent] = []

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
        self._events.append(event)
        return event

    def _validate_append(
        self,
        event_type: EventType,
        payload: Mapping[str, object],
        source_event_refs: tuple[str, ...],
    ) -> None:
        schemas = {
            "EXECUTION_STARTED": {"goal"},
            "MODEL_DECISION": {"action", "frame"},
            "TOOL_CALL_STARTED": {"request"},
            "TOOL_RESULT": {"observation"},
            "TOOL_FAILED": {"observation"},
            "EXECUTION_COMPLETED": {"output"},
            "EXECUTION_FAILED": {"failure"},
        }
        if event_type not in schemas:
            raise ValueError(f"unknown execution event type: {event_type}")
        if set(payload) != schemas[event_type]:
            raise ValueError(f"invalid payload for {event_type}")
        if self._events and self._events[-1].event_type in (
            "EXECUTION_COMPLETED",
            "EXECUTION_FAILED",
        ):
            raise ValueError("terminal execution cannot accept more events")
        if event_type == "EXECUTION_STARTED":
            if self._events or source_event_refs or not isinstance(payload["goal"], str):
                raise ValueError("execution must start once with an uncaused goal")
            return
        if not self._events or source_event_refs != (self._events[-1].event_id,):
            raise ValueError("event must cite the immediately preceding cause")

        previous = self._events[-1]
        if event_type == "MODEL_DECISION":
            frame = payload["frame"]
            raw_action = None
            if isinstance(frame, DecisionFrame) and isinstance(
                frame.raw_model_response, (ToolCall, Complete)
            ):
                raw_action = frame.raw_model_response
            if (
                previous.event_type
                not in ("EXECUTION_STARTED", "TOOL_RESULT", "TOOL_FAILED")
                or not isinstance(frame, DecisionFrame)
                or payload["action"] != frame.resulting_action
                or raw_action != frame.resulting_action
                or frame.state_version != previous.sequence
                or frame.source_event_refs != source_event_refs
                or frame.actual_request.source_event_refs != source_event_refs
                or frame.actual_tools_exposed != frame.actual_request.available_tools
                or frame.goal != self._events[0].payload["goal"]
            ):
                raise ValueError("model decision requires current execution state")
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
            if (
                previous.event_type != "TOOL_CALL_STARTED"
                or not isinstance(observation, Observation)
                or self._freeze(observation.request)
                != previous.payload.get("request")
                or observation.result.ok != (event_type == "TOOL_RESULT")
            ):
                raise ValueError("tool result must match its tool call and outcome")
        elif event_type == "EXECUTION_COMPLETED":
            frame = previous.payload.get("frame")
            if (
                previous.event_type != "MODEL_DECISION"
                or not isinstance(frame, DecisionFrame)
                or not isinstance(frame.resulting_action, Complete)
                or frame.resulting_action.output != payload["output"]
            ):
                raise ValueError("completion must match its model decision")
        elif event_type == "EXECUTION_FAILED":
            if not isinstance(payload["failure"], str) or previous.event_type not in (
                "MODEL_DECISION",
                "TOOL_RESULT",
                "TOOL_FAILED",
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
        if isinstance(value, Complete):
            output = cls._freeze(value.output)
            return value if output is value.output else Complete(output)
        if isinstance(value, Observation):
            request = cls._freeze(value.request)
            result = cls._freeze(value.result)
            if request is value.request and result is value.result:
                return value
            return Observation(request, result)
        if isinstance(value, ModelRequest):
            context = cls._freeze(value.context)
            available_tools = cls._freeze(value.available_tools)
            source_event_refs = cls._freeze(value.source_event_refs)
            if (
                context is value.context
                and available_tools is value.available_tools
                and source_event_refs is value.source_event_refs
            ):
                return value
            return ModelRequest(context, available_tools, source_event_refs)
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
    status: Literal["running", "completed", "failed"]
    goal: str
    decision_count: int = 0
    latest_observation: Observation | None = None
    last_action: object | None = None
    last_result: ToolResult | None = None
    completion: str | None = None
    failure: str | None = None


def fold_execution_state(events: Iterable[ExecutionEvent]) -> ExecutionState:
    state: ExecutionState | None = None
    seen_ids: set[str] = set()
    for expected_sequence, event in enumerate(events, start=1):
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
            )
            continue
        if state is None:
            raise ValueError("execution must begin with EXECUTION_STARTED")
        if state.status != "running":
            raise ValueError("terminal execution state cannot accept more events")

        values = {
            "version": event.sequence,
            "status": state.status,
            "goal": state.goal,
            "decision_count": state.decision_count,
            "latest_observation": state.latest_observation,
            "last_action": state.last_action,
            "last_result": state.last_result,
            "completion": state.completion,
            "failure": state.failure,
        }
        if event.event_type == "MODEL_DECISION":
            values["decision_count"] = state.decision_count + 1
            values["last_action"] = event.payload.get("action")
        elif event.event_type in ("TOOL_RESULT", "TOOL_FAILED"):
            observation = event.payload.get("observation")
            if not isinstance(observation, Observation):
                raise ValueError("tool result events require an observation")
            values["latest_observation"] = observation
            values["last_result"] = observation.result
        elif event.event_type == "EXECUTION_COMPLETED":
            output = event.payload.get("output")
            if not isinstance(output, str):
                raise ValueError("completion events require output")
            values["status"] = "completed"
            values["completion"] = output
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


def _action_projection(action: object | None) -> dict[str, object] | None:
    if isinstance(action, ToolCall):
        tool = _request_projection(action.request)["tool"]
        return {"type": "tool_call", "tool": tool}
    if isinstance(action, Complete):
        return {"type": "complete", "output": _text_projection(action.output)}
    if action is None:
        return None
    return {"type": type(action).__name__}


def _observation_projection(
    observation: Observation | None,
) -> dict[str, object] | None:
    if observation is None:
        return None
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
        "state": {
            "version": state.version,
            "status": state.status,
            "decision_count": state.decision_count,
            "last_action": _action_projection(state.last_action),
            "last_result": (
                {
                    "ok": state.last_result.ok,
                    "error_code": state.last_result.error_code,
                    "exit_code": state.last_result.exit_code,
                    "canonical_truncated": state.last_result.truncated,
                }
                if state.last_result is not None
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
) -> ModelRequest:
    return ModelRequest(
        context=_bounded_context(state, max_context_chars),
        available_tools=TOOL_CONTRACTS,
        source_event_refs=source_event_refs,
    )


@dataclass(frozen=True)
class ExecutionResult:
    status: Literal["completed", "failed"]
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
    ) -> None:
        if max_decisions < 1:
            raise ValueError("max_decisions must be positive")
        if max_context_chars < 768:
            raise ValueError("max_context_chars must be at least 768")
        self._model = model
        self._tools = tools
        self._max_decisions = max_decisions
        self._max_context_chars = max_context_chars

    def run(self, goal: str) -> ExecutionResult:
        event_log = EventLog()
        event_log.append("EXECUTION_STARTED", {"goal": goal})
        steps: list[ExecutionStep] = []
        for decision in range(1, self._max_decisions + 1):
            state = fold_execution_state(event_log.events)
            source_refs = (event_log.events[-1].event_id,)
            request = _build_model_request(
                state, source_refs, self._max_context_chars
            )
            raw_response = self._model.decide(request)
            raw_response_snapshot = EventLog._freeze(raw_response)
            action = (
                raw_response
                if isinstance(raw_response, (ToolCall, Complete))
                else None
            )
            action_snapshot = EventLog._freeze(action)
            frame = DecisionFrame(
                decision_id=f"decision-{decision:06d}",
                model_identifier=self._model.identifier,
                goal=goal,
                state_version=state.version,
                source_event_refs=source_refs,
                actual_request=request,
                actual_tools_exposed=request.available_tools,
                raw_model_response=raw_response_snapshot,
                resulting_action=action_snapshot,
            )
            decision_event = event_log.append(
                "MODEL_DECISION",
                {"action": action_snapshot, "frame": frame},
                source_refs,
            )
            if isinstance(action, Complete):
                steps.append(ExecutionStep(decision, action, None))
                event_log.append(
                    "EXECUTION_COMPLETED",
                    {"output": action.output},
                    (decision_event.event_id,),
                )
                return self._result(event_log, steps)
            if action is None:
                steps.append(ExecutionStep(decision, raw_response, None))
                event_log.append(
                    "EXECUTION_FAILED",
                    {"failure": f"unknown_action:{type(raw_response).__name__}"},
                    (decision_event.event_id,),
                )
                return self._result(event_log, steps)
            call_event = event_log.append(
                "TOOL_CALL_STARTED",
                {"request": action.request},
                (decision_event.event_id,),
            )
            result = self._tools.execute(action.request)
            observation = Observation(action.request, result)
            event_log.append(
                "TOOL_RESULT" if result.ok else "TOOL_FAILED",
                {"observation": observation},
                (call_event.event_id,),
            )
            steps.append(ExecutionStep(decision, action, observation))
        event_log.append(
            "EXECUTION_FAILED",
            {"failure": "decision_limit_reached"},
            (event_log.events[-1].event_id,),
        )
        return self._result(event_log, steps)

    @staticmethod
    def _result(
        event_log: EventLog, steps: list[ExecutionStep]
    ) -> ExecutionResult:
        events = event_log.events
        state = fold_execution_state(events)
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
