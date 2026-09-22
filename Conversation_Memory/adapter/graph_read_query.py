"""Bounded request-only clues and conservative persisted-role qualification.

Natural input supplies indexed name clues, never inferred syntax. Diagnostics
are private selection information: neither graph refs nor these statuses are
claims to inject into a public MemoryContext.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice

from .controlled_relation import ControlledRelationResolver, _normalize
from .models import BackendCandidate, SourceProvenance

_LIMIT = 5
_RESOLVER = ControlledRelationResolver()
QUERY_MAX_RECENT_TURNS = 12
QUERY_MAX_RECENT_CHARS = 6000
QUERY_MAX_MESSAGE_CHARS = 8000


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("graph_read_text_invalid")


def _refs(value):
    if (not isinstance(value, tuple) or len(value) > _LIMIT
            or any(not isinstance(ref, str) or not ref.strip() for ref in value)):
        raise ValueError("graph_read_refs_invalid")


@dataclass(frozen=True)
class ReadClue:
    """A single concept; entity_refs are OR alternatives, never AND clauses."""
    text: str
    entity_refs: tuple[str, ...] = ()

    def __post_init__(self):
        _text(self.text)
        _refs(self.entity_refs)


@dataclass(frozen=True)
class RelationConstraint:
    """One actual Fact must contain this predicate and any requested roles."""
    relation: str | None = None
    subject_refs: tuple[str, ...] = ()
    object_refs: tuple[str, ...] = ()

    def __post_init__(self):
        if self.relation is not None:
            _text(self.relation)
        _refs(self.subject_refs)
        _refs(self.object_refs)
        if self.relation is None and not (self.subject_refs or self.object_refs):
            raise ValueError("graph_read_empty_relation_constraint")


@dataclass(frozen=True)
class QueryContextTurn:
    role: str
    text: str

    def __post_init__(self):
        if self.role not in {"user", "assistant", "summary"} or not isinstance(self.text, str):
            raise ValueError("graph_query_context_invalid")


@dataclass(frozen=True)
class QuerySource:
    """Exact source location; -1 is the current message, others are near turns."""
    index: int
    quote: str
    occurrence: int
    start: int
    end: int

    def __post_init__(self):
        if (type(self.index) is not int or not -1 <= self.index < QUERY_MAX_RECENT_TURNS
                or not isinstance(self.quote, str) or not 1 <= len(self.quote) <= 256
                or type(self.occurrence) is not int or not 0 <= self.occurrence < 16
                or type(self.start) is not int or type(self.end) is not int
                or not 0 <= self.start < self.end or self.end - self.start != len(self.quote)):
            raise ValueError("graph_query_source_invalid")


def _query_sources(sources):
    if (not isinstance(sources, tuple) or not 1 <= len(sources) <= 2
            or any(not isinstance(item, QuerySource) for item in sources)):
        raise ValueError("graph_query_sources_invalid")


@dataclass(frozen=True)
class QueryClue:
    id: str
    text: str
    kind: str
    sources: tuple[QuerySource, ...]

    def __post_init__(self):
        if (self.id not in {"c1", "c2", "c3"} or not isinstance(self.text, str)
                or not self.text.strip() or len(self.text) > 128
                or self.kind not in {"name", "current_user", "topic", "literal"}):
            raise ValueError("graph_query_clue_invalid")
        _query_sources(self.sources)
        if not any(self.text in source.quote for source in self.sources):
            raise ValueError("graph_query_clue_not_in_source")


@dataclass(frozen=True)
class QueryRelation:
    subject: str
    predicate: str
    object: str
    sources: tuple[QuerySource, ...]

    def __post_init__(self):
        if (self.subject not in {"c1", "c2", "c3", "?entity"}
                or self.object not in {"c1", "c2", "c3", "?entity", "?value"}
                or not isinstance(self.predicate, str) or not self.predicate.strip()
                or len(self.predicate) > 48):
            raise ValueError("graph_query_relation_invalid")
        _query_sources(self.sources)


@dataclass(frozen=True)
class QueryIntent:
    mode: str
    clues: tuple[QueryClue, ...] = ()
    relations: tuple[QueryRelation, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __post_init__(self):
        if self.mode not in {"open", "precise"}:
            raise ValueError("graph_query_mode_invalid")
        for values, cls, limit in ((self.clues, QueryClue, 3), (self.relations, QueryRelation, 2)):
            if (not isinstance(values, tuple) or len(values) > limit
                    or any(not isinstance(item, cls) for item in values)):
                raise ValueError("graph_query_intent_limit")
        if (not isinstance(self.unresolved, tuple) or len(self.unresolved) > 4
                or any(not isinstance(item, str) or not item.strip() or len(item) > 96
                       for item in self.unresolved)):
            raise ValueError("graph_query_unresolved_invalid")
        ids = {item.id for item in self.clues}
        if len(ids) != len(self.clues):
            raise ValueError("graph_query_duplicate_clue")
        kinds = {item.id: item.kind for item in self.clues}
        for relation in self.relations:
            if any(value.startswith("c") and value not in ids for value in (relation.subject, relation.object)):
                raise ValueError("graph_query_unknown_clue")
            if kinds.get(relation.subject) == "literal":
                raise ValueError("graph_query_literal_subject")
        if self.mode == "precise" and not self.relations:
            raise ValueError("graph_query_precise_needs_relation")
        if self.mode == "open" and self.relations:
            raise ValueError("graph_query_open_has_relation")
        if sum(relation.object == "?value" for relation in self.relations) > 1:
            raise ValueError("graph_query_multiple_unknown_values")


@dataclass(frozen=True)
class GraphReadQuery:
    text: str
    clues: tuple[ReadClue, ...] = ()
    require_all: bool = False
    relations: tuple[RelationConstraint, ...] = ()
    intent: QueryIntent | None = None
    recent_context: tuple[QueryContextTurn, ...] = ()

    def __post_init__(self):
        _text(self.text)
        if type(self.require_all) is not bool:
            raise ValueError("graph_read_require_all_invalid")
        for values, cls in ((self.clues, ReadClue), (self.relations, RelationConstraint)):
            if (not isinstance(values, tuple) or len(values) > _LIMIT
                    or any(not isinstance(item, cls) for item in values)):
                raise ValueError("graph_read_conditions_invalid")
        if self.intent is not None and not isinstance(self.intent, QueryIntent):
            raise ValueError("graph_query_intent_invalid")
        if (not isinstance(self.recent_context, tuple)
                or len(self.recent_context) > QUERY_MAX_RECENT_TURNS
                or any(not isinstance(turn, QueryContextTurn) for turn in self.recent_context)
                or sum(len(turn.text) for turn in self.recent_context) > QUERY_MAX_RECENT_CHARS):
            raise ValueError("graph_query_context_limit")


def query_source(index, quote, occurrence, text, recent_context):
    """Locate the requested exact occurrence without trusting model offsets."""
    if (type(index) is not int or not -1 <= index < len(recent_context)
            or not isinstance(quote, str) or not 1 <= len(quote) <= 256
            or type(occurrence) is not int or not 0 <= occurrence < 16):
        raise ValueError("graph_query_source_invalid")
    source = text if index == -1 else recent_context[index].text
    start = -1
    for _ in range(occurrence + 1):
        start = source.find(quote, start + 1)
        if start < 0:
            raise ValueError("graph_query_quote_missing")
    return QuerySource(index, quote, occurrence, start, start + len(quote))


def validate_query_intent(query: GraphReadQuery) -> QueryIntent | None:
    """Recheck source/shape at the Memory boundary; no semantic entailment claim."""
    query.__post_init__()
    intent = query.intent
    if intent is None:
        return None
    if query.clues or query.relations or query.require_all:
        raise ValueError("graph_query_mixed_contracts")
    intent.__post_init__()
    for turn in query.recent_context:
        turn.__post_init__()
    for item in (*intent.clues, *intent.relations):
        item.__post_init__()
        for source in item.sources:
            source.__post_init__()
            actual = query_source(source.index, source.quote, source.occurrence,
                                  query.text, query.recent_context)
            if source != actual:
                raise ValueError("graph_query_source_mismatch")
        if (isinstance(item, QueryClue) and item.kind == "current_user"
                and not any(source.index == -1 or query.recent_context[source.index].role == "user"
                            for source in item.sources)):
            raise ValueError("graph_query_current_user_source_role")
    return intent


@dataclass(frozen=True)
class CompiledGraphReadQuery:
    request: GraphReadQuery
    diagnostics: dict = field(default_factory=dict, repr=False)


def _ref_diagnostics(request, backend, diagnostics):
    refs = tuple(dict.fromkeys(
        ref for values in (
            *(clue.entity_refs for clue in request.clues),
            *(part for relation in request.relations
              for part in (relation.subject_refs, relation.object_refs)),
        ) for ref in values
    ))
    diagnostics["requested_entity_refs"] = refs
    if not refs:
        return
    try:
        view = backend.first_hit_view()
        diagnostics["unknown_entity_refs"] = tuple(
            ref for ref in refs if not view.eligible(f"entity:{ref.casefold()}"))
    except Exception:
        diagnostics["entity_reference_validation"] = "unavailable"


def compile_graph_read_query(query: str | GraphReadQuery, backend) -> CompiledGraphReadQuery:
    """Retain full text and bound indexed surface lookup without model calls.

    Two named surfaces are two observable clues, not an inferred conjunction.
    Six results are requested only to detect a five-result truncation. Missing
    capacity stays visible; it cannot establish the absence of a memory.
    """
    if isinstance(query, GraphReadQuery):
        diagnostics = {"input_mode": "explicit", "selection_mode": "constrained"
                       if query.relations or query.require_all else "open",
                       "parse_gaps": ()}
        _ref_diagnostics(query, backend, diagnostics)
        return CompiledGraphReadQuery(query, diagnostics)
    _text(query)
    diagnostics = {"input_mode": "natural", "selection_mode": "open",
                   "parse_gaps": ("relation_direction_unresolved", "combination_unresolved")}
    clues = []
    try:
        matching = getattr(backend, "_matching_surfaces", None)
        if callable(matching):
            ensure = getattr(backend, "_ensure_indexes", None)
            if callable(ensure):
                ensure()
            surfaces = tuple(islice(matching(query), _LIMIT + 1))
            diagnostics["clue_limit_exceeded"] = len(surfaces) > _LIMIT
            truncated = []
            for surface in surfaces[:_LIMIT]:
                candidates = backend.find_entity_candidates(surface, limit=_LIMIT + 1)
                refs = tuple(dict.fromkeys(item.entity_ref for item in candidates))
                if len(refs) > _LIMIT:
                    truncated.append(surface)
                clues.append(ReadClue(surface, refs[:_LIMIT]))
            diagnostics["truncated_entity_alternatives"] = tuple(truncated)
        else:
            refs = tuple(dict.fromkeys(backend.resolve_target_entity_refs(query, limit=_LIMIT + 1)))
            if refs:
                clues.append(ReadClue(query, refs[:_LIMIT]))
            diagnostics["surface_grouping_unavailable"] = True
            diagnostics["entity_alternatives_truncated"] = len(refs) > _LIMIT
    except Exception:
        # Independently resolved clues survive; unavailable lookup never turns
        # missing conditions into a successful precise interpretation.
        diagnostics["entity_lookup_unavailable"] = True
    request = GraphReadQuery(query, tuple(clues))
    diagnostics["unresolved_clue_indices"] = tuple(
        i for i, clue in enumerate(clues) if not clue.entity_refs)
    _ref_diagnostics(request, backend, diagnostics)
    return CompiledGraphReadQuery(request, diagnostics)


@dataclass(frozen=True)
class FactQualification:
    # Each index is one obligation. Different Facts may satisfy different
    # indices; no predicate or role is combined across Fact boundaries here.
    statuses: tuple[str, ...]
    diagnostics: dict = field(default_factory=dict, repr=False)

    @property
    def matched_indices(self):
        return tuple(i for i, status in enumerate(self.statuses) if status == "matched")

    @property
    def unknown_indices(self):
        return tuple(i for i, status in enumerate(self.statuses) if status == "unknown")


def _source_status(candidate):
    """Check stored source shape; exact Cold text remains the owner's check."""
    meta = candidate.metadata
    try:
        provenance = SourceProvenance(**meta["provenance"])
        if any(not isinstance(getattr(provenance, name), str)
               or not getattr(provenance, name).strip() for name in (
                   "segment_id", "conversation_id", "turn_id", "source_timestamp",
                   "source_timezone", "ingestion_version")):
            return "provenance_invalid"
    except (TypeError, ValueError, KeyError):
        return "provenance_invalid"
    refs = meta.get("source_refs")
    if refs is None:
        refs = ({"turn_id": provenance.turn_id, "source_start": meta.get("source_start"),
                 "source_end": meta.get("source_end"), "source_role": provenance.source_role},)
    if not isinstance(refs, (tuple, list)) or not refs or len(refs) > 64:
        return "source_refs_invalid"
    for ref in refs:
        if not isinstance(ref, dict):
            return "source_refs_invalid"
        start, end = ref.get("source_start"), ref.get("source_end")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end
                or not isinstance(ref.get("turn_id"), str) or not ref["turn_id"].strip()
                or ref.get("source_role") not in {"user", "assistant"}):
            return "source_refs_invalid"
        if "supporting_span" in ref and (not isinstance(ref["supporting_span"], str)
                                         or len(ref["supporting_span"]) != end - start):
            return "source_refs_invalid"
    return "stored_source_ranges"


def _relation_status(wanted, stored):
    if wanted is None:
        return "matched"  # Caller supplied a role-only condition.
    if not isinstance(stored, str) or not stored.strip():
        return "unknown"
    if _normalize(wanted) == _normalize(stored):
        return "matched"
    # Reuse the controlled vocabulary on predicate surfaces only. Free text
    # surrounding a predicate must not supply a guessed relation or direction.
    left, right = _RESOLVER.resolve_query_relations((wanted, stored))
    if left is None or right is None:
        return "unknown"
    return "matched" if left == right else "unmatched"


def _role_status(metadata, node_id, alternatives, role, view):
    if not alternatives:
        return "matched", "not_required"
    actual = metadata.get(role + "_entity_ref")
    if not isinstance(actual, str) or not actual.strip():
        return "unknown", "binding_missing"
    try:
        roles = view.entity_roles(node_id, actual)
    except Exception:
        return "unknown", "role_view_unavailable"
    if role not in roles:
        return "unknown", "persisted_role_missing"
    return ("matched", "persisted_role") if actual in alternatives else ("unmatched", "different_identity")


def qualify_fact(candidate: BackendCandidate, node_id: str, query: GraphReadQuery,
                 view) -> FactQualification:
    """Qualify each requested tuple using only one actual stored Fact.

    An unknown role can still be used for open association; it is never counted
    as a fulfilled explicit obligation. Qualification does not alter traversal.
    """
    source = _source_status(candidate)
    statuses, details = [], []
    for condition in query.relations:
        relation = _relation_status(condition.relation, candidate.metadata.get("relation"))
        subject, subject_reason = _role_status(candidate.metadata, node_id, condition.subject_refs, "subject", view)
        obj, object_reason = _role_status(candidate.metadata, node_id, condition.object_refs, "object", view)
        parts = (relation, subject, obj)
        status = ("unknown" if source != "stored_source_ranges" else
                  "unmatched" if "unmatched" in parts else
                  "unknown" if "unknown" in parts else "matched")
        statuses.append(status)
        details.append({"relation": relation, "subject": subject, "object": obj,
                        "subject_reason": subject_reason, "object_reason": object_reason})
    return FactQualification(tuple(statuses), {"source_status": source,
                                              "source_check_scope": "stored_metadata_only",
                                              "constraints": tuple(details)})
