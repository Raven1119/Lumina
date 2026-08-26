from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeAlias


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


@dataclass(frozen=True)
class DecisionContext:
    goal: str
    observation: Observation | None


class Model(Protocol):
    def decide(self, context: DecisionContext) -> Action: ...


class ScriptedModel:
    def __init__(self, actions: list[Action]) -> None:
        self._actions = iter(actions)
        self.seen_contexts: list[DecisionContext] = []

    def decide(self, context: DecisionContext) -> Action:
        self.seen_contexts.append(context)
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


@dataclass(frozen=True)
class ExecutionResult:
    status: Literal["completed", "failed"]
    output: str | None
    failure: str | None
    steps: tuple[ExecutionStep, ...]


class RootAgentProcess:
    def __init__(self, model: Model, tools: ToolHost, max_decisions: int) -> None:
        if max_decisions < 1:
            raise ValueError("max_decisions must be positive")
        self._model = model
        self._tools = tools
        self._max_decisions = max_decisions

    def run(self, goal: str) -> ExecutionResult:
        observation = None
        steps: list[ExecutionStep] = []
        for decision in range(1, self._max_decisions + 1):
            action = self._model.decide(DecisionContext(goal, observation))
            if isinstance(action, Complete):
                steps.append(ExecutionStep(decision, action, None))
                return ExecutionResult("completed", action.output, None, tuple(steps))
            if not isinstance(action, ToolCall):
                steps.append(ExecutionStep(decision, action, None))
                return ExecutionResult(
                    "failed",
                    None,
                    f"unknown_action:{type(action).__name__}",
                    tuple(steps),
                )
            result = self._tools.execute(action.request)
            observation = Observation(action.request, result)
            steps.append(ExecutionStep(decision, action, observation))
        return ExecutionResult("failed", None, "decision_limit_reached", tuple(steps))
