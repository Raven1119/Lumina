"""Experiment D derived one-shot Directive delivery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from Mind.trace import (
    MIND_DIRECTIVE_APPLIED,
    MIND_DIRECTIVE_ISSUED,
    MindEvent,
    MindTrace,
    TraceError,
    _validate_decision_id,
    replay_activation,
)


class DirectiveState(str, Enum):
    PENDING = "pending"
    CONSUMED = "consumed"


@dataclass(frozen=True)
class DirectiveApplication:
    directive_id: str
    decision_id: str
    text: str

    def as_model_context(self) -> str:
        """Render advisory guidance without changing Intention or authority."""
        return (
            "[Mind Supervisor Directive]\n"
            "Treat this high-level guidance as a strong advisory prior, "
            "not an order or execution plan.\n"
            f"{self.text}"
        )


def project_directive_state(
    events: Sequence[MindEvent],
) -> DirectiveState | None:
    """Derive pending/consumed solely from a validated append-only trace."""
    issue, applied = _directive_events(events)
    if issue is None:
        return None
    if applied is None:
        return DirectiveState.PENDING
    return DirectiveState.CONSUMED


def prepare_for_execution_decision(
    trace: MindTrace,
    decision_id: str,
    *,
    eligible: bool = True,
) -> DirectiveApplication | None:
    """Durably bind one pending Directive to one caller-named decision."""
    if type(trace) is not MindTrace:
        raise TraceError("invalid_trace")
    _validate_decision_id(decision_id)
    if type(eligible) is not bool:
        raise TraceError("invalid_eligibility_flag")

    trace._refresh_for_delivery()
    issue, applied = _directive_events(trace.events)
    if issue is None:
        return None
    if applied is not None:
        if applied.payload["decision_id"] != decision_id:
            return None
        return _application(issue, decision_id)
    if not eligible:
        return None

    trace.append(
        MIND_DIRECTIVE_APPLIED,
        {
            "decision_id": decision_id,
            "directive_id": issue.payload["directive_id"],
        },
        source_event_seqs=(issue.seq,),
    )
    return _application(issue, decision_id)


def _directive_events(
    events: Sequence[MindEvent],
) -> tuple[MindEvent | None, MindEvent | None]:
    history = tuple(events)
    replay_activation(history)
    issue = next(
        (
            event
            for event in history
            if event.event_type == MIND_DIRECTIVE_ISSUED
        ),
        None,
    )
    applied = next(
        (
            event
            for event in history
            if event.event_type == MIND_DIRECTIVE_APPLIED
        ),
        None,
    )
    return issue, applied


def _application(
    issue: MindEvent,
    decision_id: str,
) -> DirectiveApplication:
    return DirectiveApplication(
        directive_id=str(issue.payload["directive_id"]),
        decision_id=decision_id,
        text=str(issue.payload["text"]),
    )
