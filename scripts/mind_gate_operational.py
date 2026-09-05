"""Sandboxed A/B operational comparison for the Mind stage-2 promotion.

Runs a fixed synthetic message set through the real /api/chat path with the
ConstantMindGate (baseline) and the LlmMindGate (candidate) in two isolated,
identically seeded sandboxes, then compares end-to-end latency and provider
call counts, and verifies fail-open behavior under provider failure.

Real user data is never touched: every state path (Hot/Cold/compaction,
decision log, MAGMA persist) lives inside freshly created temp sandboxes.

Usage (Conversation_Memory venv, from repository root):
    Conversation_Memory/.venv/Scripts/python.exe -m scripts.mind_gate_operational
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
CM_ROOT = ROOT / "Conversation_Memory"

_GATE_MODEL_NAME = "deepseek-v4-pro"
_GATE_MAX_TOKENS = 8
_GATE_TEMPERATURE = 0.0
_MAX_ADDED_LATENCY_MEDIAN_MS = 3000.0

# Fixed 20-message synthetic set (subset of the stage-2 development set).
MESSAGES = (
    "我上周跟你说我报名了哪个马拉松比赛？",
    "我之前说过我对什么食物过敏？",
    "我们公司的新产品代号是什么？我前几天说过。",
    "我老公的生日是哪天？我以前告诉过你。",
    "我有没有跟你说过我会游泳？",
    "我喜欢的乐队是哪个？",
    "我大学学的什么专业？",
    "张三是我的大学室友，对吧？",
    "我上次是不是说 project Aurora 推迟到十月了？",
    "我之前说过我反对这个方案，对吧？",
    "早上好！",
    "今天心情不错，陪我聊聊。",
    "谢谢你，帮大忙了。",
    "帮我把这句话翻译成英文：今天天气很好，适合出门散步。",
    "1 到 100 之间有多少个质数？",
    "用 Python 写一个反转字符串的函数。",
    "法国的首都是哪里？",
    "光合作用的基本原理是什么？",
    "HTTP 和 HTTPS 有什么区别？",
    "二战是哪一年结束的？")

# Two synthetic segments seeded identically into both sandboxes so the Recall
# path has real memory content during the comparison.
_SEED_SEGMENTS = (
    (
        ("user", "我报名了下个月的杭州马拉松。"),
        ("assistant", "记下了，杭州马拉松。"),
    ),
    (
        ("user", "我对花生过敏，点菜的时候要注意。"),
        ("assistant", "好的，我会记住你对花生过敏。"),
    ),
)


class CountingModelClient:
    """ModelClient wrapper counting generate() calls (provider-call evidence)."""

    client_kind = "model"

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls += 1
        return self._inner.generate(
            recent_context,
            user_message,
            system_prompt=system_prompt,
        )

    def summarize_hot_draft(self, old_summary, moved_turns):
        return self._inner.summarize_hot_draft(old_summary, moved_turns)


def run_sequence(post, messages: list[str] | tuple[str, ...]) -> list[dict]:
    records = []
    for message in messages:
        started = time.perf_counter()
        status = post(message)
        records.append(
            {
                "message": message,
                "status": status,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
    return records


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return ordered[index]


def compare_runs(baseline: dict, candidate: dict) -> dict:
    base_lat = [r["latency_ms"] for r in baseline["records"]]
    cand_lat = [r["latency_ms"] for r in candidate["records"]]
    messages = len(baseline["records"])
    added_calls = (candidate["gate_calls"] - baseline["gate_calls"]) / messages
    added_latency = median(cand_lat) - median(base_lat)
    all_ok = all(
        r["status"] == 200
        for r in (*baseline["records"], *candidate["records"])
    )
    return {
        "baseline": {
            "median_ms": median(base_lat),
            "p95_ms": _p95(base_lat),
            "answer_calls": baseline["answer_calls"],
            "gate_calls": baseline["gate_calls"],
        },
        "candidate": {
            "median_ms": median(cand_lat),
            "p95_ms": _p95(cand_lat),
            "answer_calls": candidate["answer_calls"],
            "gate_calls": candidate["gate_calls"],
        },
        "added_calls_per_message": added_calls,
        "added_latency_median_ms": added_latency,
        "acceptance": {
            "added_calls_exactly_one": added_calls == 1.0,
            "median_added_latency_within_3s": (
                added_latency <= _MAX_ADDED_LATENCY_MEDIAN_MS
            ),
            "all_responses_ok": all_ok,
        },
    }


def _guard_sandbox(path: Path) -> Path:
    resolved = path.resolve()
    data_root = (ROOT / "data").resolve()
    if resolved == data_root or data_root in resolved.parents:
        raise ValueError(f"sandbox must not live under real data/: {resolved}")
    if ROOT.resolve() in resolved.parents:
        raise ValueError(f"sandbox must not live inside the repository: {resolved}")
    return resolved


def _seed_memory(persist_dir: Path) -> None:
    if str(CM_ROOT) not in sys.path:
        sys.path.insert(0, str(CM_ROOT))
    from adapter.magma_adapter import MagmaMemoryAdapter
    from adapter.models import ColdDraftSegment, ColdDraftTurn

    adapter = MagmaMemoryAdapter.create_real(
        persist_dir,
        fail_if_unavailable=True,
        ingestion_version="grounded-span-v2",
    )
    for index, turns in enumerate(_SEED_SEGMENTS, start=1):
        started_at = datetime(2026, 8, 10, 9, tzinfo=UTC)
        segment = ColdDraftSegment(
            segment_id=f"mind-ops-seed-{index}",
            conversation_id=f"mind-ops-seed-{index}",
            state="pending_digest",
            turns=tuple(
                ColdDraftTurn(
                    turn_id=f"mind-ops-seed-{index}-turn-{turn_index}",
                    role=role,
                    content=text,
                    timestamp=started_at,
                    source_timezone="Asia/Shanghai",
                    timezone_source="client",
                )
                for turn_index, (role, text) in enumerate(turns, start=1)
            ),
            created_at=started_at,
            source_timezone="Asia/Shanghai",
            schema_version="2",
        )
        result = adapter.ingest(segment)
        if getattr(result, "status", None) != "completed":
            raise RuntimeError(f"seed ingestion failed: {result!r}")


def _build_app(sandbox: Path, gate, gate_counter: CountingModelClient | None):
    if str(CM_ROOT) not in sys.path:
        sys.path.insert(0, str(CM_ROOT))
    os.environ["LUMINA_DREAM_MAGMA_PERSIST_DIR"] = str(sandbox / "magma")

    from fastapi.testclient import TestClient

    from core.main import create_app
    from core.model_client import build_model_client_from_env

    answer_client = CountingModelClient(build_model_client_from_env())
    if answer_client._inner.client_kind != "model":
        raise RuntimeError(
            "real model configuration required for the operational comparison"
        )
    app = create_app(
        draft_store_path=sandbox / "draft" / "hot_drafts.jsonl",
        mind_decision_log_path=sandbox / "mind" / "decisions.jsonl",
        model_client=answer_client,
        enable_compaction=False,
        recall_enabled=True,
        mind_gate=gate,
    )
    return TestClient(app), answer_client


def _run_variant(template: Path, work_dir: Path, name: str, gate) -> dict:
    sandbox = _guard_sandbox(work_dir / name)
    shutil.copytree(template, sandbox)
    client, answer_client = _build_app(sandbox, gate, None)
    # Warm up per variant (BGE lazy load, provider connection, first gate
    # call) so cold-start cost does not pollute the latency comparison.
    client.post("/api/chat", json={"message": "你好"})
    answer_calls_after_warmup = answer_client.calls
    gate_counter = getattr(gate, "_model_client", None)
    gate_calls_after_warmup = getattr(gate_counter, "calls", 0)
    records = run_sequence(
        lambda message: client.post(
            "/api/chat",
            json={"message": message},
        ).status_code,
        list(MESSAGES),
    )
    return {
        "records": records,
        "answer_calls": answer_client.calls - answer_calls_after_warmup,
        "gate_calls": getattr(gate_counter, "calls", 0) - gate_calls_after_warmup,
    }


def _check_fail_open(work_dir: Path) -> dict:
    from core.model_client import DeepSeekAnthropicModelClient
    from Mind.llm_gate import LlmMindGate

    sandbox = _guard_sandbox(work_dir / "fail_open")
    failing_client = DeepSeekAnthropicModelClient(
        api_key="invalid",
        base_url="https://provider.invalid/anthropic",
        model=_GATE_MODEL_NAME,
        max_tokens=_GATE_MAX_TOKENS,
        temperature=_GATE_TEMPERATURE,
        timeout=5.0,
    )
    gate = LlmMindGate(failing_client)
    client, _ = _build_app(sandbox, gate, None)
    statuses = [
        client.post("/api/chat", json={"message": message}).status_code
        for message in MESSAGES[:5]
    ]
    log_lines = (
        (sandbox / "mind" / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if (sandbox / "mind" / "decisions.jsonl").exists()
        else []
    )
    return {
        "statuses": statuses,
        "all_ok": all(status == 200 for status in statuses),
        "decision_log_lines": len(log_lines),
        "note": (
            "gate provider invalid: decisions must fail open (no log lines "
            "written for failed decisions is expected; chat must stay 200)"
        ),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="docs/experiments/mind_stage2_promotion/operational",
    )
    args = parser.parse_args(argv)

    from core.env_loader import load_env_file
    from Mind.constant_gate import ConstantMindGate
    from Mind.llm_gate import LlmMindGate
    from core.model_client import build_model_client_from_env

    load_env_file(override=False)

    work_dir = Path(tempfile.mkdtemp(prefix="lumina-mind-ops-"))
    template = _guard_sandbox(work_dir / "template")
    template.mkdir(parents=True)
    (template / "draft").mkdir()
    (template / "mind").mkdir()
    _seed_memory(template / "magma")

    baseline = _run_variant(template, work_dir, "baseline", ConstantMindGate())

    gate_client = CountingModelClient(
        build_model_client_from_env(
            model_name_override=_GATE_MODEL_NAME,
            max_tokens_override=_GATE_MAX_TOKENS,
            temperature_override=_GATE_TEMPERATURE,
        )
    )
    candidate = _run_variant(template, work_dir, "candidate", LlmMindGate(gate_client))

    fail_open = _check_fail_open(work_dir)
    comparison = compare_runs(baseline, candidate)
    comparison["fail_open"] = fail_open
    comparison["acceptance"]["fail_open_ok"] = fail_open["all_ok"]
    comparison["sandbox"] = str(work_dir)
    comparison["created_at"] = (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out / "baseline_records.json").write_text(
        json.dumps(baseline["records"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out / "candidate_records.json").write_text(
        json.dumps(candidate["records"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(comparison["acceptance"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
