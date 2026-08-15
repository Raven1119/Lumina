from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .models import ColdDraftTurn


_SPAN_VERSION = "grounded_span_v2"
_MAX_SOURCE_SPAN_CHARS = 160
_SOURCE_BOUNDARIES = frozenset("\n!;?。！；？")


@dataclass(frozen=True)
class GroundedSpanUnit:
    unit_id: str
    turn_id: str
    source_role: Literal["user", "assistant"]
    text: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.source_role not in {"user", "assistant"}:
            raise ValueError("source_role must be user or assistant")


def build_grounded_spans(
    turns: Iterable[ColdDraftTurn],
) -> tuple[GroundedSpanUnit, ...]:
    """Build exact, deterministic units from every conversation turn."""

    units: list[GroundedSpanUnit] = []
    for turn in turns:
        if turn.role not in {"user", "assistant"}:
            continue
        for start, end in _source_slices(turn.content):
            units.append(GroundedSpanUnit(
                unit_id=(
                    f"{_SPAN_VERSION}:{turn.turn_id}:{start}:{end}"
                ),
                turn_id=turn.turn_id,
                source_role=turn.role,
                text=turn.content[start:end],
                start=start,
                end=end,
            ))
    return tuple(units)


def _source_slices(text: str) -> Iterable[tuple[int, int]]:
    start = 0
    for index, character in enumerate(text):
        if character not in _SOURCE_BOUNDARIES:
            continue
        end = index + 1
        if text[start:end].strip():
            yield from _bounded_slice(start, end)
        start = end
    if start < len(text) and text[start:].strip():
        yield from _bounded_slice(start, len(text))


def _bounded_slice(start: int, end: int) -> Iterable[tuple[int, int]]:
    cursor = start
    while cursor < end:
        next_cursor = min(cursor + _MAX_SOURCE_SPAN_CHARS, end)
        yield cursor, next_cursor
        cursor = next_cursor
