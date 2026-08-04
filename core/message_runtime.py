"""Single synchronous chat path for the Cold Draft MVP."""

from typing import TYPE_CHECKING

from core.contracts import (
    AssistantResponse,
    ChatCompactionResponse,
    ChatRequest,
    ChatResponse,
    MemoryTurn,
    MessageRuntimeResult,
)
from core.draft_context import DraftContextProvider
from core.draft_store import JsonlDraftStore
from core.hot_draft_compactor import HotDraftCompactor
from core.model_client import MOCK_ASSISTANT_TEXT, ModelClient
from core.turn_provenance import (
    Clock,
    DraftTurnFactory,
    TurnIdFactory,
    resolve_source_timezone,
)

if TYPE_CHECKING:
    from Conversation_Memory.adapter.interfaces import MemoryRetriever
    from Conversation_Memory.adapter.models import RecallPolicy


_MEMORY_CONTEXT_START = "[Relevant conversation memory]"
_MEMORY_CONTEXT_END = "[/Relevant conversation memory]"
_HOT_SUMMARY_START = "[Hot rolling summary]"
_HOT_SUMMARY_END = "[/Hot rolling summary]"


class MessageRuntime:
    def __init__(
        self,
        *,
        hot_store: JsonlDraftStore,
        draft_context_provider: DraftContextProvider,
        model_client: ModelClient,
        compactor: HotDraftCompactor | None = None,
        clock: Clock | None = None,
        turn_id_factory: TurnIdFactory | None = None,
        default_timezone: str = "UTC",
        recall_enabled: bool = False,
        memory_retriever: "MemoryRetriever | None" = None,
        recall_policy: "RecallPolicy | None" = None,
    ) -> None:
        self._hot_store = hot_store
        self._draft_context_provider = draft_context_provider
        self._model_client = model_client
        self._compactor = compactor
        self._recall_enabled = recall_enabled
        self._memory_retriever = memory_retriever
        self._recall_policy = recall_policy
        self._turn_factory = DraftTurnFactory(
            clock=clock,
            id_factory=turn_id_factory,
            default_timezone=default_timezone,
        )

    def handle_chat(self, request: ChatRequest) -> MessageRuntimeResult:
        user_message = request.message if request.message is not None else request.text
        user_message = user_message or ""
        source_timezone, timezone_source = resolve_source_timezone(
            request.client_timezone,
            self._turn_factory.default_timezone,
        )
        user_turn = self._turn_factory.create(
            role="user",
            text=user_message,
            source_timezone=source_timezone,
            timezone_source=timezone_source,
        )
        recent_context, context_event = self._load_context()
        model_context = self._with_memory_context(recent_context, user_message)
        assistant_text, response_type, phase, model_event = self._generate(
            model_context,
            user_message,
        )
        assistant_turn = self._turn_factory.create(
            role="assistant",
            text=assistant_text,
            source_timezone=source_timezone,
            timezone_source=timezone_source,
        )

        events = ["response", context_event, model_event]
        events.append(self._capture_turns(user_turn, assistant_turn))
        compaction, compaction_event = self._compact()
        events.append(compaction_event)
        response = ChatResponse(
            app="lumina",
            status="ok",
            phase=phase,
            message_consumed=True,
            response=AssistantResponse(type=response_type, text=assistant_text),
            compaction=compaction,
        )
        return MessageRuntimeResult(
            response=response,
            recent_context=recent_context,
            events=tuple(event for event in events if event is not None),
        )

    def _load_context(self) -> tuple[list[dict[str, str]], str]:
        try:
            recent = self._draft_context_provider.get_recent_context()
            summary = self._hot_store.read_summary()
            if summary is None:
                return recent, "draft_context_read"
            summary_block = (
                f"{_HOT_SUMMARY_START}\n"
                f"{summary.content}\n"
                f"{_HOT_SUMMARY_END}"
            )
            return [
                {"role": "summary", "text": summary_block},
                *recent,
            ], "draft_context_read"
        except Exception:
            return [], "draft_context_read_failed"

    def _with_memory_context(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
    ) -> list[dict[str, str]]:
        if (
            not self._recall_enabled
            or self._memory_retriever is None
            or self._recall_policy is None
        ):
            return recent_context
        try:
            memory_context = self._memory_retriever.recall(
                user_message,
                self._recall_policy,
            )
            rendered_text = memory_context.rendered_text
        except Exception:
            return recent_context
        if not isinstance(rendered_text, str) or not rendered_text.strip():
            return recent_context
        memory_block = (
            f"{_MEMORY_CONTEXT_START}\n"
            f"{rendered_text}\n"
            f"{_MEMORY_CONTEXT_END}"
        )
        return [*recent_context, {"role": "user", "text": memory_block}]

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

    def _capture_turns(
        self,
        user_turn: MemoryTurn,
        assistant_turn: MemoryTurn,
    ) -> str:
        succeeded = True
        for turn in (user_turn, assistant_turn):
            try:
                self._hot_store.append_turn(turn)
            except Exception:
                succeeded = False
        return "draft_write" if succeeded else "draft_write_failed"

    def _compact(self) -> tuple[ChatCompactionResponse, str]:
        if self._compactor is None:
            return ChatCompactionResponse(
                status="not_needed",
                archived_turns=0,
                summary_updated=False,
            ), "compaction_skipped"
        try:
            result = self._compactor.maybe_compact()
        except Exception:
            return ChatCompactionResponse(
                status="failed",
                archived_turns=0,
                summary_updated=False,
            ), "compaction_failed"
        response = ChatCompactionResponse(
            status=result.status,
            archived_turns=result.archived_turns,
            summary_updated=result.summary_updated,
        )
        if result.status == "completed":
            return response, "compacted"
        if result.status == "failed":
            return response, "compaction_failed"
        return response, "compaction_skipped"
