"""Bounded source-grounded Formation with durable extraction/verification seams.

The v1 unit-only functions remain for historical checkpoint compatibility.
New production ingestion uses ``form_grounded_memory_batch`` (v2): full-window
mentions and facts, followed by one mandatory batch semantic verification.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Protocol

from .identity_coverage import self_identity_coverage_unit
from .models import ColdDraftSegment


FORMATION_VERSION = "grounded-formation-v1"
FORMATION_ENTITY_VERSION = "grounded-formation-v2"
_MAX_ENTITY_UNITS = 96
_MAX_MENTIONS = 192
_MAX_OUTPUT_CHARS = 160_000
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


@dataclass(frozen=True)
class GroundedEntityMention:
    """One source occurrence, independent of whether any fact was accepted."""

    id: str
    surface: str
    turn_id: str
    source_start: int
    source_end: int
    source_role: str
    identity: str
    same_as: str | None = None
    distinct_from: tuple[str, ...] = ()
    identity_source_refs: tuple[SourceRef, ...] = ()


@dataclass(frozen=True)
class GroundedUnitMentions:
    unit_id: str
    subject: str | None = None
    object: str | None = None
    mentions: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroundedMemoryBatch:
    units: tuple[GroundedMemoryUnit, ...]
    mentions: tuple[GroundedEntityMention, ...]
    unit_mentions: tuple[GroundedUnitMentions, ...]


_ENTITY_FORMATION_PROMPT = """Extract source-grounded atomic facts AND entity mentions from the ENTIRE bounded conversation. Return strict JSON only: {"mentions":[...],"units":[...]}.
Each mention: {"handle":str,"surface":str,"turn_id":str,"occurrence":int,"identity":"named"|"current_user"|"new"|"unresolved","same_as":str|null,"distinct_from":[str],"identity_source_refs":[{"turn_id":str,"supporting_span":str}]}.
surface must be an exact substring of that turn. occurrence is its zero-based ordinal among non-overlapping exact occurrences of that SAME surface in that SAME turn: first=0, second=1, etc. It is NOT a character offset; the backend computes and checks exact source positions. Handles are unique within this output. Cover people, projects, materials, samples, software, organizations, places and other specifically referred-to entities, including entities in questions, quotations, plans and hypotheses even when units is empty. Do not turn numbers, units, measurements or generic categories into entities. Repeated surface occurrences may be separate mentions. Do not silently stop after an arbitrary prefix.
identity named means a specifically named referent; current_user only means the actual user speaker (including explicit self-name), never 'my colleague', 'we' or first person inside quotations. new requires explicit evidence of a different/new identity despite sharing a name. unresolved includes pronouns without supported antecedents. same_as points to a mention handle ONLY with explicit alias or unambiguous coreference evidence within this window; distinct_from handles require explicit difference evidence. Supply exact identity_source_refs for current_user, new, same_as and distinct_from. Names alone do not prove identity equality. No external knowledge or fuzzy merging.
Each unit: {"text":str,"subject":str,"relation":str,"value":str,"source_refs":[{"turn_id":str,"supporting_span":str}],"referenced_time":str|null,"subject_mention":str|null,"object_mention":str|null,"mentions":[str]}.
Use exact unambiguous source substrings as supporting_span. Preserve values, numbers, units, dates, negation, hypothesis, plan, reported-speech status and uncertainty. One self-contained fact per unit. The whole proposition AND subject/relation/value pairing must be entailed, not just words found somewhere in the source. Questions do not assert their proposed answer; hypotheses do not establish their consequence as an unconditional fact. Preserve a report as a report. An assistant-only assertion cannot establish a user/world fact or successful assistant action; cite an explicit user acceptance/correction where available. Do not invent absent context.
subject_mention/object_mention reference actual semantic roles in this fact, independent of surface word order. object_mention is null for literal/measurement attributes and where the complete proposition cannot safely express a binary entity relationship. mentions lists other participants supported by this fact's cited source, not all nearby names. The fact may cite several turns to preserve coreference evidence. Return mentions with units=[] when only mentions are supported. Limits: 96 units and 192 mentions; if impossible to cover within limits return {"error":"formation_output_too_large"} instead of truncating."""

_ENTITY_VERIFICATION_PROMPT = """Verify ONLY against the supplied bounded conversation, candidates, exact source locations and speaker roles. Return strict JSON only: {"units":[{"supported":bool}],"mentions":[{"supported":bool,"identity_supported":bool}]} with one result per input item in the same order, including empty lists. Do not return reasoning.
For EVERY unit verify complete proposition entailment AND correct subject/relation/value binding AND cited subject/object/participant mention roles. Mere lexical overlap is insufficient: 'A likes tea, B likes coffee' does not support A likes coffee. Permit faithful paraphrase/translation and source-supported cross-turn coreference. Reject invented details, incorrect measurements, changed dates, omitted negation/uncertainty/conditions, question-to-answer or hypothesis-to-fact conversion, or report-to-world-fact conversion. Preserve the speaker and speech-act: assistant claims do not authorize user/world facts or prove completed assistant actions without explicit cited user confirmation. Cited refs must carry the evidence, not merely unrelated words in the broader window. Null object is correct for a literal attribute or a proposition without a safely identifiable binary entity object.
For each mention supported verifies that this exact occurrence really refers to an entity (entities in questions/hypotheses/quotes are allowed; numbers/units/generic categories alone are not). identity_supported separately verifies its identity kind and every same_as/distinct_from link using the supplied identity_source_refs. current_user must be the actual user, not 'my colleague', plural 'we' or a quoted speaker. new requires explicit difference/new-identity evidence; exact spelling alone does not justify identity reuse or merging. Unsupported pronoun identity must be unresolved. A mention can be supported while its proposed identity is unsupported; keep these decisions separate."""


def form_grounded_memory_batch(
    segment: ColdDraftSegment,
    model: FormationModel,
    *,
    extracted_checkpoint: dict[str, Any] | None = None,
    verified_checkpoint: dict[str, Any] | None = None,
    checkpoint: Callable[[str, dict[str, Any]], None] | None = None,
) -> GroundedMemoryBatch:
    """Extract once, verify once; checkpoint each successful stage before proceeding.

    Callback failures propagate without advancing the next stage. A malformed or
    unavailable verifier is a retryable Formation failure, never an empty success.
    Only explicit false decisions discard unsupported candidates normally.
    """
    _check_entity_window(segment)
    if verified_checkpoint is not None:
        try:
            batch = deserialize_grounded_memory_batch(verified_checkpoint)
            if not validate_persisted_grounded_memory_batch(batch, segment):
                raise ValueError()
        except (TypeError, ValueError, KeyError):
            raise FormationError("formation_checkpoint_invalid") from None
        return batch
    if extracted_checkpoint is None:
        payload = _entity_source_payload(segment)
        parsed = _entity_model_json(model, payload, _ENTITY_FORMATION_PROMPT)
        if parsed == {"error": "formation_output_too_large"}:
            raise FormationError("formation_output_too_large")
        extracted = {
            "schema_version": FORMATION_ENTITY_VERSION,
            "source_digest": _entity_source_digest(segment),
            "output": parsed,
        }
    else:
        extracted = extracted_checkpoint
    mentions, units, roles = _parse_entity_extraction(extracted, segment)
    if extracted_checkpoint is None and checkpoint is not None:
        checkpoint("extracted", extracted)
    if mentions or units:
        verification = _entity_model_json(
            model,
            {
                **_entity_source_payload(segment),
                "units": [
                    {"candidate": asdict(unit), "roles": asdict(role)}
                    for unit, role in zip(units, roles)
                ],
                "mentions": [asdict(mention) for mention in mentions],
            },
            _ENTITY_VERIFICATION_PROMPT,
            error_code="formation_verification_failed",
        )
        unit_decisions, mention_decisions = _parse_entity_decisions(
            verification, len(units), len(mentions),
        )
    else:
        unit_decisions, mention_decisions = (), ()
    accepted_mentions: list[GroundedEntityMention] = []
    turns = {turn.turn_id: turn for turn in segment.turns}
    for mention, (supported, identity_supported) in zip(mentions, mention_decisions):
        identity_claim = (
            mention.identity in {"current_user", "new"}
            or mention.same_as is not None or bool(mention.distinct_from)
        )
        if identity_claim and not any(
            turns[ref.turn_id].role == "user"
            for ref in mention.identity_source_refs
        ):
            identity_supported = False
        if supported:
            accepted_mentions.append(mention if identity_supported else replace(
                mention, identity="unresolved", same_as=None, distinct_from=(),
                identity_source_refs=(),
            ))
    accepted_ids = {mention.id for mention in accepted_mentions}
    # A rejected target cannot survive as an identity assertion in another record.
    accepted_mentions = [
        replace(mention, identity="unresolved", same_as=None, distinct_from=())
        if (
            mention.same_as is not None and mention.same_as not in accepted_ids
            or any(ref not in accepted_ids for ref in mention.distinct_from)
        ) else mention
        for mention in accepted_mentions
    ]
    accepted_units: dict[str, tuple[GroundedMemoryUnit, GroundedUnitMentions]] = {}
    for unit, role, supported in zip(units, roles, unit_decisions):
        participating = {ref for ref in (role.subject, role.object, *role.mentions) if ref}
        if supported and participating <= accepted_ids:
            accepted_units.setdefault(unit.id, (unit, role))
    batch = GroundedMemoryBatch(
        units=tuple(item[0] for item in accepted_units.values()),
        mentions=tuple(accepted_mentions),
        unit_mentions=tuple(item[1] for item in accepted_units.values()),
    )
    if checkpoint is not None:
        checkpoint("verified", serialize_grounded_memory_batch(batch))
    return batch


def serialize_grounded_memory_batch(batch: GroundedMemoryBatch) -> dict[str, Any]:
    # Return a JSON-shaped object even before the owner's file roundtrip.
    return json.loads(json.dumps({
        "schema_version": FORMATION_ENTITY_VERSION,
        "units": serialize_grounded_memory_units(batch.units),
        "mentions": [asdict(mention) for mention in batch.mentions],
        "unit_mentions": [asdict(role) for role in batch.unit_mentions],
    }, ensure_ascii=False))


def deserialize_grounded_memory_batch(raw: Any) -> GroundedMemoryBatch:
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version", "units", "mentions", "unit_mentions",
    } or raw["schema_version"] != FORMATION_ENTITY_VERSION:
        raise ValueError("formed_batch_invalid")
    if not isinstance(raw["mentions"], list) or len(raw["mentions"]) > _MAX_MENTIONS:
        raise ValueError("formed_batch_invalid")
    try:
        mentions = tuple(GroundedEntityMention(**{
            **item,
            "distinct_from": tuple(item["distinct_from"]),
            "identity_source_refs": tuple(SourceRef(**ref) for ref in item["identity_source_refs"]),
        }) for item in raw["mentions"])
        roles = tuple(GroundedUnitMentions(**{
            **item, "mentions": tuple(item["mentions"]),
        }) for item in raw["unit_mentions"])
        units = deserialize_grounded_memory_units(raw["units"])
        batch = GroundedMemoryBatch(units, mentions, roles)
        if not _entity_batch_links_valid(batch):
            raise ValueError()
    except (TypeError, ValueError, KeyError, AttributeError):
        raise ValueError("formed_batch_invalid") from None
    return batch


def validate_persisted_grounded_memory_batch(
    batch: GroundedMemoryBatch, segment: ColdDraftSegment,
) -> bool:
    """Recheck immutable source and links; never rerun a successful verifier."""
    try:
        if not _entity_batch_links_valid(batch):
            return False
        turns = {turn.turn_id: turn for turn in segment.turns}
        for mention in batch.mentions:
            turn = turns.get(mention.turn_id)
            if (
                turn is None or mention.source_role != turn.role
                or type(mention.source_start) is not int or type(mention.source_end) is not int
                or not 0 <= mention.source_start < mention.source_end <= len(turn.content)
                or turn.content[mention.source_start:mention.source_end] != mention.surface
                or mention.id != _mention_id(segment, mention.turn_id, mention.source_start, mention.source_end)
                or mention.identity not in {"named", "current_user", "new", "unresolved"}
                or _entity_refs([asdict(ref) for ref in mention.identity_source_refs], segment, allow_empty=True) is None
                or (mention.identity in {"current_user", "new"} or mention.same_as is not None or mention.distinct_from) and not mention.identity_source_refs
            ):
                return False
            if (
                mention.identity in {"current_user", "new"}
                or mention.same_as is not None or mention.distinct_from
            ) and not any(
                turns[ref.turn_id].role == "user"
                for ref in mention.identity_source_refs
            ):
                return False
        for unit in batch.units:
            candidate = {key: value for key, value in asdict(unit).items() if key not in {"id", "formation_version"}}
            if _validate_entity_unit(candidate, segment) != unit:
                return False
    except (TypeError, ValueError, KeyError, AttributeError):
        return False
    return True


def _check_entity_window(segment: ColdDraftSegment) -> None:
    if (
        len(segment.turns) > _MAX_TURNS
        or sum(len(turn.content) for turn in segment.turns) > _MAX_SOURCE_CHARS
    ):
        raise FormationError("formation_window_too_large")
    if len({turn.turn_id for turn in segment.turns}) != len(segment.turns):
        raise FormationError("formation_source_invalid")


def _entity_source_payload(segment: ColdDraftSegment) -> dict[str, Any]:
    return {"turns": [
        {"turn_id": turn.turn_id, "role": turn.role, "text": turn.content}
        for turn in segment.turns
    ]}


def _entity_source_digest(segment: ColdDraftSegment) -> str:
    return hashlib.sha256(json.dumps(
        {"segment_id": segment.segment_id, "conversation_id": segment.conversation_id,
         "turns": [asdict(turn) for turn in segment.turns]},
        ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()


def _entity_model_json(
    model: FormationModel, payload: dict[str, Any], prompt: str,
    *, error_code: str = "formation_failed",
) -> dict[str, Any]:
    try:
        serialized_payload = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"),
        )
        if len(serialized_payload) > _MAX_OUTPUT_CHARS:
            raise FormationError("formation_verification_window_too_large")
        raw = model.generate(
            [], serialized_payload, system_prompt=prompt,
        )
        if not isinstance(raw, str) or len(raw) > _MAX_OUTPUT_CHARS:
            raise FormationError("formation_output_too_large")
        # A complete JSON-only Markdown fence changes transport formatting, not
        # schema. Never search for/extract JSON from commentary or mixed blocks.
        fenced = re.fullmatch(
            r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```",
            raw.strip(), flags=re.DOTALL | re.IGNORECASE,
        )
        if fenced is not None:
            raw = fenced.group(1)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except FormationError:
        raise
    except Exception:
        raise FormationError(error_code) from None


def _mention_id(segment: ColdDraftSegment, turn_id: str, start: int, end: int) -> str:
    value = json.dumps([segment.conversation_id, turn_id, start, end], ensure_ascii=False, separators=(",", ":"))
    return "mention_v2:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _entity_refs(
    raw: Any, segment: ColdDraftSegment, *, allow_empty: bool = False,
    expand_repeated: bool = False,
) -> tuple[SourceRef, ...] | None:
    if (
        not isinstance(raw, (list, tuple))
        or (not raw and not allow_empty) or len(raw) > _MAX_TURNS
    ):
        return None
    turns = {turn.turn_id: turn for turn in segment.turns}
    order = {turn.turn_id: index for index, turn in enumerate(segment.turns)}
    refs: list[SourceRef] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"turn_id", "supporting_span"}:
            return None
        turn_id, span = item["turn_id"], item["supporting_span"]
        if (
            not isinstance(turn_id, str) or turn_id not in turns
            or not isinstance(span, str) or not span
        ):
            return None
        count = turns[turn_id].content.count(span)
        if count == 0 or count > 1 and not expand_repeated:
            return None
        if count > 1:
            # Identity claims are still verified against source, with an exact
            # whole-turn citation rather than a guessed duplicate occurrence.
            span = turns[turn_id].content
        ref = SourceRef(turn_id, span)
        if ref not in refs:
            refs.append(ref)
    return tuple(sorted(refs, key=lambda ref: (
        order[ref.turn_id], turns[ref.turn_id].content.index(ref.supporting_span),
    )))


def _validate_entity_unit(raw: Any, segment: ColdDraftSegment) -> GroundedMemoryUnit | None:
    if isinstance(raw, dict) and "referenced_time" not in raw:
        raw = {**raw, "referenced_time": None}
    if not isinstance(raw, dict) or set(raw) != {
        "text", "subject", "relation", "value", "source_refs", "referenced_time",
    }:
        return None
    if not all(
        isinstance(raw[key], str) and raw[key].strip()
        and len(raw[key]) <= _MAX_FIELD_CHARS
        for key in ("text", "subject", "relation", "value")
    ):
        return None
    text, subject, relation, value = (raw[key].strip() for key in ("text", "subject", "relation", "value"))
    if len([part for part in _ATOMIC_BOUNDARY.split(text) if part.strip()]) > 1:
        return None
    refs = _entity_refs(raw["source_refs"], segment)
    if refs is None:
        return None
    turns = {turn.turn_id: turn for turn in segment.turns}
    # Role authorization is never supplied by the extractor or verifier alone.
    if not any(turns[ref.turn_id].role == "user" for ref in refs):
        return None
    source = "\n".join(ref.supporting_span for ref in refs)
    referenced_time = raw["referenced_time"]
    if referenced_time is not None and (
        not isinstance(referenced_time, str) or not referenced_time.strip()
        or referenced_time not in source
    ):
        return None
    if any(
        detail not in source
        for field in (text, subject, relation, value)
        for detail in _DETAIL.findall(field)
    ):
        return None
    return GroundedMemoryUnit(
        id=_stable_id(text, subject, relation, value, refs, referenced_time, formation_version=FORMATION_ENTITY_VERSION),
        text=text, subject=subject, relation=relation, value=value,
        source_refs=refs, referenced_time=referenced_time,
        formation_version=FORMATION_ENTITY_VERSION,
    )


def _locate_entity_mention(item: dict[str, Any], content: str) -> tuple[int, int]:
    """Resolve an exact occurrence; never guess among repeated name positions.

    Real-provider development evidence showed inaccurate character counting in
    otherwise complete JSON. New outputs use occurrence ordinals. Previously
    checkpointed correct offsets remain readable; a unique exact substring also
    permits deterministic correction without trusting a model's character count.
    """
    surface = item["surface"]
    if not isinstance(surface, str) or not surface:
        raise FormationError("formation_mention_source_invalid")
    positions = [match.start() for match in re.finditer(re.escape(surface), content)]
    if not positions:
        raise FormationError("formation_mention_source_invalid")
    if "occurrence" in item:
        occurrence = item["occurrence"]
        if type(occurrence) is not int or not 0 <= occurrence < len(positions):
            raise FormationError("formation_mention_source_invalid")
        start = positions[occurrence]
        end = start + len(surface)
        if len(positions) > 1 and (
            "source_start" in item and item["source_start"] != start
            or "source_end" in item and item["source_end"] != end
        ):
            raise FormationError("formation_mention_source_invalid")
    elif len(positions) == 1:
        start, end = positions[0], positions[0] + len(surface)
    else:
        start, end = item.get("source_start"), item.get("source_end")
        if (
            type(start) is not int or type(end) is not int
            or start not in positions or end != start + len(surface)
        ):
            raise FormationError("formation_mention_source_invalid")
    if content[start:end] != surface:
        raise FormationError("formation_mention_source_invalid")
    return start, end


def _parse_entity_extraction(
    raw: Any, segment: ColdDraftSegment,
) -> tuple[
    tuple[GroundedEntityMention, ...], tuple[GroundedMemoryUnit, ...],
    tuple[GroundedUnitMentions, ...],
]:
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema_version", "source_digest", "output"}
        or raw["schema_version"] != FORMATION_ENTITY_VERSION
        or raw["source_digest"] != _entity_source_digest(segment)
    ):
        raise FormationError("formation_checkpoint_invalid")
    output = raw["output"]
    if (
        not isinstance(output, dict) or set(output) != {"mentions", "units"}
        or not isinstance(output["mentions"], list)
        or not isinstance(output["units"], list)
    ):
        raise FormationError("formation_output_invalid")
    if len(output["mentions"]) > _MAX_MENTIONS or len(output["units"]) > _MAX_ENTITY_UNITS:
        raise FormationError("formation_output_too_large")
    if len(json.dumps(output, ensure_ascii=False)) > _MAX_OUTPUT_CHARS:
        raise FormationError("formation_output_too_large")
    turns = {turn.turn_id: turn for turn in segment.turns}
    handles: dict[str, str] = {}
    parsed: list[tuple[dict[str, Any], GroundedEntityMention]] = []
    mention_required = {
        "handle", "surface", "turn_id", "identity",
    }
    mention_allowed = mention_required | {
        "occurrence", "source_start", "source_end",
        "same_as", "distinct_from", "identity_source_refs",
    }
    for item in output["mentions"]:
        if (
            not isinstance(item, dict) or not mention_required <= set(item)
            or set(item) - mention_allowed
        ):
            raise FormationError("formation_output_invalid")
        handle = item["handle"]
        if not isinstance(handle, str) or not handle or handle in handles:
            raise FormationError("formation_output_invalid")
        turn_id, surface = item["turn_id"], item["surface"]
        if not isinstance(turn_id, str) or turn_id not in turns:
            raise FormationError("formation_mention_source_invalid")
        start, end = _locate_entity_mention(item, turns[turn_id].content)
        if not isinstance(item["identity"], str) or item["identity"] not in {"named", "current_user", "new", "unresolved"}:
            raise FormationError("formation_output_invalid")
        refs = _entity_refs(
            item.get("identity_source_refs", []), segment,
            allow_empty=True, expand_repeated=True,
        )
        if refs is None:
            raise FormationError("formation_mention_source_invalid")
        mention_id = _mention_id(segment, turn_id, start, end)
        handles[handle] = mention_id
        parsed.append((item, GroundedEntityMention(mention_id, surface, turn_id, start, end, turns[turn_id].role, item["identity"], identity_source_refs=refs)))
    mentions: dict[str, GroundedEntityMention] = {}
    for item, mention in parsed:
        same = item.get("same_as")
        distinct = item.get("distinct_from", [])
        if (
            same is not None and (not isinstance(same, str) or same not in handles)
            or not isinstance(distinct, list)
            or not all(isinstance(ref, str) and ref in handles for ref in distinct)
        ):
            raise FormationError("formation_mention_link_invalid")
        if (same is not None or distinct or mention.identity in {"current_user", "new"}) and not mention.identity_source_refs:
            raise FormationError("formation_mention_source_invalid")
        mention = replace(mention, same_as=handles.get(same), distinct_from=tuple(dict.fromkeys(handles[ref] for ref in distinct)))
        if mention.id in mentions and mentions[mention.id] != mention:
            raise FormationError("formation_mention_link_invalid")
        mentions[mention.id] = mention
    units: list[GroundedMemoryUnit] = []
    roles: list[GroundedUnitMentions] = []
    unit_required = {"text", "subject", "relation", "value", "source_refs"}
    unit_allowed = unit_required | {
        "referenced_time", "subject_mention", "object_mention", "mentions",
    }
    for item in output["units"]:
        if (
            not isinstance(item, dict) or not unit_required <= set(item)
            or set(item) - unit_allowed
        ):
            raise FormationError("formation_output_invalid")
        if not all(
            isinstance(item[key], str) and item[key].strip()
            and len(item[key]) <= _MAX_FIELD_CHARS
            for key in ("text", "subject", "relation", "value")
        ) or _entity_refs(item["source_refs"], segment) is None:
            raise FormationError("formation_output_invalid")
        if item.get("referenced_time") is not None and (
            not isinstance(item["referenced_time"], str)
            or not item["referenced_time"].strip()
            or len(item["referenced_time"]) > _MAX_FIELD_CHARS
        ):
            raise FormationError("formation_output_invalid")
        participant_handles = item.get("mentions", [])
        subject_handle, object_handle = item.get("subject_mention"), item.get("object_mention")
        if (
            not isinstance(participant_handles, list)
            or not all(isinstance(ref, str) and ref in handles for ref in participant_handles)
            or any(
                ref is not None and (not isinstance(ref, str) or ref not in handles)
                for ref in (subject_handle, object_handle)
            )
        ):
            raise FormationError("formation_mention_link_invalid")
        unit = _validate_entity_unit({key: value for key, value in item.items() if key not in {"subject_mention", "object_mention", "mentions"}}, segment)
        if unit is None:
            continue
        units.append(unit)
        roles.append(GroundedUnitMentions(unit.id, handles.get(subject_handle), handles.get(object_handle), tuple(dict.fromkeys(handles[ref] for ref in participant_handles))))
    turn_order = {turn.turn_id: index for index, turn in enumerate(segment.turns)}
    ordered_mentions = tuple(sorted(
        mentions.values(),
        key=lambda mention: (turn_order[mention.turn_id], mention.source_start, mention.source_end),
    ))
    batch = GroundedMemoryBatch(tuple(units), ordered_mentions, tuple(roles))
    if not _entity_batch_links_valid(batch, allow_duplicate_units=True):
        raise FormationError("formation_mention_link_invalid")
    return batch.mentions, batch.units, batch.unit_mentions


def _parse_entity_decisions(
    raw: Any, unit_count: int, mention_count: int,
) -> tuple[tuple[bool, ...], tuple[tuple[bool, bool], ...]]:
    if (
        not isinstance(raw, dict) or set(raw) != {"units", "mentions"}
        or not isinstance(raw["units"], list)
        or not isinstance(raw["mentions"], list)
        or len(raw["units"]) != unit_count
        or len(raw["mentions"]) != mention_count
    ):
        raise FormationError("formation_verification_failed")
    if not all(
        isinstance(item, dict) and set(item) == {"supported"}
        and type(item["supported"]) is bool
        for item in raw["units"]
    ) or not all(
        isinstance(item, dict) and set(item) == {"supported", "identity_supported"}
        and type(item["supported"]) is bool
        and type(item["identity_supported"]) is bool
        for item in raw["mentions"]
    ):
        raise FormationError("formation_verification_failed")
    return (
        tuple(item["supported"] for item in raw["units"]),
        tuple((item["supported"], item["identity_supported"]) for item in raw["mentions"]),
    )


def _entity_batch_links_valid(batch: GroundedMemoryBatch, *, allow_duplicate_units: bool = False) -> bool:
    ids = {mention.id for mention in batch.mentions}
    unit_ids = {unit.id for unit in batch.units}
    if (
        len(ids) != len(batch.mentions)
        or len(batch.units) != len(batch.unit_mentions)
        or not allow_duplicate_units and len(unit_ids) != len(batch.units)
    ):
        return False
    for mention in batch.mentions:
        if (
            mention.same_as is not None
            and (mention.same_as not in ids or mention.same_as == mention.id)
            or mention.id in mention.distinct_from
            or not set(mention.distinct_from) <= ids
            or mention.same_as in mention.distinct_from
        ):
            return False
    for unit, role in zip(batch.units, batch.unit_mentions):
        participants = {ref for ref in (role.subject, role.object, *role.mentions) if ref}
        if (
            unit.formation_version != FORMATION_ENTITY_VERSION
            or role.unit_id != unit.id or not participants <= ids
        ):
            return False
    # Identity cycles have no grounded antecedent; never let binding order choose.
    by_id = {mention.id: mention for mention in batch.mentions}
    for mention in batch.mentions:
        seen = {mention.id}
        target = mention.same_as
        while target is not None:
            if target in seen:
                return False
            seen.add(target)
            target = by_id[target].same_as
    return True


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
    # Deterministic self-identity coverage guard: fills an omitted explicit
    # self-identification with one strict-validated source-grounded unit.
    # LLM-free; never enters semantic_unit_ids (checkpoint reuse revalidates
    # it through the same strict path).
    identity_unit = self_identity_coverage_unit(
        segment, tuple(accepted.values()), validate=_validate_candidate,
    )
    if identity_unit is not None:
        accepted.setdefault(identity_unit.id, identity_unit)
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
    if not isinstance(raw_units, list) or len(raw_units) > _MAX_ENTITY_UNITS:
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
            unit.formation_version not in {FORMATION_VERSION, FORMATION_ENTITY_VERSION}
            or unit.id != _stable_id(
                unit.text, unit.subject, unit.relation, unit.value,
                unit.source_refs, unit.referenced_time,
                formation_version=unit.formation_version,
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
    *,
    formation_version: str = FORMATION_VERSION,
) -> str:
    canonical = json.dumps(
        {
            "formation_version": formation_version,
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
    prefix = "grounded_memory_v2" if formation_version == FORMATION_ENTITY_VERSION else "grounded_memory_v1"
    return f"{prefix}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _value_only_key(value: str) -> str:
    return _key(value).rstrip(".!?。！？")
