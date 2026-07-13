import json

import httpx
import pytest

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
    assert client.generate([], "hello") == MOCK_ASSISTANT_TEXT


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
            {"role": "user", "text": "earlier user", "source": "hidden"},
            {"role": "assistant", "text": "earlier reply"},
            {"role": "system", "text": "drop this"},
        ],
        "current user",
    )

    assert result == "provider answer"
    assert "not assistant text" not in result
    assert captured["url"] == "https://provider.invalid/anthropic/v1/messages"
    assert captured["headers"]["x-api-key"] == "test-value"
    assert captured["body"] == {
        "model": "test-model",
        "max_tokens": 1000,
        "messages": [
            {"role": "user", "content": "earlier user"},
            {"role": "assistant", "content": "earlier reply"},
            {"role": "user", "content": "current user"},
        ],
    }


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
        client.generate([], "hello")
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
        invalid.generate([], "hello")
