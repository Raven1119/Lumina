"""Mock and explicit MiniMax model clients for the Cold Draft MVP."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal, Protocol

import httpx


MOCK_ASSISTANT_TEXT = "Lumina backend shell received your message."
ModelClientKind = Literal["mock", "model"]


class ModelClient(Protocol):
    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
    ) -> str:
        ...


class MockModelClient:
    client_kind: ModelClientKind = "mock"

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
    ) -> str:
        return MOCK_ASSISTANT_TEXT


class ModelClientError(RuntimeError):
    """A provider failure safe to handle without exposing provider details."""


class MiniMaxAnthropicModelClient:
    """Minimal synchronous client for MiniMax's Anthropic-compatible API."""

    client_kind: ModelClientKind = "model"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        max_tokens: int = 1000,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens
        self._http_client = http_client or httpx.Client(timeout=timeout)

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
    ) -> str:
        body = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [
                *self._project_context(recent_context),
                {"role": "user", "content": user_message},
            ],
        }
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
) -> ModelClient:
    env = environ if environ is not None else os.environ
    if env.get("LUMINA_MODEL_MODE", "mock").strip().lower() != "real":
        return MockModelClient()

    provider = env.get("LUMINA_MODEL_PROVIDER", "").strip().lower()
    api_key = env.get("LUMINA_MODEL_API_KEY", "").strip()
    base_url = env.get("LUMINA_MODEL_BASE_URL", "").strip()
    model = env.get("LUMINA_MODEL_NAME", "").strip()
    if provider != "minimax-anthropic" or not all((api_key, base_url, model)):
        return MockModelClient()

    return MiniMaxAnthropicModelClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
