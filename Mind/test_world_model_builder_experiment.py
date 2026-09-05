from __future__ import annotations

import ast
import json
import inspect
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from Mind.world_model_builder_experiment import (
    MAX_EVIDENCE_ITEMS,
    BuilderFailure,
    CandidateSource,
    DynamicsAction,
    DynamicsObservation,
    DynamicsState,
    RevisionEvidence,
    WorldModelBuilder,
    WorldModelRevisionRequest,
    load_registered_campaign,
    run_builder_case,
    run_campaign,
)


CANONICAL_SOURCE = '''class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "boost" if state["mode"] == "normal" else "normal"}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''


VALID_CANDIDATE_SOURCE = '''class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "boost" if state["mode"] == "normal" else "normal"}
        if state["mode"] == "boost":
            return {"value": state["value"] + action["delta"] * 2, "mode": state["mode"]}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''


REVERSE_CANDIDATE_SOURCE = '''class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "reverse" if state["mode"] == "forward" else "forward"}
        if state["mode"] == "reverse":
            return {"value": state["value"] - action["delta"], "mode": state["mode"]}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''


CLAMPED_CANDIDATE_SOURCE = '''class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "clamped" if state["mode"] == "free" else "free"}
        if state["mode"] == "clamped" and state["value"] + action["delta"] < 0:
            return {"value": 0, "mode": state["mode"]}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''


class ScriptedModel:
    client_kind = "model"

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append(
            {
                "recent_context": recent_context,
                "user_message": user_message,
                "system_prompt": system_prompt,
            }
        )
        return self.output

    def summarize_hot_draft(self, old_summary, moved_turns):
        raise AssertionError("Builder must not call the Hot Draft surface")


class SequenceModel:
    client_kind = "model"

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        return next(self.outputs)

    def summarize_hot_draft(self, old_summary, moved_turns):
        raise AssertionError("Builder must not call the Hot Draft surface")


class FailingModel:
    client_kind = "model"

    def __init__(self):
        self.calls = 0

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls += 1
        raise RuntimeError("provider unavailable")

    def summarize_hot_draft(self, old_summary, moved_turns):
        raise AssertionError("Builder must not call the Hot Draft surface")


def _request(current_model_source: str = CANONICAL_SOURCE) -> WorldModelRevisionRequest:
    return WorldModelRevisionRequest(
        current_model_version="wm-v1",
        current_model_source=current_model_source,
        evidence=(
            RevisionEvidence(
                state=DynamicsState(value=2, mode="boost"),
                action=DynamicsAction(kind="step", delta=1),
                expected=DynamicsObservation(value=3, mode="boost"),
                observed=DynamicsObservation(value=4, mode="boost"),
                result="ERROR",
            ),
        ),
        revision_reason=(
            "Observed prediction error indicates the current transition rule "
            "is inconsistent with recorded action-observation evidence."
        ),
    )


def _run_source(tmp_path, source, request=None):
    request = request or _request()
    canonical_path = tmp_path / "canonical_world_model.py"
    candidate_path = tmp_path / "host-owned" / "candidate_world_model.py"
    canonical_path.write_text(request.current_model_source, encoding="utf-8")
    model = ScriptedModel(
        json.dumps({"type": "world_model_candidate", "source": source})
    )
    return (
        run_builder_case(
            builder=WorldModelBuilder(model),
            request=request,
            canonical_path=canonical_path,
            candidate_path=candidate_path,
        ),
        canonical_path,
        candidate_path,
        model,
    )


def test_w1_1_builder_receives_only_the_fresh_revision_request():
    model = ScriptedModel(
        json.dumps(
            {"type": "world_model_candidate", "source": VALID_CANDIDATE_SOURCE}
        )
    )

    result = WorldModelBuilder(model).build(_request())

    assert result == CandidateSource(source=VALID_CANDIDATE_SOURCE)
    assert len(model.calls) == 1
    call = model.calls[0]
    assert call["recent_context"] == []
    request_document = json.loads(call["user_message"])
    assert set(request_document) == {
        "current_model_source",
        "current_model_version",
        "evidence",
        "revision_reason",
    }
    assert request_document["evidence"] == [
        {
            "action": {"delta": 1, "kind": "step"},
            "expected": {"mode": "boost", "value": 3},
            "observed": {"mode": "boost", "value": 4},
            "result": "ERROR",
            "state": {"mode": "boost", "value": 2},
        }
    ]
    serialized_call = json.dumps(call, ensure_ascii=False)
    assert "Root transcript" not in serialized_call
    assert "Mind transcript" not in serialized_call


def test_w1_2_strict_candidate_envelope_rejects_malformed_output():
    malformed_outputs = (
        "```json\n{}\n```",
        json.dumps(
            {
                "type": "world_model_candidate",
                "source": VALID_CANDIDATE_SOURCE,
                "commentary": "extra",
            }
        ),
        json.dumps(
            {
                "type": "world_model_candidate",
                "source": VALID_CANDIDATE_SOURCE,
                "path": "canonical_world_model.py",
            }
        ),
        json.dumps({"type": "world_model_candidate", "source": "   "}),
        (
            '{"type":"world_model_candidate","source":"bad",'
            f'"source":{json.dumps(VALID_CANDIDATE_SOURCE)}}}'
        ),
    )

    for raw in malformed_outputs:
        model = ScriptedModel(raw)
        assert WorldModelBuilder(model).build(_request()) == BuilderFailure(
            "invalid_output"
        )
        assert len(model.calls) == 1


def test_w1_3_4_7_host_loads_candidate_without_touching_canonical(tmp_path):
    canonical_path = tmp_path / "canonical_world_model.py"
    candidate_path = tmp_path / "host-owned" / "candidate_world_model.py"
    canonical_path.write_text(CANONICAL_SOURCE, encoding="utf-8")
    canonical_before = canonical_path.read_bytes()
    model = ScriptedModel(
        json.dumps(
            {"type": "world_model_candidate", "source": VALID_CANDIDATE_SOURCE}
        )
    )

    result = run_builder_case(
        builder=WorldModelBuilder(model),
        request=_request(),
        canonical_path=canonical_path,
        candidate_path=candidate_path,
    )

    assert result.status == "VALIDATED", result.failure_reason
    assert result.failure_reason is None
    assert result.current_accuracy == 0.0
    assert result.candidate_accuracy == 1.0
    assert canonical_path.read_bytes() == canonical_before
    assert candidate_path.read_text(encoding="utf-8") == VALID_CANDIDATE_SOURCE
    assert [event["event_type"] for event in result.events] == [
        "WORLD_MODEL_REVISION_REQUESTED",
        "WORLD_MODEL_CANDIDATE_PROPOSED",
        "WORLD_MODEL_CANDIDATE_VALIDATED",
    ]
    assert not hasattr(result.proposal, "path")


@pytest.mark.parametrize(
    "unsafe_source",
    [
        '''import os

class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        return {"value": state["value"], "mode": state["mode"]}
''',
        '''class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        return {"value": open("escape.txt"), "mode": state["mode"]}
''',
        '''class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        return {"value": exec("raise SystemExit"), "mode": state["mode"]}
''',
    ],
)
def test_w1_5_unsafe_source_is_rejected_before_write_or_execution(
    tmp_path, unsafe_source
):
    result, canonical_path, candidate_path, _ = _run_source(
        tmp_path, unsafe_source
    )

    assert result.status == "REJECTED"
    assert result.failure_reason == "unsafe_or_invalid_candidate"
    assert not candidate_path.exists()
    assert canonical_path.read_text(encoding="utf-8") == CANONICAL_SOURCE


@pytest.mark.parametrize(
    "invalid_source",
    [
        '''class CandidateWorldModel:
    def predict(self, state, action):
        return {"value": state["value"], "mode": state["mode"]}
''',
        '''class CandidateWorldModel:
    version = "candidate"
''',
        '''class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        return {"value": state["value"], "mode": state["mode"]}

    def plan(self, state):
        return state
''',
    ],
)
def test_w1_6_candidate_contract_rejects_missing_or_planning_api(
    tmp_path, invalid_source
):
    result, _, candidate_path, _ = _run_source(tmp_path, invalid_source)

    assert result.status == "REJECTED"
    assert result.failure_reason == "unsafe_or_invalid_candidate"
    assert not candidate_path.exists()


def test_w1_8_9_current_stays_wrong_and_candidate_improves_on_build_evidence(
    tmp_path,
):
    evidence = (
        RevisionEvidence(
            state=DynamicsState(value=2, mode="boost"),
            action=DynamicsAction(kind="step", delta=1),
            expected=DynamicsObservation(value=3, mode="boost"),
            observed=DynamicsObservation(value=4, mode="boost"),
            result="ERROR",
        ),
        RevisionEvidence(
            state=DynamicsState(value=4, mode="boost"),
            action=DynamicsAction(kind="step", delta=3),
            expected=DynamicsObservation(value=7, mode="boost"),
            observed=DynamicsObservation(value=10, mode="boost"),
            result="ERROR",
        ),
        RevisionEvidence(
            state=DynamicsState(value=4, mode="normal"),
            action=DynamicsAction(kind="step", delta=3),
            expected=DynamicsObservation(value=7, mode="normal"),
            observed=DynamicsObservation(value=7, mode="normal"),
            result="MATCHED",
        ),
    )
    request = WorldModelRevisionRequest(
        current_model_version="wm-v1",
        current_model_source=CANONICAL_SOURCE,
        evidence=evidence,
        revision_reason=_request().revision_reason,
    )

    result, _, _, _ = _run_source(tmp_path, VALID_CANDIDATE_SOURCE, request)

    assert result.status == "VALIDATED", result.failure_reason
    assert result.current_accuracy == pytest.approx(1 / 3)
    assert result.current_accuracy < 1.0
    assert result.candidate_accuracy == 1.0
    assert result.candidate_accuracy > result.current_accuracy


def test_w1_10_environment_observation_remains_reality_authority(tmp_path):
    always_wrong_candidate = '''class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        return {"value": state["value"], "mode": state["mode"]}
'''
    request = _request()
    evidence_before = request.evidence

    result, _, _, _ = _run_source(tmp_path, always_wrong_candidate, request)

    assert result.status == "VALIDATED", result.failure_reason
    assert result.candidate_accuracy == 0.0
    assert request.evidence == evidence_before
    assert request.evidence[0].observed == DynamicsObservation(
        value=4, mode="boost"
    )


def test_w1_11_no_automatic_promotion(tmp_path):
    result, canonical_path, candidate_path, _ = _run_source(
        tmp_path, VALID_CANDIDATE_SOURCE
    )

    assert result.status == "VALIDATED", result.failure_reason
    assert canonical_path.read_text(encoding="utf-8") == CANONICAL_SOURCE
    assert 'version = "wm-v1"' in canonical_path.read_text(encoding="utf-8")
    assert candidate_path != canonical_path
    assert 'version = "candidate"' in candidate_path.read_text(encoding="utf-8")


def test_w1_12_builder_has_no_execution_mind_or_filesystem_authority():
    constructor_parameters = set(inspect.signature(WorldModelBuilder).parameters)
    builder_source = inspect.getsource(WorldModelBuilder)
    builder_tree = ast.parse(textwrap.dedent(builder_source))
    referenced_names = {
        node.id for node in ast.walk(builder_tree) if isinstance(node, ast.Name)
    }
    forbidden = {
        "ExecutionOrgan",
        "AgentProcess",
        "IPython",
        "shell",
        "filesystem",
        "Intention",
        "spawn_child",
        "open",
        "Path",
    }

    assert constructor_parameters == {"model"}
    assert forbidden.isdisjoint(referenced_names)
    assert set(vars(WorldModelBuilder(ScriptedModel("{}")))) == {"_model"}


def test_builder_rejects_unbounded_or_inconsistent_request_without_model_call():
    evidence = _request().evidence * (MAX_EVIDENCE_ITEMS + 1)
    model = ScriptedModel("should not be called")
    request = WorldModelRevisionRequest(
        current_model_version="wm-v1",
        current_model_source=CANONICAL_SOURCE,
        evidence=evidence,
        revision_reason=_request().revision_reason,
    )

    assert WorldModelBuilder(model).build(request) == BuilderFailure(
        "invalid_request"
    )
    assert model.calls == []


def test_registered_campaign_is_frozen_and_scripted_campaign_passes(tmp_path):
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "w1" / "manifest.json"
    )
    model = SequenceModel(
        [
            json.dumps(
                {"type": "world_model_candidate", "source": source}
            )
            for source in (
                VALID_CANDIDATE_SOURCE,
                REVERSE_CANDIDATE_SOURCE,
                CLAMPED_CANDIDATE_SOURCE,
            )
        ]
    )
    output_path = tmp_path / "campaign-result.json"

    result = run_campaign(
        campaign,
        model=model,
        output_path=output_path,
    )

    assert len(campaign.cases) == 3
    assert len(model.calls) == 3
    assert all(call[0] == [] for call in model.calls)
    assert result["verdict"] == "WORLD_MODEL_W1_PASS"
    assert result["summary"] == {
        "candidate_accuracy_1_count": 3,
        "canonical_unchanged_count": 3,
        "improved_count": 3,
        "provider_failed_count": 0,
        "safe_count": 3,
        "syntactic_contract_valid_count": 3,
    }
    assert output_path.is_file()
    assert json.loads(output_path.read_text(encoding="utf-8")) == result


def test_provider_unavailable_is_blocked_without_retry(tmp_path):
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "w1" / "manifest.json"
    )
    model = FailingModel()

    result = run_campaign(
        campaign,
        model=model,
        output_path=tmp_path / "blocked.json",
    )

    assert model.calls == 3
    assert result["summary"]["provider_failed_count"] == 3
    assert result["verdict"] == "WORLD_MODEL_W1_BLOCKED"


@pytest.mark.parametrize(
    "resource_or_overfit_source",
    [
        '''class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        return {"value": state["value"] * 1000000 * 1000000, "mode": state["mode"]}
''',
        '''class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        if state["value"] < 6:
            return {"value": 4, "mode": state["mode"]}
        return {"value": state["value"], "mode": state["mode"]}
''',
    ],
)
def test_resource_amplification_and_numeric_range_memorizer_are_rejected(
    tmp_path, resource_or_overfit_source
):
    result, _, candidate_path, _ = _run_source(
        tmp_path, resource_or_overfit_source
    )

    assert result.status == "REJECTED"
    assert result.failure_reason == "unsafe_or_invalid_candidate"
    assert not candidate_path.exists()


def test_host_refuses_preexisting_candidate_destination(tmp_path):
    request = _request()
    canonical_path = tmp_path / "canonical_world_model.py"
    candidate_path = tmp_path / "candidate_world_model.py"
    canonical_path.write_text(request.current_model_source, encoding="utf-8")
    candidate_path.write_text("preexisting", encoding="utf-8")
    model = ScriptedModel(
        json.dumps(
            {"type": "world_model_candidate", "source": VALID_CANDIDATE_SOURCE}
        )
    )

    result = run_builder_case(
        builder=WorldModelBuilder(model),
        request=request,
        canonical_path=canonical_path,
        candidate_path=candidate_path,
    )

    assert result.failure_reason == "candidate_destination_exists"
    assert candidate_path.read_text(encoding="utf-8") == "preexisting"
    assert model.calls == []


def test_host_refuses_candidate_hard_linked_to_canonical(tmp_path):
    request = _request()
    canonical_path = tmp_path / "canonical_world_model.py"
    candidate_path = tmp_path / "candidate_world_model.py"
    canonical_path.write_text(request.current_model_source, encoding="utf-8")
    try:
        os.link(canonical_path, candidate_path)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    canonical_before = canonical_path.read_bytes()
    model = ScriptedModel(
        json.dumps(
            {"type": "world_model_candidate", "source": VALID_CANDIDATE_SOURCE}
        )
    )

    result = run_builder_case(
        builder=WorldModelBuilder(model),
        request=request,
        canonical_path=canonical_path,
        candidate_path=candidate_path,
    )

    assert result.failure_reason == "candidate_destination_exists"
    assert canonical_path.read_bytes() == canonical_before
    assert model.calls == []


def test_module_cli_can_load_before_any_provider_call():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "Mind.world_model_builder_experiment",
            "--help",
        ],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert "Run frozen World Model W1" in completed.stdout
