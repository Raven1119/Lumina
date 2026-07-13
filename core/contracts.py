"""Minimal contracts for the Cold Draft chat MVP."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict


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


class MemoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class MessageRuntimeResult:
    response: ChatResponse
    recent_context: list[dict[str, str]] = field(default_factory=list)
    events: tuple[str, ...] = ()
