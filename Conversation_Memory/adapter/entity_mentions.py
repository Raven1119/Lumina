"""Grounded mention candidate extraction and subset-enforced selection.

During grounded-formation ingestion, each validator-accepted unit's source
spans are grounded for entity mentions: one span-level extraction call per
unique ``(turn_id, supporting_span)``, an exact-span gate, and — only when a
span backs more than one unit — one subset-enforced selector call per unit.
The selector may only choose from the grounded candidates; a non-subset output
is a schema failure. Ported from the validated shadow logic; source
experiments: ``docs/experiments/unit_conditioned_entity_mention/RESULT.md``
and ``docs/experiments/grounded_mention_selection/RESULT.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .grounded_formation import FormationModel, GroundedMemoryUnit
from .models import ColdDraftSegment

_MENTION_EXTRACTION_PROMPT = """Extract only entity mentions from the supplied Text.

Speaker: {speaker}
Text: {text}

Return ONLY strict JSON: {{"entities": ["exact source mention", ...]}}.
An entity is a person, organization, or location explicitly named in Text.
Copy each candidate exactly from Text. Return [] for no entity. Do not include
common nouns, dates, numbers, identifiers, aliases, pronouns, EntityRefs,
canonical identities, or any explanation."""

_MENTION_SELECTION_PROMPT = """Select from the Candidate mentions only those that belong to the given Fact.

Speaker: {speaker}
Fact subject: {subject}
Fact relation: {relation}
Fact value: {value}
Fact statement: {statement}
Text: {text}
Candidate mentions: {candidates}

Return ONLY strict JSON: {{"entities": ["candidate mention", ...]}}.
Each returned item MUST be copied exactly from Candidate mentions. Select
only the candidates that participate in THIS Fact (typically the subject and
the value as they appear in Text); never select candidates that belong to
other facts in Text. Return [] when none of the candidates belong to this
Fact. Do not invent, modify, or add mentions; do not include any
explanation."""

_MAX_ATTEMPTS = 2  # one bounded retry, mirroring the Formation failure style


class MentionExtractionError(RuntimeError):
    """A safe mention failure that must leave the Cold segment pending."""

    def __init__(self, code: str = "mention_extraction_failed") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MentionSurface:
    unit_id: str
    surface: str
    turn_id: str
    supporting_span: str


def _validate_mentions_payload(raw: str) -> tuple[str, ...] | None:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"entities"}:
        return None
    entities = payload["entities"]
    if not isinstance(entities, list) or not all(
        isinstance(item, str) for item in entities
    ):
        return None
    return tuple(entities)


def ground_exact_mentions(
    span: str,
    candidates: tuple[str, ...],
) -> tuple[str, ...]:
    """Keep unique candidates that are exact substrings of the span."""
    mentions: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if candidate in span:
            mentions.append(candidate)
    return tuple(mentions)


def extract_span_mentions(
    model: FormationModel,
    *,
    role: str,
    span: str,
) -> tuple[str, ...]:
    """One extraction call with one bounded retry; failures raise."""
    prompt = _MENTION_EXTRACTION_PROMPT.format(speaker=role, text=span)
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            raw = model.generate(
                [], "Extract entity mentions now.", system_prompt=prompt,
            )
        except Exception:
            continue
        candidates = _validate_mentions_payload(raw)
        if candidates is not None:
            return ground_exact_mentions(span, candidates)
    raise MentionExtractionError("mention_extraction_failed")


def select_unit_mentions(
    model: FormationModel,
    *,
    role: str,
    span: str,
    unit: GroundedMemoryUnit,
    candidates: tuple[str, ...],
) -> tuple[str, ...]:
    """One selector call with one bounded retry; subset violation is a schema
    failure. The selector can never generate a new mention."""
    prompt = _MENTION_SELECTION_PROMPT.format(
        speaker=role,
        subject=unit.subject,
        relation=unit.relation,
        value=unit.value,
        statement=unit.text,
        text=span,
        candidates=json.dumps(list(candidates), ensure_ascii=False),
    )
    allowed = set(candidates)
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            raw = model.generate(
                [], "Select mentions now.", system_prompt=prompt,
            )
        except Exception:
            continue
        selected = _validate_mentions_payload(raw)
        if selected is None:
            continue
        unique = tuple(dict.fromkeys(selected))
        if all(item in allowed for item in unique):
            return unique
    raise MentionExtractionError("mention_selection_failed")


def ground_unit_mentions(
    segment: ColdDraftSegment,
    units: tuple[GroundedMemoryUnit, ...],
    model: FormationModel,
) -> tuple[MentionSurface, ...]:
    """Ground mention surfaces for validated units against their source spans.

    Extraction input is always the span text resolved from the segment turns —
    never unit.text and never the Cold store. A span backing exactly one unit
    attaches all grounded mentions without a selector call; a shared span gets
    one subset-enforced selector call per unit.
    """
    turns = {turn.turn_id: turn for turn in segment.turns}
    span_units: dict[tuple[str, str], list[GroundedMemoryUnit]] = {}
    for unit in units:
        for ref in unit.source_refs:
            span_units.setdefault((ref.turn_id, ref.supporting_span), []).append(unit)
    mentions: list[MentionSurface] = []
    for (turn_id, span), backed_units in span_units.items():
        role = turns[turn_id].role
        grounded = extract_span_mentions(model, role=role, span=span)
        if not grounded:
            continue
        if len(backed_units) == 1:
            unit = backed_units[0]
            mentions.extend(
                MentionSurface(unit.id, surface, turn_id, span)
                for surface in grounded
            )
            continue
        for unit in backed_units:
            selected = select_unit_mentions(
                model, role=role, span=span, unit=unit, candidates=grounded,
            )
            mentions.extend(
                MentionSurface(unit.id, surface, turn_id, span)
                for surface in selected
            )
    return tuple(mentions)
