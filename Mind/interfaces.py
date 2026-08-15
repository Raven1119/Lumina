"""Mind organ decision contracts.

Mind is the decision organ sitting in front of Recall on the chat path.
Its stage-1 decision contract is deliberately minimal: a degenerate response
plan carrying only the recall verdict. Audit metadata (timestamps, reasons)
belongs to the decision log, not to this contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MindDecision:
    recall: bool


class MindGate(Protocol):
    """Decides whether the current user message should trigger Recall."""

    def decide(
        self,
        user_message: str,
        recent_context: list[dict[str, str]],
    ) -> MindDecision:
        ...
