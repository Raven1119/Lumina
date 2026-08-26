from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Literal

import httpx

from Execution_lab2.execution import (
    ClaimComplete,
    IPYTHON_TOOL_CONTRACTS,
    IPythonCode,
    ModelRequest,
    NativeModelDecision,
    ReadRequest,
    ShellRequest,
    ToolCall,
    TOOL_CONTRACTS,
    Wait,
    WriteRequest,
)


MODEL = "deepseek-v4-pro"
_ENDPOINT = "https://api.deepseek.com/chat/completions"
_TIMEOUT_SECONDS = 60.0

_NATIVE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a UTF-8 text file in the execution workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write exact UTF-8 text content in the execution workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run one explicit argv command in the execution workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["argv"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Pause until an exact external event type arrives.",
            "parameters": {
                "type": "object",
                "properties": {"event_type": {"type": "string"}},
                "required": ["event_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claim_complete",
            "description": "Ask Runtime to verify the caller-owned completion spec.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_IPYTHON_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ipython",
            "description": "Execute Python in the persistent workspace kernel.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    },
    _NATIVE_TOOLS[3],
    _NATIVE_TOOLS[4],
]


class _ProviderError(Exception):
    pass


class DeepSeekModel:
    identifier = MODEL

    def __init__(
        self,
        *,
        tool_mode: Literal["native", "ipython"] = "native",
        transport: Callable[[dict[str, object]], object] | None = None,
    ) -> None:
        if tool_mode not in ("native", "ipython"):
            raise ValueError("tool_mode must be native or ipython")
        self._tool_mode = tool_mode
        self._transport = transport or self._post

    @property
    def tool_contracts(self) -> tuple[str, ...]:
        return (
            TOOL_CONTRACTS
            if self._tool_mode == "native"
            else IPYTHON_TOOL_CONTRACTS
        )

    def decide(self, request: ModelRequest) -> NativeModelDecision:
        system_prompt = (
            "Use the provided functions to act on the environment. "
            "Claim completion only through claim_complete."
        )
        if self._tool_mode == "ipython":
            system_prompt = (
                "Use the persistent IPython environment to inspect and modify "
                "the workspace. Use claim_complete when the task is finished."
            )
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]
        continuation = request.native_tool_continuation
        if continuation is None:
            messages.append({"role": "user", "content": request.context})
        else:
            previous_context, outcome_content = self._bounded_continuation(
                continuation.previous_model_context,
                self._outcome_content(request.context),
                request.model_visible_context_limit,
            )
            messages.extend(
                [
                    {
                        "role": "user",
                        "content": previous_context,
                    },
                    self._assistant_message(
                        continuation.raw_provider_response,
                        continuation.provider_tool_call_id,
                    ),
                    {
                        "role": "tool",
                        "tool_call_id": continuation.provider_tool_call_id,
                        "content": outcome_content,
                    },
                ]
            )
        payload: dict[str, object] = {
            "model": MODEL,
            "messages": messages,
            "tools": (
                _NATIVE_TOOLS
                if self._tool_mode == "native"
                else _IPYTHON_TOOLS
            ),
            "thinking": {"type": "disabled"},
            "stream": False,
        }
        try:
            response = self._without_reasoning(self._transport(payload))
        except _ProviderError as exc:
            return NativeModelDecision(
                None,
                payload,
                None,
                failure=f"model_provider:{exc}",
            )
        except TimeoutError:
            return NativeModelDecision(
                None,
                payload,
                None,
                failure="model_provider:timeout",
            )
        except OSError:
            return NativeModelDecision(
                None,
                payload,
                None,
                failure="model_provider:error",
            )
        if not isinstance(response, Mapping):
            return self._failure(payload, response, "malformed_response")
        try:
            message = response["choices"][0]["message"]
            tool_calls = message["tool_calls"]
        except (KeyError, IndexError, TypeError):
            return self._failure(payload, response, "malformed_response")
        if not isinstance(tool_calls, list):
            return self._failure(payload, response, "malformed_response")
        if not tool_calls:
            return self._failure(payload, response, "no_tool_call")
        if len(tool_calls) > 1:
            return self._failure(payload, response, "multiple_tool_calls")
        call = tool_calls[0]
        if not isinstance(call, Mapping):
            return self._failure(payload, response, "malformed_response")
        call_id = call.get("id")
        function = call.get("function")
        if (
            call.get("type") != "function"
            or not isinstance(call_id, str)
            or not call_id
            or not isinstance(function, Mapping)
            or not isinstance(function.get("name"), str)
            or not isinstance(function.get("arguments"), str)
        ):
            return self._failure(payload, response, "malformed_response")
        name = function["name"]
        allowed_names = (
            {"read", "write", "shell", "wait", "claim_complete"}
            if self._tool_mode == "native"
            else {"ipython", "wait", "claim_complete"}
        )
        if name not in allowed_names:
            return self._failure(payload, response, "unknown_tool")
        try:
            arguments = json.loads(function["arguments"])
        except json.JSONDecodeError:
            return self._failure(payload, response, "invalid_json")
        action = self._action(name, arguments)
        if action is None:
            return self._failure(payload, response, "invalid_arguments")
        return NativeModelDecision(
            action,
            payload,
            response,
            provider_tool_call_id=call_id,
        )

    @staticmethod
    def _post(payload: dict[str, object]) -> object:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise _ProviderError("missing_credentials")
        try:
            response = httpx.post(
                _ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TimeoutError from exc
        except httpx.HTTPStatusError as exc:
            raise _ProviderError(f"http_{exc.response.status_code}") from None
        except httpx.HTTPError as exc:
            raise OSError from exc
        try:
            return response.json()
        except ValueError:
            raise _ProviderError("malformed_response") from None

    @staticmethod
    def _action(name: str, arguments: object):
        if not isinstance(arguments, dict):
            return None
        if (
            name == "ipython"
            and set(arguments) == {"code"}
            and isinstance(arguments["code"], str)
            and arguments["code"]
        ):
            return IPythonCode(arguments["code"])
        if (
            name == "read"
            and set(arguments) == {"path"}
            and isinstance(arguments["path"], str)
            and arguments["path"]
        ):
            return ToolCall(ReadRequest(arguments["path"]))
        if (
            name == "write"
            and set(arguments) == {"path", "content"}
            and isinstance(arguments["path"], str)
            and arguments["path"]
            and isinstance(arguments["content"], str)
        ):
            return ToolCall(WriteRequest(arguments["path"], arguments["content"]))
        if (
            name == "shell"
            and set(arguments) == {"argv"}
            and isinstance(arguments["argv"], list)
            and arguments["argv"]
            and all(isinstance(item, str) and item for item in arguments["argv"])
        ):
            return ToolCall(ShellRequest(tuple(arguments["argv"])))
        if (
            name == "wait"
            and set(arguments) == {"event_type"}
            and isinstance(arguments["event_type"], str)
            and arguments["event_type"]
        ):
            return Wait(arguments["event_type"])
        if name == "claim_complete" and not arguments:
            return ClaimComplete()
        return None

    @staticmethod
    def _failure(payload, response, reason):
        return NativeModelDecision(
            None,
            payload,
            response,
            failure=f"model_protocol:{reason}",
        )

    @staticmethod
    def _assistant_message(response: object, call_id: str) -> dict[str, object]:
        try:
            message = response["choices"][0]["message"]
            calls = message["tool_calls"]
        except (KeyError, IndexError, TypeError):
            raise ValueError("durable native continuation is malformed") from None
        if not isinstance(calls, (list, tuple)) or len(calls) != 1:
            raise ValueError("durable native continuation is malformed")
        call = DeepSeekModel._without_reasoning(calls[0])
        if not isinstance(call, dict) or call.get("id") != call_id:
            raise ValueError("durable native continuation call id changed")
        return {
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": [call],
        }

    @staticmethod
    def _outcome_content(context: str) -> str:
        document = json.loads(context)
        if document.get("observation") is not None:
            outcome = {"observation": document["observation"]}
        elif document.get("incoming_event") is not None:
            outcome = {"incoming_event": document["incoming_event"]}
        else:
            outcome = {"context": document}
        return json.dumps(outcome, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _bounded_continuation(
        previous_context: str,
        outcome_content: str,
        limit: int | None,
    ) -> tuple[str, str]:
        if limit is None or len(previous_context) + len(outcome_content) <= limit:
            return previous_context, outcome_content
        documents = [json.loads(previous_context), json.loads(outcome_content)]

        def render() -> tuple[str, str]:
            return tuple(
                json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                for value in documents
            )

        def text_fields(value: object) -> list[dict[str, object]]:
            fields: list[dict[str, object]] = []
            if isinstance(value, dict):
                if {"text", "truncated", "original_chars"} <= value.keys():
                    fields.append(value)
                else:
                    for item in value.values():
                        fields.extend(text_fields(item))
            elif isinstance(value, list):
                for item in value:
                    fields.extend(text_fields(item))
            return fields

        rendered = render()
        fields = text_fields(documents)
        while sum(map(len, rendered)) > limit:
            populated = [field for field in fields if field["text"]]
            if not populated:
                raise ValueError("native continuation metadata exceeds context bound")
            field = max(populated, key=lambda item: len(str(item["text"])))
            overflow = sum(map(len, rendered)) - limit
            text = str(field["text"])
            field["text"] = text[: max(0, len(text) - overflow)]
            field["truncated"] = True
            rendered = render()
        return rendered

    @staticmethod
    def _without_reasoning(value: object) -> object:
        if isinstance(value, Mapping):
            return {
                key: DeepSeekModel._without_reasoning(item)
                for key, item in value.items()
                if key != "reasoning_content"
            }
        if isinstance(value, (list, tuple)):
            return [DeepSeekModel._without_reasoning(item) for item in value]
        return value
