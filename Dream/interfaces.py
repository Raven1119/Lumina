"""Narrow Lumina-owned dependencies used by the Dream runner."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from adapter.interfaces import MemoryIngestor


class ColdDraftOwner(Protocol):
    def list_pending(self, limit: int) -> list[dict[str, Any]]: ...

    def mark_consumed(self, segment_id: str) -> bool: ...


class MemoryIngestorProvider(Protocol):
    """Return an ingestor configured for the requested durable key version."""

    def get(self, ingestion_version: str) -> "MemoryIngestor": ...
