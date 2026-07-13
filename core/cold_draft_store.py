"""Segment-oriented JSONL Cold Draft storage."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_ALLOWED_ROLES = {"user", "assistant"}
_PENDING = "pending_digest"
_CONSUMED = "consumed"
_DEFAULT_SOURCE = "hot_draft_precompression"


class ColdDraftStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append_segment(
        self,
        turns: list[dict[str, str]],
        source: str = _DEFAULT_SOURCE,
        *,
        segment_id: str | None = None,
    ) -> dict[str, Any]:
        safe_turns = self._safe_turns(turns)
        safe_source = self._safe_source(source)
        safe_segment_id = self._safe_segment_id(segment_id)

        for existing in self._read_records():
            if existing.get("segment_id") != safe_segment_id:
                continue
            if (
                existing.get("turns") == safe_turns
                and existing.get("source") == safe_source
            ):
                return deepcopy(existing)
            raise ValueError("cold draft segment conflict")

        record = {
            "segment_id": safe_segment_id,
            "turns": safe_turns,
            "created_at": datetime.now(UTC).isoformat(),
            "source": safe_source,
            "state": _PENDING,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")
        return deepcopy(record)

    def list_pending(self, limit: int | None = None) -> list[dict[str, Any]]:
        pending = [
            record
            for record in self._read_records()
            if record.get("state") == _PENDING
        ]
        if limit is not None:
            pending = pending[: self._safe_limit(limit)]
        return deepcopy(pending)

    def mark_consumed(self, segment_id: str) -> bool:
        if not isinstance(segment_id, str) or not segment_id:
            return False
        records = self._read_records()
        for record in records:
            if record.get("segment_id") == segment_id and record.get("state") == _PENDING:
                record["state"] = _CONSUMED
                record["consumed_at"] = datetime.now(UTC).isoformat()
                self._rewrite_records(records)
                return True
        return False

    def _read_records(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                raw: Any = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self._valid_record(raw):
                records.append(raw)
        return records

    def _rewrite_records(self, records: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                file.write("\n")
        temporary.replace(self._path)

    @staticmethod
    def _safe_turns(turns: Any) -> list[dict[str, str]]:
        if not isinstance(turns, list) or not turns:
            raise ValueError("invalid cold draft turns")
        safe: list[dict[str, str]] = []
        for raw in turns:
            if not isinstance(raw, dict):
                raise ValueError("invalid cold draft turns")
            role = raw.get("role")
            text = raw.get("text")
            if role not in _ALLOWED_ROLES or not isinstance(text, str) or not text.strip():
                raise ValueError("invalid cold draft turns")
            safe.append({"role": role, "text": text})
        return safe

    @staticmethod
    def _safe_source(source: Any) -> str:
        if not isinstance(source, str) or not source.strip():
            return _DEFAULT_SOURCE
        return source.strip()

    @staticmethod
    def _safe_segment_id(segment_id: Any) -> str:
        if segment_id is None:
            return uuid.uuid4().hex
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValueError("invalid cold draft segment id")
        return segment_id.strip()

    @staticmethod
    def _safe_limit(limit: Any) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return 1
        return limit

    @staticmethod
    def _valid_record(raw: Any) -> bool:
        return (
            isinstance(raw, dict)
            and isinstance(raw.get("segment_id"), str)
            and isinstance(raw.get("turns"), list)
            and raw.get("state") in {_PENDING, _CONSUMED}
        )
