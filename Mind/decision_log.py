"""Append-only JSONL audit log for Mind decisions.

Follows the JsonlDraftStore append idiom: mkdir + open("a") + compact JSON
lines. This module never swallows exceptions; fail-soft handling lives in the
caller (core MessageRuntime).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from Mind.interfaces import MindDecision


class JsonlDecisionLog:
    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(path)
        self._clock = clock or (lambda: datetime.now(UTC))

    def record(
        self, decision: MindDecision, *, turn_id: str | None = None,
        query_audit: dict | None = None,
    ) -> None:
        decided_at = (
            self._clock()
            .astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        record = {
            "turn_id": turn_id,
            "recall": decision.recall,
            "decided_at": decided_at,
        }
        # Optional additions preserve direct legacy callers and existing lines.
        if query_audit is not None:
            record.update(query_audit)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
