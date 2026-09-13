"""Check measurement mechanics, not semantic model capability."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_PATH = Path(__file__).with_name("entity_memory_acceptance.py")
_SPEC = importlib.util.spec_from_file_location("memory_acceptance_measurements", _PATH)
acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acceptance)


def test_reliability_gold_stays_frozen():
    fixture = Path(__file__).parent / "fixtures/memory_reliability_holdout.json"
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == "a51da409fef4f650155ea5ee7fe0340454658fbdec8dc51d4e60ae610ae4f282"


def test_combination_adds_conjunction_without_rewriting_legacy_metric():
    scenario = {"facts": [{"id": "f", "turn": "t", "subject": ["s"], "relation": ["r"], "value": ["v"]}], "mentions": [], "queries": [{"required_facts": ["f"], "required_mentions": ["M"]}]}
    report = {"events": [], "recalls": [{"evidence": [], "mentions": [{"surface": "M"}]}]}
    score = acceptance._score(scenario, report)
    assert score["query_complete"] == 1
    assert score["query_combined_complete"] == 0
    assert score["necessary_evidence_hits"] == 0
    assert score["necessary_mention_hits"] == 1


def test_injection_is_one_candidate_and_keeps_original_response():
    raw = json.dumps({"units": [{"subject": "Device", "value": "7 V", "source_refs": [{"supporting_span": "Device is 7 V"}]}]})
    rule = {"kind": "unit_source_span", "subject_contains": "Device", "value_without_spaces": "7V", "replacement_span": "absent"}
    changed, audit = acceptance._inject_fault(raw, rule)
    assert audit["applied"] and audit["target_index"] == 0
    assert json.loads(raw)["units"][0]["source_refs"][0]["supporting_span"] == "Device is 7 V"
    assert json.loads(changed)["units"][0]["source_refs"][0]["supporting_span"] == "absent"
    unchanged, missing = acceptance._inject_fault('{"units": []}', rule)
    assert unchanged == '{"units": []}' and not missing["applied"]


def test_changed_verifier_input_never_replays_by_position():
    calls = []
    client = SimpleNamespace(_http_client=SimpleNamespace(event_hooks={"response": []}))
    prompt = "Verify fully entailed facts"
    prior = {"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "input": {"units": [1, 2]}, "output": "old", "category": "semantic_validation"}
    model = acceptance._AuditedModel(client, calls, [prior], replay_only=True)
    with pytest.raises(RuntimeError, match="replay_output_missing"):
        model.generate([], '{"units": [1]}', system_prompt=prompt)
    assert calls == []
    assert model.generate([], '{"units": [1,2]}', system_prompt=prompt) == "old"
    assert calls[0]["reused_provider_output"]
    assert "reused_provider_output" not in prior


def test_retained_state_requires_owned_path_and_matching_frozen_marker(tmp_path):
    allowed = tmp_path / "Conversation_Memory/tests"
    allowed.mkdir(parents=True)
    args = SimpleNamespace(config_root=tmp_path, state_root=tmp_path / "data", resume_state=False)
    with pytest.raises(ValueError, match="task_owned"):
        acceptance._state_context(args, "fixture-a")
    args.state_root = allowed / ".memory_reliability_state_probe"
    with acceptance._state_context(args, "fixture-a") as state:
        assert Path(state) == args.state_root
    args.resume_state = True
    with pytest.raises(ValueError, match="marker_mismatch"):
        acceptance._state_context(args, "different-fixture")
    with acceptance._state_context(args, "fixture-a") as state:
        assert Path(state).is_dir()


def test_checkpoint_resume_guard_cannot_resample_extraction():
    client = SimpleNamespace(_http_client=SimpleNamespace(event_hooks={"response": []}))
    model = acceptance._AuditedModel(client, [])
    model.forbid_new_extraction = True
    with pytest.raises(RuntimeError, match="new_extraction_forbidden"):
        model.generate([], '{}', system_prompt="Extract source-grounded atomic facts")
    assert model.calls == []
