"""FastAPI application for the Cold Draft chat MVP."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from threading import Lock
from typing import cast

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from Conversation_Memory.adapter.interfaces import MemoryIngestor, MemoryRetriever
from Conversation_Memory.adapter.models import RecallPolicy
from core.cold_draft_store import ColdDraftStore
from core.contracts import (
    ChatRequest,
    ChatResponse,
    CompactionStatusResponse,
    DreamRunResponse,
    DreamStatusResponse,
    DraftTurn,
    HistoryResponse,
    HistoryTurnResponse,
    StatusResponse,
)
from core.draft_context import DraftContextProvider
from core.draft_store import JsonlDraftStore
from core.env_loader import load_env_file
from core.hot_draft_compactor import HotDraftCompactor
from core.message_runtime import MessageRuntime
from core.model_client import ModelClient, build_model_client_from_env
from core.turn_provenance import Clock, TurnIdFactory
from Dream.cold_draft_digest import ColdDraftDigestionTask
from Dream.models import DreamRunPolicy
from Dream.runner import DreamRunner


FRONTEND_DIRECTORY = Path(__file__).resolve().parent.parent / "edge" / "static"
_ROOT_DIRECTORY = Path(__file__).resolve().parent.parent
_CHAT_BACKGROUND_PATH = _ROOT_DIRECTORY / "prompts" / "chat_background.md"
_CONVERSATION_MEMORY_DIRECTORY = _ROOT_DIRECTORY / "Conversation_Memory"
_DREAM_POLICY = DreamRunPolicy()
_PENDING_STATUS_LIMIT = 100


def _history_projection(turn: DraftTurn) -> HistoryTurnResponse | None:
    if not turn.has_native_provenance:
        return None
    stored = turn.storage_turn()
    return HistoryTurnResponse(
        turn_id=stored["turn_id"],
        role=turn.role,
        content=turn.text,
        timestamp=stored["created_at"],
    )


def _history_snapshot(
    hot_store: JsonlDraftStore,
    cold_store: ColdDraftStore,
) -> list[HistoryTurnResponse]:
    # Read Hot first so Cold-first compaction can only create an overlap, never
    # a missing turn. Cold then wins the stable-ID deduplication below.
    hot_turns = hot_store.list_all_raw()
    cold_turns = cold_store.list_all_turns()
    seen: set[str] = set()
    projected: list[HistoryTurnResponse] = []
    for turn in [*cold_turns, *hot_turns]:
        history_turn = _history_projection(turn)
        if history_turn is None or history_turn.turn_id in seen:
            continue
        seen.add(history_turn.turn_id)
        projected.append(history_turn)
    return projected


def _history_page(
    turns: list[HistoryTurnResponse],
    *,
    limit: int,
    before: str | None,
) -> HistoryResponse:
    end = len(turns)
    if before is not None:
        try:
            end = next(
                index for index, turn in enumerate(turns)
                if turn.turn_id == before
            )
        except StopIteration:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_history_cursor",
                    "message": "history cursor is invalid",
                },
            ) from None
    start = max(0, end - limit)
    page = turns[start:end]
    has_more = start > 0
    return HistoryResponse(
        turns=page,
        has_more=has_more,
        next_before=page[0].turn_id if has_more and page else None,
    )


class _SharedMemoryIngestorProvider:
    """Adapt the app's one memory adapter to Dream's existing provider seam."""

    def __init__(self, ingestor: MemoryIngestor, ingestion_version: str) -> None:
        self._ingestor = ingestor
        self._ingestion_version = ingestion_version

    def get(self, ingestion_version: str) -> MemoryIngestor:
        if ingestion_version != self._ingestion_version:
            raise RuntimeError("memory_ingestor_version_unavailable")
        return self._ingestor


def _hot_path(configured: str | Path | None) -> Path:
    if configured is not None:
        return Path(configured)
    return Path(os.environ.get("LUMINA_DRAFT_STORE_PATH", "data/draft/hot_drafts.jsonl"))


def _model_kind(client: ModelClient) -> str:
    return "mock" if getattr(client, "client_kind", "model") == "mock" else "model"


def _recall_enabled(value: str | None) -> bool:
    return isinstance(value, str) and value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _load_chat_background(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        raise RuntimeError("Chat background could not be loaded.") from None
    if not content:
        raise RuntimeError("Chat background is empty.")
    return content


def _build_memory_retriever() -> MemoryRetriever:
    if str(_CONVERSATION_MEMORY_DIRECTORY) not in sys.path:
        sys.path.insert(0, str(_CONVERSATION_MEMORY_DIRECTORY))

    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter

    persist_dir = Path(
        os.environ.get(
            "LUMINA_DREAM_MAGMA_PERSIST_DIR",
            str(_ROOT_DIRECTORY / "data" / "conversation_memory" / "magma"),
        )
    )
    return MagmaMemoryAdapter.create_real(
        persist_dir,
        fail_if_unavailable=True,
        ingestion_version=_DREAM_POLICY.ingestion_version,
    )


def create_app(
    *,
    draft_store_path: str | Path | None = None,
    cold_draft_path: str | Path | None = None,
    compaction_state_path: str | Path | None = None,
    model_client: ModelClient | None = None,
    env_file_path: str | Path | None = ".env.local",
    retain_recent_raw_turns: int = 12,
    max_raw_turns_before_compression: int = 24,
    enable_compaction: bool = True,
    default_timezone: str | None = None,
    clock: Clock | None = None,
    turn_id_factory: TurnIdFactory | None = None,
    recall_enabled: bool | None = None,
    memory_retriever: MemoryRetriever | None = None,
    recall_policy: RecallPolicy | None = None,
) -> FastAPI:
    chat_background = _load_chat_background(_CHAT_BACKGROUND_PATH)
    if env_file_path is not None:
        load_env_file(env_file_path, override=False)

    effective_model = model_client or build_model_client_from_env()
    effective_recall_enabled = (
        recall_enabled
        if recall_enabled is not None
        else _recall_enabled(
            os.environ.get("LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED")
        )
    )
    effective_memory = memory_retriever
    effective_recall_policy = (
        recall_policy
        if recall_policy is not None
        else RecallPolicy()
        if effective_recall_enabled
        else None
    )
    if effective_memory is None:
        try:
            effective_memory = _build_memory_retriever()
        except Exception:
            effective_memory = None
    runtime_retriever = effective_memory if effective_recall_enabled else None

    hot_path = _hot_path(draft_store_path)
    effective_cold_path = Path(cold_draft_path) if cold_draft_path is not None else hot_path.parent / "cold_drafts.jsonl"
    effective_state_path = Path(compaction_state_path) if compaction_state_path is not None else hot_path.parent / "hot_draft_compaction_state.json"

    hot_store = JsonlDraftStore(hot_path)
    cold_store = ColdDraftStore(effective_cold_path)
    dream_runner = None
    if (
        effective_memory is not None
        and callable(getattr(effective_memory, "ingest", None))
        and getattr(effective_memory, "ingestion_version", None)
        == _DREAM_POLICY.ingestion_version
    ):
        ingestor = cast(MemoryIngestor, effective_memory)
        provider = _SharedMemoryIngestorProvider(
            ingestor,
            _DREAM_POLICY.ingestion_version,
        )
        dream_runner = DreamRunner(
            cold_store,
            ColdDraftDigestionTask(cold_store, provider),
        )
    context_provider = DraftContextProvider(hot_store)
    compactor = None
    if enable_compaction:
        summary_callable = getattr(
            effective_model,
            "summarize_hot_draft",
            None,
        )
        compactor = HotDraftCompactor(
            hot_store,
            cold_store,
            effective_state_path,
            summarizer=summary_callable if callable(summary_callable) else None,
            retain_recent_raw_turns=retain_recent_raw_turns,
            max_raw_turns_before_compression=max_raw_turns_before_compression,
        )
    runtime = MessageRuntime(
        hot_store=hot_store,
        draft_context_provider=context_provider,
        model_client=effective_model,
        chat_background=chat_background,
        compactor=compactor,
        clock=clock,
        turn_id_factory=turn_id_factory,
        default_timezone=(
            default_timezone
            if default_timezone is not None
            else os.environ.get("LUMINA_DEFAULT_TIMEZONE", "UTC")
        ),
        recall_enabled=effective_recall_enabled,
        memory_retriever=runtime_retriever,
        recall_policy=effective_recall_policy,
    )

    app = FastAPI(title="Lumina Cold Draft MVP", version="0.1.0")
    app.state.message_runtime = runtime
    app.state.hot_draft_store = hot_store
    app.state.cold_draft_store = cold_store
    app.state.dream_runner = dream_runner
    app.state.writer_lock = Lock()
    app.state.dream_running = False
    app.state.recall_enabled = effective_recall_enabled
    app.state.hot_draft_compactor = compactor

    @app.get("/api/status", response_model=StatusResponse)
    def get_status() -> StatusResponse:
        pending = cold_store.count_pending_bounded(_PENDING_STATUS_LIMIT)
        return StatusResponse(
            app="lumina",
            status="ok",
            mode=_model_kind(effective_model),
            draft_enabled=True,
            recall_enabled=effective_recall_enabled,
            compaction=CompactionStatusResponse(
                running=compactor.is_running if compactor is not None else False,
            ),
            dream=DreamStatusResponse(
                available=app.state.dream_runner is not None,
                running=app.state.dream_running,
                pending_segments=pending.count,
                pending_truncated=pending.truncated,
            ),
        )

    @app.get("/api/history", response_model=HistoryResponse)
    def get_history(
        limit: int = Query(default=40, ge=1, le=100),
        before: str | None = None,
    ) -> HistoryResponse:
        return _history_page(
            _history_snapshot(hot_store, cold_store),
            limit=limit,
            before=before,
        )

    @app.post("/api/chat", response_model=ChatResponse)
    def post_chat(request: ChatRequest) -> ChatResponse:
        if not app.state.writer_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "writer_busy",
                    "message": "another write operation is in progress",
                },
            )
        try:
            message = request.message if request.message is not None else request.text
            if message is None or not message.strip():
                raise HTTPException(status_code=400, detail="message is required")
            return runtime.handle_chat(request).response
        finally:
            app.state.writer_lock.release()

    @app.post("/api/dream/run", response_model=DreamRunResponse)
    def post_dream() -> DreamRunResponse:
        runner = app.state.dream_runner
        if runner is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "dream_unavailable",
                    "message": "Dream is unavailable",
                },
            )
        if not app.state.writer_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "writer_busy",
                    "message": "another write operation is in progress",
                },
            )
        app.state.dream_running = True
        try:
            report = runner.run_once(_DREAM_POLICY)
            return DreamRunResponse(
                attempted=report.attempted,
                ingested=report.ingested,
                consumed=report.consumed,
                skipped=report.skipped,
                failed=report.failed,
            )
        except Exception:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "dream_failed",
                    "message": "Dream could not complete",
                },
            ) from None
        finally:
            app.state.dream_running = False
            app.state.writer_lock.release()

    if FRONTEND_DIRECTORY.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=FRONTEND_DIRECTORY, html=True),
            name="frontend",
        )

    return app


app = create_app()
