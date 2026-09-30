"""FastAPI Chat, Cold Draft, memory-v1, and manual Execution entrypoints."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from Conversation_Memory.engine.clock import TZ
from Conversation_Memory.engine.config import preset
from Conversation_Memory.engine.embed import resolve_embedder
from Conversation_Memory.facade import MemoryV1
from Conversation_Memory.model_client import build_memory_model_from_env
from core.cold_draft_store import ColdDraftStore
from core.contracts import (ChatRequest, ChatResponse, CompactionStatusResponse,
                            DraftTurn, DreamRunResponse, DreamStatusResponse,
                            HistoryResponse,
                            HistoryTurnResponse, MemoryListResponse,
                            MemoryStatusResponse, StatusResponse)
from core.draft_store import JsonlDraftStore
from core.env_loader import load_env_file
from core.hot_draft_compactor import HotDraftCompactor
from core.memory_adapter import draft_turns_to_memory
from core.message_runtime import MessageRuntime
from core.model_client import (DeepSeekAnthropicModelClient, ModelClient,
                               build_model_client_from_env)
from core.turn_provenance import Clock, TurnIdFactory
from Execution.pool import HelperPool
from model_policy import model_for
from config.lumina import load_config
from core.dialogue_io import DialogueIO, response as fallback_response
from Nervous.bus import EventBus
from Nervous.lumina_state import LuminaState
from Nervous.scheduler import DialogueScheduler
from Mind.runner import DialogueRunner
from Language.channel import LanguageChannel

FRONTEND_DIRECTORY = Path(__file__).resolve().parent.parent / 'edge' / 'static'
_ROOT_DIRECTORY = Path(__file__).resolve().parent.parent
_CHAT_BACKGROUND_PATH = _ROOT_DIRECTORY / 'prompts' / 'chat_background.md'


def _load_chat_background(path: Path) -> str:
    try:
        value = path.read_text(encoding='utf-8-sig').strip()
    except (OSError, UnicodeError):
        raise RuntimeError('Chat background could not be loaded.') from None
    if not value:
        raise RuntimeError('Chat background is empty.')
    return value


def _recall_enabled(value: str | None) -> bool:
    return value is None or value.strip().lower() in {'1', 'true', 'yes', 'on'}


def should_auto_dream(*, real_mode: bool, recall_enabled: bool,
                      embed_available: bool, has_cursor: bool,
                      paused: bool, pending_turns: int, trigger_turns: int) -> bool:
    return (real_mode and recall_enabled and embed_available and has_cursor
            and not paused and pending_turns >= trigger_turns)


def _history_projection(turn: DraftTurn) -> HistoryTurnResponse | None:
    if not turn.has_native_provenance:
        return None
    stored = turn.storage_turn()
    return HistoryTurnResponse(turn_id=stored['turn_id'], role=turn.role,
                               content=turn.text, timestamp=stored['created_at'])


def _history_snapshot(hot: JsonlDraftStore, cold: ColdDraftStore) -> list[HistoryTurnResponse]:
    seen: set[str] = set()
    projected = []
    for turn in [*cold.list_all_turns(), *hot.list_all_raw()]:
        item = _history_projection(turn)
        if item is not None and item.turn_id not in seen:
            seen.add(item.turn_id)
            projected.append(item)
    return projected


def _history_page(turns: list[HistoryTurnResponse], *, limit: int,
                  before: str | None) -> HistoryResponse:
    end = len(turns)
    if before is not None:
        try:
            end = next(i for i, turn in enumerate(turns) if turn.turn_id == before)
        except StopIteration:
            raise HTTPException(status_code=400, detail={'code': 'invalid_history_cursor',
                                                          'message': 'history cursor is invalid'}) from None
    start = max(0, end-limit)
    page = turns[start:end]
    return HistoryResponse(turns=page, has_more=start>0,
                           next_before=page[0].turn_id if start>0 and page else None)


def create_app(*, draft_store_path: str | Path | None = None,
               cold_draft_path: str | Path | None = None,
               compaction_state_path: str | Path | None = None,
               memory_dir: str | Path | None = None,
               model_client: ModelClient | None = None,
               memory: MemoryV1 | None = None,
               env_file_path: str | Path | None = '.env.local',
               retain_recent_raw_turns: int = 12,
               max_raw_turns_before_compression: int = 24,
               enable_compaction: bool = True,
               default_timezone: str | None = None,
               clock: Clock | None = None,
               turn_id_factory: TurnIdFactory | None = None,
               recall_enabled: bool | None = None,
               lumina_config=None, nervous_db_path=None, language_model=None) -> FastAPI:
    background = _load_chat_background(_CHAT_BACKGROUND_PATH)
    if env_file_path is not None:
        load_env_file(env_file_path, override=False)
    settings = load_config(overrides=lumina_config)
    model = model_client or build_model_client_from_env()
    if isinstance(model, DeepSeekAnthropicModelClient):
        # A1 keeps its v1 wire format. A2 expects a complete JSON object;
        # prefilling a field can duplicate the object on current providers.
        model.answer_prefill_enabled = settings['mind']['protocol']=='a1'
        if settings['mind']['protocol']=='a2':
            model._max_tokens=settings['model']['max_output_tokens']
    enabled = (recall_enabled if recall_enabled is not None else
               _recall_enabled(os.environ.get('LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED')))
    hot_path = Path(draft_store_path or os.environ.get('LUMINA_DRAFT_STORE_PATH',
                                                       'data/draft/hot_drafts.jsonl'))
    cold_path = Path(cold_draft_path or hot_path.parent/'cold_drafts.jsonl')
    state_path = Path(compaction_state_path or hot_path.parent/'hot_draft_compaction_state.json')
    mem_path = Path(memory_dir or os.environ.get('LUMINA_MEMORY_DIR', 'data/memory_v1'))
    hot = JsonlDraftStore(hot_path)
    cold = ColdDraftStore(cold_path)
    writer_lock = Lock()
    memory_model = build_memory_model_from_env(mem_path/'cache')
    if memory is None:
        def embedder_factory():
            override = os.environ.get('LUMINA_EMBED_MODEL_PATH')
            hf_home = _ROOT_DIRECTORY/'Memory_lab'/'cache'/'hf'
            return resolve_embedder('bge-m3', False, mem_path/'embed_cache',
                                    hf_home=hf_home,
                                    model_path=Path(override) if override else None)
        memory = MemoryV1(mem_path, embedder_factory=embedder_factory,
                          model_factory=(lambda: memory_model) if memory_model else None,
                          config=replace(preset('P8', 'bge-m3'),
                                         llm_model=model_for('memory')),
                          commit_lock=writer_lock)
    compactor = (HotDraftCompactor(hot, cold, state_path,
                 summarizer=getattr(model, 'summarize_hot_draft', None),
                 retain_recent_raw_turns=retain_recent_raw_turns,
                 max_raw_turns_before_compression=max_raw_turns_before_compression,
                 allow_multi_speech=settings['mind']['protocol']=='a2')
                 if enable_compaction else None)
    runtime = MessageRuntime(hot_store=hot, model_client=model,
                             chat_background=background, memory=memory,
                             recall_enabled=enabled, compactor=compactor,
                             clock=clock, turn_id_factory=turn_id_factory,
                             default_timezone=default_timezone or
                                 os.environ.get('LUMINA_DEFAULT_TIMEZONE', 'Asia/Shanghai'))
    @asynccontextmanager
    async def lifespan(_app):
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(title='Lumina', version='1.0.0', lifespan=lifespan)
    app.state.message_runtime = runtime
    app.state.hot_draft_store = hot
    app.state.cold_draft_store = cold
    app.state.memory = memory
    app.state.dream_runner = memory
    app.state.writer_lock = writer_lock
    app.state.dream_running = False
    app.state.recall_enabled = enabled
    app.state.hot_draft_compactor = compactor
    app.state.summary_truncated = False
    trigger = max(1, int(os.environ.get('LUMINA_DREAM_TRIGGER_TURNS', '40')))

    def now() -> datetime:
        return clock.now() if clock is not None else datetime.now(TZ)

    def cold_input():
        return draft_turns_to_memory(cold.list_all_turns(), now(), skip_untimed=True)

    def run_dream(manual: bool) -> DreamRunResponse:
        app.state.dream_running = True
        try:
            with writer_lock:
                snapshot = cold_input()
            outcome = memory.dream_once(snapshot, manual=manual, now=now())
            return DreamRunResponse(status=outcome.status, window_turns=outcome.window_turns,
                                    dream_id=outcome.dream_id, patterns=outcome.patterns)
        finally:
            app.state.dream_running = False
            live_state.snapshot()

    def maybe_auto_dream(response: ChatResponse) -> None:
        if (response.phase != 'model_chat' or memory_model is None
                or not enabled or not memory.has_cold_cursor()
                or memory.auto_paused()):
            return
        try:
            memory._embedder()
            with writer_lock:
                count = memory.unintegrated_turn_count(cold_input())
            if not should_auto_dream(real_mode=True, recall_enabled=enabled,
                    embed_available=True, has_cursor=True, paused=False,
                    pending_turns=count, trigger_turns=trigger):
                return
        except Exception:
            return
        Thread(target=run_dream, kwargs={'manual': False}, daemon=True,
               name='memory-v1-dream').start()

    bus_path = nervous_db_path or (hot_path.parent/'nervous.sqlite' if draft_store_path is not None else settings['nervous']['db_path'])
    bus = EventBus(bus_path)
    live_state = LuminaState(memory._dream_lock, bus)
    pool = HelperPool(bus, settings)
    live_state.attach_pool(pool)
    io = DialogueIO(runtime, writer_lock)
    if language_model is None and settings['language']['model']:
        language_model = build_model_client_from_env(
            model_name_override=settings['language']['model'],
            max_tokens_override=settings['model']['max_output_tokens'])
    channel = LanguageChannel(bus, io, settings, language_model)
    scheduler = DialogueScheduler(bus, None, channel,
        lambda payload: maybe_auto_dream(ChatResponse.model_validate(payload)), pool=pool)
    runner = DialogueRunner(bus, io, settings, live_state, scheduler.emit, pool=pool)
    scheduler.runner = runner
    app.state.dialogue_scheduler = scheduler
    app.state.nervous_bus = bus
    app.state.lumina_state = live_state
    app.state.helper_pool = pool
    app.state.lumina_config = settings

    @app.get('/api/status', response_model=StatusResponse)
    def get_status() -> StatusResponse:
        try:
            inspection = memory.inspect(now(), limit=0)
            with writer_lock:
                remaining = memory.unintegrated_turn_count(cold_input())
            override = os.environ.get('LUMINA_EMBED_MODEL_PATH')
            snapshot = (_ROOT_DIRECTORY/'Memory_lab'/'cache'/'hf'/'hub'/
                        'models--BAAI--bge-m3'/'snapshots'/
                        '5617a9f61b028005a4858fdac845db406aefb181')
            available = bool(getattr(memory._local, 'embedder', None)) or any(
                (path/'pytorch_model.bin').is_file() or (path/'model.safetensors').is_file()
                for path in ((Path(override), snapshot) if override else (snapshot,)))
            latest = inspection['dream_log'][0] if inspection['dream_log'] else None
        except Exception:
            inspection = {'memory_count': 0, 'pattern_count': 0}
            remaining = 0
            available = False
            latest = None
        return StatusResponse(app='lumina', status='ok', lumina=live_state.snapshot(),
            frontend_poll_interval_s=settings['frontend']['poll_interval_s'],
            mode='mock' if getattr(model, 'client_kind', 'model')=='mock' else 'model',
            recall_enabled=enabled,
            compaction=CompactionStatusResponse(running=compactor.is_running if compactor else False,
                summary_truncated=compactor.last_summary_truncated if compactor else False),
            dream=DreamStatusResponse(available=memory_model is not None,
                running=memory._dream_lock.locked(), unintegrated_cold_turns=remaining,
                last_at=latest['at'] if latest else None,
                last_result=latest['status'] if latest else None,
                auto_paused=memory.auto_paused()),
            memory=MemoryStatusResponse(memory_count=inspection['memory_count'],
                pattern_count=inspection['pattern_count'], embedding_available=available))

    @app.get('/api/memory', response_model=MemoryListResponse)
    def get_memory() -> MemoryListResponse:
        try:
            return MemoryListResponse.model_validate(memory.inspect(now()))
        except Exception:
            raise HTTPException(status_code=503,
                                detail={'code':'memory_unavailable','message':'Memory is unavailable'}) from None

    @app.get('/api/history', response_model=HistoryResponse)
    def get_history(limit: int = Query(default=40, ge=1, le=100),
                    before: str | None = None) -> HistoryResponse:
        return _history_page(_history_snapshot(hot, cold), limit=limit, before=before)

    @app.post('/api/chat', response_model=ChatResponse)
    def post_chat(request: ChatRequest) -> ChatResponse:
        message = request.message if request.message is not None else request.text
        if message is None or not message.strip():
            raise HTTPException(status_code=400, detail='message is required')
        event_id = uuid.uuid4().hex
        bus.publish(event_id, 'user.message', request.model_dump())
        scheduler.start() # Also supports ASGI transports without lifespan management.
        reply = bus.wait(event_id+':reply', settings['chat']['first_reply_timeout_s'])
        return ChatResponse.model_validate(reply or fallback_response(
            phase='mock_chat' if getattr(model, 'client_kind', 'model')=='mock' else 'model_chat'))

    @app.post('/api/dream/run', response_model=DreamRunResponse)
    def post_dream() -> DreamRunResponse:
        return run_dream(manual=True)

    if FRONTEND_DIRECTORY.is_dir():
        app.mount('/', StaticFiles(directory=FRONTEND_DIRECTORY, html=True), name='frontend')
    return app


app = create_app()
