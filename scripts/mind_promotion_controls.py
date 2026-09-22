"""Mind production-seam promotion control.

Proves the promotion control queries actually travel the production seam
LlmMindGate -> MessageRuntime -> real MAGMA retriever -> answer model:
for each query, assert the decision log recorded recall=true (no false
decline) and the answer model's system prompt contains the required evidence.

Runs in an isolated temp sandbox; real user data is never touched.
Heavy imports (MAGMA stack) happen inside main() so the module stays
importable for unit tests in the root venv.

Usage (Conversation_Memory venv, from repository root):
    Conversation_Memory/.venv/Scripts/python.exe -m scripts.mind_promotion_controls
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from core.contracts import ChatRequest
from Mind.decision_log import JsonlDecisionLog

ROOT = Path(__file__).resolve().parents[1]


def _read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_controls(
    runtime,
    *,
    log_path: Path,
    recording_model,
    query_specs,
) -> dict:
    controls = []
    for name, query, expected in query_specs:
        before = len(_read_log(log_path))
        result = runtime.handle_chat(ChatRequest(message=query))
        new_records = _read_log(log_path)[before:]
        prompt = (
            recording_model.system_prompts[-1]
            if recording_model.system_prompts
            else ""
        )
        controls.append(
            {
                "name": name,
                "query": query,
                "gate_recalled": any(
                    record.get("recall") is True for record in new_records
                ),
                "evidence_in_prompt": expected.casefold() in prompt.casefold(),
                "no_false_decline": "mind_recall_declined" not in result.events,
            }
        )
    return {
        "passed": all(
            control["gate_recalled"]
            and control["evidence_in_prompt"]
            and control["no_false_decline"]
            for control in controls
        ),
        "controls": controls,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        required=True,
        help="Explicit output directory for this isolated validation run",
    )
    args = parser.parse_args(argv)


    from Conversation_Memory.adapter.backend import RealMagmaBackend
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from Conversation_Memory.adapter.models import RecallPolicy
    from core.cold_draft_store import ColdDraftStore
    from core.draft_context import DraftContextProvider
    from core.draft_store import JsonlDraftStore
    from core.env_loader import load_env_file
    from core.hot_draft_compactor import HotDraftCompactor
    from core.message_runtime import MessageRuntime
    from core.model_client import build_model_client_from_env
    from Dream.cold_draft_digest import ColdDraftDigestionTask
    from Dream.models import DreamRunPolicy
    from Dream.runner import DreamRunner
    from Conversation_Memory.ingestion.state_store import IngestionStateStore
    from Mind.llm_gate import LlmMindGate
    from scripts.mind_gate_operational import _guard_sandbox
    from scripts.recall_e2e_test import (
        FIXED_TURNS,
        _HOT_TAIL,
        _QUERY_SPECS,
        _draft_turn,
        _fake_rolling_summarizer,
        _FixedIngestorProvider,
    )

    load_env_file(override=False)

    sandbox = _guard_sandbox(
        Path(tempfile.mkdtemp(prefix="lumina-mind-controls-"))
    )

    # Seed memory through the real production pipeline, mirroring the Recall
    # E2E: Hot Draft -> Cold-first compaction -> Dream -> MAGMA.
    hot_seed = JsonlDraftStore(sandbox / "seed" / "hot_drafts.jsonl")
    cold_store = ColdDraftStore(sandbox / "seed" / "cold_drafts.jsonl")
    for turn in (*FIXED_TURNS, *_HOT_TAIL):
        hot_seed.append_turn(_draft_turn(turn))
    compactor = HotDraftCompactor(
        hot_seed,
        cold_store,
        sandbox / "seed" / "hot_draft_compaction_state.json",
        summarizer=_fake_rolling_summarizer,
        retain_recent_raw_turns=2,
        max_raw_turns_before_compression=6,
    )
    if compactor.maybe_compact().status != "completed":
        raise RuntimeError("seed compaction failed")
    adapter = MagmaMemoryAdapter(
        RealMagmaBackend(sandbox / "magma"),
        IngestionStateStore(sandbox / "ingestion_state.json"),
        ingestion_version="grounded-span-v2",
    )
    runner = DreamRunner(
        cold_store,
        ColdDraftDigestionTask(cold_store, _FixedIngestorProvider(adapter)),
    )
    dream = runner.run_once(
        DreamRunPolicy(
            max_segments=10,
            stop_on_error=False,
            ingestion_version="grounded-span-v2",
        )
    )
    if dream.failed != 0:
        raise RuntimeError("seed Dream run failed")

    class RecordingModel:
        client_kind = "model"

        def __init__(self) -> None:
            self.system_prompts: list[str] = []

        def generate(self, recent_context, user_message, *, system_prompt):
            self.system_prompts.append(system_prompt)
            return "control answer"

    gate_client = build_model_client_from_env(
        model_name_override="deepseek-v4-pro",
        max_tokens_override=8,
        temperature_override=0.0,
    )
    if getattr(gate_client, "client_kind", None) != "model":
        raise RuntimeError("real model configuration required for controls")

    recording_model = RecordingModel()
    hot = JsonlDraftStore(sandbox / "draft" / "hot.jsonl")
    log_path = sandbox / "mind" / "decisions.jsonl"
    # Same diagnostic policy as the E2E 10-query suite: identical store and
    # policy isolate the Mind gate as the only variable versus the gate-free
    # E2E run. (Production-policy evidence bounds are a separate Recall
    # quality matter, out of promotion scope.)
    e2e_policy = RecallPolicy(
        top_k=5,
        max_chars=1200,
        max_evidence_items=5,
        max_graph_depth=6,
        max_nodes=200,
    )
    runtime = MessageRuntime(
        hot_store=hot,
        draft_context_provider=DraftContextProvider(hot),
        model_client=recording_model,
        chat_background="control background",
        recall_enabled=True,
        memory_retriever=adapter,
        recall_policy=e2e_policy,
        mind_gate=LlmMindGate(gate_client),
        mind_decision_log=JsonlDecisionLog(log_path),
    )

    report = run_controls(
        runtime,
        log_path=log_path,
        recording_model=recording_model,
        query_specs=_QUERY_SPECS,
    )
    report["sandbox"] = str(sandbox)
    report["created_at"] = (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mind_seam_controls.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"passed": report["passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
