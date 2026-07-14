"""FastAPI application for the Cold Draft chat MVP."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from core.cold_draft_store import ColdDraftStore
from core.contracts import ChatRequest, ChatResponse, StatusResponse
from core.draft_context import DraftContextProvider
from core.draft_store import JsonlDraftStore
from core.env_loader import load_env_file
from core.hot_draft_compactor import HotDraftCompactor
from core.message_runtime import MessageRuntime
from core.model_client import ModelClient, build_model_client_from_env
from core.turn_provenance import Clock, TurnIdFactory


FRONTEND_DIRECTORY = Path(__file__).resolve().parent.parent / "edge" / "static"


def _hot_path(configured: str | Path | None) -> Path:
    if configured is not None:
        return Path(configured)
    return Path(os.environ.get("LUMINA_DRAFT_STORE_PATH", "data/draft/hot_drafts.jsonl"))


def _model_kind(client: ModelClient) -> str:
    return "mock" if getattr(client, "client_kind", "model") == "mock" else "model"


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
) -> FastAPI:
    if env_file_path is not None:
        load_env_file(env_file_path, override=False)

    effective_model = model_client or build_model_client_from_env()
    hot_path = _hot_path(draft_store_path)
    effective_cold_path = Path(cold_draft_path) if cold_draft_path is not None else hot_path.parent / "cold_drafts.jsonl"
    effective_state_path = Path(compaction_state_path) if compaction_state_path is not None else hot_path.parent / "hot_draft_compaction_state.json"

    hot_store = JsonlDraftStore(hot_path)
    cold_store = ColdDraftStore(effective_cold_path)
    context_provider = DraftContextProvider(hot_store)
    compactor = None
    if enable_compaction:
        compactor = HotDraftCompactor(
            hot_store,
            cold_store,
            effective_state_path,
            retain_recent_raw_turns=retain_recent_raw_turns,
            max_raw_turns_before_compression=max_raw_turns_before_compression,
        )
    runtime = MessageRuntime(
        hot_store=hot_store,
        draft_context_provider=context_provider,
        model_client=effective_model,
        compactor=compactor,
        clock=clock,
        turn_id_factory=turn_id_factory,
        default_timezone=(
            default_timezone
            if default_timezone is not None
            else os.environ.get("LUMINA_DEFAULT_TIMEZONE", "UTC")
        ),
    )

    app = FastAPI(title="Lumina Cold Draft MVP", version="0.1.0")
    app.state.message_runtime = runtime
    app.state.hot_draft_store = hot_store
    app.state.cold_draft_store = cold_store

    @app.get("/api/status", response_model=StatusResponse)
    def get_status() -> StatusResponse:
        return StatusResponse(
            app="lumina",
            status="ok",
            mode=_model_kind(effective_model),
            draft_enabled=True,
        )

    @app.post("/api/chat", response_model=ChatResponse)
    def post_chat(request: ChatRequest) -> ChatResponse:
        message = request.message if request.message is not None else request.text
        if message is None or not message.strip():
            raise HTTPException(status_code=400, detail="message is required")
        return runtime.handle_chat(request).response

    if FRONTEND_DIRECTORY.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=FRONTEND_DIRECTORY, html=True),
            name="frontend",
        )

    return app


app = create_app()
