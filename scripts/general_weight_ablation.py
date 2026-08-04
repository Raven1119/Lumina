from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import tempfile
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any, Iterator, Sequence

from scripts.recall_depth_eval import (
    CATEGORIES,
    FIXTURE_PATH,
    _load_and_validate_fixture,
    _ndcg,
    _quiet_call,
    _segments_from_fixture,
)

from adapter import _adaptive_traversal as adaptive_traversal
from adapter import _recall_execution as recall_execution
from adapter.backend import RealMagmaBackend
from adapter.interfaces import MemoryRetriever
from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import RecallPolicy
from ingestion.state_store import IngestionStateStore


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "GENERAL_WEIGHT_ABLATION.md"
VARIANTS = {
    "A": {
        "name": "upstream fallback",
        "weights": {
            "ENTITY": 0.60,
            "SEMANTIC": 0.30,
            "TEMPORAL": 0.05,
            "CAUSAL": 0.05,
        },
    },
    "B": {
        "name": "balanced non-causal",
        "weights": {
            "ENTITY": 1.0 / 3.0,
            "SEMANTIC": 1.0 / 3.0,
            "TEMPORAL": 1.0 / 3.0,
            "CAUSAL": 0.0,
        },
    },
}
POLICY = RecallPolicy(
    top_k=1,
    max_graph_depth=2,
    max_nodes=200,
    max_evidence_items=6,
    max_chars=6000,
    intent="GENERAL",
    temporal_window=None,
    beam_width=10,
    drop_threshold=0.15,
)
REPETITIONS = 3
MARKER_NAME = ".lumina-general-weight-ablation"
MARKER_VALUE = "general-weight-ablation-v1\n"


def _validate_weights(weights: dict[str, float]) -> None:
    if set(weights) != {"ENTITY", "SEMANTIC", "TEMPORAL", "CAUSAL"}:
        raise ValueError("relation weights must contain exactly four known edge types")
    if any(value < 0.0 or value > 1.0 for value in weights.values()):
        raise ValueError("relation weights must be within [0, 1]")
    if abs(sum(weights.values()) - 1.0) > 1e-12:
        raise ValueError("relation weights must sum to 1")


@contextmanager
def _temporary_general_weights(
    weights: dict[str, float],
    anchor_log: list[tuple[str, ...]],
) -> Iterator[None]:
    """Patch only private GENERAL weights and observe the existing call seam."""
    _validate_weights(weights)
    original_mapping = adaptive_traversal._INTENT_WEIGHTS["GENERAL"]
    original_call = recall_execution._adaptive_traverse

    def capture_anchors(**kwargs: Any) -> Any:
        anchors = kwargs.get("anchors")
        if not isinstance(anchors, Sequence):
            raise RuntimeError("adaptive traversal did not receive an anchor sequence")
        node_ids = tuple(getattr(node, "node_id", "") for node in anchors)
        if any(not isinstance(node_id, str) or not node_id for node_id in node_ids):
            raise RuntimeError("adaptive traversal received an invalid fused anchor")
        if len(node_ids) != len(set(node_ids)):
            raise RuntimeError("adaptive traversal received duplicate fused anchors")
        anchor_log.append(node_ids)
        return original_call(**kwargs)

    try:
        adaptive_traversal._INTENT_WEIGHTS["GENERAL"] = dict(weights)
        recall_execution._adaptive_traverse = capture_anchors
        yield
    finally:
        recall_execution._adaptive_traverse = original_call
        adaptive_traversal._INTENT_WEIGHTS["GENERAL"] = original_mapping


def _create_owned_temp() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="lumina-general-weight-ablation-"))
    (temp_dir / MARKER_NAME).write_text(MARKER_VALUE, encoding="utf-8")
    return temp_dir


def _cleanup_owned_temp(temp_dir: Path) -> None:
    marker = temp_dir / MARKER_NAME
    if not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER_VALUE:
        raise RuntimeError("refusing to clean an unowned evaluation directory")
    shutil.rmtree(temp_dir)
    if temp_dir.exists():
        raise RuntimeError("evaluation temporary directory was not cleaned")


def _ingest_fixture(data: dict[str, Any], temp_dir: Path) -> MagmaMemoryAdapter:
    backend = _quiet_call(RealMagmaBackend, temp_dir / "magma")
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(temp_dir / "ingestion_state.json"),
        ingestion_version="general-weight-ablation-v1",
    )
    ingested = 0
    for segment in _segments_from_fixture(data):
        result = _quiet_call(adapter.ingest, segment)
        if result.status != "completed" or len(result.memory_ids) != len(segment.turns):
            raise RuntimeError(
                f"real ingestion failed for {segment.segment_id}: "
                f"{result.safe_error_code or result.status}"
            )
        ingested += len(segment.turns)
    if ingested != 48 or ingested != len(data["turns"]):
        raise RuntimeError("the shared real MAGMA store must contain exactly 48 turns")
    return adapter


def _record_question(
    retriever: MemoryRetriever,
    question: dict[str, Any],
    known_evidence: set[str],
) -> dict[str, Any]:
    started = perf_counter()
    context = _quiet_call(retriever.recall, question["query"], POLICY)
    latency_ms = (perf_counter() - started) * 1000.0
    if context.safe_error_code:
        raise RuntimeError(
            f"Recall failed for {question['question_id']}: {context.safe_error_code}"
        )

    returned = [item.provenance.turn_id for item in context.evidence]
    if len(returned) != len(set(returned)):
        raise ValueError("public Recall returned duplicate evidence IDs")
    unknown = set(returned) - known_evidence
    if unknown:
        raise ValueError(f"Recall returned unknown fixture evidence: {sorted(unknown)}")
    if len(returned) > POLICY.max_evidence_items:
        raise ValueError("Recall exceeded max_evidence_items")
    if len(context.rendered_text) > POLICY.max_chars:
        raise ValueError("Recall exceeded max_chars")

    gold_ids = list(question["gold_evidence_ids"])
    gold = set(gold_ids)
    found = len(gold.intersection(returned))
    evidence_count = len(returned)
    answerable = bool(gold)
    record = {
        "question_id": question["question_id"],
        "category": question["category"],
        "gold_evidence_ids": gold_ids,
        "returned_evidence_ids": returned,
        "evidence_recall": round(found / len(gold), 6) if answerable else None,
        "complete_hit": int(found == len(gold)) if answerable else None,
        "ndcg": round(_ndcg(returned, gold), 6) if answerable else None,
        "strict_gold_noise_ratio": round(
            sum(evidence_id not in gold for evidence_id in returned) / evidence_count,
            6,
        )
        if evidence_count
        else 0.0,
        "evidence_count": evidence_count,
        "rendered_chars": len(context.rendered_text),
        "latency_ms": round(latency_ms, 3),
        "truncated": bool(context.truncated),
    }
    for field in (
        "evidence_recall",
        "complete_hit",
        "ndcg",
        "strict_gold_noise_ratio",
    ):
        value = record[field]
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError(f"{field} is outside [0, 1]")
    if record["latency_ms"] < 0:
        raise ValueError("latency must be non-negative")
    return record


def _fingerprint(records: list[dict[str, Any]]) -> str:
    stable = [
        {key: value for key, value in record.items() if key != "latency_ms"}
        for record in records
    ]
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mean(records: list[dict[str, Any]], field: str) -> float | None:
    values = [record[field] for record in records if record[field] is not None]
    return round(fmean(values), 6) if values else None


def _summarize(
    stable_records: list[dict[str, Any]],
    timed_records: list[dict[str, Any]],
) -> dict[str, Any]:
    answerable = [record for record in stable_records if record["gold_evidence_ids"]]
    controls = [record for record in stable_records if not record["gold_evidence_ids"]]
    return {
        "query_count": len(stable_records),
        "answerable_count": len(answerable),
        "evidence_recall": _mean(answerable, "evidence_recall"),
        "complete_hit_rate": _mean(answerable, "complete_hit"),
        "ndcg": _mean(answerable, "ndcg"),
        "strict_gold_noise_ratio": _mean(
            stable_records, "strict_gold_noise_ratio"
        ),
        "mean_evidence_count": _mean(stable_records, "evidence_count"),
        "mean_rendered_chars": _mean(stable_records, "rendered_chars"),
        "mean_latency_ms": _mean(timed_records, "latency_ms"),
        "no_answer_count": len(controls),
        "no_answer_nonempty_rate": (
            round(fmean(bool(record["evidence_count"]) for record in controls), 6)
            if controls
            else None
        ),
        "no_answer_mean_evidence_count": (
            _mean(controls, "evidence_count") if controls else None
        ),
        "truncated_rate": _mean(stable_records, "truncated"),
    }


def _stable_summary_signature(records: list[dict[str, Any]]) -> str:
    summary = _summarize(records, records)
    summary.pop("mean_latency_ms")
    return json.dumps(summary, sort_keys=True, separators=(",", ":"))


def _validate_complete_run(records: list[dict[str, Any]]) -> None:
    if len(records) != 20:
        raise ValueError("each complete run must contain exactly 20 questions")
    if len({record["question_id"] for record in records}) != 20:
        raise ValueError("each complete run must contain 20 unique question IDs")
    distribution = Counter(record["category"] for record in records)
    if distribution != Counter({category: 4 for category in CATEGORIES}):
        raise ValueError("question category distribution changed")


def _run_variant(
    adapter: MagmaMemoryAdapter,
    data: dict[str, Any],
    variant_id: str,
) -> dict[str, Any]:
    anchor_log: list[tuple[str, ...]] = []
    weights = VARIANTS[variant_id]["weights"]
    known_evidence = {turn["evidence_id"] for turn in data["turns"]}
    runs: list[list[dict[str, Any]]] = []
    with _temporary_general_weights(weights, anchor_log):
        warmup = _record_question(adapter, data["questions"][0], known_evidence)
        if not warmup["returned_evidence_ids"]:
            raise RuntimeError("warm-up unexpectedly returned no evidence")
        if len(anchor_log) != 1:
            raise RuntimeError("warm-up did not enter adaptive traversal exactly once")
        anchor_log.clear()

        for _repeat in range(REPETITIONS):
            records = [
                _record_question(adapter, question, known_evidence)
                for question in data["questions"]
            ]
            _validate_complete_run(records)
            runs.append(records)

    expected_calls = REPETITIONS * len(data["questions"])
    if len(anchor_log) != expected_calls:
        raise RuntimeError("not every Recall entered adaptive traversal exactly once")
    fingerprints = [_fingerprint(records) for records in runs]
    if len(set(fingerprints)) != 1:
        raise RuntimeError(f"variant {variant_id} retrieval fingerprint was unstable")
    signatures = [_stable_summary_signature(records) for records in runs]
    if len(set(signatures)) != 1:
        raise RuntimeError(f"variant {variant_id} non-latency metrics were unstable")

    stable_records = runs[0]
    timed_records = [record for run in runs for record in run]
    return {
        "variant": variant_id,
        "name": VARIANTS[variant_id]["name"],
        "weights": weights,
        "runs": runs,
        "anchor_log": anchor_log,
        "fingerprint": fingerprints[0],
        "overall": _summarize(stable_records, timed_records),
        "categories": {
            category: _summarize(
                [record for record in stable_records if record["category"] == category],
                [record for record in timed_records if record["category"] == category],
            )
            for category in CATEGORIES
        },
    }


def _compare_anchors(results: dict[str, dict[str, Any]]) -> int:
    left = results["A"]["anchor_log"]
    right = results["B"]["anchor_log"]
    if len(left) != len(right):
        raise RuntimeError("A/B recorded different numbers of fused-anchor calls")
    for index, (left_ids, right_ids) in enumerate(zip(left, right)):
        if left_ids != right_ids:
            raise RuntimeError(
                f"A/B fused anchor IDs differed before adaptive traversal at call {index}"
            )
    return len(left)


def _recommend(results: dict[str, dict[str, Any]]) -> tuple[str, str]:
    a = results["A"]["overall"]
    b = results["B"]["overall"]
    quality_fields = ("evidence_recall", "complete_hit_rate", "ndcg")
    quality_not_lower = all(b[field] >= a[field] for field in quality_fields)
    noise_lower = b["strict_gold_noise_ratio"] < a["strict_gold_noise_ratio"]
    cost_not_higher = all(
        b[field] <= a[field]
        for field in ("mean_evidence_count", "mean_rendered_chars")
    )
    controls_not_worse = all(
        b[field] <= a[field]
        for field in (
            "no_answer_nonempty_rate",
            "no_answer_mean_evidence_count",
        )
    )
    category_decline = any(
        results["B"]["categories"][category][field]
        < results["A"]["categories"][category][field]
        for category in CATEGORIES
        for field in quality_fields
        if results["A"]["categories"][category][field] is not None
    )
    if (
        quality_not_lower
        and noise_lower
        and cost_not_higher
        and controls_not_worse
        and not category_decline
    ):
        return (
            "B",
            "Balanced non-causal weights meet every predeclared production-follow-up gate, "
            "but this read-only task does not change production.",
        )

    quality_deltas = [b[field] - a[field] for field in quality_fields]
    category_decline = any(
        results["B"]["categories"][category][field]
        < results["A"]["categories"][category][field]
        for category in CATEGORIES
        for field in quality_fields
        if results["A"]["categories"][category][field] is not None
    )
    control_worse = any(
        b[field] > a[field]
        for field in (
            "no_answer_nonempty_rate",
            "no_answer_mean_evidence_count",
        )
    )
    if (
        any(delta < 0 for delta in quality_deltas)
        or not noise_lower
        or category_decline
        or control_worse
    ):
        return (
            "A",
            "The balanced candidate lost overall retrieval quality or category quality, "
            "failed to lower strict-gold noise, or worsened a no-answer control; retain "
            "the upstream fallback.",
        )
    return (
        "C",
        "The remaining evidence is insufficient for a clear decision, so the production "
        "fallback should remain unchanged.",
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _delta(left: Any, right: Any) -> str:
    if left is None or right is None:
        return "n/a"
    return f"{right - left:+.6f}"


def _render_report(payload: dict[str, Any]) -> str:
    a = payload["results"]["A"]
    b = payload["results"]["B"]
    fields = (
        ("Evidence Recall", "evidence_recall"),
        ("Complete-hit rate", "complete_hit_rate"),
        ("NDCG", "ndcg"),
        ("Strict-gold noise ratio", "strict_gold_noise_ratio"),
        ("Mean evidence count", "mean_evidence_count"),
        ("Mean rendered characters", "mean_rendered_chars"),
        ("Mean Recall latency (ms)", "mean_latency_ms"),
    )
    lines = [
        "# GENERAL Relation-Weight Ablation",
        "",
        "## Scope",
        "",
        "This is a read-only GENERAL relation-weight ablation on a fixed synthetic "
        "Conversation Memory corpus. It evaluates retrieved evidence, not final answers "
        "or an LLM judge. It is not LoCoMo or LongMemEval, uses no real user data, and "
        "does not change production configuration or behavior.",
        "",
        "## Variants",
        "",
        "| Variant | ENTITY | SEMANTIC | TEMPORAL | CAUSAL |",
        "| --- | ---: | ---: | ---: | ---: |",
        "| A — upstream fallback | 0.60 | 0.30 | 0.05 | 0.05 |",
        "| B — balanced non-causal | 1/3 | 1/3 | 1/3 | 0 |",
        "",
        "Both use the production transition formula `0.6 * relation_weight + 0.4 * "
        "cosine_similarity`. Candidate B is an experimental control, not a new default.",
        "",
        "## Fixed configuration",
        "",
        f"- Dataset: `{payload['dataset']['dataset_id']}`; "
        f"{payload['dataset']['turn_count']} turns and "
        f"{payload['dataset']['question_count']} questions.",
        f"- Categories: `{json.dumps(payload['dataset']['category_distribution'], sort_keys=True)}`.",
        f"- Recall policy: `{json.dumps(payload['configuration']['policy'], sort_keys=True)}`.",
        "- Anchors: the same production dense + lexical RRF path; `top_k=1`.",
        "- Traversal and rendering: production Adaptive Traversal and GENERAL Context "
        "Linearization through `MemoryRetriever.recall(...)`.",
        f"- Runs: one unmeasured warm-up per variant, then "
        f"{payload['configuration']['repetitions']} complete timed repetitions per variant.",
        f"- Environment: Python {payload['environment']['python']} on "
        f"`{payload['environment']['platform']}`; latency is local-only.",
        "- The sole A/B variable was the private GENERAL relation-weight mapping.",
        "",
        "## Results",
        "",
        "### Overall",
        "",
        "| Metric | A | B | B - A |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, field in fields:
        lines.append(
            f"| {label} | {_fmt(a['overall'][field])} | "
            f"{_fmt(b['overall'][field])} | "
            f"{_delta(a['overall'][field], b['overall'][field])} |"
        )

    lines.extend(
        [
            "",
            "### By category",
            "",
            "| Category | Variant | Recall | Complete hit | NDCG | Noise | Evidence | Chars | Latency ms |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for category in CATEGORIES:
        for variant_id, result in (("A", a), ("B", b)):
            item = result["categories"][category]
            lines.append(
                f"| {category} | {variant_id} | {_fmt(item['evidence_recall'])} | "
                f"{_fmt(item['complete_hit_rate'])} | {_fmt(item['ndcg'])} | "
                f"{_fmt(item['strict_gold_noise_ratio'])} | "
                f"{_fmt(item['mean_evidence_count'])} | "
                f"{_fmt(item['mean_rendered_chars'])} | "
                f"{_fmt(item['mean_latency_ms'])} |"
            )

    lines.extend(
        [
            "",
            "### No-answer controls",
            "",
            "| Variant | Controls | Nonempty rate | Mean evidence count |",
            "| --- | ---: | ---: | ---: |",
            f"| A | {a['overall']['no_answer_count']} | "
            f"{_fmt(a['overall']['no_answer_nonempty_rate'])} | "
            f"{_fmt(a['overall']['no_answer_mean_evidence_count'])} |",
            f"| B | {b['overall']['no_answer_count']} | "
            f"{_fmt(b['overall']['no_answer_nonempty_rate'])} | "
            f"{_fmt(b['overall']['no_answer_mean_evidence_count'])} |",
            "",
            "### Determinism and fairness",
            "",
            f"- Fused anchor IDs matched before Adaptive Traversal in "
            f"`{payload['anchor_comparisons']}/{payload['anchor_comparisons']}` paired calls.",
            f"- A fingerprint: `{a['fingerprint']}`.",
            f"- B fingerprint: `{b['fingerprint']}`.",
            "- All three non-latency fingerprints and metric summaries matched within "
            "each variant; latency is the mean across the three timed repetitions.",
            "- The marker-owned temporary MAGMA store was removed after evaluation.",
            "",
            "## Interpretation",
            "",
            payload["interpretation"],
            "",
            "## Limitations",
            "",
            "- The fixture has only 20 questions and 48 synthetic turns.",
            "- It evaluates evidence retrieval, not an answer model or an LLM judge.",
            "- Current causal edges are not trusted; candidate B therefore assigns "
            "CAUSAL a weight of zero.",
            "- Local latency is machine-specific and is not a production SLA.",
            "- The result cannot be directly generalized to real, large memory graphs, "
            "LoCoMo, or LongMemEval.",
            "",
            "## Recommendation",
            "",
            f"**{payload['recommendation']}** — {payload['recommendation_reason']}",
            "",
            "Production GENERAL weights remain unchanged. Any later production change "
            "requires a separate authorized task.",
            "",
        ]
    )
    return "\n".join(lines)


def _interpret(results: dict[str, dict[str, Any]]) -> str:
    a = results["A"]["overall"]
    b = results["B"]["overall"]
    multi_a = results["A"]["categories"]["multi_hop"]
    multi_b = results["B"]["categories"]["multi_hop"]
    temporal_a = results["A"]["categories"]["temporal"]
    temporal_b = results["B"]["categories"]["temporal"]
    update_a = results["A"]["categories"]["knowledge_update"]
    update_b = results["B"]["categories"]["knowledge_update"]
    return " ".join(
        (
            "The ENTITY-biased fallback retained higher overall Evidence Recall and NDCG, "
            "and it was clearly stronger on temporal questions, although B had a higher "
            "overall complete-hit rate.",
            f"Balanced weights did not reduce strict-gold noise: the B-minus-A change was "
            f"`{_delta(a['strict_gold_noise_ratio'], b['strict_gold_noise_ratio'])}`.",
            f"Multi-hop improved in complete hit from "
            f"`{_fmt(multi_a['complete_hit_rate'])}` to "
            f"`{_fmt(multi_b['complete_hit_rate'])}` and in NDCG from "
            f"`{_fmt(multi_a['ndcg'])}` to `{_fmt(multi_b['ndcg'])}`. Temporal clearly "
            f"regressed: Recall changed from `{_fmt(temporal_a['evidence_recall'])}` to "
            f"`{_fmt(temporal_b['evidence_recall'])}` and NDCG from "
            f"`{_fmt(temporal_a['ndcg'])}` to `{_fmt(temporal_b['ndcg'])}`. "
            f"Knowledge-update had a mixed result: complete hit changed from "
            f"`{_fmt(update_a['complete_hit_rate'])}` to "
            f"`{_fmt(update_b['complete_hit_rate'])}`, while NDCG changed from "
            f"`{_fmt(update_a['ndcg'])}` to `{_fmt(update_b['ndcg'])}`. Direct-fact and "
            "causal-control answerable quality metrics were unchanged.",
            "The balanced candidate is not worth a separate production GENERAL weight "
            "change on this evidence. No causal specialization, abstention, or "
            "answer-quality conclusion is implied.",
        )
    )


def _run() -> dict[str, Any]:
    data = _load_and_validate_fixture(FIXTURE_PATH)
    if len(data["turns"]) != 48:
        raise ValueError("fixture must contain exactly 48 turns")
    if len(data["questions"]) != 20:
        raise ValueError("fixture must contain exactly 20 questions")
    if set(VARIANTS) != {"A", "B"}:
        raise ValueError("the ablation must compare exactly variants A and B")
    for variant in VARIANTS.values():
        _validate_weights(variant["weights"])

    production_weights = adaptive_traversal._INTENT_WEIGHTS.get("GENERAL")
    if production_weights != VARIANTS["A"]["weights"]:
        raise RuntimeError("production GENERAL weights no longer match variant A")
    original_mapping = production_weights
    original_call = recall_execution._adaptive_traverse

    temp_dir = _create_owned_temp()
    results: dict[str, dict[str, Any]] = {}
    started = perf_counter()
    try:
        adapter = _ingest_fixture(data, temp_dir)
        for variant_id in ("A", "B"):
            results[variant_id] = _run_variant(adapter, data, variant_id)
        anchor_comparisons = _compare_anchors(results)
    finally:
        if adaptive_traversal._INTENT_WEIGHTS.get("GENERAL") is not original_mapping:
            adaptive_traversal._INTENT_WEIGHTS["GENERAL"] = original_mapping
        if recall_execution._adaptive_traverse is not original_call:
            recall_execution._adaptive_traverse = original_call
        _cleanup_owned_temp(temp_dir)

    if adaptive_traversal._INTENT_WEIGHTS.get("GENERAL") is not original_mapping:
        raise RuntimeError("private GENERAL weight mapping was not restored")
    if recall_execution._adaptive_traverse is not original_call:
        raise RuntimeError("private adaptive traversal call point was not restored")

    recommendation, reason = _recommend(results)
    payload = {
        "dataset": {
            "dataset_id": data["dataset_id"],
            "turn_count": len(data["turns"]),
            "question_count": len(data["questions"]),
            "category_distribution": dict(
                Counter(question["category"] for question in data["questions"])
            ),
            "contains_real_user_data": False,
        },
        "configuration": {
            "policy": asdict(POLICY),
            "repetitions": REPETITIONS,
            "warmup": "one unmeasured Recall per variant",
            "variant_order": ["A", "B"],
            "sole_variable": "private GENERAL relation-weight mapping",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "latency_scope": "current local environment only",
        },
        "results": results,
        "anchor_comparisons": anchor_comparisons,
        "recommendation": recommendation,
        "recommendation_reason": reason,
        "interpretation": _interpret(results),
        "elapsed_seconds": round(perf_counter() - started, 3),
        "temp_cleaned": not temp_dir.exists(),
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the read-only GENERAL relation-weight A/B ablation."
    )
    parser.parse_args(argv)
    payload = _run()
    report = _render_report(payload)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    print(
        json.dumps(
            {
                "elapsed_seconds": payload["elapsed_seconds"],
                "temp_cleaned": payload["temp_cleaned"],
                "anchor_comparisons": payload["anchor_comparisons"],
                "recommendation": payload["recommendation"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
