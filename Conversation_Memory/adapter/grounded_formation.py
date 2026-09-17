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
FORMATION_PROGRESS_VERSION = "grounded-formation-v2-progress-v1"
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

    def __init__(self, code: str = "formation_failed", *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class SourceRef:
    turn_id: str
    supporting_span: str


@dataclass(frozen=True)
class GroundedMemoryUnit:
    id: str
    text: str
    subject: str | None
    relation: str | None
    value: str | None
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
class FormationIssue:
    candidate: str
    index: int
    code: str
    status: str


@dataclass(frozen=True)
class GroundedMemoryBatch:
    units: tuple[GroundedMemoryUnit, ...]
    mentions: tuple[GroundedEntityMention, ...]
    unit_mentions: tuple[GroundedUnitMentions, ...]
    issues: tuple[FormationIssue, ...] = ()


@dataclass(frozen=True)
class _EntityExtraction:
    batch: GroundedMemoryBatch
    unit_indexes: tuple[int, ...]
    mention_indexes: tuple[int, ...]
    mention_handles: tuple[tuple[str, str], ...]


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

_ENTITY_SOURCE_REPAIR_PROMPT = """Repair ONLY invalid source_refs for the supplied immutable fact candidates. Return strict JSON only: {"repairs":[{"index":int,"source_refs":[{"turn_id":str,"supporting_span":str}]}]} with exactly one repair per supplied candidate index. Return no other fields.
Do not change or regenerate any fact text, subject, relation, value, referenced_time, or mention roles. For each candidate use ONLY turn IDs already listed in its original source_refs. supporting_span must be an exact, uniquely occurring substring of that turn; include enough context to preserve the complete proposition, pairing, speaker role, negation, uncertainty, conditions and reported speech. A whole turn is allowed when a shorter substring is ambiguous. Questions and hypotheses do not establish unconditional facts, and assistant claims do not authorize user/world facts or completed assistant actions. Use source_refs=[] if those turns do not support the unchanged candidate. Do not invent or repair entities or identities."""


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
        response = _entity_model_response(model, payload, _ENTITY_FORMATION_PROMPT)
        extracted = {
            "schema_version": FORMATION_PROGRESS_VERSION,
            "source_coverage_version": 1,
            "source_digest": _entity_source_digest(segment),
            "response": response if isinstance(response, str) else "",
        }
        if not isinstance(response, str):
            extracted["response_error"] = "formation_output_invalid"
        elif len(response) > _MAX_OUTPUT_CHARS:
            # A marked diagnostic prefix is never parsed as a complete response.
            extracted["response"] = response[:_MAX_OUTPUT_CHARS]
            extracted["response_error"] = "formation_output_too_large"
        if checkpoint is not None:
            checkpoint("extracted", extracted)
    else:
        extracted = extracted_checkpoint
    parsed = _parse_entity_extraction(extracted, segment)
    mentions, units, roles = (
        parsed.batch.mentions, parsed.batch.units, parsed.batch.unit_mentions,
    )
    issues = list(parsed.batch.issues)
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
    for mention, index, (supported, identity_supported) in zip(
        mentions, parsed.mention_indexes, mention_decisions,
    ):
        if not supported:
            issues.append(FormationIssue(
                "mention", index, "formation_mention_rejected", "rejected",
            ))
            continue
        identity_claim = (
            mention.identity in {"current_user", "new"}
            or mention.same_as is not None or bool(mention.distinct_from)
        )
        if identity_claim and not any(
            turns[ref.turn_id].role == "user"
            for ref in mention.identity_source_refs
        ):
            identity_supported = False
        if not identity_supported and not any(
            issue.candidate == "identity" and issue.index == index
            and issue.status == "pending" for issue in issues
        ):
            issues.append(FormationIssue(
                "identity", index, "formation_identity_rejected", "rejected",
            ))
        accepted_mentions.append(mention if identity_supported else replace(
            mention, identity="unresolved", same_as=None, distinct_from=(),
            identity_source_refs=(),
        ))
    accepted_ids = {mention.id for mention in accepted_mentions}
    original_mentions = {mention.id: mention for mention in mentions}
    invalid_identity = _rejected_identity_dependencies(
        original_mentions,
        set(original_mentions) - accepted_ids | {
            mention.id for mention in accepted_mentions if mention.identity == "unresolved"
        },
    )
    for mention, index in zip(mentions, parsed.mention_indexes):
        if mention.id in accepted_ids & invalid_identity and not any(
            issue.candidate == "identity" and issue.index == index for issue in issues
        ) and mention.identity != "unresolved":
            issues.append(FormationIssue(
                "identity", index, "formation_identity_dependency_rejected", "rejected",
            ))
    # Keep the occurrence, but never preserve a claim through a rejected target.
    accepted_mentions = [
        replace(mention, identity="unresolved", same_as=None, distinct_from=(),
                identity_source_refs=()) if mention.id in invalid_identity else mention
        for mention in accepted_mentions
    ]
    final_mentions = {mention.id: mention for mention in accepted_mentions}
    accepted_units: dict[str, tuple[GroundedMemoryUnit, GroundedUnitMentions]] = {}
    for unit, role, index, supported in zip(
        units, roles, parsed.unit_indexes, unit_decisions,
    ):
        participating = {ref for ref in (role.subject, role.object, *role.mentions) if ref}
        identity_independent = participating <= accepted_ids and _verified_unit_identity_independent(
            unit, role, final_mentions, invalid_identity,
        )
        if supported and identity_independent:
            accepted_units.setdefault(unit.id, (unit, role))
        else:
            issues.append(FormationIssue(
                "unit", index,
                "formation_semantic_rejected" if not supported
                else "formation_mention_dependency_rejected" if not participating <= accepted_ids
                else "formation_identity_dependency_rejected",
                "rejected",
            ))
    batch = GroundedMemoryBatch(
        units=tuple(item[0] for item in accepted_units.values()),
        mentions=tuple(accepted_mentions),
        unit_mentions=tuple(item[1] for item in accepted_units.values()),
        issues=tuple(dict.fromkeys(issues)),
    )
    if checkpoint is not None:
        checkpoint("verified", serialize_grounded_memory_batch(batch))
    return batch


def repair_grounded_memory_batch(
    segment: ColdDraftSegment,
    model: FormationModel,
    *,
    extracted_checkpoint: dict[str, Any],
    verified_checkpoint: dict[str, Any],
    repair_checkpoint: dict[str, Any] | None = None,
    checkpoint: Callable[[str, dict[str, Any]], None] | None = None,
) -> GroundedMemoryBatch:
    """Locate failed citations once, without changing accepted facts or identities.

    The owner retains the original verified checkpoint and persists the merged
    ``repair_verified`` result separately, even when some issues remain pending.
    A saved repair receipt is reused after parsing or verification failures.
    """
    base = form_grounded_memory_batch(
        segment, model, verified_checkpoint=verified_checkpoint,
    )
    output = _entity_extraction_output(extracted_checkpoint, segment)
    parsed = _parse_entity_extraction(extracted_checkpoint, segment)
    candidates = _entity_source_repair_candidates(output, parsed, base, segment)
    if not candidates:
        return base
    indexes = tuple(index for index, _item, _role in candidates)
    if repair_checkpoint is None:
        selected_turns = {
            ref["turn_id"] for _index, item, _role in candidates
            for ref in item["source_refs"]
        }
        response = _entity_model_response(
            model,
            {
                "turns": [
                    turn for turn in _entity_source_payload(segment)["turns"]
                    if turn["turn_id"] in selected_turns
                ],
                "units": [
                    {"index": index, "candidate": item}
                    for index, item, _role in candidates
                ],
            },
            _ENTITY_SOURCE_REPAIR_PROMPT,
            error_code="formation_repair_failed",
        )
        receipt = {
            "schema_version": FORMATION_PROGRESS_VERSION,
            "source_digest": _entity_source_digest(segment),
            "unit_indexes": list(indexes),
            "response": response if isinstance(response, str) else "",
        }
        if not isinstance(response, str):
            receipt["response_error"] = "formation_output_invalid"
        elif len(response) > _MAX_OUTPUT_CHARS:
            receipt["response"] = response[:_MAX_OUTPUT_CHARS]
            receipt["response_error"] = "formation_output_too_large"
        if checkpoint is not None:
            checkpoint("repair", receipt)
    else:
        receipt = repair_checkpoint
    repairs = _entity_source_repair_output(receipt, indexes, segment)
    mentions_by_id = {mention.id: mention for mention in base.mentions}
    unresolved = {
        mention.id for mention in base.mentions if mention.identity == "unresolved"
    }
    existing = {unit.id: role for unit, role in zip(base.units, base.unit_mentions)}
    issues = list(base.issues)
    resolved: set[int] = set()
    eligible: list[tuple[int, GroundedMemoryUnit, GroundedUnitMentions, set[str]]] = []
    covered_positions: set[int] = set()
    for index, item, role in candidates:
        refs = repairs[index]
        allowed_turns = {ref["turn_id"] for ref in item["source_refs"]}
        if any(ref["turn_id"] not in allowed_turns for ref in refs):
            raise FormationError("formation_repair_output_invalid")
        if not refs or _entity_refs(refs, segment) is None:
            continue
        resolved.add(index)
        candidate = {**item, "source_refs": refs}
        unit = _validate_entity_unit({
            key: value for key, value in candidate.items()
            if key not in {"subject_mention", "object_mention", "mentions"}
        }, segment)
        covered = None
        if unit is None and extracted_checkpoint.get("source_coverage_version") == 1:
            covered = _complete_entity_role_source(
                candidate, segment, mentions_by_id, role.subject, role.object,
            )
            if covered is not None:
                unit = _validate_entity_unit(covered, segment)
        if unit is None:
            issues.append(_entity_unit_issue(candidate, segment, index))
            continue
        role = replace(role, unit_id=unit.id)
        if not _unit_identity_independent(unit, role, mentions_by_id, unresolved):
            issues.append(FormationIssue(
                "unit", index, "formation_identity_dependency_invalid", "pending",
            ))
            continue
        if unit.id in existing:
            if role != existing[unit.id]:
                issues.append(FormationIssue(
                    "unit", index, "formation_repair_role_conflict", "pending",
                ))
            continue
        dependencies = _entity_role_dependencies(role, mentions_by_id)
        if covered is not None:
            covered_positions.add(len(eligible))
        eligible.append((index, unit, role, dependencies))
    additions: list[tuple[GroundedMemoryUnit, GroundedUnitMentions]] = []
    if eligible:
        necessary = set().union(*(item[3] for item in eligible))
        mentions = tuple(mention for mention in base.mentions if mention.id in necessary)
        omitted = _source_coverage_over_budget(
            segment, [item[1] for item in eligible], [item[2] for item in eligible],
            mentions, covered_positions, dependencies=[item[3] for item in eligible],
        )
        for position in sorted(omitted):
            issues.append(FormationIssue(
                "unit", eligible[position][0], "formation_source_coverage_budget_exceeded", "pending",
            ))
        eligible = [item for position, item in enumerate(eligible) if position not in omitted]
    if eligible:
        necessary = set().union(*(item[3] for item in eligible))
        mentions = tuple(mention for mention in base.mentions if mention.id in necessary)
        verification = _entity_model_json(
            model,
            {
                **_entity_source_payload(segment),
                "units": [
                    {"candidate": asdict(unit), "roles": asdict(role)}
                    for _index, unit, role, _dependencies in eligible
                ],
                "mentions": [asdict(mention) for mention in mentions],
            },
            _ENTITY_VERIFICATION_PROMPT,
            error_code="formation_verification_failed",
        )
        unit_decisions, mention_decisions = _parse_entity_decisions(
            verification, len(eligible), len(mentions),
        )
        supported_mentions = {
            mention.id for mention, (supported, identity_supported)
            in zip(mentions, mention_decisions) if supported
        }
        rejected_identity = _rejected_identity_dependencies(
            mentions_by_id,
            unresolved | {
                mention.id for mention, (supported, identity_supported)
                in zip(mentions, mention_decisions) if not supported or not identity_supported
            },
        )
        for (index, unit, role, dependencies), supported in zip(eligible, unit_decisions):
            participating = {ref for ref in (role.subject, role.object, *role.mentions) if ref}
            identity_independent = _verified_unit_identity_independent(
                unit, role, mentions_by_id, rejected_identity,
            ) and not any(
                # Repair cannot demote a saved binding. A literal fact is safe
                # through an already-unresolved occurrence, not through a saved
                # positive identity which this new verification has rejected.
                mid in rejected_identity and mentions_by_id[mid].identity != "unresolved"
                for mid in participating
            )
            if not supported or not dependencies <= supported_mentions:
                issues.append(FormationIssue(
                    "unit", index,
                    "formation_semantic_rejected" if not supported
                    else "formation_mention_dependency_rejected", "rejected",
                ))
            elif not identity_independent:
                issues.append(FormationIssue(
                    "unit", index, "formation_identity_dependency_rejected", "rejected",
                ))
            elif unit.id not in existing:
                additions.append((unit, role))
                existing[unit.id] = role
            elif role != existing[unit.id]:
                issues.append(FormationIssue(
                    "unit", index, "formation_repair_role_conflict", "pending",
                ))
    original_source_issues = {
        issue for issue in base.issues
        if issue.candidate == "unit" and issue.index in resolved
        and issue.status == "pending" and issue.code == "formation_output_invalid"
    }
    issues = [
        replace(issue, status="repaired") if issue in original_source_issues else issue
        for issue in issues
    ]
    merged = GroundedMemoryBatch(
        base.units + tuple(unit for unit, _role in additions),
        base.mentions,
        base.unit_mentions + tuple(role for _unit, role in additions),
        tuple(dict.fromkeys(issues)),
    )
    if not validate_persisted_grounded_memory_batch(merged, segment):
        raise FormationError("formation_checkpoint_invalid")
    if checkpoint is not None:
        checkpoint("repair_verified", serialize_grounded_memory_batch(merged))
    return merged


def can_repair_grounded_memory_batch(
    segment: ColdDraftSegment, *, extracted_checkpoint: dict[str, Any],
    batch: GroundedMemoryBatch,
) -> bool:
    """Report the existing one-shot repair eligibility without calling a model."""
    return bool(_entity_source_repair_candidates(
        _entity_extraction_output(extracted_checkpoint, segment),
        _parse_entity_extraction(extracted_checkpoint, segment), batch, segment,
    ))


def _entity_source_repair_candidates(
    output: dict[str, Any], parsed: _EntityExtraction,
    base: GroundedMemoryBatch, segment: ColdDraftSegment,
) -> tuple[tuple[int, dict[str, Any], GroundedUnitMentions], ...]:
    """Keep only citation failures with independently valid bodies and roles."""
    pending = {
        issue.index for issue in base.issues if issue.candidate == "unit"
        and issue.code == "formation_output_invalid" and issue.status == "pending"
    }
    rejected = {
        issue.index for issue in base.issues
        if issue.candidate == "unit" and issue.status == "rejected"
    }
    verified_ids = {mention.id for mention in base.mentions}
    handles = {
        handle: mention_id for handle, mention_id in parsed.mention_handles
        if mention_id in verified_ids
    }
    turns = {turn.turn_id: turn for turn in segment.turns}
    required = {"text", "subject", "relation", "value", "source_refs"}
    allowed = required | {"referenced_time", "subject_mention", "object_mention", "mentions"}
    candidates: list[tuple[int, dict[str, Any], GroundedUnitMentions]] = []
    for index, item in enumerate(output["units"]):
        if (
            index not in pending or index in rejected
            or not isinstance(item, dict) or not required <= set(item)
            or set(item) - allowed
        ):
            continue
        refs = item["source_refs"]
        if (
            not isinstance(refs, list) or not refs or len(refs) > _MAX_TURNS
            or any(
                not isinstance(ref, dict) or set(ref) != {"turn_id", "supporting_span"}
                or not isinstance(ref["turn_id"], str) or ref["turn_id"] not in turns
                or not isinstance(ref["supporting_span"], str)
                for ref in refs
            )
            or _entity_refs(refs, segment) is not None
        ):
            continue
        participants = item.get("mentions", [])
        subject, object_ = item.get("subject_mention"), item.get("object_mention")
        if (
            not isinstance(participants, list)
            or not all(isinstance(ref, str) and ref in handles for ref in participants)
            or any(
                ref is not None and (not isinstance(ref, str) or ref not in handles)
                for ref in (subject, object_)
            )
        ):
            continue
        referenced_time = item.get("referenced_time")
        if referenced_time is not None and (
            not isinstance(referenced_time, str) or not referenced_time.strip()
            or len(referenced_time) > _MAX_FIELD_CHARS
        ):
            continue
        # A whole-turn structural probe is not semantic approval. It prevents
        # spending repair calls on non-atomic, unauthorized or impossible details.
        probe = _validate_entity_unit({
            **{key: value for key, value in item.items() if key not in {
                "subject_mention", "object_mention", "mentions",
            }},
            "source_refs": [
                {"turn_id": ref["turn_id"], "supporting_span": turns[ref["turn_id"]].content}
                for ref in refs
            ],
        }, segment)
        if probe is None:
            continue
        role = GroundedUnitMentions(
            probe.id, handles.get(subject), handles.get(object_),
            tuple(dict.fromkeys(handles[ref] for ref in participants)),
        )
        candidates.append((index, item, role))
    return tuple(candidates)


def _entity_role_dependencies(
    role: GroundedUnitMentions, mentions: dict[str, GroundedEntityMention],
) -> set[str]:
    needed = {ref for ref in (role.subject, role.object, *role.mentions) if ref}
    pending = list(needed)
    while pending:
        mention = mentions[pending.pop()]
        for target in (mention.same_as, *mention.distinct_from):
            if target is not None and target not in needed:
                needed.add(target)
                pending.append(target)
    return needed


def _entity_source_repair_output(
    raw: Any, indexes: tuple[int, ...], segment: ColdDraftSegment,
) -> dict[int, list[dict[str, str]]]:
    required = {"schema_version", "source_digest", "unit_indexes", "response"}
    if (
        not isinstance(raw, dict) or not required <= set(raw)
        or set(raw) - required - {"response_error"}
        or raw["schema_version"] != FORMATION_PROGRESS_VERSION
        or raw["source_digest"] != _entity_source_digest(segment)
        or not isinstance(raw["unit_indexes"], list)
        or not all(type(index) is int for index in raw["unit_indexes"])
        or raw["unit_indexes"] != list(indexes)
        or not isinstance(raw["response"], str) or len(raw["response"]) > _MAX_OUTPUT_CHARS
    ):
        raise FormationError("formation_checkpoint_invalid")
    if "response_error" in raw:
        if not isinstance(raw["response_error"], str) or raw["response_error"] not in {
            "formation_output_invalid", "formation_output_too_large",
        }:
            raise FormationError("formation_checkpoint_invalid")
        raise FormationError(raw["response_error"])
    output = _parse_entity_model_json(raw["response"], error_code="formation_repair_output_invalid")
    if (
        set(output) != {"repairs"} or not isinstance(output["repairs"], list)
        or len(output["repairs"]) != len(indexes)
    ):
        raise FormationError("formation_repair_output_invalid")
    repairs: dict[int, list[dict[str, str]]] = {}
    for item in output["repairs"]:
        if (
            not isinstance(item, dict) or set(item) != {"index", "source_refs"}
            or type(item["index"]) is not int or item["index"] not in indexes
            or item["index"] in repairs or not isinstance(item["source_refs"], list)
            or len(item["source_refs"]) > _MAX_TURNS
            or any(
                not isinstance(ref, dict) or set(ref) != {"turn_id", "supporting_span"}
                or not isinstance(ref["turn_id"], str) or not isinstance(ref["supporting_span"], str)
                or not ref["supporting_span"] for ref in item["source_refs"]
            )
        ):
            raise FormationError("formation_repair_output_invalid")
        repairs[item["index"]] = item["source_refs"]
    return repairs


def serialize_grounded_memory_batch(batch: GroundedMemoryBatch) -> dict[str, Any]:
    # Return a JSON-shaped object even before the owner's file roundtrip.
    return json.loads(json.dumps({
        "schema_version": FORMATION_PROGRESS_VERSION,
        "units": serialize_grounded_memory_units(batch.units),
        "mentions": [asdict(mention) for mention in batch.mentions],
        "unit_mentions": [asdict(role) for role in batch.unit_mentions],
        "issues": [asdict(issue) for issue in batch.issues],
    }, ensure_ascii=False))


def deserialize_grounded_memory_batch(raw: Any) -> GroundedMemoryBatch:
    if not isinstance(raw, dict):
        raise ValueError("formed_batch_invalid")
    legacy = raw.get("schema_version") == FORMATION_ENTITY_VERSION
    expected = {"schema_version", "units", "mentions", "unit_mentions"}
    if not legacy:
        expected.add("issues")
    if set(raw) != expected or raw["schema_version"] not in {
        FORMATION_ENTITY_VERSION, FORMATION_PROGRESS_VERSION,
    }:
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
        raw_issues = [] if legacy else raw["issues"]
        if not isinstance(raw_issues, list):
            raise ValueError()
        issues = tuple(FormationIssue(**item) for item in raw_issues)
        batch = GroundedMemoryBatch(units, mentions, roles, issues)
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
    response = _entity_model_response(model, payload, prompt, error_code=error_code)
    try:
        return _parse_entity_model_json(response, error_code=error_code)
    except FormationError as error:
        # Verification has no saved successful stage yet. Saved extraction and
        # repair receipts use the parser directly and cannot be resampled.
        raise FormationError(error.code, retryable=True) from None


def _entity_model_response(
    model: FormationModel, payload: dict[str, Any], prompt: str,
    *, error_code: str = "formation_failed",
) -> str:
    try:
        serialized_payload = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"),
        )
        if len(serialized_payload) > _MAX_OUTPUT_CHARS:
            raise FormationError("formation_verification_window_too_large")
        return model.generate(
            [], serialized_payload, system_prompt=prompt,
        )
    except FormationError:
        raise
    except Exception:
        raise FormationError(error_code, retryable=True) from None


def _parse_entity_model_json(
    raw: str, *, error_code: str = "formation_failed",
) -> dict[str, Any]:
    try:
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


def _complete_entity_role_source(
    item: dict[str, Any], segment: ColdDraftSegment,
    mentions: dict[str, GroundedEntityMention], subject: str | None, object_: str | None,
) -> dict[str, Any] | None:
    """Prepare missing literal role evidence, never invent a value or binding.

    Only previously detail-rejected candidates are eligible. Original valid
    facts keep their references and IDs; a new receipt opts into this parsing.
    The enlarged source must still pass the unchanged semantic/identity gates.
    """
    if _entity_unit_issue(item, segment, 0).code != "formation_detail_unsupported":
        return None
    refs = _entity_refs(item["source_refs"], segment)
    if refs is None or len({ref.turn_id for ref in refs}) != 1:
        return None
    turn = next(turn for turn in segment.turns if turn.turn_id == refs[0].turn_id)
    source = "\n".join(ref.supporting_span for ref in refs)
    missing = {
        detail for key in ("text", "subject", "relation", "value")
        for detail in _DETAIL.findall(item[key]) if detail not in source
    }
    role_details: set[str] = set()
    starts = [turn.content.index(ref.supporting_span) for ref in refs]
    ends = [start + len(ref.supporting_span) for start, ref in zip(starts, refs)]
    for mid, field in ((subject, item["subject"]), (object_, item["value"])):
        if mid is None:
            continue
        mention = mentions[mid]
        if (
            mention.identity not in {"named", "new"} or mention.same_as is not None
            or mention.turn_id != turn.turn_id or mention.surface not in field
        ):
            return None
        # Positions were validated by the mention parser. Do not find the first
        # surface again: repeated names can refer to different occurrences.
        starts.append(mention.source_start)
        ends.append(mention.source_end)
        role_details.update(_DETAIL.findall(mention.surface))
    if not missing or not missing <= role_details:
        return None
    span = turn.content[min(starts):max(ends)]
    if turn.content.count(span) != 1:
        return None
    return {
        **{key: value for key, value in item.items()
           if key not in {"subject_mention", "object_mention", "mentions"}},
        "source_refs": [{"turn_id": turn.turn_id, "supporting_span": span}],
    }


def _source_coverage_over_budget(
    segment: ColdDraftSegment, units, roles, mentions,
    covered_positions: set[int], *, dependencies: list[set[str]] | None = None,
) -> set[int]:
    """Reserve original eligible input, then fit new candidates in source order."""
    if not covered_positions:
        return set()
    items = [{"candidate": asdict(unit), "roles": asdict(role)}
             for unit, role in zip(units, roles)]
    original = [item for index, item in enumerate(items) if index not in covered_positions]
    # Initial formation verifies every mention independently. Repair verifies
    # only the dependency closure of its subset, including newly fitted facts.
    necessary = (
        {mention.id for mention in mentions} if dependencies is None
        else set().union(*(refs for index, refs in enumerate(dependencies)
                           if index not in covered_positions))
    )
    payload = {**_entity_source_payload(segment), "units": original,
               "mentions": [asdict(mention) for mention in mentions if mention.id in necessary]}
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    count = len(original)
    omitted: set[int] = set()
    for position in sorted(covered_positions):
        extra = len(json.dumps(items[position], ensure_ascii=False, separators=(",", ":"))) + bool(count)
        added_mentions = [
            asdict(mention) for mention in mentions
            if dependencies is not None
            and mention.id in dependencies[position] - necessary
        ]
        if added_mentions:
            extra += len(json.dumps(added_mentions, ensure_ascii=False, separators=(",", ":"))) - 2
            extra += bool(payload["mentions"])
        if size + extra > _MAX_OUTPUT_CHARS:
            omitted.add(position)
        else:
            size += extra
            count += 1
            payload["mentions"].extend(added_mentions)
            necessary.update(mention["id"] for mention in added_mentions)
    return omitted


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


def _entity_extraction_output(raw: Any, segment: ColdDraftSegment) -> dict[str, Any]:
    if (
        not isinstance(raw, dict)
        or raw.get("source_digest") != _entity_source_digest(segment)
    ):
        raise FormationError("formation_checkpoint_invalid")
    if raw.get("schema_version") == FORMATION_ENTITY_VERSION:
        if set(raw) != {"schema_version", "source_digest", "output"}:
            raise FormationError("formation_checkpoint_invalid")
        output = raw["output"]
    elif raw.get("schema_version") == FORMATION_PROGRESS_VERSION:
        if (
            not {"schema_version", "source_digest", "response"} <= set(raw)
            or set(raw) - {"schema_version", "source_digest", "response", "response_error", "source_coverage_version"}
            or not isinstance(raw["response"], str)
            or len(raw["response"]) > _MAX_OUTPUT_CHARS
            or ("source_coverage_version" in raw and (
                type(raw["source_coverage_version"]) is not int
                or raw["source_coverage_version"] != 1
            ))
        ):
            raise FormationError("formation_checkpoint_invalid")
        if "response_error" in raw:
            if raw["response_error"] not in {
                "formation_output_invalid", "formation_output_too_large",
            }:
                raise FormationError("formation_checkpoint_invalid")
            raise FormationError(raw["response_error"])
        output = _parse_entity_model_json(raw["response"])
    else:
        raise FormationError("formation_checkpoint_invalid")
    if output == {"error": "formation_output_too_large"}:
        raise FormationError("formation_output_too_large")
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
    return output


def _parse_entity_extraction(raw: Any, segment: ColdDraftSegment) -> _EntityExtraction:
    """Isolate malformed proposals and only their actual identity/role dependencies."""
    output = _entity_extraction_output(raw, segment)
    turns = {turn.turn_id: turn for turn in segment.turns}
    issues: list[FormationIssue] = []
    handles: dict[str, str] = {}
    parsed: dict[int, tuple[dict[str, Any], GroundedEntityMention]] = {}
    invalid_identity: set[str] = set()
    handle_counts: dict[str, int] = {}
    for item in output["mentions"]:
        if isinstance(item, dict) and isinstance(item.get("handle"), str):
            handle = item["handle"]
            handle_counts[handle] = handle_counts.get(handle, 0) + 1
    mention_required = {
        "handle", "surface", "turn_id", "identity",
    }
    mention_allowed = mention_required | {
        "occurrence", "source_start", "source_end",
        "same_as", "distinct_from", "identity_source_refs",
    }
    for index, item in enumerate(output["mentions"]):
        if (
            not isinstance(item, dict) or not mention_required <= set(item)
            or set(item) - mention_allowed
        ):
            issues.append(FormationIssue("mention", index, "formation_output_invalid", "pending"))
            continue
        handle = item["handle"]
        if not isinstance(handle, str) or not handle or handle_counts[handle] != 1:
            issues.append(FormationIssue("mention", index, "formation_mention_handle_invalid", "pending"))
            continue
        turn_id, surface = item["turn_id"], item["surface"]
        if not isinstance(turn_id, str) or turn_id not in turns:
            issues.append(FormationIssue("mention", index, "formation_mention_source_invalid", "pending"))
            continue
        try:
            start, end = _locate_entity_mention(item, turns[turn_id].content)
        except FormationError as error:
            issues.append(FormationIssue("mention", index, error.code, "pending"))
            continue
        mention_id = _mention_id(segment, turn_id, start, end)
        identity = item["identity"]
        if not isinstance(identity, str) or identity not in {"named", "current_user", "new", "unresolved"}:
            identity = "unresolved"
            invalid_identity.add(mention_id)
            issues.append(FormationIssue("identity", index, "formation_identity_invalid", "pending"))
        refs = _entity_refs(
            item.get("identity_source_refs", []), segment,
            allow_empty=True, expand_repeated=True,
        )
        if refs is None:
            refs = ()
            invalid_identity.add(mention_id)
            issues.append(FormationIssue("identity", index, "formation_mention_source_invalid", "pending"))
        handles[handle] = mention_id
        parsed[index] = (item, GroundedEntityMention(
            mention_id, surface, turn_id, start, end, turns[turn_id].role,
            identity, identity_source_refs=refs,
        ))
    mentions: dict[str, GroundedEntityMention] = {}
    conflicting_positions: set[str] = set()
    for index, (item, mention) in parsed.items():
        same = item.get("same_as")
        distinct = item.get("distinct_from", [])
        if (
            same is not None and (not isinstance(same, str) or same not in handles)
            or not isinstance(distinct, list)
            or not all(isinstance(ref, str) and ref in handles for ref in distinct)
        ):
            invalid_identity.add(mention.id)
            issues.append(FormationIssue("identity", index, "formation_mention_link_invalid", "pending"))
            # Retain valid constraints for dependency propagation, never binding.
            same = same if isinstance(same, str) and same in handles else None
            distinct = [ref for ref in distinct if isinstance(ref, str) and ref in handles] if isinstance(distinct, list) else []
        if (same is not None or distinct or mention.identity in {"current_user", "new"}) and not mention.identity_source_refs:
            invalid_identity.add(mention.id)
            issues.append(FormationIssue("identity", index, "formation_mention_source_invalid", "pending"))
        mention = replace(mention, same_as=handles.get(same), distinct_from=tuple(dict.fromkeys(handles[ref] for ref in distinct)))
        if mention.id in mentions and mentions[mention.id] != mention:
            conflicting_positions.add(mention.id)
        mentions[mention.id] = mention
    if conflicting_positions:
        for index, (_item, mention) in parsed.items():
            if mention.id in conflicting_positions:
                issues.append(FormationIssue("mention", index, "formation_mention_position_conflict", "pending"))
        mentions = {mid: mention for mid, mention in mentions.items() if mid not in conflicting_positions}
        handles = {handle: mid for handle, mid in handles.items() if mid in mentions}
    invalid_identity = _invalid_identity_dependencies(mentions, invalid_identity)
    for index, (_item, mention) in parsed.items():
        if mention.id in mentions and mention.id in invalid_identity and not any(
            issue.candidate == "identity" and issue.index == index
            for issue in issues
        ):
            issues.append(FormationIssue("identity", index, "formation_identity_dependency_invalid", "pending"))
    mentions = {
        mid: replace(mention, identity="unresolved", same_as=None,
                     distinct_from=(), identity_source_refs=())
        if mid in invalid_identity else mention
        for mid, mention in mentions.items()
    }
    units: list[GroundedMemoryUnit] = []
    roles: list[GroundedUnitMentions] = []
    unit_indexes: list[int] = []
    covered_positions: set[int] = set()
    unit_required = {"text", "subject", "relation", "value", "source_refs"}
    unit_allowed = unit_required | {
        "referenced_time", "subject_mention", "object_mention", "mentions",
    }
    for index, item in enumerate(output["units"]):
        if (
            not isinstance(item, dict) or not unit_required <= set(item)
            or set(item) - unit_allowed
        ):
            issues.append(FormationIssue("unit", index, "formation_output_invalid", "pending"))
            continue
        if not all(
            isinstance(item[key], str) and item[key].strip()
            and len(item[key]) <= _MAX_FIELD_CHARS
            for key in ("text", "subject", "relation", "value")
        ) or _entity_refs(item["source_refs"], segment) is None:
            issues.append(FormationIssue("unit", index, "formation_output_invalid", "pending"))
            continue
        if item.get("referenced_time") is not None and (
            not isinstance(item["referenced_time"], str)
            or not item["referenced_time"].strip()
            or len(item["referenced_time"]) > _MAX_FIELD_CHARS
        ):
            issues.append(FormationIssue("unit", index, "formation_referenced_time_invalid", "pending"))
            continue
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
            issues.append(FormationIssue("unit", index, "formation_mention_dependency_invalid", "pending"))
            continue
        unit = _validate_entity_unit({key: value for key, value in item.items() if key not in {"subject_mention", "object_mention", "mentions"}}, segment)
        covered = None
        if unit is None and raw.get("source_coverage_version") == 1:
            covered = _complete_entity_role_source(
                item, segment, mentions, handles.get(subject_handle), handles.get(object_handle),
            )
            if covered is not None:
                unit = _validate_entity_unit(covered, segment)
        if unit is None:
            issues.append(_entity_unit_issue(item, segment, index))
            continue
        role = GroundedUnitMentions(
            unit.id, handles.get(subject_handle), handles.get(object_handle),
            tuple(dict.fromkeys(handles[ref] for ref in participant_handles)),
        )
        if not _unit_identity_independent(unit, role, mentions, invalid_identity):
            issues.append(FormationIssue("unit", index, "formation_identity_dependency_invalid", "pending"))
            continue
        if covered is not None:
            covered_positions.add(len(units))
        units.append(unit)
        roles.append(role)
        unit_indexes.append(index)
    turn_order = {turn.turn_id: index for index, turn in enumerate(segment.turns)}
    ordered_mentions = tuple(sorted(
        mentions.values(),
        key=lambda mention: (turn_order[mention.turn_id], mention.source_start, mention.source_end),
    ))
    omitted = _source_coverage_over_budget(
        segment, units, roles, ordered_mentions, covered_positions,
    )
    for position in sorted(omitted):
        issues.append(FormationIssue(
            "unit", unit_indexes[position], "formation_source_coverage_budget_exceeded", "pending",
        ))
    units = [unit for position, unit in enumerate(units) if position not in omitted]
    roles = [role for position, role in enumerate(roles) if position not in omitted]
    unit_indexes = [index for position, index in enumerate(unit_indexes) if position not in omitted]
    batch = GroundedMemoryBatch(
        tuple(units), ordered_mentions, tuple(roles), tuple(dict.fromkeys(issues)),
    )
    if not _entity_batch_links_valid(batch, allow_duplicate_units=True):
        raise FormationError("formation_mention_link_invalid")
    indexes_by_id: dict[str, int] = {}
    for index, (_item, mention) in parsed.items():
        indexes_by_id.setdefault(mention.id, index)
    return _EntityExtraction(
        batch, tuple(unit_indexes),
        tuple(indexes_by_id[mention.id] for mention in ordered_mentions),
        tuple(handles.items()),
    )


def _invalid_identity_dependencies(
    mentions: dict[str, GroundedEntityMention], invalid: set[str],
) -> set[str]:
    invalid = set(invalid)
    for mention in mentions.values():
        if (
            mention.same_as is not None and mention.same_as not in mentions
            or any(ref not in mentions for ref in mention.distinct_from)
            or mention.id in mention.distinct_from
            or mention.same_as in mention.distinct_from
        ):
            invalid.add(mention.id)
        seen: set[str] = set()
        current = mention.id
        while current in mentions:
            if current in seen:
                invalid.update(seen)
                break
            seen.add(current)
            current = mentions[current].same_as
    while True:
        previous = set(invalid)
        for mention in mentions.values():
            if mention.same_as in invalid or any(ref in invalid for ref in mention.distinct_from):
                invalid.add(mention.id)
            if mention.id in invalid:
                # DISTINCT_FROM is a symmetric anti-merge constraint.
                invalid.update(mention.distinct_from)
        if invalid == previous:
            return invalid


def _directed_identity_dependencies(
    mentions: dict[str, GroundedEntityMention], invalid: set[str],
) -> set[str]:
    """New protocols invalidate claimants and dependents, not independent targets.

    A failed negative assertion is not positive equality evidence. Callers
    withhold failed identities rather than retrying name binding. Historical
    v2 parsing retains its checkpoint semantics.
    """
    invalid = set(invalid)
    for mention in mentions.values():
        if (mention.same_as is not None and mention.same_as not in mentions
                or any(ref not in mentions for ref in mention.distinct_from)
                or mention.id in mention.distinct_from
                or mention.same_as in mention.distinct_from):
            invalid.add(mention.id)
    return _rejected_identity_dependencies(mentions, invalid)


def _rejected_identity_dependencies(
    mentions: dict[str, GroundedEntityMention], rejected: set[str],
) -> set[str]:
    """Close final rejected claims over their local directed dependencies.

    A same-as class cannot also be distinct from itself. Reject the conflicting
    assertion and its dependents, without invalidating unrelated co-occurrences
    or the target's independent identity. This does not revisit saved batches.
    """
    invalid = set(rejected)
    roots: dict[str, str | None] = {}
    for mid in mentions:
        seen: set[str] = set()
        current = mid
        while current in mentions and current not in seen:
            seen.add(current)
            parent = mentions[current].same_as
            if parent is None:
                break
            current = parent
        else:
            current = None
        roots[mid] = current
    for mention in mentions.values():
        if roots[mention.id] is None or any(
            roots[mention.id] == roots.get(target)
            for target in mention.distinct_from
        ):
            invalid.add(mention.id)
    while True:
        previous = len(invalid)
        for mention in mentions.values():
            if any(
                target is not None and (target not in mentions or target in invalid)
                for target in (mention.same_as, *mention.distinct_from)
            ):
                invalid.add(mention.id)
        if len(invalid) == previous:
            return invalid


def _entity_unit_issue(raw: dict[str, Any], segment: ColdDraftSegment, index: int) -> FormationIssue:
    source = "\n".join(ref["supporting_span"] for ref in raw["source_refs"])
    turns = {turn.turn_id: turn for turn in segment.turns}
    if len([part for part in _ATOMIC_BOUNDARY.split(raw["text"]) if part.strip()]) > 1:
        return FormationIssue("unit", index, "formation_unit_non_atomic", "pending")
    if raw.get("referenced_time") is not None and raw["referenced_time"] not in source:
        return FormationIssue("unit", index, "formation_referenced_time_invalid", "pending")
    if not any(turns[ref["turn_id"]].role == "user" for ref in raw["source_refs"]):
        return FormationIssue("unit", index, "formation_role_unsupported", "rejected")
    if any(
        detail not in source for key in ("text", "subject", "relation", "value")
        for detail in _DETAIL.findall(raw[key])
    ):
        return FormationIssue("unit", index, "formation_detail_unsupported", "rejected")
    return FormationIssue("unit", index, "formation_unit_invalid", "pending")


def _unit_identity_independent(
    unit: GroundedMemoryUnit, role: GroundedUnitMentions,
    mentions: dict[str, GroundedEntityMention], invalid_identity: set[str],
) -> bool:
    """A failed identity cannot supply missing names to an otherwise valid fact.

    Literal participation remains eligible for the unchanged semantic verifier;
    the mention is unresolved and cannot create a positive entity binding.
    """
    source = "\n".join(ref.supporting_span for ref in unit.source_refs)
    for ref, field in ((role.subject, unit.subject), (role.object, unit.value)):
        if ref in invalid_identity:
            surface = mentions[ref].surface
            if surface not in source or surface not in field:
                return False
    for ref in role.mentions:
        if ref in invalid_identity:
            surface = mentions[ref].surface
            if surface not in source or surface not in unit.text:
                return False
    return True


def _verified_unit_identity_independent(
    unit: GroundedMemoryUnit, role: GroundedUnitMentions,
    mentions: dict[str, GroundedEntityMention], invalid_identity: set[str],
) -> bool:
    """After an identity veto, retain only independently literal propositions.

    Surface overlap alone admits e.g. ``She (Nadia)`` after ``She -> Nadia``
    was rejected. Check the whole proposition and its literal arguments; the
    unchanged verifier cannot override its own rejected identity dependency.
    Parsing still uses the original eligibility check before verification.
    """
    if not _unit_identity_independent(unit, role, mentions, invalid_identity):
        return False
    participating = {ref for ref in (role.subject, role.object, *role.mentions) if ref}
    if not participating & invalid_identity:
        return True
    return (
        any(unit.text in ref.supporting_span for ref in unit.source_refs)
        and unit.subject in unit.text and unit.value in unit.text
    )


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
        raise FormationError("formation_verification_failed", retryable=True)
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
        raise FormationError("formation_verification_failed", retryable=True)
    return (
        tuple(item["supported"] for item in raw["units"]),
        tuple((item["supported"], item["identity_supported"]) for item in raw["mentions"]),
    )


def _entity_batch_links_valid(batch: GroundedMemoryBatch, *, allow_duplicate_units: bool = False) -> bool:
    if not _formation_issues_valid(batch.issues):
        return False
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


def _formation_issues_valid(issues: tuple[FormationIssue, ...]) -> bool:
    if not isinstance(issues, tuple) or len(issues) > 4 * (_MAX_MENTIONS + _MAX_ENTITY_UNITS):
        return False
    for issue in issues:
        if (
            not isinstance(issue, FormationIssue)
            or issue.candidate not in {"unit", "mention", "identity"}
            or type(issue.index) is not int
            or not 0 <= issue.index < (
                _MAX_ENTITY_UNITS if issue.candidate == "unit" else _MAX_MENTIONS
            )
            or not isinstance(issue.code, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", issue.code) is None
            or issue.status not in {"pending", "rejected", "repaired"}
        ):
            return False
    return len(set(issues)) == len(issues)


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
