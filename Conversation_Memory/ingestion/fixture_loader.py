from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from adapter.models import ColdDraftSegment, ColdDraftTurn

SUPPORTED_SCHEMAS = frozenset({"1", "2"})
ALLOWED_ROLES = frozenset({"user", "assistant"})
_OFFSET = re.compile(r"^[+-](?:0\d|1\d|2[0-3]):[0-5]\d$")


class SegmentValidationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _required_text(data: dict[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SegmentValidationError(f"invalid_{name}")
    return value.strip()


def _timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SegmentValidationError(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SegmentValidationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SegmentValidationError("timestamp_timezone_required")
    return parsed


def parse_segment(data: Any) -> ColdDraftSegment:
    if not isinstance(data, dict):
        raise SegmentValidationError("invalid_segment")
    schema = _required_text(data, "schema_version")
    if schema not in SUPPORTED_SCHEMAS:
        raise SegmentValidationError("unsupported_schema_version")
    state = _required_text(data, "state")
    if state != "pending_digest":
        raise SegmentValidationError("segment_not_pending")
    raw_turns = data.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise SegmentValidationError("invalid_turns")
    source_timezone = _required_text(data, "source_timezone")
    if not _OFFSET.fullmatch(source_timezone):
        try:
            ZoneInfo(source_timezone)
        except ZoneInfoNotFoundError as exc:
            raise SegmentValidationError("invalid_source_timezone") from exc
    turns = []
    for raw in raw_turns:
        if not isinstance(raw, dict):
            raise SegmentValidationError("invalid_turn")
        role = _required_text(raw, "role")
        if role not in ALLOWED_ROLES:
            raise SegmentValidationError("invalid_role")
        turn_timezone = raw.get("source_timezone", source_timezone)
        if not isinstance(turn_timezone, str) or not turn_timezone.strip():
            raise SegmentValidationError("invalid_source_timezone")
        if not _OFFSET.fullmatch(turn_timezone):
            try:
                ZoneInfo(turn_timezone)
            except ZoneInfoNotFoundError as exc:
                raise SegmentValidationError("invalid_source_timezone") from exc
        timezone_source = raw.get(
            "timezone_source",
            "legacy_segment_fallback" if schema == "1" else None,
        )
        if timezone_source not in {
            "client",
            "configured_default",
            "legacy_segment_fallback",
        }:
            raise SegmentValidationError("invalid_timezone_source")
        turns.append(ColdDraftTurn(
            turn_id=_required_text(raw, "turn_id"),
            role=role,
            content=_required_text(raw, "content"),
            timestamp=_timestamp(raw.get("timestamp"), "invalid_turn_timestamp"),
            source_timezone=turn_timezone,
            timezone_source=timezone_source,
        ))
    return ColdDraftSegment(
        segment_id=_required_text(data, "segment_id"),
        conversation_id=_required_text(data, "conversation_id"),
        state=state,
        turns=tuple(turns),
        created_at=_timestamp(data.get("created_at"), "invalid_created_at"),
        source_timezone=source_timezone,
        schema_version=schema,
    )


def load_fixture(path: str | Path) -> ColdDraftSegment:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SegmentValidationError("fixture_unreadable") from exc
    return parse_segment(raw)
