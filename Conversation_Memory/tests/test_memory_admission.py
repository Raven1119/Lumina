"""Frozen synthetic admission controls for the 07226c0 identity verdict gap.

These call the actual Formation and ingestion facade with deliberately
inconsistent verifier fields. They test admission, not model accuracy.
"""

from __future__ import annotations

import copy
import json

import pytest

from Conversation_Memory.adapter.entity_consolidation import EntityCandidate
from Conversation_Memory.adapter.grounded_formation import (
    FORMATION_ENTITY_VERSION,
    form_grounded_memory_batch,
    repair_grounded_memory_batch,
)
from Conversation_Memory.tests.test_entity_ingestion_v2 import (
    Backend, adapter, mention, segment,
)


def fact(cold, text, subject, relation, value, *, source=None, **roles):
    return {
        "text": text, "subject": subject, "relation": relation, "value": value,
        "source_refs": [{
            "turn_id": cold.turns[0].turn_id,
            "supporting_span": source if source is not None else cold.turns[0].content,
        }],
        "referenced_time": None, **roles,
    }


def identity_ref(cold):
    return [{"turn_id": cold.turns[0].turn_id, "supporting_span": cold.turns[0].content}]


class VerdictModel:
    def __init__(self, output=None, *, identity_false=(), mention_false=(), repairs=None):
        self.output = output
        self.identity_false = set(identity_false)
        self.mention_false = set(mention_false)
        self.repairs = repairs
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        assert recent_context == []
        payload = json.loads(user_message)
        if system_prompt.startswith("Extract source-grounded"):
            self.calls.append(("extract", payload))
            assert self.output is not None, "completed extraction must not repeat"
            return json.dumps(self.output, ensure_ascii=False)
        if system_prompt.startswith("Repair ONLY invalid source_refs"):
            self.calls.append(("repair", payload))
            assert self.repairs is not None, "repair was not authorized by this case"
            return json.dumps(self.repairs, ensure_ascii=False)
        assert system_prompt.startswith("Verify ONLY")
        self.calls.append(("verify", payload))
        return json.dumps({
            "units": [{"supported": True} for _ in payload["units"]],
            "mentions": [{
                "supported": item["surface"] not in self.mention_false,
                "identity_supported": item["surface"] not in self.identity_false,
            } for item in payload["mentions"]],
        })


class NoCalls:
    def __init__(self):
        self.calls = []

    def generate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("a completed checkpoint must make no model calls")


def pronoun_case(role, language="en"):
    if language == "en":
        target, other, independent = "Nadia", "Omar", "Cedar"
        if role == "subject":
            pronoun, source, text = "She", "She grows mint.", "Nadia grows mint."
            subject, relation, value = target, "grows", "mint"
        elif role == "object":
            pronoun, source, text = "her", "Cedar trusts her.", "Cedar trusts Nadia."
            subject, relation, value = independent, "trusts", target
        else:
            pronoun, source = "her", "Cedar rehearses beside her."
            text = "Cedar rehearses beside Nadia."
            subject, relation, value = independent, "rehearses beside", target
        good = "Cedar is available."
        content = f"Nadia and Omar arrived. {source} {good}"
        good_relation, good_value = "is", "available"
    else:
        target, other, independent, pronoun = "沈桐", "罗航", "竹项目", "她"
        if role == "subject":
            source, text = "她喜欢薄荷。", "沈桐喜欢薄荷。"
            subject, relation, value = target, "喜欢", "薄荷"
        elif role == "object":
            source, text = "竹项目由她负责。", "竹项目由沈桐负责。"
            subject, relation, value = independent, "由其负责", target
        else:
            source, text = "竹项目与她联合排练。", "竹项目与沈桐联合排练。"
            subject, relation, value = independent, "与其联合排练", target
        good = "竹项目可用。"
        content = f"{good}沈桐和罗航到了。{source}"
        good_relation, good_value = "状态", "可用"
    cold = segment(content, sid=f"admission-{language}-{role}")
    mentions = [
        mention(cold, pronoun, "pronoun", same_as="target", identity_source_refs=identity_ref(cold)),
        mention(cold, independent, "independent"),
        mention(cold, other, "other"), mention(cold, target, "target"),
    ]
    roles = {"subject_mention": "pronoun" if role == "subject" else "independent"}
    if role == "object":
        roles["object_mention"] = "pronoun"
    if role == "participant":
        roles["mentions"] = ["pronoun"]
    bad = fact(cold, text, subject, relation, value, **roles)
    healthy = fact(cold, good, independent, good_relation, good_value,
                   source=good, subject_mention="independent")
    units = [healthy, bad] if language == "zh" else [bad, healthy]
    return cold, {"mentions": mentions, "units": units}, pronoun, text, good


@pytest.mark.parametrize("role", ["subject", "object", "participant"])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_identity_false_blocks_only_fact_that_substitutes_a_name(role, language):
    cold, output, pronoun, bad, good = pronoun_case(role, language)
    model = VerdictModel(output, identity_false={pronoun})
    stages = {}
    batch = form_grounded_memory_batch(cold, model, checkpoint=stages.__setitem__)

    assert [unit.text for unit in batch.units] == [good]
    assert len(batch.mentions) == len(output["mentions"])
    unresolved = next(item for item in batch.mentions if item.surface == pronoun)
    assert unresolved.identity == "unresolved" and unresolved.same_as is None
    bad_index = next(i for i, item in enumerate(output["units"]) if item["text"] == bad)
    assert any(issue.candidate == "unit" and issue.index == bad_index
               and issue.status == "rejected" for issue in batch.issues)
    assert [kind for kind, _ in model.calls] == ["extract", "verify"]
    assert len(model.calls[1][1]["units"]) == 2
    no_calls = NoCalls()
    assert form_grounded_memory_batch(
        cold, no_calls, verified_checkpoint=stages["verified"],
    ) == batch
    assert no_calls.calls == []


@pytest.mark.parametrize("role", ["subject", "object", "participant"])
def test_retaining_the_pronoun_does_not_authorize_an_inserted_specific_name(role):
    cold, output, pronoun, _bad, good = pronoun_case(role)
    candidate = output["units"][0]
    if role == "subject":
        candidate["text"], candidate["subject"] = "She (Nadia) grows mint.", "She (Nadia)"
    elif role == "object":
        candidate["text"], candidate["value"] = "Cedar trusts her (Nadia).", "her (Nadia)"
    else:
        candidate["text"] = "Cedar rehearses beside her (Nadia)."
    model = VerdictModel(output, identity_false={pronoun})
    batch = form_grounded_memory_batch(cold, model)
    assert [item.text for item in batch.units] == [good]
    assert len(batch.mentions) == 4
    assert [kind for kind, _ in model.calls] == ["extract", "verify"]


@pytest.mark.parametrize("text", ["She (Nadia) grows mint.", "Nadia grows mint."])
def test_literal_subject_field_cannot_hide_a_name_substitution_in_fact_text(text):
    cold, output, pronoun, _bad, good = pronoun_case("subject")
    output["units"][0]["text"] = text
    output["units"][0]["subject"] = pronoun
    batch = form_grounded_memory_batch(cold, VerdictModel(output, identity_false={pronoun}))
    assert [item.text for item in batch.units] == [good]


def test_literal_body_cannot_authorize_a_different_structured_value():
    cold = segment("Tao and Luis arrived. He likes tea. Cedar serves coffee.",
                   sid="admission-literal-wrong-value")
    output = {"mentions": [
        mention(cold, "Tao", "target"),
        mention(cold, "He", "pronoun", same_as="target", identity_source_refs=identity_ref(cold)),
        mention(cold, "Cedar", "independent"),
    ], "units": [
        fact(cold, "He likes tea.", "He", "likes", "coffee", subject_mention="pronoun"),
        fact(cold, "Cedar serves coffee.", "Cedar", "serves", "coffee", subject_mention="independent"),
    ]}
    batch = form_grounded_memory_batch(cold, VerdictModel(output, identity_false={"He"}))
    assert [item.text for item in batch.units] == ["Cedar serves coffee."]
    assert len(batch.mentions) == 3


@pytest.mark.parametrize("failed_field", ["identity", "mention"])
def test_failed_middle_same_as_target_invalidates_transitive_name_substitution(failed_field):
    cold = segment("Marta also goes by May. May is called M. She grows thyme. Spruce is ready.",
                   sid="admission-chain")
    refs = identity_ref(cold)
    output = {"mentions": [
        mention(cold, "She", "pronoun", same_as="short", identity_source_refs=refs),
        mention(cold, "Marta", "name"),
        mention(cold, "May", "alias", same_as="name", identity_source_refs=refs),
        mention(cold, "M", "short", start=cold.turns[0].content.index("M. "),
                same_as="alias", identity_source_refs=refs),
        mention(cold, "Spruce", "independent"),
    ], "units": [
        fact(cold, "Marta grows thyme.", "Marta", "grows", "thyme", subject_mention="pronoun"),
        fact(cold, "Spruce is ready.", "Spruce", "is", "ready", subject_mention="independent"),
    ]}
    model = VerdictModel(output, **{f"{failed_field}_false": {"May"}})
    batch = form_grounded_memory_batch(cold, model)

    assert [item.text for item in batch.units] == ["Spruce is ready."]
    for surface in ("M", "She"):
        mention_record = next(item for item in batch.mentions if item.surface == surface)
        assert mention_record.identity == "unresolved" and mention_record.same_as is None
    assert next(item for item in batch.mentions if item.surface == "Marta").identity == "named"
    assert next(item for item in batch.mentions if item.surface == "Spruce").identity == "named"
    assert ("May" in {item.surface for item in batch.mentions}) == (failed_field == "identity")
    assert [kind for kind, _ in model.calls] == ["extract", "verify"]


def test_distinct_from_conflicts_with_transitive_same_as_without_blocking_other_entity():
    cold = segment("Ren is called Rowan. Rowan is called Ro. Ro is different from Ren. Ro likes tea. Birch is ready.",
                   sid="admission-distinct")
    refs = identity_ref(cold)
    output = {"mentions": [
        mention(cold, "Ren", "name"),
        mention(cold, "Rowan", "alias", same_as="name", identity_source_refs=refs),
        mention(cold, "Ro", "short", start=cold.turns[0].content.index("Ro. "),
                same_as="alias", distinct_from=["name"], identity_source_refs=refs),
        mention(cold, "Birch", "independent"),
    ], "units": [
        fact(cold, "Ren likes tea.", "Ren", "likes", "tea", subject_mention="short"),
        fact(cold, "Birch is ready.", "Birch", "is", "ready", subject_mention="independent"),
    ]}
    batch = form_grounded_memory_batch(cold, VerdictModel(output))
    assert [item.text for item in batch.units] == ["Birch is ready."]
    assert len(batch.mentions) == 4
    by_surface = {item.surface: item for item in batch.mentions}
    assert by_surface["Ren"].identity == by_surface["Rowan"].identity == "named"
    assert by_surface["Rowan"].same_as == by_surface["Ren"].id
    assert by_surface["Birch"].identity == "named"
    assert any(issue.candidate in {"identity", "unit"} for issue in batch.issues)


@pytest.mark.parametrize("kind", ["named", "pronoun", "no_role"])
def test_source_literal_fact_survives_without_a_supported_identity_binding(kind):
    text = "He likes tea." if kind == "pronoun" else "Tao likes tea."
    cold = segment(f"Tao and Luis arrived. {text}", sid=f"admission-literal-{kind}")
    surface = "He" if kind == "pronoun" else "Tao"
    identity = {"same_as": "target", "identity_source_refs": identity_ref(cold)} if kind == "pronoun" else {}
    mentions = [mention(cold, surface, "literal", **identity)]
    if kind == "pronoun":
        mentions.append(mention(cold, "Tao", "target"))
    output = {"mentions": mentions, "units": [fact(
        cold, text, surface, "likes", "tea", source=text,
        subject_mention=None if kind == "no_role" else "literal",
    )]}
    batch = form_grounded_memory_batch(cold, VerdictModel(output, identity_false={surface}))
    assert [item.text for item in batch.units] == [text]
    assert next(item for item in batch.mentions if item.surface == surface).identity == "unresolved"
    assert not any(issue.candidate == "unit" for issue in batch.issues)


def test_public_ingest_and_restart_never_persist_rejected_pronoun_substitution(tmp_path):
    cold, output, pronoun, _bad, good = pronoun_case("subject")
    backend, model = Backend(), VerdictModel(output, identity_false={pronoun})
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(cold)
    assert result.status == "completed"
    assert [event["text"] for event in backend.events.values()] == [good]
    record = next(item for item in backend.mentions.values() if item["surface"] == pronoun)
    assert record["entity_ref"] is None and record["identity"] == "unresolved"
    frozen = copy.deepcopy((backend.events, backend.mentions, memory.state_store.get(
        memory.state_store.key(cold.segment_id, FORMATION_ENTITY_VERSION),
    )))
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    repeated = restarted.ingest(cold)
    assert repeated.already_ingested and repeated.memory_ids == result.memory_ids
    assert (backend.events, backend.mentions, restarted.state_store.get(
        restarted.state_store.key(cold.segment_id, FORMATION_ENTITY_VERSION),
    )) == frozen
    assert no_calls.calls == []


def test_verified_new_local_alias_still_reuses_target_with_older_same_name(tmp_path):
    cold = segment("Another Sol is a sculptor. This Sol also goes by Solar. Solar shapes clay.",
                   sid="admission-new-alias")
    output = {"mentions": [
        mention(cold, "Sol", "new", identity="new", identity_source_refs=identity_ref(cold)),
        mention(cold, "Solar", "alias", same_as="new", identity_source_refs=identity_ref(cold)),
    ], "units": [fact(cold, "Solar shapes clay.", "Solar", "shapes", "clay", subject_mention="alias")]}
    backend = Backend()
    backend.candidates.append(EntityCandidate("older-sol", "Sol"))
    result = adapter(tmp_path, backend, VerdictModel(output)).ingest(cold)
    assert result.status == "completed"
    records = {record["surface"]: record for record in backend.mentions.values()}
    assert records["Solar"]["entity_ref"] == records["Sol"]["entity_ref"]
    assert records["Sol"]["entity_ref"] not in {None, "older-sol"}


@pytest.mark.parametrize("role", ["subject", "object", "participant"])
def test_repair_rejects_new_identity_dependency_without_mutating_previous_batch(role):
    cold, output, pronoun, bad, good = pronoun_case(role)
    bad_index = next(i for i, item in enumerate(output["units"]) if item["text"] == bad)
    output["units"][bad_index]["source_refs"][0]["supporting_span"] = "missing citation"
    stages = {}
    base = form_grounded_memory_batch(cold, VerdictModel(output), checkpoint=stages.__setitem__)
    frozen = copy.deepcopy(stages["verified"])
    model = VerdictModel(identity_false={pronoun}, repairs={"repairs": [{
        "index": bad_index, "source_refs": identity_ref(cold),
    }]})
    repaired = repair_grounded_memory_batch(
        cold, model, extracted_checkpoint=stages["extracted"],
        verified_checkpoint=stages["verified"], checkpoint=stages.__setitem__,
    )
    assert [item.text for item in repaired.units] == [good]
    assert repaired.mentions == base.mentions and repaired.unit_mentions == base.unit_mentions
    assert stages["verified"] == frozen
    assert [kind for kind, _ in model.calls] == ["repair", "verify"]
    assert [item["candidate"]["text"] for item in model.calls[1][1]["units"]] == [bad]
    no_calls = NoCalls()
    assert form_grounded_memory_batch(
        cold, no_calls, verified_checkpoint=stages["repair_verified"],
    ) == repaired
    assert no_calls.calls == []


def test_repair_can_keep_source_literal_fact_with_already_unresolved_mention():
    cold = segment("Tao likes tea. Fern is available.", sid="admission-literal-repair")
    output = {"mentions": [mention(cold, "Tao", "tao"), mention(cold, "Fern", "fern")],
              "units": [
                  fact(cold, "Tao likes tea.", "Tao", "likes", "tea", source="missing citation", subject_mention="tao"),
                  fact(cold, "Fern is available.", "Fern", "is", "available", source="Fern is available.", subject_mention="fern"),
              ]}
    stages = {}
    base = form_grounded_memory_batch(
        cold, VerdictModel(output, identity_false={"Tao"}), checkpoint=stages.__setitem__,
    )
    model = VerdictModel(identity_false={"Tao"}, repairs={"repairs": [{
        "index": 0, "source_refs": [{"turn_id": cold.turns[0].turn_id, "supporting_span": "Tao likes tea."}],
    }]})
    repaired = repair_grounded_memory_batch(
        cold, model, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages["verified"],
    )
    assert [item.text for item in repaired.units] == ["Fern is available.", "Tao likes tea."]
    assert repaired.mentions == base.mentions
    assert next(item for item in repaired.mentions if item.surface == "Tao").identity == "unresolved"
    assert [kind for kind, _ in model.calls] == ["repair", "verify"]
