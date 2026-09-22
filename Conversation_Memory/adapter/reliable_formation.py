"""Four bounded, separately receipted Formation stages for reliable v6 writes.

Explicit v4/v5 checkpoints retain their original versioned parsing contracts.

This module authorizes text before optional graph projections. It owns no store:
the existing ingestion owner checkpoints the exact stage request and response.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from . import grounded_formation as gf


FORMATION_RELIABLE_VERSION = "grounded-formation-v4"
FORMATION_RELIABLE_VERSION_V5 = "grounded-formation-v5"
FORMATION_RELIABLE_VERSION_V6 = "grounded-formation-v6"
PROGRESS_VERSION = "reliable-formation-progress-v1"
PROGRESS_VERSION_V5 = "reliable-formation-progress-v2"
PROGRESS_VERSION_V6 = "reliable-formation-progress-v3"
STAGE_VERSION = "reliable-formation-stage-v1"
_STAGES = ("F1", "F2", "G1", "G2")

F1_PROMPT = """F1: Extract source-grounded conversational statements from the bounded source turns.
Return strict JSON only: {"facts":[{"text":str,"origin_turn_id":str,"source_turn_ids":[str]}]}.
Do not produce entities, SRV, identity links, roles, confidence or normalized time.
Use the supplied exact turn IDs. origin_turn_id is the turn that makes the statement, report, proposal, acceptance or refusal; it is NOT the earliest contextual turn. List all turns needed to interpret it, including an earlier proposal when a later user response accepts it.
Write self-contained third-person propositions in the source language. Resolve first/second-person only when their referents are supported by the source. Preserve ownership, direction, negation, uncertainty, quantities, conditions, one-off scope and speech-act/state distinctions. Related qualifications may stay together; separate unrelated propositions. Do not emit redundant restatements.
The program will prefix each text with User stated: or Lumina stated: based on the actual origin role BEFORE verification. Extract assistant suggestions or claims as that speaker's statement, never as user confirmation or independently executed action. A user report is still a report, not external proof. Do not invent acceptance from a question, tentative response, silence, or an assistant summary. Ambiguous pronouns must remain honestly ambiguous; do not insert a guessed name.
No stage can manufacture missing evidence. Return an empty facts list when there is no supported statement worth preserving. At most 96 facts, each text at most 2000 characters."""

F1_PROMPT_V5 = F1_PROMPT.replace(
    "Ambiguous pronouns must remain honestly ambiguous; do not insert a guessed name.",
    "Ambiguous pronouns must remain honestly ambiguous; do not insert a guessed name. "
    "The program owns the speaker prefix; the body text must NOT re-attribute the statement "
    "to the other dialogue party: text for an assistant-origin turn must not say the user "
    "asked or said it, and text for a user-origin turn must not say the assistant asked or said it.")

F1_PROMPT_V6 = F1_PROMPT_V5.replace(
    "The program owns the speaker prefix; the body text must NOT re-attribute the statement "
    "to the other dialogue party: text for an assistant-origin turn must not say the user "
    "asked or said it, and text for a user-origin turn must not say the assistant asked or said it.",
    "The program owns the speaker prefix: it prefixes each text with User stated: or "
    "Lumina stated: based on the actual origin turn role, recording who made the statement; "
    "never write that prefix into the text itself. The body may legitimately mention or "
    "quote the other dialogue party: a user turn may relay Lumina's suggestion, and an "
    "assistant turn may report what the user said. Such cross-references describe the "
    "statement's content, not a different speaker. F2 independently verifies that the "
    "actual origin speaker really expressed the complete proposition.")

# Deterministic manifest screen on two fields the code itself produces: the
# machine-owned speaker prefix and the body's leading re-attribution.
_ATTRIBUTION_CONFLICT_PREFIXES = {
    "assistant": ("the user", "user ", "用户"),
    "user": ("lumina", "the assistant", "assistant ", "助手"),
}

F2_PROMPT = """F2: Independently verify the exact frozen canonical statements and their source attribution.
Return strict JSON only: {"decisions":[{"fact_id":str,"verdict":"supported"|"rejected"|"insufficient","used_source_ids":[str]}]} with exactly one decision for every candidate ID, no duplicates.
Each candidate has its OWN allowed_source_ids and origin_turn_id. The shared sources table is deduplicated transport, not permission to borrow another candidate's evidence. Read only that candidate's allowed source package. used_source_ids must be within that package, include its origin, and collectively support every clause of the unchanged canonical text. Context needed for references, short acceptance or conditions must be included. Do not cite only a summary if the actual authorization is elsewhere.
Verify that the actual origin speaker really made the asserted statement, including every identity, actor, owner, direction, state, scope, uncertainty and condition. A real USER ID by itself proves nothing. User stated: requires that user's cited report/acceptance, not an assistant assertion about the user. Lumina stated: establishes only Lumina's speech, not user acceptance, external execution or successful action. Do not reinterpret the attribution prefix as a license to invent the content said. Preserve proposal, unanswered, tentative, accepted and completed distinctions semantically, not by keywords.
Reject unsupported disambiguation, role interpretation, added state or changed ownership inside the text even if later graph projections could be empty. Use insufficient if the supplied package cannot decide; never rewrite text, repair refs, suggest structure or approve from other candidates. Your judgment is about cited conversational support, not independently verified world truth."""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def _json(raw):
    return gf._parse_entity_model_json(raw, error_code="reliable_stage_output_invalid")


def _stage(name, prompt, payload, model, segment, progress, checkpoint, parser, *, empty_response=None):
    """One of four fixed stages, never a retry/resampling loop."""
    request = {"system": prompt, "payload": payload, "model_policy": "deepseek-v4-pro"}
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > gf._MAX_OUTPUT_CHARS:
        raise gf.FormationError("reliable_stage_input_too_large")
    binding = {"schema_version": STAGE_VERSION, "stage": name,
               "source_digest": gf._entity_source_digest(segment),
               "request": request, "request_digest": digest(request),
               "execution": "deterministic_empty" if empty_response is not None else "provider"}
    saved = progress["stages"].get(name)
    if saved is None:
        saved = {**binding, "status": "reserved"}
        progress["stages"][name] = saved
        checkpoint("formation", deepcopy(progress))
        # A reserved stage with no response cannot be resent automatically:
        # delivery may have occurred even if the transport returned no answer.
        try:
            raw = empty_response if empty_response is not None else gf._entity_model_response(
                model, payload, prompt, error_code="reliable_stage_delivery_unknown")
        except gf.FormationError as error:
            raise gf.FormationError("reliable_stage_delivery_unknown") from error
        saved = {**binding, "status": "received", "response": raw, "response_digest": digest(raw)}
        progress["stages"][name] = saved
        checkpoint("formation", deepcopy(progress))
    elif (not isinstance(saved, dict)
          or set(saved) - {*binding, "status", "response", "response_digest", "parsed", "parsed_digest", "summary"}
          or any(saved.get(k) != v for k, v in binding.items())
          or saved.get("status") not in {"reserved", "received", "parsed"}):
        raise gf.FormationError("reliable_stage_checkpoint_invalid")
    if saved["status"] == "reserved":
        raise gf.FormationError("reliable_stage_delivery_unknown")
    if not isinstance(saved.get("response"), str) or saved.get("response_digest") != digest(saved.get("response")):
        raise gf.FormationError("reliable_stage_checkpoint_invalid")
    parsed, summary = parser(_json(saved["response"]))
    result = {**binding, "status": "parsed", "response": saved["response"],
              "response_digest": saved["response_digest"], "parsed": parsed, "parsed_digest": digest(parsed), "summary": summary}
    if saved["status"] == "parsed":
        if saved != result:
            raise gf.FormationError("reliable_stage_checkpoint_invalid")
    else:
        progress["stages"][name] = result
        checkpoint("formation", deepcopy(progress))
    return parsed


def _sources(segment):
    return {t.turn_id: {"turn_id": t.turn_id, "role": t.role, "text": t.content,
                        "timestamp": t.timestamp.isoformat(), "timezone": t.source_timezone}
            for t in segment.turns}


def _parse_f1(raw, segment, *, version=FORMATION_RELIABLE_VERSION):
    if not isinstance(raw, dict) or set(raw) != {"facts"} or not isinstance(raw["facts"], list) or len(raw["facts"]) > gf._MAX_ENTITY_UNITS:
        raise gf.FormationError("reliable_f1_output_invalid")
    sources = _sources(segment); order = list(sources); facts, issues, seen = [], [], set()
    for index, item in enumerate(raw["facts"]):
        valid = isinstance(item, dict) and set(item) == {"text", "origin_turn_id", "source_turn_ids"}
        if valid:
            text, origin, ids = item["text"], item["origin_turn_id"], item["source_turn_ids"]
            valid = (isinstance(text, str) and bool(text.strip()) and len(text) <= gf._MAX_FIELD_CHARS
                     and isinstance(origin, str) and origin in sources and isinstance(ids, list)
                     and all(isinstance(t, str) and t in sources for t in ids) and len(ids) <= len(sources))
        if not valid:
            issues.append({"index": index, "code": "reliable_fact_source_invalid", "status": "pending"})
            continue
        allowed = set(ids) | {origin}
        position = order.index(origin)
        # Fixed one-turn preceding context closes short responses before F2;
        # explicitly selected additional sources are preserved as well.
        if position:
            allowed.add(order[position - 1])
        allowed = [tid for tid in order if tid in allowed]
        role = sources[origin]["role"]
        prefix = "User stated: " if role == "user" else "Lumina stated: "
        canonical = prefix + text.strip()
        if (version == FORMATION_RELIABLE_VERSION_V5
                and text.strip().casefold().startswith(_ATTRIBUTION_CONFLICT_PREFIXES[role])):
            issues.append({"index": index, "code": "reliable_fact_attribution_conflict", "status": "rejected"})
            continue
        key = digest([canonical, origin, allowed])
        if key in seen:
            continue
        seen.add(key)
        facts.append({"fact_id": "f" + str(index), "text": canonical, "origin_turn_id": origin,
                      "allowed_source_ids": allowed})
    return {"facts": facts, "issues": issues}, {"facts": len(facts), "pending": len(issues)}


def _parse_f2(raw, candidates):
    if not isinstance(raw, dict) or set(raw) != {"decisions"} or not isinstance(raw["decisions"], list):
        raise gf.FormationError("reliable_f2_output_invalid")
    by_id = {f["fact_id"]: f for f in candidates}; decisions = {}
    for d in raw["decisions"]:
        if (not isinstance(d, dict) or set(d) != {"fact_id", "verdict", "used_source_ids"}
                or not isinstance(d["fact_id"], str) or d["fact_id"] not in by_id or d["fact_id"] in decisions
                or not isinstance(d["verdict"], str) or d["verdict"] not in {"supported", "rejected", "insufficient"}
                or not isinstance(d["used_source_ids"], list)
                or not all(isinstance(t, str) and t in by_id[d["fact_id"]]["allowed_source_ids"] for t in d["used_source_ids"])
                or len(set(d["used_source_ids"])) != len(d["used_source_ids"])
                or d["verdict"] == "supported" and by_id[d["fact_id"]]["origin_turn_id"] not in d["used_source_ids"]):
            raise gf.FormationError("reliable_f2_authorization_invalid")
        decisions[d["fact_id"]] = d
    if set(decisions) != set(by_id):
        raise gf.FormationError("reliable_f2_authorization_invalid")
    ordered = [decisions[f["fact_id"]] for f in candidates]
    return ordered, {v: sum(d["verdict"] == v for d in ordered) for v in ("supported", "rejected", "insufficient")}


def _progress_version(version):
    if version == FORMATION_RELIABLE_VERSION_V6:
        return PROGRESS_VERSION_V6
    return PROGRESS_VERSION_V5 if version == FORMATION_RELIABLE_VERSION_V5 else PROGRESS_VERSION


def _prepare_progress(progress, version):
    progress = deepcopy(progress) if progress is not None else {"schema_version": _progress_version(version), "stages": {}}
    allowed = {"schema_version", "stages", "identity_candidates"}
    if version in {FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6}:
        # The owner may additionally mark its persisted body phase.
        allowed.add("bodies")
    if (not isinstance(progress, dict) or set(progress) - allowed
            or progress.get("schema_version") != _progress_version(version) or not isinstance(progress.get("stages"), dict)
            or any(name not in _STAGES for name in progress["stages"])):
        raise gf.FormationError("reliable_stage_checkpoint_invalid")
    keys = set(progress["stages"])
    if keys != set(_STAGES[:len(keys)]):
        raise gf.FormationError("reliable_stage_checkpoint_invalid")
    return progress


def form_reliable_bodies(segment, model, *, progress=None, checkpoint, version=FORMATION_RELIABLE_VERSION_V5):
    """Body phase: F1/F2 only; structure remains unauthorized until the tail."""
    progress = _prepare_progress(progress, version)
    gf._check_entity_window(segment)
    sources = _sources(segment)
    f1_prompt = {FORMATION_RELIABLE_VERSION_V5: F1_PROMPT_V5,
                 FORMATION_RELIABLE_VERSION_V6: F1_PROMPT_V6}.get(version, F1_PROMPT)
    f1 = _stage("F1", f1_prompt, {"turns": list(sources.values())}, model, segment, progress, checkpoint,
                lambda raw: _parse_f1(raw, segment, version=version))
    visible = set(t for f in f1["facts"] for t in f["allowed_source_ids"])
    f2 = _stage("F2", F2_PROMPT, {"candidates": f1["facts"], "sources": {t: s for t, s in sources.items() if t in visible}},
                model, segment, progress, checkpoint, lambda raw: _parse_f2(raw, f1["facts"]),
                empty_response='{"decisions":[]}' if not f1["facts"] else None)
    accepted = [f for f, d in zip(f1["facts"], f2) if d["verdict"] == "supported"]
    return accepted, f1, f2, progress


def form_reliable_structure(segment, model, *, progress, checkpoint, identity_candidates, accepted, f1, f2,
                            version=FORMATION_RELIABLE_VERSION_V5):
    """Structure phase: identity snapshot, G1/G2 and the authorized batch tail."""
    from ._reliable_projection import (
        G1_PROMPT, G2_PROMPT, G1_PROMPT_V5, G2_PROMPT_V5,
        parse_g1, parse_g2, authorized_batch,
    )
    progress = _prepare_progress(progress, version)
    v5 = version in {FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6}
    sources = _sources(segment)
    if "identity_candidates" not in progress:
        if callable(identity_candidates):
            identity_candidates = identity_candidates()
        if len(identity_candidates) > 24:
            raise gf.FormationError("reliable_identity_candidate_budget")
        progress["identity_candidates"] = deepcopy(list(identity_candidates))
        checkpoint("formation", deepcopy(progress))
    prior = progress["identity_candidates"]
    if not isinstance(prior, list) or len(prior) > 24 or not all(isinstance(p, dict) for p in prior):
        raise gf.FormationError("reliable_stage_checkpoint_invalid")
    g1_payload = {"accepted_facts": accepted, "sources": sources,
                  "prior_identities": prior, "source_use": "Full current sources only for independent mention discovery; facts retain their allowed_source_ids. Prior identities never support a new Fact."}
    g1 = _stage("G1", G1_PROMPT_V5 if v5 else G1_PROMPT, g1_payload, model, segment, progress, checkpoint,
                lambda raw: parse_g1(raw, segment, accepted, prior, version=version))
    g2_payload = {"facts": accepted, "sources": sources, "mentions": g1["mentions"],
                  "projections": g1["projections"], "prior_identities": prior}
    g2 = _stage("G2", G2_PROMPT_V5 if v5 else G2_PROMPT, g2_payload, model, segment, progress, checkpoint,
                lambda raw: parse_g2(raw, g1, version=version), empty_response='{"mentions":[],"projections":[]}'
                if not g1["mentions"] and not g1["projections"] else None)
    batch, context = authorized_batch(segment, accepted, g1, g2, version=version)
    extra = [gf.FormationIssue("unit", x["index"], x["code"], x["status"]) for x in f1["issues"]]
    extra += [gf.FormationIssue("unit", i, "reliable_source_" + d["verdict"],
                                "pending" if d["verdict"] == "insufficient" else "rejected")
              for i, d in enumerate(f2) if d["verdict"] != "supported"]
    batch = gf.GroundedMemoryBatch(batch.units, batch.mentions, batch.unit_mentions, (*batch.issues, *extra))
    context["receipts"] = {s: {k: progress["stages"][s][k]
                               for k in ("request_digest", "response_digest", "parsed_digest", "execution")}
                           for s in _STAGES}
    context["used_sources"] = {f["fact_id"]: d["used_source_ids"] for f, d in zip(f1["facts"], f2)}
    return batch, context


def form_reliable_batch(segment, model, *, progress=None, checkpoint, identity_candidates=(),
                        version=FORMATION_RELIABLE_VERSION):
    if version in {FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6}:
        accepted, f1, f2, progress = form_reliable_bodies(
            segment, model, progress=progress, checkpoint=checkpoint, version=version)
        return form_reliable_structure(
            segment, model, progress=progress, checkpoint=checkpoint,
            identity_candidates=identity_candidates, accepted=accepted, f1=f1, f2=f2, version=version)
    from ._reliable_projection import G1_PROMPT, G2_PROMPT, parse_g1, parse_g2, authorized_batch
    gf._check_entity_window(segment)
    progress = _prepare_progress(progress, version)
    sources = _sources(segment)
    f1 = _stage("F1", F1_PROMPT, {"turns": list(sources.values())}, model, segment, progress, checkpoint,
                lambda raw: _parse_f1(raw, segment))
    visible = set(t for f in f1["facts"] for t in f["allowed_source_ids"])
    f2 = _stage("F2", F2_PROMPT, {"candidates": f1["facts"], "sources": {t: s for t, s in sources.items() if t in visible}},
                model, segment, progress, checkpoint, lambda raw: _parse_f2(raw, f1["facts"]),
                empty_response='{"decisions":[]}' if not f1["facts"] else None)
    accepted = [f for f, d in zip(f1["facts"], f2) if d["verdict"] == "supported"]
    if "identity_candidates" not in progress:
        if callable(identity_candidates):
            identity_candidates = identity_candidates()
        if len(identity_candidates) > 24:
            raise gf.FormationError("reliable_identity_candidate_budget")
        progress["identity_candidates"] = deepcopy(list(identity_candidates))
        checkpoint("formation", deepcopy(progress))
    prior = progress["identity_candidates"]
    if not isinstance(prior, list) or len(prior) > 24 or not all(isinstance(p, dict) for p in prior):
        raise gf.FormationError("reliable_stage_checkpoint_invalid")
    g1_payload = {"accepted_facts": accepted, "sources": sources,
                  "prior_identities": prior, "source_use": "Full current sources only for independent mention discovery; facts retain their allowed_source_ids. Prior identities never support a new Fact."}
    g1 = _stage("G1", G1_PROMPT, g1_payload, model, segment, progress, checkpoint,
                lambda raw: parse_g1(raw, segment, accepted, prior))
    g2_payload = {"facts": accepted, "sources": sources, "mentions": g1["mentions"],
                  "projections": g1["projections"], "prior_identities": prior}
    g2 = _stage("G2", G2_PROMPT, g2_payload, model, segment, progress, checkpoint,
                lambda raw: parse_g2(raw, g1), empty_response='{"mentions":[],"projections":[]}'
                if not g1["mentions"] and not g1["projections"] else None)
    batch, context = authorized_batch(segment, accepted, g1, g2)
    extra = [gf.FormationIssue("unit", x["index"], x["code"], x["status"]) for x in f1["issues"]]
    extra += [gf.FormationIssue("unit", i, "reliable_source_" + d["verdict"],
                                "pending" if d["verdict"] == "insufficient" else "rejected")
              for i, d in enumerate(f2) if d["verdict"] != "supported"]
    batch = gf.GroundedMemoryBatch(batch.units, batch.mentions, batch.unit_mentions, (*batch.issues, *extra))
    context["receipts"] = {s: {k: progress["stages"][s][k]
                               for k in ("request_digest", "response_digest", "parsed_digest", "execution")}
                           for s in _STAGES}
    context["used_sources"] = {f["fact_id"]: d["used_source_ids"] for f, d in zip(f1["facts"], f2)}
    return batch, context
