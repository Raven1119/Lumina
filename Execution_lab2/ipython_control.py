from __future__ import annotations

import os
import queue
import time
from dataclasses import dataclass
from pathlib import Path

from jupyter_client.manager import start_new_kernel


@dataclass(frozen=True)
class IPythonResult:
    ok: bool
    output: str = ""
    error_code: str | None = None
    error: str | None = None
    truncated: bool = False
    original_output_chars: int = 0


class PersistentIPython:
    def __init__(
        self,
        workspace: str | Path,
        *,
        max_code_chars: int = 20_000,
        execution_timeout_seconds: float = 10.0,
        max_output_chars: int = 10_000,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        if max_code_chars < 1 or max_output_chars < 1:
            raise ValueError("code and output limits must be positive")
        if execution_timeout_seconds <= 0:
            raise ValueError("execution timeout must be positive")
        self._max_code_chars = max_code_chars
        self._execution_timeout_seconds = execution_timeout_seconds
        self._max_output_chars = max_output_chars
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
            startup_timeout=self._execution_timeout_seconds,
            kernel_name="python3",
            cwd=str(self._workspace),
            env=kernel_environment,
        )

    def _execute(self, code: str) -> IPythonResult:
        message_id = self._client.execute(code, allow_stdin=False, stop_on_error=False)
        deadline = time.monotonic() + self._execution_timeout_seconds
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
                    f"IPython execution exceeded {self._execution_timeout_seconds} seconds",
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
            if message_type == "stream":
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

    def _bounded(self, text: str) -> tuple[str, bool]:
        return text[: self._max_output_chars], len(text) > self._max_output_chars
