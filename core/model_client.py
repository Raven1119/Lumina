"""Mock and explicit DeepSeek model clients for Lumina."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, Literal, Protocol

import httpx

from core.contracts import MemoryTurn


MOCK_ASSISTANT_TEXT = "Lumina backend shell received your message."
ModelClientKind = Literal["mock", "model"]
DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_MODEL = "deepseek-v4-pro"
_HOT_DRAFT_SUMMARY_PROMPT = """You maintain Lumina's rolling Hot Draft summary.
Rewrite the existing summary together with the supplied archived conversation turns into one concise, directly readable summary.

Preserve, when present:
- the user's identity and stable background;
- long-term and current goals;
- explicit constraints and acceptance criteria;
- confirmed decisions;
- rejected approaches and their important reasons;
- current work state and unresolved items;
- important transitions from an earlier state to a newer state.

Do not invent facts or recommendations. Preserve uncertainty, distinguish historical state from current state, retain important negative conclusions, and remove stale or valueless repetition. Use only the supplied existing summary and archived turns. Return summary text only."""


class ModelClient(Protocol):
    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        ...

    def summarize_hot_draft(
        self,
        old_summary: str | None,
        moved_turns: list[MemoryTurn],
    ) -> str:
        ...


class MockModelClient:
    client_kind: ModelClientKind = "mock"

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        return MOCK_ASSISTANT_TEXT

    def summarize_hot_draft(
        self,
        old_summary: str | None,
        moved_turns: list[MemoryTurn],
    ) -> str:
        raise ModelClientError("Rolling summary requires a configured model.")


class ModelClientError(RuntimeError):
    """A provider failure safe to handle without exposing provider details."""


class DeepSeekAnthropicModelClient:
    """Minimal synchronous client for DeepSeek's Anthropic-compatible API."""

    client_kind: ModelClientKind = "model"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        max_tokens: int = 1000,
        temperature: float | None = None,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._http_client = http_client or httpx.Client(timeout=timeout)

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "thinking": {"type": "disabled"},
            "messages": [
                *self._project_context(recent_context),
                {"role": "user", "content": user_message},
            ],
        }
        if self._temperature is not None:
            body["temperature"] = self._temperature
        summary_blocks = [
            item.get("text")
            for item in recent_context
            if item.get("role") == "summary"
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        ]
        body['system'] = '\n\n'.join([system_prompt, *summary_blocks])
        return self._request(body)

    def summarize_hot_draft(
        self,
        old_summary: str | None,
        moved_turns: list[MemoryTurn],
    ) -> str:
        payload = {
            "existing_summary": old_summary,
            "archived_turns": [
                {"role": turn.role, "text": turn.text}
                for turn in moved_turns
            ],
        }
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": 0.0,
            "thinking": {"type": "disabled"},
            "system": _HOT_DRAFT_SUMMARY_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            ],
        }
        return self._request(body)

    def _request(self, body: dict[str, Any]) -> str:
        headers = {
            "X-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }

        try:
            response = self._http_client.post(
                f"{self._base_url}/v1/messages",
                headers=headers,
                json=body,
            )
        except Exception:
            raise ModelClientError("Provider request failed.") from None

        if not 200 <= response.status_code < 300:
            raise ModelClientError("Provider request failed.")

        try:
            payload = response.json()
        except Exception:
            raise ModelClientError("Provider response was invalid.") from None

        text = self._extract_text(payload)
        if not text.strip():
            raise ModelClientError("Provider response was invalid.")
        return text

    @staticmethod
    def _project_context(
        recent_context: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for item in recent_context:
            role = item.get("role")
            text = item.get("text")
            if role in {"user", "assistant"} and isinstance(text, str):
                messages.append({"role": role, "content": text})
        return messages

    @staticmethod
    def _extract_text(payload: Any) -> str:
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
            raise ModelClientError("Provider response was invalid.")
        for block in payload["content"]:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                return block["text"]
        raise ModelClientError("Provider response was invalid.")


def build_model_client_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    model_name_override: str | None = None,
    max_tokens_override: int | None = None,
    temperature_override: float | None = None,
) -> ModelClient:
    env = environ if environ is not None else os.environ
    if env.get("LUMINA_MODEL_MODE", "mock").strip().lower() != "real":
        return MockModelClient()

    api_key = env.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return MockModelClient()
    model = (
        model_name_override.strip()
        if model_name_override is not None
        else DEEPSEEK_MODEL
    )
    if model != DEEPSEEK_MODEL:
        return MockModelClient()

    client_options: dict[str, Any] = {
        "api_key": api_key,
        "base_url": DEEPSEEK_ANTHROPIC_BASE_URL,
        "model": model,
    }
    if max_tokens_override is not None:
        client_options["max_tokens"] = max_tokens_override
    if temperature_override is not None:
        client_options["temperature"] = temperature_override
    return DeepSeekAnthropicModelClient(
        **client_options,
    )
