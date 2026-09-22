"""Read-only Chat Recall decisions and optional source-backed lookup intent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from Conversation_Memory.adapter.graph_read_query import GraphReadQuery


@dataclass(frozen=True)
class MindDecision:
    recall: bool
    query: GraphReadQuery | None = None
    audit: dict | None = None


class MindGate(Protocol):
    def decide(self, user_message: str, recent_context: list[dict[str, str]]) -> MindDecision:
        ...


class EvidenceSelector(Protocol):
    def select(
        self, user_message: str, recent_context: list[dict[str, str]],
        evidence_items: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        """Select existing evidence IDs, without changing facts or the question."""
        ...
