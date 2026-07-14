"""Bounded, synchronous, developer-triggered Dream entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from core.cold_draft_store import ColdDraftStore

from .cold_draft_digest import ColdDraftDigestionTask
from .interfaces import ColdDraftOwner
from .models import DreamRunPolicy, DreamRunReport, SegmentDigestResult


_ROOT = Path(__file__).resolve().parents[1]
_CONVERSATION_MEMORY_ROOT = _ROOT / "Conversation_Memory"
if str(_CONVERSATION_MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONVERSATION_MEMORY_ROOT))

from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.interfaces import MemoryIngestor  # noqa: E402
from ingestion.state_store import IngestionStateStore  # noqa: E402


class RealMemoryIngestorProvider:
    def __init__(self, persist_dir: Path, state_path: Path) -> None:
        self._persist_dir = persist_dir
        self._state_store = IngestionStateStore(state_path)
        self._cache: dict[str, MemoryIngestor] = {}

    def get(self, ingestion_version: str) -> MemoryIngestor:
        if ingestion_version not in self._cache:
            self._cache[ingestion_version] = MagmaMemoryAdapter.create_real(
                self._persist_dir,
                self._state_store,
                ingestion_version=ingestion_version,
            )
        return self._cache[ingestion_version]


class DreamRunner:
    def __init__(
        self,
        owner: ColdDraftOwner,
        task: ColdDraftDigestionTask,
    ) -> None:
        self._owner = owner
        self._task = task

    def run_once(self, policy: DreamRunPolicy) -> DreamRunReport:
        try:
            records = self._owner.list_pending(limit=policy.max_segments)
        except Exception:
            return DreamRunReport.from_results((
                SegmentDigestResult(
                    "dream-run",
                    "failed",
                    False,
                    False,
                    "cold_draft_read_failed",
                ),
            ))

        results: list[SegmentDigestResult] = []
        for record in records[: policy.max_segments]:
            try:
                result = self._task.digest(record, policy.ingestion_version)
            except Exception:
                result = SegmentDigestResult(
                    "invalid-segment",
                    "failed",
                    False,
                    False,
                    "unexpected_digestion_failure",
                )
            results.append(result)
            if result.status == "failed" and policy.stop_on_error:
                break
        return DreamRunReport.from_results(tuple(results))


def build_default_runner() -> DreamRunner:
    cold_path = Path(
        os.environ.get(
            "LUMINA_DREAM_COLD_DRAFT_PATH",
            str(_ROOT / "data" / "draft" / "cold_drafts.jsonl"),
        )
    )
    state_path = Path(
        os.environ.get(
            "LUMINA_DREAM_INGESTION_STATE_PATH",
            str(_ROOT / "data" / "conversation_memory" / "ingestion_state.json"),
        )
    )
    persist_dir = Path(
        os.environ.get(
            "LUMINA_DREAM_MAGMA_PERSIST_DIR",
            str(_ROOT / "data" / "conversation_memory" / "magma"),
        )
    )
    owner = ColdDraftStore(cold_path)
    provider = RealMemoryIngestorProvider(persist_dir, state_path)
    task = ColdDraftDigestionTask(owner, provider)
    return DreamRunner(owner, task)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded Cold Draft digestion once")
    parser.add_argument("--max-segments", type=int, default=10)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--ingestion-version", default="dream-v1")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = DreamRunPolicy(
            max_segments=args.max_segments,
            stop_on_error=args.stop_on_error,
            ingestion_version=args.ingestion_version,
        )
        report = build_default_runner().run_once(policy)
    except Exception:
        report = DreamRunReport.from_results((
            SegmentDigestResult(
                "dream-run",
                "failed",
                False,
                False,
                "dream_initialization_failed",
            ),
        ))
    print(json.dumps(asdict(report), ensure_ascii=False, separators=(",", ":")))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
