"""Bounded Chat Recall decisions; no persistent identity or action authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

MAX_RECALL_QUERY_CHARS = 256
MAX_CONTEXT_REFS = 2
MAX_CONTEXT_REF_CHARS = 96


@dataclass(frozen=True)
class MindContextRef:
    """A local index and exact span in this call's existing context view."""
    index: int
    span: str


@dataclass(frozen=True)
class MindDecision:
    recall: bool
    query: str | None = None
    context_refs: tuple[MindContextRef, ...] = ()


class MindDecisionError(ValueError):
    """A rejected proposal with bounded, externally visible audit information."""
    def __init__(self, code: str, *, candidate_query: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.candidate_query = (
            candidate_query if isinstance(candidate_query, str) and len(candidate_query) <= 2048 else None
        )


def validate_mind_decision(
    decision: MindDecision, recent_context: list[dict[str, str]],
) -> None:
    """Check structure and literal provenance, not semantic coreference truth."""
    if not isinstance(decision, MindDecision):
        raise MindDecisionError("invalid_decision_type")
    candidate = decision.query if isinstance(decision.query, str) else None

    def reject(code: str) -> None:
        raise MindDecisionError(code, candidate_query=candidate)

    if type(decision.recall) is not bool:
        reject("invalid_recall_type")
    if type(decision.context_refs) is not tuple or len(decision.context_refs) > MAX_CONTEXT_REFS:
        reject("invalid_context_refs")
    if decision.query is None:
        if decision.context_refs:
            reject("refs_without_query")
        return
    if not decision.recall:
        reject("query_without_recall")
    if not isinstance(decision.query, str) or not decision.query.strip():
        reject("invalid_query")
    if len(decision.query) > MAX_RECALL_QUERY_CHARS:
        reject("query_too_long")
    if not decision.context_refs:
        reject("query_without_refs")
    seen = set()
    for ref in decision.context_refs:
        if not isinstance(ref, MindContextRef) or type(ref.index) is not int:
            reject("invalid_context_ref")
        if not 0 <= ref.index < len(recent_context):
            reject("context_index_out_of_range")
        if not isinstance(ref.span, str) or not ref.span.strip() or len(ref.span) > MAX_CONTEXT_REF_CHARS:
            reject("invalid_context_span")
        item = recent_context[ref.index]
        if (not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}
                or not isinstance(item.get("text"), str) or ref.span not in item["text"]):
            reject("context_source_mismatch")
        if ref.span not in decision.query:
            reject("context_span_not_in_query")
        identity = (ref.index, ref.span)
        if identity in seen:
            reject("duplicate_context_ref")
        seen.add(identity)


class MindGate(Protocol):
    """Decides Recall and optionally supplies a sourced standalone query."""
    def decide(
        self,
        user_message: str,
        recent_context: list[dict[str, str]],
    ) -> MindDecision:
        ...
