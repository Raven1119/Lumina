"""Lumina-owned contracts for the Cold Draft chat MVP."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


ModelResponseType = Literal["mock", "model", "fallback", "none", "error", "pending"]
ChatPhase = Literal["mock_chat", "model_chat"]


class CompactionStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    running: bool
    summary_truncated: bool = False


class MemoryStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_count: int
    pattern_count: int
    embedding_available: bool


class LuminaStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    states: list[str]
    focus: str
    thinking: bool
    executing: bool = False
    helpers: list[dict] = Field(default_factory=list)


class StatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["lumina"]
    status: Literal["ok"]
    mode: Literal["mock", "model"]
    draft_enabled: Literal[True] = True
    recall_enabled: bool
    compaction: CompactionStatusResponse
    dream: "DreamStatusResponse"
    memory: MemoryStatusResponse | None = None
    lumina: LuminaStatusResponse | None = None
    frontend_poll_interval_s: int = 5
    dead_letters: int = 0


class DreamStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    running: bool
    unintegrated_cold_turns: int
    last_at: str | None = None
    last_result: str | None = None
    auto_paused: bool = False


class DreamRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    window_turns: int
    dream_id: str | None = None
    patterns: int = 0


class MemoryItemResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    text: str
    time_label: str
    pi: float
    pattern: bool
    source_count: int


class DreamLogResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    at: str
    status: str
    n_ops: int
    n_rejected: int


class MemoryListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    memory_count: int
    pattern_count: int
    memories: list[MemoryItemResponse]
    patterns: list[MemoryItemResponse]
    dream_log: list[DreamLogResponse]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str | None = None
    text: str | None = None
    client_timezone: str | None = None


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ModelResponseType
    text: str


class ChatCompactionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_needed", "completed", "failed"]
    archived_turns: int
    summary_updated: bool


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["lumina"]
    status: Literal["ok"]
    phase: ChatPhase
    message_consumed: bool
    response: AssistantResponse
    compaction: ChatCompactionResponse


class HistoryTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    role: Literal["user", "assistant"]
    content: str
    timestamp: str


class HistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turns: list[HistoryTurnResponse]
    has_more: bool
    next_before: str | None


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
