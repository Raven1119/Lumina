from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
    source_role: Literal["user", "assistant"]
    source_timestamp: str
    source_timezone: str
    ingestion_version: str
    timezone_source: str = "legacy_segment_fallback"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_role, str)
            or self.source_role not in {"user", "assistant"}
        ):
            raise ValueError("source_role must be user or assistant")


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
    final_min_score: float | None = None
    relation_surfaces: tuple[str, ...] | None = None

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
        if self.relation_surfaces is not None and (
            not isinstance(self.relation_surfaces, tuple)
            or not all(
                isinstance(surface, str) and bool(surface.strip())
                for surface in self.relation_surfaces
            )
        ):
            raise ValueError(
                "relation_surfaces must be a tuple of non-empty strings or None"
            )
        if self.final_min_score is not None:
            if isinstance(self.final_min_score, bool) or not isinstance(
                self.final_min_score, (int, float)
            ):
                raise ValueError(
                    'final_min_score must be a finite number or None'
                )
            try:
                final_min_score = float(self.final_min_score)
            except (OverflowError, ValueError):
                raise ValueError(
                    'final_min_score must be a finite number or None'
                ) from None
            if not isfinite(final_min_score):
                raise ValueError(
                    'final_min_score must be a finite number or None'
                )


@dataclass(frozen=True)
class MemoryEvidence:
    evidence_id: str
    text: str
    timestamp: str | None
    provenance: SourceProvenance


@dataclass(frozen=True)
class MemoryContext:
    """Bounded Recall result; successful evidence cardinality is zero to K."""

    query: str
    evidence: tuple[MemoryEvidence, ...] = ()
    rendered_text: str = ""
    truncated: bool = False
    safe_error_code: str | None = None


@dataclass(frozen=True)
class EntityMention:
    """A source occurrence, never an assertion about the named object."""

    mention_id: str
    surface: str
    source_start: int
    source_end: int
    provenance: SourceProvenance
    resolved: bool
    ambiguous: bool = False


@dataclass(frozen=True)
class EntityMentionContext:
    query: str
    mentions: tuple[EntityMention, ...] = ()
    truncated: bool = False
    safe_error_code: str | None = None


@dataclass(frozen=True)
class BackendCandidate:
    text: str
    timestamp: str | None
    score: float | None
    metadata: dict[str, Any] = field(default_factory=dict)
