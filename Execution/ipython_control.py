from __future__ import annotations

import os
import json
import queue
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from jupyter_client.manager import start_new_kernel


_SPAWN_CHILD_COMM_TARGET = "lumina.spawn_child"
_SPAWN_CHILD_BOOTSTRAP = r'''
import asyncio as _lumina_asyncio
from comm import create_comm as _lumina_create_comm
from IPython import get_ipython as _lumina_get_ipython

_lumina_kernel = _lumina_get_ipython().kernel
_lumina_comm_manager = _lumina_kernel.comm_manager
_lumina_kernel.control_handlers.setdefault("comm_msg", _lumina_comm_manager.comm_msg)
_lumina_kernel.control_handlers.setdefault("comm_close", _lumina_comm_manager.comm_close)

async def spawn_child(goal: str):
    if not isinstance(goal, str):
        raise TypeError("goal must be a string")
    if not goal or len(goal) > 1024:
        raise ValueError("goal must contain 1 to 1024 characters")
    loop = _lumina_asyncio.get_running_loop()
    future = loop.create_future()
    comm = _lumina_create_comm(target_name="lumina.spawn_child", primary=False)

    def receive(message):
        content = message.get("content", {})
        reply = content.get("data", {}) if isinstance(content, dict) else {}
        if not isinstance(reply, dict):
            return
        status = reply.get("status")

        def settle():
            if future.done():
                return
            if status == "ok":
                future.set_result({key: value for key, value in reply.items() if key != "status"})
            else:
                future.set_exception(RuntimeError(str(reply.get("error") or "spawn_child failed")))

        loop.call_soon_threadsafe(settle)

    comm.on_msg(receive)
    comm.open(data={"goal": goal})
    try:
        return await future
    finally:
        comm.close()
'''


@dataclass(frozen=True)
class IPythonResult:
    ok: bool
    output: str = ""
    error_code: str | None = None
    error: str | None = None
    truncated: bool = False
    original_output_chars: int = 0
    cognitive_request: str | None = None

    def __post_init__(self):
        if self.cognitive_request is not None:
            if not self.ok:
                raise ValueError('cognitive_request_requires_successful_result')
            validate_cognitive_request(self.cognitive_request)


def validate_cognitive_request(text):
    """An actor-authored request, never a claim authenticated as reality."""
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError('cognitive_request_bound')
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {'question', 'evidence_files', 'model_ref'}:
        raise ValueError('invalid_cognitive_request')
    if not isinstance(value['question'], str) or not value['question'].strip() or len(value['question']) > 1000:
        raise ValueError('invalid_cognitive_question')
    files = value['evidence_files']
    if not isinstance(files, list) or len(files) > 3:
        raise ValueError('cognitive_evidence_bound')
    for file in files:
        if (not isinstance(file, str) or not file or len(file) > 128
                or PurePosixPath(file).is_absolute() or PureWindowsPath(file).drive
                or '..' in PurePosixPath(file.replace('\\', '/')).parts):
            raise ValueError('cognitive_evidence_requires_relative_path')
    if not isinstance(value['model_ref'], str) or len(value['model_ref']) > 128:
        raise ValueError('invalid_cognitive_model_ref')
    return value


class PersistentIPython:
    def __init__(
        self,
        workspace: str | Path,
        *,
        max_code_chars: int = 20_000,
        kernel_startup_timeout_seconds: float = 10.0,
        execution_timeout_seconds: float = 10.0,
        max_output_chars: int = 10_000,
        spawn_child_handler: (
            Callable[[str], Mapping[str, object]] | None
        ) = None,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        if max_code_chars < 1 or max_output_chars < 1:
            raise ValueError("code and output limits must be positive")
        if kernel_startup_timeout_seconds <= 0:
            raise ValueError("kernel startup timeout must be positive")
        if execution_timeout_seconds <= 0:
            raise ValueError("execution timeout must be positive")
        self._max_code_chars = max_code_chars
        self._kernel_startup_timeout_seconds = kernel_startup_timeout_seconds
        self._code_execution_timeout_seconds = execution_timeout_seconds
        self._max_output_chars = max_output_chars
        self._spawn_child_handler = spawn_child_handler
        self._manager = None
        self._client = None
        self._closed = False

    @property
    def is_alive(self) -> bool:
        if self._manager is None:
            return False
        try:
            return bool(self._manager.is_alive())
        except Exception:
            return True

    def bind_spawn_child_handler(
        self,
        handler: Callable[[str], Mapping[str, object]],
    ) -> None:
        if not callable(handler):
            raise TypeError("spawn_child handler must be callable")
        if self._closed:
            raise RuntimeError("IPython kernel is closed")
        previous_handler = self._spawn_child_handler
        self._spawn_child_handler = handler
        if self._manager is None or previous_handler is not None:
            return
        if not self.is_alive:
            self._spawn_child_handler = previous_handler
            raise RuntimeError("IPython kernel exited")
        result = self._execute(_SPAWN_CHILD_BOOTSTRAP)
        if not result.ok:
            self._spawn_child_handler = previous_handler
            raise RuntimeError(result.error or "spawn_child bootstrap failed")

    def execute(self, code: str) -> IPythonResult:
        if not isinstance(code, str) or not code:
            return IPythonResult(
                False,
                error_code="invalid_code",
                error="code must be a non-empty string",
            )
        if len(code) > self._max_code_chars:
            return IPythonResult(
                False,
                error_code="code_limit",
                error=f"code exceeds {self._max_code_chars} characters",
            )
        if self._closed:
            return IPythonResult(
                False,
                error_code="kernel_closed",
                error="IPython kernel is closed",
            )
        try:
            self._start()
        except Exception as exc:
            error, truncated = self._bounded(f"{type(exc).__name__}: {exc}")
            self.close()
            return IPythonResult(
                False,
                error_code="kernel_startup_error",
                error=error,
                truncated=truncated,
            )
        try:
            return self._execute(code)
        except Exception as exc:
            error, truncated = self._bounded(f"{type(exc).__name__}: {exc}")
            return IPythonResult(
                False,
                error_code="kernel_error",
                error=error,
                truncated=truncated,
            )

    def interrupt(self) -> bool:
        if self._manager is None or not self.is_alive:
            return False
        try:
            self._manager.interrupt_kernel()
        except Exception:
            return False
        return True

    def close(self) -> None:
        if self._closed and not self.is_alive:
            return
        if self._manager is None:
            self._closed = True
            return
        try:
            try:
                if self._client is not None:
                    self._client.stop_channels()
            finally:
                try:
                    if self.is_alive:
                        self._manager.shutdown_kernel(now=True)
                finally:
                    self._manager.cleanup_resources()
        finally:
            self._closed = not self.is_alive

    def _start(self) -> None:
        if self._manager is not None:
            if not self.is_alive:
                raise RuntimeError("IPython kernel exited")
            return
        kernel_environment = dict(os.environ)
        kernel_environment.pop("DEEPSEEK_API_KEY", None)
        self._manager, self._client = start_new_kernel(
            startup_timeout=self._kernel_startup_timeout_seconds,
            kernel_name="python3",
            cwd=str(self._workspace),
            env=kernel_environment,
        )
        if self._spawn_child_handler is not None:
            result = self._execute(_SPAWN_CHILD_BOOTSTRAP)
            if not result.ok:
                raise RuntimeError(
                    result.error or "spawn_child bootstrap failed"
                )

    def _execute(self, code: str) -> IPythonResult:
        message_id = self._client.execute(code, allow_stdin=False, stop_on_error=False)
        deadline = time.monotonic() + self._code_execution_timeout_seconds
        output_parts: list[str] = []
        output_chars = 0
        captured_chars = 0
        execution_error: str | None = None
        error_truncated = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._manager.interrupt_kernel()
                output = "".join(output_parts)
                return IPythonResult(
                    False,
                    output,
                    "timeout",
                    "IPython execution exceeded "
                    f"{self._code_execution_timeout_seconds} seconds",
                    output_chars > self._max_output_chars,
                    output_chars,
                )
            try:
                message = self._client.get_iopub_msg(timeout=remaining)
            except queue.Empty:
                continue
            if message.get("parent_header", {}).get("msg_id") != message_id:
                continue
            message_type = message.get("msg_type")
            content = message.get("content", {})
            text = None
            if message_type == "comm_open":
                self._handle_spawn_child(content)
            elif message_type == "stream":
                text = content.get("text")
            elif message_type in ("execute_result", "display_data"):
                text = content.get("data", {}).get("text/plain")
            elif message_type == "error":
                execution_error, error_truncated = self._bounded(
                    f"{content.get('ename', 'Error')}: {content.get('evalue', '')}"
                )
            elif message_type == "status" and content.get("execution_state") == "idle":
                break
            if isinstance(text, str):
                output_chars += len(text)
                remaining_output = self._max_output_chars - captured_chars
                if remaining_output > 0:
                    captured = text[:remaining_output]
                    output_parts.append(captured)
                    captured_chars += len(captured)
        output = "".join(output_parts)
        if execution_error is not None:
            return IPythonResult(
                False,
                output,
                "execution_error",
                execution_error,
                output_chars > self._max_output_chars or error_truncated,
                output_chars,
            )
        return IPythonResult(
            True,
            output,
            truncated=output_chars > self._max_output_chars,
            original_output_chars=output_chars,
        )

    def _handle_spawn_child(self, content: object) -> None:
        if not isinstance(content, Mapping):
            return
        if content.get("target_name") != _SPAWN_CHILD_COMM_TARGET:
            return
        comm_id = content.get("comm_id")
        data = content.get("data")
        if not isinstance(comm_id, str) or not isinstance(data, Mapping):
            return
        goal = data.get("goal")
        try:
            if self._spawn_child_handler is None:
                raise RuntimeError("spawn_child is unavailable")
            if not isinstance(goal, str) or not goal or len(goal) > 1_024:
                raise ValueError("goal must contain 1 to 1024 characters")
            reply = dict(self._spawn_child_handler(goal))
            response: dict[str, object] = {"status": "ok", **reply}
        except Exception as exc:
            response = {"status": "error", "error": str(exc)[:1_024]}
        message = self._client.session.msg(
            "comm_msg", {"comm_id": comm_id, "data": response}
        )
        self._client.control_channel.send(message)

    def _bounded(self, text: str) -> tuple[str, bool]:
        return text[: self._max_output_chars], len(text) > self._max_output_chars
