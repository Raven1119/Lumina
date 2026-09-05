"""Offline shadow evaluation harness for the stage-2 Mind Recall gate.

Replays the labeled evaluation set through a gate decision callable without
touching the production chat path, appends per-case records to an append-only
JSONL shadow log, and summarizes the promotion-gate metrics defined in
docs/plan/MIND_STAGE2_SHADOW_EXPERIMENT.md.

Usage:
    python -m scripts.mind_gate_shadow --gate constant   # pipeline dry run
    python -m scripts.mind_gate_shadow                   # real LLM gate, 3 runs
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from core.model_client import ModelClient, build_model_client_from_env
from Mind.constant_gate import ConstantMindGate
from Mind.llm_gate import LlmMindGate

_GATE_MODEL_NAME = "deepseek-v4-pro"
_GATE_MAX_TOKENS = 8
_GATE_TEMPERATURE = 0.0
_FALSE_ALLOW_RATE_LIMIT = 0.25

_VALID_LABELS = {True, False, "ambiguous"}
_REQUIRED_FIELDS = {"case_id", "message", "recall_needed", "rationale", "stratum"}

# decide(message, context) -> (shadow_recall, raw_output, parse_ok)
DecideFn = Callable[[str, list[dict[str, str]]], tuple[bool | None, str | None, bool]]


def load_cases(path: str | Path) -> list[dict]:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("evaluation set must be a JSON array")
    seen: set[str] = set()
    for case in cases:
        missing = _REQUIRED_FIELDS - case.keys()
        if missing:
            raise ValueError(f"case missing fields: {sorted(missing)}")
        if case["recall_needed"] not in _VALID_LABELS:
            raise ValueError(f"invalid recall_needed label: {case['case_id']}")
        if case["case_id"] in seen:
            raise ValueError(f"duplicate case_id: {case['case_id']}")
        seen.add(case["case_id"])
    return cases


def run_shadow(
    cases: list[dict],
    decide: DecideFn,
    *,
    runs: int,
    log_path: str | Path,
    prompt_version: str,
    model_name: str,
    clock: Callable[[], datetime] | None = None,
    delay: float = 0.0,
) -> dict:
    clock = clock or (lambda: datetime.now(UTC))
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
                    recall, raw_output, parse_ok = decide(
                        case["message"],
                        case.get("context", []),
                    )
                except Exception:
                    recall, raw_output, parse_ok = None, None, False
                record = {
                    "run_index": run_index,
                    "case_id": case["case_id"],
                    "recall_needed_label": case["recall_needed"],
                    "shadow_recall": recall,
                    "raw_output": raw_output,
                    "parse_ok": parse_ok,
                    "prompt_version": prompt_version,
                    "model": model_name,
                    "latency_ms": round((time.monotonic() - started) * 1000, 1),
                    "decided_at": clock()
                    .astimezone(UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                }
                file.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                records.append(record)
    return _summarize(records)


def _effective_decision(record: dict) -> bool:
    # Protocol failures fail open to allow, mirroring the production seam.
    if not record["parse_ok"]:
        return True
    return bool(record["shadow_recall"])


def _summarize(records: list[dict]) -> dict:
    evaluated = [r for r in records if r["recall_needed_label"] in (True, False)]
    true_ids = sorted(
        {r["case_id"] for r in evaluated if r["recall_needed_label"] is True}
    )
    safety_failures = [
        case_id
        for case_id in true_ids
        if not all(
            _effective_decision(r) for r in evaluated if r["case_id"] == case_id
        )
    ]
    false_records = [r for r in evaluated if r["recall_needed_label"] is False]
    false_allow_rate = (
        sum(1 for r in false_records if _effective_decision(r))
        / len(false_records)
        if false_records
        else 0.0
    )
    fluctuating_false = sorted(
        case_id
        for case_id in {r["case_id"] for r in false_records}
        if len(
            {_effective_decision(r) for r in false_records if r["case_id"] == case_id}
        )
        > 1
    )
    protocol_failures = [r["case_id"] for r in records if not r["parse_ok"]]
    return {
        "evaluated_case_ids": sorted({r["case_id"] for r in evaluated}),
        "safety_failures": safety_failures,
        "false_allow_rate": false_allow_rate,
        "fluctuating_false_cases": fluctuating_false,
        "protocol_failures": protocol_failures,
        "promotion_gate_passed": (
            not safety_failures and false_allow_rate <= _FALSE_ALLOW_RATE_LIMIT
        ),
    }


class _RawRecordingClient:
    """ModelClient wrapper exposing the last raw provider text to the harness."""

    client_kind = "model"

    def __init__(self, inner: ModelClient) -> None:
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


def _build_llm_decide() -> tuple[DecideFn, str, str]:
    client = _RawRecordingClient(
        build_model_client_from_env(
            model_name_override=_GATE_MODEL_NAME,
            max_tokens_override=_GATE_MAX_TOKENS,
            temperature_override=_GATE_TEMPERATURE,
        )
    )
    gate = LlmMindGate(client)

    def decide(message: str, context: list[dict[str, str]]):
        try:
            decision = gate.decide(message, context)
        except ValueError:
            return None, client.last_text, False
        return decision.recall, client.last_text, True

    return decide, gate.prompt_version, _GATE_MODEL_NAME


def _build_constant_decide() -> tuple[DecideFn, str, str]:
    gate = ConstantMindGate()

    def decide(message: str, context: list[dict[str, str]]):
        return gate.decide(message, context).recall, "constant", True

    return decide, "constant-gate", "none"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        default="docs/experiments/mind_stage2_shadow/recall_needed_labels.json",
    )
    parser.add_argument(
        "--log",
        default="docs/experiments/mind_stage2_shadow/shadow_log.jsonl",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--gate", choices=["llm", "constant"], default="llm")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="seconds to sleep between gate calls (provider pacing)",
    )
    args = parser.parse_args(argv)

    if args.gate == "llm":
        from core.env_loader import load_env_file

        load_env_file(override=False)

    cases = load_cases(args.labels)
    if args.gate == "constant":
        decide, prompt_version, model_name = _build_constant_decide()
    else:
        decide, prompt_version, model_name = _build_llm_decide()
    summary = run_shadow(
        cases,
        decide,
        runs=args.runs,
        log_path=args.log,
        prompt_version=prompt_version,
        model_name=model_name,
        delay=args.delay,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
