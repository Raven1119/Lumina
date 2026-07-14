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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
CONVERSATION_MEMORY_ROOT = ROOT / "Conversation_Memory"
if str(CONVERSATION_MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(CONVERSATION_MEMORY_ROOT))

from adapter.backend import RealMagmaBackend  # noqa: E402
from adapter.interfaces import MemoryIngestor, MemoryRetriever  # noqa: E402
from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.models import MemoryContext, RecallPolicy  # noqa: E402
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
from ingestion.state_store import IngestionStateStore  # noqa: E402


SANDBOX_MARKER = ".recall_e2e_sandbox"
SANDBOX_MARKER_CONTENT = "lumina-recall-e2e-v1\n"
DEFAULT_WORK_DIR = ROOT / "data" / "recall_e2e_test"
INGESTION_VERSION = "recall-e2e-v1"
_EVIDENCE_ID = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SyntheticTurn:
    timestamp: str
    role: str
    text: str


FIXED_TURNS = (
    SyntheticTurn(
        "2026-07-14T10:00:00+08:00",
        "user",
        "I completed the membrane experiment yesterday.",
    ),
    SyntheticTurn(
        "2026-07-14T10:05:00+08:00",
        "assistant",
        "The experiment was recorded as completed.",
    ),
    SyntheticTurn(
        "2026-07-14T11:00:00+08:00",
        "user",
        "The first experiment failed because the solvent evaporated too quickly.",
    ),
    SyntheticTurn(
        "2026-07-14T11:05:00+08:00",
        "assistant",
        "The rapid solvent evaporation caused the failure.",
    ),
    SyntheticTurn(
        "2026-07-15T09:00:00+08:00",
        "user",
        "I changed the solvent today and repeated the experiment.",
    ),
    SyntheticTurn(
        "2026-07-15T09:05:00+08:00",
        "assistant",
        "The repeated experiment used the new solvent.",
    ),
)

_HOT_TAIL = (
    SyntheticTurn(
        "2026-07-15T09:10:00+08:00",
        "user",
        "Please keep the most recent pair in the Hot Draft.",
    ),
    SyntheticTurn(
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
        "recall": {"queries": 6, "passed": 0, "failed": 0, "checks": {}},
        "provenance": {
            "passed": False,
            "temporal_normalization_passed": False,
            "timestamp_mapping": "segment_created_at_for_all_turns",
        },
        "bounds": {
            "top_k": False,
            "max_evidence_items": False,
            "max_chars": False,
        },
        "restart_recall": {"passed": False},
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


def _memory_counts(backend: RealMagmaBackend) -> tuple[int, int]:
    return len(backend.trg.graph_db.nodes), int(backend.trg.vector_db.size())


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
        len(context.evidence) <= min(policy.top_k, policy.max_evidence_items),
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
        _require(provenance.ingestion_version == INGESTION_VERSION, "provenance", "ingestion_version_mismatch")
        _require(bool(provenance.source_timezone), "provenance", "source_timezone_missing")
        try:
            source_time = datetime.fromisoformat(provenance.source_timestamp)
        except ValueError as exc:
            raise AcceptanceFailure("provenance", "source_timestamp_invalid") from exc
        _require(
            source_time.tzinfo is not None and source_time.utcoffset() is not None,
            "provenance",
            "source_timestamp_not_aware",
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
)
_NEGATIVE_QUERY = "Which catalyst was purchased from Sigma-Aldrich?"


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
    temporal_context: MemoryContext,
    source_created_at: str,
) -> None:
    evidence_ids = {
        item.evidence_id
        for item in temporal_context.evidence
        if "completed the membrane experiment yesterday" in item.text.casefold()
    }
    _require(bool(evidence_ids), "temporal", "temporal_evidence_missing")
    matched: list[dict[str, Any]] = []
    for node in backend.trg.graph_db.nodes.values():
        attributes = getattr(node, "attributes", {})
        if attributes.get("evidence_id") not in evidence_ids:
            continue
        references = attributes.get("temporal_references", [])
        matched.extend(item for item in references if isinstance(item, dict))
    yesterday = next(
        (
            item
            for item in matched
            if str(item.get("original_expression", "")).casefold() == "yesterday"
        ),
        None,
    )
    _require(yesterday is not None, "temporal", "yesterday_normalization_missing")
    _require(yesterday.get("reference_timestamp") == source_created_at, "temporal", "temporal_reference_mismatch")
    _require(yesterday.get("reference_timezone") == "UTC", "temporal", "temporal_timezone_mismatch")
    reference = datetime.fromisoformat(source_created_at)
    _require(
        yesterday.get("normalized_start") == (reference - timedelta(days=1)).isoformat(),
        "temporal",
        "yesterday_normalization_incorrect",
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
    _require(not any(value in lowered for value in forbidden), "report", "report_leak")
    _require(_UUID.search(serialized) is None, "report", "report_uuid_leak")


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
        hot_store.append_turn(MemoryTurn(role=turn.role, text=turn.text))
    stored_hot = hot_store.list_recent(limit=len(all_turns))
    _require(
        [(turn.role, turn.text) for turn in stored_hot]
        == [(turn.role, turn.text) for turn in all_turns],
        "draft",
        "hot_draft_order_mismatch",
    )
    _verbose(verbose, "synthetic Hot Draft written")

    compactor = HotDraftCompactor(
        hot_store,
        cold_store,
        paths.compaction_state,
        retain_recent_raw_turns=2,
        max_raw_turns_before_compression=6,
    )
    compaction = compactor.maybe_compact()
    _require(compaction.status == "compacted", "compaction", "cold_first_compaction_failed")
    pending = cold_store.list_pending(limit=10)
    _require(len(pending) == 1, "compaction", "pending_segment_count_mismatch")
    source_record = pending[0]
    source_turns = [(item["role"], item["text"]) for item in source_record["turns"]]
    expected_source_turns = [(turn.role, turn.text) for turn in FIXED_TURNS]
    _require(source_turns == expected_source_turns, "compaction", "cold_draft_content_mismatch")
    _require(
        [(turn.role, turn.text) for turn in hot_store.list_recent(limit=len(all_turns))]
        == [(turn.role, turn.text) for turn in all_turns],
        "compaction",
        "physical_hot_draft_changed",
    )
    converted = ColdDraftSegmentConverter().convert(source_record, INGESTION_VERSION)
    _require(
        all(turn.timestamp.isoformat() == source_record["created_at"] for turn in converted.turns),
        "provenance",
        "production_timestamp_mapping_mismatch",
    )
    segment_ids = {source_record["segment_id"]}
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
    _require(first_dream.attempted >= 1, "dream", "dream_attempted_zero")
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
    consumed_record = next(
        (item for item in cold_records if item.get("segment_id") == source_record["segment_id"]),
        None,
    )
    _require(consumed_record is not None, "dream", "consumed_record_missing")
    _require(consumed_record.get("state") == "consumed", "dream", "cold_draft_not_consumed")
    _require(
        [(item["role"], item["text"]) for item in consumed_record["turns"]] == source_turns,
        "dream",
        "cold_draft_raw_content_changed",
    )
    report["dream"].update({"attempted": first_dream.attempted, "failed": first_dream.failed})
    report["cold_draft"]["consumed"] = 1
    _verbose(verbose, "Dream completed and source consumed")

    node_count, vector_count = _memory_counts(backend)
    _require(node_count == len(FIXED_TURNS), "magma", "magma_event_count_mismatch")
    _require(vector_count == len(FIXED_TURNS), "magma", "magma_vector_count_mismatch")
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
    report["recall"].update(
        {"passed": 6, "failed": 0, "checks": checks}
    )
    _validate_temporal_metadata(
        backend,
        contexts["temporal"],
        source_record["created_at"],
    )
    report["provenance"].update(
        {"passed": True, "temporal_normalization_passed": True}
    )
    _verbose(verbose, "six-query recall suite passed")

    top_one = _silenced(
        adapter.recall,
        _QUERY_SPECS[0][1],
        RecallPolicy(top_k=1, max_chars=1200, max_evidence_items=5, max_graph_depth=6, max_nodes=200),
    )
    max_two = _silenced(
        adapter.recall,
        _QUERY_SPECS[0][1],
        RecallPolicy(top_k=10, max_chars=1200, max_evidence_items=2, max_graph_depth=6, max_nodes=200),
    )
    max_chars = _silenced(
        adapter.recall,
        _QUERY_SPECS[0][1],
        RecallPolicy(top_k=5, max_chars=120, max_evidence_items=5, max_graph_depth=6, max_nodes=200),
    )
    for context, context_policy in (
        (top_one, RecallPolicy(top_k=1, max_chars=1200, max_evidence_items=5, max_graph_depth=6, max_nodes=200)),
        (max_two, RecallPolicy(top_k=10, max_chars=1200, max_evidence_items=2, max_graph_depth=6, max_nodes=200)),
        (max_chars, RecallPolicy(top_k=5, max_chars=120, max_evidence_items=5, max_graph_depth=6, max_nodes=200)),
    ):
        _validate_public_context(context, context_policy, segment_ids, paths.root)
    _require(len(top_one.evidence) <= 1, "bounds", "top_k_not_enforced")
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
