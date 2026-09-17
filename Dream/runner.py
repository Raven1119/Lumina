"""Bounded, synchronous, developer-triggered Dream entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from core.cold_draft_store import ColdDraftStore
from core.model_client import ModelClient, build_model_client_from_env

from .cold_draft_digest import ColdDraftDigestionTask
from .interfaces import ColdDraftOwner
from .models import DreamRunPolicy, DreamRunReport, SegmentDigestResult


_ROOT = Path(__file__).resolve().parents[1]
_CONVERSATION_MEMORY_ROOT = _ROOT / "Conversation_Memory"
_LEGACY_INGESTION_VERSION = "grounded-span-v2"
_FORMATION_MAX_TOKENS = 8192
if str(_CONVERSATION_MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONVERSATION_MEMORY_ROOT))

from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.grounded_formation import FORMATION_ENTITY_VERSION as FORMATION_VERSION  # noqa: E402
from adapter.reliable_formation import (  # noqa: E402
    FORMATION_RELIABLE_VERSION,
    FORMATION_RELIABLE_VERSION_V5,
    FORMATION_RELIABLE_VERSION_V6,
)
from adapter.interfaces import MemoryIngestor  # noqa: E402
from ingestion.state_store import IngestionStateStore  # noqa: E402


class RealMemoryIngestorProvider:
    def __init__(
        self,
        persist_dir: Path,
        state_path: Path,
        formation_model: ModelClient | None = None,
        *, first_hit=None, cold_store=None, associative_read_profile="first-hit-v1",
    ) -> None:
        self._persist_dir = persist_dir
        self._state_store = IngestionStateStore(state_path)
        self._formation_model = formation_model
        self._first_hit = first_hit
        self._cold_store = cold_store
        self._associative_read_profile = associative_read_profile
        self._cache: dict[str, MemoryIngestor] = {}

    def get(self, ingestion_version: str) -> MemoryIngestor:
        if ingestion_version not in {FORMATION_VERSION, FORMATION_RELIABLE_VERSION,
                                     FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6,
                                     _LEGACY_INGESTION_VERSION}:
            raise RuntimeError("unsupported_ingestion_version")
        if (
            ingestion_version in {FORMATION_VERSION, FORMATION_RELIABLE_VERSION,
                                  FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6}
            and self._formation_model is None
        ):
            raise RuntimeError("formation_model_unavailable")
        if ingestion_version not in self._cache:
            self._cache[ingestion_version] = MagmaMemoryAdapter.create_real(
                self._persist_dir,
                self._state_store,
                ingestion_version=ingestion_version,
                first_hit=self._first_hit,
                cold_store=self._cold_store,
                associative_read_profile=self._associative_read_profile,
                formation_model=(
                    self._formation_model
                    if ingestion_version in {FORMATION_VERSION, FORMATION_RELIABLE_VERSION,
                                             FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6}
                    else None
                ),
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
            records = (
                self._owner.list_pending(limit=policy.max_segments)
                if policy.stop_on_error
                else self._owner.list_pending_page(limit=policy.max_segments)
            )
        except Exception:
            return DreamRunReport.from_results((
                SegmentDigestResult(
                    "dream-run",
                    "failed",
                    False,
                    False,
                    "cold_draft_read_failed",
                    retryable=True,
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
                    retryable=True,
                )
            results.append(result)
            if result.status == "failed" and policy.stop_on_error:
                break
            if not policy.stop_on_error:
                try:
                    # The owner key comes from the selected record, never the
                    # sanitized diagnostic identifier in the digest result.
                    saved = self._owner.advance_pending_cursor(record["segment_id"])
                except Exception:
                    saved = False
                if not saved:
                    return DreamRunReport.from_results(tuple(results), progress_saved=False)
        return DreamRunReport.from_results(tuple(results))


def build_formation_model_client() -> ModelClient:
    return build_model_client_from_env(
        max_tokens_override=_FORMATION_MAX_TOKENS,
    )


def build_default_runner(
    model_client: ModelClient | None = None,
    *, first_hit=None, associative_read_profile="first-hit-v1",
) -> DreamRunner:
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
    owner = ColdDraftStore(
        cold_path, **({"source_window_segments": 32} if first_hit is not None else {}),
    )
    effective_model = model_client or build_formation_model_client()
    provider = RealMemoryIngestorProvider(
        persist_dir,
        state_path,
        effective_model
        if getattr(effective_model, "client_kind", None) == "model"
        else None,
        first_hit=first_hit, cold_store=owner, associative_read_profile=associative_read_profile,
    )
    task = ColdDraftDigestionTask(owner, provider)
    return DreamRunner(owner, task)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded Cold Draft digestion once")
    parser.add_argument("--max-segments", type=int, default=10)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--ingestion-version", default=None)
    parser.add_argument("--first-hit", action="store_true",
                        help="Explicit first-hit-v1 local links for an explicit historical Formation version")
    parser.add_argument("--reliable-memory", action="store_true",
                        help="Deprecated: Formation v6, FirstHit local links and reliable-v2 reading are the default for a configured real model")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        effective_model = build_formation_model_client()
        explicit_version = args.ingestion_version
        if args.reliable_memory and explicit_version not in {None, FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6}:
            raise ValueError("reliable_memory_requires_reliable_formation")
        real_model = getattr(effective_model, "client_kind", None) == "model"
        # Promoted default: a configured real model without an explicit
        # historical version uses the v6 writer and reliable-v2 reading.
        reliable = args.reliable_memory or (explicit_version is None and real_model)
        ingestion_version = explicit_version or (
            FORMATION_RELIABLE_VERSION_V6
            if reliable
            else (
                FORMATION_VERSION
                if real_model
                else _LEGACY_INGESTION_VERSION
            )
        )
        policy = DreamRunPolicy(
            max_segments=args.max_segments,
            stop_on_error=args.stop_on_error,
            ingestion_version=ingestion_version,
        )
        if args.first_hit or reliable:
            from adapter.first_hit import FirstHitPolicy
            if ingestion_version not in {FORMATION_VERSION, FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6}:
                raise ValueError("first_hit_requires_supported_formation_version")
            runner = build_default_runner(
                effective_model, first_hit=FirstHitPolicy(),
                **({"associative_read_profile": "reliable-v2"} if reliable else {}),
            )
        else:
            runner = build_default_runner(effective_model)
        report = runner.run_once(policy)
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
    return 1 if report.failed or not report.progress_saved else 0


if __name__ == "__main__":
    raise SystemExit(main())
