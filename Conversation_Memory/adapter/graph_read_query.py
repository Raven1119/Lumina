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
class GraphReadQuery:
    text: str
    clues: tuple[ReadClue, ...] = ()
    require_all: bool = False
    relations: tuple[RelationConstraint, ...] = ()

    def __post_init__(self):
        _text(self.text)
        if type(self.require_all) is not bool:
            raise ValueError("graph_read_require_all_invalid")
        for values, cls in ((self.clues, ReadClue), (self.relations, RelationConstraint)):
            if (not isinstance(values, tuple) or len(values) > _LIMIT
                    or any(not isinstance(item, cls) for item in values)):
                raise ValueError("graph_read_conditions_invalid")


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
