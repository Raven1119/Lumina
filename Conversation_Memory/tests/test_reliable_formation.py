"""Synthetic authorization/recovery contracts, not evidence of model semantics.

The four scripted responses deliberately include both approvals and refusals.
These tests prove that recorded judgments control the intended boundary; only
the separately frozen real-provider panel can assess judgment correctness.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys

import pytest

from Conversation_Memory.adapter import grounded_formation as gf
from Conversation_Memory.adapter import reliable_formation as rf
from Conversation_Memory.adapter import _reliable_projection as rp
from Conversation_Memory.adapter.models import ColdDraftSegment, ColdDraftTurn


PROMPTS = {"F1": rf.F1_PROMPT, "F2": rf.F2_PROMPT,
           "G1": rp.G1_PROMPT, "G2": rp.G2_PROMPT}
PROMPTS_V5 = {"F1": rf.F1_PROMPT_V5, "F2": rf.F2_PROMPT,
              "G1": rp.G1_PROMPT_V5, "G2": rp.G2_PROMPT_V5}


def segment(*turns):
    now = datetime(2026, 9, 10, 10, tzinfo=UTC)
    return ColdDraftSegment(
        "synthetic-reliable", "synthetic-reliable-conversation", "pending_digest",
        tuple(ColdDraftTurn(f"t{i}", role, text, now + timedelta(minutes=i),
                            "UTC", "client") for i, (role, text) in enumerate(turns)),
        now, "UTC", "2",
    )


def fact(text, origin="t0", sources=None):
    return {"text": text, "origin_turn_id": origin,
            "source_turn_ids": sources if sources is not None else [origin]}


def mention(handle, surface, *, turn="t0", occurrence=0, identity="new",
            same_as=None, distinct=(), existing=None, sources=None):
    return {"handle": handle, "surface": surface, "turn_id": turn,
            "occurrence": occurrence, "identity": identity, "same_as": same_as,
            "distinct_from": list(distinct), "existing_entity_ref": existing,
            "identity_source_ids": [turn] if sources is None else sources}


def projection(fid="f0", *, relation=None, mentions=(), time=None):
    return {"fact_id": fid, "relation": relation, "mentions": list(mentions),
            "referenced_time": time}


def relation(*, subject="Ada", predicate="returned", obj="the user's cart",
             subject_mention="actor", object_mention="cart"):
    return {"subject": subject, "predicate": predicate, "object": obj,
            "subject_mention": subject_mention, "object_mention": object_mention}


def approve_bodies(payload):
    return {"decisions": [
        {"fact_id": f["fact_id"], "verdict": "supported",
         "used_source_ids": list(f["allowed_source_ids"])}
        for f in payload["candidates"]
    ]}


def no_projections(payload):
    return {"mentions": [], "projections": [
        projection(f["fact_id"]) for f in payload["accepted_facts"]
    ]}


def approve_projections(payload):
    return {
        "mentions": [{"handle": m["handle"], "occurrence_supported": True,
                      "identity_supported": True} for m in payload["mentions"]],
        "projections": [{"fact_id": p["fact_id"],
                         "relation_supported": p["relation"] is not None,
                         "subject_role_supported": p["relation"] is not None,
                         "object_role_supported": p["relation"] is not None,
                         "mention_support": [True] * len(p["mentions"]),
                         "time_supported": p["referenced_time"] is not None}
                        for p in payload["projections"]],
    }


class StagedModel:
    def __init__(self, facts=(), *, f2=approve_bodies, g1=no_projections,
                 g2=approve_projections, overrides=None, prompts=None):
        self.responses = {"F1": {"facts": list(facts)}, "F2": f2,
                          "G1": g1, "G2": g2, **(overrides or {})}
        self.prompts = prompts or PROMPTS
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        assert recent_context == []
        stage = next(key for key, value in self.prompts.items() if value == system_prompt)
        payload = json.loads(user_message)
        self.calls.append((stage, deepcopy(payload)))
        value = self.responses[stage]
        if isinstance(value, Exception):
            raise value
        value = value(payload) if callable(value) else value
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class UnexpectedModelCall(BaseException):
    """Cannot be converted by the production Exception -> FormationError guard."""


class NoCalls:
    def generate(self, *args, **kwargs):
        raise UnexpectedModelCall("a saved or unknown-delivery stage must not be resent")


class ReceiptStore:
    def __init__(self, *, stop=None):
        self.latest = None
        self.history = []
        self.stop = stop

    def __call__(self, kind, progress):
        assert kind == "formation"
        self.latest = deepcopy(progress)
        self.history.append(deepcopy(progress))
        if self.stop:
            stage, status = self.stop
            if progress["stages"].get(stage, {}).get("status") == status:
                self.stop = None
                raise OSError("synthetic crash after durable stage receipt")


def run(seg, model, *, progress=None, store=None, prior=()):
    store = store if store is not None else ReceiptStore()
    batch, context = rf.form_reliable_batch(
        seg, model, progress=progress, checkpoint=store, identity_candidates=prior,
    )
    return batch, context, store


def run_v5(seg, model, *, progress=None, store=None, prior=()):
    store = store if store is not None else ReceiptStore()
    batch, context = rf.form_reliable_batch(
        seg, model, progress=progress, checkpoint=store, identity_candidates=prior,
        version=rf.FORMATION_RELIABLE_VERSION_V5,
    )
    return batch, context, store


def borrowing_v5(**kwargs):
    seg, model = borrowing(**kwargs)
    model.prompts = PROMPTS_V5
    return seg, model


def ordinary():
    text = "Ada returned the cart; this ended only the one-day loan."
    return segment(("user", text)), StagedModel([fact(text)])


def test_origin_is_actual_speaker_after_context_and_canonical_before_f2():
    seg = segment(
        ("assistant", "Would you ask Ada to cover just Saturday?"),
        ("user", "I sent that request; Ada has not replied."),
        ("assistant", "The request is still unanswered."),
        ("user", "Unrelated: the lamp is blue."),
    )
    model = StagedModel([
        fact("The user sent Ada the Saturday-only request; it remains unanswered.",
             "t1", ["t1"]),
    ])
    batch, context, store = run(seg, model)
    unit = batch.units[0]
    f2 = dict(model.calls)["F2"]
    assert unit.text == f2["candidates"][0]["text"]
    assert unit.text.startswith("User stated: ")
    assert context["origins"][unit.id] == "t1"
    assert [r.turn_id for r in unit.source_refs] == ["t0", "t1"]
    assert [r.supporting_span for r in unit.source_refs] == [t.content for t in seg.turns[:2]]
    assert set(f2["sources"]) == {"t0", "t1"}
    assert set(dict(model.calls)["G1"]["sources"]) == {"t0", "t1", "t2", "t3"}
    assert list(store.latest["stages"]) == ["F1", "F2", "G1", "G2"]
    assert set(context["receipts"]) == set(PROMPTS)
    assert unit.formation_version == rf.FORMATION_RELIABLE_VERSION


@pytest.mark.parametrize("bad_refs", [["t3"], ["t0"], ["t1", "t1"], ["missing"]])
def test_f2_cannot_borrow_other_candidate_sources_or_omit_origin(bad_refs):
    seg = segment(("user", "Did I authorize it?"), ("assistant", "I proposed it."),
                  ("user", "A different action is complete."),
                  ("assistant", "I heard the unrelated report."))

    def f2(payload):
        assert set(payload["sources"]) == {"t0", "t1", "t2", "t3"}
        assert payload["candidates"][0]["allowed_source_ids"] == ["t0", "t1"]
        result = approve_bodies(payload)
        result["decisions"][0]["used_source_ids"] = bad_refs
        return result

    model = StagedModel([fact("Lumina proposed an action.", "t1"),
                         fact("Lumina heard the unrelated report.", "t3")], f2=f2)
    with pytest.raises(gf.FormationError, match="reliable_f2_authorization_invalid"):
        run(seg, model)
    assert [name for name, _ in model.calls] == ["F1", "F2"]


def test_f2_cannot_rewrite_canonical_body_in_its_decision():
    seg, model = ordinary()

    def f2(payload):
        result = approve_bodies(payload)
        result["decisions"][0]["text"] = "Ada now owns the cart permanently."
        return result

    model.responses["F2"] = f2
    with pytest.raises(gf.FormationError, match="reliable_f2_authorization_invalid"):
        run(seg, model)
    assert [stage for stage, _ in model.calls] == ["F1", "F2"]


def test_missing_origin_is_pending_and_cannot_become_a_fabricated_source():
    seg, _ = ordinary()
    model = StagedModel([fact("Ada owns the cart.", "missing", ["t0"])])
    batch, _, store = run(seg, model)
    assert batch.units == ()
    assert any(i.code == "reliable_fact_source_invalid" and i.status == "pending"
               for i in batch.issues)
    assert [stage for stage, _ in model.calls] == ["F1", "G1"]
    assert store.latest["stages"]["F2"]["parsed"] == []


@pytest.mark.parametrize("verdict,status", [("rejected", "rejected"), ("insufficient", "pending")])
def test_f2_refusal_cannot_be_rescued_by_optional_projection(verdict, status):
    seg = segment(("user", "Ada and Bela were here; she carried the cart."))

    def f2(payload):
        result = approve_bodies(payload)
        result["decisions"][0]["verdict"] = verdict
        return result

    def g1(payload):
        assert payload["accepted_facts"] == []
        return {"mentions": [mention("ada", "Ada")], "projections": []}

    batch, _, _ = run(seg, StagedModel([fact("Ada carried the cart.")], f2=f2, g1=g1))
    assert batch.units == ()
    assert [m.surface for m in batch.mentions] == ["Ada"]
    assert any(i.code == "reliable_source_" + verdict and i.status == status for i in batch.issues)


@pytest.mark.parametrize("claim", [
    "The user agreed to assist every week.",
    "The user sent the diagram and a follow-up note.",
    "The user adopted the permission rule.",
    "The user completed the proposed preparation procedure.",
    "The user approved every recording excerpt.",
    "The user granted Ada permission to publish the original recording.",
])
def test_assistant_claim_is_attributed_speech_but_wrong_user_anchor_is_refused(claim):
    seg = segment(("user", "What are you proposing?"), ("assistant", claim))

    def f2(payload):
        first, second = payload["candidates"]
        assert first["text"] == "Lumina stated: " + claim
        assert second["text"] == "User stated: " + claim
        assert first["origin_turn_id"] == "t1" and second["origin_turn_id"] == "t0"
        result = approve_bodies(payload)
        result["decisions"][1].update(verdict="rejected", used_source_ids=["t0"])
        return result

    model = StagedModel([fact(claim, "t1"), fact(claim, "t0", ["t0", "t1"])], f2=f2)
    batch, context, _ = run(seg, model)
    assert [u.text for u in batch.units] == ["Lumina stated: " + claim]
    assert context["origins"] == {batch.units[0].id: "t1"}
    assert batch.unit_mentions[0].subject is None
    assert any(i.code == "reliable_source_rejected" for i in batch.issues)


def borrowing(*, object_handle="cart", object_approved=True,
              relation_approved=True, time_approved=True):
    seg = segment(("user", "Ada returned my cart at Elm Hall on Tuesday."))
    g1 = {"mentions": [mention("actor", "Ada"),
                       mention("owner", "my", identity="current_user"),
                       mention("cart", "cart"), mention("place", "Elm Hall")],
          "projections": [projection(relation=relation(object_mention=object_handle),
                                     mentions=["owner", "place"],
                                     time={"text": "Tuesday", "turn_id": "t0"})]}

    def g2(payload):
        assert payload["projections"][0]["relation"] == g1["projections"][0]["relation"]
        result = approve_projections(payload)
        result["projections"][0].update(relation_supported=relation_approved,
                                        object_role_supported=object_approved,
                                        time_supported=time_approved)
        return result

    return seg, StagedModel([fact("Ada returned the user's cart at Elm Hall on Tuesday.")],
                            g1=g1, g2=g2)


@pytest.mark.parametrize("handle,approved,expected", [
    ("owner", False, None), ("place", False, None), ("cart", True, "cart"),
])
def test_complete_relation_role_verdict_distinguishes_owner_cart_and_location(handle, approved, expected):
    batch, _, _ = run(*borrowing(object_handle=handle, object_approved=approved))
    unit, roles = batch.units[0], batch.unit_mentions[0]
    by_id = {m.id: m for m in batch.mentions}
    assert (unit.subject, unit.relation, unit.value) == ("Ada", "returned", "the user's cart")
    assert by_id[roles.subject].surface == "Ada"
    assert (by_id[roles.object].surface if roles.object else None) == expected
    assert {by_id[mid].surface for mid in roles.mentions} == {"my", "Elm Hall"}
    assert unit.text == "User stated: Ada returned the user's cart at Elm Hall on Tuesday."


def test_relation_refusal_blocks_both_roles_but_keeps_body_and_independent_mentions():
    batch, context, _ = run(*borrowing(relation_approved=False, time_approved=False))
    unit, roles = batch.units[0], batch.unit_mentions[0]
    assert unit.subject is unit.relation is unit.value is None
    assert roles.subject is roles.object is None
    assert len(roles.mentions) == 2
    assert unit.referenced_time is None and context["times"][unit.id] is None
    assert unit.text.endswith("cart at Elm Hall on Tuesday.")


@pytest.mark.parametrize("field,value", [("subject", "actor"), ("object", True),
                                         ("predicate", None), ("object", [])])
def test_invalid_optional_relation_is_withheld_without_denying_body(field, value):
    seg, model = borrowing()
    model.responses["G1"]["projections"][0]["relation"][field] = value
    model.responses["G2"] = approve_projections
    batch, _, _ = run(seg, model)
    assert len(batch.units) == 1
    assert batch.units[0].subject is batch.units[0].relation is batch.units[0].value is None
    assert any(i.code == "reliable_relation_syntax_withheld" for i in batch.issues)


def test_failed_distinct_claim_keeps_target_and_blocks_namesake_identity_authority():
    seg = segment(("user", "Ada met a second Ada. She waited."))
    prior = [{"entity_ref": "E_existing", "canonical_surface": "Ada"}]
    g1 = {"mentions": [mention("a", "Ada"),
                       mention("b", "Ada", occurrence=1, identity="named",
                               distinct=["a"], existing="E_existing"),
                       mention("p", "She", same_as="b")], "projections": []}

    def g2(payload):
        result = approve_projections(payload)
        result["mentions"][1]["identity_supported"] = False
        return result

    batch, context, store = run(seg, StagedModel(g1=g1, g2=g2), prior=prior)
    a, b, pronoun = batch.mentions
    assert a.identity == "new"
    assert b.identity == pronoun.identity == "unresolved"
    assert b.same_as is pronoun.same_as is None
    assert b.distinct_from == pronoun.distinct_from == ()
    assert b.identity_source_refs == pronoun.identity_source_refs == ()
    assert context["explicit_identity_refs"] == {}
    assert store.latest["identity_candidates"] == prior


def test_legacy_identity_closure_remains_distinct_from_new_directed_proofs():
    def m(mid, **kwargs):
        return gf.GroundedEntityMention(mid, mid, "t0", 0, 1, "user", "new", **kwargs)
    rows = {"a": m("a"), "b": m("b", distinct_from=("a",)),
            "p": m("p", same_as="b")}
    assert gf._directed_identity_dependencies(rows, {"b"}) == {"b", "p"}
    assert gf._invalid_identity_dependencies(rows, {"b"}) == {"a", "b", "p"}
    rows["b"] = m("b", same_as="a", distinct_from=("a",))
    assert gf._directed_identity_dependencies(rows, set()) == {"b", "p"}
    rows["b"] = m("b", same_as="p")
    assert gf._directed_identity_dependencies(rows, set()) == {"b", "p"}


@pytest.mark.parametrize("stage", list(PROMPTS))
def test_raw_response_is_recoverable_without_repeating_successful_stage(stage):
    seg, model = ordinary()
    expected, expected_context, _ = run(seg, deepcopy(model))
    store = ReceiptStore(stop=(stage, "received"))
    with pytest.raises(OSError, match="after durable"):
        run(seg, model, store=store)
    count = list(PROMPTS).index(stage) + 1
    assert [s for s, _ in model.calls] == list(PROMPTS)[:count]
    assert store.latest["stages"][stage]["status"] == "received"
    resumed = ordinary()[1]
    resumed.responses.update({s: AssertionError("saved stage was repeated") for s in list(PROMPTS)[:count]})
    batch, context, finished = run(seg, resumed, progress=store.latest)
    assert batch == expected and context == expected_context
    assert [s for s, _ in resumed.calls] == list(PROMPTS)[count:]
    assert all(s["status"] == "parsed" for s in finished.latest["stages"].values())


@pytest.mark.parametrize("stage", list(PROMPTS))
def test_unknown_delivery_reservation_never_becomes_retry_or_empty_success(stage):
    seg, model = ordinary()
    model.responses[stage] = TimeoutError("synthetic unknown delivery")
    store = ReceiptStore()
    with pytest.raises(gf.FormationError, match="reliable_stage_delivery_unknown"):
        run(seg, model, store=store)
    assert store.latest["stages"][stage]["status"] == "reserved"
    with pytest.raises(gf.FormationError, match="reliable_stage_delivery_unknown"):
        run(seg, NoCalls(), progress=store.latest)
    assert [s for s, _ in model.calls].count(stage) == 1


def test_invalid_json_is_retained_and_not_resampled():
    seg, model = ordinary()
    model.responses["F2"] = "This is not JSON."
    store = ReceiptStore()
    with pytest.raises(gf.FormationError, match="reliable_stage_output_invalid"):
        run(seg, model, store=store)
    assert store.latest["stages"]["F2"]["status"] == "received"
    assert store.latest["stages"]["F2"]["response"] == "This is not JSON."
    with pytest.raises(gf.FormationError, match="reliable_stage_output_invalid"):
        run(seg, NoCalls(), progress=store.latest)


@pytest.mark.parametrize("stage", list(PROMPTS))
@pytest.mark.parametrize("field", ["source_digest", "request", "parsed", "response"])
def test_stage_receipt_rejects_source_request_result_and_response_tampering(stage, field):
    seg, model = ordinary()
    _, _, store = run(seg, model)
    changed = deepcopy(store.latest)
    saved = changed["stages"][stage]
    if field == "request":
        saved[field]["model_policy"] = "unapproved-model"
        saved["request_digest"] = rf.digest(saved[field])
    elif field == "parsed":
        saved[field] = {"forged": True}
        saved["parsed_digest"] = rf.digest(saved[field])
    elif field == "response":
        saved[field] = json.dumps({"forged": True})
    else:
        saved[field] = "changed"
    with pytest.raises(gf.FormationError):
        run(seg, NoCalls(), progress=changed)


@pytest.mark.parametrize("field,value", [("content", "A different source statement."),
                                         ("role", "assistant"),
                                         ("timestamp", datetime(2026, 9, 11, tzinfo=UTC))])
def test_completed_receipts_do_not_authorize_changed_cold(field, value):
    seg, model = ordinary()
    _, _, store = run(seg, model)
    changed = replace(seg, turns=(replace(seg.turns[0], **{field: value}),))
    with pytest.raises(gf.FormationError, match="reliable_stage_checkpoint_invalid"):
        run(changed, NoCalls(), progress=store.latest)


def test_completed_receipts_replay_exactly_and_freeze_prior_identity_candidates():
    seg, model = ordinary()
    prior = [{"entity_ref": "E_recorded", "canonical_surface": "Ada"}]
    batch, context, store = run(seg, model, prior=prior)
    replayed, replay_context, _ = run(seg, NoCalls(), progress=store.latest,
                                     prior=[{"entity_ref": "E_new_graph_entry"}])
    assert replayed == batch and replay_context == context


@pytest.mark.parametrize("stage", ["F2", "G1", "G2"])
def test_missing_verdict_or_projection_manifest_is_incomplete_not_empty_success(stage):
    seg, model = ordinary()
    model.responses[stage] = {"decisions": []} if stage == "F2" else {"mentions": [], "projections": []}
    store = ReceiptStore()
    with pytest.raises(gf.FormationError):
        run(seg, model, store=store)
    assert store.latest["stages"][stage]["status"] == "received"
    with pytest.raises(gf.FormationError):
        run(seg, NoCalls(), progress=store.latest)


def test_v2_and_out_of_order_progress_are_never_reinterpreted():
    seg, model = ordinary()
    for progress in ({"schema_version": gf.FORMATION_ENTITY_VERSION, "stages": {}},
                     {"schema_version": rf.PROGRESS_VERSION, "stages": {"G1": {}}}):
        with pytest.raises(gf.FormationError, match="reliable_stage_checkpoint_invalid"):
            run(seg, NoCalls(), progress=progress)



def test_g2_cannot_recover_roles_participants_or_time_from_another_source_package():
    seg = segment(("user", "The cart was returned."),
                  ("assistant", "We have changed subjects."),
                  ("user", "Ada visited Elm Hall on Tuesday."))
    g1 = {"mentions": [mention("actor", "Ada", turn="t2"),
                       mention("place", "Elm Hall", turn="t2")],
          "projections": [projection(relation=relation(object_mention="place"),
                                     mentions=["actor", "place"],
                                     time={"text": "Tuesday", "turn_id": "t2"})]}

    def g2(payload):
        proposed = payload["projections"][0]
        assert proposed["relation"]["subject_mention"] is None
        assert proposed["relation"]["object_mention"] is None
        assert proposed["mentions"] == [] and proposed["referenced_time"] is None
        result = approve_projections(payload)
        result["projections"][0]["relation_supported"] = False
        return result

    batch, _, _ = run(seg, StagedModel([fact("The cart was returned.")], g1=g1, g2=g2))
    assert {m.surface for m in batch.mentions} == {"Ada", "Elm Hall"}
    assert batch.unit_mentions[0] == gf.GroundedUnitMentions(batch.units[0].id)
    assert batch.units[0].referenced_time is None


def test_assistant_first_person_occurrence_is_preserved_without_user_identity():
    seg = segment(("assistant", "I suggested asking Ada."))
    g1 = {"mentions": [mention("speaker", "I", identity="current_user")],
          "projections": [projection(mentions=["speaker"])]}

    def g2(payload):
        result = approve_projections(payload)
        result["mentions"][0]["identity_supported"] = False
        return result

    batch, context, _ = run(seg, StagedModel([fact("Lumina suggested asking Ada.")],
                                            g1=g1, g2=g2))
    assert batch.units[0].text.startswith("Lumina stated: ")
    assert batch.mentions[0].source_role == "assistant"
    assert batch.mentions[0].identity == "unresolved"
    assert batch.unit_mentions[0].mentions == ()
    assert context["explicit_identity_refs"] == {}


@pytest.mark.parametrize("malformed", [[], {}, True, None])
def test_f2_non_string_verdict_is_a_bounded_stage_failure(malformed):
    seg, model = ordinary()

    def f2(payload):
        result = approve_bodies(payload)
        result["decisions"][0]["verdict"] = malformed
        return result

    model.responses["F2"] = f2
    with pytest.raises(gf.FormationError, match="reliable_f2_authorization_invalid"):
        run(seg, model)


def test_bad_optional_identity_syntax_does_not_block_the_supported_body():
    seg, model = borrowing()
    model.responses["G1"]["mentions"][0]["same_as"] = []
    model.responses["G2"] = approve_projections
    batch, _, _ = run(seg, model)
    assert len(batch.units) == 1
    assert batch.mentions[0].identity == "unresolved"
    assert batch.unit_mentions[0].subject is None
    assert batch.unit_mentions[0].object is not None


def test_g2_approval_defers_missing_g1_evidence_for_current_user():
    seg, model = borrowing()
    model.responses["G1"]["mentions"][1]["identity_source_ids"] = []
    batch, _, _ = run(seg, model)
    owner = next(m for m in batch.mentions if m.surface == "my")
    assert owner.identity == "current_user"
    assert owner.id in batch.unit_mentions[0].mentions
    assert ("identity", 1, "reliable_identity_evidence_deferred_to_g2", "repaired") in {
        (i.candidate, i.index, i.code, i.status) for i in batch.issues}


def test_g2_approval_defers_missing_g1_evidence_for_new_entity():
    seg, model = borrowing()
    model.responses["G1"]["mentions"][0]["identity_source_ids"] = []
    batch, _, _ = run(seg, model)
    actor = next(m for m in batch.mentions if m.surface == "Ada")
    assert actor.identity == "new"
    assert batch.unit_mentions[0].subject == actor.id


def test_g2_rejection_keeps_evidenceless_current_user_unresolved():
    seg, model = borrowing()
    model.responses["G1"]["mentions"][1]["identity_source_ids"] = []

    def g2(payload):
        result = approve_projections(payload)
        result["mentions"][1]["identity_supported"] = False
        return result

    model.responses["G2"] = g2
    batch, _, _ = run(seg, model)
    owner = next(m for m in batch.mentions if m.surface == "my")
    assert owner.identity == "unresolved"
    assert owner.id not in batch.unit_mentions[0].mentions
    assert not any(i.code == "reliable_identity_evidence_deferred_to_g2" for i in batch.issues)


@pytest.mark.parametrize("patch", [
    {"identity": "same_as"},
    {"existing_entity_ref": "E_001"},
    {"identity": "named", "existing_entity_ref": "E_001"},
    {"same_as": "ghost"},
])
def test_missing_evidence_never_defers_other_syntax_defects(patch):
    seg, model = borrowing()
    model.responses["G1"]["mentions"][0].update(patch, identity_source_ids=[])
    batch, _, _ = run(seg, model)
    actor = next(m for m in batch.mentions if m.surface == "Ada")
    assert actor.identity == "unresolved"
    assert batch.unit_mentions[0].subject is None


def test_deferred_identity_revives_same_as_dependents():
    seg = segment(("user", "I told Ada that I left."))
    g1 = {"mentions": [mention("first", "I", identity="current_user", sources=[]),
                       mention("second", "I", occurrence=1, same_as="first"),
                       mention("ada", "Ada")],
          "projections": [projection(mentions=["first", "second", "ada"])]}
    batch, _, _ = run(seg, StagedModel([fact("The user told Ada that the user left.")],
                                       g1=g1))
    first = next(m for m in batch.mentions if m.surface == "I" and m.source_start == 0)
    second = next(m for m in batch.mentions if m.surface == "I" and m.source_start > 0)
    assert first.identity == "current_user"
    assert second.identity == "new" and second.same_as == first.id
    assert {first.id, second.id} <= set(batch.unit_mentions[0].mentions)



def configured_ingestor(tmp_path, backend, model, *, cold=None, real=False):
    from types import SimpleNamespace
    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from Conversation_Memory.adapter.models import BackendCandidate
    from Conversation_Memory.ingestion.state_store import IngestionStateStore

    memory = MagmaMemoryAdapter(
        backend, IngestionStateStore(tmp_path / "ingestion.json"),
        ingestion_version=rf.FORMATION_RELIABLE_VERSION, formation_model=model,
        first_hit=FirstHitPolicy(), cold_store=cold,
        associative_read_profile="reliable-v1",
    )
    activations = []
    if not real:
        def activate(cue, *, target_entity_refs, exclude_evidence_ids):
            activations.append((cue, target_entity_refs, exclude_evidence_ids))
            candidates = tuple((BackendCandidate("Historical fact " + str(i), None, None,
                                                {"evidence_id": "old-" + str(i)}), 0.5, 0.0)
                               for i in range(2))
            return SimpleNamespace(candidates=candidates, safe_error_code=None)
        memory._activate_first_hit = activate
    return memory, activations


def association_state(memory, seg):
    from Conversation_Memory.adapter._first_hit_ingestion import _stage_version
    return memory.state_store.get(memory.state_store.key(
        seg.segment_id, _stage_version(rf.FORMATION_RELIABLE_VERSION)))


@pytest.mark.parametrize("crash", ["graph_before_vector", "partial_edge", "links_persist"])
def test_ingest_recovers_frozen_bindings_graph_vector_and_plan_without_generation(tmp_path, crash):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = borrowing(object_handle="owner", object_approved=False)
    backend = DurableBackend(tmp_path / "graph.json", crash=crash)
    memory, activated = configured_ingestor(tmp_path, backend, model)
    legacy_key = memory.state_store.key(seg.segment_id, "first-hit-v1")
    legacy = {"untouched_legacy_receipt": True}
    memory.state_store.put(legacy_key, legacy)

    def assert_receipts_precede_writes():
        state = memory.state_store.get(memory.state_store.key(seg.segment_id, rf.FORMATION_RELIABLE_VERSION))
        assert all(r["status"] == "parsed" for r in state["formation"]["stages"].values())
        assert "mentions" in state
        assert association_state(memory, seg)["status"] == "planned"

    backend.before_mutation = assert_receipts_precede_writes
    first = memory.ingest(seg)
    assert first.status == "failed" and first.retryable
    assert [stage for stage, _ in model.calls] == list(PROMPTS)
    assert len(activated) == 1
    plan = association_state(memory, seg)["link_plan"]
    resumed_backend = DurableBackend(backend.path)
    resumed, calls = configured_ingestor(tmp_path, resumed_backend, NoCalls())
    result = resumed.ingest(seg)
    assert result.status == "completed", result
    assert calls == []
    assert association_state(resumed, seg)["link_plan"] == plan
    assert association_state(resumed, seg)["status"] == "completed"
    assert resumed.state_store.get(legacy_key) == legacy
    assert len(result.memory_ids) == 1 and len(resumed_backend.events) == 6
    mid = result.memory_ids[0]
    assert mid in resumed_backend.vectors
    assert len(resumed_backend.links) == 2 * len(plan)
    metadata = resumed_backend.events[mid]["metadata"]
    assert metadata["object_entity_ref"] is None
    assert metadata["subject_entity_ref"] is not None
    assert metadata["entities"] == []
    assert metadata["provenance"]["ingestion_version"] == rf.FORMATION_RELIABLE_VERSION
    assert set(metadata["formation_receipts"]) == set(PROMPTS)
    assert metadata["used_source_ids"] == metadata["source_package_ids"] == ["t0"]
    assert backend.semantic_flags == [False]
    assert resumed_backend.entity_flags == [False]
    before = backend.path.read_bytes(), resumed.state_store.path.read_bytes()
    assert resumed.ingest(seg).already_ingested
    assert (backend.path.read_bytes(), resumed.state_store.path.read_bytes()) == before


def test_ingest_origin_timestamp_and_relative_time_use_different_real_anchors(tmp_path):
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg = segment(("assistant", "Please return the cart tomorrow."),
                  ("user", "Yes, I agree to that deadline."))
    model = StagedModel([fact("The user agreed to return the cart tomorrow.", "t1")],
                        g1={"mentions": [], "projections": [
                            projection(time={"text": "tomorrow", "turn_id": "t0"})]})
    backend = DurableBackend(tmp_path / "graph.json")
    memory, _ = configured_ingestor(tmp_path, backend, model)
    result = memory.ingest(seg)
    assert result.status == "completed", result
    event = backend.events[result.memory_ids[0]]
    metadata = event["metadata"]
    assert event["timestamp"] == seg.turns[1].timestamp
    assert metadata["origin_turn_id"] == metadata["turn_id"] == "t1"
    assert metadata["role"] == metadata["provenance"]["source_role"] == "user"
    assert metadata["provenance"]["source_timestamp"] == seg.turns[1].timestamp.isoformat()
    assert datetime.fromisoformat(metadata["temporal_mentions"][0]["reference_timestamp"]) == seg.turns[0].timestamp
    assert event["text"].startswith("User stated: ")


def test_v4_binder_does_not_revive_unverified_same_name_but_preserves_explicit_reuse():
    from Conversation_Memory.adapter._entity_ingestion import _bind_mentions, _validate_records
    from Conversation_Memory.adapter.entity_consolidation import EntityCandidate
    from Conversation_Memory.tests.test_entity_ingestion_v2 import Backend

    seg = segment(("user", "Ada arrived."))
    m = gf.GroundedEntityMention("mention-v4-test", "Ada", "t0", 0, 3, "user", "named")
    batch = gf.GroundedMemoryBatch((), (m,), ())
    backend = Backend()
    backend.candidates = [EntityCandidate("E_existing", "Ada")]
    args = {"version": rf.FORMATION_RELIABLE_VERSION}
    records = _bind_mentions(batch, seg, backend, **args)
    assert records[0]["entity_ref"] is None
    assert records[0]["candidate_entity_refs"] == ["E_existing"]
    assert _validate_records(records, batch, seg, **args)
    forged = deepcopy(records)
    forged[0]["entity_ref"] = "E_existing"
    assert not _validate_records(forged, batch, seg, **args)
    assert _bind_mentions(batch, seg, backend)[0]["entity_ref"] == "E_existing"
    args["explicit_identity_refs"] = {m.id: "E_existing"}
    approved = _bind_mentions(batch, seg, backend, **args)
    assert approved[0]["entity_ref"] == "E_existing"
    assert _validate_records(approved, batch, seg, **args)


def test_contradictory_explicit_identity_binding_is_withheld_without_poisoning_target():
    from Conversation_Memory.adapter._entity_ingestion import _bind_mentions, _validate_records
    from Conversation_Memory.adapter.entity_consolidation import EntityCandidate
    from Conversation_Memory.tests.test_entity_ingestion_v2 import Backend

    seg = segment(("user", "Ada met another Ada."))
    prior = [{"entity_ref": "E_existing", "canonical_surface": "Ada"}]
    g1 = {"mentions": [mention("a", "Ada", identity="named", existing="E_existing"),
                       mention("b", "Ada", occurrence=1, identity="named",
                               distinct=["a"], existing="E_existing")],
          "projections": [projection(mentions=["a", "b"])]}
    batch, context, _ = run(seg, StagedModel([fact("Ada met another Ada.")], g1=g1), prior=prior)
    assert len(batch.units) == 1
    assert [m.identity for m in batch.mentions] == ["named", "unresolved"]
    assert context["explicit_identity_refs"] == {batch.mentions[0].id: "E_existing"}
    backend = Backend()
    backend.candidates = [EntityCandidate("E_existing", "Ada")]
    args = {"version": rf.FORMATION_RELIABLE_VERSION,
            "explicit_identity_refs": context["explicit_identity_refs"]}
    records = _bind_mentions(batch, seg, backend, **args)
    assert [r["entity_ref"] for r in records] == ["E_existing", None]
    assert _validate_records(records, batch, seg, **args)


def append_cold(owner, seg):
    owner.append_segment([
        {"turn_id": t.turn_id, "role": t.role, "text": t.content,
         "created_at": t.timestamp.isoformat(), "source_timezone": t.source_timezone,
         "timezone_source": t.timezone_source} for t in seg.turns
    ], segment_id=seg.segment_id)


def test_manual_dream_consumes_only_after_reliable_projection_and_edges_are_durable(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_MIND_DECISION_LOG_PATH", str(tmp_path / "decisions.jsonl"))
    from core.cold_draft_store import ColdDraftStore
    from Dream.cold_draft_digest import ColdDraftDigestionTask
    from Dream.models import DreamRunPolicy
    from Dream.runner import DreamRunner
    from Conversation_Memory.tests.test_first_hit_ingestion import DurableBackend

    seg, model = ordinary()
    owner = ColdDraftStore(tmp_path / "cold.jsonl", source_window_segments=8,
                           source_window_bytes=65536)
    append_cold(owner, seg)
    before = deepcopy(owner.list_pending()[0])
    original_turns = owner.list_all_turns()
    backend = DurableBackend(tmp_path / "graph.json", crash="partial_edge")
    memory, _ = configured_ingestor(tmp_path, backend, model, cold=owner)

    class Provider:
        def get(self, version):
            assert version == rf.FORMATION_RELIABLE_VERSION
            return memory

    runner = DreamRunner(owner, ColdDraftDigestionTask(owner, Provider()))
    policy = DreamRunPolicy(ingestion_version=rf.FORMATION_RELIABLE_VERSION)
    failed = runner.run_once(policy)
    assert failed.failed == 1 and failed.consumed == 0
    assert owner.list_pending()[0] == before
    assert association_state(memory, seg)["status"] == "planned"
    memory, calls = configured_ingestor(tmp_path, DurableBackend(backend.path), NoCalls(), cold=owner)
    completed = runner.run_once(policy)
    assert completed.consumed == 1 and not owner.list_pending()
    assert calls == []
    assert association_state(memory, seg)["status"] == "completed"
    assert owner.list_all_turns() == original_turns


@pytest.mark.skipif(Path(sys.prefix).resolve() !=
                    (Path(__file__).resolve().parents[1] / ".venv").resolve(),
                    reason="real MAGMA uses the isolated Memory environment")
def test_real_magma_repairs_missing_vector_without_reforming_then_recall_expands_cold(tmp_path, monkeypatch):
    import dotenv
    import socket
    monkeypatch.setenv("LUMINA_MIND_DECISION_LOG_PATH", str(tmp_path / "decisions.jsonl"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(dotenv, "find_dotenv", lambda *a, **k: "")

    def deny_network(*args, **kwargs):
        raise UnexpectedModelCall("network must stay disabled in synthetic acceptance")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    from core.cold_draft_store import ColdDraftStore
    from Dream.cold_draft_digest import ColdDraftSegmentConverter
    from Conversation_Memory.adapter.backend import RealMagmaBackend
    from Conversation_Memory.adapter.models import RecallPolicy
    from sentence_transformers import SentenceTransformer
    from time import perf_counter

    compute = {"initializations": [], "encodings": []}
    model_init = SentenceTransformer.__init__
    model_encode = SentenceTransformer.encode

    def counted_init(instance, *args, **kwargs):
        record = {"status": "attempted"}
        compute["initializations"].append(record)
        started = perf_counter()
        try:
            result = model_init(instance, *args, **kwargs)
            record["status"] = "completed"
            return result
        finally:
            record["seconds"] = perf_counter() - started

    def counted_encode(instance, sentences, *args, **kwargs):
        record = {"items": 1 if isinstance(sentences, str) else len(sentences), "status": "attempted"}
        compute["encodings"].append(record)
        started = perf_counter()
        try:
            result = model_encode(instance, sentences, *args, **kwargs)
            record["status"] = "completed"
            return result
        finally:
            record["seconds"] = perf_counter() - started

    monkeypatch.setattr(SentenceTransformer, "__init__", counted_init)
    monkeypatch.setattr(SentenceTransformer, "encode", counted_encode)
    original, model = ordinary()
    owner = ColdDraftStore(tmp_path / "cold.jsonl", source_window_segments=8,
                           source_window_bytes=65536)
    append_cold(owner, original)
    seg = ColdDraftSegmentConverter().convert(owner.list_pending()[0], rf.FORMATION_RELIABLE_VERSION)
    backend = RealMagmaBackend(tmp_path / "magma")
    memory, _ = configured_ingestor(tmp_path, backend, model, cold=owner, real=True)
    enriched_metadata = []
    enrich = backend.trg.keyword_enricher.enrich_content

    def capture_enrichment(content, *, metadata):
        enriched_metadata.append(deepcopy(metadata))
        return enrich(content, metadata=metadata)

    monkeypatch.setattr(backend.trg.keyword_enricher, "enrich_content", capture_enrichment)

    def fail_vector(*args, **kwargs):
        backend.persist()
        raise OSError("synthetic failure after durable graph, before vector")

    monkeypatch.setattr(backend.trg.vector_db, "add_vector", fail_vector)
    failed = memory.ingest(seg)
    assert failed.safe_error_code == "memory_write_failed" and failed.retryable
    events = [n for n in backend.trg.graph_db.nodes.values() if n.attributes.get("evidence_id")]
    assert len(events) == 1
    node = events[0]
    assert node.node_id not in backend.trg.vector_db.id_to_index
    assert enriched_metadata and all(m["entities"] == [] for m in enriched_metadata)
    assert node.attributes["subject_entity_ref"] is node.attributes["object_entity_ref"] is None
    assert node.attributes["entities"] == []
    restarted = RealMagmaBackend(tmp_path / "magma")
    resumed, _ = configured_ingestor(tmp_path, restarted, NoCalls(), cold=owner, real=True)
    encode = restarted.trg.encoder.encode

    def no_new_embedding(*args, **kwargs):
        raise UnexpectedModelCall("vector repair must reuse the persisted embedding")

    monkeypatch.setattr(restarted.trg.encoder, "encode", no_new_embedding)
    complete = resumed.ingest(seg)
    assert complete.status == "completed", complete
    assert complete.memory_ids == (node.node_id,)
    assert restarted.trg.vector_db.entries[node.node_id].metadata["entities"] == []
    assert not any(link.link_type.value == "ENTITY" and node.node_id in
                   (link.source_node_id, link.target_node_id)
                   for link in restarted.trg.graph_db.links.values())
    assert resumed.ingest(seg).already_ingested
    # A terminal checkpoint does not prove its vector file remains complete.
    # Delete through the real FAISS owner, persist the loss, and repair without
    # loading another model or creating a second EVENT/embedding.
    assert restarted.trg.vector_db.delete_vector(node.node_id)
    restarted.persist()
    assert node.node_id not in restarted.trg.vector_db.id_to_index
    encoded_before_repair = len(compute["encodings"])
    terminal = resumed.ingest(seg)
    assert terminal.status == "completed" and terminal.already_ingested
    assert terminal.memory_ids == (node.node_id,)
    assert node.node_id in restarted.trg.vector_db.id_to_index
    assert len(compute["encodings"]) == encoded_before_repair
    assert node.node_id in (tmp_path / "magma/vectors/metadata.json").read_text(encoding="utf-8")
    from faiss import read_index
    assert read_index(str(tmp_path / "magma/vectors/index.faiss")).ntotal == 1
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    assert resumed.ingest(seg).already_ingested
    assert {str(path.relative_to(tmp_path)): path.read_bytes()
            for path in tmp_path.rglob("*") if path.is_file()} == before
    monkeypatch.setattr(restarted.trg.encoder, "encode", encode)
    policy = RecallPolicy(top_k=3, max_chars=3000, max_evidence_items=5,
                          include_source_context=True)
    result = resumed.recall_associative("Ada returned the cart", policy, include_sources=True)
    assert result.facts.safe_error_code is None and result.facts.evidence
    assert result.sources.evidence
    assert any(e.provenance.turn_id == "t0" and e.text == seg.turns[0].content
               for e in result.sources.evidence)
    assert all(e.text.startswith("User stated: ") for e in result.facts.evidence)
    assert [stage for stage, _ in model.calls] == list(PROMPTS)

    print("reliable_formation_local_compute=" + json.dumps(compute, sort_keys=True))


@pytest.mark.parametrize("origin,text", [
    ("t1", "The user asked Ada to return the cart."),
    ("t1", "user asked Ada to return the cart."),
    ("t1", "用户要求 Ada 归还推车。"),
    ("t0", "Lumina noted the return."),
    ("t0", "The assistant noted the return."),
    ("t0", "助手记录了归还。"),
])
def test_v5_cross_attribution_is_isolated_and_window_continues(origin, text):
    seg = segment(("user", "Ada returned the cart."), ("assistant", "I noted the return."))
    model = StagedModel([fact(text, origin), fact("Ada returned the cart.", "t0")],
                        prompts=PROMPTS_V5)
    batch, _, _ = run_v5(seg, model)
    assert [unit.text for unit in batch.units] == ["User stated: Ada returned the cart."]
    assert [candidate["text"] for candidate in dict(model.calls)["F2"]["candidates"]] == [
        "User stated: Ada returned the cart."]
    assert any(issue.code == "reliable_fact_attribution_conflict" and issue.status == "rejected"
               for issue in batch.issues)


@pytest.mark.parametrize("origin,text", [("t1", "The user asked Ada to return the cart."),
                                         ("t0", "The assistant noted the return.")])
def test_v4_parse_has_no_attribution_screen(origin, text):
    seg = segment(("user", "Ada returned the cart."), ("assistant", "I noted the return."))
    model = StagedModel([fact(text, origin)])
    batch, _, _ = run(seg, model)
    assert len(batch.units) == 1
    assert not any(issue.code == "reliable_fact_attribution_conflict" for issue in batch.issues)


def test_v5_missing_identity_evidence_is_valid_for_new_and_current_user():
    seg, model = borrowing_v5()
    model.responses["G1"]["mentions"][0]["identity_source_ids"] = []
    model.responses["G1"]["mentions"][1]["identity_source_ids"] = []
    batch, _, _ = run_v5(seg, model)
    actor = next(m for m in batch.mentions if m.surface == "Ada")
    owner = next(m for m in batch.mentions if m.surface == "my")
    assert actor.identity == "new" and owner.identity == "current_user"
    assert batch.unit_mentions[0].subject == actor.id
    assert owner.id in batch.unit_mentions[0].mentions
    assert not any(i.code == "reliable_identity_evidence_deferred_to_g2" for i in batch.issues)


def test_v5_dangling_identity_evidence_is_invalid_despite_g2_approval():
    seg, model = borrowing_v5()
    model.responses["G1"]["mentions"][0]["identity_source_ids"] = ["t9"]
    batch, _, _ = run_v5(seg, model)
    actor = next(m for m in batch.mentions if m.surface == "Ada")
    assert actor.identity == "unresolved"
    assert batch.unit_mentions[0].subject is None
    assert not any(i.code == "reliable_identity_evidence_deferred_to_g2" for i in batch.issues)


def test_v5_named_still_requires_positive_identity_evidence():
    seg = segment(("user", "Ada returned the cart."))
    prior = [{"entity_ref": "E_existing", "canonical_surface": "Ada"}]

    def run_with(sources):
        g1 = {"mentions": [mention("a", "Ada", identity="named", existing="E_existing",
                                   sources=sources)],
              "projections": [projection(mentions=["a"])]}
        return run_v5(seg, StagedModel([fact("Ada returned the cart.")], g1=g1,
                                       prompts=PROMPTS_V5), prior=prior)

    batch, context, _ = run_with([])
    assert batch.mentions[0].identity == "unresolved"
    assert context["explicit_identity_refs"] == {}
    batch, context, _ = run_with(["t0"])
    assert batch.mentions[0].identity == "named"
    assert context["explicit_identity_refs"] == {batch.mentions[0].id: "E_existing"}


@pytest.mark.parametrize("surface", ["I", "me", "My", "mine", "Myself", "we", "us", "OUR", "ours",
                                     "我", "我们", "俺", "咱们", "咱"])
def test_v5_first_person_on_assistant_turn_cannot_be_current_user(surface):
    content = (surface + " suggested asking Ada.") if surface.isascii() else (surface + "建议询问 Ada。")
    seg = segment(("assistant", content))
    g1 = {"mentions": [mention("speaker", surface, identity="current_user")],
          "projections": [projection(mentions=["speaker"])]}
    batch, _, _ = run_v5(seg, StagedModel([fact("Lumina suggested asking Ada.")], g1=g1,
                                          prompts=PROMPTS_V5))
    assert batch.mentions[0].identity == "unresolved"
    assert batch.unit_mentions[0].mentions == ()
    assert any(i.code == "reliable_identity_self_reference_role_conflict" for i in batch.issues)
    assert not any(i.code == "reliable_identity_evidence_deferred_to_g2" for i in batch.issues)


def test_v5_first_person_on_user_turn_binds_current_user_normally():
    batch, _, _ = run_v5(*borrowing_v5())
    owner = next(m for m in batch.mentions if m.surface == "my")
    assert owner.identity == "current_user"
    assert owner.id in batch.unit_mentions[0].mentions
    assert not any(i.code == "reliable_identity_self_reference_role_conflict" for i in batch.issues)


def test_v5_rejected_relation_voids_its_role_votes():
    seg, model = borrowing_v5()

    def g2(payload):
        result = approve_projections(payload)
        result["projections"][0].update(relation_supported=False,
                                        subject_role_supported=True,
                                        object_role_supported=True)
        return result

    model.responses["G2"] = g2
    batch, _, _ = run_v5(seg, model)
    unit, roles = batch.units[0], batch.unit_mentions[0]
    assert unit.subject is unit.relation is unit.value is None
    assert roles.subject is roles.object is None
    assert len(roles.mentions) == 2
    assert unit.formation_version == rf.FORMATION_RELIABLE_VERSION_V5
    assert unit.id.startswith("grounded_memory_v5:")
    assert all(m.id.startswith("mention_v5:") for m in batch.mentions)


def test_v5_one_call_composes_body_and_structure_phases():
    seg, composed_model = borrowing_v5()
    expected, expected_context, _ = run_v5(seg, composed_model)
    _, phase_model = borrowing_v5()
    store = ReceiptStore()
    accepted, f1, f2, progress = rf.form_reliable_bodies(
        seg, phase_model, progress=None, checkpoint=store,
        version=rf.FORMATION_RELIABLE_VERSION_V5)
    assert [s for s, _ in phase_model.calls] == ["F1", "F2"]
    batch, context = rf.form_reliable_structure(
        seg, phase_model, progress=progress, checkpoint=store, identity_candidates=(),
        accepted=accepted, f1=f1, f2=f2, version=rf.FORMATION_RELIABLE_VERSION_V5)
    assert batch == expected and context == expected_context
    assert [s for s, _ in phase_model.calls] == list(PROMPTS_V5)
    assert [s for s, _ in composed_model.calls] == list(PROMPTS_V5)


def test_v5_progress_schema_is_never_reinterpreted_as_v4():
    seg, model = borrowing_v5()
    _, _, store = run_v5(seg, model)
    assert store.latest["schema_version"] == rf.PROGRESS_VERSION_V5
    with pytest.raises(gf.FormationError, match="reliable_stage_checkpoint_invalid"):
        run(seg, NoCalls(), progress=store.latest)
    seg4, model4 = borrowing()
    _, _, store4 = run(seg4, model4)
    assert store4.latest["schema_version"] == rf.PROGRESS_VERSION
    with pytest.raises(gf.FormationError, match="reliable_stage_checkpoint_invalid"):
        run_v5(seg4, NoCalls(), progress=store4.latest)


PROMPTS_V6 = {"F1": rf.F1_PROMPT_V6, "F2": rf.F2_PROMPT,
              "G1": rp.G1_PROMPT_V5, "G2": rp.G2_PROMPT_V5}


def run_v6(seg, model, *, progress=None, store=None, prior=()):
    store = store if store is not None else ReceiptStore()
    batch, context = rf.form_reliable_batch(
        seg, model, progress=progress, checkpoint=store, identity_candidates=prior,
        version=rf.FORMATION_RELIABLE_VERSION_V6,
    )
    return batch, context, store


def borrowing_v6(**kwargs):
    seg, model = borrowing(**kwargs)
    model.prompts = PROMPTS_V6
    return seg, model


@pytest.mark.parametrize("origin,text,prefix", [
    ("t0", "Lumina 建议我先做小样。", "User stated: "),
    ("t1", "用户刚才说他明天会回来。", "Lumina stated: "),
    ("t0", "你刚才建议我先测试 A。", "User stated: "),
    ("t1", "The user said the plan already works.", "Lumina stated: "),
    ("t0", "The assistant asked me to wait for the result.", "User stated: "),
])
def test_v6_cross_reference_bodies_are_accepted_with_true_role_prefix(origin, text, prefix):
    seg = segment(("user", "先做哪个?"), ("assistant", "我建议先做小样。"))
    model = StagedModel([fact(text, origin)], prompts=PROMPTS_V6)
    batch, _, store = run_v6(seg, model)
    assert [unit.text for unit in batch.units] == [prefix + text]
    assert not any(issue.code == "reliable_fact_attribution_conflict" for issue in batch.issues)
    assert all(stage["status"] == "parsed" for stage in store.latest["stages"].values())


def test_v6_prefix_follows_real_role_and_f2_receives_canonical_candidates():
    seg = segment(("assistant", "用户刚才说他明天会回来。"),
                  ("user", "Lumina 建议我先做小样。"))
    model = StagedModel([fact("用户刚才说他明天会回来。", "t0"),
                         fact("Lumina 建议我先做小样。", "t1")], prompts=PROMPTS_V6)
    batch, context, _ = run_v6(seg, model)
    candidates = dict(model.calls)["F2"]["candidates"]
    assert [c["text"] for c in candidates] == [
        "Lumina stated: 用户刚才说他明天会回来。",
        "User stated: Lumina 建议我先做小样。"]
    assert [c["allowed_source_ids"] for c in candidates] == [["t0"], ["t0", "t1"]]
    assert [context["origins"][unit.id] for unit in batch.units] == ["t0", "t1"]
    assert [unit.formation_version for unit in batch.units] == [rf.FORMATION_RELIABLE_VERSION_V6] * 2


def test_v6_completed_receipts_replay_byte_identical_without_provider():
    seg, model = borrowing_v6()
    batch, context, store = run_v6(seg, model)
    assert store.latest["schema_version"] == rf.PROGRESS_VERSION_V6
    assert batch.units[0].id.startswith("grounded_memory_v6:")
    assert all(m.id.startswith("mention_v6:") for m in batch.mentions)
    replayed, replay_context, _ = run_v6(seg, NoCalls(), progress=store.latest)
    # Replayed parsed receipts must byte-match the saved ones or _stage fails
    # closed; identical batch/context confirms the same authorized result.
    assert replayed == batch and replay_context == context
    with pytest.raises(gf.FormationError, match="reliable_stage_checkpoint_invalid"):
        run_v5(seg, NoCalls(), progress=store.latest)
    with pytest.raises(gf.FormationError, match="reliable_stage_checkpoint_invalid"):
        run(seg, NoCalls(), progress=store.latest)


def test_v5_progress_is_never_reinterpreted_as_v6():
    seg, model = borrowing_v5()
    _, _, store = run_v5(seg, model)
    assert store.latest["schema_version"] == rf.PROGRESS_VERSION_V5
    with pytest.raises(gf.FormationError, match="reliable_stage_checkpoint_invalid"):
        run_v6(seg, NoCalls(), progress=store.latest)


@pytest.mark.parametrize("surface", ["I", "my", "我们", "咱"])
def test_v6_first_person_on_assistant_turn_cannot_be_current_user(surface):
    content = (surface + " suggested asking Ada.") if surface.isascii() else (surface + "建议询问 Ada。")
    seg = segment(("assistant", content))
    g1 = {"mentions": [mention("speaker", surface, identity="current_user")],
          "projections": [projection(mentions=["speaker"])]}
    batch, _, _ = run_v6(seg, StagedModel([fact("Lumina suggested asking Ada.")], g1=g1,
                                          prompts=PROMPTS_V6))
    assert batch.mentions[0].identity == "unresolved"
    assert batch.unit_mentions[0].mentions == ()
    assert any(i.code == "reliable_identity_self_reference_role_conflict" for i in batch.issues)
    assert not any(i.code == "reliable_identity_evidence_deferred_to_g2" for i in batch.issues)


def test_v6_dangling_identity_evidence_is_invalid_despite_g2_approval():
    seg, model = borrowing_v6()
    model.responses["G1"]["mentions"][0]["identity_source_ids"] = ["t9"]
    batch, _, _ = run_v6(seg, model)
    actor = next(m for m in batch.mentions if m.surface == "Ada")
    assert actor.identity == "unresolved"
    assert batch.unit_mentions[0].subject is None
    assert not any(i.code == "reliable_identity_evidence_deferred_to_g2" for i in batch.issues)


def test_v6_missing_identity_evidence_is_valid_for_new_and_current_user():
    seg, model = borrowing_v6()
    model.responses["G1"]["mentions"][0]["identity_source_ids"] = []
    model.responses["G1"]["mentions"][1]["identity_source_ids"] = []
    batch, _, _ = run_v6(seg, model)
    actor = next(m for m in batch.mentions if m.surface == "Ada")
    owner = next(m for m in batch.mentions if m.surface == "my")
    assert actor.identity == "new" and owner.identity == "current_user"
    assert batch.unit_mentions[0].subject == actor.id
    assert owner.id in batch.unit_mentions[0].mentions
    assert not any(i.code == "reliable_identity_evidence_deferred_to_g2" for i in batch.issues)


def test_v6_rejected_relation_voids_its_role_votes():
    seg, model = borrowing_v6()

    def g2(payload):
        result = approve_projections(payload)
        result["projections"][0].update(relation_supported=False,
                                        subject_role_supported=True,
                                        object_role_supported=True)
        return result

    model.responses["G2"] = g2
    batch, _, _ = run_v6(seg, model)
    unit, roles = batch.units[0], batch.unit_mentions[0]
    assert unit.subject is unit.relation is unit.value is None
    assert roles.subject is roles.object is None
    assert len(roles.mentions) == 2
    assert unit.formation_version == rf.FORMATION_RELIABLE_VERSION_V6
    assert unit.id.startswith("grounded_memory_v6:")
    assert all(m.id.startswith("mention_v6:") for m in batch.mentions)
