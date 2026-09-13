"""Frozen source-coverage controls against the actual Formation/ingestion path.

The verifier is scripted to isolate evidence preparation and safety routing;
these tests do not claim real-provider semantic accuracy.
"""

from __future__ import annotations

import copy
import json
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from Conversation_Memory.adapter.grounded_formation import (
    FORMATION_ENTITY_VERSION, FORMATION_PROGRESS_VERSION,
    FormationError, _entity_source_digest, form_grounded_memory_batch,
    serialize_grounded_memory_batch, validate_persisted_grounded_memory_batch,
)
from Conversation_Memory.tests.test_entity_ingestion_v2 import Backend, adapter, mention, segment
from Conversation_Memory.tests.test_memory_admission import NoCalls


@pytest.fixture(autouse=True)
def isolate_decision_log(tmp_path, monkeypatch):
    assert tmp_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    monkeypatch.setenv("LUMINA_MIND_DECISION_LOG_PATH", str(tmp_path / "mind_decisions.jsonl"))


class CoverageModel:
    def __init__(self, output=None, *, reject=False, identity_false=(), fail_verify=False, repairs=None):
        self.raw = json.dumps(output, ensure_ascii=False, separators=(",", ":")) if output is not None else None
        self.reject, self.identity_false = reject, set(identity_false)
        self.fail_verify, self.repairs = fail_verify, repairs
        self.calls, self.verifications = [], []

    def generate(self, recent_context, user_message, *, system_prompt):
        assert recent_context == []
        payload = json.loads(user_message)
        if system_prompt.startswith("Extract source-grounded"):
            self.calls.append("extract")
            assert self.raw is not None, "successful extraction must not repeat"
            return self.raw
        if system_prompt.startswith("Repair ONLY invalid source_refs"):
            self.calls.append("repair")
            assert self.repairs is not None
            return json.dumps(self.repairs)
        assert system_prompt.startswith("Verify ONLY")
        self.calls.append("verify")
        self.verifications.append(payload)
        if self.fail_verify:
            raise RuntimeError("synthetic verifier transport failure")
        return json.dumps({
            "units": [{"supported": not self.reject} for _ in payload["units"]],
            "mentions": [{"supported": True, "identity_supported": item["surface"] not in self.identity_false}
                         for item in payload["mentions"]],
        })


def fact(cold, text, subject, relation, value, span, **roles):
    return {"text": text, "subject": subject, "relation": relation, "value": value,
            "referenced_time": None,
            "source_refs": [{"turn_id": cold.turns[0].turn_id, "supporting_span": span}], **roles}


def attribute_case(kind="en_identifier"):
    if kind == "zh_reverse":
        source = "颜色为银白色的设备是 槐木-58，容量为48 GB。"
        name, span, text = "槐木-58", "颜色为银白色", "槐木-58 的颜色为银白色。"
        relation, value, effective = "颜色", "银白色", "颜色为银白色的设备是 槐木-58"
    else:
        name = "Juniper" if kind == "plain_name" else "Juniper-62"
        source = f"Device {name} has capacity 96 GB, color is maroon."
        span, text, relation, value = "color is maroon.", f"{name} is maroon.", "color", "maroon"
        effective = span if kind == "plain_name" else f"{name} has capacity 96 GB, color is maroon."
    cold = segment(source, sid=f"source-coverage-{kind}")
    output = {"mentions": [mention(cold, name, "device")], "units": [
        fact(cold, text, name, relation, value, span, subject_mention="device"),
    ]}
    return cold, output, effective


def object_case():
    cold = segment("Owner Asha-26 coordinates project Willow-83, now operational.", sid="source-coverage-object")
    output = {"mentions": [mention(cold, "Willow-83", "project"), mention(cold, "Asha-26", "person")],
              "units": [fact(cold, "Asha-26 coordinates Willow-83.", "Asha-26", "coordinates", "Willow-83",
                             "coordinates project", subject_mention="person", object_mention="project")]}
    return cold, output, "Asha-26 coordinates project Willow-83"


@pytest.mark.parametrize("kind", ["en_identifier", "zh_reverse", "plain_name", "object"])
def test_short_valid_refs_reach_verifier_with_required_explicit_role_source(kind):
    cold, output, effective = object_case() if kind == "object" else attribute_case(kind)
    original = copy.deepcopy(output)
    model, stages = CoverageModel(output), {}
    batch = form_grounded_memory_batch(cold, model, checkpoint=stages.__setitem__)
    assert model.calls == ["extract", "verify"]
    assert len(model.verifications[0]["units"]) == 1
    assert len(batch.units) == 1
    candidate = model.verifications[0]["units"][0]["candidate"]
    assert candidate["source_refs"] == [{"turn_id": cold.turns[0].turn_id, "supporting_span": effective}]
    for key in ("text", "subject", "relation", "value", "referenced_time"):
        assert candidate[key] == original["units"][0][key]
    assert batch.units[0].source_refs[0].supporting_span == effective
    assert stages["extracted"]["response"] == model.raw
    assert json.loads(stages["extracted"]["response"]) == original and output == original
    assert validate_persisted_grounded_memory_batch(batch, cold)


def test_expanded_and_already_sufficient_refs_have_the_same_stable_evidence_id():
    cold, output, effective = attribute_case()
    recovered = form_grounded_memory_batch(cold, CoverageModel(output))
    full = copy.deepcopy(output)
    full["units"][0]["source_refs"][0]["supporting_span"] = effective
    original_full = copy.deepcopy(full)
    unchanged = form_grounded_memory_batch(cold, CoverageModel(full))
    assert len(recovered.units) == len(unchanged.units) == 1
    assert recovered.units == unchanged.units and recovered.unit_mentions == unchanged.unit_mentions
    assert full == original_full


@pytest.mark.parametrize("guard", ["wrong_pair", "question", "negation", "conditional"])
def test_expanding_source_does_not_replace_the_mandatory_semantic_verdict(guard):
    if guard == "wrong_pair":
        source, name, span = "Garnet-12 has 96 GB; Topaz-24 has 48 GB.", "Garnet-12", "48 GB."
        text, relation, value = "Garnet-12 has 48 GB.", "capacity", "48 GB"
    else:
        name, span = "Slate-36", "maroon?" if guard == "question" else "maroon."
        source = {
            "question": "Is Slate-36 maroon?",
            "negation": "Slate-36 is not maroon.",
            "conditional": "If Slate-36 warms up, it becomes maroon.",
        }[guard]
        text, relation, value = "Slate-36 is maroon.", "color", "maroon"
    cold = segment(source, sid=f"source-coverage-{guard}")
    output = {"mentions": [mention(cold, name, "device")],
              "units": [fact(cold, text, name, relation, value, span, subject_mention="device")]}
    model = CoverageModel(output, reject=True)
    batch = form_grounded_memory_batch(cold, model)
    assert len(model.verifications[0]["units"]) == 1
    assert model.verifications[0]["turns"][0]["text"] == source
    assert batch.units == () and len(batch.mentions) == 1
    assert any(issue.candidate == "unit" and issue.code == "formation_semantic_rejected"
               and issue.status == "rejected" for issue in batch.issues)


def test_absent_value_still_fails_detail_guard_after_role_source_is_available():
    cold, output, _effective = attribute_case()
    output["units"][0].update(text="Juniper-62 has capacity 128 GB.", relation="capacity", value="128 GB")
    output["units"][0]["source_refs"][0]["supporting_span"] = "capacity 96 GB"
    model = CoverageModel(output)
    batch = form_grounded_memory_batch(cold, model)
    assert batch.units == () and model.verifications[0]["units"] == []
    assert any(issue.code == "formation_detail_unsupported" and issue.status == "rejected"
               for issue in batch.issues)


def test_assistant_source_does_not_gain_user_authority_from_role_coverage():
    cold, output, _effective = attribute_case()
    cold = replace(cold, turns=(replace(cold.turns[0], role="assistant"),))
    model = CoverageModel(output)
    batch = form_grounded_memory_batch(cold, model)
    assert batch.units == () and model.verifications[0]["units"] == []
    assert all(item.source_role == "assistant" for item in batch.mentions)


def test_restored_candidate_still_obeys_final_identity_veto():
    cold, output, _effective = attribute_case()
    model = CoverageModel(output, identity_false={"Juniper-62"})
    batch = form_grounded_memory_batch(cold, model)
    assert len(model.verifications[0]["units"]) == 1
    assert batch.units == () and batch.mentions[0].identity == "unresolved"
    assert any(issue.code == "formation_identity_dependency_rejected" for issue in batch.issues)


@pytest.mark.parametrize("defect", ["no_role", "participant_only", "cross_turn", "unresolved", "ambiguous_ref"])
def test_coverage_does_not_guess_missing_or_ineligible_role_evidence(defect):
    cold, output, _effective = attribute_case()
    candidate = output["units"][0]
    if defect == "no_role":
        candidate.pop("subject_mention")
    elif defect == "participant_only":
        candidate.pop("subject_mention")
        candidate["mentions"] = ["device"]
    elif defect == "cross_turn":
        second = replace(cold.turns[0], turn_id="separate-turn", content="color is maroon.")
        cold = replace(cold, turns=(replace(cold.turns[0], content="Juniper-62 is known."), second))
        output["mentions"] = [mention(cold, "Juniper-62", "device")]
        candidate["source_refs"][0]["turn_id"] = second.turn_id
    elif defect == "unresolved":
        output["mentions"][0]["identity"] = "unresolved"
    else:
        text = "Juniper-62 is maroon. The label also says maroon."
        cold = replace(cold, turns=(replace(cold.turns[0], content=text),))
        output["mentions"] = [mention(cold, "Juniper-62", "device")]
        candidate["source_refs"][0]["supporting_span"] = "maroon"
    model = CoverageModel(output)
    batch = form_grounded_memory_batch(cold, model)
    assert batch.units == () and model.verifications[0]["units"] == []
    assert any(issue.candidate == "unit" for issue in batch.issues)


def test_public_ingest_projects_effective_source_and_restarts_without_new_calls(tmp_path):
    cold, output, effective = attribute_case()
    original_source = asdict(cold)
    backend, model = Backend(), CoverageModel(output)
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(cold)
    assert result.status == "completed" and len(result.memory_ids) == 1
    metadata = next(iter(backend.events.values()))["metadata"]
    assert metadata["source_refs"][0]["supporting_span"] == effective
    ref = metadata["source_refs"][0]
    assert cold.turns[0].content[ref["source_start"]:ref["source_end"]] == effective
    assert ref["source_role"] == "user"
    key = memory.state_store.key(cold.segment_id, FORMATION_ENTITY_VERSION)
    state = memory.state_store.get(key)
    assert state["extracted"]["response"] == model.raw
    assert state["verified"]["units"][0]["source_refs"][0]["supporting_span"] == effective
    frozen = copy.deepcopy((state, backend.events, backend.mentions))
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    repeated = restarted.ingest(cold)
    assert repeated.already_ingested and repeated.memory_ids == result.memory_ids
    assert (restarted.state_store.get(key), backend.events, backend.mentions) == frozen
    assert no_calls.calls == [] and asdict(cold) == original_source


@pytest.mark.parametrize("stage", ["extracted", "verified"])
def test_durable_checkpoint_failure_reuses_raw_processing_version_or_verified_batch(stage):
    cold, output, effective = attribute_case()
    stages = {}

    def durable_then_fail(name, payload):
        stages[name] = copy.deepcopy(payload)
        if name == stage:
            raise OSError("synthetic failure after durable checkpoint")

    with pytest.raises(OSError):
        form_grounded_memory_batch(cold, CoverageModel(output), checkpoint=durable_then_fail)
    model = CoverageModel() if stage == "extracted" else NoCalls()
    restored = form_grounded_memory_batch(
        cold, model, extracted_checkpoint=stages["extracted"], verified_checkpoint=stages.get("verified"),
    )
    assert len(restored.units) == 1 and restored.units[0].source_refs[0].supporting_span == effective
    assert model.calls == (["verify"] if stage == "extracted" else [])


def test_public_graph_failure_preserves_new_effective_source_and_identity(tmp_path):
    cold, output, effective = attribute_case()
    backend = Backend(fail_persist=2)
    model = CoverageModel(output)
    memory = adapter(tmp_path, backend, model)
    failed = memory.ingest(cold)
    assert failed.status == "failed" and failed.retryable
    key = memory.state_store.key(cold.segment_id, FORMATION_ENTITY_VERSION)
    state = memory.state_store.get(key)
    assert len(state["verified"]["units"]) == 1
    frozen = copy.deepcopy((state["verified"], state["mentions"]))
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    assert restarted.ingest(cold).status == "completed"
    assert len(backend.events) == 1 and no_calls.calls == []
    assert next(iter(backend.events.values()))["metadata"]["source_refs"][0]["supporting_span"] == effective
    final = restarted.state_store.get(key)
    assert (final["verified"], final["mentions"]) == frozen


def old_receipt(cold, output):
    return {"schema_version": FORMATION_PROGRESS_VERSION, "source_digest": _entity_source_digest(cold),
            "response": json.dumps(output, ensure_ascii=False, separators=(",", ":"))}


def test_old_raw_and_old_verified_keep_their_original_source_semantics():
    cold, output, _effective = attribute_case()
    raw = old_receipt(cold, output)
    frozen = copy.deepcopy(raw)
    model, stages = CoverageModel(), {}
    old_batch = form_grounded_memory_batch(cold, model, extracted_checkpoint=raw, checkpoint=stages.__setitem__)
    assert old_batch.units == () and model.verifications[0]["units"] == []
    assert any(issue.code == "formation_detail_unsupported" for issue in old_batch.issues)
    no_calls = NoCalls()
    restored = form_grounded_memory_batch(cold, no_calls, verified_checkpoint=stages["verified"])
    assert restored == old_batch and no_calls.calls == [] and raw == frozen
    assert serialize_grounded_memory_batch(restored) == stages["verified"]


def seed_old_raw_state(tmp_path, cold, output, backend):
    memory = adapter(tmp_path, backend, CoverageModel(output, fail_verify=True))
    assert memory.ingest(cold).status == "failed"
    key = memory.state_store.key(cold.segment_id, FORMATION_ENTITY_VERSION)
    state = memory.state_store.get(key)
    state["extracted"] = old_receipt(cold, output)
    memory.state_store.put(key, state)
    return key


def test_legacy_completed_checkpoint_does_not_backfill_rejected_attribute(tmp_path):
    cold, output, _effective = attribute_case()
    backend = Backend()
    key = seed_old_raw_state(tmp_path, cold, output, backend)
    model = CoverageModel()
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(cold)
    assert result.status == "completed" and result.memory_ids == () and model.calls == ["verify"]
    original = copy.deepcopy(memory.state_store.get(key))
    no_calls = NoCalls()
    repeated = adapter(tmp_path, backend, no_calls).ingest(cold)
    assert repeated.already_ingested and no_calls.calls == [] and backend.events == {}
    assert memory.state_store.get(key) == original


def test_legacy_partial_repair_does_not_gain_source_coverage_or_rewrite_saved_result(tmp_path):
    cold, output, _effective = attribute_case()
    original_short = copy.deepcopy(output["units"][0]["source_refs"])
    output["units"][0]["source_refs"][0]["supporting_span"] = "absent citation"
    backend = Backend()
    key = seed_old_raw_state(tmp_path, cold, output, backend)
    initial = adapter(tmp_path, backend, CoverageModel())
    assert initial.ingest(cold).status == "failed"
    before = copy.deepcopy(initial.state_store.get(key))
    model = CoverageModel(repairs={"repairs": [{"index": 0, "source_refs": original_short}]})
    repaired = adapter(tmp_path, backend, model)
    result = repaired.ingest(cold)
    assert result.status == "completed" and result.memory_ids == () and model.calls == ["repair"]
    state = repaired.state_store.get(key)
    assert all(state[name] == before[name] for name in ("extracted", "verified", "mentions"))
    assert any(issue["code"] == "formation_detail_unsupported" for issue in state["repair_verified"]["issues"])
    frozen = copy.deepcopy(state)
    no_calls = NoCalls()
    assert adapter(tmp_path, backend, no_calls).ingest(cold).already_ingested
    assert no_calls.calls == [] and repaired.state_store.get(key) == frozen and backend.events == {}


def test_coverage_budget_reserves_independent_facts_and_the_complete_verifier_payload():
    shades = ["tone" + chr(ord("a") + i) for i in range(24)]
    clauses = [f"color is {shade}." for shade in shades]
    source = "Device Beacon-51 notes: " + ("x" * 7500) + " " + " ".join(clauses) + " Linden is available."
    cold = segment(source, sid="source-coverage-budget")
    output = {"mentions": [mention(cold, "Beacon-51", "device"), mention(cold, "Linden", "healthy")],
              "units": [fact(cold, f"Beacon-51 color is {shade}.", "Beacon-51", "color", shade,
                             clause, subject_mention="device") for shade, clause in zip(shades, clauses)]}
    # An already-valid fact comes after the expensive recovery candidates.
    output["units"].append(fact(cold, "Linden is available.", "Linden", "is", "available",
                                "Linden is available.", subject_mention="healthy"))
    model = CoverageModel(output)
    batch = form_grounded_memory_batch(cold, model)
    assert "Linden is available." in {item.text for item in batch.units}
    recovered = [item for item in batch.units if item.subject == "Beacon-51"]
    assert 0 < len(recovered) < 24
    assert model.calls == ["extract", "verify"]
    payload = model.verifications[0]
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 160000
    assert payload["turns"][0]["text"] == source and len(payload["mentions"]) == 2
    budget_issues = [issue for issue in batch.issues if issue.code == "formation_source_coverage_budget_exceeded"]
    assert len(budget_issues) == 24 - len(recovered)
    assert all(issue.candidate == "unit" and issue.status == "pending" and issue.index < 24
               for issue in budget_issues)


def test_repeated_name_coverage_uses_the_declared_exact_second_occurrence():
    source = "Mica-73 color is cobalt. Another Mica-73 color is coral."
    cold = segment(source, sid="source-coverage-repeated-name")
    output = {"mentions": [
        mention(cold, "Mica-73", "first"),
        mention(cold, "Mica-73", "second", start=source.rindex("Mica-73"), identity="new",
                distinct_from=["first"], identity_source_refs=[{
                    "turn_id": cold.turns[0].turn_id, "supporting_span": "Another Mica-73 color is coral.",
                }]),
    ], "units": [fact(cold, "Mica-73 color is coral.", "Mica-73", "color", "coral",
                      "color is coral.", subject_mention="second")]}
    model = CoverageModel(output)
    batch = form_grounded_memory_batch(cold, model)
    assert len(batch.units) == 1 and len(batch.mentions) == 2
    ref = batch.units[0].source_refs[0]
    assert ref.supporting_span == "Mica-73 color is coral."
    assert source.index(ref.supporting_span) == source.rindex("Mica-73")
    assert batch.unit_mentions[0].subject == batch.mentions[1].id
    assert "cobalt" not in ref.supporting_span


@pytest.mark.parametrize("already_sufficient", [False, True])
def test_multiple_same_turn_refs_are_preserved_or_minimally_covered_as_needed(already_sufficient):
    source = "Owner Iris-42 leads group Rowan-84; the archive records green status."
    cold = segment(source, sid="source-coverage-multiple-refs")
    first_span = "Iris-42 leads group Rowan-84" if already_sufficient else "leads group"
    candidate = fact(cold, "Iris-42 leads Rowan-84.", "Iris-42", "leads", "Rowan-84", first_span,
                     subject_mention="person", object_mention="group")
    candidate["source_refs"].append({"turn_id": cold.turns[0].turn_id, "supporting_span": "green status."})
    output = {"mentions": [mention(cold, "Iris-42", "person"), mention(cold, "Rowan-84", "group")],
              "units": [candidate]}
    original_refs = copy.deepcopy(candidate["source_refs"])
    model = CoverageModel(output)
    batch = form_grounded_memory_batch(cold, model)
    assert len(batch.units) == 1
    expected = original_refs if already_sufficient else [{
        "turn_id": cold.turns[0].turn_id,
        "supporting_span": "Iris-42 leads group Rowan-84; the archive records green status.",
    }]
    assert [asdict(ref) for ref in batch.units[0].source_refs] == expected
    assert model.verifications[0]["units"][0]["candidate"]["source_refs"] == expected
    assert candidate["source_refs"] == original_refs


@pytest.mark.parametrize("version", [2, True], ids=["unknown_version", "boolean_is_not_version_one"])
def test_unknown_source_coverage_marker_fails_without_a_model_call(version):
    cold, output, _effective = attribute_case()
    receipt = {**old_receipt(cold, output), "source_coverage_version": version}
    no_calls = NoCalls()
    with pytest.raises(FormationError, match="formation_checkpoint_invalid") as raised:
        form_grounded_memory_batch(cold, no_calls, extracted_checkpoint=receipt)
    assert raised.value.retryable is False and no_calls.calls == []


def test_new_receipt_repair_short_ref_uses_coverage_without_migrating_previous_stages(tmp_path):
    cold, output, effective = attribute_case()
    short_refs = copy.deepcopy(output["units"][0]["source_refs"])
    output["units"][0]["source_refs"][0]["supporting_span"] = "absent citation"
    backend = Backend()
    initial_model = CoverageModel(output)
    initial = adapter(tmp_path, backend, initial_model)
    first = initial.ingest(cold)
    assert first.status == "failed" and first.retryable and first.memory_ids == ()
    key = initial.state_store.key(cold.segment_id, FORMATION_ENTITY_VERSION)
    before = copy.deepcopy(initial.state_store.get(key))
    model = CoverageModel(repairs={"repairs": [{"index": 0, "source_refs": short_refs}]})
    repaired = adapter(tmp_path, backend, model)
    result = repaired.ingest(cold)
    assert result.status == "completed" and len(result.memory_ids) == 1
    assert initial_model.calls == ["extract", "verify"] and model.calls == ["repair", "verify"]
    state = repaired.state_store.get(key)
    assert all(state[name] == before[name] for name in ("extracted", "verified", "mentions"))
    assert state["extracted"]["response"] == initial_model.raw
    assert state["repair_verified"]["units"][0]["source_refs"][0]["supporting_span"] == effective
    assert json.loads(state["repair"]["response"])["repairs"][0]["source_refs"] == short_refs
    assert next(iter(backend.events.values()))["metadata"]["source_refs"][0]["supporting_span"] == effective
    frozen = copy.deepcopy((state, backend.events, backend.mentions))
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    assert restarted.ingest(cold).already_ingested
    assert no_calls.calls == [] and (restarted.state_store.get(key), backend.events, backend.mentions) == frozen


def test_repair_coverage_budget_keeps_original_repair_candidates_and_durable_facts(tmp_path):
    shades = ["tone" + chr(ord("a") + i) for i in range(24)]
    clauses = [f"color is {shade}." for shade in shades]
    source = ("Device Beacon-51 notes: " + ("x" * 7500) + " " + " ".join(clauses)
              + " Cedar is ready. Linden is available.")
    cold = segment(source, sid="source-coverage-repair-budget")
    output = {"mentions": [mention(cold, "Beacon-51", "device"), mention(cold, "Cedar", "ordinary"),
                           mention(cold, "Linden", "healthy")],
              "units": [fact(cold, f"Beacon-51 color is {shade}.", "Beacon-51", "color", shade,
                             "absent citation", subject_mention="device") for shade in shades]}
    output["units"].append(fact(cold, "Cedar is ready.", "Cedar", "is", "ready",
                                "absent citation", subject_mention="ordinary"))
    output["units"].append(fact(cold, "Linden is available.", "Linden", "is", "available",
                                "Linden is available.", subject_mention="healthy"))
    repairs = {"repairs": [{"index": index, "source_refs": [{
        "turn_id": cold.turns[0].turn_id, "supporting_span": span,
    }]} for index, span in enumerate([*clauses, "Cedar is ready."])]}
    backend = Backend()
    initial = adapter(tmp_path, backend, CoverageModel(output))
    first = initial.ingest(cold)
    assert first.status == "failed" and first.retryable and len(first.memory_ids) == 1
    key = initial.state_store.key(cold.segment_id, FORMATION_ENTITY_VERSION)
    before = copy.deepcopy((initial.state_store.get(key), backend.events, backend.mentions))
    model = CoverageModel(repairs=repairs)
    repaired = adapter(tmp_path, backend, model)
    result = repaired.ingest(cold)
    assert result.status == "failed" and result.retryable is False
    assert result.memory_ids[:1] == first.memory_ids
    texts = {event["text"] for event in backend.events.values()}
    assert {"Linden is available.", "Cedar is ready."} <= texts
    recovered = [text for text in texts if text.startswith("Beacon-51")]
    assert 0 < len(recovered) < 24
    assert model.calls == ["repair", "verify"]
    payload = model.verifications[0]
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 160000
    verified_texts = [item["candidate"]["text"] for item in payload["units"]]
    assert "Cedar is ready." in verified_texts and "Linden is available." not in verified_texts
    state = repaired.state_store.get(key)
    assert state["status"] == "partial"
    assert all(state[name] == before[0][name] for name in ("extracted", "verified", "mentions"))
    assert all(backend.events[mid] == event for mid, event in before[1].items())
    assert backend.mentions == before[2]
    assert any(issue["code"] == "formation_source_coverage_budget_exceeded" and issue["status"] == "pending"
               for issue in state["repair_verified"]["issues"])
    frozen = copy.deepcopy((state, backend.events, backend.mentions))
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    repeated = restarted.ingest(cold)
    assert repeated.status == "failed" and repeated.retryable is False and repeated.memory_ids == result.memory_ids
    assert no_calls.calls == [] and (restarted.state_store.get(key), backend.events, backend.mentions) == frozen

def test_repair_budget_counts_only_dependencies_of_retained_candidates(tmp_path):
    from Conversation_Memory.adapter.grounded_formation import repair_grounded_memory_batch

    ordinary = [("Linden", "green"), ("Cedar", "blue"), ("Maple", "red"), ("Ash", "white")]
    covered = [("Beacon-51", "tonea"), ("Beacon-52", "toneb"),
               ("Beacon-53", "tonec"), ("Beacon-54", "toned")]
    source = (" ".join(f"{name} is {value}." for name, value in ordinary) + " "
              + " ".join(f"{name} color is {value}." for name, value in covered) + " " + "x" * 18600)
    assert len(source) == 18764
    cold = segment(source, sid="review-repair-coverage-reserved-mentions")
    mentions = [mention(cold, name, name) for name, _value in ordinary]
    mentions.extend(mention(cold, name, name, identity_source_refs=[{
        "turn_id": cold.turns[0].turn_id, "supporting_span": source,
    }]) for name, _value in covered)
    units = [fact(cold, f"{name} is {value}.", name, "is", value,
                  "absent citation", subject_mention=name) for name, value in ordinary]
    units.extend(fact(cold, f"{name} color is {value}.", name, "color", value,
                      "absent citation", subject_mention=name) for name, value in covered)
    output = {"mentions": mentions, "units": units}
    repairs = {"repairs": [{"index": index, "source_refs": [{
        "turn_id": cold.turns[0].turn_id,
        "supporting_span": source if index < 4 else f"color is {covered[index - 4][1]}.",
    }]} for index in range(8)]}
    backend = Backend()
    initial_model = CoverageModel(output)
    initial = adapter(tmp_path, backend, initial_model)
    first = initial.ingest(cold)
    assert first.status == "failed" and first.retryable and first.memory_ids == ()
    assert initial_model.calls == ["extract", "verify"]
    assert len(initial_model.verifications[0]["mentions"]) == 8
    assert initial_model.verifications[0]["units"] == []
    key = initial.state_store.key(cold.segment_id, FORMATION_ENTITY_VERSION)
    before = copy.deepcopy((initial.state_store.get(key), backend.mentions))

    # The original repair candidates fit by themselves under unchanged old semantics.
    legacy_raw = copy.deepcopy(before[0]["extracted"])
    legacy_raw.pop("source_coverage_version")
    legacy_model = CoverageModel(repairs=repairs)
    legacy = repair_grounded_memory_batch(
        cold, legacy_model, extracted_checkpoint=legacy_raw, verified_checkpoint=before[0]["verified"],
    )
    ordinary_texts = {f"{name} is {value}." for name, value in ordinary}
    assert {unit.text for unit in legacy.units} == ordinary_texts
    assert legacy_model.calls == ["repair", "verify"]
    assert len(legacy_model.verifications[0]["mentions"]) == 4

    model = CoverageModel(repairs=repairs)
    repaired = adapter(tmp_path, backend, model)
    result = repaired.ingest(cold)
    texts = {event["text"] for event in backend.events.values()}
    assert ordinary_texts <= texts
    restored = texts - ordinary_texts
    assert 0 < len(restored) < 4
    assert result.status == "failed" and result.retryable is False
    assert model.calls == ["repair", "verify"]
    payload = model.verifications[0]
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 160000
    assert {item["surface"] for item in payload["mentions"]} == {
        item["candidate"]["subject"] for item in payload["units"]
    }
    assert len(payload["mentions"]) == 4 + len(restored)
    state = repaired.state_store.get(key)
    assert state["status"] == "partial"
    assert all(state[name] == before[0][name] for name in ("extracted", "verified", "mentions"))
    assert backend.mentions == before[1] and len(backend.mentions) == 8
    assert json.loads(state["repair"]["response"]) == repairs
    budget_issues = [issue for issue in state["repair_verified"]["issues"]
                     if issue["code"] == "formation_source_coverage_budget_exceeded"]
    assert len(budget_issues) == 4 - len(restored)
    assert all(issue["index"] >= 4 and issue["status"] == "pending" for issue in budget_issues)
    frozen = copy.deepcopy((state, backend.events, backend.mentions))
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    repeated = restarted.ingest(cold)
    assert repeated.status == "failed" and repeated.retryable is False and repeated.memory_ids == result.memory_ids
    assert no_calls.calls == [] and (restarted.state_store.get(key), backend.events, backend.mentions) == frozen
