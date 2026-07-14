"""Small injectable primitives for creating native Draft Turn V2 records."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.contracts import DraftTurn, TimezoneSource


class Clock(Protocol):
    def now(self) -> datetime:
        ...


class TurnIdFactory(Protocol):
    def new_id(self) -> str:
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidTurnIdFactory:
    def new_id(self) -> str:
        return uuid.uuid4().hex


def configured_timezone(value: str | None) -> str:
    candidate = value.strip() if isinstance(value, str) and value.strip() else "UTC"
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return "UTC"
    return candidate


def resolve_source_timezone(
    client_timezone: str | None,
    default_timezone: str,
) -> tuple[str, TimezoneSource]:
    if isinstance(client_timezone, str) and client_timezone.strip():
        candidate = client_timezone.strip()
        try:
            ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            pass
        else:
            return candidate, "client"
    return configured_timezone(default_timezone), "configured_default"


class DraftTurnFactory:
    def __init__(
        self,
        *,
        clock: Clock | None = None,
        id_factory: TurnIdFactory | None = None,
        default_timezone: str = "UTC",
    ) -> None:
        self._clock = clock or SystemClock()
        self._id_factory = id_factory or UuidTurnIdFactory()
        self.default_timezone = configured_timezone(default_timezone)

    def create(
        self,
        *,
        role: str,
        text: str,
        source_timezone: str,
        timezone_source: TimezoneSource,
    ) -> DraftTurn:
        created_at = self._clock.now()
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return DraftTurn(
            turn_id=self._id_factory.new_id(),
            role=role,
            text=text,
            created_at=created_at.astimezone(UTC),
            source_timezone=source_timezone,
            timezone_source=timezone_source,
        )
