"""Minimal JSONL-backed draft chat turn store.

This is a hot-path draft buffer, not MemoryRuntime or long-term memory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.contracts import MemoryTurn


class JsonlDraftStore:
    """Append-only JSONL draft store for minimal chat turns.

    `list_recent` returns chronological results within the requested recent
    slice. Invalid JSONL lines are skipped safely.
    """

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

        record = {
            **turn.storage_turn(),
            "schema_version": 2,
            "source": "chat_draft",
            "safe": True,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")

        return len(self._read_turns())

    def list_recent(self, limit: int = 10) -> list[MemoryTurn]:
        safe_limit = self._safe_limit(limit)
        return self._read_turns()[-safe_limit:]

    def _read_turns(self) -> list[MemoryTurn]:
        if not self._path.exists():
            return []

        turns: list[MemoryTurn] = []
        with self._path.open("r", encoding="utf-8") as file:
            for line in file:
                turn = self._parse_line(line)
                if turn is not None:
                    turns.append(turn)
        return turns

    def _parse_line(self, line: str) -> MemoryTurn | None:
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError:
            return None

        if not isinstance(raw, dict):
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

    def _safe_limit(self, limit: int) -> int:
        if not isinstance(limit, int) or limit < 1:
            return 1
        return limit
