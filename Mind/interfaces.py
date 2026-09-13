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
