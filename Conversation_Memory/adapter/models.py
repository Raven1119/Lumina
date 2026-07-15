from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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

    def __post_init__(self) -> None:
        for name in (
            "top_k",
            "max_chars",
            "max_evidence_items",
            "max_graph_depth",
            "max_nodes",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")


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
