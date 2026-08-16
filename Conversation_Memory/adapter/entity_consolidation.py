"""Deterministic ordinary-subject EntityRef binding for grounded writes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .grounded_formation import GroundedMemoryUnit


class EntityConsolidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class EntityCandidate:
    entity_ref: str
    canonical_surface: str


@dataclass(frozen=True)
class EntityBinding:
    unit_id: str
    entity_ref: str
    canonical_surface: str


@dataclass(frozen=True)
class MentionEntityBinding:
    unit_id: str
    entity_ref: str
    canonical_surface: str
    turn_id: str
    supporting_span: str


def stable_entity_ref(unit_id: str) -> str:
    """Allocate a retry-stable ref from the already durable unit identity."""
    if not isinstance(unit_id, str) or not unit_id:
        raise EntityConsolidationError("entity_unit_id_invalid")
    return "E_" + hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:12]


def stable_mention_entity_ref(unit_id: str, surface: str) -> str:
    """Allocate a retry-stable mention ref from unit identity + exact surface."""
    if not isinstance(surface, str) or not surface:
        raise EntityConsolidationError("entity_surface_invalid")
    return stable_entity_ref(f"{unit_id}:{surface}")


def resolve_entity_binding(
    unit: GroundedMemoryUnit,
    candidates: tuple[EntityCandidate, ...],
) -> EntityBinding | None:
    """Bind only a uniquely matched canonical surface; ambiguity fails open."""
    canonical_surface = unit.subject.strip()
    if not canonical_surface:
        raise EntityConsolidationError("entity_surface_invalid")
    matches = tuple(
        candidate
        for candidate in candidates
        if candidate.canonical_surface == canonical_surface
    )
    if len(matches) > 1:
        return None
    return EntityBinding(
        unit_id=unit.id,
        entity_ref=(matches[0].entity_ref if matches else stable_entity_ref(unit.id)),
        canonical_surface=canonical_surface,
    )


def resolve_mention_entity_binding(
    unit: GroundedMemoryUnit,
    *,
    surface: str,
    turn_id: str,
    supporting_span: str,
    candidates: tuple[EntityCandidate, ...],
) -> MentionEntityBinding | None:
    """Bind one grounded mention surface; ambiguity fails open per mention."""
    if (
        not isinstance(surface, str)
        or not surface
        or surface not in supporting_span
    ):
        raise EntityConsolidationError("entity_surface_invalid")
    matches = tuple(
        candidate
        for candidate in candidates
        if candidate.canonical_surface == surface
    )
    if len(matches) > 1:
        return None
    return MentionEntityBinding(
        unit_id=unit.id,
        entity_ref=(
            matches[0].entity_ref
            if matches
            else stable_mention_entity_ref(unit.id, surface)
        ),
        canonical_surface=surface,
        turn_id=turn_id,
        supporting_span=supporting_span,
    )
