from __future__ import annotations

import re
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from adapter.models import ColdDraftTurn, NormalizedTemporalReference

_OFFSETS = {
    "today": timedelta(0),
    "yesterday": timedelta(days=-1),
    "tomorrow": timedelta(days=1),
    "last week": timedelta(weeks=-1),
    "next week": timedelta(weeks=1),
}
_PATTERN = re.compile(r"\b(last week|next week|yesterday|tomorrow|today)\b", re.IGNORECASE)


def normalize_temporal_references(turn: ColdDraftTurn, timezone_name: str) -> tuple[NormalizedTemporalReference, ...]:
    base = turn.timestamp
    if base.tzinfo is None or base.utcoffset() is None:
        raise ValueError("timestamp_timezone_required")
    if "/" in timezone_name:
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("source_timezone_invalid") from exc
    refs = []
    for match in _PATTERN.finditer(turn.content):
        expression = match.group(1).lower()
        normalized = base + _OFFSETS[expression]
        refs.append(NormalizedTemporalReference(
            original_expression=match.group(0),
            reference_timestamp=base.isoformat(),
            reference_timezone=timezone_name,
            normalized_start=normalized.isoformat(),
            normalized_end=normalized.isoformat(),
        ))
    return tuple(refs)
