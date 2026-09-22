"""Isolated production-Draft -> Dream -> real-MAGMA -> recall acceptance run."""

from __future__ import annotations

import argparse
import contextlib
import gc
import io
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
CONVERSATION_MEMORY_ROOT = ROOT / "Conversation_Memory"

import Conversation_Memory.adapter.magma_adapter as magma_adapter_module  # noqa: E402
from Conversation_Memory.adapter._grounded_spans import build_grounded_spans  # noqa: E402
from Conversation_Memory.adapter.backend import RealMagmaBackend  # noqa: E402
from Conversation_Memory.adapter.interfaces import MemoryIngestor, MemoryRetriever  # noqa: E402
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from Conversation_Memory.adapter.models import (  # noqa: E402
    ColdDraftSegment,
    ColdDraftTurn,
    MemoryContext,
    RecallPolicy,
)
from core.cold_draft_store import ColdDraftStore  # noqa: E402
from core.contracts import MemoryTurn  # noqa: E402
from core.draft_store import JsonlDraftStore  # noqa: E402
from core.hot_draft_compactor import HotDraftCompactor  # noqa: E402
from Dream.cold_draft_digest import (  # noqa: E402
    ColdDraftDigestionTask,
    ColdDraftSegmentConverter,
)
from Dream.models import DreamRunPolicy  # noqa: E402
from Dream.runner import DreamRunner  # noqa: E402
from Conversation_Memory.ingestion.state_store import IngestionStateStore  # noqa: E402
from Conversation_Memory.recall.bge_reranker import BGE_MODEL, BGE_REVISION  # noqa: E402


SANDBOX_MARKER = ".recall_e2e_sandbox"
SANDBOX_MARKER_CONTENT = "lumina-recall-e2e-v1\n"
DEFAULT_WORK_DIR = ROOT / "data" / "recall_e2e_test"
INGESTION_VERSION = "grounded-span-v2"
_EVIDENCE_ID = re.compile(r"^grounded_span_v2:.+:\d+:\d+$")
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SyntheticTurn:
    turn_id: str
    timestamp: str
    role: str
    text: str


SOURCE_TIMEZONE = "Asia/Shanghai"

FIXED_TURNS = (
    SyntheticTurn(
        "e2e-turn-001",
        "2026-07-14T10:00:00+08:00",
        "user",
        "I completed the membrane experiment yesterday. 我昨天完成了膜实验。",
    ),
    SyntheticTurn(
        "e2e-turn-002",
        "2026-07-14T10:05:00+08:00",
        "assistant",
        "The experiment was recorded as completed.",
    ),
    SyntheticTurn(
        "e2e-turn-003",
        "2026-07-14T11:00:00+08:00",
        "user",
        "The first experiment failed because the solvent evaporated too quickly.",
    ),
    SyntheticTurn(
        "e2e-turn-004",
        "2026-07-14T11:05:00+08:00",
        "assistant",
        "The rapid solvent evaporation caused the failure.",
    ),
    SyntheticTurn(
        "e2e-turn-005",
        "2026-07-15T09:00:00+08:00",
        "user",
        "I changed the solvent today and repeated the experiment. "
        "我上周一更换了溶剂，下周一准备复查。",
    ),
    SyntheticTurn(
        "e2e-turn-006",
        "2026-07-15T09:05:00+08:00",
        "assistant",
        "The repeated experiment used the new solvent.",
    ),
)

_HOT_TAIL = (
    SyntheticTurn(
        "e2e-tail-001",
        "2026-07-15T09:10:00+08:00",
        "user",
        "Please keep the most recent pair in the Hot Draft.",
    ),
    SyntheticTurn(
        "e2e-tail-002",
        "2026-07-15T09:11:00+08:00",
        "assistant",
        "The most recent pair remains available as recent context.",
    ),
)


@dataclass(frozen=True)
class SandboxPaths:
    root: Path
    hot_draft: Path
    cold_draft: Path
    compaction_state: Path
    magma: Path
    ingestion_state: Path
    report: Path
    logs: Path

    @classmethod
    def from_root(cls, root: Path) -> "SandboxPaths":
        return cls(
            root=root,
            hot_draft=root / "draft" / "hot_drafts.jsonl",
            cold_draft=root / "draft" / "cold_drafts.jsonl",
            compaction_state=root / "draft" / "hot_draft_compaction_state.json",
            magma=root / "conversation_memory" / "magma",
            ingestion_state=root / "conversation_memory" / "ingestion_state.json",
            report=root / "reports" / "recall_e2e_result.json",
            logs=root / "logs",
        )


class AcceptanceFailure(RuntimeError):
    def __init__(self, stage: str, code: str) -> None:
        super().__init__(code)
        self.stage = stage
        self.code = code


class SandboxSafetyError(AcceptanceFailure):
    def __init__(self, code: str) -> None:
        super().__init__("sandbox", code)


class _FixedIngestorProvider:
    def __init__(self, ingestor: MemoryIngestor) -> None:
        self._ingestor = ingestor

    def get(self, ingestion_version: str) -> MemoryIngestor:
        return self._ingestor


class _TimedSwitchingBackend:
    """Private E2E seam for one live adapter over isolated MAGMA stores."""

    def __init__(self) -> None:
        self._delegate: RealMagmaBackend | None = None
        self.last_ms = 0.0
        self.last_count = 0
        self.last_candidates = ()

    def switch(self, delegate: RealMagmaBackend) -> None:
        self._delegate = delegate
        self.last_ms = 0.0
        self.last_count = 0
        self.last_candidates = ()

    def recall(self, query: str, policy: RecallPolicy, target_entity_ref: str | None = None,
               target_entity_refs: tuple[str, ...] = ()):
        delegate = self._require_delegate()
        started = perf_counter()
        try:
            candidates = delegate.recall(query, policy, target_entity_ref=target_entity_ref,
                                         target_entity_refs=target_entity_refs)
            self.last_count = len(candidates)
            self.last_candidates = tuple(candidates)
            return candidates
        finally:
            self.last_ms = (perf_counter() - started) * 1000.0

    def _require_delegate(self) -> RealMagmaBackend:
        if self._delegate is None:
            raise RuntimeError("E2E backend delegate is not configured")
        return self._delegate

    def __getattr__(self, name: str):
        return getattr(self._require_delegate(), name)


class _TimedBgeReranker:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.calls = 0
        self.last_ms = 0.0
        self.last_scores = ()

    def score(self, query: str, candidate_texts):
        self.calls += 1
        started = perf_counter()
        try:
            scores = tuple(self.delegate.score(query, candidate_texts))
            self.last_scores = scores
            return scores
        finally:
            self.last_ms = (perf_counter() - started) * 1000.0


@contextlib.contextmanager
def _capture_bge_runtime():
    """Time the production-private lazy factory without adding public API."""

    original_factory = magma_adapter_module._create_bge_reranker
    state: dict[str, Any] = {"factory_calls": 0, "instance": None}

    def timed_factory():
        state["factory_calls"] += 1
        instance = _TimedBgeReranker(original_factory())
        state["instance"] = instance
        return instance

    magma_adapter_module._create_bge_reranker = timed_factory
    try:
        yield state
    finally:
        magma_adapter_module._create_bge_reranker = original_factory


def _fake_rolling_summarizer(
    old_summary: str | None,
    moved_turns: list[MemoryTurn],
) -> str:
    parts = [old_summary] if old_summary else []
    parts.extend(f"{turn.role}: {turn.text}" for turn in moved_turns)
    return "\n".join(parts)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_sandbox_path(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.exists() and candidate.is_symlink():
        raise SandboxSafetyError("sandbox_symlink_refused")
    resolved = candidate.resolve(strict=False)
    drive_root = Path(resolved.anchor).resolve(strict=False)
    home = Path.home().resolve(strict=False)
    data_root = (ROOT / "data").resolve(strict=False)
    exact_forbidden = {drive_root, home, ROOT.resolve(strict=False), data_root}
    if resolved in exact_forbidden:
        raise SandboxSafetyError("unsafe_sandbox_path")

    forbidden_trees = (
        (ROOT / "data" / "draft").resolve(strict=False),
        (ROOT / "data" / "conversation_memory").resolve(strict=False),
        (ROOT / ".git").resolve(strict=False),
        (ROOT / "Conversation_Memory" / "upstream" / "MAGMA").resolve(strict=False),
    )
    if any(_is_within(resolved, forbidden) for forbidden in forbidden_trees):
        raise SandboxSafetyError("production_or_sensitive_path_refused")

    repository_root = ROOT.resolve(strict=False)
    if _is_within(resolved, repository_root) and not _is_within(resolved, data_root):
        raise SandboxSafetyError("repository_code_path_refused")
    return resolved


def _has_valid_marker(path: Path) -> bool:
    marker = path / SANDBOX_MARKER
    if not marker.is_file() or marker.is_symlink():
        return False
    try:
        return marker.read_text(encoding="utf-8") == SANDBOX_MARKER_CONTENT
    except OSError:
        return False


def reset_test_sandbox(path: Path) -> Path:
    safe = validate_sandbox_path(path)
    if safe.exists():
        if not safe.is_dir():
            raise SandboxSafetyError("sandbox_not_directory")
        if not _has_valid_marker(safe):
            raise SandboxSafetyError("sandbox_marker_required")
        shutil.rmtree(safe)
    safe.mkdir(parents=True, exist_ok=False)
    (safe / SANDBOX_MARKER).write_text(
        SANDBOX_MARKER_CONTENT,
        encoding="utf-8",
    )
    return safe


def cleanup_test_sandbox(path: Path) -> None:
    safe = validate_sandbox_path(path)
    if not safe.exists():
        return
    if not safe.is_dir() or not _has_valid_marker(safe):
        raise SandboxSafetyError("sandbox_marker_required")
    shutil.rmtree(safe)


def _require(condition: bool, stage: str, code: str) -> None:
    if not condition:
        raise AcceptanceFailure(stage, code)


def _base_report(keep_data: bool) -> dict[str, Any]:
    return {
        "result": "FAIL",
        "sandbox": True,
        "cold_draft": {
            "compacted": False,
            "pending_created": 0,
            "consumed": 0,
            "raw_order_preserved": False,
        },
        "dream": {"attempted": 0, "failed": 0, "second_attempted": 0},
        "magma": {"events": 0, "vectors": 0, "persisted": False},
        "recall": {"queries": 10, "passed": 0, "failed": 0, "checks": {}},
        "provenance": {
            "passed": False,
            "temporal_normalization_passed": False,
            "timestamp_mapping": "native_per_turn_v2",
        },
        "bounds": {
            "top_k": False,
            "max_evidence_items": False,
            "max_chars": False,
        },
        "restart_recall": {"passed": False},
        "grounded_bge_acceptance": {
            "result": "FAIL",
            "model": BGE_MODEL,
            "revision": BGE_REVISION,
            "queries": 11,
            "top_k": 10,
            "max_graph_depth": 1,
            "max_nodes": 20,
            "score_floor": None,
            "max_evidence_items": 3,
            "depth0_positive_complete": 0,
            "depth1_positive_complete": 0,
            "no_answer_empty_depth0": 0,
            "no_answer_empty_depth1": 0,
            "near_miss_empty_depth0": 0,
            "near_miss_empty_depth1": 0,
            "graph_added_candidates": [],
            "graph_added_required_evidence": [],
            "xiaolin": False,
            "meeting_roles": [],
            "meeting_role_evidence": False,
            "assistant_self_memory": False,
            "median_recall_latency_depth0": 0.0,
            "median_recall_latency_depth1": 0.0,
            "median_bge_latency_depth0": 0.0,
            "median_bge_latency_depth1": 0.0,
            "median_candidate_count_depth0": 0.0,
            "median_candidate_count_depth1": 0.0,
            "graphiti_rows": [],
            "top1": 0,
            "top2_answer_complete": 0,
            "top3_answer_complete": 0,
            "lazy_load": False,
            "instance_reuse": False,
            "public_score_exposure": "none",
            "factory_calls": 0,
            "median_magma_ms": 0.0,
            "median_bge_ms": 0.0,
            "median_total_ms": 0.0,
            "median_candidate_count": 0.0,
            "median_top2_rendered_chars": 0.0,
            "median_top2_token_estimate": 0.0,
            "rows": [],
        },
        "idempotency": {
            "passed": False,
            "node_count_stable": False,
            "vector_count_stable": False,
            "state_stable": False,
            "evidence_ids_stable": False,
        },
        "leak_checks": {"passed": False},
        "cleanup": {
            "mode": "keep-data" if keep_data else "default",
            "passed": False,
        },
        "failures": [],
    }


def _safe_failure_report(stage: str, code: str, keep_data: bool) -> dict[str, Any]:
    report = _base_report(keep_data)
    report["failures"] = [{"stage": stage, "code": code}]
    return report


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validate_isolated_runtime() -> None:
    expected = (CONVERSATION_MEMORY_ROOT / ".venv").resolve(strict=False)
    if Path(sys.prefix).resolve(strict=False) != expected:
        raise AcceptanceFailure("runtime", "isolated_environment_required")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        _require(isinstance(raw, dict), "cold_draft", "invalid_cold_draft_record")
        records.append(raw)
    return records


def _draft_turn(turn: SyntheticTurn) -> MemoryTurn:
    source_time = datetime.fromisoformat(turn.timestamp)
    return MemoryTurn(
        turn_id=turn.turn_id,
        role=turn.role,
        text=turn.text,
        created_at=source_time.astimezone(UTC),
        source_timezone=SOURCE_TIMEZONE,
        timezone_source="client",
    )


def _memory_counts(backend: RealMagmaBackend) -> tuple[int, int]:
    return len(backend.trg.graph_db.nodes), int(backend.trg.vector_db.size())


def _query_anchor_count(
    backend: RealMagmaBackend,
    query: str,
    policy: RecallPolicy,
) -> int:
    candidates = backend.recall(
        query,
        replace(policy, max_graph_depth=0),
    )
    return len(candidates)


def _public_context_text(context: MemoryContext) -> str:
    return json.dumps(asdict(context), ensure_ascii=False, sort_keys=True)


def _validate_public_context(
    context: MemoryContext,
    policy: RecallPolicy,
    segment_ids: set[str],
    work_dir: Path,
) -> None:
    _require(context.safe_error_code is None, "recall", "recall_unavailable")
    _require(
        len(context.evidence) <= policy.max_evidence_items,
        "bounds",
        "evidence_count_exceeded",
    )
    _require(
        len(context.rendered_text) <= policy.max_chars,
        "bounds",
        "rendered_text_exceeded",
    )
    for item in context.evidence:
        provenance = item.provenance
        _require(bool(_EVIDENCE_ID.fullmatch(item.evidence_id)), "provenance", "invalid_evidence_id")
        _require(provenance.segment_id in segment_ids, "provenance", "foreign_segment")
        _require(bool(provenance.conversation_id), "provenance", "conversation_id_missing")
        _require(bool(provenance.turn_id), "provenance", "turn_id_missing")
        _require(
            provenance.source_role in {"user", "assistant"},
            "provenance",
            "source_role_invalid",
        )
        _require(provenance.ingestion_version == INGESTION_VERSION, "provenance", "ingestion_version_mismatch")
        _require(bool(provenance.source_timezone), "provenance", "source_timezone_missing")
        _require(provenance.timezone_source == "client", "provenance", "timezone_source_mismatch")
        try:
            source_time = datetime.fromisoformat(provenance.source_timestamp)
        except ValueError as exc:
            raise AcceptanceFailure("provenance", "source_timestamp_invalid") from exc
        _require(
            source_time.tzinfo is not None and source_time.utcoffset() is not None,
            "provenance",
            "source_timestamp_not_aware",
        )
    for source_role, label in (
        ("user", "[USER]"),
        ("assistant", "[LUMINA]"),
    ):
        role_count = sum(
            item.provenance.source_role == source_role
            for item in context.evidence
        )
        _require(
            context.rendered_text.count(label) >= role_count,
            "provenance",
            "model_visible_source_role_missing",
        )

    public = _public_context_text(context)
    lowered = public.lower()
    forbidden = (
        str(work_dir).lower(),
        str(ROOT).lower(),
        "traceback",
        "openai_api_key",
        "networkx",
        "faiss",
        "embedding object",
        "graph object",
    )
    _require(not any(value and value in lowered for value in forbidden), "leak", "public_context_leak")
    _require(_UUID.search(public) is None, "leak", "magma_uuid_leak")


def _evidence_ids(context: MemoryContext) -> tuple[str, ...]:
    return tuple(item.evidence_id for item in context.evidence)


def _provenance_signature(context: MemoryContext) -> tuple[tuple[str, tuple[tuple[str, Any], ...]], ...]:
    return tuple(
        (item.evidence_id, tuple(sorted(asdict(item.provenance).items())))
        for item in context.evidence
    )


def _contains(context: MemoryContext, phrase: str) -> bool:
    expected = phrase.casefold()
    return any(expected in item.text.casefold() for item in context.evidence)


_QUERY_SPECS = (
    ("exact_overlap", "What caused the first experiment to fail?", "solvent evaporated too quickly"),
    ("semantic_paraphrase", "Why did the initial membrane test go wrong?", "solvent evaporated too quickly"),
    ("behavior_change", "What did the user change before repeating the experiment?", "changed the solvent"),
    ("temporal", "When was the membrane experiment completed?", "completed the membrane experiment yesterday"),
    ("entity", "What happened in the membrane experiment?", "membrane experiment"),
    (
        "assistant_self_memory",
        "What did Lumina record about the experiment?",
        "experiment was recorded as completed",
    ),
    ("zh_temporal", "我什么时候完成了膜实验？", "我昨天完成了膜实验"),
    ("zh_previous_week", "上周一发生了什么？", "我上周一更换了溶剂"),
    ("zh_next_week", "我准备什么时候复查？", "下周一准备复查"),
)
_NEGATIVE_QUERY = "Which catalyst was purchased from Sigma-Aldrich?"


def _grounded_bge_segment(
    scenario_id: str,
    turns: tuple[tuple[str, str], ...],
    scenario_index: int,
) -> ColdDraftSegment:
    started_at = datetime(2026, 8, 1, 1, 0, tzinfo=UTC) + timedelta(
        days=scenario_index,
    )
    source_turns = tuple(
        ColdDraftTurn(
            turn_id=f"{scenario_id}-turn-{index}",
            role=role,
            content=text,
            timestamp=started_at + timedelta(minutes=index),
            source_timezone=SOURCE_TIMEZONE,
            timezone_source="client",
        )
        for index, (role, text) in enumerate(turns, start=1)
    )
    return ColdDraftSegment(
        segment_id=f"grounded-bge-{scenario_id}",
        conversation_id=f"grounded-bge-{scenario_id}",
        state="pending_digest",
        turns=source_turns,
        created_at=started_at,
        source_timezone=SOURCE_TIMEZONE,
        schema_version="2",
    )


def _grounded_answer_complete(
    text: str,
    required_groups: tuple[tuple[str, ...], ...],
    forbidden: tuple[str, ...] = (),
) -> bool:
    return (
        all(any(option in text for option in group) for group in required_groups)
        and not any(item in text for item in forbidden)
    )


_GROUNDED_BGE_SCENARIOS = (
    (
        "assistant_contamination_correction_negation",
        (
            ("user", "今天已经很晚了，我准备睡觉。"),
            ("assistant", "早点睡吧，你明天还有实验室例会。"),
            ("user", "我什么时候说过我明天有实验室例会？"),
            ("assistant", "你没有说过，是我刚才自己推测的。"),
            ("user", "我明天其实没有实验室例会。"),
        ),
        (
            (
                "sleep",
                "我准备睡觉了。",
                (("准备睡觉", "早点睡", "sleep", "rest early"),),
                (),
            ),
            (
                "tomorrow_correction",
                "我明天有什么安排？",
                (("没有实验室例会",),),
                ("还有实验室例会",),
            ),
        ),
    ),
    (
        "same_turn_sleep_printer",
        (("user", "我今晚准备早点睡。另外实验室打印机坏了。"),),
        (
            (
                "same_turn_sleep",
                "我准备睡觉了。",
                (("早点睡", "sleep early", "rest early"),),
                (),
            ),
            (
                "same_turn_printer",
                "打印机怎么样了？",
                (("打印机", "printer"), ("坏了", "broken", "broke")),
                (),
            ),
        ),
    ),
    (
        "state_update_control",
        (
            ("user", "我准备最终选择方案 Alpha。"),
            ("assistant", "Alpha 看起来适合当前目标。"),
            ("user", "我还需要比较成本和维护难度。"),
            ("assistant", "可以继续评估两个方案。"),
            ("user", "我改变决定了，最终选择方案 Beta。"),
        ),
        (
            ("current_choice", "我现在最终选择什么？", (("Beta",),), ()),
            ("historical_choice", "我一开始考虑的是什么？", (("Alpha",),), ()),
        ),
    ),
    (
        "xiaolin_shanghai_multiturn",
        (
            ("user", "我的朋友小林下周要出差。"),
            ("assistant", "她去哪？"),
            ("user", "去上海。"),
        ),
        (
            (
                "xiaolin_destination",
                "小林下周去哪？",
                (("小林", "Xiaolin"), ("上海", "Shanghai")),
                (),
            ),
        ),
    ),
    (
        "exact_room_307",
        (
            ("user", "下周三的项目评审安排在海棠会议室 307。"),
            ("assistant", "好的，我会记住活动地点。"),
            ("user", "到场后直接去会议室，不需要先到前台。"),
        ),
        (
            (
                "room_307",
                "下周三项目评审安排在哪个房间？",
                (("307",), ("海棠会议室", "Haitang")),
                (),
            ),
        ),
    ),
    (
        "port_and_unrelated_health",
        (("user", "服务端口改成5433。上线后只检查健康状态。"),),
        (
            ("port_5433", "服务端口是多少？", (("5433",),), ()),
            (
                "health_status",
                "上线后检查什么？",
                (("健康状态", "health status", "health check"),),
                (),
            ),
        ),
    ),
    (
        "uncertainty",
        (("user", "我可能下个月去上海。"),),
        (
            (
                "uncertain_trip",
                "我下个月有什么计划？",
                (
                    ("可能", "may", "might"),
                    ("下个月", "next month"),
                    ("上海", "Shanghai"),
                ),
                (),
            ),
        ),
    ),
)


_GROUNDED_BGE_NEGATIVE_QUERIES = (
    ("python_list_comprehension", "no_answer", "Explain Python list comprehension."),
    ("derivative_x_squared", "no_answer", "What is the derivative of x^2?"),
    (
        "unrelated_translation",
        "no_answer",
        "Translate 'good morning' into French.",
    ),
    ("generic_chemistry", "no_answer", "What is the chemical formula of water?"),
    ("generic_coding", "no_answer", "How do I reverse a list in Python?"),
    ("casual_greeting", "no_answer", "Hello, how are you?"),
    (
        "sleep_vs_python_sleep",
        "near_miss",
        "Python 的 time.sleep() 函数怎么用？",
    ),
    (
        "printer_vs_python_print",
        "near_miss",
        "如何用 Python print 打印列表？",
    ),
    (
        "shanghai_vs_coordinates",
        "near_miss",
        "上海的经纬度是多少？",
    ),
)


def _grounded_bge_private_texts() -> tuple[str, ...]:
    source_texts = tuple(
        text
        for _scenario_id, turns, _queries in _GROUNDED_BGE_SCENARIOS
        for _role, text in turns
    )
    query_texts = tuple(
        query
        for _scenario_id, _turns, queries in _GROUNDED_BGE_SCENARIOS
        for _query_id, query, _required, _forbidden in queries
    )
    negative_query_texts = tuple(
        query for _query_id, _kind, query in _GROUNDED_BGE_NEGATIVE_QUERIES
    )
    return source_texts + query_texts + negative_query_texts


def _run_query_suite(
    retriever: MemoryRetriever,
    policy: RecallPolicy,
    segment_ids: set[str],
    work_dir: Path,
) -> tuple[dict[str, MemoryContext], dict[str, bool]]:
    contexts: dict[str, MemoryContext] = {}
    checks: dict[str, bool] = {}
    for name, query, expected in _QUERY_SPECS:
        context = retriever.recall(query, policy)
        repeated = retriever.recall(query, policy)
        _validate_public_context(context, policy, segment_ids, work_dir)
        _validate_public_context(repeated, policy, segment_ids, work_dir)
        _require(bool(context.evidence), "recall", f"{name}_empty")
        _require(_contains(context, expected), "recall", f"{name}_evidence_missing")
        _require(_evidence_ids(context) == _evidence_ids(repeated), "recall", f"{name}_ordering_unstable")
        contexts[name] = context
        checks[name] = True

    exact_matches = {
        item.evidence_id
        for item in contexts["exact_overlap"].evidence
        if "solvent evaporated too quickly" in item.text.casefold()
    }
    paraphrase_matches = {
        item.evidence_id
        for item in contexts["semantic_paraphrase"].evidence
        if "solvent evaporated too quickly" in item.text.casefold()
    }
    _require(bool(exact_matches & paraphrase_matches), "recall", "cause_evidence_not_shared")

    negative = retriever.recall(_NEGATIVE_QUERY, policy)
    repeated_negative = retriever.recall(_NEGATIVE_QUERY, policy)
    _validate_public_context(negative, policy, segment_ids, work_dir)
    _validate_public_context(repeated_negative, policy, segment_ids, work_dir)
    fabricated_terms = ("catalyst", "sigma-aldrich", "purchased from")
    _require(
        all(
            not any(term in item.text.casefold() for term in fabricated_terms)
            for item in negative.evidence
        ),
        "recall",
        "negative_query_fabricated_evidence",
    )
    _require(
        _evidence_ids(negative) == _evidence_ids(repeated_negative),
        "recall",
        "negative_ordering_unstable",
    )
    contexts["negative"] = negative
    checks["negative_source_bounded"] = True
    return contexts, checks


def _validate_temporal_metadata(
    backend: RealMagmaBackend,
) -> None:
    attributes_by_turn: dict[str, list[dict[str, Any]]] = {}
    for node in backend.trg.graph_db.nodes.values():
        attributes = getattr(node, "attributes", {})
        turn_id = attributes.get("provenance", {}).get("turn_id")
        if isinstance(turn_id, str):
            attributes_by_turn.setdefault(turn_id, []).append(attributes)
    first = attributes_by_turn.get(FIXED_TURNS[0].turn_id, [])
    first_mentions = {
        item.get("original_expression"): item
        for attributes in first
        for item in attributes.get("temporal_mentions", [])
        if isinstance(item, dict)
    }
    for expression, language in (("yesterday", "en"), ("昨天", "zh")):
        mention = first_mentions.get(expression, {})
        _require(
            (
                mention.get("reference_timestamp") == "2026-07-14T02:00:00Z"
                and mention.get("reference_timezone") == SOURCE_TIMEZONE
                and mention.get("normalized_start") == "2026-07-12T16:00:00Z"
                and mention.get("normalized_end") == "2026-07-13T16:00:00Z"
                and mention.get("language") == language
            ),
            "temporal",
            f"{language}_yesterday_normalization_incorrect",
        )
    _require(
        {"original": "昨天", "parsed": "2026-07-12T16:00:00Z"}
        in [
            item
            for attributes in first
            for item in attributes.get("dates_mentioned", [])
        ],
        "temporal",
        "chinese_dates_mentioned_missing",
    )

    schedule = attributes_by_turn.get(FIXED_TURNS[4].turn_id, [])
    schedule_mentions = {
        item.get("original_expression"): item
        for attributes in schedule
        for item in attributes.get("temporal_mentions", [])
        if isinstance(item, dict)
    }
    expected_intervals = {
        "上周一": ("2026-07-05T16:00:00Z", "2026-07-06T16:00:00Z"),
        "下周一": ("2026-07-19T16:00:00Z", "2026-07-20T16:00:00Z"),
    }
    for expression, interval in expected_intervals.items():
        mention = schedule_mentions.get(expression, {})
        _require(
            (
                mention.get("normalized_start"),
                mention.get("normalized_end"),
                mention.get("language"),
            ) == (*interval, "zh"),
            "temporal",
            f"{expression}_normalization_incorrect",
        )


def _validate_report_safety(report: dict[str, Any], work_dir: Path) -> None:
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    lowered = serialized.lower()
    forbidden = [
        str(work_dir).lower(),
        str(ROOT).lower(),
        "traceback",
        "openai_api_key",
        "provider body",
        "networkx",
        "faiss",
    ]
    forbidden.extend(turn.text.casefold() for turn in FIXED_TURNS)
    forbidden.extend(text.casefold() for text in _grounded_bge_private_texts())
    _require(not any(value in lowered for value in forbidden), "report", "report_leak")
    _require(_UUID.search(serialized) is None, "report", "report_uuid_leak")


def _run_grounded_bge_acceptance(
    paths: SandboxPaths,
    report: dict[str, Any],
) -> None:
    report['grounded_bge_acceptance'].update({
        'post_rerank_scoring': 'hindsight',
        'hindsight_commit': (
            'f1c825d88471d069aec0480446d071c589ab10bd'
        ),
        'final_min_score': None,
        'recency': 'linear_365_day_floor_0_1',
        'recency_alpha': 0.2,
        'temporal_signal': 'neutral_0_5',
        'temporal_alpha': 0.2,
        'proof_signal': 'neutral_0_5',
        'proof_count_alpha': 0.1,
        'reference_time': 'latest_candidate_source_timestamp',
    })
    acceptance_root = paths.root / "grounded_bge_acceptance"
    state_store = IngestionStateStore(acceptance_root / "ingestion_state.json")
    timed_backend = _TimedSwitchingBackend()
    adapter = MagmaMemoryAdapter(
        timed_backend,
        state_store,
        ingestion_version=INGESTION_VERSION,
    )
    rows: list[dict[str, Any]] = []
    private_candidate_diagnostics: list[dict[str, Any]] = []
    capacity_diagnostics: list[dict[str, Any]] = []
    instance_ids: set[int] = set()
    graphiti_rows: list[dict[str, Any]] = []
    graph_added_candidates: set[str] = set()
    graph_added_required_evidence: set[str] = set()
    meeting_roles: set[str] = set()
    xiaolin_passed = False
    stability_cases = 0

    class EarlyWallClock(datetime):
        @classmethod
        def now(cls, timezone):
            return cls(1990, 1, 1, tzinfo=timezone)

    class LateWallClock(datetime):
        @classmethod
        def now(cls, timezone):
            return cls(2090, 1, 1, tzinfo=timezone)

    with _capture_bge_runtime() as bge_runtime:
        lazy_before_first_recall = (
            bge_runtime["factory_calls"] == 0
            and bge_runtime["instance"] is None
        )

        def run_graphiti_probe(
            query: str,
            policy: RecallPolicy,
            *,
            segment_id: str,
        ) -> dict[str, Any]:
            active_reranker = bge_runtime["instance"]
            previous_calls = (
                active_reranker.calls
                if isinstance(active_reranker, _TimedBgeReranker)
                else 0
            )
            if isinstance(active_reranker, _TimedBgeReranker):
                active_reranker.last_ms = 0.0
                active_reranker.last_scores = ()
            started = perf_counter()
            context = _silenced(adapter.recall, query, policy)
            total_ms = (perf_counter() - started) * 1000.0
            reranker = bge_runtime["instance"]
            bge_ms = (
                reranker.last_ms
                if isinstance(reranker, _TimedBgeReranker)
                and reranker.calls > previous_calls
                else 0.0
            )
            if isinstance(reranker, _TimedBgeReranker):
                instance_ids.add(id(reranker))
            _validate_public_context(
                context,
                policy,
                {segment_id},
                paths.root,
            )
            _require(
                0 <= timed_backend.last_count <= policy.max_nodes,
                "grounded_bge",
                "graphiti_candidate_count_out_of_bounds",
            )
            _require(
                all(
                    not hasattr(evidence, "score")
                    for evidence in context.evidence
                ),
                "grounded_bge",
                "public_bge_score_exposed",
            )
            return {
                "context": context,
                "candidates": tuple(timed_backend.last_candidates),
                "candidate_count": timed_backend.last_count,
                "magma_ms": timed_backend.last_ms,
                "bge_ms": bge_ms,
                "total_ms": total_ms,
            }

        def run_stability_probe(
            query: str,
            policy: RecallPolicy,
            *,
            segment_id: str,
        ) -> dict[str, Any]:
            nonlocal stability_cases
            original_datetime = magma_adapter_module.datetime
            try:
                magma_adapter_module.datetime = EarlyWallClock
                early = run_graphiti_probe(
                    query,
                    policy,
                    segment_id=segment_id,
                )
                magma_adapter_module.datetime = LateWallClock
                late = run_graphiti_probe(
                    query,
                    policy,
                    segment_id=segment_id,
                )
            finally:
                magma_adapter_module.datetime = original_datetime
            _require(
                early['context'] == late['context'],
                'grounded_bge',
                'wall_clock_shift_changed_top3',
            )
            stability_cases += 1
            return early

        depth0_policy = RecallPolicy(
            top_k=10,
            max_graph_depth=0,
            max_nodes=20,
            max_evidence_items=3,
            max_chars=5000,
        )
        depth1_policy = replace(depth0_policy, max_graph_depth=1)
        for scenario_index, (scenario_id, turns, queries) in enumerate(
            _GROUNDED_BGE_SCENARIOS
        ):
            scenario_root = acceptance_root / scenario_id
            try:
                backend = _silenced(
                    RealMagmaBackend,
                    scenario_root / "magma",
                )
            except Exception as exc:
                raise AcceptanceFailure(
                    "grounded_bge",
                    "scenario_magma_initialization_failed",
                ) from exc
            timed_backend.switch(backend)
            segment = _grounded_bge_segment(
                scenario_id,
                turns,
                scenario_index,
            )
            ingestion = _silenced(adapter.ingest, segment)
            expected_units = build_grounded_spans(segment.turns)
            _require(
                ingestion.status == "completed"
                and len(ingestion.memory_ids) == len(expected_units),
                "grounded_bge",
                "production_grounded_ingestion_failed",
            )

            for query_id, query, required_groups, forbidden in queries:
                depth0 = run_graphiti_probe(
                    query,
                    depth0_policy,
                    segment_id=segment.segment_id,
                )
                depth1 = run_stability_probe(
                    query,
                    depth1_policy,
                    segment_id=segment.segment_id,
                )
                depth0_context = depth0["context"]
                depth1_context = depth1["context"]
                depth0_complete = _grounded_answer_complete(
                    depth0_context.rendered_text,
                    required_groups,
                )
                depth1_complete = _grounded_answer_complete(
                    depth1_context.rendered_text,
                    required_groups,
                )
                depth0_ids = {
                    candidate.metadata.get("evidence_id")
                    for candidate in depth0["candidates"]
                    if isinstance(candidate.metadata.get("evidence_id"), str)
                }
                depth1_ids = {
                    candidate.metadata.get("evidence_id")
                    for candidate in depth1["candidates"]
                    if isinstance(candidate.metadata.get("evidence_id"), str)
                }
                added_ids = depth1_ids - depth0_ids
                if added_ids:
                    graph_added_candidates.add(query_id)
                if depth1_complete and not depth0_complete:
                    graph_added_required_evidence.add(query_id)
                if query_id == "tomorrow_correction":
                    meeting_roles.update(
                        item.provenance.source_role
                        for item in depth1_context.evidence
                    )
                if query_id == "xiaolin_destination":
                    matching_items = [
                        item
                        for item in depth1_context.evidence
                        if any(
                            any(option in item.text for option in group)
                            for group in required_groups
                        )
                    ]
                    xiaolin_passed = (
                        depth1_complete
                        and len({item.evidence_id for item in matching_items}) >= 2
                        and all(
                            item.provenance.source_role == "user"
                            for item in matching_items
                        )
                    )
                graphiti_rows.append(
                    {
                        "case_id": query_id,
                        "kind": "positive",
                        "depth0_complete": depth0_complete,
                        "depth1_complete": depth1_complete,
                        "depth0_empty": not depth0_context.evidence,
                        "depth1_empty": not depth1_context.evidence,
                        "depth0_candidate_count": depth0["candidate_count"],
                        "depth1_candidate_count": depth1["candidate_count"],
                        "depth0_bge_ms": round(depth0["bge_ms"], 3),
                        "depth1_bge_ms": round(depth1["bge_ms"], 3),
                        "depth0_total_ms": round(depth0["total_ms"], 3),
                        "depth1_total_ms": round(depth1["total_ms"], 3),
                    }
                )
                top2_policy = replace(
                    depth1_policy,
                    max_evidence_items=2,
                )
                started = perf_counter()
                context_top2 = _silenced(
                    adapter.recall,
                    query,
                    top2_policy,
                )
                total_ms = (perf_counter() - started) * 1000.0
                reranker = bge_runtime["instance"]
                _require(
                    isinstance(reranker, _TimedBgeReranker),
                    "grounded_bge",
                    "bge_not_loaded",
                )
                instance_ids.add(id(reranker))
                magma_ms = timed_backend.last_ms
                bge_ms = reranker.last_ms
                candidate_count = timed_backend.last_count
                magma_candidates = tuple(timed_backend.last_candidates)
                bge_scores = tuple(reranker.last_scores)

                _validate_public_context(
                    context_top2,
                    top2_policy,
                    {segment.segment_id},
                    paths.root,
                )
                _require(
                    0 < candidate_count <= top2_policy.max_nodes,
                    "grounded_bge",
                    "candidate_count_out_of_bounds",
                )
                _require(
                    all(
                        not hasattr(evidence, "score")
                        for evidence in context_top2.evidence
                    ),
                    "grounded_bge",
                    "public_bge_score_exposed",
                )
                private_rerankable = tuple(
                    (index, candidate)
                    for index, candidate in enumerate(magma_candidates)
                    if isinstance(candidate.text, str) and candidate.text.strip()
                )
                _require(
                    len(private_rerankable) == candidate_count
                    and len(bge_scores) == candidate_count,
                    'grounded_bge',
                    'private_diagnostic_shape_mismatch',
                )
                ranked_candidates = tuple(
                    candidate
                    for (_original, candidate), _score in sorted(
                        zip(private_rerankable, bge_scores, strict=True),
                        key=lambda item: (-item[1], item[0][0]),
                    )
                )
                magma_group_presence = tuple(
                    any(
                        any(option in candidate.text for option in group)
                        for candidate in magma_candidates
                    )
                    for group in required_groups
                )
                required_group_bge_ranks = tuple(
                    next(
                        (
                            rank
                            for rank, candidate in enumerate(
                                ranked_candidates,
                                start=1,
                            )
                            if any(
                                option in candidate.text
                                for option in group
                            )
                        ),
                        None,
                    )
                    for group in required_groups
                )
                private_candidate_diagnostics.append(
                    {
                        'query': query_id,
                        'magma': tuple(
                            {
                                'text': candidate.text,
                                'source_role': candidate.metadata.get(
                                    'provenance',
                                    {},
                                ).get('source_role'),
                                'oracle_required': any(
                                    any(
                                        option in candidate.text
                                        for option in group
                                    )
                                    for group in required_groups
                                ),
                            }
                            for candidate in magma_candidates
                        ),
                        'bge': tuple(
                            {
                                'text': candidate.text,
                                'source_role': candidate.metadata.get(
                                    'provenance',
                                    {},
                                ).get('source_role'),
                                'oracle_required': any(
                                    any(
                                        option in candidate.text
                                        for option in group
                                    )
                                    for group in required_groups
                                ),
                            }
                            for candidate in ranked_candidates
                        ),
                    }
                )
                top1_text = (
                    context_top2.evidence[0].text
                    if context_top2.evidence
                    else ""
                )
                top1_correct = _grounded_answer_complete(
                    top1_text,
                    required_groups,
                    forbidden,
                )
                top2_complete = _grounded_answer_complete(
                    context_top2.rendered_text,
                    required_groups,
                    forbidden,
                )

                contexts_by_count = {2: context_top2}
                for evidence_count in (1, 3, 4, 5):
                    diagnostic_policy = replace(
                        top2_policy,
                        max_evidence_items=evidence_count,
                    )
                    diagnostic_context = _silenced(
                        adapter.recall,
                        query,
                        diagnostic_policy,
                    )
                    _validate_public_context(
                        diagnostic_context,
                        diagnostic_policy,
                        {segment.segment_id},
                        paths.root,
                    )
                    contexts_by_count[evidence_count] = diagnostic_context
                complete_by_count = {
                    evidence_count: _grounded_answer_complete(
                        contexts_by_count[evidence_count].rendered_text,
                        required_groups,
                        forbidden,
                    )
                    for evidence_count in range(1, 6)
                }
                top3_complete = complete_by_count[3]
                capacity_diagnostics.append(
                    {
                        'query': query_id,
                        'required_group_bge_ranks': required_group_bge_ranks,
                        'required_groups_present_in_magma': (
                            magma_group_presence
                        ),
                        'answer_complete_by_evidence_count': complete_by_count,
                        'evidence_count_by_bound': {
                            count: len(context.evidence)
                            for count, context in contexts_by_count.items()
                        },
                        'rendered_chars_by_bound': {
                            count: len(context.rendered_text)
                            for count, context in contexts_by_count.items()
                        },
                    }
                )
                rendered_chars = len(context_top2.rendered_text)
                rows.append(
                    {
                        "scenario": scenario_id,
                        "query": query_id,
                        "top1": top1_correct,
                        "top2_answer_complete": top2_complete,
                        "top3_answer_complete": top3_complete,
                        "candidate_count": candidate_count,
                        "magma_ms": round(magma_ms, 3),
                        "bge_ms": round(bge_ms, 3),
                        "total_ms": round(total_ms, 3),
                        "top2_rendered_chars": rendered_chars,
                        "top2_token_estimate": (rendered_chars + 3) // 4,
                    }
                )

        combined_turns = tuple(
            turn
            for _scenario_id, turns, _queries in _GROUNDED_BGE_SCENARIOS
            for turn in turns
        )
        combined_segment = _grounded_bge_segment(
            "combined_negative",
            combined_turns,
            len(_GROUNDED_BGE_SCENARIOS),
        )
        try:
            combined_backend = _silenced(
                RealMagmaBackend,
                acceptance_root / "combined_negative" / "magma",
            )
        except Exception as exc:
            raise AcceptanceFailure(
                "grounded_bge",
                "combined_magma_initialization_failed",
            ) from exc
        timed_backend.switch(combined_backend)
        combined_ingestion = _silenced(adapter.ingest, combined_segment)
        combined_units = build_grounded_spans(combined_segment.turns)
        _require(
            combined_ingestion.status == "completed"
            and len(combined_ingestion.memory_ids) == len(combined_units),
            "grounded_bge",
            "combined_grounded_ingestion_failed",
        )
        for query_id, kind, query in _GROUNDED_BGE_NEGATIVE_QUERIES:
            depth0 = run_graphiti_probe(
                query,
                depth0_policy,
                segment_id=combined_segment.segment_id,
            )
            depth1 = run_stability_probe(
                query,
                depth1_policy,
                segment_id=combined_segment.segment_id,
            )
            depth0_ids = {
                candidate.metadata.get("evidence_id")
                for candidate in depth0["candidates"]
                if isinstance(candidate.metadata.get("evidence_id"), str)
            }
            depth1_ids = {
                candidate.metadata.get("evidence_id")
                for candidate in depth1["candidates"]
                if isinstance(candidate.metadata.get("evidence_id"), str)
            }
            if depth1_ids - depth0_ids:
                graph_added_candidates.add(query_id)
            graphiti_rows.append(
                {
                    "case_id": query_id,
                    "kind": kind,
                    "depth0_complete": None,
                    "depth1_complete": None,
                    "depth0_empty": not depth0["context"].evidence,
                    "depth1_empty": not depth1["context"].evidence,
                    "depth0_candidate_count": depth0["candidate_count"],
                    "depth1_candidate_count": depth1["candidate_count"],
                    "depth0_bge_ms": round(depth0["bge_ms"], 3),
                    "depth1_bge_ms": round(depth1["bge_ms"], 3),
                    "depth0_total_ms": round(depth0["total_ms"], 3),
                    "depth1_total_ms": round(depth1["total_ms"], 3),
                }
            )

        factory_calls = int(bge_runtime["factory_calls"])
        reranker = bge_runtime["instance"]
        instance_reuse = (
            factory_calls == 1
            and len(instance_ids) == 1
            and isinstance(reranker, _TimedBgeReranker)
            and reranker.calls
            == len(rows) * 8 + len(_GROUNDED_BGE_NEGATIVE_QUERIES) * 3
        )

    _require(len(rows) == 11, "grounded_bge", "query_count_mismatch")
    top1 = sum(bool(row["top1"]) for row in rows)
    top2 = sum(bool(row["top2_answer_complete"]) for row in rows)
    top3 = sum(bool(row["top3_answer_complete"]) for row in rows)
    _require(
        stability_cases == 20,
        'grounded_bge',
        'stability_case_count_mismatch',
    )
    positive_graphiti_rows = [
        row for row in graphiti_rows if row["kind"] == "positive"
    ]
    no_answer_rows = [
        row for row in graphiti_rows if row["kind"] == "no_answer"
    ]
    near_miss_rows = [
        row for row in graphiti_rows if row["kind"] == "near_miss"
    ]
    depth0_positive = sum(
        bool(row["depth0_complete"]) for row in positive_graphiti_rows
    )
    depth1_positive = sum(
        bool(row["depth1_complete"]) for row in positive_graphiti_rows
    )
    no_answer_empty_depth0 = sum(
        bool(row["depth0_empty"]) for row in no_answer_rows
    )
    no_answer_empty_depth1 = sum(
        bool(row["depth1_empty"]) for row in no_answer_rows
    )
    near_miss_empty_depth0 = sum(
        bool(row["depth0_empty"]) for row in near_miss_rows
    )
    near_miss_empty_depth1 = sum(
        bool(row["depth1_empty"]) for row in near_miss_rows
    )
    suite_complete_by_count = {
        evidence_count: sum(
            bool(item['answer_complete_by_evidence_count'][evidence_count])
            for item in capacity_diagnostics
        )
        for evidence_count in range(1, 6)
    }
    minimum_global_evidence_count = next(
        (
            evidence_count
            for evidence_count, complete in suite_complete_by_count.items()
            if complete == len(rows)
        ),
        None,
    )
    xiaolin_diagnostic = next(
        item
        for item in capacity_diagnostics
        if item['query'] == 'xiaolin_destination'
    )
    report["grounded_bge_acceptance"].update(
        {
            "depth0_positive_complete": depth0_positive,
            "depth1_positive_complete": depth1_positive,
            "no_answer_empty_depth0": no_answer_empty_depth0,
            "no_answer_empty_depth1": no_answer_empty_depth1,
            "near_miss_empty_depth0": near_miss_empty_depth0,
            "near_miss_empty_depth1": near_miss_empty_depth1,
            "graph_added_candidates": sorted(graph_added_candidates),
            "graph_added_required_evidence": sorted(
                graph_added_required_evidence
            ),
            "xiaolin": xiaolin_passed,
            "meeting_roles": sorted(meeting_roles),
            "meeting_role_evidence": meeting_roles == {"user", "assistant"},
            "median_recall_latency_depth0": round(
                median(row["depth0_total_ms"] for row in graphiti_rows),
                3,
            ),
            "median_recall_latency_depth1": round(
                median(row["depth1_total_ms"] for row in graphiti_rows),
                3,
            ),
            "median_bge_latency_depth0": round(
                median(row["depth0_bge_ms"] for row in graphiti_rows),
                3,
            ),
            "median_bge_latency_depth1": round(
                median(row["depth1_bge_ms"] for row in graphiti_rows),
                3,
            ),
            "median_candidate_count_depth0": median(
                row["depth0_candidate_count"] for row in graphiti_rows
            ),
            "median_candidate_count_depth1": median(
                row["depth1_candidate_count"] for row in graphiti_rows
            ),
            "graphiti_rows": graphiti_rows,
            "top1": top1,
            "top2_answer_complete": top2,
            "top3_answer_complete": top3,
            "lazy_load": lazy_before_first_recall and factory_calls == 1,
            "instance_reuse": instance_reuse,
            "factory_calls": factory_calls,
            "median_magma_ms": round(
                median(row["magma_ms"] for row in rows),
                3,
            ),
            "median_bge_ms": round(
                median(row["bge_ms"] for row in rows),
                3,
            ),
            "median_total_ms": round(
                median(row["total_ms"] for row in rows),
                3,
            ),
            "median_candidate_count": median(
                row["candidate_count"] for row in rows
            ),
            "median_top2_rendered_chars": median(
                row["top2_rendered_chars"] for row in rows
            ),
            "median_top2_token_estimate": median(
                row["top2_token_estimate"] for row in rows
            ),
            "rows": rows,
        }
    )
    report['grounded_bge_acceptance'][
        'repeated_run_top3_identical'
    ] = stability_cases == 20
    report['grounded_bge_acceptance'][
        'wall_clock_shift_top3_identical'
    ] = stability_cases == 20
    report['grounded_bge_acceptance']['capacity_diagnostics'] = json.loads(
        json.dumps({
        'suite_complete_by_evidence_count': suite_complete_by_count,
        'minimum_global_evidence_count': minimum_global_evidence_count,
        'xiaolin_required_user_span_bge_ranks': (
            xiaolin_diagnostic['required_group_bge_ranks']
        ),
        'xiaolin_required_spans_present_in_magma': sum(
            bool(value)
            for value in xiaolin_diagnostic[
                'required_groups_present_in_magma'
            ]
        ),
        'median_evidence_count_by_bound': {
            evidence_count: median(
                item['evidence_count_by_bound'][evidence_count]
                for item in capacity_diagnostics
            )
            for evidence_count in range(1, 6)
        },
        'max_evidence_count_by_bound': {
            evidence_count: max(
                item['evidence_count_by_bound'][evidence_count]
                for item in capacity_diagnostics
            )
            for evidence_count in range(1, 6)
        },
        'median_rendered_chars_by_bound': {
            evidence_count: median(
                item['rendered_chars_by_bound'][evidence_count]
                for item in capacity_diagnostics
            )
            for evidence_count in range(1, 6)
        },
        'max_rendered_chars_by_bound': {
            evidence_count: max(
                item['rendered_chars_by_bound'][evidence_count]
                for item in capacity_diagnostics
            )
            for evidence_count in range(1, 6)
        },
        'approx_median_tokens_by_bound': {
            evidence_count: median(
                (
                    item['rendered_chars_by_bound'][evidence_count] + 3
                ) // 4
                for item in capacity_diagnostics
            )
            for evidence_count in range(1, 6)
        },
        'approx_max_tokens_by_bound': {
            evidence_count: max(
                (
                    item['rendered_chars_by_bound'][evidence_count] + 3
                ) // 4
                for item in capacity_diagnostics
            )
            for evidence_count in range(1, 6)
        },
            'rows': capacity_diagnostics,
        })
    )
    _require(
        len(private_candidate_diagnostics) == len(rows)
        and all(
            candidate['source_role'] in {'user', 'assistant'}
            for item in private_candidate_diagnostics
            for stage in ('magma', 'bge')
            for candidate in item[stage]
        ),
        'grounded_bge',
        'private_candidate_diagnostic_invalid',
    )
    _require(
        depth1_positive == 11,
        "grounded_bge",
        "production_depth1_required_evidence_incomplete",
    )
    _require(
        xiaolin_passed,
        "grounded_bge",
        "xiaolin_required_user_spans_incomplete",
    )
    _require(
        meeting_roles == {"user", "assistant"},
        "grounded_bge",
        "meeting_role_evidence_incomplete",
    )
    _require(
        report["grounded_bge_acceptance"]["assistant_self_memory"],
        "grounded_bge",
        "assistant_self_memory_incomplete",
    )
    _require(
        lazy_before_first_recall and factory_calls == 1,
        "grounded_bge",
        "bge_not_lazy_loaded_once",
    )
    _require(
        instance_reuse,
        "grounded_bge",
        "bge_instance_not_reused",
    )
    report["grounded_bge_acceptance"]["result"] = "PASS"


def _verbose(enabled: bool, message: str) -> None:
    if enabled:
        print(f"Recall E2E step: {message}")


def _silenced(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return call(*args, **kwargs)


def _execute_pipeline(
    paths: SandboxPaths,
    report: dict[str, Any],
    *,
    verbose: bool,
) -> None:
    paths.logs.mkdir(parents=True, exist_ok=True)
    hot_store = JsonlDraftStore(paths.hot_draft)
    cold_store = ColdDraftStore(paths.cold_draft)
    all_turns = (*FIXED_TURNS, *_HOT_TAIL)
    fixed_times = [datetime.fromisoformat(turn.timestamp) for turn in FIXED_TURNS]
    _require(fixed_times == sorted(fixed_times), "draft", "fixed_timestamps_not_ordered")
    for turn in all_turns:
        hot_store.append_turn(_draft_turn(turn))
    stored_hot = hot_store.list_recent(limit=len(all_turns))
    _require(
        stored_hot == [_draft_turn(turn) for turn in all_turns],
        "draft",
        "hot_draft_order_mismatch",
    )
    restarted_hot = JsonlDraftStore(paths.hot_draft).list_recent(limit=len(all_turns))
    _require(restarted_hot == stored_hot, "draft", "hot_draft_restart_provenance_changed")
    _require(
        len({turn.turn_id for turn in stored_hot}) == len(all_turns),
        "draft",
        "hot_draft_turn_ids_not_unique",
    )
    _verbose(verbose, "synthetic Hot Draft written")

    compactor = HotDraftCompactor(
        hot_store,
        cold_store,
        paths.compaction_state,
        summarizer=_fake_rolling_summarizer,
        retain_recent_raw_turns=2,
        max_raw_turns_before_compression=6,
    )
    compaction = compactor.maybe_compact()
    _require(compaction.status == "completed", "compaction", "cold_first_compaction_failed")
    pending = ColdDraftStore(paths.cold_draft).list_pending(limit=10)
    _require(len(pending) == 1, "compaction", "pending_segment_count_mismatch")
    source_record = pending[0]
    _require(source_record.get("schema_version") == 2, "compaction", "cold_draft_schema_not_v2")
    source_turns = [(item["role"], item["text"]) for item in source_record["turns"]]
    expected_source_turns = [(turn.role, turn.text) for turn in FIXED_TURNS]
    _require(source_turns == expected_source_turns, "compaction", "cold_draft_content_mismatch")
    expected_cold_turns = [_draft_turn(turn).storage_turn() for turn in FIXED_TURNS]
    _require(
        source_record["turns"] == expected_cold_turns,
        "compaction",
        "cold_draft_turn_provenance_changed",
    )
    physical_cold_before = _read_jsonl(paths.cold_draft)
    _require(
        len(physical_cold_before) == len(expected_cold_turns),
        "compaction",
        "cold_draft_physical_line_count_mismatch",
    )
    _require(
        all(
            item.get("record_type") == "cold_turn"
            and item.get("segment_id") == source_record["segment_id"]
            and item.get("segment_turn_count") == len(expected_cold_turns)
            and item.get("state") == "pending_digest"
            for item in physical_cold_before
        ),
        "compaction",
        "cold_draft_physical_segment_metadata_mismatch",
    )
    _require(
        [item.get("segment_turn_index") for item in physical_cold_before]
        == list(range(len(expected_cold_turns))),
        "compaction",
        "cold_draft_physical_turn_index_mismatch",
    )
    _require(
        [
            {
                key: item[key]
                for key in (
                    "turn_id",
                    "role",
                    "text",
                    "created_at",
                    "source_timezone",
                    "timezone_source",
                )
            }
            for item in physical_cold_before
        ]
        == expected_cold_turns,
        "compaction",
        "cold_draft_physical_turn_provenance_changed",
    )
    hot_context = hot_store.read_context()
    expected_hot_tail = tuple(_draft_turn(turn) for turn in _HOT_TAIL)
    expected_summary = _fake_rolling_summarizer(
        None,
        [_draft_turn(turn) for turn in FIXED_TURNS],
    )
    _require(
        hot_context.raw_turns == expected_hot_tail,
        "compaction",
        "hot_draft_recent_tail_mismatch",
    )
    _require(
        hot_context.summary is not None
        and hot_context.summary.content == expected_summary
        and hot_context.summary.generation == 1
        and hot_context.summary.source_turn_count == len(FIXED_TURNS),
        "compaction",
        "hot_draft_summary_mismatch",
    )
    physical_hot_records = [
        json.loads(line)
        for line in paths.hot_draft.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    _require(
        sum(record.get("record_type") == "summary" for record in physical_hot_records)
        == 1
        and physical_hot_records[0] == hot_context.summary.storage_record()
        and physical_hot_records[1:]
        == [
            {
                **turn.storage_turn(),
                "schema_version": 2,
                "source": "chat_draft",
                "safe": True,
            }
            for turn in expected_hot_tail
        ],
        "compaction",
        "hot_draft_physical_layout_mismatch",
    )
    restarted_hot_context = JsonlDraftStore(paths.hot_draft).read_context()
    _require(
        restarted_hot_context == hot_context,
        "compaction",
        "hot_draft_compacted_restart_mismatch",
    )
    converted = ColdDraftSegmentConverter().convert(source_record, INGESTION_VERSION)
    _require(
        [turn.turn_id for turn in converted.turns]
        == [turn.turn_id for turn in FIXED_TURNS],
        "provenance",
        "production_turn_id_mapping_mismatch",
    )
    _require(
        [turn.timestamp for turn in converted.turns]
        == [_draft_turn(turn).created_at for turn in FIXED_TURNS],
        "provenance",
        "production_timestamp_mapping_mismatch",
    )
    _require(
        all(
            turn.source_timezone == SOURCE_TIMEZONE
            and turn.timezone_source == "client"
            for turn in converted.turns
        ),
        "provenance",
        "production_timezone_mapping_mismatch",
    )
    segment_ids = {source_record["segment_id"]}
    grounded_units = build_grounded_spans(converted.turns)
    report["cold_draft"].update(
        {
            "compacted": True,
            "pending_created": len(pending),
            "raw_order_preserved": True,
        }
    )
    _verbose(verbose, "Cold-first pending segment created")

    try:
        backend = _silenced(RealMagmaBackend, paths.magma)
    except Exception as exc:
        raise AcceptanceFailure("magma", "magma_initialization_failed") from exc
    state_store = IngestionStateStore(paths.ingestion_state)
    adapter = MagmaMemoryAdapter(
        backend,
        state_store,
        ingestion_version=INGESTION_VERSION,
    )
    runner = DreamRunner(
        cold_store,
        ColdDraftDigestionTask(cold_store, _FixedIngestorProvider(adapter)),
    )
    first_dream = _silenced(
        runner.run_once,
        DreamRunPolicy(
            max_segments=10,
            stop_on_error=False,
            ingestion_version=INGESTION_VERSION,
        ),
    )
    _require(first_dream.attempted == 1, "dream", "dream_segment_attempt_mismatch")
    _require(first_dream.failed == 0, "dream", "dream_failed")
    _require(
        all(item.status == "consumed" and item.consumed for item in first_dream.results),
        "dream",
        "dream_not_consumed",
    )
    state_after_first = paths.ingestion_state.read_bytes()
    state_records = state_store.read_all()
    key = state_store.key(source_record["segment_id"], INGESTION_VERSION)
    _require(state_records.get(key, {}).get("status") == "completed", "dream", "ingestion_not_completed")
    cold_records = _read_jsonl(paths.cold_draft)
    consumed_records = [
        item
        for item in cold_records
        if item.get("segment_id") == source_record["segment_id"]
    ]
    _require(
        len(consumed_records) == len(physical_cold_before),
        "dream",
        "consumed_record_count_mismatch",
    )
    _require(
        all(item.get("state") == "consumed" for item in consumed_records)
        and len({item.get("consumed_at") for item in consumed_records}) == 1,
        "dream",
        "cold_draft_not_consumed_as_segment",
    )
    for original, consumed in zip(
        physical_cold_before,
        consumed_records,
        strict=True,
    ):
        unchanged = dict(consumed)
        unchanged.pop("consumed_at", None)
        unchanged["state"] = "pending_digest"
        _require(
            unchanged == original,
            "dream",
            "cold_draft_raw_content_changed",
        )
    _require(
        ColdDraftStore(paths.cold_draft).list_pending(limit=10) == [],
        "dream",
        "consumed_segment_still_pending",
    )
    report["dream"].update({"attempted": first_dream.attempted, "failed": first_dream.failed})
    report["cold_draft"]["consumed"] = 1
    _verbose(verbose, "Dream completed and source consumed")

    node_count, vector_count = _memory_counts(backend)
    _require(
        node_count == len(grounded_units),
        "magma",
        "magma_event_count_mismatch",
    )
    _require(
        vector_count == len(grounded_units),
        "magma",
        "magma_vector_count_mismatch",
    )
    events_by_evidence_id = {
        attributes.get("evidence_id"): (node, attributes)
        for node in backend.trg.graph_db.nodes.values()
        for attributes in [getattr(node, "attributes", {})]
    }
    turns_by_id = {turn.turn_id: turn for turn in converted.turns}
    for unit in grounded_units:
        stored = events_by_evidence_id.get(unit.unit_id)
        _require(stored is not None, "magma", "grounded_event_missing")
        node, attributes = stored
        source_turn = turns_by_id[unit.turn_id]
        expected_timestamp = source_turn.timestamp.isoformat()
        provenance = attributes.get("provenance", {})
        _require(
            getattr(node, "content_narrative", None) == unit.text
            and source_turn.content[unit.start:unit.end] == unit.text,
            "magma",
            "grounded_event_source_mismatch",
        )
        _require(
            attributes.get("source_start") == unit.start
            and attributes.get("source_end") == unit.end
            and attributes.get("role") == unit.source_role
            and unit.source_role == source_turn.role,
            "provenance",
            "grounded_offsets_mismatch",
        )
        _require(
            getattr(node, "timestamp", None).isoformat() == expected_timestamp,
            "magma",
            "magma_event_timestamp_mismatch",
        )
        _require(
            provenance.get("turn_id") == unit.turn_id
            and provenance.get("source_role") == unit.source_role
            and provenance.get("source_timestamp") == expected_timestamp
            and provenance.get("source_timezone") == SOURCE_TIMEZONE
            and provenance.get("timezone_source") == "client",
            "provenance",
            "grounded_event_provenance_mismatch",
        )
    report["magma"].update(
        {"events": node_count, "vectors": vector_count, "persisted": True}
    )

    policy = RecallPolicy(
        top_k=5,
        max_chars=1200,
        max_evidence_items=5,
        max_graph_depth=6,
        max_nodes=200,
    )
    contexts, checks = _silenced(
        _run_query_suite,
        adapter,
        policy,
        segment_ids,
        paths.root,
    )
    expected_recall_provenance = {
        turn.turn_id: (
            turn.role,
            turn.timestamp.isoformat(),
            turn.source_timezone,
            turn.timezone_source,
        )
        for turn in converted.turns
    }
    for context in contexts.values():
        for evidence in context.evidence:
            expected = expected_recall_provenance.get(evidence.provenance.turn_id)
            _require(expected is not None, "provenance", "recall_turn_id_unknown")
            _require(
                (
                    evidence.provenance.source_role,
                    evidence.provenance.source_timestamp,
                    evidence.provenance.source_timezone,
                    evidence.provenance.timezone_source,
                )
                == expected,
                "provenance",
                "recall_turn_provenance_mismatch",
            )
    report["recall"].update(
        {"passed": 10, "failed": 0, "checks": checks}
    )
    _validate_temporal_metadata(backend)
    report["provenance"].update(
        {"passed": True, "temporal_normalization_passed": True}
    )
    _verbose(verbose, "ten-query recall suite passed")

    top_one_policy = RecallPolicy(
        top_k=1,
        max_chars=1200,
        max_evidence_items=5,
        max_graph_depth=6,
        max_nodes=200,
    )
    max_two_policy = RecallPolicy(
        top_k=10,
        max_chars=1200,
        max_evidence_items=2,
        max_graph_depth=6,
        max_nodes=200,
    )
    max_chars_policy = RecallPolicy(
        top_k=5,
        max_chars=120,
        max_evidence_items=5,
        max_graph_depth=6,
        max_nodes=200,
    )
    top_one = _silenced(
        adapter.recall,
        _QUERY_SPECS[0][1],
        top_one_policy,
    )
    max_two = _silenced(
        adapter.recall,
        _QUERY_SPECS[0][1],
        max_two_policy,
    )
    max_chars = _silenced(
        adapter.recall,
        _QUERY_SPECS[0][1],
        max_chars_policy,
    )
    for context, context_policy in (
        (top_one, top_one_policy),
        (max_two, max_two_policy),
        (max_chars, max_chars_policy),
    ):
        _validate_public_context(context, context_policy, segment_ids, paths.root)
    top_one_anchor_count = _silenced(
        _query_anchor_count,
        backend,
        _QUERY_SPECS[0][1],
        top_one_policy,
    )
    _require(
        0 < top_one_anchor_count <= top_one_policy.top_k,
        "bounds",
        "top_k_anchor_limit_not_enforced",
    )
    _require(len(max_two.evidence) <= 2, "bounds", "max_evidence_items_not_enforced")
    _require(len(max_chars.rendered_text) <= 120, "bounds", "max_chars_not_enforced")
    _require(max_chars.truncated, "bounds", "max_chars_truncation_flag_missing")
    report["bounds"].update(
        {"top_k": True, "max_evidence_items": True, "max_chars": True}
    )

    evidence_before = {
        name: _evidence_ids(context) for name, context in contexts.items()
    }
    provenance_before = {
        name: _provenance_signature(context) for name, context in contexts.items()
    }
    second_dream = _silenced(
        runner.run_once,
        DreamRunPolicy(
            max_segments=10,
            stop_on_error=False,
            ingestion_version=INGESTION_VERSION,
        ),
    )
    second_node_count, second_vector_count = _memory_counts(backend)
    state_after_second = paths.ingestion_state.read_bytes()
    repeated_context = _silenced(adapter.recall, _QUERY_SPECS[0][1], policy)
    _require(second_dream.attempted == 0, "idempotency", "second_dream_reprocessed_consumed")
    _require(second_node_count == node_count, "idempotency", "node_count_changed")
    _require(second_vector_count == vector_count, "idempotency", "vector_count_changed")
    _require(state_after_second == state_after_first, "idempotency", "ingestion_state_changed")
    _require(
        _evidence_ids(repeated_context) == evidence_before["exact_overlap"],
        "idempotency",
        "evidence_ids_changed",
    )
    report["dream"]["second_attempted"] = second_dream.attempted
    report["idempotency"].update(
        {
            "passed": True,
            "node_count_stable": True,
            "vector_count_stable": True,
            "state_stable": True,
            "evidence_ids_stable": True,
        }
    )
    _verbose(verbose, "second Dream run remained idempotent")

    del runner, adapter, backend
    del contexts, top_one, max_two, max_chars, repeated_context
    gc.collect()
    try:
        restarted_backend = _silenced(RealMagmaBackend, paths.magma)
    except Exception as exc:
        raise AcceptanceFailure("restart", "magma_restart_failed") from exc
    restarted_adapter = MagmaMemoryAdapter(
        restarted_backend,
        IngestionStateStore(paths.ingestion_state),
        ingestion_version=INGESTION_VERSION,
    )
    restarted_counts = _memory_counts(restarted_backend)
    _require(restarted_counts == (node_count, vector_count), "restart", "persisted_counts_changed")
    restarted_contexts, restarted_checks = _silenced(
        _run_query_suite,
        restarted_adapter,
        policy,
        segment_ids,
        paths.root,
    )
    _require(all(restarted_checks.values()), "restart", "restart_query_failed")
    _require(
        {
            name: _evidence_ids(context)
            for name, context in restarted_contexts.items()
        }
        == evidence_before,
        "restart",
        "restart_evidence_ids_changed",
    )
    _require(
        {
            name: _provenance_signature(context)
            for name, context in restarted_contexts.items()
        }
        == provenance_before,
        "restart",
        "restart_provenance_changed",
    )
    _require(
        IngestionStateStore(paths.ingestion_state).read_all().get(key, {}).get("status")
        == "completed",
        "restart",
        "restart_state_not_completed",
    )
    report["restart_recall"]["passed"] = True
    report["leak_checks"]["passed"] = True
    _verbose(verbose, "persisted recall restart passed")

    assistant_query = next(
        item for item in _QUERY_SPECS if item[0] == "assistant_self_memory"
    )
    graphiti_policy = RecallPolicy(
        top_k=10,
        max_graph_depth=1,
        max_nodes=20,
        max_evidence_items=3,
        max_chars=5000,
        final_min_score=None,
    )
    assistant_context = _silenced(
        restarted_adapter.recall,
        assistant_query[1],
        graphiti_policy,
    )
    _validate_public_context(
        assistant_context,
        graphiti_policy,
        segment_ids,
        paths.root,
    )
    report["grounded_bge_acceptance"]["assistant_self_memory"] = any(
        assistant_query[2].casefold() in item.text.casefold()
        and item.provenance.source_role == "assistant"
        for item in assistant_context.evidence
    )

    del restarted_adapter, restarted_backend, restarted_contexts
    del restarted_checks, assistant_context
    gc.collect()
    _silenced(_run_grounded_bge_acceptance, paths, report)
    _verbose(verbose, "grounded production Recall acceptance passed")


def run_acceptance(
    work_dir: Path = DEFAULT_WORK_DIR,
    *,
    keep_data: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    _validate_isolated_runtime()
    safe_root = reset_test_sandbox(work_dir)
    paths = SandboxPaths.from_root(safe_root)
    report = _base_report(keep_data)
    try:
        _verbose(verbose, "sandbox initialized")
        _execute_pipeline(paths, report, verbose=verbose)
        report["result"] = "PASS"
    except AcceptanceFailure as exc:
        report["failures"] = [{"stage": exc.stage, "code": exc.code}]
    except Exception:
        report["failures"] = [{"stage": "internal", "code": "unexpected_failure"}]

    report["cleanup"]["passed"] = True
    try:
        _validate_report_safety(report, safe_root)
        _write_report(paths.report, report)
    except AcceptanceFailure as exc:
        report["result"] = "FAIL"
        report["failures"] = [{"stage": exc.stage, "code": exc.code}]
    except Exception:
        report["result"] = "FAIL"
        report["failures"] = [{"stage": "report", "code": "report_write_failed"}]

    if not keep_data:
        try:
            cleanup_test_sandbox(safe_root)
        except AcceptanceFailure:
            report["result"] = "FAIL"
            report["cleanup"]["passed"] = False
            report["failures"] = [{"stage": "cleanup", "code": "sandbox_cleanup_failed"}]
            try:
                _write_report(paths.report, report)
            except Exception:
                pass
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run isolated Cold Draft/Dream/MAGMA recall acceptance",
    )
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def _print_summary(report: dict[str, Any], keep_data: bool) -> None:
    print(f"Recall E2E: {report['result']}")
    if report["result"] == "PASS":
        print(f"Cold Draft segments: {report['cold_draft']['consumed']} consumed")
        print(f"Dream failures: {report['dream']['failed']}")
        print(
            "Recall queries: "
            f"{report['recall']['passed']}/{report['recall']['queries']} passed"
        )
        print(
            "Restart recall: "
            f"{'PASS' if report['restart_recall']['passed'] else 'FAIL'}"
        )
        print(
            "Idempotency: "
            f"{'PASS' if report['idempotency']['passed'] else 'FAIL'}"
        )
        print(
            "Production depth-1 evidence: "
            f"{report['grounded_bge_acceptance']['depth1_positive_complete']}/"
            f"{report['grounded_bge_acceptance']['queries']} complete"
        )
    else:
        failure = report.get("failures", [{}])[0]
        print(
            "Failure: "
            f"{failure.get('stage', 'internal')}:{failure.get('code', 'unexpected_failure')}"
        )
    print(
        "Report: retained in sandbox"
        if keep_data
        else "Report: retained only with --keep-data"
    )


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        report = run_acceptance(
            args.work_dir,
            keep_data=args.keep_data,
            verbose=args.verbose,
        )
    except AcceptanceFailure as exc:
        report = _safe_failure_report(exc.stage, exc.code, args.keep_data)
    except Exception:
        report = _safe_failure_report("internal", "unexpected_failure", args.keep_data)
    _print_summary(report, args.keep_data)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
