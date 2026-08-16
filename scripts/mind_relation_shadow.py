"""Offline shadow harness for Mind-supplied relation surfaces (v3rel).

Evaluates whether a single extended Mind gate call can supply query relation
surfaces that the existing ControlledRelationResolver canonicalizes, so the
relation gate can operate in normal Chat. Shadow-only: production code
(`Mind/llm_gate.py`, `core/`) is never touched.

Design and acceptance thresholds: docs/plan/MIND_RELATION_SHADOW.md.
The model outputs RAW relation surface text (never canonical IDs); query-side
canonicalization is performed by the real ControlledRelationResolver.
Out-of-vocabulary probes must resolve to the resolver's real UNRESOLVED —
an empty relations list never stands in for UNRESOLVED.

Usage (Conversation_Memory venv, from repository root):
    Conversation_Memory/.venv/Scripts/python.exe -m scripts.mind_relation_shadow \
        --gate v3rel --runs 3 --delay 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CM_ROOT = ROOT / "Conversation_Memory"
if str(CM_ROOT) not in sys.path:
    sys.path.insert(0, str(CM_ROOT))

from adapter.controlled_relation import (  # noqa: E402
    UNRESOLVED,
    ControlledRelationResolver,
    _ALIASES,
)
from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.models import (  # noqa: E402
    BackendCandidate,
    RecallPolicy,
    SourceProvenance,
)
from ingestion.state_store import IngestionStateStore  # noqa: E402

V3REL_PROMPT_VERSION = "mind-gate-v3rel"
_GATE_MODEL_NAME = "MiniMax-M3"
_GATE_MAX_TOKENS = 64  # JSON output is longer than v2's single word
_GATE_TEMPERATURE = 0.0
_MAX_RELATIONS = 2
_RESOLVER = ControlledRelationResolver()

_REQUIRED_FIELDS = {
    "case_id",
    "stratum",
    "message",
    "recall_needed",
    "expected_relation_ids",
    "out_of_vocabulary",
    "memory",
    "rationale",
}


def load_cases(path: str | Path) -> list[dict]:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("evaluation set must be a JSON array")
    valid_ids = set(_ALIASES)
    seen: set[str] = set()
    for case in cases:
        missing = _REQUIRED_FIELDS - case.keys()
        if missing:
            raise ValueError(f"case missing fields: {sorted(missing)}")
        if not isinstance(case["recall_needed"], bool):
            raise ValueError(f"invalid recall_needed label: {case['case_id']}")
        expected = case["expected_relation_ids"]
        if not isinstance(expected, list) or any(
            item not in valid_ids for item in expected
        ):
            raise ValueError(f"invalid expected_relation_ids: {case['case_id']}")
        if case["out_of_vocabulary"] and expected:
            raise ValueError(
                f"out-of-vocabulary case must not expect ids: {case['case_id']}"
            )
        if case["case_id"] in seen:
            raise ValueError(f"duplicate case_id: {case['case_id']}")
        seen.add(case["case_id"])
    return cases


def build_v3rel_prompt(
    message: str,
    context: list[dict[str, str]],
) -> tuple[str, str]:
    vocabulary_lines = "\n".join(
        f"- {canonical_id}: {' / '.join(aliases)}"
        for canonical_id, aliases in sorted(_ALIASES.items())
    )
    system_prompt = (
        "你是 Lumina 的 Recall 门控。判断一条用户消息是否需要 "
        "Conversation Memory（长期对话记忆）才能可靠回答，并给出回答该消息"
        "需要从记忆中查证的关系（relation surface）。\n\n"
        "规则：\n"
        "- 仅凭当前可见的聊天上下文就能可靠回答 → recall 为 false\n"
        "- 不能可靠回答（包括需要查证记忆才能诚实地回答，或诚实地承认不知道）"
        "→ recall 为 true\n"
        "- relations: 回答该消息需要查证的关系短语（relation surface），"
        "0 到 2 个。用自然语言短语（中文或英文均可），照实描述所需关系；"
        "如果消息不需要记忆，输出空列表。\n"
        "- 参考关系词表（仅供措辞参考，不限于逐字匹配）：\n"
        f"{vocabulary_lines}\n\n"
        '只输出一行 JSON：{"recall": true或false, "relations": ["...", ...]}。'
        "不要输出任何其他内容，不要回答消息本身。"
    )
    context_text = "\n".join(
        f"{item.get('role', '?')}: {item.get('text', '')}" for item in context
    ) or "（无）"
    user_prompt = (
        f"[当前可见的聊天上下文]\n{context_text}\n\n"
        f"[待判断的用户消息]\n{message}\n\n"
        '[你的判断，只输出一行 JSON：{"recall": ..., "relations": [...]}]'
    )
    return system_prompt, user_prompt


def parse_v3rel_output(raw: str) -> tuple[bool, tuple[str, ...]]:
    if not isinstance(raw, str):
        raise ValueError("non-string gate output")
    try:
        payload = json.loads(raw.strip())
    except json.JSONDecodeError:
        raise ValueError("gate output is not bare JSON") from None
    if not isinstance(payload, dict) or set(payload) != {"recall", "relations"}:
        raise ValueError("gate output must be exactly {recall, relations}")
    recall = payload["recall"]
    relations = payload["relations"]
    if not isinstance(recall, bool):
        raise ValueError("recall must be a bool")
    if (
        not isinstance(relations, list)
        or len(relations) > _MAX_RELATIONS
        or any(not isinstance(item, str) or not item.strip() for item in relations)
    ):
        raise ValueError("relations must be a list of <=2 non-empty strings")
    return recall, tuple(item.strip() for item in relations)


class _RawRecordingClient:
    client_kind = "model"

    def __init__(self, inner) -> None:
        self._inner = inner
        self.last_text: str | None = None

    def generate(self, recent_context, user_message, *, system_prompt):
        self.last_text = None
        text = self._inner.generate(
            recent_context,
            user_message,
            system_prompt=system_prompt,
        )
        self.last_text = text
        return text


def build_v3rel_decide(client) -> Callable[[str, list[dict[str, str]]], dict]:
    """decide(message, context) -> outcome dict; protocol failures fail open."""
    if isinstance(client, _RawRecordingClient):
        recording = client
    else:
        recording = None

    def decide(message: str, context: list[dict[str, str]]) -> dict:
        system_prompt, user_prompt = build_v3rel_prompt(message, context)
        raw: str | None = None
        try:
            raw = client.generate([], user_prompt, system_prompt=system_prompt)
            if recording is not None:
                raw = recording.last_text
            recall, surfaces = parse_v3rel_output(raw)
        except Exception:
            # Fail open: allow Recall, supply no surfaces.
            return {
                "recall": True,
                "surfaces": (),
                "resolved_ids": (),
                "raw_output": raw if isinstance(raw, str) else None,
                "parse_ok": False,
            }
        return {
            "recall": recall,
            "surfaces": surfaces,
            # Query-side canonicalization by the real resolver; surfaces that
            # do not resolve become the resolver's genuine UNRESOLVED.
            "resolved_ids": _RESOLVER.resolve_query_relations(surfaces),
            "raw_output": raw,
            "parse_ok": True,
        }

    return decide


def _fixture_candidate(evidence_id: str, spec: dict) -> BackendCandidate:
    timestamp = datetime(2026, 8, 14, tzinfo=UTC)
    provenance = SourceProvenance(
        segment_id="relation-shadow-segment",
        conversation_id="relation-shadow-conversation",
        turn_id=f"turn-{evidence_id}",
        source_role="user",
        source_timestamp=timestamp.isoformat(),
        source_timezone="UTC",
        ingestion_version="grounded-formation-v1",
        timezone_source="configured_default",
    )
    return BackendCandidate(
        spec["text"],
        timestamp.isoformat(),
        None,
        {
            "evidence_id": evidence_id,
            "subject": spec["subject"],
            "relation": spec["relation"],
            "value": spec["value"],
            "provenance": provenance.__dict__,
        },
    )


class _FixtureBackend:
    def __init__(self, candidates) -> None:
        self._candidates = candidates

    def recall(self, _query, _policy, target_entity_ref=None):
        return list(self._candidates)


class _StubReranker:
    def score(self, _query, texts):
        return [1.0 - index / 10 for index, _ in enumerate(texts)]


def evaluate_downstream(
    case: dict,
    surfaces: tuple[str, ...] | None,
    *,
    state_path: str | Path,
) -> dict:
    """Run the real adapter+resolver against the case's synthetic memory.

    Returns correct/distractor presence in the recalled evidence.
    `memory.correct` is a list of units (two for compound cases);
    correct_present means ALL correct units survived.
    """
    memory = case.get("memory") or {}
    candidates = []
    correct_specs = memory.get("correct") or []
    for index, spec in enumerate(correct_specs, start=1):
        candidates.append(_fixture_candidate(f"correct-{index}", spec))
    if memory.get("distractor"):
        candidates.append(_fixture_candidate("distractor", memory["distractor"]))
    adapter = MagmaMemoryAdapter(
        _FixtureBackend(candidates),
        IngestionStateStore(state_path),
    )
    adapter._bge_reranker = _StubReranker()
    adapter._bge_reranker_load_attempted = True
    policy = RecallPolicy(
        max_graph_depth=0,
        max_evidence_items=4,
        final_min_score=None,
        relation_surfaces=surfaces,
    )
    context = adapter.recall(case["message"], policy)
    evidence_ids = {item.evidence_id for item in context.evidence}
    expected_correct = {f"correct-{i}" for i in range(1, len(correct_specs) + 1)}
    return {
        "correct_present": (
            expected_correct <= evidence_ids if correct_specs else None
        ),
        "distractor_present": (
            "distractor" in evidence_ids if memory.get("distractor") else None
        ),
    }


def oracle_surfaces(case: dict) -> tuple[str, ...] | None:
    """First-alias surfaces for the labeled ids, bypassing the model."""
    expected = case["expected_relation_ids"]
    if not expected:
        return None
    return tuple(_ALIASES[canonical_id][0] for canonical_id in expected)


def run_downstream(
    cases: list[dict],
    records: list[dict],
    *,
    state_dir: str | Path,
) -> dict:
    """Baseline / oracle / candidate evidence outcomes per relation case."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    results = {"baseline": {}, "oracle": {}, "candidate": []}
    for case in cases:
        if not case.get("memory"):
            continue
        case_id = case["case_id"]
        results["baseline"][case_id] = evaluate_downstream(
            case, None, state_path=state_dir / f"baseline-{case_id}.json"
        )
        results["oracle"][case_id] = evaluate_downstream(
            case,
            oracle_surfaces(case),
            state_path=state_dir / f"oracle-{case_id}.json",
        )
        for record in records:
            if record["case_id"] != case_id:
                continue
            surfaces = tuple(record["surfaces"]) or None
            outcome = evaluate_downstream(
                case,
                surfaces,
                state_path=(
                    state_dir
                    / f"candidate-{case_id}-run{record['run_index']}.json"
                ),
            )
            results["candidate"].append(
                {
                    "case_id": case_id,
                    "run_index": record["run_index"],
                    "surfaces": list(record["surfaces"]),
                    "recall": record["recall"],
                    "parse_ok": record["parse_ok"],
                    **outcome,
                }
            )
    return results


def summarize_downstream(results: dict) -> dict:
    def rate(entries, key, value):
        applicable = [entry for entry in entries if entry[key] is not None]
        if not applicable:
            return None
        return sum(1 for entry in applicable if entry[key] is value) / len(
            applicable
        )

    candidate = [
        entry
        for entry in results["candidate"]
        if entry["parse_ok"] and entry["recall"] is True
    ]
    return {
        # correct evidence filtered out by surfaces = unsafe kill
        "correct_evidence_survival": {
            "baseline": rate(results["baseline"].values(), "correct_present", True),
            "oracle": rate(results["oracle"].values(), "correct_present", True),
            "candidate": rate(candidate, "correct_present", True),
        },
        "wrong_relation_rejection": {
            "baseline": rate(
                results["baseline"].values(), "distractor_present", False
            ),
            "oracle": rate(results["oracle"].values(), "distractor_present", False),
            "candidate": rate(candidate, "distractor_present", False),
        },
        "correct_evidence_kills": [
            {"case_id": entry["case_id"], "run_index": entry["run_index"]}
            for entry in candidate
            if entry["correct_present"] is False
        ],
    }


def run_shadow(
    cases: list[dict],
    decide: Callable[[str, list[dict[str, str]]], dict],
    *,
    runs: int,
    log_path: str | Path,
    prompt_version: str,
    model_name: str,
    delay: float = 0.0,
) -> list[dict]:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    with path.open("a", encoding="utf-8") as file:
        for run_index in range(1, runs + 1):
            for case in cases:
                if delay > 0:
                    time.sleep(delay)
                started = time.monotonic()
                try:
                    outcome = decide(case["message"], case.get("context", []))
                except Exception:
                    outcome = {
                        "recall": True,
                        "surfaces": (),
                        "resolved_ids": (),
                        "raw_output": None,
                        "parse_ok": False,
                    }
                record = {
                    "run_index": run_index,
                    "case_id": case["case_id"],
                    "recall_needed_label": case["recall_needed"],
                    "recall": outcome["recall"],
                    "surfaces": list(outcome["surfaces"]),
                    "resolved_ids": [
                        item if item is not UNRESOLVED else "UNRESOLVED"
                        for item in outcome["resolved_ids"]
                    ],
                    "raw_output": outcome["raw_output"],
                    "parse_ok": outcome["parse_ok"],
                    "prompt_version": prompt_version,
                    "model": model_name,
                    "latency_ms": round((time.monotonic() - started) * 1000, 1),
                    "decided_at": datetime.now(UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                }
                file.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                records.append(record)
    return records


def summarize(records: list[dict], cases: list[dict]) -> dict:
    by_id = {case["case_id"]: case for case in cases}
    total = len(records)
    parse_ok = sum(1 for record in records if record["parse_ok"])

    unsafe_failures = []
    for record in records:
        case = by_id[record["case_id"]]
        if not record["parse_ok"]:
            if not (record["recall"] is True and not record["surfaces"]):
                unsafe_failures.append(
                    {"case_id": record["case_id"],
                     "reason": "protocol_failure_not_failed_open"}
                )
            continue
        if case["recall_needed"] and record["recall"] is False:
            unsafe_failures.append(
                {"case_id": record["case_id"],
                 "reason": "recall_needed_declined"}
            )
        if not case["recall_needed"] and record["recall"] is True:
            unsafe_failures.append(
                {"case_id": record["case_id"], "reason": "no_memory_allowed"}
            )

    # Extraction accuracy: parse-ok, recall=true, relation-labeled cases.
    per_stratum: dict[str, list[bool]] = {}
    for record in records:
        case = by_id[record["case_id"]]
        if (
            not record["parse_ok"]
            or record["recall"] is not True
            or not case["expected_relation_ids"]
        ):
            continue
        hit = sorted(record["resolved_ids"]) == sorted(
            case["expected_relation_ids"]
        )
        per_stratum.setdefault(case["stratum"], []).append(hit)
    extraction_accuracy = {
        stratum: sum(hits) / len(hits) for stratum, hits in per_stratum.items()
    }

    oov_records = [
        record
        for record in records
        if by_id[record["case_id"]]["out_of_vocabulary"]
        and record["parse_ok"]
        and record["recall"] is True
    ]
    oov_unresolved = sum(
        1
        for record in oov_records
        if record["surfaces"] and all(
            item == "UNRESOLVED" for item in record["resolved_ids"]
        )
    )
    oov_empty_relations = sum(
        1 for record in oov_records if not record["surfaces"]
    )

    # Per-case run-level fluctuation (parse-ok records only): any variance in
    # (recall bit, resolved ids) across runs.
    outcomes: dict[str, set] = {}
    for record in records:
        if not record["parse_ok"]:
            continue
        outcomes.setdefault(record["case_id"], set()).add(
            (record["recall"], tuple(record["resolved_ids"]))
        )
    fluctuating_cases = sorted(
        case_id for case_id, seen in outcomes.items() if len(seen) > 1
    )

    return {
        "records": total,
        "protocol_valid": parse_ok / total if total else 0.0,
        "unsafe_failures": unsafe_failures,
        "extraction_accuracy": extraction_accuracy,
        "oov_probe": {
            "evaluated": len(oov_records),
            "resolver_unresolved": oov_unresolved,
            "model_returned_empty_relations": oov_empty_relations,
        },
        "fluctuating_cases": fluctuating_cases,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        default="docs/experiments/mind_relation_shadow/relation_labels.json",
    )
    parser.add_argument(
        "--log",
        default="docs/experiments/mind_relation_shadow/shadow_log.jsonl",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--gate", choices=["v3rel", "v2"], default="v3rel"
    )
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="skip gate runs; read --log, run the three-way downstream "
        "comparison, and write downstream_summary.json next to the log",
    )
    args = parser.parse_args(argv)

    if args.analyze:
        import tempfile

        cases = load_cases(args.labels)
        records = [
            json.loads(line)
            for line in Path(args.log).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        with tempfile.TemporaryDirectory() as state_dir:
            downstream = run_downstream(cases, records, state_dir=state_dir)
        summary = {
            "gate": summarize(records, cases),
            "downstream": summarize_downstream(downstream),
        }
        summary_path = Path(args.log).parent / "downstream_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    from core.env_loader import load_env_file
    from core.model_client import build_model_client_from_env

    load_env_file(override=False)
    cases = load_cases(args.labels)
    if args.gate == "v2":
        from Mind.llm_gate import LlmMindGate

        client = _RawRecordingClient(
            build_model_client_from_env(
                model_name_override=_GATE_MODEL_NAME,
                max_tokens_override=8,
                temperature_override=_GATE_TEMPERATURE,
            )
        )
        gate = LlmMindGate(client)

        def decide(message, context):
            try:
                decision = gate.decide(message, context)
            except ValueError:
                return {
                    "recall": True,
                    "surfaces": (),
                    "resolved_ids": (),
                    "raw_output": client.last_text,
                    "parse_ok": False,
                }
            return {
                "recall": decision.recall,
                "surfaces": (),
                "resolved_ids": (),
                "raw_output": client.last_text,
                "parse_ok": True,
            }

        prompt_version = gate.prompt_version
    else:
        client = _RawRecordingClient(
            build_model_client_from_env(
                model_name_override=_GATE_MODEL_NAME,
                max_tokens_override=_GATE_MAX_TOKENS,
                temperature_override=_GATE_TEMPERATURE,
            )
        )
        decide = build_v3rel_decide(client)
        prompt_version = V3REL_PROMPT_VERSION

    records = run_shadow(
        cases,
        decide,
        runs=args.runs,
        log_path=args.log,
        prompt_version=prompt_version,
        model_name=_GATE_MODEL_NAME,
        delay=args.delay,
    )
    print(json.dumps(summarize(records, cases), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
