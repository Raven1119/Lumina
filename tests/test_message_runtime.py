from pathlib import Path

from core.cold_draft_store import ColdDraftStore
from core.contracts import ChatRequest, MemoryTurn
from core.draft_context import DraftContextProvider
from core.draft_store import JsonlDraftStore
from core.hot_draft_compactor import HotDraftCompactor
from core.message_runtime import MessageRuntime
from core.model_client import MOCK_ASSISTANT_TEXT, MockModelClient


class _RecordingModel:
    client_kind = "model"

    def __init__(self, answer: str = "model answer") -> None:
        self.answer = answer
        self.contexts: list[list[dict[str, str]]] = []
        self.messages: list[str] = []

    def generate(self, recent_context, user_message):
        self.contexts.append(recent_context)
        self.messages.append(user_message)
        return self.answer


class _FailingModel:
    client_kind = "model"

    def generate(self, recent_context, user_message):
        raise RuntimeError("provider private URL and key")


def _runtime(tmp_path: Path, model=None, *, compact: bool = False) -> tuple[MessageRuntime, JsonlDraftStore, ColdDraftStore]:
    hot = JsonlDraftStore(tmp_path / "hot.jsonl")
    cold = ColdDraftStore(tmp_path / "cold.jsonl")
    compactor = None
    if compact:
        compactor = HotDraftCompactor(
            hot,
            cold,
            tmp_path / "state.json",
            retain_recent_raw_turns=2,
            max_raw_turns_before_compression=2,
        )
    return (
        MessageRuntime(
            hot_store=hot,
            draft_context_provider=DraftContextProvider(hot),
            model_client=model or MockModelClient(),
            compactor=compactor,
        ),
        hot,
        cold,
    )


def test_model_is_called_before_current_turn_is_written(tmp_path: Path) -> None:
    model = _RecordingModel()
    runtime, hot, _ = _runtime(tmp_path, model)
    runtime.handle_chat(ChatRequest(message="first"))
    runtime.handle_chat(ChatRequest(message="second"))
    assert model.contexts[0] == []
    assert model.contexts[1] == [
        {"role": "user", "text": "first"},
        {"role": "assistant", "text": "model answer"},
    ]
    assert {"role": "user", "text": "second"} not in model.contexts[1]
    assert [turn.role for turn in hot.list_recent(10)] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_mock_model_returns_truthful_mock_semantics(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    result = runtime.handle_chat(ChatRequest(message="hello"))
    assert result.response.phase == "mock_chat"
    assert result.response.response.type == "mock"


def test_model_failure_returns_and_persists_safe_fallback(tmp_path: Path) -> None:
    runtime, hot, _ = _runtime(tmp_path, _FailingModel())
    result = runtime.handle_chat(ChatRequest(message="hello"))
    assert result.response.phase == "model_chat"
    assert result.response.response.type == "fallback"
    assert result.response.response.text == MOCK_ASSISTANT_TEXT
    assert [turn.text for turn in hot.list_recent(10)] == [
        "hello",
        MOCK_ASSISTANT_TEXT,
    ]
    assert "provider private" not in str(result)


def test_compaction_runs_after_user_and_assistant_writes(tmp_path: Path) -> None:
    runtime, hot, cold = _runtime(tmp_path, _RecordingModel(), compact=True)
    runtime.handle_chat(ChatRequest(message="one"))
    result = runtime.handle_chat(ChatRequest(message="two"))
    assert result.events[-1] == "compacted"
    assert len(hot.list_recent(10)) == 4
    assert len(cold.list_pending()) == 1
    assert [turn["role"] for turn in cold.list_pending()[0]["turns"]] == [
        "user",
        "assistant",
    ]


class _FailingHotStore(JsonlDraftStore):
    def append_turn(self, turn: MemoryTurn) -> int:
        raise OSError("private draft path")


def test_draft_failure_does_not_leak_or_break_response(tmp_path: Path) -> None:
    hot = _FailingHotStore(tmp_path / "hot.jsonl")
    runtime = MessageRuntime(
        hot_store=hot,
        draft_context_provider=DraftContextProvider(hot),
        model_client=MockModelClient(),
    )
    result = runtime.handle_chat(ChatRequest(message="hello"))
    assert result.response.response.type == "mock"
    assert result.events[-2:] == ("draft_write_failed", "compaction_skipped")
    assert "private draft path" not in str(result)
