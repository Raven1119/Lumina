"""Read-only Chat Recall permission; no query editing or action authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MindDecision:
    recall: bool


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
