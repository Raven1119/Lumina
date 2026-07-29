from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import platform
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any, Callable, TypeVar
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = ROOT / "Conversation_Memory"
FIXTURE_PATH = MEMORY_ROOT / "fixtures" / "recall_depth_eval.json"
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from adapter.backend import RealMagmaBackend  # noqa: E402
from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.models import ColdDraftSegment, ColdDraftTurn, RecallPolicy  # noqa: E402
from ingestion.state_store import IngestionStateStore  # noqa: E402


DEPTHS = (0, 1, 2)
CATEGORIES = (
    "direct_fact",
    "multi_hop",
    "temporal",
    "knowledge_update",
    "causal_control",
)
FIXED_POLICY = RecallPolicy(
    top_k=1,
    max_graph_depth=0,
    max_nodes=200,
    max_evidence_items=6,
    max_chars=6000,
)
MARKER_NAME = ".lumina-recall-depth-eval"
MARKER_VALUE = "recall-depth-eval-v1\n"
T = TypeVar("T")


def _quiet_call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return function(*args, **kwargs)


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _load_and_validate_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("dataset_id") != "recall-depth-eval-v1":
        raise ValueError("fixture dataset_id must be recall-depth-eval-v1")

    turns = data.get("turns")
    questions = data.get("questions")
    if not isinstance(turns, list) or not 40 <= len(turns) <= 60:
        raise ValueError("fixture must contain 40 to 60 turns")
    if not isinstance(questions, list) or len(questions) != 20:
        raise ValueError("fixture must contain exactly 20 questions")

    evidence_ids: list[str] = []
    timestamps: list[str] = []
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise ValueError(f"turn {index} must be an object")
        evidence_id = turn.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError(f"turn {index} has an invalid evidence_id")
        evidence_ids.append(evidence_id)
        for field in ("conversation_id", "text", "source_timezone"):
            if not isinstance(turn.get(field), str) or not turn[field].strip():
                raise ValueError(f"{evidence_id} has an invalid {field}")
        if turn.get("role") not in {"user", "assistant"}:
            raise ValueError(f"{evidence_id} has an invalid role")
        timestamp = turn.get("created_at")
        _parse_timestamp(timestamp, f"{evidence_id}.created_at")
        timestamps.append(timestamp)
        try:
            ZoneInfo(turn["source_timezone"])
        except Exception as exc:
            raise ValueError(f"{evidence_id} has an invalid source_timezone") from exc

    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("fixture evidence IDs must be unique")
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("fixture timestamps must be unique")

    known_evidence = set(evidence_ids)
    question_ids: list[str] = []
    category_counts: Counter[str] = Counter()
    control_count = 0
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise ValueError(f"question {index} must be an object")
        question_id = question.get("question_id")
        category = question.get("category")
        query = question.get("query")
        gold = question.get("gold_evidence_ids")
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError(f"question {index} has an invalid question_id")
        if category not in CATEGORIES:
            raise ValueError(f"{question_id} has an invalid category")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"{question_id} has an invalid query")
        if (
            not isinstance(gold, list)
            or any(not isinstance(item, str) for item in gold)
            or len(gold) != len(set(gold))
        ):
            raise ValueError(f"{question_id} has invalid gold evidence IDs")
        missing = set(gold) - known_evidence
        if missing:
            raise ValueError(f"{question_id} references missing gold evidence: {missing}")
        question_ids.append(question_id)
        category_counts[category] += 1
        if not gold:
            control_count += 1
            if category != "causal_control":
                raise ValueError("no-answer controls must use causal_control category")

    if len(question_ids) != len(set(question_ids)):
        raise ValueError("fixture question IDs must be unique")
    if category_counts != Counter({category: 4 for category in CATEGORIES}):
        raise ValueError("fixture must contain exactly four questions per category")
    if control_count != 2:
        raise ValueError("fixture must contain exactly two no-answer controls")
    return data


def _segments_from_fixture(data: dict[str, Any]) -> tuple[ColdDraftSegment, ...]:
    grouped: dict[str, list[ColdDraftTurn]] = defaultdict(list)
    conversation_order: list[str] = []
    for item in data["turns"]:
        conversation_id = item["conversation_id"]
        if conversation_id not in grouped:
            conversation_order.append(conversation_id)
        grouped[conversation_id].append(
            ColdDraftTurn(
                turn_id=item["evidence_id"],
                role=item["role"],
                content=item["text"],
                timestamp=_parse_timestamp(
                    item["created_at"],
                    f"{item['evidence_id']}.created_at",
                ),
                source_timezone=item["source_timezone"],
                timezone_source="client",
            )
        )

    segments = []
    for conversation_id in conversation_order:
        turns = tuple(grouped[conversation_id])
        segments.append(
            ColdDraftSegment(
                segment_id=f"{data['dataset_id']}:{conversation_id}",
                conversation_id=conversation_id,
                state="pending_digest",
                turns=turns,
                created_at=min(turn.timestamp for turn in turns),
                source_timezone=turns[0].source_timezone,
                schema_version="2",
            )
        )
    return tuple(segments)


def _ndcg(returned: list[str], gold: set[str]) -> float:
    if not returned or not gold:
        return 0.0
    dcg = sum(
        (1.0 if evidence_id in gold else 0.0) / math.log2(rank + 2)
        for rank, evidence_id in enumerate(returned)
    )
    ideal_relevant = min(len(gold), len(returned))
    idcg = sum(1.0 / math.log2(rank + 2) for rank in range(ideal_relevant))
    return dcg / idcg if idcg else 0.0


def _mean(records: list[dict[str, Any]], field: str) -> float | None:
    values = [record[field] for record in records if record[field] is not None]
    return round(fmean(values), 6) if values else None


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [record for record in records if record["gold_evidence_ids"]]
    controls = [record for record in records if not record["gold_evidence_ids"]]
    return {
        "query_count": len(records),
        "answerable_count": len(answerable),
        "mean_evidence_recall": _mean(answerable, "evidence_recall"),
        "complete_hit_rate": _mean(answerable, "complete_hit"),
        "mean_ndcg": _mean(answerable, "ndcg"),
        "mean_irrelevant_ratio": _mean(records, "irrelevant_ratio"),
        "control_nonempty_rate": (
            round(
                fmean(1.0 if record["evidence_count"] else 0.0 for record in controls),
                6,
            )
            if controls
            else None
        ),
        "control_mean_evidence_count": (
            round(fmean(record["evidence_count"] for record in controls), 6)
            if controls
            else None
        ),
        "mean_latency_ms": _mean(records, "latency_ms"),
        "mean_evidence_count": _mean(records, "evidence_count"),
        "mean_rendered_chars": _mean(records, "rendered_chars"),
        "truncated_rate": _mean(records, "truncated"),
    }


def _validate_results(records: list[dict[str, Any]]) -> None:
    if len(records) != 20 * len(DEPTHS):
        raise ValueError("evaluation did not produce all 60 question-depth results")
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_question[record["question_id"]].append(record)
        if record["depth"] not in DEPTHS:
            raise ValueError("unexpected depth in results")
        if record["evidence_count"] != len(record["returned_evidence_ids"]):
            raise ValueError("evidence count does not match returned IDs")
        if record["evidence_count"] > FIXED_POLICY.max_evidence_items:
            raise ValueError("evidence count exceeded max_evidence_items")
        if record["rendered_chars"] > FIXED_POLICY.max_chars:
            raise ValueError("rendered text exceeded max_chars")
        if record["depth"] == 0 and record["evidence_count"] > FIXED_POLICY.top_k:
            raise ValueError("depth 0 returned non-anchor expansion evidence")
        for field in ("evidence_recall", "complete_hit", "ndcg"):
            value = record[field]
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{field} is outside [0, 1]")
        if not 0.0 <= record["irrelevant_ratio"] <= 1.0:
            raise ValueError("irrelevant_ratio is outside [0, 1]")
        if record["latency_ms"] < 0 or record["rendered_chars"] < 0:
            raise ValueError("cost metrics must be non-negative")

    if len(by_question) != 20:
        raise ValueError("evaluation did not cover exactly 20 questions")
    for question_id, question_records in by_question.items():
        if {record["depth"] for record in question_records} != set(DEPTHS):
            raise ValueError(f"{question_id} did not run all three depths")
        non_depth_configs = {
            json.dumps(record["non_depth_policy"], sort_keys=True)
            for record in question_records
        }
        if len(non_depth_configs) != 1:
            raise ValueError(f"{question_id} used unequal non-depth parameters")


def _fingerprint(records: list[dict[str, Any]]) -> str:
    stable_records = []
    for record in records:
        stable_records.append(
            {
                key: value
                for key, value in record.items()
                if key not in {"latency_ms"}
            }
        )
    encoded = json.dumps(
        stable_records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _create_owned_temp() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="lumina-recall-depth-eval-"))
    (temp_dir / MARKER_NAME).write_text(MARKER_VALUE, encoding="utf-8")
    return temp_dir


def _cleanup_owned_temp(temp_dir: Path) -> None:
    marker = temp_dir / MARKER_NAME
    if not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER_VALUE:
        raise RuntimeError("refusing to clean unowned evaluation directory")
    shutil.rmtree(temp_dir)


def _run_evaluation(data: dict[str, Any], temp_dir: Path) -> dict[str, Any]:
    backend = _quiet_call(RealMagmaBackend, temp_dir / "magma")
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(temp_dir / "ingestion_state.json"),
        ingestion_version="recall-depth-eval-v1",
    )

    ingested_turns = 0
    for segment in _segments_from_fixture(data):
        result = _quiet_call(adapter.ingest, segment)
        if result.status != "completed" or len(result.memory_ids) != len(segment.turns):
            raise RuntimeError(
                f"real ingestion failed for {segment.segment_id}: "
                f"{result.safe_error_code or result.status}"
            )
        ingested_turns += len(segment.turns)
    if ingested_turns != len(data["turns"]):
        raise RuntimeError("real ingestion did not write every fixture turn")

    warmup_policy = replace(FIXED_POLICY, max_graph_depth=2)
    warmup = _quiet_call(adapter.recall, data["questions"][0]["query"], warmup_policy)
    if warmup.safe_error_code:
        raise RuntimeError(f"warm-up Recall failed: {warmup.safe_error_code}")

    non_depth_policy = {
        key: value
        for key, value in asdict(FIXED_POLICY).items()
        if key != "max_graph_depth"
    }
    known_evidence = {turn["evidence_id"] for turn in data["turns"]}
    records: list[dict[str, Any]] = []
    for question in data["questions"]:
        gold_ids = list(question["gold_evidence_ids"])
        gold = set(gold_ids)
        for depth in DEPTHS:
            policy = replace(FIXED_POLICY, max_graph_depth=depth)
            started = perf_counter()
            context = _quiet_call(adapter.recall, question["query"], policy)
            latency_ms = (perf_counter() - started) * 1000.0
            if context.safe_error_code:
                raise RuntimeError(
                    f"Recall failed for {question['question_id']} depth {depth}: "
                    f"{context.safe_error_code}"
                )
            returned = [item.provenance.turn_id for item in context.evidence]
            if len(returned) != len(set(returned)):
                raise ValueError("public Recall returned duplicate evidence IDs")
            unknown = set(returned) - known_evidence
            if unknown:
                raise ValueError(f"Recall returned unknown fixture evidence: {unknown}")
            found = len(gold.intersection(returned))
            answerable = bool(gold)
            evidence_count = len(returned)
            records.append(
                {
                    "question_id": question["question_id"],
                    "category": question["category"],
                    "depth": depth,
                    "returned_evidence_ids": returned,
                    "gold_evidence_ids": gold_ids,
                    "evidence_recall": (
                        round(found / len(gold), 6) if answerable else None
                    ),
                    "complete_hit": (
                        1 if answerable and found == len(gold) else 0
                        if answerable
                        else None
                    ),
                    "ndcg": round(_ndcg(returned, gold), 6)
                    if answerable
                    else None,
                    "irrelevant_ratio": round(
                        (
                            sum(item not in gold for item in returned)
                            / evidence_count
                        )
                        if evidence_count
                        else 0.0,
                        6,
                    ),
                    "latency_ms": round(latency_ms, 3),
                    "evidence_count": evidence_count,
                    "rendered_chars": len(context.rendered_text),
                    "truncated": bool(context.truncated),
                    "non_depth_policy": non_depth_policy,
                }
            )

    _validate_results(records)
    depth_summary = {
        str(depth): _summarize(
            [record for record in records if record["depth"] == depth]
        )
        for depth in DEPTHS
    }
    category_summary = {
        category: {
            str(depth): _summarize(
                [
                    record
                    for record in records
                    if record["category"] == category and record["depth"] == depth
                ]
            )
            for depth in DEPTHS
        }
        for category in CATEGORIES
    }
    return {
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
            "depths": list(DEPTHS),
            "fixed_policy": non_depth_policy,
            "allowed_edge_types": ["temporal", "semantic", "causal"],
            "run_order": "question fixture order; depth 0, then 1, then 2",
            "warmup": "one unmeasured depth-2 Recall before timed queries",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "latency_scope": "current local environment only; not a production SLA",
        },
        "item_results": records,
        "depth_summary": depth_summary,
        "category_depth_summary": category_summary,
        "retrieval_fingerprint": _fingerprint(records),
    }


def _print_payload(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            {
                "dataset": payload["dataset"],
                "configuration": payload["configuration"],
                "environment": payload["environment"],
                "retrieval_fingerprint": payload["retrieval_fingerprint"],
                "temp_cleaned": payload["temp_cleaned"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    print("ITEM_RESULTS")
    for record in payload["item_results"]:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    print("DEPTH_SUMMARY")
    for depth, summary in payload["depth_summary"].items():
        print(json.dumps({"depth": int(depth), **summary}, sort_keys=True))
    print("CATEGORY_DEPTH_SUMMARY")
    for category, summaries in payload["category_depth_summary"].items():
        for depth, summary in summaries.items():
            print(
                json.dumps(
                    {"category": category, "depth": int(depth), **summary},
                    sort_keys=True,
                )
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate current Conversation Memory Recall at graph depths 0, 1, and 2."
    )
    parser.add_argument("--output", type=Path, help="Optional machine-readable JSON output.")
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the marker-owned temporary MAGMA store for explicit debugging.",
    )
    args = parser.parse_args(argv)

    data = _load_and_validate_fixture(FIXTURE_PATH)
    temp_dir = _create_owned_temp()
    payload: dict[str, Any] | None = None
    cleaned = False
    try:
        payload = _run_evaluation(data, temp_dir)
    finally:
        if not args.keep_temp:
            _cleanup_owned_temp(temp_dir)
            cleaned = True

    if payload is None:
        raise RuntimeError("evaluation did not produce a result")
    payload["temp_cleaned"] = cleaned
    if args.keep_temp:
        payload["retained_temp_dir"] = str(temp_dir)
    if args.output:
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    _print_payload(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
