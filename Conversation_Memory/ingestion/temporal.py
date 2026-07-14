from __future__ import annotations

import re
from datetime import timedelta, timezone
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
_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def normalize_temporal_references(
    turn: ColdDraftTurn,
    timezone_name: str | None = None,
) -> tuple[NormalizedTemporalReference, ...]:
    base = turn.timestamp
    if base.tzinfo is None or base.utcoffset() is None:
        raise ValueError("timestamp_timezone_required")
    timezone_name = timezone_name or turn.source_timezone
    offset_match = _OFFSET.fullmatch(timezone_name)
    if offset_match:
        hours = int(offset_match.group(2))
        minutes = int(offset_match.group(3))
        if hours > 23 or minutes > 59:
            raise ValueError("source_timezone_invalid")
        delta = timedelta(hours=hours, minutes=minutes)
        if offset_match.group(1) == "-":
            delta = -delta
        source_zone = timezone(delta)
    else:
        try:
            source_zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("source_timezone_invalid") from exc
    localized_base = base.astimezone(source_zone)
    refs = []
    for match in _PATTERN.finditer(turn.content):
        expression = match.group(1).lower()
        normalized = localized_base + _OFFSETS[expression]
        refs.append(NormalizedTemporalReference(
            original_expression=match.group(0),
            reference_timestamp=localized_base.isoformat(),
            reference_timezone=timezone_name,
            normalized_start=normalized.isoformat(),
            normalized_end=normalized.isoformat(),
        ))
    return tuple(refs)
