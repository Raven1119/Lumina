from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
from copy import copy
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar


ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = ROOT / "Conversation_Memory"
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from adapter import backend as backend_module  # noqa: E402
from adapter.backend import RealMagmaBackend  # noqa: E402
from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.models import RecallPolicy  # noqa: E402
from Dream.cold_draft_digest import ColdDraftSegmentConverter  # noqa: E402
from ingestion.state_store import IngestionStateStore  # noqa: E402


INGESTION_VERSION = "cross-turn-reference-probe-v1"
MARKER_NAME = ".lumina-cross-turn-reference-probe"
MARKER_VALUE = "cross-turn-reference-probe-v1\n"
DEPTHS = (0, 1, 2)
REPETITIONS = 2
T = TypeVar("T")


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "A",
        "name": "显式实体基线",
        "scope": "same_segment",
        "query": "我以前问过郭盛老师什么？",
        "segments": (
            (
                "probe-a",
                (
                    ("a-antecedent", "user", "我的导师叫郭盛。"),
                    ("a-reference", "user", "郭盛主要研究什么？"),
                ),
            ),
        ),
        "antecedent_ids": ("a-antecedent",),
        "anaphor_ids": ("a-reference",),
    },
    {
        "id": "B",
        "name": "同 segment 代词",
        "scope": "same_segment",
        "query": "我以前问过郭盛老师什么？",
        "segments": (
            (
                "probe-b",
                (
                    ("b-antecedent", "user", "我的导师叫郭盛。"),
                    ("b-anaphor", "user", "他主要研究什么？"),
                ),
            ),
        ),
        "antecedent_ids": ("b-antecedent",),
        "anaphor_ids": ("b-anaphor",),
    },
    {
        "id": "C",
        "name": "地点指代",
        "scope": "same_segment",
        "query": "我问过 USC 的哪些事情？",
        "segments": (
            (
                "probe-c",
                (
                    ("c-antecedent", "user", "我准备去 USC。"),
                    ("c-anaphor", "user", "那里有哪些研究 AI Infra 的老师？"),
                ),
            ),
        ),
        "antecedent_ids": ("c-antecedent",),
        "anaphor_ids": ("c-anaphor",),
    },
    {
        "id": "D",
        "name": "方案指代",
        "scope": "same_segment",
        "query": "我最终选择了哪个方案，接下来要做什么？",
        "segments": (
            (
                "probe-d",
                (
                    ("d-antecedent", "user", "我们有三个方案。"),
                    ("d-choice", "user", "我决定采用第二个。"),
                    ("d-next", "user", "这个方案先实现最小闭环。"),
                ),
            ),
        ),
        "antecedent_ids": ("d-antecedent",),
        "anaphor_ids": ("d-choice", "d-next"),
    },
    {
        "id": "E",
        "name": "跨 segment 指代",
        "scope": "cross_segment",
        "query": "我以前问过郭盛老师什么？",
        "segments": (
            (
                "probe-e-one",
                (("e-antecedent", "user", "我的导师叫郭盛。"),),
            ),
            (
                "probe-e-two",
                (("e-anaphor", "user", "他主要研究什么？"),),
            ),
        ),
        "antecedent_ids": ("e-antecedent",),
        "anaphor_ids": ("e-anaphor",),
    },
    {
        "id": "F",
        "name": "弱语义承接",
        "scope": "same_segment",
        "query": "我们决定保留什么？",
        "segments": (
            (
                "probe-f",
                (
                    ("f-antecedent", "user", "先保留 upstream fallback。"),
                    ("f-anaphor", "user", "就按这个做。"),
                ),
            ),
        ),
        "antecedent_ids": ("f-antecedent",),
        "anaphor_ids": ("f-anaphor",),
    },
)


def _quiet_call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return function(*args, **kwargs)


def _create_owned_temp() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="lumina-cross-turn-reference-"))
    (directory / MARKER_NAME).write_text(MARKER_VALUE, encoding="utf-8")
    return directory


def _cleanup_owned_temp(directory: Path) -> None:
    marker = directory / MARKER_NAME
    if not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER_VALUE:
        raise RuntimeError("refusing to clean an unowned probe directory")
    shutil.rmtree(directory)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _raw_segment(
    segment_id: str,
    turns: tuple[tuple[str, str, str], ...],
    start: datetime,
) -> dict[str, Any]:
    raw_turns = []
    for index, (turn_id, role, text) in enumerate(turns):
        raw_turns.append(
            {
                "turn_id": turn_id,
                "role": role,
                "text": text,
                "created_at": _timestamp_text(start + timedelta(minutes=index)),
                "source_timezone": "UTC",
                "timezone_source": "configured_default",
            }
        )
    return {
        "schema_version": 2,
        "segment_id": segment_id,
        "turns": raw_turns,
        "created_at": _timestamp_text(start),
        "source": "synthetic_cross_turn_reference_probe",
        "state": "pending_digest",
    }


def _node_turn_id(node: Any) -> str | None:
    attributes = getattr(node, "attributes", None)
    if not isinstance(attributes, dict):
        return None
    provenance = attributes.get("provenance")
    if not isinstance(provenance, dict):
        return None
    turn_id = provenance.get("turn_id")
    return turn_id if isinstance(turn_id, str) else None


@contextmanager
def _capture_execution_context() -> Iterator[list[Any]]:
    original = backend_module._execute_fixed_recall
    captured: list[Any] = []

    def capture(**kwargs: Any) -> Any:
        context = original(**kwargs)
        captured.append(context)
        return context

    backend_module._execute_fixed_recall = capture
    try:
        yield captured
    finally:
        backend_module._execute_fixed_recall = original


def _policy(mode: str, depth: int) -> RecallPolicy:
    return RecallPolicy(
        top_k=1,
        max_graph_depth=depth,
        max_nodes=100,
        max_evidence_items=6,
        max_chars=4000,
        intent="GENERAL" if mode == "adaptive_general" else None,
    )


def _record_recall(
    adapter: MagmaMemoryAdapter,
    scenario: dict[str, Any],
    *,
    mode: str,
    depth: int,
) -> dict[str, Any]:
    with _capture_execution_context() as captured:
        public_context = _quiet_call(
            adapter.recall,
            scenario["query"],
            _policy(mode, depth),
        )
    if public_context.safe_error_code:
        raise RuntimeError(
            f"Recall failed for {scenario['id']} {mode} depth {depth}: "
            f"{public_context.safe_error_code}"
        )
    if len(captured) != 1:
        raise RuntimeError("Recall did not execute exactly one backend context")

    internal_context = captured[0]
    anchors = tuple(
        turn_id
        for turn_id in (
            _node_turn_id(node) for node in internal_context.anchor_nodes
        )
        if turn_id is not None
    )
    returned = tuple(item.provenance.turn_id for item in public_context.evidence)
    if len(returned) != len(set(returned)):
        raise RuntimeError("public Recall returned duplicate turn IDs")

    antecedents = tuple(scenario["antecedent_ids"])
    anaphors = tuple(scenario["anaphor_ids"])
    relevant = (*antecedents, *anaphors)
    antecedent_in_evidence = all(item in returned for item in antecedents)
    anaphor_in_evidence = all(item in returned for item in anaphors)
    co_retrieved = antecedent_in_evidence and anaphor_in_evidence
    correct_order = co_retrieved and [
        item for item in returned if item in relevant
    ] == list(relevant)
    return {
        "scenario_id": scenario["id"],
        "mode": mode,
        "depth": depth,
        "anchors": list(anchors),
        "antecedent_anchor": all(item in anchors for item in antecedents),
        "anaphor_anchor": all(item in anchors for item in anaphors),
        "returned_turn_ids": list(returned),
        "antecedent_in_evidence": antecedent_in_evidence,
        "anaphor_in_evidence": anaphor_in_evidence,
        "co_retrieved": co_retrieved,
        "correct_order": correct_order,
        "evidence_count": len(returned),
        "irrelevant_turn_ids": [
            item for item in returned if item not in relevant
        ],
        "truncated": public_context.truncated,
    }


def _aggregate(
    records: list[dict[str, Any]],
    *,
    mode: str,
    depth: int,
    scope: str | None = None,
) -> dict[str, Any]:
    selected = [
        record
        for record in records
        if record["mode"] == mode
        and record["depth"] == depth
        and (
            scope is None
            or next(
                item for item in SCENARIOS
                if item["id"] == record["scenario_id"]
            )["scope"] == scope
        )
    ]
    count = len(selected)
    if not count:
        raise RuntimeError("aggregate selection is empty")
    return {
        "scenario_count": count,
        "antecedent_anchor_rate": sum(
            record["antecedent_anchor"] for record in selected
        ) / count,
        "anaphor_anchor_rate": sum(
            record["anaphor_anchor"] for record in selected
        ) / count,
        "antecedent_recall_rate": sum(
            record["antecedent_in_evidence"] for record in selected
        ) / count,
        "anaphor_recall_rate": sum(
            record["anaphor_in_evidence"] for record in selected
        ) / count,
        "co_retrieval_rate": sum(
            record["co_retrieved"] for record in selected
        ) / count,
        "correct_order_rate": sum(
            record["correct_order"] for record in selected
        ) / count,
        "mean_evidence_count": sum(
            record["evidence_count"] for record in selected
        ) / count,
        "mean_irrelevant_count": sum(
            len(record["irrelevant_turn_ids"]) for record in selected
        ) / count,
    }


def _minimum_depth(
    records: list[dict[str, Any]],
    scenario_id: str,
    mode: str,
) -> int | None:
    for depth in DEPTHS:
        record = next(
            item for item in records
            if item["scenario_id"] == scenario_id
            and item["mode"] == mode
            and item["depth"] == depth
        )
        if record["co_retrieved"]:
            return depth
    return None


def _relationship_audit(
    backend: RealMagmaBackend,
    turn_to_node: dict[str, str],
) -> list[dict[str, Any]]:
    node_to_turn = {
        node_id: turn_id for turn_id, node_id in turn_to_node.items()
    }
    matching = []
    for link in backend.trg.graph_db.links.values():
        if (
            link.source_node_id not in node_to_turn
            or link.target_node_id not in node_to_turn
        ):
            continue
        matching.append(
            {
                "source_turn_id": node_to_turn[link.source_node_id],
                "target_turn_id": node_to_turn[link.target_node_id],
                "link_type": link.link_type.value,
                "sub_type": link.properties.get("sub_type"),
                "time_delta": link.properties.get("time_delta"),
            }
        )
    matching.sort(
        key=lambda item: (
            item["source_turn_id"],
            item["target_turn_id"],
            item["link_type"],
            str(item["sub_type"]),
        )
    )
    return matching


def _event_audit(
    backend: RealMagmaBackend,
    turn_to_node: dict[str, str],
    source_text: dict[str, str],
) -> dict[str, Any]:
    records = []
    for turn_id, node_id in turn_to_node.items():
        node = backend.trg.graph_db.get_node(node_id)
        attributes = dict(getattr(node, "attributes", {}))
        provenance = attributes.get("provenance", {})
        records.append(
            {
                "turn_id": turn_id,
                "text_exact": getattr(node, "content_narrative", None)
                == source_text[turn_id],
                "embedding_dimensions": len(
                    getattr(node, "embedding_vector", ()) or ()
                ),
                "role": attributes.get("role"),
                "segment_id": (
                    provenance.get("segment_id")
                    if isinstance(provenance, dict)
                    else None
                ),
                "conversation_id": (
                    provenance.get("conversation_id")
                    if isinstance(provenance, dict)
                    else None
                ),
                "has_segment_turn_index": "segment_turn_index" in attributes,
                "has_segment_turn_count": "segment_turn_count" in attributes,
            }
        )
    return {
        "event_count": len(records),
        "all_text_exact": all(record["text_exact"] for record in records),
        "embedding_dimensions": sorted(
            {record["embedding_dimensions"] for record in records}
        ),
        "roles": sorted({record["role"] for record in records}),
        "all_have_segment_id": all(record["segment_id"] for record in records),
        "all_have_conversation_id": all(
            record["conversation_id"] for record in records
        ),
        "any_have_segment_turn_index": any(
            record["has_segment_turn_index"] for record in records
        ),
        "any_have_segment_turn_count": any(
            record["has_segment_turn_count"] for record in records
        ),
    }


def _fingerprint(records: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _merge_event_audits(audits: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event_count": sum(item["event_count"] for item in audits),
        "all_text_exact": all(item["all_text_exact"] for item in audits),
        "embedding_dimensions": sorted(
            {
                dimension
                for item in audits
                for dimension in item["embedding_dimensions"]
            }
        ),
        "roles": sorted(
            {role for item in audits for role in item["roles"]}
        ),
        "all_have_segment_id": all(
            item["all_have_segment_id"] for item in audits
        ),
        "all_have_conversation_id": all(
            item["all_have_conversation_id"] for item in audits
        ),
        "any_have_segment_turn_index": any(
            item["any_have_segment_turn_index"] for item in audits
        ),
        "any_have_segment_turn_count": any(
            item["any_have_segment_turn_count"] for item in audits
        ),
    }


def _isolated_backend(
    template: RealMagmaBackend,
    persist_dir: Path,
) -> RealMagmaBackend:
    backend = copy(template)
    backend.persist_dir = persist_dir
    trg = copy(template.trg)
    trg.graph_db = type(template.trg.graph_db)()
    trg.vector_db = type(template.trg.vector_db)(
        dimension=template.trg.encoder.dimension,
        persist_path=str(persist_dir / "vectors"),
    )
    trg.persist_dir = persist_dir
    trg.stats = {key: 0 for key in template.trg.stats}
    trg.async_tasks = []
    trg.pending_consolidation = []
    backend.trg = trg
    return backend


def _run_probe(directory: Path) -> dict[str, Any]:
    converter = ColdDraftSegmentConverter()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records: list[dict[str, Any]] = []
    event_audits: list[dict[str, Any]] = []
    direct_relationships: dict[str, list[dict[str, Any]]] = {}
    scenario_fingerprints: dict[str, str] = {}
    total_turn_count = 0
    backend_template: RealMagmaBackend | None = None
    for scenario_index, scenario in enumerate(SCENARIOS):
        scenario_directory = directory / scenario["id"].lower()
        if backend_template is None:
            backend = _quiet_call(
                RealMagmaBackend,
                scenario_directory / "magma",
            )
            backend_template = backend
        else:
            backend = _isolated_backend(
                backend_template,
                scenario_directory / "magma",
            )
        adapter = MagmaMemoryAdapter(
            backend,
            IngestionStateStore(
                scenario_directory / "ingestion-state.json"
            ),
            ingestion_version=INGESTION_VERSION,
        )
        turn_to_node: dict[str, str] = {}
        source_text: dict[str, str] = {}
        scenario_start = start + timedelta(days=scenario_index)
        for segment_index, (segment_id, turns) in enumerate(
            scenario["segments"]
        ):
            segment_start = scenario_start + timedelta(minutes=segment_index * 10)
            raw = _raw_segment(segment_id, turns, segment_start)
            segment = converter.convert(raw, INGESTION_VERSION)
            result = _quiet_call(adapter.ingest, segment)
            if (
                result.status != "completed"
                or len(result.memory_ids) != len(segment.turns)
            ):
                raise RuntimeError(
                    f"ingestion failed for {segment_id}: "
                    f"{result.safe_error_code or result.status}"
                )
            for turn, memory_id in zip(segment.turns, result.memory_ids):
                turn_to_node[turn.turn_id] = memory_id
                source_text[turn.turn_id] = turn.content
        expected_turns = sum(len(turns) for _segment, turns in scenario["segments"])
        if len(turn_to_node) != expected_turns:
            raise RuntimeError(
                f"probe did not ingest every turn for scenario {scenario['id']}"
            )
        total_turn_count += expected_turns

        repeated: list[list[dict[str, Any]]] = []
        for _repeat in range(REPETITIONS):
            scenario_records = []
            for mode in ("fixed", "adaptive_general"):
                for depth in DEPTHS:
                    scenario_records.append(
                        _record_recall(
                            adapter,
                            scenario,
                            mode=mode,
                            depth=depth,
                        )
                    )
            repeated.append(scenario_records)

        fingerprints = [_fingerprint(items) for items in repeated]
        if len(set(fingerprints)) != 1:
            raise RuntimeError(
                f"Recall results were not stable for scenario {scenario['id']}"
            )
        scenario_fingerprints[scenario["id"]] = fingerprints[0]
        records.extend(repeated[0])
        event_audits.append(
            _event_audit(backend, turn_to_node, source_text)
        )
        direct_relationships[scenario["id"]] = _relationship_audit(
            backend,
            turn_to_node,
        )

    return {
        "fixture": {
            "scenario_count": len(SCENARIOS),
            "segment_count": sum(len(item["segments"]) for item in SCENARIOS),
            "turn_count": total_turn_count,
            "contains_real_user_data": False,
        },
        "policy": {
            "top_k": 1,
            "depths": list(DEPTHS),
            "max_nodes": 100,
            "max_evidence_items": 6,
            "max_chars": 4000,
            "fixed": {
                "intent": None,
                "beam_width": None,
                "drop_threshold": None,
            },
            "adaptive_general": {
                "intent": "GENERAL",
                "resolved_beam_width": 10,
                "resolved_drop_threshold": 0.15,
                "relation_weights": {
                    "ENTITY": 0.6,
                    "SEMANTIC": 0.3,
                    "TEMPORAL": 0.05,
                    "CAUSAL": 0.05,
                },
            },
        },
        "event_audit": _merge_event_audits(event_audits),
        "direct_relationships": direct_relationships,
        "records": records,
        "minimum_co_retrieval_depth": {
            mode: {
                scenario["id"]: _minimum_depth(records, scenario["id"], mode)
                for scenario in SCENARIOS
            }
            for mode in ("fixed", "adaptive_general")
        },
        "aggregates": {
            mode: {
                str(depth): _aggregate(records, mode=mode, depth=depth)
                for depth in DEPTHS
            }
            for mode in ("fixed", "adaptive_general")
        },
        "scope_aggregates_at_depth_1": {
            mode: {
                scope: _aggregate(
                    records,
                    mode=mode,
                    depth=1,
                    scope=scope,
                )
                for scope in ("same_segment", "cross_segment")
            }
            for mode in ("fixed", "adaptive_general")
        },
        "stable_repetitions": REPETITIONS,
        "scenario_fingerprints": scenario_fingerprints,
        "retrieval_fingerprint": _fingerprint(records),
    }


def main() -> int:
    directory = _create_owned_temp()
    payload: dict[str, Any] | None = None
    try:
        payload = _run_probe(directory)
    finally:
        _cleanup_owned_temp(directory)
    if payload is None:
        raise RuntimeError("probe did not produce a result")
    payload["temp_cleaned"] = not directory.exists()
    output = {
        "fixture": payload["fixture"],
        "policy": payload["policy"],
        "event_audit": payload["event_audit"],
        "minimum_co_retrieval_depth": payload["minimum_co_retrieval_depth"],
        "aggregates": payload["aggregates"],
        "scope_aggregates_at_depth_1": payload[
            "scope_aggregates_at_depth_1"
        ],
        "scenario_results": [
            {
                "scenario_id": record["scenario_id"],
                "mode": record["mode"],
                "depth": record["depth"],
                "anchors": record["anchors"],
                "returned_turn_ids": record["returned_turn_ids"],
                "co_retrieved": record["co_retrieved"],
                "correct_order": record["correct_order"],
                "irrelevant_count": len(record["irrelevant_turn_ids"]),
            }
            for record in payload["records"]
        ],
        "stable_repetitions": payload["stable_repetitions"],
        "retrieval_fingerprint": payload["retrieval_fingerprint"],
        "temp_cleaned": payload["temp_cleaned"],
    }
    print(
        json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
