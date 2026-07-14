"""Cold-first logical compaction for the JSONL Hot Draft."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from core.cold_draft_store import ColdDraftStore
from core.draft_store import JsonlDraftStore


CompactionStatus = Literal[
    "skipped",
    "compacted",
    "cold_draft_failed",
    "hot_state_failed",
]


@dataclass(frozen=True)
class CompactionResult:
    status: CompactionStatus
    compacted: bool
    preserved_segment_id: str | None
    compressed_turn_count: int


class HotDraftCompactor:
    def __init__(
        self,
        hot_store: JsonlDraftStore,
        cold_store: ColdDraftStore,
        state_path: str | Path,
        *,
        retain_recent_raw_turns: int = 12,
        max_raw_turns_before_compression: int = 24,
    ) -> None:
        self._hot_store = hot_store
        self._cold_store = cold_store
        self._state_path = Path(state_path)
        self._retain_recent = max(1, int(retain_recent_raw_turns))
        self._max_raw = max(1, int(max_raw_turns_before_compression))

    def maybe_compact(self) -> CompactionResult:
        raw_turns = self._read_raw_turns()
        if len(raw_turns) <= self._max_raw:
            return self._skipped()

        state = self._read_state()
        already_compressed = self._compressed_until(state, len(raw_turns))
        eligible = raw_turns[already_compressed:]
        desired = len(eligible) - self._retain_recent
        boundary = self._complete_pair_boundary(eligible, desired)
        if boundary == 0:
            return self._skipped()

        segment_turns = [turn.storage_turn() for turn in eligible[:boundary]]
        segment_id = self._stable_segment_id(already_compressed, segment_turns)
        try:
            segment = self._cold_store.append_segment(
                segment_turns,
                source="hot_draft_precompression",
                segment_id=segment_id,
            )
        except Exception:
            return CompactionResult("cold_draft_failed", False, None, 0)

        summary = {
            "summary_id": f"summary-{segment_id}",
            "created_at": datetime.now(UTC).isoformat(),
            "source_segment_id": segment_id,
            "compressed_turn_count": boundary,
            "text": f"[Compressed conversation segment preserved in Cold Draft: {boundary} turns.]",
        }
        try:
            self._write_state(
                summaries=[*state.get("summaries", []), summary],
                compressed_until_count=already_compressed + boundary,
            )
        except Exception:
            return CompactionResult("hot_state_failed", False, segment["segment_id"], 0)

        return CompactionResult("compacted", True, segment["segment_id"], boundary)

    def get_context_turns(self, limit: int | None = None) -> list[dict[str, str]]:
        raw_turns = self._read_raw_turns()
        state = self._read_state()
        compressed_until = self._compressed_until(state, len(raw_turns))
        summaries = state.get("summaries", [])
        view = [
            {"role": "assistant", "text": summary["text"]}
            for summary in summaries
            if isinstance(summary, dict) and isinstance(summary.get("text"), str)
        ]
        view.extend(
            {"role": turn.role, "text": turn.text}
            for turn in raw_turns[compressed_until:]
        )
        if limit is None:
            return view
        safe_limit = limit if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else 1
        return view[-safe_limit:]

    def _read_raw_turns(self) -> list[Any]:
        return list(self._hot_store.list_recent(limit=1_000_000))

    def _read_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"summaries": [], "compressed_until_count": 0}
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"summaries": [], "compressed_until_count": 0}
        if not isinstance(state, dict) or not isinstance(state.get("summaries"), list):
            return {"summaries": [], "compressed_until_count": 0}
        return state

    def _write_state(
        self,
        *,
        summaries: list[dict[str, Any]],
        compressed_until_count: int,
    ) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "summaries": summaries,
                    "compressed_until_count": compressed_until_count,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self._state_path)

    @staticmethod
    def _compressed_until(state: dict[str, Any], raw_count: int) -> int:
        value = state.get("compressed_until_count", 0)
        if not isinstance(value, int) or isinstance(value, bool):
            return 0
        return min(max(value, 0), raw_count)

    @staticmethod
    def _complete_pair_boundary(eligible: list[Any], desired: int) -> int:
        boundary = 0
        while boundary + 2 <= desired and boundary + 1 < len(eligible):
            if eligible[boundary].role != "user" or eligible[boundary + 1].role != "assistant":
                break
            boundary += 2
        return boundary

    @staticmethod
    def _stable_segment_id(
        already_compressed: int,
        turns: list[dict[str, Any]],
    ) -> str:
        material = json.dumps(
            {"offset": already_compressed, "turns": turns},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"compact-{hashlib.sha256(material).hexdigest()[:32]}"

    @staticmethod
    def _skipped() -> CompactionResult:
        return CompactionResult("skipped", False, None, 0)
