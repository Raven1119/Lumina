from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    include_source_context: bool = False
    # UTF-8 bound for reliable-v1; old read profiles retain their contract.
    max_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.max_bytes is not None and (type(self.max_bytes) is not int or self.max_bytes < 1):
            raise ValueError("max_bytes must be a positive integer or None")
        if type(self.include_source_context) is not bool:
            raise ValueError("include_source_context must be a bool")
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
class BodyMemoryEvidence(MemoryEvidence):
    """One visible verified paraphrase unit, never an exact source quotation."""

    body_ref: str
    body_unit_ref: str
    source_turn_ids: tuple[str, ...]
    representation: str = "verified_paraphrase"


@dataclass(frozen=True)
class MemoryContext:
    """Bounded Recall result; successful evidence cardinality is zero to K.

    Consumability invariant: ``rendered_text`` contains only authorized,
    safe content, so a non-empty ``rendered_text`` is always consumable.
    ``safe_error_code`` reports a degraded optional channel (for example a
    Cold source supplement); it never marks otherwise rendered content as
    unusable. A failed read returns an empty ``rendered_text`` with a code.
    """

    query: str
    evidence: tuple[MemoryEvidence, ...] = ()
    rendered_text: str = ""
    truncated: bool = False
    safe_error_code: str | None = None


@dataclass(frozen=True)
class PreparedRecall:
    """One bounded read, with exact blocks and private selection dependencies.

    Subsets preserve source facts, labels, ordering and retrieval status. They
    neither retrieve again nor infer semantic identity/occupation support.
    If preparation metadata is unavailable, ``context`` remains usable as the
    original fallback; selection operations raise a stable, safe error.
    """

    context: MemoryContext
    _rendered_blocks: tuple[str, ...] = field(default=(), repr=False)
    _dependencies: tuple[tuple[str, tuple[str, ...]], ...] | None = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if type(self._rendered_blocks) is not tuple:
            raise ValueError("invalid_prepared_recall")
        if self._dependencies is None:
            if self._rendered_blocks:
                raise ValueError("invalid_prepared_recall")
            return
        ids = tuple(item.evidence_id for item in self.context.evidence)
        if (type(self.context.evidence) is not tuple
                or any(type(eid) is not str or not eid for eid in ids)
                or len(set(ids)) != len(ids)
                or len(self._rendered_blocks) != len(ids)
                or any(type(block) is not str for block in self._rendered_blocks)
                or "\n".join(self._rendered_blocks) != self.context.rendered_text
                or type(self._dependencies) is not tuple
                or len(self._dependencies) != len(ids)):
            raise ValueError("invalid_prepared_recall")
        known = set(ids)
        keys = []
        for edge in self._dependencies:
            if (type(edge) is not tuple or len(edge) != 2
                    or type(edge[0]) is not str or edge[0] not in known
                    or type(edge[1]) is not tuple
                    or any(type(dep) is not str or dep not in known for dep in edge[1])
                    or len(set(edge[1])) != len(edge[1])):
                raise ValueError("invalid_prepared_recall")
            keys.append(edge[0])
        if tuple(keys) != ids:
            raise ValueError("invalid_prepared_recall")

    @property
    def selection_items(self) -> tuple[tuple[str, str], ...]:
        if self._dependencies is None:
            raise ValueError("prepared_recall_unavailable")
        return tuple((item.evidence_id, block)
                     for item, block in zip(self.context.evidence, self._rendered_blocks))

    def subset(self, evidence_ids: tuple[str, ...]) -> MemoryContext:
        if self._dependencies is None:
            raise ValueError("prepared_recall_unavailable")
        dependencies = dict(self._dependencies)
        if (type(evidence_ids) is not tuple
                or any(type(eid) is not str or eid not in dependencies for eid in evidence_ids)
                or len(set(evidence_ids)) != len(evidence_ids)):
            raise ValueError("invalid_recall_selection")
        wanted = set(evidence_ids)
        pending = list(evidence_ids)
        while pending:
            for dependency in dependencies[pending.pop()]:
                if dependency not in wanted:
                    wanted.add(dependency)
                    pending.append(dependency)
        selected = tuple((item, block) for item, block in
                         zip(self.context.evidence, self._rendered_blocks)
                         if item.evidence_id in wanted)
        return replace(self.context, evidence=tuple(item for item, _ in selected),
                       rendered_text="\n".join(block for _, block in selected))


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


@dataclass(frozen=True)
class SourceExcerpt:
    """An exact source range, not an extracted or verified world fact."""

    evidence_id: str
    text: str
    provenance: SourceProvenance
    turn_index: int
    source_start: int
    source_end: int
    turn_length: int


@dataclass(frozen=True)
class SourceMemoryContext:
    """Explicit raw dialogue view, distinct from ordinary fact evidence."""

    query: str
    evidence: tuple[SourceExcerpt, ...] = ()
    rendered_text: str = ""
    truncated: bool = False
    safe_error_code: str | None = None


@dataclass(frozen=True)
class AssociativeSelection:
    """The representation actually sent for a selected Fact.

    `sources` (reliable-v1) replaces the Fact text: the Fact DTO is retained
    for traceability only. `fact+sources` (reliable-v2) supplements the
    always-visible canonical body. Source IDs name only visible exact ranges.
    """

    evidence_id: str
    channel: Literal["direct", "associated"]
    visible_representation: Literal["fact", "sources", "fact+sources"]
    source_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssociativeMemoryContext:
    """Fact activation and optional Cold excerpts share one character budget.

    Activation scores, graph handles and paths remain private to the adapter.
    The same consumability invariant as ``MemoryContext`` applies: a
    non-empty ``rendered_text`` is consumable even when ``safe_error_code``
    reports a degraded optional source channel.
    """

    facts: MemoryContext
    sources: SourceMemoryContext
    rendered_text: str = ""
    truncated: bool = False
    safe_error_code: str | None = None
    selections: tuple[AssociativeSelection, ...] = ()


@dataclass(frozen=True)
class SourceRangeReference:
    """Original coordinates accepted by SourceReader.read, not an episode ID."""

    segment_id: str
    start_turn: int
    end_turn: int
    start_char: int = 0
    end_char: int | None = None


@dataclass(frozen=True)
class SourceExperience:
    """A positional dialogue view; association and world truth remain judgments."""

    reference: SourceRangeReference
    evidence: tuple[SourceExcerpt, ...]
    rendered_text: str
    truncated: bool


@dataclass(frozen=True)
class ExperienceContext:
    query: str
    experiences: tuple[SourceExperience, ...] = ()
    rendered_text: str = ""
    truncated: bool = False
    safe_error_code: str | None = None

    @property
    def evidence(self) -> tuple[SourceExcerpt, ...]:
        return tuple(item for view in self.experiences for item in view.evidence)
