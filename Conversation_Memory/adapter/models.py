from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any


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

    @property
    def base_timestamp(self) -> str:
        return self.reference_timestamp

    @property
    def normalized_timestamp(self) -> str:
        return self.normalized_start


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
    min_relevance: float | None = None
    max_relevance_candidates: int = 20

    def __post_init__(self) -> None:
        for name in (
            "top_k",
            "max_chars",
            "max_evidence_items",
            "max_graph_depth",
            "max_nodes",
            "max_relevance_candidates",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.max_relevance_candidates > 20:
            raise ValueError("max_relevance_candidates must be at most 20")
        if self.min_relevance is not None:
            if isinstance(self.min_relevance, bool) or not isinstance(
                self.min_relevance, (int, float)
            ):
                raise ValueError("min_relevance must be a finite number or None")
            value = float(self.min_relevance)
            if not math.isfinite(value):
                raise ValueError("min_relevance must be finite")
            if not -1.0 <= value <= 1.0:
                raise ValueError("min_relevance must be between -1.0 and 1.0")


@dataclass(frozen=True)
class MemoryEvidence:
    evidence_id: str
    text: str
    timestamp: str | None
    provenance: SourceProvenance
    relevance_score: float | None = None


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
    query_embedding: tuple[float, ...] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    evidence_embedding: tuple[float, ...] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class ScoredRecallCandidate:
    candidate: BackendCandidate
    relevance_score: float


@dataclass(frozen=True)
class RelevanceScoreResult:
    scored_candidates: tuple[ScoredRecallCandidate, ...] = ()
    candidates_scored: int = 0
    safe_error_code: str | None = None
