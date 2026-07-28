from pathlib import Path
from datetime import UTC, datetime, timedelta

from Conversation_Memory.adapter.models import (
    MemoryContext,
    MemoryEvidence,
    RecallPolicy,
    SourceProvenance,
)
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


class _RecordingRetriever:
    def __init__(self, context=None, error: Exception | None = None) -> None:
        self.context = context
        self.error = error
        self.calls: list[tuple[str, RecallPolicy]] = []

    def recall(self, query, policy):
        self.calls.append((query, policy))
        if self.error is not None:
            raise self.error
        return self.context


class _LeakyMemoryContext:
    def __init__(self, evidence: MemoryEvidence) -> None:
        self.rendered_text = "safe bounded memory"
        self.evidence = (evidence,)
        self.backend_score = "secret-backend-score"
        self.internal_marker = "secret-internal-marker"

    def __repr__(self) -> str:
        return "secret-context-repr"


class _SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values)

    def now(self) -> datetime:
        return self.values.pop(0)


class _SequenceIds:
    def __init__(self, *values: str) -> None:
        self.values = list(values)

    def new_id(self) -> str:
        return self.values.pop(0)


def _runtime(
    tmp_path: Path,
    model=None,
    *,
    compact: bool = False,
    **runtime_kwargs,
) -> tuple[MessageRuntime, JsonlDraftStore, ColdDraftStore]:
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
            **runtime_kwargs,
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


def test_turns_get_distinct_injected_ids_times_and_client_timezone(tmp_path: Path) -> None:
    user_time = datetime(2026, 7, 15, 3, 55, tzinfo=UTC)
    assistant_time = user_time + timedelta(seconds=3)
    runtime, hot, _ = _runtime(
        tmp_path,
        _RecordingModel("same"),
        clock=_SequenceClock(user_time, assistant_time),
        turn_id_factory=_SequenceIds("turn-user", "turn-assistant"),
    )
    runtime.handle_chat(
        ChatRequest(message="same", client_timezone="America/New_York")
    )
    turns = hot.list_recent(2)
    assert [turn.turn_id for turn in turns] == ["turn-user", "turn-assistant"]
    assert [turn.created_at for turn in turns] == [user_time, assistant_time]
    assert all(turn.source_timezone == "America/New_York" for turn in turns)
    assert all(turn.timezone_source == "client" for turn in turns)


def test_missing_or_invalid_client_timezone_uses_configured_default(tmp_path: Path) -> None:
    base = datetime(2026, 7, 14, tzinfo=UTC)
    runtime, hot, _ = _runtime(
        tmp_path,
        clock=_SequenceClock(base, base, base, base),
        turn_id_factory=_SequenceIds("u1", "a1", "u2", "a2"),
        default_timezone="Asia/Shanghai",
    )
    runtime.handle_chat(ChatRequest(message="missing"))
    runtime.handle_chat(ChatRequest(message="invalid", client_timezone="Mars/Olympus"))
    turns = hot.list_recent(4)
    assert all(turn.source_timezone == "Asia/Shanghai" for turn in turns)
    assert all(turn.timezone_source == "configured_default" for turn in turns)


def test_recall_is_disabled_by_default_and_draft_behavior_is_unchanged(
    tmp_path: Path,
) -> None:
    model = _RecordingModel()
    retriever = _RecordingRetriever(
        MemoryContext("hello", rendered_text="must not be used")
    )
    runtime, hot, _ = _runtime(
        tmp_path,
        model,
        memory_retriever=retriever,
        recall_policy=RecallPolicy(),
    )

    runtime.handle_chat(ChatRequest(message="hello"))

    assert retriever.calls == []
    assert model.contexts == [[]]
    assert model.messages == ["hello"]
    assert [turn.text for turn in hot.list_recent(2)] == ["hello", "model answer"]


def test_enabled_recall_injects_only_bounded_rendered_text(
    tmp_path: Path,
) -> None:
    model = _RecordingModel()
    policy = RecallPolicy()
    retriever = _RecordingRetriever(
        MemoryContext("remember", rendered_text="bounded memory text")
    )
    runtime, hot, _ = _runtime(
        tmp_path,
        model,
        recall_enabled=True,
        memory_retriever=retriever,
        recall_policy=policy,
    )

    result = runtime.handle_chat(ChatRequest(message="remember"))

    assert retriever.calls == [("remember", policy)]
    assert model.contexts == [[{
        "role": "user",
        "text": (
            "[Relevant conversation memory]\n"
            "bounded memory text\n"
            "[/Relevant conversation memory]"
        ),
    }]]
    assert model.messages == ["remember"]
    assert result.recent_context == []
    assert [turn.text for turn in hot.list_recent(2)] == [
        "remember",
        "model answer",
    ]
    assert "[Relevant conversation memory]" not in (
        tmp_path / "hot.jsonl"
    ).read_text(encoding="utf-8")


def test_enabled_empty_recall_is_normal_chat_without_empty_block(
    tmp_path: Path,
) -> None:
    model = _RecordingModel()
    retriever = _RecordingRetriever(
        MemoryContext("hello", rendered_text=" \n\t")
    )
    runtime, _, _ = _runtime(
        tmp_path,
        model,
        recall_enabled=True,
        memory_retriever=retriever,
        recall_policy=RecallPolicy(),
    )

    result = runtime.handle_chat(ChatRequest(message="hello"))

    assert len(retriever.calls) == 1
    assert model.contexts == [[]]
    assert result.response.response.text == "model answer"


def test_recall_exception_falls_back_to_normal_model_and_draft_flow(
    tmp_path: Path,
) -> None:
    model = _RecordingModel()
    retriever = _RecordingRetriever(
        error=RuntimeError("private recall path and traceback")
    )
    runtime, hot, _ = _runtime(
        tmp_path,
        model,
        recall_enabled=True,
        memory_retriever=retriever,
        recall_policy=RecallPolicy(),
    )

    result = runtime.handle_chat(ChatRequest(message="hello"))

    assert len(retriever.calls) == 1
    assert model.contexts == [[]]
    assert result.response.response.text == "model answer"
    assert [turn.text for turn in hot.list_recent(2)] == [
        "hello",
        "model answer",
    ]
    assert "private recall" not in str(result)
    assert "traceback" not in str(result)


def test_recall_dto_and_internal_fields_cannot_leak_into_model_or_draft(
    tmp_path: Path,
) -> None:
    provenance = SourceProvenance(
        segment_id="secret-segment-id",
        conversation_id="secret-conversation-id",
        turn_id="secret-turn-id",
        source_timestamp="2026-07-15T00:00:00+00:00",
        source_timezone="UTC",
        ingestion_version="secret-ingestion-version",
    )
    evidence = MemoryEvidence(
        evidence_id="secret-evidence-id",
        text="secret-raw-evidence-text",
        timestamp="2026-07-15T00:00:00+00:00",
        provenance=provenance,
    )
    model = _RecordingModel()
    retriever = _RecordingRetriever(_LeakyMemoryContext(evidence))
    runtime, _, _ = _runtime(
        tmp_path,
        model,
        recall_enabled=True,
        memory_retriever=retriever,
        recall_policy=RecallPolicy(),
    )

    runtime.handle_chat(ChatRequest(message="hello"))

    model_input = repr(model.contexts)
    draft_text = (tmp_path / "hot.jsonl").read_text(encoding="utf-8")
    assert "safe bounded memory" in model_input
    for secret in (
        "secret-evidence-id",
        "secret-segment-id",
        "secret-conversation-id",
        "secret-turn-id",
        "secret-ingestion-version",
        "secret-raw-evidence-text",
        "secret-backend-score",
        "secret-internal-marker",
        "secret-context-repr",
    ):
        assert secret not in model_input
        assert secret not in draft_text
