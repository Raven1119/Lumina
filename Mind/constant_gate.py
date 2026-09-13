"""Stage-1 placeholder Mind gate: always allows Recall.

This gate keeps the original-query behavior with no model call. Optional
contextual query fields keep defaults compatible with this boolean decision.
"""

from __future__ import annotations

from Mind.interfaces import MindDecision


class ConstantMindGate:
    def decide(
        self,
        user_message: str,
        recent_context: list[dict[str, str]],
    ) -> MindDecision:
        return MindDecision(recall=True)
