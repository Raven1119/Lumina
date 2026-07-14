"""Isolated real-MAGMA relevance threshold calibration for synthetic data."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
CONVERSATION_MEMORY_ROOT = ROOT / "Conversation_Memory"
if str(CONVERSATION_MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(CONVERSATION_MEMORY_ROOT))

from adapter.backend import RealMagmaBackend  # noqa: E402
from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.models import RecallPolicy  # noqa: E402
from ingestion.fixture_loader import load_fixture  # noqa: E402
from ingestion.state_store import IngestionStateStore  # noqa: E402


CALIBRATION_FIXTURE = (
    CONVERSATION_MEMORY_ROOT / "fixtures" / "relevance_calibration_v1.json"
)
DEFAULT_WORK_DIR = ROOT / "data" / "relevance_calibration"
SANDBOX_MARKER = ".relevance_calibration_sandbox"
SANDBOX_MARKER_CONTENT = "lumina-relevance-calibration-v1\n"
INGESTION_VERSION = "relevance-calibration-v1"
MIN_POSITIVE_RECALL = 0.90
MAX_FALSE_INJECTION_RATE = 0.10
THRESHOLDS = tuple(round(-1.0 + index * 0.05, 2) for index in range(41))
PERFORMANCE_RUNS = 7
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CalibrationQuery:
    query_id: str
    query: str
    label: str
    category: str
    expected_turn_ids: tuple[str, ...]


@dataclass(frozen=True)
class CalibrationObservation:
    label: str
    score: float


@dataclass(frozen=True)
class CalibrationDataset:
    memory_fixture: Path
    queries: tuple[CalibrationQuery, ...]


@dataclass(frozen=True)
class CalibrationPaths:
    root: Path
    magma: Path
    ingestion_state: Path
    report: Path
    logs: Path

    @classmethod
    def from_root(cls, root: Path) -> "CalibrationPaths":
        return cls(
            root=root,
            magma=root / "conversation_memory" / "magma",
            ingestion_state=root / "conversation_memory" / "ingestion_state.json",
            report=root / "reports" / "relevance_calibration_result.json",
            logs=root / "logs",
        )


class CalibrationFailure(RuntimeError):
    def __init__(self, stage: str, code: str) -> None:
        super().__init__(code)
        self.stage = stage
        self.code = code


class CalibrationSafetyError(CalibrationFailure):
    def __init__(self, code: str) -> None:
        super().__init__("sandbox", code)


def _require(condition: bool, stage: str, code: str) -> None:
    if not condition:
        raise CalibrationFailure(stage, code)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_calibration_path(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.exists() and candidate.is_symlink():
        raise CalibrationSafetyError("sandbox_symlink_refused")
    resolved = candidate.resolve(strict=False)
    drive_root = Path(resolved.anchor).resolve(strict=False)
    home = Path.home().resolve(strict=False)
    repository_root = ROOT.resolve(strict=False)
    data_root = (ROOT / "data").resolve(strict=False)
    if resolved in {drive_root, home, repository_root, data_root}:
        raise CalibrationSafetyError("unsafe_sandbox_path")
    forbidden_trees = (
        (ROOT / "data" / "draft").resolve(strict=False),
        (ROOT / "data" / "conversation_memory").resolve(strict=False),
        (ROOT / ".git").resolve(strict=False),
        (CONVERSATION_MEMORY_ROOT / "upstream" / "MAGMA").resolve(strict=False),
    )
    if any(_is_within(resolved, forbidden) for forbidden in forbidden_trees):
        raise CalibrationSafetyError("production_or_sensitive_path_refused")
    if _is_within(resolved, repository_root) and not _is_within(resolved, data_root):
        raise CalibrationSafetyError("repository_code_path_refused")
    return resolved


def _has_valid_marker(path: Path) -> bool:
    marker = path / SANDBOX_MARKER
    if not marker.is_file() or marker.is_symlink():
        return False
    try:
        return marker.read_text(encoding="utf-8") == SANDBOX_MARKER_CONTENT
    except OSError:
        return False


def reset_calibration_sandbox(path: Path) -> Path:
    safe = validate_calibration_path(path)
    if safe.exists():
        if not safe.is_dir():
            raise CalibrationSafetyError("sandbox_not_directory")
        if not _has_valid_marker(safe):
            raise CalibrationSafetyError("sandbox_marker_required")
        shutil.rmtree(safe)
    safe.mkdir(parents=True, exist_ok=False)
    (safe / SANDBOX_MARKER).write_text(SANDBOX_MARKER_CONTENT, encoding="utf-8")
    return safe


def cleanup_calibration_sandbox(path: Path) -> None:
    safe = validate_calibration_path(path)
    if not safe.exists():
        return
    if not safe.is_dir() or not _has_valid_marker(safe):
        raise CalibrationSafetyError("sandbox_marker_required")
    shutil.rmtree(safe)


def _validate_isolated_runtime() -> None:
    expected = (CONVERSATION_MEMORY_ROOT / ".venv").resolve(strict=False)
    if Path(sys.prefix).resolve(strict=False) != expected:
        raise CalibrationFailure("runtime", "isolated_environment_required")


def load_calibration_dataset(path: Path = CALIBRATION_FIXTURE) -> CalibrationDataset:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationFailure("fixture", "calibration_fixture_unreadable") from exc
    _require(isinstance(raw, dict), "fixture", "calibration_fixture_invalid")
    _require(raw.get("schema_version") == "1", "fixture", "calibration_schema_unsupported")
    memory_name = raw.get("memory_fixture")
    _require(
        isinstance(memory_name, str)
        and memory_name == Path(memory_name).name
        and memory_name.endswith(".json"),
        "fixture",
        "memory_fixture_invalid",
    )
    queries_raw = raw.get("queries")
    _require(isinstance(queries_raw, list), "fixture", "calibration_queries_invalid")
    queries: list[CalibrationQuery] = []
    seen_ids: set[str] = set()
    for item in queries_raw:
        _require(isinstance(item, dict), "fixture", "calibration_query_invalid")
        query_id = item.get("id")
        query = item.get("query")
        label = item.get("label")
        category = item.get("category")
        expected = item.get("expected_turn_ids")
        _require(
            isinstance(query_id, str) and query_id and query_id not in seen_ids,
            "fixture",
            "calibration_query_id_invalid",
        )
        _require(isinstance(query, str) and query.strip(), "fixture", "calibration_query_text_invalid")
        _require(label in {"relevant", "irrelevant"}, "fixture", "calibration_label_invalid")
        _require(isinstance(category, str) and category, "fixture", "calibration_category_invalid")
        _require(
            isinstance(expected, list)
            and all(isinstance(turn_id, str) and turn_id for turn_id in expected),
            "fixture",
            "calibration_expected_turns_invalid",
        )
        _require(
            (label == "relevant" and bool(expected))
            or (label == "irrelevant" and not expected),
            "fixture",
            "calibration_label_expectation_mismatch",
        )
        seen_ids.add(query_id)
        queries.append(CalibrationQuery(
            query_id,
            query.strip(),
            label,
            category,
            tuple(expected),
        ))
    positive_count = sum(item.label == "relevant" for item in queries)
    negative_count = sum(item.label == "irrelevant" for item in queries)
    _require(positive_count >= 20, "fixture", "insufficient_positive_queries")
    _require(negative_count >= 20, "fixture", "insufficient_negative_queries")
    memory_fixture = Path(path).resolve().parent / memory_name
    _require(memory_fixture.is_file(), "fixture", "memory_fixture_missing")
    return CalibrationDataset(memory_fixture, tuple(queries))


def collect_observations(
    adapter: MagmaMemoryAdapter,
    queries: Sequence[CalibrationQuery],
) -> tuple[CalibrationObservation, ...]:
    policy = RecallPolicy(
        top_k=20,
        max_chars=10000,
        max_evidence_items=20,
        max_graph_depth=6,
        max_nodes=200,
        min_relevance=-1.0,
        max_relevance_candidates=20,
    )
    observations: list[CalibrationObservation] = []
    for item in queries:
        context = adapter.recall(item.query, policy)
        _require(context.safe_error_code is None, "scoring", "relevance_scoring_unavailable")
        _require(
            all(
                evidence.relevance_score is not None
                and -1.0 <= evidence.relevance_score <= 1.0
                for evidence in context.evidence
            ),
            "scoring",
            "relevance_score_invalid",
        )
        if item.label == "relevant":
            expected_scores = [
                float(evidence.relevance_score)
                for evidence in context.evidence
                if evidence.provenance.turn_id in item.expected_turn_ids
            ]
            score = max(expected_scores, default=-1.0)
        else:
            score = max(
                (float(evidence.relevance_score) for evidence in context.evidence),
                default=-1.0,
            )
        observations.append(CalibrationObservation(item.label, score))
    return tuple(observations)


def metrics_for_threshold(
    observations: Sequence[CalibrationObservation],
    threshold: float,
) -> dict[str, float]:
    positives = [item for item in observations if item.label == "relevant"]
    negatives = [item for item in observations if item.label == "irrelevant"]
    true_positive = sum(item.score >= threshold for item in positives)
    false_positive = sum(item.score >= threshold for item in negatives)
    false_negative = len(positives) - true_positive
    true_negative = len(negatives) - false_positive
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / len(positives) if positives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    false_injection_rate = false_positive / len(negatives) if negatives else 0.0
    empty_accuracy = true_negative / len(negatives) if negatives else 0.0
    return {
        "threshold": round(float(threshold), 4),
        "positive_recall": round(recall, 4),
        "negative_rejection_rate": round(empty_accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_injection_rate": round(false_injection_rate, 4),
        "empty_accuracy": round(empty_accuracy, 4),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
    }


def sweep_thresholds(
    observations: Sequence[CalibrationObservation],
    thresholds: Sequence[float] = THRESHOLDS,
) -> list[dict[str, float]]:
    return [metrics_for_threshold(observations, threshold) for threshold in thresholds]


def select_recommendation(metrics: Sequence[dict[str, float]]) -> dict[str, Any]:
    rule = (
        "positive_recall >= 0.90 and false_injection_rate <= 0.10; "
        "minimize false_injection_rate, then maximize F1, then choose the highest threshold"
    )
    eligible = [
        item for item in metrics
        if item["positive_recall"] >= MIN_POSITIVE_RECALL
        and item["false_injection_rate"] <= MAX_FALSE_INJECTION_RATE
    ]
    if not eligible:
        return {
            "threshold": None,
            "rule": rule,
            "usable": False,
            "limitation_code": "threshold_not_recommended",
        }
    selected = min(
        eligible,
        key=lambda item: (
            item["false_injection_rate"],
            -item["f1"],
            -item["threshold"],
        ),
    )
    return {
        "threshold": selected["threshold"],
        "rule": rule,
        "usable": True,
        "positive_recall": selected["positive_recall"],
        "false_injection_rate": selected["false_injection_rate"],
        "empty_accuracy": selected["empty_accuracy"],
        "f1": selected["f1"],
        "limitation_code": None,
    }


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def score_statistics(values: Sequence[float]) -> dict[str, float]:
    _require(bool(values), "calibration", "empty_score_distribution")
    return {
        "min": round(min(values), 4),
        "p10": round(_percentile(values, 0.10), 4),
        "median": round(statistics.median(values), 4),
        "p90": round(_percentile(values, 0.90), 4),
        "max": round(max(values), 4),
    }


def measure_performance(
    adapter: MagmaMemoryAdapter,
    backend: RealMagmaBackend,
    query: str,
    threshold: float | None,
    *,
    runs: int = PERFORMANCE_RUNS,
) -> dict[str, Any]:
    _require(runs >= 3, "performance", "insufficient_performance_runs")
    disabled_policy = RecallPolicy(top_k=4, max_evidence_items=4, max_chars=1200)
    measurement_threshold = 0.0 if threshold is None else threshold
    enabled_policy = RecallPolicy(
        top_k=4,
        max_evidence_items=4,
        max_chars=1200,
        min_relevance=measurement_threshold,
        max_relevance_candidates=20,
    )
    adapter.recall(query, disabled_policy)
    adapter.recall(query, enabled_policy)
    encoder_identity = id(backend.trg.encoder)
    disabled_ms: list[float] = []
    enabled_ms: list[float] = []
    scored_counts: list[int] = []
    additional_calls: list[int] = []
    query_calls: list[int] = []
    for _ in range(runs):
        started = time.perf_counter_ns()
        disabled = adapter.recall(query, disabled_policy)
        disabled_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        _require(disabled.safe_error_code is None, "performance", "disabled_recall_failed")
        query_calls.append(int(backend.last_recall_diagnostics["query_embedding_calls"]))

        started = time.perf_counter_ns()
        enabled = adapter.recall(query, enabled_policy)
        enabled_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        _require(enabled.safe_error_code is None, "performance", "enabled_recall_failed")
        query_calls.append(int(backend.last_recall_diagnostics["query_embedding_calls"]))
        additional_calls.append(int(backend.last_recall_diagnostics["additional_embedding_calls"]))
        scored_counts.append(int(adapter.last_recall_diagnostics["candidates_scored"]))
    disabled_median = statistics.median(disabled_ms)
    enabled_median = statistics.median(enabled_ms)
    return {
        "runs": runs,
        "gate_disabled_median_ms": round(disabled_median, 3),
        "gate_enabled_median_ms": round(enabled_median, 3),
        "added_median_ms": round(enabled_median - disabled_median, 3),
        "measurement_threshold": round(measurement_threshold, 4),
        "query_embedding_calls_per_recall": max(query_calls, default=0),
        "additional_relevance_embedding_calls": max(additional_calls, default=0),
        "candidate_count_scored_median": round(statistics.median(scored_counts), 2),
        "candidate_count_scored_max": max(scored_counts, default=0),
        "model_reloads": 0 if id(backend.trg.encoder) == encoder_identity else 1,
        "additional_llm_calls": 0,
        "additional_llm_tokens": 0,
    }


def _base_report(keep_data: bool) -> dict[str, Any]:
    return {
        "result": "FAIL",
        "sandbox": True,
        "samples": {"positive": 0, "negative": 0},
        "score_statistics": {},
        "overlap": {},
        "threshold_metrics": [],
        "recommendation": {
            "threshold": None,
            "usable": False,
            "limitation_code": "calibration_not_run",
        },
        "performance": {},
        "leak_checks": {"passed": False},
        "cleanup": {
            "mode": "keep-data" if keep_data else "default",
            "passed": False,
        },
        "failures": [],
        "limitations": ["synthetic_calibration_only"],
    }


def _execute_calibration(paths: CalibrationPaths, report: dict[str, Any]) -> None:
    dataset = load_calibration_dataset()
    paths.logs.mkdir(parents=True, exist_ok=True)
    backend = RealMagmaBackend(paths.magma)
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(paths.ingestion_state),
        ingestion_version=INGESTION_VERSION,
    )
    ingestion = adapter.ingest(load_fixture(dataset.memory_fixture))
    _require(ingestion.status == "completed", "ingestion", "calibration_ingestion_failed")
    observations = collect_observations(adapter, dataset.queries)
    positives = [item.score for item in observations if item.label == "relevant"]
    negatives = [item.score for item in observations if item.label == "irrelevant"]
    metrics = sweep_thresholds(observations)
    recommendation = select_recommendation(metrics)
    positive_stats = score_statistics(positives)
    negative_stats = score_statistics(negatives)
    overlap_start = max(positive_stats["min"], negative_stats["min"])
    overlap_end = min(positive_stats["max"], negative_stats["max"])
    performance = measure_performance(
        adapter,
        backend,
        dataset.queries[0].query,
        recommendation["threshold"],
    )
    _require(performance["additional_relevance_embedding_calls"] == 0, "performance", "unexpected_embedding_call")
    _require(performance["model_reloads"] == 0, "performance", "model_reloaded")
    _require(performance["candidate_count_scored_max"] <= 20, "performance", "candidate_bound_exceeded")
    report.update({
        "result": "PASS" if recommendation["usable"] else "PARTIAL",
        "samples": {"positive": len(positives), "negative": len(negatives)},
        "score_statistics": {
            "positive": positive_stats,
            "negative": negative_stats,
        },
        "overlap": {
            "exists": overlap_start <= overlap_end,
            "start": round(overlap_start, 4),
            "end": round(overlap_end, 4),
        },
        "threshold_metrics": metrics,
        "recommendation": recommendation,
        "performance": performance,
        "leak_checks": {"passed": True},
    })
    if not recommendation["usable"]:
        report["limitations"].append("threshold_not_recommended")


def _validate_report_safety(report: dict[str, Any], work_dir: Path) -> None:
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    lowered = serialized.lower()
    forbidden = (
        str(work_dir).lower(),
        str(ROOT).lower(),
        "traceback",
        "openai_api_key",
        "provider body",
        "index.faiss",
        "graph.json",
    )
    _require(not any(value and value in lowered for value in forbidden), "report", "report_leak")
    _require(_UUID.search(serialized) is None, "report", "report_uuid_leak")


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _silenced(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return call(*args, **kwargs)


def run_calibration(
    work_dir: Path = DEFAULT_WORK_DIR,
    *,
    keep_data: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    _validate_isolated_runtime()
    safe_root = reset_calibration_sandbox(work_dir)
    paths = CalibrationPaths.from_root(safe_root)
    report = _base_report(keep_data)
    try:
        if verbose:
            print("Relevance calibration step: sandbox initialized")
        _silenced(_execute_calibration, paths, report)
        if verbose:
            print("Relevance calibration step: scoring and threshold sweep completed")
    except CalibrationFailure as exc:
        report["failures"] = [{"stage": exc.stage, "code": exc.code}]
    except Exception:
        report["failures"] = [{"stage": "internal", "code": "unexpected_failure"}]

    report["cleanup"]["passed"] = True
    try:
        _validate_report_safety(report, safe_root)
        _write_report(paths.report, report)
    except CalibrationFailure as exc:
        report["result"] = "FAIL"
        report["failures"] = [{"stage": exc.stage, "code": exc.code}]
    except Exception:
        report["result"] = "FAIL"
        report["failures"] = [{"stage": "report", "code": "report_write_failed"}]

    if not keep_data:
        try:
            cleanup_calibration_sandbox(safe_root)
        except CalibrationFailure:
            report["result"] = "FAIL"
            report["cleanup"]["passed"] = False
            report["failures"] = [{"stage": "cleanup", "code": "sandbox_cleanup_failed"}]
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibrate the isolated local relevance gate with real MAGMA",
    )
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def _print_summary(report: dict[str, Any], keep_data: bool) -> None:
    print(f"Relevance Calibration: {report['result']}")
    if report["result"] in {"PASS", "PARTIAL"}:
        print(
            "Queries: "
            f"{report['samples']['positive']} positive / "
            f"{report['samples']['negative']} negative"
        )
        threshold = report["recommendation"].get("threshold")
        print(f"Recommended threshold: {threshold if threshold is not None else 'NONE'}")
    else:
        failure = report.get("failures", [{}])[0]
        print(
            "Failure: "
            f"{failure.get('stage', 'internal')}:"
            f"{failure.get('code', 'unexpected_failure')}"
        )
    print("Report: retained in sandbox" if keep_data else "Report: retained only with --keep-data")


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        report = run_calibration(
            args.work_dir,
            keep_data=args.keep_data,
            verbose=args.verbose,
        )
    except CalibrationFailure as exc:
        report = _base_report(args.keep_data)
        report["failures"] = [{"stage": exc.stage, "code": exc.code}]
    except Exception:
        report = _base_report(args.keep_data)
        report["failures"] = [{"stage": "internal", "code": "unexpected_failure"}]
    _print_summary(report, args.keep_data)
    return 0 if report["result"] in {"PASS", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
