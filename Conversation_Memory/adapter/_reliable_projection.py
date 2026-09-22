"""Optional, independently authorized structure for reliable Formation v4."""
from __future__ import annotations

from dataclasses import replace

from . import grounded_formation as gf
from .body_payload import FORMATION_BODY_VERSION
from .entity_consolidation import stable_entity_ref
from .user_self import CURRENT_USER_ENTITY_REF
from .reliable_formation import (
    FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6,
    digest,
)


G1_PROMPT = """G1: Propose OPTIONAL graph structure for the already accepted canonical conversational statements; independently identify entity occurrences in the current source window.
Return strict JSON only:
{"mentions":[{"handle":str,"surface":str,"turn_id":str,"occurrence":int,"identity":"new"|"named"|"current_user"|"unresolved","same_as":str|null,"distinct_from":[str],"identity_source_ids":[str],"existing_entity_ref":str|null}],
"projections":[{"fact_id":str,"relation":{"subject":str,"predicate":str,"object":str,"subject_mention":str|null,"object_mention":str|null}|null,"mentions":[str],"referenced_time":{"text":str,"turn_id":str}|null}]}.
Exactly one projection row per accepted fact ID; relation may be null. Do not alter or create Facts. At most 192 mentions. Mention surface is an exact substring in the named source turn; occurrence is its zero-based exact occurrence number. Source IDs are actual current turn IDs.
Entity identity and event role are different assertions. For a genuinely introduced entity use new. For a repeated local identity use same_as with an earlier handle and explicit source evidence; do not infer equality merely from spelling. current_user denotes the dialogue user, not the assistant. named REQUIRES an existing_entity_ref from the supplied prior_identities and positive source evidence of this continuation; prior same-name occurrences alone do not prove it. Otherwise use unresolved. distinct_from needs actual evidence, not merely two different mention handles. Prior identity evidence is only for identity, never new Fact truth.
Relations project the CONTENT AS REPORTED by the canonical Fact, retaining its negation/state/scope; they are navigation assertions, not external verification. Only choose a safe binary pairing; otherwise relation=null. Its subject/object mention must denote the actual corresponding argument, not the speaker, owner, recipient or nearby noun unless that is the chosen predicate argument. E.g. 'Jo returned the user's can' may project Jo--returned--the can; 'my' names an owner, not the returned object. The owner can independently be an ordinary participant. Do not replace names with mention handles in semantic strings or use placeholder predicates/boolean values.
For each Fact, relation participants must have occurrences in that Fact's allowed source package. Ordinary mentions are independently relevant entities, not every co-occurrence in the window. Optional referenced_time is an exact phrase from a source turn in that package; leave null when not needed. Never turn proposal/unanswered/tentative into accepted/completed, and never project assistant assertions as user confirmations. Discovery of a source mention does not assert any event happened."""

G2_PROMPT = """G2: Independently authorize the frozen proposed graph assertions. Never rewrite them or alter F2 text judgments.
Return strict JSON only:
{"mentions":[{"handle":str,"occurrence_supported":bool,"identity_supported":bool}],
"projections":[{"fact_id":str,"relation_supported":bool,"subject_role_supported":bool,"object_role_supported":bool,"mention_support":[bool],"time_supported":bool}]}.
Exactly one decision per supplied mention handle and projection fact ID. mention_support matches the exact ordinary-mention list order. False rejects only that assertion; empty optional structure needs no permission.
For each relation assess the WHOLE subject--predicate--object proposition AS REPORTED in the accepted canonical Fact, with its actual source package and source attribution. Then verify each role mention really denotes THAT predicate argument. A possessive user in 'my watering can' is the owner, not the object of 'returned'; a person receiving a returned cart is not automatically that cart. Conversely a source-grounded cart occurrence should retain the correct cart object role. A meeting location is a legitimate ordinary participant if supported, not the object of agreement merely because it occurs nearby. Do not remove all roles as a shortcut.
Evaluate ordinary entity participation separately from subject/object roles. A supported source occurrence can survive in a question or hypothesis without authorizing its event. Verify identities using their declared source_ids and, only for explicit existing_entity_ref, the supplied prior occurrence evidence. Same spelling alone is insufficient for identity reuse; invented distinct_from must fail its claimant without invalidating an independent target. current_user is the dialogue user; quoted or assistant first-person is not that user by default. Reject unsupported same_as, disambiguation and role claims.
Use each Fact's allowed_source_ids; visibility elsewhere is not authorization. Check negation, conditions, tentative/proposal, unanswered, acceptance, completion and ownership as semantics, not keywords. Approved body text does NOT approve structure automatically. Time needs an exact supported expression and correct referenced turn. No source or model output proves an external execution merely because someone said it."""

G1_PROMPT_V5 = G1_PROMPT.replace(
    "Source IDs are actual current turn IDs.",
    "Source IDs are actual current turn IDs. identity_source_ids is REQUIRED for same_as and "
    "named claims and OPTIONAL for new and current_user claims; every listed ID must be an "
    "actual current window turn ID.")

G2_PROMPT_V5 = G2_PROMPT.replace(
    "Do not remove all roles as a shortcut.",
    "Do not remove all roles as a shortcut. Rejecting a relation voids its subject and object "
    "role votes. Code-enforced evidence and role rules are not subject to your vote: a "
    "syntactically invalid identity stays unresolved regardless of identity_supported.")

# Closed-class first-person surfaces for the v5 current_user role backstop.
_FIRST_PERSON_SELF_SURFACES = frozenset({
    "i", "me", "my", "mine", "myself", "we", "us", "our", "ours",
    "我", "我们", "俺", "咱们", "咱",
})


def _version_tag(version):
    if version == FORMATION_BODY_VERSION:
        return "v7"
    if version == FORMATION_RELIABLE_VERSION_V6:
        return "v6"
    return "v5" if version == FORMATION_RELIABLE_VERSION_V5 else "v4"


def reliable_unit_id(segment, fact, *, version=FORMATION_RELIABLE_VERSION):
    return "grounded_memory_" + _version_tag(version) + ":" + digest(
        [version, gf._entity_source_digest(segment), fact])


def _refs(ids, turns):
    if not isinstance(ids, list) or not ids or not all(isinstance(t, str) and t in turns for t in ids):
        return None
    return tuple(gf.SourceRef(tid, turns[tid].content) for tid in turns if tid in ids)


def _g2_evidence_deferrable(mention, by_handle):
    # The G1 prompt requires identity evidence only for same_as and named,
    # while parse_g1 voids every kind without it. A current_user/new mention
    # whose sole defect is that omission keeps its recorded G2 vote as the
    # identity authorizer; every other syntax rule still applies.
    return (not mention["identity_syntax_valid"]
            and mention["identity"] in {"current_user", "new"}
            and not mention["identity_source_ids"]
            and (mention["same_as"] is None
                 or (isinstance(mention["same_as"], str) and mention["same_as"] in by_handle))
            and isinstance(mention["distinct_from"], list)
            and all(isinstance(x, str) and x in by_handle for x in mention["distinct_from"])
            and mention["existing_entity_ref"] is None)


def parse_g1(raw, segment, facts, prior, *, version=FORMATION_RELIABLE_VERSION):
    if (not isinstance(raw, dict) or set(raw) != {"mentions", "projections"}
            or not isinstance(raw["mentions"], list) or len(raw["mentions"]) > gf._MAX_MENTIONS
            or not isinstance(raw["projections"], list) or len(raw["projections"]) > gf._MAX_ENTITY_UNITS):
        raise gf.FormationError("reliable_g1_output_invalid")
    turns = {t.turn_id: t for t in segment.turns}; issues = []; mentions = []; handles = set(); positions = set()
    v5 = version in {FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6, FORMATION_BODY_VERSION}
    prior_refs = {r["entity_ref"] for r in prior if isinstance(r, dict) and isinstance(r.get("entity_ref"), str)}
    required = {"handle", "surface", "turn_id", "occurrence", "identity", "same_as", "distinct_from", "identity_source_ids", "existing_entity_ref"}
    for index, m in enumerate(raw["mentions"]):
        if (not isinstance(m, dict) or set(m) != required or not isinstance(m["handle"], str) or not m["handle"]
                or m["handle"] in handles or not isinstance(m["turn_id"], str) or m["turn_id"] not in turns):
            issues.append({"index": index, "code": "reliable_mention_invalid"}); continue
        try:
            start, end = gf._locate_entity_mention(m, turns[m["turn_id"]].content)
        except (gf.FormationError, KeyError, TypeError):
            issues.append({"index": index, "code": "reliable_mention_position_invalid"}); continue
        mid = "mention_" + _version_tag(version) + ":" + digest([version, segment.conversation_id, m["turn_id"], start, end])
        if mid in positions:
            # A duplicate source occurrence never creates a second identity.
            issues.append({"index": index, "code": "reliable_mention_duplicate"}); continue
        positions.add(mid); handles.add(m["handle"])
        refs = _refs(m["identity_source_ids"], turns)
        identity = m["identity"]
        if v5:
            # Missing evidence (empty) is contract-valid for new/current_user;
            # a listed ID that names no current turn is invalid for every kind.
            ids = m["identity_source_ids"]
            evidence_ok = (refs is not None if m["same_as"] is not None or identity not in {"new", "current_user"}
                           else isinstance(ids, list) and (not ids or refs is not None))
        else:
            evidence_ok = refs is not None
        valid = (isinstance(identity, str) and identity in {"new", "named", "current_user", "unresolved"}
                 and evidence_ok and (m["same_as"] is None or isinstance(m["same_as"], str))
                 and isinstance(m["distinct_from"], list) and all(isinstance(x, str) for x in m["distinct_from"])
                 and (m["existing_entity_ref"] is None or isinstance(m["existing_entity_ref"], str)))
        if valid and identity == "named" and not m["same_as"]:
            valid = m["existing_entity_ref"] in prior_refs
        if valid and m["existing_entity_ref"] is not None and (identity != "named" or m["same_as"] is not None):
            valid = False
        if (v5 and identity == "current_user" and turns[m["turn_id"]].role != "user"
                and isinstance(m["surface"], str)
                and m["surface"].strip().casefold() in _FIRST_PERSON_SELF_SURFACES):
            # A first-person surface on a non-user turn cannot be the dialogue
            # user; no G2 vote can override this code-determinable conflict.
            valid = False
            issues.append({"index": index, "code": "reliable_identity_self_reference_role_conflict"})
        mentions.append({**m, "mention_id": mid, "source_start": start, "source_end": end,
                         "source_role": turns[m["turn_id"]].role, "identity_syntax_valid": valid,
                         "identity_source_ids": list(dict.fromkeys(m["identity_source_ids"])) if refs else []})
    by_handle = {m["handle"]: m for m in mentions}
    for m in mentions:
        if (m["same_as"] is not None and (not isinstance(m["same_as"], str) or m["same_as"] not in by_handle)
                or not isinstance(m["distinct_from"], list)
                or any(not isinstance(x, str) or x not in by_handle for x in m["distinct_from"])):
            m["identity_syntax_valid"] = False
    by_fact = {f["fact_id"]: f for f in facts}; projections = {}; required_p = {"fact_id", "relation", "mentions", "referenced_time"}
    for index, p in enumerate(raw["projections"]):
        if (not isinstance(p, dict) or set(p) != required_p or not isinstance(p["fact_id"], str)
                or p["fact_id"] not in by_fact or p["fact_id"] in projections):
            raise gf.FormationError("reliable_g1_projection_manifest_invalid")
        allowed = by_fact[p["fact_id"]]["allowed_source_ids"]
        in_package = lambda handle: isinstance(handle, str) and handle in by_handle and by_handle[handle]["turn_id"] in allowed
        relation = p["relation"]
        if relation is not None:
            valid = (isinstance(relation, dict) and set(relation) == {"subject", "predicate", "object", "subject_mention", "object_mention"}
                     and all(isinstance(relation[k], str) and relation[k].strip() and len(relation[k]) <= gf._MAX_FIELD_CHARS
                             and relation[k] not in by_handle for k in ("subject", "predicate", "object")))
            if not valid:
                relation = None; issues.append({"index": index, "code": "reliable_relation_syntax_withheld"})
            else:
                relation = dict(relation)
                for key in ("subject_mention", "object_mention"):
                    if not in_package(relation[key]):relation[key] = None
        participants = p["mentions"]
        if not isinstance(participants, list) or not all(isinstance(h, str) for h in participants):
            participants = []; issues.append({"index": index, "code": "reliable_participants_syntax_withheld"})
        participants = list(dict.fromkeys(h for h in participants if in_package(h)))
        time = p["referenced_time"]
        if time is not None and not (isinstance(time, dict) and set(time) == {"text", "turn_id"}
              and isinstance(time["turn_id"], str) and time["turn_id"] in allowed
              and isinstance(time["text"], str) and bool(time["text"])
              and turns[time["turn_id"]].content.count(time["text"]) == 1):
            time = None; issues.append({"index": index, "code": "reliable_time_syntax_withheld"})
        projections[p["fact_id"]] = {"fact_id": p["fact_id"], "relation": relation, "mentions": participants, "referenced_time": time}
    if set(projections) != set(by_fact):
        raise gf.FormationError("reliable_g1_projection_manifest_invalid")
    parsed = {"mentions": mentions, "projections": [projections[f["fact_id"]] for f in facts], "issues": issues}
    return parsed, {"mentions": len(mentions), "projections": len(projections), "syntax_withheld": len(issues)}


def parse_g2(raw, proposed, *, version=FORMATION_RELIABLE_VERSION):
    if not isinstance(raw, dict) or set(raw) != {"mentions", "projections"}:
        raise gf.FormationError("reliable_g2_output_invalid")
    if not isinstance(raw["mentions"], list) or not isinstance(raw["projections"], list):
        raise gf.FormationError("reliable_g2_output_invalid")
    mm = {m["handle"]: m for m in proposed["mentions"]}; pp = {p["fact_id"]: p for p in proposed["projections"]}
    mentions, projections = {}, {}
    for d in raw["mentions"]:
        if (not isinstance(d, dict) or set(d) != {"handle", "occurrence_supported", "identity_supported"}
                or not isinstance(d["handle"], str) or d["handle"] not in mm or d["handle"] in mentions
                or any(type(d[k]) is not bool for k in ("occurrence_supported", "identity_supported"))):
            raise gf.FormationError("reliable_g2_authorization_invalid")
        mentions[d["handle"]] = d
    for d in raw["projections"]:
        if (not isinstance(d, dict) or set(d) != {"fact_id", "relation_supported", "subject_role_supported", "object_role_supported", "mention_support", "time_supported"}
                or not isinstance(d["fact_id"], str) or d["fact_id"] not in pp or d["fact_id"] in projections
                or any(type(d[k]) is not bool for k in ("relation_supported", "subject_role_supported", "object_role_supported", "time_supported"))
                or not isinstance(d["mention_support"], list) or len(d["mention_support"]) != len(pp[d["fact_id"]]["mentions"])
                or any(type(v) is not bool for v in d["mention_support"])):
            raise gf.FormationError("reliable_g2_authorization_invalid")
        projections[d["fact_id"]] = d
    if set(mentions) != set(mm) or set(projections) != set(pp):
        raise gf.FormationError("reliable_g2_authorization_invalid")
    result = {"mentions": [mentions[m["handle"]] for m in proposed["mentions"]],
              "projections": [projections[p["fact_id"]] for p in proposed["projections"]]}
    return result, {"identities_supported": sum(d["identity_supported"] for d in mentions.values()),
                    "relations_supported": sum(d["relation_supported"] for d in projections.values())}


def authorized_batch(segment, facts, proposed, decisions, *, version=FORMATION_RELIABLE_VERSION):
    turns = {t.turn_id: t for t in segment.turns}; mm = {m["handle"]: m for m in proposed["mentions"]}
    handles = {h: m["mention_id"] for h, m in mm.items()}; mentions = {}; invalid = set(); existing = {}; deferred = []
    v5 = version in {FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6, FORMATION_BODY_VERSION}
    for index, (m, d) in enumerate(zip(proposed["mentions"], decisions["mentions"])):
        mid = m["mention_id"]
        if not d["occurrence_supported"]:
            invalid.add(mid);continue
        # The v4 evidence-omission repair is itself declared valid by the v5
        # contract, so the deferral path applies to frozen v4 parses only.
        deferrable = not v5 and d["identity_supported"] and _g2_evidence_deferrable(m, mm)
        if deferrable:
            deferred.append(index)
        same = handles.get(m["same_as"]) if isinstance(m["same_as"], str) else None
        distinct = tuple(handles[h] for h in m["distinct_from"] if isinstance(h, str) and h in handles) if isinstance(m["distinct_from"], list) else ()
        mentions[mid] = gf.GroundedEntityMention(mid, m["surface"], m["turn_id"], m["source_start"], m["source_end"],
                m["source_role"], m["identity"] if m["identity_syntax_valid"] or deferrable else "unresolved", same, distinct,
                _refs(m["identity_source_ids"], turns) or ())
        if not (m["identity_syntax_valid"] or deferrable) or not d["identity_supported"] or m["identity"] == "unresolved":invalid.add(mid)
        if m["existing_entity_ref"] is not None:existing[mid] = m["existing_entity_ref"]
    invalid = gf._directed_identity_dependencies(mentions, invalid)
    # Local negative constraints may also contradict two separately verified
    # references to the same persisted identity. Reject only the claimant;
    # never let binder failure discard independently accepted body text.
    def prospective_ref(mid):
        while mid in mentions and mid not in invalid:
            mention = mentions[mid]
            if mention.same_as:
                mid = mention.same_as
                continue
            if mention.identity == "current_user":return CURRENT_USER_ENTITY_REF
            if mention.identity == "new":return stable_entity_ref(mid)
            return existing.get(mid)
        return None
    conflicts = {mid for mid, m in mentions.items() if mid not in invalid
                 and prospective_ref(mid) is not None
                 and any(prospective_ref(mid) == prospective_ref(other) for other in m.distinct_from)}
    invalid = gf._directed_identity_dependencies(mentions, invalid | conflicts)
    mentions = {mid: replace(m, identity="unresolved", same_as=None, distinct_from=(), identity_source_refs=()) if mid in invalid else m
                for mid, m in mentions.items()}
    existing = {mid: ref for mid, ref in existing.items() if mid in mentions and mid not in invalid}
    units, roles, context = [], [], {"origins": {}, "times": {}, "fact_ids": {}, "explicit_identity_refs": existing}
    for f, p, d in zip(facts, proposed["projections"], decisions["projections"]):
        refs = tuple(gf.SourceRef(tid, turns[tid].content) for tid in f["allowed_source_ids"])
        uid = reliable_unit_id(segment, f, version=version)
        relation = p["relation"] if d["relation_supported"] else None
        def authorized_handle(handle):
            mid = handles.get(handle)
            return mid if mid in mentions and mid not in invalid else None
        if v5 and not d["relation_supported"]:
            # A rejected relation voids its role votes; the manifest cannot
            # keep a subject/object edge without its relation.
            subject = obj = None
        else:
            subject = authorized_handle(relation["subject_mention"]) if relation and d["subject_role_supported"] else None
            obj = authorized_handle(relation["object_mention"]) if relation and d["object_role_supported"] else None
        participants = tuple(dict.fromkeys(mid for h, allowed in zip(p["mentions"], d["mention_support"])
                                          if allowed and (mid := authorized_handle(h))))
        time = p["referenced_time"] if d["time_supported"] else None
        units.append(gf.GroundedMemoryUnit(uid, f["text"], relation["subject"] if relation else None,
                 relation["predicate"] if relation else None, relation["object"] if relation else None,
                 refs, version, time["text"] if time else None))
        roles.append(gf.GroundedUnitMentions(uid, subject, obj, participants))
        context["origins"][uid] = f["origin_turn_id"];context["times"][uid] = time;context["fact_ids"][uid] = f["fact_id"]
    issues = tuple(gf.FormationIssue("projection", i["index"], i["code"], "rejected") for i in proposed["issues"])
    issues += tuple(gf.FormationIssue("identity", i, "reliable_identity_evidence_deferred_to_g2", "repaired")
                    for i in deferred)
    return gf.GroundedMemoryBatch(tuple(units), tuple(mentions.values()), tuple(roles), issues), context
