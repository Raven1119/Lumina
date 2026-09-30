"""One Chat path: Hot, local memory read, one Answer, trace, then compaction."""
from __future__ import annotations

from datetime import datetime

from Conversation_Memory.answer import (answer_messages, answer_system,
                                        fallback_answer_v5, parse_answer_v5_tolerant)
from Conversation_Memory.engine.types import RecallResult
from Conversation_Memory.facade import MemoryRead, MemoryV1
from core.contracts import (AssistantResponse, ChatCompactionResponse, ChatRequest,
                            ChatResponse, MessageRuntimeResult)
from core.draft_store import JsonlDraftStore
from core.hot_draft_compactor import HotDraftCompactor
from core.memory_adapter import draft_turns_to_memory
from core.model_client import MOCK_ASSISTANT_TEXT, ModelClient
from core.turn_provenance import (Clock, DraftTurnFactory, TurnIdFactory,
                                  resolve_source_timezone)


class MessageRuntime:
    def __init__(self, *, hot_store: JsonlDraftStore, model_client: ModelClient,
                 chat_background: str, memory: MemoryV1 | None = None,
                 recall_enabled: bool = True,
                 compactor: HotDraftCompactor | None = None,
                 clock: Clock | None = None,
                 turn_id_factory: TurnIdFactory | None = None,
                 default_timezone: str = 'Asia/Shanghai') -> None:
        self._hot_store = hot_store
        self._model_client = model_client
        self._background = chat_background
        self._memory = memory
        self._recall_enabled = recall_enabled
        self._compactor = compactor
        self._turn_factory = DraftTurnFactory(
            clock=clock, id_factory=turn_id_factory, default_timezone=default_timezone)

    def handle_chat(self, request: ChatRequest) -> MessageRuntimeResult:
        message = request.message if request.message is not None else request.text
        message = message or ''
        zone, source = resolve_source_timezone(request.client_timezone,
                                                self._turn_factory.default_timezone)
        user = self._turn_factory.create(role='user', text=message,
                                         source_timezone=zone, timezone_source=source)
        now = user.created_at
        context = self._hot_store.read_context()
        hot = draft_turns_to_memory(context.raw_turns, now)
        summary = context.summary
        until = (datetime.fromisoformat(summary.summary_until.replace('Z', '+00:00'))
                 if summary and summary.summary_until else None)
        read = MemoryRead('', (), RecallResult())
        events = ['draft_context_read']
        if self._memory is not None and self._recall_enabled:
            try:
                read = self._memory.recall_and_render(message, hot, now)
                events.append('memory_recalled')
            except Exception:
                events.append('memory_recall_failed')
        system = answer_system(now, read.block, self._background)
        messages = answer_messages({'message': message}, hot,
                                   summary.content if summary else None, until, now)
        phase = 'mock_chat' if getattr(self._model_client, 'client_kind', 'model') == 'mock' else 'model_chat'
        response_type = 'mock' if phase == 'mock_chat' else 'model'
        noticed = {'理解': '', '借鉴': '', '顺带': ''}
        try:
            if phase == 'mock_chat':
                raw = self._model_client.generate([], message, system_prompt=system)
            elif callable(getattr(self._model_client, 'complete_answer', None)):
                raw = self._model_client.complete_answer(system, messages)
            else:
                raw = self._model_client.generate(
                    [{'role': row['role'], 'text': row['content']} for row in messages[:-1]],
                    messages[-1]['content'], system_prompt=system)
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError('empty_answer')
            if phase == 'mock_chat':
                reply = raw
            else:
                reply, noticed, found, _, _ = parse_answer_v5_tolerant(raw)
                if not found:
                    reply = fallback_answer_v5(raw)
                if not reply.strip():
                    raise ValueError('empty_reply')
        except Exception:
            reply = MOCK_ASSISTANT_TEXT
            response_type = 'fallback'
            events.append('model_call_failed')
        assistant = self._turn_factory.create(role='assistant', text=reply,
                                               source_timezone=zone, timezone_source=source)
        for turn in (user, assistant):
            try:
                self._hot_store.append_turn(turn)
            except Exception:
                events.append('draft_write_failed')
        if self._memory is not None and self._recall_enabled:
            try:
                self._memory.record_trace(assistant.turn_id, assistant.created_at,
                                          read, noticed, reply)
                events.append('memory_trace_written')
            except Exception:
                events.append('memory_trace_failed')
        compaction = ChatCompactionResponse(status='not_needed', archived_turns=0,
                                             summary_updated=False)
        if self._compactor is not None:
            try:
                result = self._compactor.maybe_compact()
                compaction = ChatCompactionResponse(
                    status=result.status, archived_turns=result.archived_turns,
                    summary_updated=result.summary_updated)
            except Exception:
                compaction = ChatCompactionResponse(status='failed', archived_turns=0,
                                                     summary_updated=False)
                events.append('compaction_failed')
        response = ChatResponse(app='lumina', status='ok', phase=phase,
                                message_consumed=True,
                                response=AssistantResponse(type=response_type, text=reply),
                                compaction=compaction)
        return MessageRuntimeResult(response=response,
                                    recent_context=[{'role': t.role, 'text': t.text}
                                                    for t in context.raw_turns],
                                    events=tuple(events))
