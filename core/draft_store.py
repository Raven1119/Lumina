"""JSONL-backed rolling Hot Draft storage.

The file contains at most one summary record followed by recent raw turns.
Long-term preservation remains the responsibility of ColdDraftStore.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.contracts import MemoryTurn


@dataclass(frozen=True)
class HotDraftSummary:
    content: str
    generation: int
    source_turn_count: int
    updated_at: str

    def storage_record(self) -> dict[str, Any]:
        return {
            "record_type": "summary",
            "content": self.content,
            "generation": self.generation,
            "source_turn_count": self.source_turn_count,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class HotDraftContext:
    summary: HotDraftSummary | None
    raw_turns: tuple[MemoryTurn, ...]


class JsonlDraftStore:
    """Hot Draft owner for one rolling summary and recent raw turns."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append_turn(self, turn: MemoryTurn) -> int:
        if not turn.text.strip():
            raise ValueError("text is required")
        if not turn.has_native_provenance:
            raise ValueError("native turn provenance is required")

        existing_turns = self._read_turns()
        for existing in existing_turns:
            if existing.turn_id != turn.turn_id:
                continue
            if existing == turn:
                return len(existing_turns)
            raise ValueError("hot draft turn conflict")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as file:
            self._write_json_line(file, self._turn_record(turn))

        return len(existing_turns) + 1

    def list_recent(self, limit: int = 10) -> list[MemoryTurn]:
        safe_limit = self._safe_limit(limit)
        return self._read_turns()[-safe_limit:]

    def list_all_raw(self) -> list[MemoryTurn]:
        """Return all valid raw turns in chronological order."""
        return self._read_turns()

    def read_summary(self) -> HotDraftSummary | None:
        """Return the current summary independently from conversational turns."""
        return self._read_context().summary

    def read_context(self) -> HotDraftContext:
        """Read summary and raw turns from one file snapshot."""
        return self._read_context()

    def replace_contents_atomically(
        self,
        summary: HotDraftSummary,
        raw_turns: list[MemoryTurn],
    ) -> None:
        """Replace Hot Draft with summary-first JSONL without an invalid window."""
        if (
            not isinstance(summary, HotDraftSummary)
            or self._parse_summary_record(summary.storage_record()) != summary
        ):
            raise ValueError("valid hot draft summary is required")
        for turn in raw_turns:
            if not isinstance(turn, MemoryTurn) or not turn.text.strip():
                raise ValueError("valid raw turns are required")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                self._write_json_line(file, summary.storage_record())
                for turn in raw_turns:
                    self._write_json_line(file, self._turn_record(turn))
                file.flush()
                os.fsync(file.fileno())
            self._replace_file(temporary_path, self._path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _replace_file(source: Path, destination: Path) -> None:
        os.replace(source, destination)

    def _read_turns(self) -> list[MemoryTurn]:
        return list(self._read_context().raw_turns)

    def _read_context(self) -> HotDraftContext:
        if not self._path.exists():
            return HotDraftContext(None, ())

        turns: list[MemoryTurn] = []
        summary: HotDraftSummary | None = None
        try:
            with self._path.open("r", encoding="utf-8") as file:
                for line in file:
                    raw = self._parse_json_object(line)
                    if raw is None:
                        continue
                    if summary is None:
                        summary = self._parse_summary_record(raw)
                    turn = self._parse_turn_record(raw)
                    if turn is not None:
                        turns.append(turn)
        except OSError:
            return HotDraftContext(None, ())
        return HotDraftContext(summary, tuple(turns))

    @staticmethod
    def _turn_record(turn: MemoryTurn) -> dict[str, Any]:
        return {
            **turn.storage_turn(),
            "schema_version": 2 if turn.has_native_provenance else 1,
            "source": "chat_draft",
            "safe": True,
        }

    @staticmethod
    def _write_json_line(file: Any, record: dict[str, Any]) -> None:
        file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        file.write("\n")

    def _parse_turn_line(self, line: str) -> MemoryTurn | None:
        raw = self._parse_json_object(line)
        return self._parse_turn_record(raw)

    def _parse_turn_record(self, raw: dict[str, Any] | None) -> MemoryTurn | None:
        if raw is None or raw.get("record_type") == "summary":
            return None

        try:
            fields = {"role": raw.get("role"), "text": raw.get("text")}
            provenance_names = (
                "turn_id",
                "created_at",
                "source_timezone",
                "timezone_source",
            )
            native_record = raw.get("schema_version") == 2 or any(
                name in raw
                for name in ("turn_id", "source_timezone", "timezone_source")
            )
            if native_record:
                fields.update({name: raw.get(name) for name in provenance_names})
            return MemoryTurn.model_validate(fields)
        except ValidationError:
            return None

    def _parse_summary_line(self, line: str) -> HotDraftSummary | None:
        raw = self._parse_json_object(line)
        return self._parse_summary_record(raw)

    def _parse_summary_record(
        self,
        raw: dict[str, Any] | None,
    ) -> HotDraftSummary | None:
        if raw is None or raw.get("record_type") != "summary":
            return None
        content = raw.get("content")
        generation = raw.get("generation")
        source_turn_count = raw.get("source_turn_count")
        updated_at = raw.get("updated_at")
        if (
            not isinstance(content, str)
            or not content.strip()
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
            or not isinstance(source_turn_count, int)
            or isinstance(source_turn_count, bool)
            or source_turn_count < 1
            or not isinstance(updated_at, str)
        ):
            return None
        try:
            timestamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if (
            timestamp.tzinfo is None
            or timestamp.utcoffset() != UTC.utcoffset(timestamp)
        ):
            return None
        return HotDraftSummary(
            content=content,
            generation=generation,
            source_turn_count=source_turn_count,
            updated_at=updated_at,
        )

    @staticmethod
    def _parse_json_object(line: str) -> dict[str, Any] | None:
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError:
            return None
        return raw if isinstance(raw, dict) else None

    @staticmethod
    def _safe_limit(limit: int) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return 1
        return limit
