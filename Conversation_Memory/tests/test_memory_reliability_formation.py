"""Candidate isolation must preserve source checks and mandatory verification.

These deterministic decisions test orchestration, not the model's ability to
understand a source. Captured verifier inputs establish the isolation boundary.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from adapter.grounded_formation import (
    FormationError,
    form_grounded_memory_batch,
    repair_grounded_memory_batch,
)
from test_entity_formation_v2 import Model, mention, segment, unit


class RecordingModel(Model):
    def __init__(self, output, *, reject_text=None):
        super().__init__(output)
        self.reject_text = reject_text
        self.requests = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.requests.append((system_prompt, user_message))
        if self.reject_text is not None and system_prompt.startswith("Verify ONLY"):
            payload = json.loads(user_message)
            self.decisions = {
                "units": [
                    {"supported": item["candidate"]["text"] != self.reject_text}
                    for item in payload["units"]
                ],
                "mentions": [
                    {"supported": True, "identity_supported": True}
                    for _ in payload["mentions"]
                ],
            }
        return super().generate(
            recent_context, user_message, system_prompt=system_prompt,
        )


def verifier_payload(model):
    assert len(model.calls) == 2
    assert model.calls[1][0].startswith("Verify ONLY")
    return model.calls[1][1]


def issue_locations(batch, status):
    return {(item.candidate, item.index) for item in batch.issues if item.status == status}


@pytest.mark.parametrize("dependency_role", ["subject_mention", "object_mention", "mentions"])
@pytest.mark.parametrize("reject_first", [False, True])
def test_bad_mention_only_isolates_units_that_reference_its_handle(dependency_role, reject_first):
    source = "Iris grows mint. Oren uses copper. Oren lives in Quito."
    bad = {
        "handle": "bad", "surface": "Absent", "turn_id": "u1",
        "occurrence": 0, "identity": "named",
    }
    dependency = ["bad"] if dependency_role == "mentions" else "bad"
    output = {
        "mentions": [mention("iris", "Iris", source), bad, mention("city", "Quito", source)],
        "units": [
            unit("Iris grows mint.", "Iris", "grows", "mint", "Iris grows mint.", subject_mention="iris"),
            unit("Oren uses copper.", "Oren", "uses", "copper", "Oren uses copper.", **{dependency_role: dependency}),
            # Sharing a nearby name is not a dependency on the broken handle.
            unit("Oren lives in Quito.", "Oren", "lives in", "Quito", "Oren lives in Quito.", object_mention="city"),
        ],
    }
    model = RecordingModel(output, reject_text="Iris grows mint." if reject_first else None)
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), model)
    verification = verifier_payload(model)

    expected = ["Iris grows mint.", "Oren lives in Quito."]
    assert [item["candidate"]["text"] for item in verification["units"]] == expected
    assert [item.text for item in batch.units] == (expected[1:] if reject_first else expected)
    assert [item["surface"] for item in verification["mentions"]] == ["Iris", "Quito"]
    assert [item.surface for item in batch.mentions] == ["Iris", "Quito"]
    assert issue_locations(batch, "pending") == {("mention", 1), ("unit", 1)}
    assert issue_locations(batch, "rejected") == ({("unit", 0)} if reject_first else set())


def test_duplicate_handle_isolates_both_occurrences_and_only_its_consumers():
    source = "Iris grows mint. Oren uses copper. Nora uses silver. Oren lives in Quito."
    output = {
        "mentions": [
            mention("iris", "Iris", source), mention("duplicate", "Oren", source),
            mention("duplicate", "Nora", source), mention("city", "Quito", source),
        ],
        "units": [
            unit("Iris grows mint.", "Iris", "grows", "mint", "Iris grows mint.", subject_mention="iris"),
            unit("Oren uses copper.", "Oren", "uses", "copper", "Oren uses copper.", subject_mention="duplicate"),
            unit("Nora uses silver.", "Nora", "uses", "silver", "Nora uses silver.", subject_mention="duplicate"),
            unit("Oren lives in Quito.", "Oren", "lives in", "Quito", "Oren lives in Quito.", object_mention="city"),
        ],
    }
    model = RecordingModel(output)
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), model)
    verification = verifier_payload(model)

    assert [item["surface"] for item in verification["mentions"]] == ["Iris", "Quito"]
    assert [item["candidate"]["text"] for item in verification["units"]] == [
        "Iris grows mint.", "Oren lives in Quito.",
    ]
    assert [item.text for item in batch.units] == ["Iris grows mint.", "Oren lives in Quito."]
    assert issue_locations(batch, "pending") == {
        ("mention", 1), ("mention", 2), ("unit", 1), ("unit", 2),
    }


@pytest.mark.parametrize("broken_identity", ["same_as_cycle", "distinct_missing", "distinct_bad_mention"])
def test_broken_identity_stays_unresolved_and_cannot_supply_missing_fact_subject(broken_identity):
    source = "Aster uses copper. Ash uses silver. He grows moss. Elm grows moss."
    refs = [{"turn_id": "u1", "supporting_span": source}]
    first = mention("aster", "Aster", source, identity_source_refs=refs)
    second = mention("ash", "Ash", source, same_as="aster", identity_source_refs=refs)
    if broken_identity == "same_as_cycle":
        first["same_as"] = "ash"
    else:
        first["distinct_from"] = ["bad_target"]
    mentions = [first, second, mention("elm", "Elm", source)]
    if broken_identity == "distinct_bad_mention":
        mentions.append({
            "handle": "bad_target", "surface": "Absent", "turn_id": "u1",
            "occurrence": 0, "identity": "named",
        })
    output = {
        "mentions": mentions,
        "units": [
            unit("Aster uses copper.", "Aster", "uses", "copper", "Aster uses copper.", subject_mention="aster"),
            unit("Aster grows moss.", "Aster", "grows", "moss", "He grows moss.", subject_mention="aster"),
            unit("Elm grows moss.", "Elm", "grows", "moss", "Elm grows moss.", subject_mention="elm"),
        ],
    }
    model = RecordingModel(output)
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), model)
    verification = verifier_payload(model)

    assert [item["identity"] for item in verification["mentions"]] == [
        "unresolved", "unresolved", "named",
    ]
    assert [item.identity for item in batch.mentions] == ["unresolved", "unresolved", "named"]
    assert all(item.same_as is None and not item.distinct_from for item in batch.mentions)
    expected = ["Aster uses copper.", "Elm grows moss."]
    assert [item["candidate"]["text"] for item in verification["units"]] == expected
    assert [item.text for item in batch.units] == expected
    pending = {("identity", 0), ("identity", 1), ("unit", 1)}
    if broken_identity == "distinct_bad_mention":
        pending.add(("mention", 3))
    assert issue_locations(batch, "pending") == pending
    assert not issue_locations(batch, "rejected")


@pytest.mark.parametrize("source", ["Does Iris grow mint?", "Suppose Iris grows mint."])
def test_explicit_semantic_false_is_rejection_after_original_verification(source):
    good = "Elm grows moss."
    output = {
        "mentions": [mention("iris", "Iris", source), mention("elm", "Elm", good, turn_id="u2")],
        "units": [
            unit("Iris grows mint.", "Iris", "grows", "mint", source, subject_mention="iris"),
            unit(good, "Elm", "grows", "moss", good, turn_id="u2", subject_mention="elm"),
        ],
    }
    model = RecordingModel(output, reject_text="Iris grows mint.")
    batch = form_grounded_memory_batch(segment(("u1", "user", source), ("u2", "user", good)), model)
    verification = verifier_payload(model)

    assert [item["candidate"]["text"] for item in verification["units"]] == ["Iris grows mint.", good]
    assert [item.text for item in batch.units] == [good]
    assert issue_locations(batch, "rejected") == {("unit", 0)}
    assert not issue_locations(batch, "pending")


@pytest.mark.parametrize("rejection", ["assistant_only", "unsupported_number"])
def test_role_and_numeric_rejections_do_not_become_retryable_processing_failures(rejection):
    good = "Elm grows moss."
    if rejection == "assistant_only":
        source, role, text = "I deleted the file.", "assistant", "I deleted the file."
        bad = unit(text, "I", "deleted", "file", source)
        expected_code = "formation_role_unsupported"
    else:
        source, role, text = "Iris uses 200 nm film.", "user", "Iris uses 300 nm film."
        bad = unit(text, "Iris", "uses", "300 nm film", source)
        expected_code = "formation_detail_unsupported"
    model = RecordingModel({
        "mentions": [mention("elm", "Elm", good, turn_id="u2")],
        "units": [bad, unit(good, "Elm", "grows", "moss", good, turn_id="u2", subject_mention="elm")],
    })
    batch = form_grounded_memory_batch(segment(("u1", role, source), ("u2", "user", good)), model)
    verification = verifier_payload(model)

    assert [item["candidate"]["text"] for item in verification["units"]] == [good]
    assert [item.text for item in batch.units] == [good]
    assert [(item.candidate, item.index, item.code, item.status) for item in batch.issues] == [
        ("unit", 0, expected_code, "rejected"),
    ]


def healthy_request_case():
    source = "Aster also goes by Ash. Aster studies Ceramic-Q. Ceramic-Q is 240 nm thick."
    output = {
        "mentions": [
            mention("material", "Ceramic-Q", source),
            mention("alias", "Ash", source, same_as="person", identity_source_refs=[
                {"turn_id": "u1", "supporting_span": "Aster also goes by Ash."},
            ]),
            mention("person", "Aster", source),
        ],
        "units": [
            unit("Aster studies Ceramic-Q.", "Aster", "studies", "Ceramic-Q", "Aster studies Ceramic-Q.", subject_mention="person", object_mention="material"),
            unit("Ceramic-Q is 240 nm thick.", "Ceramic-Q", "thickness", "240 nm", "Ceramic-Q is 240 nm thick.", subject_mention="material"),
        ],
    }
    return segment(("u1", "user", source)), output


def test_healthy_extraction_and_verifier_request_bytes_match_frozen_head():
    cold, output = healthy_request_case()
    model = RecordingModel(output)
    batch = form_grounded_memory_batch(cold, model)

    assert len(batch.units) == 2
    assert len(batch.mentions) == 3
    assert batch.issues == ()
    # SHA256 of actual prompt and serialized user_message at immutable baseline
    # 2a009088b434d181482cef0983f7bba1f3be4153, using this exact healthy case.
    expected = (
        ("2f3f17bd604eaa2819726dd848b8d3772a56ee4b750e634423f4a10b9bb24348",
         "88cdc2e93dc300813549a1fe82e9e206363b301b9369e2d35cd8fa938a85091e"),
        ("bd2d68ece850fcd1b1a81d542aa2fb09d1cb2ba66fc3d5b07ce6eee88ca3d431",
         "029b0eb2916e6eedd207a4f549e934c8dd5cf0094cfa5ebc12fbc0cbc4ec70ef"),
    )
    assert tuple(
        tuple(hashlib.sha256(value.encode("utf-8")).hexdigest() for value in request)
        for request in model.requests
    ) == expected


class RepairModel(RecordingModel):
    def __init__(self, repairs=None, *, reject_mention_field=None, raw_response=None,
                 fail_verifier=False):
        super().__init__(None)
        self.repairs = repairs
        self.reject_mention_field = reject_mention_field
        self.raw_response = raw_response
        self.fail_verifier = fail_verifier

    def generate(self, recent_context, user_message, *, system_prompt):
        if system_prompt.startswith("Repair ONLY invalid source_refs"):
            assert recent_context == []
            self.requests.append((system_prompt, user_message))
            self.calls.append((system_prompt, json.loads(user_message)))
            return self.raw_response if self.raw_response is not None else json.dumps(self.repairs)
        if self.reject_mention_field is not None and system_prompt.startswith("Verify ONLY"):
            payload = json.loads(user_message)
            decisions = [{"supported": True, "identity_supported": True} for _ in payload["mentions"]]
            for item, decision in zip(payload["mentions"], decisions):
                if item["surface"] == "Oren":
                    decision[self.reject_mention_field] = False
            self.decisions = {
                "units": [{"supported": True} for _ in payload["units"]],
                "mentions": decisions,
            }
        return super().generate(recent_context, user_message, system_prompt=system_prompt)


def source_repair_case():
    source = "Iris grows mint. Oren uses copper. Nora uses silver."
    cold = segment(("u1", "user", source))
    output = {
        "mentions": [mention(name.lower(), name, source) for name in ("Iris", "Oren", "Nora")],
        "units": [
            unit("Iris grows mint.", "Iris", "grows", "mint", "Iris grows mint.", subject_mention="iris"),
            unit("Oren uses copper.", "Oren", "uses", "copper", "absent Oren citation", subject_mention="oren"),
            unit("Nora uses silver.", "Nora", "uses", "silver", "absent Nora citation", subject_mention="nora"),
        ],
    }
    stages = {}
    original_model = RecordingModel(output)
    base = form_grounded_memory_batch(
        cold, original_model, checkpoint=lambda stage, payload: stages.__setitem__(stage, payload),
    )
    repairs = {"repairs": [
        {"index": index, "source_refs": [{"turn_id": "u1", "supporting_span": text}]}
        for index, text in ((1, "Oren uses copper."), (2, "Nora uses silver."))
    ]}
    assert [item.text for item in base.units] == ["Iris grows mint."]
    return cold, output, stages, base, repairs


@pytest.mark.parametrize("unusable_refs", [[], [{"turn_id": "u1", "supporting_span": "still absent"}]])
def test_one_unlocated_repair_preserves_other_repaired_candidate_and_original_batch(unusable_refs):
    cold, output, stages, base, repairs = source_repair_case()
    repairs["repairs"][1]["source_refs"] = unusable_refs
    original_stages = deepcopy(stages)
    model = RepairModel(repairs)
    merged = repair_grounded_memory_batch(
        cold, model, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages["verified"],
        checkpoint=lambda stage, payload: stages.__setitem__(stage, payload),
    )
    verification = verifier_payload(model)

    assert model.calls[0][1]["units"] == [
        {"index": index, "candidate": output["units"][index]} for index in (1, 2)
    ]
    assert [item["candidate"]["text"] for item in verification["units"]] == ["Oren uses copper."]
    assert [item["surface"] for item in verification["mentions"]] == ["Oren"]
    assert merged.units[:len(base.units)] == base.units
    assert merged.unit_mentions[:len(base.unit_mentions)] == base.unit_mentions
    assert merged.mentions == base.mentions
    assert [item.text for item in merged.units] == ["Iris grows mint.", "Oren uses copper."]
    assert issue_locations(merged, "pending") == {("unit", 2)}
    assert issue_locations(merged, "repaired") == {("unit", 1)}
    assert {key: stages[key] for key in original_stages} == original_stages
    assert set(stages) == {"extracted", "verified", "repair", "repair_verified"}


@pytest.mark.parametrize("rejected_field", ["supported", "identity_supported"])
def test_repair_verifier_mention_rejection_only_blocks_its_new_fact(rejected_field):
    cold, _output, stages, base, repairs = source_repair_case()
    original_stages = deepcopy(stages)
    model = RepairModel(repairs, reject_mention_field=rejected_field)
    merged = repair_grounded_memory_batch(
        cold, model, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages["verified"],
    )
    verification = verifier_payload(model)

    assert [item["surface"] for item in verification["mentions"]] == ["Oren", "Nora"]
    assert [item.text for item in merged.units] == ["Iris grows mint.", "Nora uses silver."]
    assert merged.mentions == base.mentions
    assert merged.unit_mentions[:len(base.unit_mentions)] == base.unit_mentions
    assert issue_locations(merged, "rejected") == {("unit", 1)}
    assert not issue_locations(merged, "pending")
    assert stages == original_stages


def test_invalid_repair_response_is_durable_and_cannot_trigger_another_repair_call():
    cold, _output, stages, _base, _repairs = source_repair_case()
    model = RepairModel(raw_response="not a JSON response")
    with pytest.raises(FormationError, match="formation_repair_output_invalid"):
        repair_grounded_memory_batch(
            cold, model, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages["verified"],
            checkpoint=lambda stage, payload: stages.__setitem__(stage, payload),
        )
    assert len(model.calls) == 1
    assert stages["repair"]["response"] == "not a JSON response"
    retry = RepairModel()
    with pytest.raises(FormationError, match="formation_repair_output_invalid"):
        repair_grounded_memory_batch(
            cold, retry, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages["verified"],
            repair_checkpoint=stages["repair"],
        )
    assert retry.calls == []
    assert "repair_verified" not in stages


def test_repair_verifier_retry_reuses_receipt_and_merged_restart_runs_no_model():
    cold, _output, stages, base, repairs = source_repair_case()
    model = RepairModel(repairs, fail_verifier=True)
    with pytest.raises(FormationError, match="formation_verification_failed"):
        repair_grounded_memory_batch(
            cold, model, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages["verified"],
            checkpoint=lambda stage, payload: stages.__setitem__(stage, payload),
        )
    assert len(model.calls) == 2
    assert "repair" in stages and "repair_verified" not in stages
    retry = RepairModel()
    merged = repair_grounded_memory_batch(
        cold, retry, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages["verified"],
        repair_checkpoint=stages["repair"],
        checkpoint=lambda stage, payload: stages.__setitem__(stage, payload),
    )
    assert len(retry.calls) == 1 and retry.calls[0][0].startswith("Verify ONLY")
    assert len(merged.units) == 3 and merged.mentions == base.mentions
    restarted = RepairModel()
    assert form_grounded_memory_batch(cold, restarted, verified_checkpoint=stages["repair_verified"]) == merged
    assert restarted.calls == []


@pytest.mark.parametrize("fact_handle", ["first", "second"])
def test_repair_preserves_every_valid_handle_for_one_deduplicated_occurrence(fact_handle):
    source = "Iris grows mint. Iris uses copper."
    cold = segment(("u1", "user", source))
    output = {
        "mentions": [mention("first", "Iris", source), mention("second", "Iris", source)],
        "units": [
            unit("Iris grows mint.", "Iris", "grows", "mint", "Iris grows mint.", subject_mention="first"),
            unit("Iris uses copper.", "Iris", "uses", "copper", "absent citation", subject_mention=fact_handle),
        ],
    }
    stages = {}
    base = form_grounded_memory_batch(
        cold, RecordingModel(output), checkpoint=lambda stage, payload: stages.__setitem__(stage, payload),
    )
    assert len(base.mentions) == 1 and len(base.units) == 1
    model = RepairModel({"repairs": [{
        "index": 1, "source_refs": [{"turn_id": "u1", "supporting_span": "Iris uses copper."}],
    }]})
    merged = repair_grounded_memory_batch(
        cold, model, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages["verified"],
    )

    assert [item.text for item in merged.units] == ["Iris grows mint.", "Iris uses copper."]
    assert merged.mentions == base.mentions
    assert merged.unit_mentions[1].subject == base.mentions[0].id
    assert len(verifier_payload(model)["units"]) == 1
    assert not issue_locations(merged, "pending")


def test_repair_verifies_alias_antecedent_without_revisiting_unrelated_mentions():
    source = "Oren is called Ori. Ori uses copper. Iris grows mint."
    cold = segment(("u1", "user", source))
    output = {
        "mentions": [
            mention("oren", "Oren", source),
            mention("ori", "Ori", source, same_as="oren", identity_source_refs=[
                {"turn_id": "u1", "supporting_span": "Oren is called Ori."},
            ]),
            mention("iris", "Iris", source),
        ],
        "units": [
            unit("Iris grows mint.", "Iris", "grows", "mint", "Iris grows mint.", subject_mention="iris"),
            unit("Ori uses copper.", "Ori", "uses", "copper", "absent citation", subject_mention="ori"),
        ],
    }
    stages = {}
    base = form_grounded_memory_batch(
        cold, RecordingModel(output), checkpoint=lambda stage, payload: stages.__setitem__(stage, payload),
    )
    model = RepairModel({"repairs": [{
        "index": 1, "source_refs": [{"turn_id": "u1", "supporting_span": "Ori uses copper."}],
    }]}, reject_mention_field="identity_supported")
    merged = repair_grounded_memory_batch(
        cold, model, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages["verified"],
    )
    verification = verifier_payload(model)

    assert [item["surface"] for item in verification["mentions"]] == ["Oren", "Ori"]
    assert verification["mentions"][1]["same_as"] == verification["mentions"][0]["id"]
    assert merged.units == base.units and merged.unit_mentions == base.unit_mentions
    assert merged.mentions == base.mentions
    assert issue_locations(merged, "rejected") == {("unit", 1)}
    assert not issue_locations(merged, "pending")
