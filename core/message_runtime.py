"""Single synchronous chat path for the Cold Draft MVP."""

from core.contracts import (
    AssistantResponse,
    ChatRequest,
    ChatResponse,
    MemoryTurn,
    MessageRuntimeResult,
)
from core.draft_context import DraftContextProvider
from core.draft_store import JsonlDraftStore
from core.hot_draft_compactor import HotDraftCompactor
from core.model_client import MOCK_ASSISTANT_TEXT, ModelClient


class MessageRuntime:
    def __init__(
        self,
        *,
        hot_store: JsonlDraftStore,
        draft_context_provider: DraftContextProvider,
        model_client: ModelClient,
        compactor: HotDraftCompactor | None = None,
    ) -> None:
        self._hot_store = hot_store
        self._draft_context_provider = draft_context_provider
        self._model_client = model_client
        self._compactor = compactor

    def handle_chat(self, request: ChatRequest) -> MessageRuntimeResult:
        user_message = request.message if request.message is not None else request.text
        user_message = user_message or ""
        recent_context, context_event = self._load_context()
        assistant_text, response_type, phase, model_event = self._generate(
            recent_context,
            user_message,
        )

        response = ChatResponse(
            app="lumina",
            status="ok",
            phase=phase,
            message_consumed=True,
            response=AssistantResponse(type=response_type, text=assistant_text),
        )

        events = ["response", context_event, model_event]
        events.append(self._capture_turns(user_message, assistant_text))
        events.append(self._compact())
        return MessageRuntimeResult(
            response=response,
            recent_context=recent_context,
            events=tuple(event for event in events if event is not None),
        )

    def _load_context(self) -> tuple[list[dict[str, str]], str]:
        try:
            if self._compactor is not None:
                return self._compactor.get_context_turns(), "draft_context_read"
            return self._draft_context_provider.get_recent_context(), "draft_context_read"
        except Exception:
            return [], "draft_context_read_failed"

    def _generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
    ) -> tuple[str, str, str, str | None]:
        client_kind = getattr(self._model_client, "client_kind", "model")
        phase = "mock_chat" if client_kind == "mock" else "model_chat"
        response_type = "mock" if client_kind == "mock" else "model"
        try:
            text = self._model_client.generate(recent_context, user_message)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty model response")
            return text, response_type, phase, None
        except Exception:
            return MOCK_ASSISTANT_TEXT, "fallback", phase, "model_call_failed"

    def _capture_turns(self, user_message: str, assistant_text: str) -> str:
        succeeded = True
        for turn in (
            MemoryTurn(role="user", text=user_message),
            MemoryTurn(role="assistant", text=assistant_text),
        ):
            try:
                self._hot_store.append_turn(turn)
            except Exception:
                succeeded = False
        return "draft_write" if succeeded else "draft_write_failed"

    def _compact(self) -> str:
        if self._compactor is None:
            return "compaction_skipped"
        try:
            result = self._compactor.maybe_compact()
        except Exception:
            return "compaction_failed"
        if result.compacted:
            return "compacted"
        if result.status in {"cold_draft_failed", "hot_state_failed"}:
            return "compaction_failed"
        return "compaction_skipped"
