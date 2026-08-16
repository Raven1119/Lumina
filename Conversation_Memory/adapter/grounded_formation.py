"""One-call, source-grounded atomic fact formation for manual Dream."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .models import ColdDraftSegment


FORMATION_VERSION = "grounded-formation-v1"
_MAX_TURNS = 32
_MAX_SOURCE_CHARS = 20_000
_MAX_UNITS = 32
_MAX_FIELD_CHARS = 2_000
_NEGATION = re.compile(
    r"(?:\b(?:no|not|never|without|isn't|wasn't|aren't|weren't|don't|doesn't|didn't)\b|[不没无未非勿莫])",
    re.IGNORECASE,
)
_UNCERTAINTY = re.compile(
    r"(?:\b(?:maybe|perhaps|possibly|probably|likely|uncertain|unsure|might|may|could)\b|也许|可能|大概|或许|不确定)",
    re.IGNORECASE,
)
_DETAIL = re.compile(
    r"(?:[A-Za-z]+(?:-[A-Za-z0-9]+)+|\d+(?:\.\d+)?(?:\s*[A-Za-z]+)?)"
)
_ACCEPTANCE = re.compile(
    r"^(?:yes|correct|right|exactly|that's right|that is right|对|是的|没错|正确)[。.!！?？\s]*$",
    re.IGNORECASE,
)
_EPISTEMIC_REPORT = re.compile(
    r"(?:\b(?:i\s+(?:think|believe|guess|assume|suspect|heard)|according\s+to)\b|"
    r"我(?:觉得|认为|猜|听说))",
    re.IGNORECASE,
)
_ATOMIC_BOUNDARY = re.compile(r"(?<!\d)[.!?。！？;；](?!\d)")
_FORMATION_PROMPT = """Extract only explicit, source-grounded atomic facts from the supplied bounded conversation.
Return strict JSON only: {"units":[...]}. Each unit must contain exactly:
{"text":str,"subject":str,"relation":str,"value":str,"source_refs":[{"turn_id":str,"supporting_span":str}],"referenced_time":str|null}.
Use exact source substrings as supporting_span. Preserve IDs, numbers, dates, units, negation, and uncertainty exactly. Make text self-contained and one fact only. Split unrelated facts. Multi-turn facts may cite multiple turns. Do not invent missing context. An assistant-only assertion is not a user/world fact unless a cited user turn explicitly accepts or corrects it. Do not create an ontology or normalize relation names. Return {"units":[]} when no fact is safely supported."""
_SEMANTIC_EQUIVALENCE_PROMPT = """Given ONLY each candidate and its cited source spans and roles, decide whether the candidate text, subject, relation, and value are fully entailed. Allow faithful translation, paraphrase, grammatical normalization, and speaker-role normalization. Reject any new fact, invented binding, changed value, or added semantics. Return strict JSON only: {"results":[{"supported":true}]} with exactly one boolean result per input candidate in the same order. Do not return reasoning or any other fields."""


class FormationModel(Protocol):
    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str: ...


class FormationError(RuntimeError):
    """A safe formation failure that must leave the Cold segment pending."""

    def __init__(self, code: str = "formation_failed") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceRef:
    turn_id: str
    supporting_span: str


@dataclass(frozen=True)
class GroundedMemoryUnit:
    id: str
    text: str
    subject: str
    relation: str
    value: str
    source_refs: tuple[SourceRef, ...]
    formation_version: str = FORMATION_VERSION
    referenced_time: str | None = None


def form_grounded_memory_units(
    segment: ColdDraftSegment,
    model: FormationModel,
    *,
    semantic_unit_ids: set[str] | None = None,
) -> tuple[GroundedMemoryUnit, ...]:
    """Make one Formation call and at most one narrow semantic fallback call."""

    if (
        len(segment.turns) > _MAX_TURNS
        or sum(len(turn.content) for turn in segment.turns) > _MAX_SOURCE_CHARS
    ):
        raise FormationError("formation_window_too_large")
    payload = {
        "turns": [
            {"turn_id": turn.turn_id, "role": turn.role, "text": turn.content}
            for turn in segment.turns
        ]
    }
    try:
        raw = model.generate(
            [],
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            system_prompt=_FORMATION_PROMPT,
        )
        parsed = json.loads(raw)
    except Exception:
        raise FormationError() from None
    if not isinstance(parsed, dict) or set(parsed) != {"units"}:
        raise FormationError("formation_output_invalid")
    candidates = parsed["units"]
    if not isinstance(candidates, list) or len(candidates) > _MAX_UNITS:
        raise FormationError("formation_output_invalid")

    accepted: dict[str, GroundedMemoryUnit] = {}
    semantic_candidates: list[tuple[Any, GroundedMemoryUnit]] = []
    for candidate in candidates:
        unit = _validate_candidate(candidate, segment)
        if unit is not None:
            accepted.setdefault(unit.id, unit)
            continue
        fallback_unit = _validate_candidate(
            candidate, segment, semantic_equivalence=True,
        )
        if fallback_unit is not None:
            semantic_candidates.append((candidate, fallback_unit))
    if semantic_candidates:
        decisions = _semantic_equivalence_decisions(
            model, segment, [candidate for candidate, _unit in semantic_candidates],
        )
        for supported, (_candidate, unit) in zip(decisions, semantic_candidates):
            if supported:
                accepted.setdefault(unit.id, unit)
                if semantic_unit_ids is not None:
                    semantic_unit_ids.add(unit.id)
    turn_order = {turn.turn_id: index for index, turn in enumerate(segment.turns)}
    return tuple(sorted(
        accepted.values(),
        key=lambda unit: (
            min(turn_order[ref.turn_id] for ref in unit.source_refs),
            unit.id,
        ),
    ))


def serialize_grounded_memory_units(
    units: tuple[GroundedMemoryUnit, ...],
) -> list[dict[str, Any]]:
    return [asdict(unit) for unit in units]


def deserialize_grounded_memory_units(
    raw_units: Any,
) -> tuple[GroundedMemoryUnit, ...]:
    if not isinstance(raw_units, list) or len(raw_units) > _MAX_UNITS:
        raise ValueError("formed_units_invalid")
    units: list[GroundedMemoryUnit] = []
    for raw in raw_units:
        if not isinstance(raw, dict) or set(raw) != {
            "id", "text", "subject", "relation", "value", "source_refs",
            "formation_version", "referenced_time",
        }:
            raise ValueError("formed_units_invalid")
        refs = raw["source_refs"]
        if not isinstance(refs, list) or not refs:
            raise ValueError("formed_units_invalid")
        try:
            source_refs = tuple(SourceRef(**ref) for ref in refs)
            unit = GroundedMemoryUnit(
                id=raw["id"],
                text=raw["text"],
                subject=raw["subject"],
                relation=raw["relation"],
                value=raw["value"],
                source_refs=source_refs,
                formation_version=raw["formation_version"],
                referenced_time=raw["referenced_time"],
            )
        except (TypeError, ValueError):
            raise ValueError("formed_units_invalid") from None
        if (
            unit.formation_version != FORMATION_VERSION
            or unit.id != _stable_id(
                unit.text, unit.subject, unit.relation, unit.value,
                unit.source_refs, unit.referenced_time,
            )
        ):
            raise ValueError("formed_units_invalid")
        units.append(unit)
    if len({unit.id for unit in units}) != len(units):
        raise ValueError("formed_units_invalid")
    return tuple(units)


def validate_persisted_grounded_memory_units(
    units: tuple[GroundedMemoryUnit, ...],
    segment: ColdDraftSegment,
    semantic_unit_ids: frozenset[str] = frozenset(),
) -> bool:
    """Re-run source grounding when durable formed units are reused."""

    for unit in units:
        candidate = {
            "text": unit.text,
            "subject": unit.subject,
            "relation": unit.relation,
            "value": unit.value,
            "source_refs": [asdict(ref) for ref in unit.source_refs],
            "referenced_time": unit.referenced_time,
        }
        if _validate_candidate(
            candidate, segment,
            semantic_equivalence=unit.id in semantic_unit_ids,
        ) != unit:
            return False
    return True


def _validate_candidate(
    candidate: Any,
    segment: ColdDraftSegment,
    *,
    semantic_equivalence: bool = False,
) -> GroundedMemoryUnit | None:
    expected = {"text", "subject", "relation", "value", "source_refs", "referenced_time"}
    # Real MiniMax-M3 responses omit the nullable referenced_time key entirely
    # (observed production zero-unit failure); explicit null has always been
    # valid, so treat absent as None before the exact-key check.
    if isinstance(candidate, dict) and "referenced_time" not in candidate:
        candidate = {**candidate, "referenced_time": None}
    if not isinstance(candidate, dict) or set(candidate) != expected:
        return None
    values = [candidate[name] for name in ("text", "subject", "relation", "value")]
    if not all(
        isinstance(value, str) and value.strip() and len(value) <= _MAX_FIELD_CHARS
        for value in values
    ):
        return None
    text, subject, relation, value = (value.strip() for value in values)
    if len([
        part for part in _ATOMIC_BOUNDARY.split(text)
        if part.strip()
    ]) > 1:
        return None
    referenced_time = candidate["referenced_time"]
    if referenced_time is not None and (
        not isinstance(referenced_time, str)
        or not referenced_time.strip()
        or len(referenced_time) > _MAX_FIELD_CHARS
    ):
        return None
    if referenced_time is not None:
        referenced_time = referenced_time.strip()
    claimed_referenced_time = referenced_time
    raw_refs = candidate["source_refs"]
    if not isinstance(raw_refs, list) or not raw_refs:
        return None
    turns = {turn.turn_id: turn for turn in segment.turns}
    turn_order = {turn.turn_id: index for index, turn in enumerate(segment.turns)}
    refs: list[SourceRef] = []
    seen: set[tuple[str, str]] = set()
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, dict) or set(raw_ref) != {"turn_id", "supporting_span"}:
            return None
        turn_id = raw_ref["turn_id"]
        span = raw_ref["supporting_span"]
        if (
            not isinstance(turn_id, str)
            or turn_id not in turns
            or not isinstance(span, str)
            or not span
            or turns[turn_id].content.count(span) != 1
            or len([
                part for part in _ATOMIC_BOUNDARY.split(span)
                if part.strip()
            ]) > 1
        ):
            return None
        pair = (turn_id, span)
        if pair not in seen:
            refs.append(SourceRef(*pair))
            seen.add(pair)
    refs.sort(key=lambda ref: (turn_order[ref.turn_id], turns[ref.turn_id].content.find(ref.supporting_span)))
    source = "\n".join(ref.supporting_span for ref in refs)
    if semantic_equivalence:
        if len(refs) != 1 or turns[refs[0].turn_id].role != "user":
            return None
        grounding_source = refs[0].supporting_span
        if _value_only_key(grounding_source) == _value_only_key(value):
            return None
        subject_literal = subject in source and subject in text
        relation_literal = _relation_grounded(relation, source) and relation in text
        value_literal = value in source and value in text
        if subject_literal and relation_literal:
            return None
        if not value_literal and (subject_literal or relation_literal):
            return None
    else:
        grounding_source = _authorized_grounding_source(
            refs, turns, turn_order, subject, relation, value,
        )
        if grounding_source is None:
            return None
        if subject not in source or value not in source:
            return None
        if subject not in text or relation not in text or value not in text:
            return None
    if referenced_time is not None and referenced_time not in source:
        if semantic_equivalence and referenced_time in text:
            referenced_time = None
        else:
            return None
    if (
        _NEGATION.search(grounding_source)
        or _NEGATION.search(text)
        or _UNCERTAINTY.search(grounding_source)
        or _UNCERTAINTY.search(text)
    ):
        exact_fact_spans = [
            ref.supporting_span.strip()
            for ref in refs
            if (
                subject in ref.supporting_span
                and relation in ref.supporting_span
                and value in ref.supporting_span
            )
        ]
        if len(exact_fact_spans) != 1 or text != exact_fact_spans[0]:
            return None
    for field in (
        text, subject, relation, value, claimed_referenced_time or "",
    ):
        for detail in _DETAIL.findall(field):
            if detail not in source:
                return None
    source_refs = tuple(refs)
    unit_id = _stable_id(text, subject, relation, value, source_refs, referenced_time)
    return GroundedMemoryUnit(
        id=unit_id,
        text=text,
        subject=subject,
        relation=relation,
        value=value,
        source_refs=source_refs,
        referenced_time=referenced_time,
    )


def _semantic_equivalence_decisions(
    model: FormationModel,
    segment: ColdDraftSegment,
    candidates: list[dict[str, Any]],
) -> tuple[bool, ...]:
    turns = {turn.turn_id: turn for turn in segment.turns}
    payload = {
        "candidates": [
            {
                "candidate": {
                    name: candidate[name]
                    for name in ("text", "subject", "relation", "value")
                },
                "source_refs": [
                    {
                        "role": turns[ref["turn_id"]].role,
                        "supporting_span": ref["supporting_span"],
                    }
                    for ref in candidate["source_refs"]
                ],
            }
            for candidate in candidates
        ]
    }
    try:
        raw = model.generate(
            [],
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            system_prompt=_SEMANTIC_EQUIVALENCE_PROMPT,
        )
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or set(parsed) != {"results"}:
            return (False,) * len(candidates)
        results = parsed["results"]
        if not isinstance(results, list) or len(results) != len(candidates):
            return (False,) * len(candidates)
        if not all(
            isinstance(result, dict)
            and set(result) == {"supported"}
            and isinstance(result["supported"], bool)
            for result in results
        ):
            return (False,) * len(candidates)
        return tuple(result["supported"] for result in results)
    except Exception:
        return (False,) * len(candidates)


def _authorized_grounding_source(
    refs: list[SourceRef],
    turns: dict[str, Any],
    turn_order: dict[str, int],
    subject: str,
    relation: str,
    value: str,
) -> str | None:
    relevant_user_refs = [
        ref
        for ref in refs
        if (
            turns[ref.turn_id].role == "user"
            and (
                subject in ref.supporting_span
                or relation in ref.supporting_span
                or value in ref.supporting_span
            )
        )
    ]
    user_source = "\n".join(
        ref.supporting_span for ref in relevant_user_refs
    )
    if (
        len(relevant_user_refs) == len(refs)
        and _each_ref_contributes_anchor(
            relevant_user_refs, subject, relation, value,
        )
        and
        subject in user_source
        and value in user_source
        and _relation_grounded(relation, user_source)
    ):
        return user_source
    if len(refs) == 1:
        assertion = refs[0]
        assertion_text = assertion.supporting_span.strip()
        folded = assertion_text.casefold()
        if (
            turns[assertion.turn_id].role == "assistant"
            and (
                folded.startswith("i " + _key(relation))
                or _key(assertion_text).startswith("我" + _key(relation))
            )
            and not _EPISTEMIC_REPORT.search(assertion_text)
            and subject in assertion_text
            and value in assertion_text
            and _relation_grounded(relation, assertion_text)
        ):
            return assertion_text
    if len(refs) != 2:
        return None
    assertion, acceptance = refs
    if (
        turns[assertion.turn_id].role != "assistant"
        or turns[acceptance.turn_id].role != "user"
        or turn_order[acceptance.turn_id] != turn_order[assertion.turn_id] + 1
        or not _ACCEPTANCE.fullmatch(acceptance.supporting_span.strip())
        or subject not in assertion.supporting_span
        or value not in assertion.supporting_span
        or not _relation_grounded(relation, assertion.supporting_span)
    ):
        return None
    return assertion.supporting_span


def _each_ref_contributes_anchor(
    refs: list[SourceRef],
    subject: str,
    relation: str,
    value: str,
) -> bool:
    anchors = (subject, relation, value)
    for index, ref in enumerate(refs):
        others = "\n".join(
            candidate.supporting_span
            for other_index, candidate in enumerate(refs)
            if other_index != index
        )
        if not any(
            anchor in ref.supporting_span and anchor not in others
            for anchor in anchors
        ):
            return False
    return True


def _relation_grounded(relation: str, source: str) -> bool:
    return _key(relation) in _key(source)


def _stable_id(
    text: str,
    subject: str,
    relation: str,
    value: str,
    source_refs: tuple[SourceRef, ...],
    referenced_time: str | None,
) -> str:
    canonical = json.dumps(
        {
            "formation_version": FORMATION_VERSION,
            "text": text,
            "subject": subject,
            "relation": relation,
            "value": value,
            "source_refs": [asdict(ref) for ref in source_refs],
            "referenced_time": referenced_time,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"grounded_memory_v1:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _value_only_key(value: str) -> str:
    return _key(value).rstrip(".!?。！？")
