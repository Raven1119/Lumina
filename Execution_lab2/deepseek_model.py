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
    Return,
    ROOT_IPYTHON_TOOL_CONTRACTS,
    ROOT_TOOL_CONTRACTS,
    ShellRequest,
    SpawnChild,
    ToolCall,
    TOOL_CONTRACTS,
    Wait,
    WriteRequest,
)


MODEL = "deepseek-v4-pro"
_ENDPOINT = "https://api.deepseek.com/chat/completions"
_TIMEOUT_SECONDS = 60.0
_MAX_TOOL_CALLS_PER_DECISION = 4

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
    {
        "type": "function",
        "function": {
            "name": "spawn_child",
            "description": "Admit one independent Child with a bounded local goal.",
            "parameters": {
                "type": "object",
                "properties": {"goal": {"type": "string"}},
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "return",
            "description": "Return one bounded local result to the parent Root.",
            "parameters": {
                "type": "object",
                "properties": {"local_result": {"type": "string"}},
                "required": ["local_result"],
            },
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
    _NATIVE_TOOLS[5],
    _NATIVE_TOOLS[6],
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
            ROOT_TOOL_CONTRACTS + ("return(local_result: str)",)
            if self._tool_mode == "native"
            else ROOT_IPYTHON_TOOL_CONTRACTS + ("return(local_result: str)",)
        )

    def decide(self, request: ModelRequest) -> NativeModelDecision:
        requested_names = {
            contract.partition("(")[0]
            for contract in request.available_tools
        }
        if requested_names & {"spawn_child", "return"}:
            exposed_names = requested_names
        else:
            exposed_names = {
                contract.partition("(")[0]
                for contract in (
                    TOOL_CONTRACTS
                    if self._tool_mode == "native"
                    else IPYTHON_TOOL_CONTRACTS
                )
            }
        system_prompt = (
            "Use the provided functions to act on the environment. "
            "Claim completion only through claim_complete."
            if "claim_complete" in exposed_names
            else (
                "Use the provided functions to complete the local Child goal. "
                "Return only through return."
            )
        )
        if self._tool_mode == "ipython":
            system_prompt = (
                "Use the persistent IPython environment to inspect and modify "
                "the workspace. "
                + (
                    "Use claim_complete when the task is finished."
                    if "claim_complete" in exposed_names
                    else "Return the local result only through return."
                )
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
            call_ids = (
                (continuation.provider_tool_call_id,)
                if isinstance(continuation.provider_tool_call_id, str)
                else continuation.provider_tool_call_id
            )
            outcome_contents = self._outcome_contents(request.context)
            previous_context, outcome_contents = self._bounded_continuation(
                self._sibling_previous_context(
                    continuation.previous_model_context,
                    len(outcome_contents),
                ),
                outcome_contents,
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
                        call_ids,
                    ),
                    *(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": outcome_content,
                        }
                        for call_id, outcome_content in zip(
                            call_ids,
                            outcome_contents,
                            strict=True,
                        )
                    ),
                ]
            )
        payload: dict[str, object] = {
            "model": MODEL,
            "messages": messages,
            "tools": [
                tool
                for tool in (
                _NATIVE_TOOLS
                if self._tool_mode == "native"
                else _IPYTHON_TOOLS
                )
                if tool["function"]["name"] in exposed_names
            ],
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
        if len(tool_calls) > _MAX_TOOL_CALLS_PER_DECISION:
            return self._failure(payload, response, "too_many_tool_calls")
        parsed_calls = []
        seen_call_ids = set()
        for call in tool_calls:
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
            if call_id in seen_call_ids:
                return self._failure(payload, response, "duplicate_tool_call_id")
            seen_call_ids.add(call_id)
            parsed_calls.append((call_id, function))
        allowed_names = exposed_names
        actions = []
        call_ids = []
        for call_id, function in parsed_calls:
            name = function["name"]
            if name not in allowed_names:
                return self._failure(payload, response, "unknown_tool")
            try:
                arguments = json.loads(function["arguments"])
            except json.JSONDecodeError:
                return self._failure(payload, response, "invalid_json")
            action = self._action(name, arguments)
            if action is None:
                return self._failure(payload, response, "invalid_arguments")
            if isinstance(action, SpawnChild):
                action = SpawnChild(action.goal, call_id)
            actions.append(action)
            call_ids.append(call_id)
        if (
            len(actions) > 1
            and any(
                isinstance(
                    action,
                    (Wait, ClaimComplete, SpawnChild, Return),
                )
                for action in actions
            )
            and not all(isinstance(action, SpawnChild) for action in actions)
        ):
            return self._failure(payload, response, "mixed_control_tool_calls")
        return NativeModelDecision(
            actions[0] if len(actions) == 1 else tuple(actions),
            payload,
            response,
            provider_tool_call_id=(
                call_ids[0] if len(call_ids) == 1 else tuple(call_ids)
            ),
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
        if (
            name == "spawn_child"
            and set(arguments) == {"goal"}
            and isinstance(arguments["goal"], str)
            and 0 < len(arguments["goal"]) <= 1_024
        ):
            return SpawnChild(arguments["goal"])
        if (
            name == "return"
            and set(arguments) == {"local_result"}
            and isinstance(arguments["local_result"], str)
            and len(arguments["local_result"]) <= 1_024
        ):
            return Return(arguments["local_result"])
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
    def _assistant_message(
        response: object,
        call_ids: tuple[str, ...],
    ) -> dict[str, object]:
        try:
            message = response["choices"][0]["message"]
            calls = message["tool_calls"]
        except (KeyError, IndexError, TypeError):
            raise ValueError("durable native continuation is malformed") from None
        if not isinstance(calls, (list, tuple)) or len(calls) != len(call_ids):
            raise ValueError("durable native continuation is malformed")
        clean_calls = tuple(
            DeepSeekModel._without_reasoning(call) for call in calls
        )
        if any(
            not isinstance(call, dict) or call.get("id") != call_id
            for call, call_id in zip(clean_calls, call_ids, strict=True)
        ):
            raise ValueError("durable native continuation call id changed")
        return {
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": list(clean_calls),
        }

    @staticmethod
    def _outcome_contents(context: str) -> tuple[str, ...]:
        document = json.loads(context)
        if document.get("observations") is not None:
            outcomes = tuple(
                {"observation": observation}
                for observation in document["observations"]
            )
        elif document.get("observation") is not None:
            outcomes = ({"observation": document["observation"]},)
        elif document.get("incoming_event") is not None:
            outcomes = ({"incoming_event": document["incoming_event"]},)
        else:
            outcomes = ({"context": document},)
        return tuple(
            json.dumps(outcome, ensure_ascii=False, separators=(",", ":"))
            for outcome in outcomes
        )

    @staticmethod
    def _sibling_previous_context(context: str, outcome_count: int) -> str:
        if outcome_count <= 1:
            return context
        document = json.loads(context)
        compact = {
            "goal": document.get("goal"),
            "completion_spec": document.get("completion_spec"),
        }
        return json.dumps(
            compact,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _bounded_continuation(
        previous_context: str,
        outcome_contents: tuple[str, ...],
        limit: int | None,
    ) -> tuple[str, tuple[str, ...]]:
        if limit is None or len(previous_context) + sum(
            map(len, outcome_contents)
        ) <= limit:
            return previous_context, outcome_contents
        documents = [
            json.loads(previous_context),
            *(json.loads(outcome) for outcome in outcome_contents),
        ]

        def render() -> tuple[str, ...]:
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
        return rendered[0], rendered[1:]

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
