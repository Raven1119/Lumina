from pathlib import Path
import json

import httpx
from fastapi.testclient import TestClient

from core.main import create_app
from core.model_client import MiniMaxAnthropicModelClient, MockModelClient


class _ContextModel:
    client_kind = "model"

    def __init__(self) -> None:
        self.contexts: list[list[dict[str, str]]] = []

    def generate(self, recent_context, user_message):
        self.contexts.append(recent_context)
        return f"answer:{user_message}"


class _FailingModel:
    client_kind = "model"

    def generate(self, recent_context, user_message):
        raise RuntimeError("key=private provider=https://private.invalid")


def _app(tmp_path: Path, model=None, **kwargs):
    return create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json",
        model_client=model or MockModelClient(),
        env_file_path=None,
        **kwargs,
    )


def test_status_and_mock_chat_contract(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    assert client.get("/api/status").json() == {
        "app": "lumina",
        "status": "ok",
        "mode": "mock",
        "draft_enabled": True,
    }
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 200
    assert response.json()["phase"] == "mock_chat"
    assert response.json()["response"]["type"] == "mock"
    assert client.get("/api/stream").status_code == 404


def test_static_frontend_is_served_without_shadowing_api(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    index = client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "Lumina Local Chat" in index.text

    script = client.get("/app.js")
    assert script.status_code == 200
    assert "fetch(\"/api/chat\"" in script.text
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in script.text

    stylesheet = client.get("/styles.css")
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")

    favicon = client.get("/favicon.svg")
    assert favicon.status_code == 200
    assert "image/svg+xml" in favicon.headers["content-type"]

    assert client.get("/api/status").json()["status"] == "ok"
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_chat_rejects_empty_message(tmp_path: Path) -> None:
    response = TestClient(_app(tmp_path)).post("/api/chat", json={"message": " "})
    assert response.status_code == 400
    assert response.json() == {"detail": "message is required"}


def test_model_chat_is_truthfully_labeled_and_receives_prior_turn(tmp_path: Path) -> None:
    model = _ContextModel()
    client = TestClient(_app(tmp_path, model))
    first = client.post("/api/chat", json={"message": "one"})
    second = client.post("/api/chat", json={"message": "two"})
    assert first.json()["phase"] == "model_chat"
    assert first.json()["response"] == {"type": "model", "text": "answer:one"}
    assert second.json()["response"] == {"type": "model", "text": "answer:two"}
    assert model.contexts[1] == [
        {"role": "user", "text": "one"},
        {"role": "assistant", "text": "answer:one"},
    ]


def test_real_adapter_fake_http_reaches_chat_without_thinking(tmp_path: Path) -> None:
    adapter = MiniMaxAnthropicModelClient(
        api_key="test-value",
        base_url="https://provider.invalid/anthropic",
        model="test-model",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "content": [
                            {"type": "thinking", "thinking": "hidden"},
                            {"type": "text", "text": "provider answer"},
                        ]
                    },
                )
            )
        ),
    )
    response = TestClient(_app(tmp_path, adapter)).post(
        "/api/chat",
        json={"message": "hello"},
    )
    assert response.json()["response"] == {
        "type": "model",
        "text": "provider answer",
    }
    assert "hidden" not in response.text
    assert "test-value" not in response.text
    assert "provider.invalid" not in response.text


def test_provider_failure_is_safe_fallback_without_internal_leak(tmp_path: Path) -> None:
    response = TestClient(_app(tmp_path, _FailingModel())).post(
        "/api/chat",
        json={"message": "hello"},
    )
    assert response.status_code == 200
    assert response.json()["phase"] == "model_chat"
    assert response.json()["response"]["type"] == "fallback"
    assert "private" not in response.text
    assert "provider" not in response.text
    assert str(tmp_path) not in response.text


def test_chat_compacts_to_cold_and_restart_restores_context(tmp_path: Path) -> None:
    first_model = _ContextModel()
    first_client = TestClient(
        _app(
            tmp_path,
            first_model,
            retain_recent_raw_turns=2,
            max_raw_turns_before_compression=2,
        )
    )
    first_client.post("/api/chat", json={"message": "one"})
    first_client.post("/api/chat", json={"message": "two"})
    assert (tmp_path / "cold.jsonl").exists()

    restarted_model = _ContextModel()
    restarted_client = TestClient(
        _app(
            tmp_path,
            restarted_model,
            retain_recent_raw_turns=2,
            max_raw_turns_before_compression=2,
        )
    )
    restarted_client.post("/api/chat", json={"message": "three"})
    context = restarted_model.contexts[0]
    assert context[0]["text"].startswith("[Compressed conversation segment")
    assert context[-2:] == [
        {"role": "user", "text": "two"},
        {"role": "assistant", "text": "answer:two"},
    ]


def test_chat_accepts_client_timezone_and_old_clients_still_default(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, default_timezone="Asia/Shanghai"))
    assert client.post(
        "/api/chat",
        json={"message": "with timezone", "client_timezone": "America/New_York"},
    ).status_code == 200
    assert client.post("/api/chat", json={"message": "old client"}).status_code == 200
    records = [
        json.loads(line)
        for line in (tmp_path / "hot.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all(item["source_timezone"] == "America/New_York" for item in records[:2])
    assert all(item["timezone_source"] == "client" for item in records[:2])
    assert all(item["source_timezone"] == "Asia/Shanghai" for item in records[2:])
    assert all(item["timezone_source"] == "configured_default" for item in records[2:])


def test_invalid_client_timezone_is_safe_fallback(tmp_path: Path) -> None:
    response = TestClient(_app(tmp_path)).post(
        "/api/chat",
        json={"message": "hello", "client_timezone": "not/a-zone"},
    )
    assert response.status_code == 200
    records = [
        json.loads(line)
        for line in (tmp_path / "hot.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all(item["source_timezone"] == "UTC" for item in records)
    assert all(item["timezone_source"] == "configured_default" for item in records)
