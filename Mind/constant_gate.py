"""Stage-1 placeholder Mind gate: always allows Recall.

This gate exists to put the seam, audit logging, and fail-soft behavior in
place with zero behavior change. A real LLM-backed gate is stage 2 and must
not alter the MindGate interface.
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
