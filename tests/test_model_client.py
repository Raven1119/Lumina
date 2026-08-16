import json

import httpx
import pytest

from core.contracts import MemoryTurn
from core.model_client import (
    MOCK_ASSISTANT_TEXT,
    MiniMaxAnthropicModelClient,
    MockModelClient,
    ModelClientError,
    build_model_client_from_env,
)


def test_default_configuration_uses_mock() -> None:
    client = build_model_client_from_env({})
    assert isinstance(client, MockModelClient)
    assert client.generate(
        [],
        "hello",
        system_prompt="chat background",
    ) == MOCK_ASSISTANT_TEXT


def test_complete_explicit_configuration_builds_minimax_adapter() -> None:
    client = build_model_client_from_env(
        {
            "LUMINA_MODEL_MODE": "real",
            "LUMINA_MODEL_PROVIDER": "minimax-anthropic",
            "LUMINA_MODEL_API_KEY": "test-value",
            "LUMINA_MODEL_BASE_URL": "https://provider.invalid/anthropic",
            "LUMINA_MODEL_NAME": "test-model",
        }
    )
    assert isinstance(client, MiniMaxAnthropicModelClient)


def test_explicit_model_name_override_selects_dedicated_model(monkeypatch) -> None:
    import core.model_client as model_client_module

    configured = {
        "LUMINA_MODEL_MODE": "real",
        "LUMINA_MODEL_PROVIDER": "minimax-anthropic",
        "LUMINA_MODEL_API_KEY": "test-value",
        "LUMINA_MODEL_BASE_URL": "https://provider.invalid/anthropic",
        "LUMINA_MODEL_NAME": "MiniMax-M2.7",
    }
    captured = {}
    sentinel = object()

    def build_minimax(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        model_client_module,
        "MiniMaxAnthropicModelClient",
        build_minimax,
    )
    client = build_model_client_from_env(
        configured,
        model_name_override="MiniMax-M3",
        max_tokens_override=2000,
    )
    assert client is sentinel
    assert captured == {
        "api_key": "test-value",
        "base_url": "https://provider.invalid/anthropic",
        "model": "MiniMax-M3",
        "max_tokens": 2000,
    }
    assert configured["LUMINA_MODEL_NAME"] == "MiniMax-M2.7"

    captured.clear()
    default_client = build_model_client_from_env(configured)
    assert default_client is sentinel
    assert captured["model"] == "MiniMax-M2.7"


def test_incomplete_or_unsupported_real_configuration_falls_back_to_mock() -> None:
    assert isinstance(
        build_model_client_from_env({"LUMINA_MODEL_MODE": "real"}),
        MockModelClient,
    )
    assert isinstance(
        build_model_client_from_env(
            {
                "LUMINA_MODEL_MODE": "real",
                "LUMINA_MODEL_PROVIDER": "unsupported",
                "LUMINA_MODEL_API_KEY": "test-value",
                "LUMINA_MODEL_BASE_URL": "https://provider.invalid",
                "LUMINA_MODEL_NAME": "test-model",
            }
        ),
        MockModelClient,
    )


def test_minimax_request_shape_and_thinking_block_filter() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "thinking", "thinking": "not assistant text"},
                    {"type": "text", "text": "provider answer"},
                ]
            },
        )

    client = MiniMaxAnthropicModelClient(
        api_key="test-value",
        base_url="https://provider.invalid/anthropic/",
        model="test-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.generate(
        [
            {"role": "summary", "text": "hot rolling summary"},
            {"role": "user", "text": "earlier user", "source": "hidden"},
            {"role": "assistant", "text": "earlier reply"},
            {"role": "user", "text": "bounded recall context"},
            {"role": "system", "text": "drop this"},
        ],
        "current user",
        system_prompt="chat background",
    )

    assert result == "provider answer"
    assert "not assistant text" not in result
    assert captured["url"] == "https://provider.invalid/anthropic/v1/messages"
    assert captured["headers"]["x-api-key"] == "test-value"
    assert captured["body"] == {
        "model": "test-model",
        "max_tokens": 1000,
        "system": "chat background\n\nhot rolling summary",
        "messages": [
            {"role": "user", "content": "earlier user"},
            {"role": "assistant", "content": "earlier reply"},
            {"role": "user", "content": "bounded recall context"},
            {"role": "user", "content": "current user"},
        ],
    }


def test_hot_draft_summarizer_does_not_receive_chat_background() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "new summary"}]},
        )

    client = MiniMaxAnthropicModelClient(
        api_key="test-value",
        base_url="https://provider.invalid/anthropic",
        model="test-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.summarize_hot_draft(
        "old summary",
        [MemoryTurn(role="user", text="archived raw turn")],
    )

    assert result == "new summary"
    body = captured["body"]
    assert "chat background marker" not in repr(body)
    assert json.loads(body["messages"][0]["content"]) == {
        "existing_summary": "old summary",
        "archived_turns": [
            {"role": "user", "text": "archived raw turn"},
        ],
    }


def test_hot_draft_summarizer_request_has_hard_output_budget() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "new summary"}]},
        )

    client = MiniMaxAnthropicModelClient(
        api_key="test-value",
        base_url="https://provider.invalid/anthropic",
        model="test-model",
        max_tokens=321,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.summarize_hot_draft(
        "old summary",
        [MemoryTurn(role="user", text="archived raw turn")],
    )

    body = captured["body"]
    assert body["max_tokens"] == 321
    assert body["temperature"] == 0.0


def test_provider_transport_and_invalid_body_errors_are_sanitized() -> None:
    def fail_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("raw provider detail")

    client = MiniMaxAnthropicModelClient(
        api_key="sensitive-test-value",
        base_url="https://provider.invalid/private",
        model="test-model",
        http_client=httpx.Client(transport=httpx.MockTransport(fail_transport)),
    )
    with pytest.raises(ModelClientError) as exc_info:
        client.generate([], "hello", system_prompt="chat background")
    assert str(exc_info.value) == "Provider request failed."
    assert "sensitive-test-value" not in str(exc_info.value)
    assert "provider.invalid" not in str(exc_info.value)

    invalid = MiniMaxAnthropicModelClient(
        api_key="sensitive-test-value",
        base_url="https://provider.invalid/private",
        model="test-model",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"content": []})
            )
        ),
    )
    with pytest.raises(ModelClientError, match="Provider response was invalid"):
        invalid.generate([], "hello", system_prompt="chat background")


def test_minimax_generate_includes_temperature_only_when_configured() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "answer"}]},
        )

    client = MiniMaxAnthropicModelClient(
        api_key="test-value",
        base_url="https://provider.invalid/anthropic",
        model="test-model",
        temperature=0.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.generate([], "hi", system_prompt="background")
    assert captured["body"]["temperature"] == 0.0

    default_client = MiniMaxAnthropicModelClient(
        api_key="test-value",
        base_url="https://provider.invalid/anthropic",
        model="test-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    default_client.generate([], "hi", system_prompt="background")
    assert "temperature" not in captured["body"]


def test_builder_temperature_override_passes_through(monkeypatch) -> None:
    import core.model_client as model_client_module

    captured = {}

    def build_minimax(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        model_client_module,
        "MiniMaxAnthropicModelClient",
        build_minimax,
    )
    build_model_client_from_env(
        {
            "LUMINA_MODEL_MODE": "real",
            "LUMINA_MODEL_PROVIDER": "minimax-anthropic",
            "LUMINA_MODEL_API_KEY": "test-value",
            "LUMINA_MODEL_BASE_URL": "https://provider.invalid/anthropic",
            "LUMINA_MODEL_NAME": "test-model",
        },
        temperature_override=0.0,
    )
    assert captured["temperature"] == 0.0
