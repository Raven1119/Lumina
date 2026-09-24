"""FastAPI application for the Cold Draft chat MVP."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from threading import Lock
from typing import cast

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from Conversation_Memory.adapter.interfaces import MemoryIngestor, MemoryRetriever
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.adapter.body_payload import FORMATION_BODY_VERSION
from core.cold_draft_store import ColdDraftStore
from core.contracts import (
    ChatRequest,
    ChatResponse,
    CompactionStatusResponse,
    DreamRunResponse,
    DreamStatusResponse,
    DraftTurn,
    ExecutionRequest,
    ExecutionResponse,
    HistoryResponse,
    HistoryTurnResponse,
    MemoryStatusResponse,
    StatusResponse,
)
from core.draft_context import DraftContextProvider
from core.draft_store import JsonlDraftStore
from core.env_loader import load_env_file
from core.hot_draft_compactor import HotDraftCompactor
from core.message_runtime import MessageRuntime
from core.model_client import ModelClient, MockModelClient, build_model_client_from_env
from core.turn_provenance import Clock, TurnIdFactory
from Dream.cold_draft_digest import ColdDraftDigestionTask
from Dream.models import DreamRunPolicy
from Dream.runner import DreamRunner, build_formation_model_client
from Execution import ExecutionOrgan, FileContentEquals
from Mind.constant_gate import ConstantMindGate
from Mind.decision_log import JsonlDecisionLog
from Mind.interfaces import EvidenceSelector, MindGate
from Mind.evidence_selector import (
    LlmEvidenceSelector, LlmSemanticEvidenceSelector, LlmSemanticEvidenceSelectorV2,
)
from Mind.llm_gate import LlmMindGate, LlmQueryMindGate, QUERY_GATE_MAX_TOKENS


FRONTEND_DIRECTORY = Path(__file__).resolve().parent.parent / "edge" / "static"
_ROOT_DIRECTORY = Path(__file__).resolve().parent.parent
_CHAT_BACKGROUND_PATH = _ROOT_DIRECTORY / "prompts" / "chat_background.md"
_DREAM_POLICY = DreamRunPolicy()
_FORMATION_INGESTION_VERSION = "grounded-formation-v6"
_CHAT_RECALL_POLICY = RecallPolicy(
    top_k=10,
    max_graph_depth=1,
    max_nodes=20,
    max_evidence_items=3,
    max_chars=5000,
    include_source_context=True,
)
# Explicit read-first experiment: one Answer call sees whole bounded candidates.
_DIRECT_RECALL_POLICY = RecallPolicy(
    top_k=10, max_graph_depth=1, max_nodes=20, max_evidence_items=20,
    max_chars=5000, final_min_score=None, include_source_context=True,
)
_SEMANTIC_V2_RECALL_POLICY = RecallPolicy(
    top_k=10, max_graph_depth=1, max_nodes=20, max_evidence_items=32,
    max_chars=9000, max_bytes=36000, final_min_score=None,
    include_source_context=True,
)
_PENDING_STATUS_LIMIT = 100
_EXECUTION_MAX_DECISIONS = 8
_EXECUTION_COMPLETION_SPEC = FileContentEquals(
    ".lumina-complete",
    "verified",
)


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


def _mind_decision_log_path(configured: str | Path | None) -> Path:
    if configured is not None:
        return Path(configured)
    return Path(
        os.environ.get("LUMINA_MIND_DECISION_LOG_PATH", "data/mind/decisions.jsonl")
    )


def _model_kind(client: ModelClient) -> str:
    return "mock" if getattr(client, "client_kind", "model") == "mock" else "model"


def _recall_enabled(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return False


def _default_mind_gate(chat_model: ModelClient) -> MindGate:
    mode = os.environ.get("LUMINA_MIND_GATE_MODE", "llm").strip().lower()
    if mode == "graph-read-v2":
        if _model_kind(chat_model) == "mock":
            return LlmQueryMindGate(None)
        try:
            gate_client = build_model_client_from_env(
                max_tokens_override=QUERY_GATE_MAX_TOKENS, temperature_override=0.0,
            )
        except Exception:
            return LlmQueryMindGate(None)
        return LlmQueryMindGate(gate_client if getattr(gate_client, "client_kind", None) == "model" else None)
    # Mock mode is always the stage-1 constant gate (MIND_DEFINITION_V1 §2.4).
    if _model_kind(chat_model) == "mock":
        return ConstantMindGate()
    if mode == "constant":
        return ConstantMindGate()
    try:
        gate_client = build_model_client_from_env(
            max_tokens_override=8,
            temperature_override=0.0,
        )
    except Exception:
        return ConstantMindGate()
    if getattr(gate_client, "client_kind", None) != "model":
        return ConstantMindGate()
    return LlmMindGate(gate_client)


def _default_evidence_selector(chat_model: ModelClient) -> EvidenceSelector:
    if _model_kind(chat_model) == "mock":
        return LlmEvidenceSelector(chat_model)
    try:
        client = build_model_client_from_env(
            max_tokens_override=1024, temperature_override=0.0,
        )
    except Exception:
        client = MockModelClient()
    # Unavailable/mock output causes an audited fallback to the prepared context.
    return LlmEvidenceSelector(client)


def _default_semantic_selector(chat_model: ModelClient, *, version: int = 1):
    selector_type = LlmSemanticEvidenceSelectorV2 if version == 2 else LlmSemanticEvidenceSelector
    if _model_kind(chat_model) == "mock":
        return selector_type(chat_model)
    try:
        client = build_model_client_from_env(max_tokens_override=384, temperature_override=0.0)
    except Exception:
        client = MockModelClient()
    return selector_type(client)


def _load_chat_background(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        raise RuntimeError("Chat background could not be loaded.") from None
    if not content:
        raise RuntimeError("Chat background is empty.")
    return content


def _build_memory_retriever(
    formation_model: ModelClient | None = None,
    cold_store: ColdDraftStore | None = None,
) -> MemoryRetriever:

    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter

    persist_dir = Path(
        os.environ.get(
            "LUMINA_DREAM_MAGMA_PERSIST_DIR",
            str(_ROOT_DIRECTORY / "data" / "conversation_memory" / "magma"),
        )
    )
    memory_profile = os.environ.get("LUMINA_MEMORY_PROFILE", "production").strip().lower()
    if memory_profile not in {"production", "body-recall-v1", "calibrated-first-hit-v1", "semantic-associative-v1", "semantic-associative-v2"}:
        raise ValueError("invalid_memory_profile")
    if memory_profile in {"semantic-associative-v1", "semantic-associative-v2"} and os.environ.get("LUMINA_MIND_GATE_MODE", "llm").strip().lower() != "llm":
        raise ValueError("semantic_memory_gate_profile_conflict")
    if memory_profile == "body-recall-v1":
        if formation_model is None:
            raise ValueError("body_memory_requires_formation_model")
        if os.environ.get("LUMINA_MIND_GATE_MODE", "llm").strip().lower() in {"graph-read-v2", "select"}:
            raise ValueError("body_memory_gate_profile_conflict")
        return MagmaMemoryAdapter.create_real(
            persist_dir, fail_if_unavailable=True, ingestion_version=FORMATION_BODY_VERSION,
            formation_model=formation_model, first_hit=FirstHitPolicy(), cold_store=cold_store,
            associative_read_profile="body-recall-v1")
    if formation_model is None and memory_profile in {"calibrated-first-hit-v1", "semantic-associative-v1", "semantic-associative-v2"}:
        raise ValueError("multilingual_memory_requires_formation_model")
    if (memory_profile == "calibrated-first-hit-v1"
            and os.environ.get("LUMINA_MIND_GATE_MODE", "llm").strip().lower() in {"graph-read-v2", "select"}):
        raise ValueError("calibrated_memory_gate_profile_conflict")
    if formation_model is None:
        # Mock/legacy deterministic path: grounded spans, no FirstHit.
        return MagmaMemoryAdapter.create_real(
            persist_dir,
            fail_if_unavailable=True,
            ingestion_version=_DREAM_POLICY.ingestion_version,
        )
    # Explicit candidate changes only the reader. The v6 writer is shared and
    # continues to call its original FirstHit activation/connection planner.
    read_profile = (memory_profile if memory_profile in {"calibrated-first-hit-v1", "semantic-associative-v1", "semantic-associative-v2"}
                    else "graph-read-v2" if os.environ.get("LUMINA_MIND_GATE_MODE", "llm").strip().lower()
                    == "graph-read-v2" else "reliable-v2")
    return MagmaMemoryAdapter.create_real(
        persist_dir,
        fail_if_unavailable=True,
        ingestion_version=_FORMATION_INGESTION_VERSION,
        formation_model=formation_model,
        first_hit=FirstHitPolicy(),
        cold_store=cold_store,
        associative_read_profile=read_profile,
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
    mind_gate: MindGate | None = None,
    evidence_selector: EvidenceSelector | None = None,
    semantic_selector: object | None = None,
    mind_decision_log_path: str | Path | None = None,
    execution_root: str | Path | None = None,
    execution_model: object | None = None,
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
    memory_mode = os.environ.get("LUMINA_MIND_GATE_MODE", "llm").strip().lower()
    semantic_profile = os.environ.get("LUMINA_MEMORY_PROFILE", "production").strip().lower()
    semantic_mode = semantic_profile in {"semantic-associative-v1", "semantic-associative-v2"}
    if semantic_mode and (memory_mode != "llm" or evidence_selector is not None or mind_gate is not None):
        raise ValueError("semantic_memory_gate_profile_conflict")
    if semantic_selector is not None and not semantic_mode:
        raise ValueError("semantic_selector_requires_profile")
    if memory_mode == "graph-read-v2" and evidence_selector is not None:
        raise ValueError("structured query gate cannot also select evidence")
    post_read_selection = memory_mode == "select" or evidence_selector is not None
    direct_memory_use = memory_mode == "direct" or post_read_selection or semantic_mode
    effective_memory = memory_retriever
    effective_dream_policy = _DREAM_POLICY
    effective_recall_policy = (
        recall_policy
        if recall_policy is not None
        else (_SEMANTIC_V2_RECALL_POLICY if semantic_profile == "semantic-associative-v2"
              else _DIRECT_RECALL_POLICY if direct_memory_use else _CHAT_RECALL_POLICY)
        if effective_recall_enabled
        else None
    )
    hot_path = _hot_path(draft_store_path)
    effective_cold_path = Path(cold_draft_path) if cold_draft_path is not None else hot_path.parent / "cold_drafts.jsonl"
    effective_state_path = Path(compaction_state_path) if compaction_state_path is not None else hot_path.parent / "hot_draft_compaction_state.json"

    hot_store = JsonlDraftStore(hot_path)
    # One Cold owner: Chat reads its bounded source window, Dream owns state.
    cold_store = ColdDraftStore(effective_cold_path, source_window_segments=32)
    if effective_memory is None:
        try:
            formation_model = (
                model_client
                if model_client is not None
                else build_formation_model_client()
            )
            effective_memory = _build_memory_retriever(
                formation_model
                if getattr(formation_model, "client_kind", None) == "model"
                else None,
                cold_store,
            )
        except Exception:
            if semantic_mode:
                raise
            effective_memory = None
    runtime_retriever = effective_memory if effective_recall_enabled else None

    dream_runner = None
    if (
        effective_memory is not None
        and callable(getattr(effective_memory, "ingest", None))
        and getattr(effective_memory, "ingestion_version", None)
        in {_DREAM_POLICY.ingestion_version, _FORMATION_INGESTION_VERSION, FORMATION_BODY_VERSION}
    ):
        ingestor = cast(MemoryIngestor, effective_memory)
        provider = _SharedMemoryIngestorProvider(
            ingestor,
            getattr(effective_memory, "ingestion_version"),
        )
        dream_policy = DreamRunPolicy(
            max_segments=_DREAM_POLICY.max_segments,
            stop_on_error=_DREAM_POLICY.stop_on_error,
            ingestion_version=getattr(effective_memory, "ingestion_version"),
        )
        effective_dream_policy = dream_policy
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
    # Read-first modes have no pre-read gate, including injected gates.
    # Default/constant modes preserve their existing gate and fail-open path.
    effective_mind_gate = (
        None if direct_memory_use
        else mind_gate if mind_gate is not None else _default_mind_gate(effective_model)
    )
    effective_selector = (
        evidence_selector if evidence_selector is not None
        else _default_evidence_selector(effective_model)
    ) if post_read_selection and effective_recall_enabled else None
    effective_semantic_selector = (
        semantic_selector if semantic_selector is not None
        else _default_semantic_selector(
            effective_model, version=2 if semantic_profile == "semantic-associative-v2" else 1)
    ) if semantic_mode and effective_recall_enabled else None
    mind_decision_log = JsonlDecisionLog(
        _mind_decision_log_path(mind_decision_log_path)
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
        mind_gate=effective_mind_gate,
        evidence_selector=effective_selector,
        semantic_selector=effective_semantic_selector,
        mind_decision_log=mind_decision_log,
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
    effective_execution_root = (
        Path(execution_root)
        if execution_root is not None
        else _ROOT_DIRECTORY / "data" / "execution"
    ).resolve()

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
            memory=(
                MemoryStatusResponse(
                    writer_version=getattr(effective_memory, "ingestion_version", None),
                    reader_profile=getattr(
                        effective_memory, "associative_read_profile", None,
                    ),
                )
                if effective_memory is not None
                else None
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

    @app.post("/api/execution", response_model=ExecutionResponse)
    def post_execution(request: ExecutionRequest) -> ExecutionResponse:
        goal = request.goal.strip()
        if not goal:
            raise HTTPException(status_code=400, detail="goal is required")
        run_directory = effective_execution_root / f"run-{uuid.uuid4().hex}"
        workspace = run_directory / "workspace"
        organ = None
        try:
            workspace.mkdir(parents=True)
            organ = ExecutionOrgan(
                workspace=workspace,
                event_log_path=run_directory / "state" / "events.jsonl",
                checkpoint_path=run_directory / "state" / "checkpoint.json",
                max_decisions=_EXECUTION_MAX_DECISIONS,
                model=execution_model,
            )
            result = organ.run_goal(goal, _EXECUTION_COMPLETION_SPEC)
            organ.shutdown()
        except Exception:
            if organ is not None:
                try:
                    organ.shutdown()
                except Exception:
                    pass
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "execution_unavailable",
                    "message": "Execution could not complete",
                },
            ) from None
        if result.state.execution_id is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "execution_unavailable",
                    "message": "Execution could not complete",
                },
            )
        verified = result.status == "completed" and result.output == "verified"
        return ExecutionResponse(
            execution_id=result.state.execution_id,
            status=result.status,
            result=result.output if verified else None,
            verified=verified,
        )

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
            report = runner.run_once(effective_dream_policy)
            if not report.progress_saved:
                # Completed work remains durable; the normal safe failure
                # response makes an unsaved queue position visible to callers.
                raise RuntimeError("cold_draft_progress_write_failed")
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
