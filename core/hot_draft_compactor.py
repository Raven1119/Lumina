"""Cold-first rolling semantic compaction for the JSONL Hot Draft."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Literal

from core.cold_draft_store import ColdDraftStore
from core.contracts import MemoryTurn
from core.draft_store import HotDraftSummary, JsonlDraftStore


CompactionStatus = Literal["not_needed", "completed", "failed"]
SummaryCallable = Callable[[str | None, list[MemoryTurn]], str]


@dataclass(frozen=True)
class CompactionResult:
    status: CompactionStatus
    archived_turns: int
    summary_updated: bool


class HotDraftCompactor:
    def __init__(
        self,
        hot_store: JsonlDraftStore,
        cold_store: ColdDraftStore,
        state_path: str | Path,
        *,
        summarizer: SummaryCallable | None = None,
        retain_recent_raw_turns: int = 12,
        max_raw_turns_before_compression: int = 24,
    ) -> None:
        self._hot_store = hot_store
        self._cold_store = cold_store
        self._state_path = Path(state_path)
        self._summarizer = summarizer
        self._retain_recent = max(1, int(retain_recent_raw_turns))
        self._max_raw = max(1, int(max_raw_turns_before_compression))
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def maybe_compact(self) -> CompactionResult:
        context = self._hot_store.read_context()
        raw_turns = list(context.raw_turns)
        if len(raw_turns) <= self._max_raw:
            return self._not_needed()

        desired = len(raw_turns) - self._retain_recent
        boundary = self._complete_pair_boundary(raw_turns, desired)
        if boundary == 0:
            return self._not_needed()

        self._is_running = True
        try:
            moved_turns = raw_turns[:boundary]
            recent_turns = raw_turns[boundary:]
            old_summary = context.summary
            try:
                if self._summarizer is None:
                    raise RuntimeError("rolling summary callable is unavailable")
                summary_content = self._summarizer(
                    old_summary.content if old_summary is not None else None,
                    list(moved_turns),
                )
                if (
                    not isinstance(summary_content, str)
                    or not summary_content.strip()
                ):
                    raise ValueError("rolling summary is empty")
            except Exception:
                return self._failed()

            segment_turns = [turn.storage_turn() for turn in moved_turns]
            compaction_id = self._stable_compaction_id(segment_turns)
            segment_id = f"compact-{compaction_id}"
            try:
                segment = self._cold_store.append_segment(
                    segment_turns,
                    source="hot_draft_precompression",
                    segment_id=segment_id,
                )
            except Exception:
                return self._failed()

            generation = 1 if old_summary is None else old_summary.generation + 1
            source_turn_count = boundary
            if old_summary is not None:
                source_turn_count += old_summary.source_turn_count
            new_summary = HotDraftSummary(
                content=summary_content.strip(),
                generation=generation,
                source_turn_count=source_turn_count,
                updated_at=self._utc_now(),
            )
            try:
                self._hot_store.replace_contents_atomically(
                    new_summary,
                    recent_turns,
                )
            except Exception:
                return self._failed()

            try:
                self._write_state(
                    generation=generation,
                    compaction_id=compaction_id,
                    archived_segment_id=segment["segment_id"],
                )
            except Exception:
                # State is recovery metadata, not the authority for reading Hot.
                # Cold preservation and atomic Hot replacement already succeeded.
                return CompactionResult("completed", boundary, True)

            return CompactionResult("completed", boundary, True)
        finally:
            self._is_running = False

    def _write_state(
        self,
        *,
        generation: int,
        compaction_id: str,
        archived_segment_id: str,
    ) -> None:
        state = {
            "schema_version": 2,
            "generation": generation,
            "last_compaction_id": compaction_id,
            "last_archived_segment_id": archived_segment_id,
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._state_path.parent,
                prefix=f".{self._state_path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                json.dump(state, file, ensure_ascii=False, separators=(",", ":"))
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self._state_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _complete_pair_boundary(raw_turns: list[MemoryTurn], desired: int) -> int:
        boundary = 0
        while boundary + 2 <= desired and boundary + 1 < len(raw_turns):
            if (
                raw_turns[boundary].role != "user"
                or raw_turns[boundary + 1].role != "assistant"
            ):
                break
            boundary += 2
        return boundary

    @staticmethod
    def _stable_compaction_id(turns: list[dict[str, str]]) -> str:
        material = json.dumps(
            {"turns": turns},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()[:32]

    @staticmethod
    def _utc_now() -> str:
        return (
            datetime.now(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _not_needed() -> CompactionResult:
        return CompactionResult("not_needed", 0, False)

    @staticmethod
    def _failed() -> CompactionResult:
        return CompactionResult("failed", 0, False)
