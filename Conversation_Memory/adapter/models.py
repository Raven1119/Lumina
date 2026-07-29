from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Literal


@dataclass(frozen=True)
class ColdDraftTurn:
    turn_id: str
    role: str
    content: str
    timestamp: datetime
    source_timezone: str
    timezone_source: str


@dataclass(frozen=True)
class ColdDraftSegment:
    segment_id: str
    conversation_id: str
    state: str
    turns: tuple[ColdDraftTurn, ...]
    created_at: datetime
    source_timezone: str
    schema_version: str


@dataclass(frozen=True)
class NormalizedTemporalReference:
    original_expression: str
    reference_timestamp: str
    reference_timezone: str
    normalized_start: str
    normalized_end: str
    normalization_method: str = "deterministic_relative_v1"
    normalization_confidence: float = 1.0
    language: Literal["en", "zh", "und"] = "en"

@dataclass(frozen=True)
class SourceProvenance:
    segment_id: str
    conversation_id: str
    turn_id: str
    source_timestamp: str
    source_timezone: str
    ingestion_version: str
    timezone_source: str = "legacy_segment_fallback"


@dataclass(frozen=True)
class IngestionResult:
    segment_id: str
    ingestion_version: str
    status: str
    memory_ids: tuple[str, ...] = ()
    already_ingested: bool = False
    retryable: bool = False
    safe_error_code: str | None = None


@dataclass(frozen=True)
class RecallPolicy:
    top_k: int = 5
    max_chars: int = 2000
    max_evidence_items: int = 5
    max_graph_depth: int = 5
    max_nodes: int = 100
    intent: Literal["GENERAL", "WHY", "WHEN", "ENTITY"] | None = None
    temporal_window: tuple[datetime, datetime] | None = None
    beam_width: int | None = None
    drop_threshold: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "top_k",
            "max_chars",
            "max_evidence_items",
            "max_nodes",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.max_graph_depth < 0:
            raise ValueError("max_graph_depth must be non-negative")
        if self.intent is not None and (
            not isinstance(self.intent, str)
            or self.intent not in {"GENERAL", "WHY", "WHEN", "ENTITY"}
        ):
            raise ValueError(
                "intent must be one of GENERAL, WHY, WHEN, ENTITY, or None"
            )
        if self.temporal_window is not None:
            if (
                not isinstance(self.temporal_window, tuple)
                or len(self.temporal_window) != 2
                or not all(
                    isinstance(value, datetime)
                    and value.tzinfo is not None
                    and value.utcoffset() is not None
                    for value in self.temporal_window
                )
            ):
                raise ValueError(
                    "temporal_window must be a pair of aware datetimes"
                )
            start_utc = self.temporal_window[0].astimezone(timezone.utc)
            end_utc = self.temporal_window[1].astimezone(timezone.utc)
            if start_utc >= end_utc:
                raise ValueError("temporal_window start must be before end")
        if self.beam_width is not None and (
            isinstance(self.beam_width, bool)
            or not isinstance(self.beam_width, int)
            or self.beam_width < 1
        ):
            raise ValueError("beam_width must be a positive integer or None")
        if self.drop_threshold is not None:
            if isinstance(self.drop_threshold, bool) or not isinstance(
                self.drop_threshold, (int, float)
            ):
                raise ValueError(
                    "drop_threshold must be a finite number between 0 and 1, or None"
                )
            try:
                drop_threshold = float(self.drop_threshold)
            except (OverflowError, ValueError):
                raise ValueError(
                    "drop_threshold must be a finite number between 0 and 1, or None"
                ) from None
            if not isfinite(drop_threshold) or not 0.0 <= drop_threshold <= 1.0:
                raise ValueError(
                    "drop_threshold must be a finite number between 0 and 1, or None"
                )


@dataclass(frozen=True)
class MemoryEvidence:
    evidence_id: str
    text: str
    timestamp: str | None
    provenance: SourceProvenance


@dataclass(frozen=True)
class MemoryContext:
    query: str
    evidence: tuple[MemoryEvidence, ...] = ()
    rendered_text: str = ""
    truncated: bool = False
    safe_error_code: str | None = None


@dataclass(frozen=True)
class BackendCandidate:
    text: str
    timestamp: str | None
    score: float | None
    metadata: dict[str, Any] = field(default_factory=dict)
