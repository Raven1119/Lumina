"""Lumina-owned contracts for the Cold Draft chat MVP."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, model_validator


ModelResponseType = Literal["mock", "model", "fallback"]
ChatPhase = Literal["mock_chat", "model_chat"]


class StatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["lumina"]
    status: Literal["ok"]
    mode: Literal["mock", "model"]
    draft_enabled: Literal[True] = True


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str | None = None
    text: str | None = None
    client_timezone: str | None = None


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ModelResponseType
    text: str


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["lumina"]
    status: Literal["ok"]
    phase: ChatPhase
    message_consumed: bool
    response: AssistantResponse


TimezoneSource = Literal[
    "client",
    "configured_default",
    "legacy_segment_fallback",
]


class DraftTurn(BaseModel):
    """One Hot/Cold Draft turn, including native V2 provenance when present.

    A role/text-only instance represents a legacy turn read from an existing
    JSONL file. New production writes must use all four provenance fields.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str
    turn_id: str | None = None
    created_at: datetime | None = None
    source_timezone: str | None = None
    timezone_source: TimezoneSource | None = None

    @model_validator(mode="after")
    def validate_provenance(self) -> "DraftTurn":
        values = (
            self.turn_id,
            self.created_at,
            self.source_timezone,
            self.timezone_source,
        )
        if all(value is None for value in values):
            return self
        if any(value is None for value in values):
            raise ValueError("incomplete turn provenance")
        if not self.turn_id.strip():
            raise ValueError("turn_id is required")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        try:
            ZoneInfo(self.source_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("source_timezone must be a valid IANA timezone") from exc
        return self

    @property
    def has_native_provenance(self) -> bool:
        return self.turn_id is not None

    def storage_turn(self) -> dict[str, str]:
        record = {"role": self.role, "text": self.text}
        if not self.has_native_provenance:
            return record
        created_at = self.created_at.astimezone(UTC)
        record.update({
            "turn_id": self.turn_id,
            "created_at": created_at.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
            "source_timezone": self.source_timezone,
            "timezone_source": self.timezone_source,
        })
        return record


# Compatibility name used by the original Draft context boundary.
MemoryTurn = DraftTurn


@dataclass(frozen=True)
class MessageRuntimeResult:
    response: ChatResponse
    recent_context: list[dict[str, str]] = field(default_factory=list)
    events: tuple[str, ...] = ()
