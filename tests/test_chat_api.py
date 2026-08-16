from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from Conversation_Memory.adapter.models import BackendCandidate, MemoryContext, RecallPolicy
from core import main as main_module
from Conversation_Memory.adapter import backend as memory_backend_module
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from ingestion.state_store import IngestionStateStore
from core.main import create_app
from core.model_client import MiniMaxAnthropicModelClient, MockModelClient
from Mind.constant_gate import ConstantMindGate
from Mind.llm_gate import LlmMindGate


class _ContextModel:
    client_kind = "model"

    def __init__(self) -> None:
        self.contexts: list[list[dict[str, str]]] = []
        self.messages: list[str] = []
        self.system_prompts: list[str] = []
        self.summary_calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.contexts.append(recent_context)
        self.messages.append(user_message)
        self.system_prompts.append(system_prompt)
        return f"answer:{user_message}"

    def summarize_hot_draft(self, old_summary, moved_turns):
        self.summary_calls.append((old_summary, list(moved_turns)))
        facts = "|".join(turn.text for turn in moved_turns)
        return f"{old_summary + '|' if old_summary else ''}{facts}"


class _FailingModel:
    client_kind = "model"

    def generate(self, recent_context, user_message, *, system_prompt):
        raise RuntimeError("key=private provider=https://private.invalid")


class _RecordingRetriever:
    def __init__(self, context=None, error: Exception | None = None) -> None:
        self.context = context
        self.error = error
        self.calls = []

    def recall(self, query, policy):
        self.calls.append((query, policy))
        if self.error is not None:
            raise self.error
        return self.context


class _SharedBackend:
    def __init__(self, started: Event | None = None, release: Event | None = None):
        self.events = {}
        self.started = started
        self.release = release

    def find_memory_id(self, evidence_id):
        for memory_id, event in self.events.items():
            if event["metadata"]["evidence_id"] == evidence_id:
                return memory_id
        return None

    def add_event(self, text, timestamp, metadata):
        if self.started is not None:
            self.started.set()
        if self.release is not None and not self.release.wait(timeout=5):
            raise RuntimeError("test synchronization timeout")
        memory_id = f"memory-{len(self.events)}"
        self.events[memory_id] = {
            "text": text,
            "timestamp": timestamp,
            "metadata": metadata,
        }
        return memory_id

    def create_relationships(self, memory_ids):
        return None

    def persist(self):
        return None

    def resolve_target_entity_ref(self, query):
        return None

    def recall(self, query, policy, target_entity_ref=None):
        return [
            BackendCandidate(
                event["text"],
                event["timestamp"].isoformat(),
                1.0 - index / 10,
                event["metadata"],
            )
            for index, event in enumerate(reversed(tuple(self.events.values())))
        ]


_WRITER_BUSY_RESPONSE = {
    "detail": {
        "code": "writer_busy",
        "message": "another write operation is in progress",
    }
}


def _app(tmp_path: Path, model=None, **kwargs):
    kwargs.setdefault("recall_enabled", False)
    kwargs.setdefault(
        "memory_retriever",
        _RecordingRetriever(MemoryContext("")),
    )
    # Pin the stage-1 gate: pre-existing tests assert Chat/Recall behavior,
    # not Mind gate selection. Default gate selection is covered by the
    # dedicated wiring tests below (they call create_app directly).
    kwargs.setdefault("mind_gate", ConstantMindGate())
    return create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json",
        model_client=model or MockModelClient(),
        env_file_path=None,
        **kwargs,
    )


def _shared_adapter(tmp_path: Path, backend=None):
    backend = backend or _SharedBackend()
    return MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "ingestion-state.json"),
        ingestion_version="grounded-span-v2",
    ), backend


def test_default_hot_path_and_shared_store_wiring(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("LUMINA_DRAFT_STORE_PATH", raising=False)
    app = create_app(
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json",
        model_client=MockModelClient(),
        env_file_path=None,
        recall_enabled=False,
        memory_retriever=_RecordingRetriever(MemoryContext("")),
    )
    hot_store = app.state.hot_draft_store
    history_reads = 0
    original_history_reader = hot_store.list_all_raw

    def read_history_hot():
        nonlocal history_reads
        history_reads += 1
        return original_history_reader()

    hot_store.list_all_raw = read_history_hot
    assert hot_store._path == Path("data/draft/hot_drafts.jsonl")
    assert app.state.message_runtime._hot_store is hot_store
    assert app.state.hot_draft_compactor._hot_store is hot_store
    assert TestClient(app).get("/api/history").status_code == 200
    assert history_reads == 1


def test_default_app_separates_chat_and_formation_model_clients(
    tmp_path: Path,
    monkeypatch,
) -> None:
    chat_model = _ContextModel()
    formation_model = _ContextModel()
    captured_formation_models = []

    monkeypatch.setattr(
        main_module,
        "build_model_client_from_env",
        lambda: chat_model,
    )
    monkeypatch.setattr(
        main_module,
        "build_formation_model_client",
        lambda: formation_model,
        raising=False,
    )

    def build_memory(model=None):
        captured_formation_models.append(model)
        return None

    monkeypatch.setattr(main_module, "_build_memory_retriever", build_memory)

    app = create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json",
        env_file_path=None,
        recall_enabled=False,
        memory_retriever=None,
    )

    assert app.state.message_runtime._model_client is chat_model
    assert captured_formation_models == [formation_model]


def _wired_app(tmp_path: Path, model, **kwargs):
    """create_app without a mind_gate override, for gate-selection tests."""
    kwargs.setdefault("recall_enabled", False)
    kwargs.setdefault(
        "memory_retriever",
        _RecordingRetriever(MemoryContext("")),
    )
    return create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json",
        model_client=model,
        env_file_path=None,
        **kwargs,
    )


def test_real_model_defaults_to_llm_mind_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("LUMINA_MIND_GATE_MODE", raising=False)
    gate_model = _ContextModel()

    def build_gate_client(*args, **kwargs):
        assert not args
        assert kwargs == {
            "model_name_override": "MiniMax-M3",
            "max_tokens_override": 8,
            "temperature_override": 0.0,
        }
        return gate_model

    monkeypatch.setattr(
        main_module, "build_model_client_from_env", build_gate_client
    )

    app = _wired_app(tmp_path, _ContextModel())

    gate = app.state.message_runtime._mind_gate
    assert isinstance(gate, LlmMindGate)
    assert gate.prompt_version == "mind-gate-v2"
    assert gate._model_client is gate_model


@pytest.mark.parametrize("mode", [None, "llm"])
def test_mock_mode_keeps_constant_mind_gate(
    tmp_path: Path,
    monkeypatch,
    mode: str | None,
) -> None:
    if mode is None:
        monkeypatch.delenv("LUMINA_MIND_GATE_MODE", raising=False)
    else:
        monkeypatch.setenv("LUMINA_MIND_GATE_MODE", mode)

    def forbidden_builder(*args, **kwargs):
        raise AssertionError("gate client must not be built in mock mode")

    monkeypatch.setattr(
        main_module, "build_model_client_from_env", forbidden_builder
    )

    app = _wired_app(tmp_path, MockModelClient())

    assert isinstance(app.state.message_runtime._mind_gate, ConstantMindGate)


def test_constant_mode_rolls_back_to_stage1_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "constant")

    def forbidden_builder(*args, **kwargs):
        raise AssertionError("gate client must not be built in constant mode")

    monkeypatch.setattr(
        main_module, "build_model_client_from_env", forbidden_builder
    )

    app = _wired_app(tmp_path, _ContextModel())

    assert isinstance(app.state.message_runtime._mind_gate, ConstantMindGate)


@pytest.mark.parametrize("failure", ["raises", "returns_mock"])
def test_llm_mode_without_real_gate_client_falls_back_to_constant(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "llm")
    if failure == "raises":

        def broken_builder(*args, **kwargs):
            raise RuntimeError("provider unavailable")

        builder = broken_builder
    else:

        def mock_builder(*args, **kwargs):
            return MockModelClient()

        builder = mock_builder
    monkeypatch.setattr(main_module, "build_model_client_from_env", builder)

    app = _wired_app(tmp_path, _ContextModel())

    assert isinstance(app.state.message_runtime._mind_gate, ConstantMindGate)


def test_default_llm_gate_serves_chat_with_single_recall_and_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("LUMINA_MIND_GATE_MODE", raising=False)

    class _TrueGateModel:
        client_kind = "model"

        def generate(self, recent_context, user_message, *, system_prompt):
            return "true"

    monkeypatch.setattr(
        main_module,
        "build_model_client_from_env",
        lambda *args, **kwargs: _TrueGateModel(),
    )
    model = _ContextModel()
    retriever = _RecordingRetriever(
        MemoryContext("hello", rendered_text="bounded memory")
    )
    app = _wired_app(
        tmp_path,
        model,
        recall_enabled=True,
        memory_retriever=retriever,
        mind_decision_log_path=tmp_path / "decisions.jsonl",
    )
    assert isinstance(app.state.message_runtime._mind_gate, LlmMindGate)
    client = TestClient(app)

    first = client.post("/api/chat", json={"message": "one"})
    second = client.post("/api/chat", json={"message": "two"})

    assert first.status_code == second.status_code == 200
    # Exactly one Recall per chat message, in message order; no duplicates.
    assert [call[0] for call in retriever.calls] == ["one", "two"]
    assert model.messages == ["one", "two"]
    records = [
        json.loads(line)
        for line in (tmp_path / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(records) == 2
    assert all(record["recall"] is True for record in records)


def test_chat_background_loads_once_and_requires_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    background_path = tmp_path / "chat_background.md"
    initial = "# Test background\n\nStable internal Markdown."
    background_path.write_text(
        "\ufeff \n" + initial + "\n\t",
        encoding="utf-8",
    )
    monkeypatch.setattr(main_module, "_CHAT_BACKGROUND_PATH", background_path)
    model = _ContextModel()
    app = _app(tmp_path / "first-app", model)
    background_path.write_text("replacement after restart", encoding="utf-8")
    client = TestClient(app)

    first = client.post("/api/chat", json={"message": "one"})
    second = client.post("/api/chat", json={"message": "two"})

    assert first.status_code == second.status_code == 200
    assert model.system_prompts == [initial, initial]
    persisted = (tmp_path / "first-app" / "hot.jsonl").read_text(
        encoding="utf-8"
    )
    assert initial not in persisted
    assert "replacement after restart" not in persisted
    public_text = first.text + second.text + client.get("/api/status").text
    assert initial not in public_text
    assert str(background_path) not in public_text

    restarted_model = _ContextModel()
    restarted_client = TestClient(
        _app(tmp_path / "restarted-app", restarted_model)
    )
    assert restarted_client.post(
        "/api/chat",
        json={"message": "after restart"},
    ).status_code == 200
    assert restarted_model.system_prompts == ["replacement after restart"]


@pytest.mark.parametrize("failure", ["missing", "empty", "unreadable", "invalid"])
def test_invalid_chat_background_fails_app_construction_safely(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    background_path = tmp_path / f"{failure}.md"
    if failure == "empty":
        background_path.write_text(" \n\t", encoding="utf-8")
    elif failure == "unreadable":
        background_path.mkdir()
    elif failure == "invalid":
        background_path.write_bytes(b"\xff\xfe\xfa")
    monkeypatch.setattr(main_module, "_CHAT_BACKGROUND_PATH", background_path)

    with pytest.raises(RuntimeError) as exc_info:
        _app(tmp_path / "app")

    rendered = str(exc_info.value)
    assert rendered in {
        "Chat background could not be loaded.",
        "Chat background is empty.",
    }
    assert str(background_path) not in rendered


def test_status_and_mock_chat_contract(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    assert client.get("/api/status").json() == {
        "app": "lumina",
        "status": "ok",
        "mode": "mock",
        "draft_enabled": True,
        "recall_enabled": False,
        "compaction": {"running": False},
        "dream": {
            "available": False,
            "running": False,
            "pending_segments": 0,
            "pending_truncated": False,
        },
    }
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 200
    assert response.json()["phase"] == "mock_chat"
    assert response.json()["response"]["type"] == "mock"
    assert response.json()["compaction"] == {
        "status": "not_needed",
        "archived_turns": 0,
        "summary_updated": False,
    }
    assert client.get("/api/stream").status_code == 404


def test_static_frontend_is_served_without_shadowing_api(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    index = client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "Lumina Local Chat" in index.text
    assert 'id="dream-button"' in index.text
    assert 'id="memory-status"' in index.text
    assert 'id="dream-result"' in index.text

    script = client.get("/app.js")
    assert script.status_code == 200
    assert "fetch(\"/api/chat\"" in script.text
    assert "fetch(\"/api/dream/run\"" in script.text
    assert "dreamButton.disabled" in script.text
    assert 'var url = "/api/history?limit=40"' in script.text
    assert "encodeURIComponent(historyNextBefore)" in script.text
    assert "historyLoading || (!initial && !historyHasMore)" in script.text
    assert "chatLog.scrollTop <= 125" in script.text
    assert "chatLog.insertBefore(fragment, chatLog.firstChild)" in script.text
    assert "oldTop + (chatLog.scrollHeight - oldHeight)" in script.text
    assert "createMessageBubble(turn.content, turn.role)" in script.text
    assert 'chatLog.addEventListener("scroll", handleHistoryScroll)' in script.text
    assert "payload.compaction.status" in script.text
    assert "compaction.archived_turns" in script.text
    assert 'compaction.status === "completed"' in script.text
    assert 'compaction.status === "failed"' in script.text
    assert "payload.compaction.running === true" in script.text
    assert "setInterval(function ()" in script.text
    assert "}, 500);" in script.text
    assert script.text.count("setInterval(") == 1
    assert "clearInterval(compactionPollTimer)" in script.text
    assert "activeCompactionPollGeneration !== generation" in script.text
    poll_function = script.text[
        script.text.index("function pollCompactionStatus(generation)")
        :script.text.index("function startCompactionPolling(generation)")
    ]
    assert poll_function.index(
        "activeCompactionPollGeneration !== generation"
    ) < poll_function.index('fetch("/api/status")')
    assert (
        "Hot Draft \\u6b63\\u5728\\u538b\\u7f29"
        "\\uff0c\\u8bf7\\u7a0d\\u5019\\u2026\\u2026"
    ) in script.text
    assert "return checkBackend();" in script.text
    assert "WebSocket" not in script.text
    assert "EventSource" not in script.text
    assert "textContent" in script.text
    assert "innerHTML" not in script.text
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
    client = TestClient(_app(tmp_path))
    response = client.post("/api/chat", json={"message": " "})
    assert response.status_code == 400
    assert response.json() == {"detail": "message is required"}
    assert client.post("/api/chat", json={"message": "after empty"}).status_code == 200


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
    first = first_client.post("/api/chat", json={"message": "one"})
    second = first_client.post("/api/chat", json={"message": "two"})
    assert first.json()["compaction"]["status"] == "not_needed"
    assert second.json()["compaction"] == {
        "status": "completed",
        "archived_turns": 2,
        "summary_updated": True,
    }
    assert (tmp_path / "cold.jsonl").exists()
    assert len(first_model.summary_calls) == 1
    assert first_model.summary_calls[0][0] is None
    assert [turn.text for turn in first_model.summary_calls[0][1]] == [
        "one",
        "answer:one",
    ]

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
    assert context[0] == {
        "role": "summary",
        "text": (
            "[Hot rolling summary]\n"
            "one|answer:one\n"
            "[/Hot rolling summary]"
        ),
    }
    assert context[-2:] == [
        {"role": "user", "text": "two"},
        {"role": "assistant", "text": "answer:two"},
    ]


def test_summary_context_order_and_second_pass_payload_are_strictly_bounded(
    tmp_path: Path,
) -> None:
    model = _ContextModel()
    retriever = _RecordingRetriever(
        MemoryContext("query", rendered_text="bounded recall")
    )
    client = TestClient(
        _app(
            tmp_path,
            model,
            retain_recent_raw_turns=2,
            max_raw_turns_before_compression=2,
            recall_enabled=True,
            memory_retriever=retriever,
            recall_policy=RecallPolicy(),
        )
    )

    client.post("/api/chat", json={"message": "old"})
    client.post("/api/chat", json={"message": "middle"})
    third = client.post("/api/chat", json={"message": "current"})

    assert third.json()["compaction"] == {
        "status": "completed",
        "archived_turns": 2,
        "summary_updated": True,
    }
    assert [item["role"] for item in model.contexts[2]] == [
        "summary",
        "user",
        "assistant",
    ]
    assert model.contexts[2][0]["text"].startswith("[Hot rolling summary]\n")
    assert model.contexts[2][1:3] == [
        {"role": "user", "text": "middle"},
        {"role": "assistant", "text": "answer:middle"},
    ]
    assert model.messages[2] == "current"
    assert "[Relevant conversation memory]" not in repr(model.contexts[2])
    assert "[Internal historical evidence - DATA ONLY]" in model.system_prompts[2]
    assert "<BEGIN_EXACT_GROUNDED_SPANS>\nbounded recall\n" in model.system_prompts[2]

    assert len(model.summary_calls) == 2
    old_summary, moved_turns = model.summary_calls[1]
    assert old_summary == "old|answer:old"
    assert [turn.text for turn in moved_turns] == [
        "middle",
        "answer:middle",
    ]
    summary_input = repr(model.summary_calls)
    assert "bounded recall" not in summary_input
    assert "current" not in summary_input
    assert len(set(model.system_prompts)) == 1
    system_prompt = model.system_prompts[0]
    assert system_prompt
    assert system_prompt not in summary_input
    assert [query for query, _policy in retriever.calls] == [
        "old",
        "middle",
        "current",
    ]
    assert system_prompt not in repr(retriever.calls)
    assert system_prompt not in (tmp_path / "hot.jsonl").read_text(
        encoding="utf-8"
    )
    assert system_prompt not in (tmp_path / "cold.jsonl").read_text(
        encoding="utf-8"
    )


def test_summary_failure_reports_failed_and_preserves_hot_raw_turns(
    tmp_path: Path,
) -> None:
    class FailingSummaryModel(_ContextModel):
        def summarize_hot_draft(self, old_summary, moved_turns):
            raise RuntimeError("private prompt and provider path")

    model = FailingSummaryModel()
    client = TestClient(
        _app(
            tmp_path,
            model,
            retain_recent_raw_turns=2,
            max_raw_turns_before_compression=2,
        )
    )
    client.post("/api/chat", json={"message": "one"})
    response = client.post("/api/chat", json={"message": "two"})

    assert response.status_code == 200
    assert response.json()["response"] == {
        "type": "model",
        "text": "answer:two",
    }
    assert response.json()["compaction"] == {
        "status": "failed",
        "archived_turns": 0,
        "summary_updated": False,
    }
    assert not (tmp_path / "cold.jsonl").exists()
    records = [
        json.loads(line)
        for line in (tmp_path / "hot.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["text"] for record in records] == [
        "one",
        "answer:one",
        "two",
        "answer:two",
    ]
    for leaked in ("private prompt", "provider path"):
        assert leaked not in response.text


def test_status_observes_running_compaction_without_waiting_for_chat(
    tmp_path: Path,
) -> None:
    started = Event()
    release = Event()

    class BlockingSummaryModel(_ContextModel):
        def summarize_hot_draft(self, old_summary, moved_turns):
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test synchronization timeout")
            return super().summarize_hot_draft(old_summary, moved_turns)

    model = BlockingSummaryModel()
    app = _app(
        tmp_path,
        model,
        retain_recent_raw_turns=2,
        max_raw_turns_before_compression=2,
    )
    first_client = TestClient(app)
    status_client = TestClient(app)
    assert first_client.post(
        "/api/chat",
        json={"message": "one"},
    ).json()["compaction"]["status"] == "not_needed"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            first_client.post,
            "/api/chat",
            json={"message": "two"},
        )
        assert started.wait(timeout=5)
        try:
            running = status_client.get("/api/status")
            assert running.status_code == 200
            assert running.json()["compaction"] == {"running": True}
            for forbidden in (
                "one",
                "answer:one",
                "two",
                "answer:two",
                str(tmp_path),
            ):
                assert forbidden not in running.text
        finally:
            release.set()
        response = future.result(timeout=5)

    assert response.json()["compaction"] == {
        "status": "completed",
        "archived_turns": 2,
        "summary_updated": True,
    }
    assert status_client.get("/api/status").json()["compaction"] == {
        "running": False
    }


def test_status_reports_compaction_not_running_when_disabled(
    tmp_path: Path,
) -> None:
    response = TestClient(
        _app(tmp_path, enable_compaction=False)
    ).get("/api/status")
    assert response.status_code == 200
    assert response.json()["compaction"] == {"running": False}


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


def test_recall_flag_defaults_on_and_accepts_explicit_values() -> None:
    assert main_module._recall_enabled(None) is True
    assert all(
        main_module._recall_enabled(value)
        for value in ("1", "true", "TRUE", " yes ", "on")
    )
    assert not any(
        main_module._recall_enabled(value)
        for value in ("", "0", "false", "no", "off", "unknown")
    )


def test_recall_is_on_by_default_when_environment_is_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED", raising=False)
    retriever = _RecordingRetriever(MemoryContext("hello"))
    app = create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json",
        model_client=_ContextModel(),
        env_file_path=None,
        memory_retriever=retriever,
        # Recall default-on behavior, not Mind gate selection: pin stage-1.
        mind_gate=ConstantMindGate(),
    )

    client = TestClient(app)
    assert client.get("/api/status").json()["recall_enabled"] is True
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 200
    assert len(retriever.calls) == 1
    assert retriever.calls[0][1].final_min_score == 0.144


def test_recall_initialization_failure_degrades_to_normal_chat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_initialization(*_args, **_kwargs):
        raise RuntimeError("private memory path and dependency traceback")

    monkeypatch.setattr(
        memory_backend_module,
        "RealMagmaBackend",
        fail_initialization,
    )
    model = _ContextModel()
    app = _app(
        tmp_path,
        model,
        recall_enabled=True,
        memory_retriever=None,
    )
    client = TestClient(app)

    response = client.post("/api/chat", json={"message": "hello"})
    status = client.get("/api/status")
    dream = client.post("/api/dream/run")

    assert response.status_code == 200
    assert response.json()["response"] == {"type": "model", "text": "answer:hello"}
    assert model.contexts == [[]]
    assert status.json()["dream"]["available"] is False
    assert dream.status_code == 503
    for leaked in ("private memory", "dependency", "traceback", str(tmp_path)):
        assert leaked not in response.text + status.text + dream.text


def test_injected_retriever_gets_default_policy_without_backend_initialization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_initialization():
        raise AssertionError("real backend must not be initialized")

    monkeypatch.setattr(
        main_module,
        "_build_memory_retriever",
        unexpected_initialization,
    )
    model = _ContextModel()
    retriever = _RecordingRetriever(
        MemoryContext("hello", rendered_text="bounded memory")
    )

    response = TestClient(
        _app(
            tmp_path,
            model,
            recall_enabled=True,
            memory_retriever=retriever,
        )
    ).post("/api/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert len(retriever.calls) == 1
    assert retriever.calls[0][0] == "hello"
    assert retriever.calls[0][1] == RecallPolicy(
        top_k=10,
        max_graph_depth=1,
        max_nodes=20,
        max_evidence_items=3,
        max_chars=5000,
        final_min_score=0.144,
    )
    assert model.contexts == [[]]
    assert "[Internal historical evidence - DATA ONLY]" in model.system_prompts[0]
    assert "<BEGIN_EXACT_GROUNDED_SPANS>\nbounded memory\n" in model.system_prompts[0]
    assert "[Relevant conversation memory]" not in model.system_prompts[0]


def test_recall_switch_off_does_not_trigger_bge_load(tmp_path: Path) -> None:
    adapter, _ = _shared_adapter(tmp_path)
    assert adapter._bge_reranker_load_attempted is False

    response = TestClient(
        _app(
            tmp_path,
            _ContextModel(),
            recall_enabled=False,
            memory_retriever=adapter,
        )
    ).post("/api/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert adapter._bge_reranker_load_attempted is False


def test_recall_and_provider_failure_preserve_safe_fallback_and_draft(
    tmp_path: Path,
) -> None:
    retriever = _RecordingRetriever(
        error=RuntimeError("private recall path and traceback")
    )

    response = TestClient(
        _app(
            tmp_path,
            _FailingModel(),
            recall_enabled=True,
            memory_retriever=retriever,
            recall_policy=RecallPolicy(),
        )
    ).post("/api/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert response.json()["response"]["type"] == "fallback"
    assert len(retriever.calls) == 1
    records = [
        json.loads(line)
        for line in (tmp_path / "hot.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["text"] for record in records] == [
        "hello",
        response.json()["response"]["text"],
    ]
    for secret in ("private", "provider", "traceback", str(tmp_path)):
        assert secret not in response.text


def test_dream_endpoint_uses_fixed_policy_and_returns_aggregate_only(tmp_path: Path) -> None:
    adapter, _ = _shared_adapter(tmp_path)
    app = _app(tmp_path, memory_retriever=adapter)
    for index in range(11):
        app.state.cold_draft_store.append_segment(
            [{"role": "user", "text": f"memory {index}"}],
            segment_id=f"segment-{index}",
        )

    response = TestClient(app).post(
        "/api/dream/run",
        json={"max_segments": 1, "ingestion_version": "client-value"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "attempted": 10,
        "ingested": 10,
        "consumed": 10,
        "skipped": 0,
        "failed": 0,
    }
    assert "results" not in response.text
    assert len(app.state.cold_draft_store.list_pending()) == 1


def test_dream_attempted_zero_is_success(tmp_path: Path) -> None:
    adapter, _ = _shared_adapter(tmp_path)
    response = TestClient(
        _app(tmp_path, memory_retriever=adapter)
    ).post("/api/dream/run")

    assert response.status_code == 200
    assert response.json() == {
        "attempted": 0,
        "ingested": 0,
        "consumed": 0,
        "skipped": 0,
        "failed": 0,
    }


def test_dream_uses_app_cold_owner_and_current_chat_memory_backend(tmp_path: Path) -> None:
    adapter, backend = _shared_adapter(tmp_path)

    class _FixedReranker:
        def score(self, query, candidate_texts):
            return [1.0 for _text in candidate_texts]

    adapter._bge_reranker = _FixedReranker()
    adapter._bge_reranker_load_attempted = True
    model = _ContextModel()
    app = _app(
        tmp_path,
        model,
        recall_enabled=True,
        memory_retriever=adapter,
        recall_policy=RecallPolicy(max_graph_depth=0),
    )
    app.state.cold_draft_store.append_segment(
        [{"role": "user", "text": "shared backend memory"}],
        segment_id="shared-segment",
    )
    client = TestClient(app)

    dream = client.post("/api/dream/run")
    chat = client.post("/api/chat", json={"message": "find shared memory"})

    assert dream.json() == {
        "attempted": 1,
        "ingested": 1,
        "consumed": 1,
        "skipped": 0,
        "failed": 0,
    }
    assert len(backend.events) == 1
    assert app.state.cold_draft_store.list_pending() == []
    assert chat.status_code == 200
    assert model.contexts[0] == []
    assert "shared backend memory" in model.system_prompts[0]
    assert "[Internal historical evidence - DATA ONLY]" in model.system_prompts[0]


def test_chat_background_is_not_persisted_by_compaction_dream_or_memory(
    tmp_path: Path,
) -> None:
    adapter, backend = _shared_adapter(tmp_path)
    model = _ContextModel()
    app = _app(
        tmp_path,
        model,
        memory_retriever=adapter,
        retain_recent_raw_turns=2,
        max_raw_turns_before_compression=2,
    )
    client = TestClient(app)

    client.post("/api/chat", json={"message": "one"})
    second = client.post("/api/chat", json={"message": "two"})
    assert second.json()["compaction"]["status"] == "completed"
    assert len(set(model.system_prompts)) == 1
    chat_background = model.system_prompts[0]
    assert chat_background not in repr(model.summary_calls)
    assert chat_background not in (tmp_path / "hot.jsonl").read_text(
        encoding="utf-8"
    )
    assert chat_background not in (tmp_path / "cold.jsonl").read_text(
        encoding="utf-8"
    )

    dream = client.post("/api/dream/run")

    assert dream.json()["attempted"] == 1
    assert dream.json()["consumed"] == 1
    assert chat_background not in repr(backend.events)
    assert chat_background not in (tmp_path / "ingestion-state.json").read_text(
        encoding="utf-8"
    )
    assert chat_background not in dream.text


def test_status_reports_bounded_safe_dream_state(tmp_path: Path) -> None:
    adapter, _ = _shared_adapter(tmp_path)
    app = _app(
        tmp_path,
        recall_enabled=True,
        memory_retriever=adapter,
    )
    app.state.cold_draft_store.append_segment(
        [{"role": "user", "text": r"private Cold text C:\private\draft"}],
        segment_id="secret-segment",
    )

    response = TestClient(app).get("/api/status")

    assert response.status_code == 200
    assert response.json()["recall_enabled"] is True
    assert response.json()["dream"] == {
        "available": True,
        "running": False,
        "pending_segments": 1,
        "pending_truncated": False,
    }
    for forbidden in ("private Cold text", "secret-segment", str(tmp_path)):
        assert forbidden not in response.text


def test_dream_unavailable_is_safe_503_and_chat_still_works(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)

    response = client.post("/api/dream/run")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "dream_unavailable",
            "message": "Dream is unavailable",
        }
    }
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 200


def test_dream_holds_writer_lock_against_dream_and_chat(tmp_path: Path) -> None:
    started = Event()
    release = Event()
    adapter, _ = _shared_adapter(tmp_path, _SharedBackend(started, release))
    app = _app(tmp_path, memory_retriever=adapter)
    app.state.cold_draft_store.append_segment(
        [{"role": "user", "text": "blocking memory"}],
        segment_id="blocking-segment",
    )
    first_client = TestClient(app)
    second_client = TestClient(app)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first_client.post, "/api/dream/run")
        assert started.wait(timeout=5)
        assert second_client.get("/api/status").json()["dream"]["running"] is True
        second_dream = second_client.post("/api/dream/run")
        blocked_chat = second_client.post(
            "/api/chat", json={"message": "busy"}
        )
        assert second_dream.status_code == 409
        assert second_dream.json() == _WRITER_BUSY_RESPONSE
        assert blocked_chat.status_code == 409
        assert blocked_chat.json() == _WRITER_BUSY_RESPONSE
        release.set()
        assert future.result(timeout=5).status_code == 200

    assert second_client.get("/api/status").json()["dream"]["running"] is False
    assert second_client.post(
        "/api/chat", json={"message": "after"}
    ).status_code == 200


def test_chat_holds_writer_lock_against_chat_and_dream(tmp_path: Path) -> None:
    started = Event()
    release = Event()

    class BlockingModel:
        client_kind = "model"

        def generate(self, recent_context, user_message, *, system_prompt):
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test synchronization timeout")
            return f"answer:{user_message}"

    adapter, _ = _shared_adapter(tmp_path)
    app = _app(tmp_path, BlockingModel(), memory_retriever=adapter)
    first_client = TestClient(app)
    second_client = TestClient(app)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            first_client.post,
            "/api/chat",
            json={"message": "blocking chat"},
        )
        assert started.wait(timeout=5)
        second_chat = second_client.post(
            "/api/chat", json={"message": "second chat"}
        )
        blocked_dream = second_client.post("/api/dream/run")
        assert second_chat.status_code == 409
        assert second_chat.json() == _WRITER_BUSY_RESPONSE
        assert blocked_dream.status_code == 409
        assert blocked_dream.json() == _WRITER_BUSY_RESPONSE
        release.set()
        assert future.result(timeout=5).status_code == 200


def test_unexpected_dream_failure_releases_lock_and_hides_details(tmp_path: Path) -> None:
    adapter, _ = _shared_adapter(tmp_path)
    app = _app(tmp_path, memory_retriever=adapter)
    original_runner = app.state.dream_runner

    class ExplodingRunner:
        def run_once(self, policy):
            raise RuntimeError(r"secret C:\private\graph traceback")

    app.state.dream_runner = ExplodingRunner()
    client = TestClient(app)
    response = client.post("/api/dream/run")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "dream_failed",
            "message": "Dream could not complete",
        }
    }
    assert app.state.dream_running is False
    for forbidden in ("secret", "private", "traceback"):
        assert forbidden not in response.text
    assert client.post("/api/chat", json={"message": "after failure"}).status_code == 200
    app.state.dream_runner = original_runner
    assert client.post("/api/dream/run").status_code == 200
