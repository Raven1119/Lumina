"""Project recent Hot Draft turns into role/text model context."""

from __future__ import annotations

from typing import Protocol

from core.contracts import MemoryTurn


class RecentTurnReader(Protocol):
    def list_recent(self, limit: int = ...) -> list[MemoryTurn]:
        ...


class DraftContextProvider:
    """Read most recent draft turns from a draft store as role/text dicts."""

    DEFAULT_LIMIT = 12

    def __init__(self, store: RecentTurnReader, default_limit: int = DEFAULT_LIMIT) -> None:
        self._store = store
        self._default_limit = self._safe_limit(default_limit)

    def get_recent_context(self, limit: int | None = None) -> list[dict[str, str]]:
        effective_limit = self._safe_limit(
            limit if limit is not None else self._default_limit
        )
        turns = self._store.list_recent(effective_limit)
        return [{"role": turn.role, "text": turn.text} for turn in turns]

    @staticmethod
    def _safe_limit(limit: int) -> int:
        if not isinstance(limit, int) or limit < 1:
            return 1
        return limit
