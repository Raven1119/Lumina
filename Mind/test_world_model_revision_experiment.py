from __future__ import annotations

import inspect
import hashlib
import json
from pathlib import Path

import pytest

from Mind.world_model_builder_experiment import (
    DynamicsAction,
    DynamicsObservation,
    DynamicsState,
    RevisionEvidence,
    WorldModelRevisionRequest,
)
from Mind.world_model_revision_experiment import (
    DEFAULT_BOUNDS,
    BUILDER_SYSTEM_PROMPT,
    EpisodeStatus,
    RevisionBounds,
    RevisionEpisodeRequest,
    WorldModelRevisionBuilder,
    load_registered_w2,
    run_registered_campaign,
    run_revision_episode,
)


BASE_SOURCE = '''class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "reverse" if state["mode"] == "forward" else "forward"}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''

WRONG_SAFE_SOURCE = '''class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "reverse" if state["mode"] == "forward" else "forward"}
        if state["mode"] == "reverse":
            return {"value": state["value"] + action["delta"], "mode": state["mode"]}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''

REVERSE_SOURCE = '''class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "reverse" if state["mode"] == "forward" else "forward"}
        if state["mode"] == "reverse":
            return {"value": state["value"] - action["delta"], "mode": state["mode"]}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''


def _evidence(
    value: int,
    mode: str,
    delta: int,
    expected_value: int,
    observed_value: int,
) -> RevisionEvidence:
    expected = DynamicsObservation(expected_value, mode)
    observed = DynamicsObservation(observed_value, mode)
    return RevisionEvidence(
        state=DynamicsState(value, mode),
        action=DynamicsAction("step", delta),
        expected=expected,
        observed=observed,
        result="MATCHED" if expected == observed else "ERROR",
    )


def _request() -> RevisionEpisodeRequest:
    evidence = (
        _evidence(4, "forward", 2, 6, 6),
        _evidence(4, "reverse", 2, 6, 2),
        _evidence(9, "reverse", 3, 12, 6),
    )
    return RevisionEpisodeRequest(
        revision=WorldModelRevisionRequest(
            current_model_version="wm-v1",
            current_model_source=BASE_SOURCE,
            evidence=evidence,
            revision_reason="Repair the predictive dynamics from objective evidence.",
        ),
        episode_ref="episode-test",
    )


class ScriptedModel:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[list[dict[str, str]], str, str]] = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        if not self.outputs:
            raise AssertionError("unexpected model call")
        return json.dumps(self.outputs.pop(0), separators=(",", ":"))

    def summarize_hot_draft(self, old_summary, moved_turns):
        raise AssertionError("W2 must not summarize Hot Draft")


def _paths(tmp_path: Path, source: str = BASE_SOURCE):
    current = tmp_path / "current" / "world_model.py"
    working = tmp_path / "working" / "world_model.py"
    notes = tmp_path / "working" / "notes" / "world_model.md"
    current.parent.mkdir(parents=True)
    current.write_text(source, encoding="utf-8")
    return current, working, notes


def test_initial_attention_is_bounded_and_uses_handles_not_full_resources(tmp_path):
    model = ScriptedModel([{"type": "unresolved", "notes": "Need a discriminating negative delta."}])
    current, working, notes = _paths(tmp_path)

    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    assert len(model.calls) == 1
    recent_context, message, prompt = model.calls[0]
    assert recent_context == []
    assert BASE_SOURCE not in message
    assert "episode-test" not in message
    assert "reverse-step" not in message
    assert "Mind transcript" not in message
    assert '"handles"' in message
    assert '"first_divergence"' in message
    assert "Reality Evidence is authoritative" in prompt


def test_model_read_and_evidence_read_are_explicit_and_bounded(tmp_path):
    model = ScriptedModel([
        {"type": "read_file", "path": "world_model.py"},
        {"type": "read_file", "path": "evidence.json", "start": 0, "count": 2},
        {"type": "unresolved", "notes": "The inspected subset is not enough."},
    ])
    current, working, notes = _paths(tmp_path)

    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.model_turns == 3
    assert result.tool_calls == 2
    first_observation = json.loads(model.calls[1][1])["recent_observations"][-1]
    assert first_observation["content"] == BASE_SOURCE
    second_observation = json.loads(model.calls[2][1])["recent_observations"][-1]
    assert second_observation["kind"] == "evidence"
    assert len(second_observation["items"]) == 2


def test_semantic_write_auto_verifies_first_divergence_then_applies_atomically(tmp_path):
    model = ScriptedModel([
        {"type": "write_file", "path": "world_model.py", "content": WRONG_SAFE_SOURCE},
        {"type": "write_file", "path": "world_model.py", "content": REVERSE_SOURCE},
    ])
    current, working, notes = _paths(tmp_path)
    evidence_before = _request().revision.evidence

    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.CONSISTENT_ENOUGH
    assert result.semantic_revisions == 2
    assert current.read_text(encoding="utf-8") == REVERSE_SOURCE
    assert working.read_text(encoding="utf-8") == REVERSE_SOURCE
    assert _request().revision.evidence == evidence_before
    feedback = json.loads(model.calls[1][1])["recent_observations"][-1]
    assert feedback["kind"] == "verifier"
    assert feedback["accuracy"] < 1.0
    assert feedback["first_divergence"] == {
        "action": {"delta": 2, "kind": "step"},
        "actual": {"mode": "reverse", "value": 2},
        "index": 1,
        "observed_delta": -2,
        "predicted": {"mode": "reverse", "value": 6},
        "predicted_delta": 2,
        "state": {"mode": "reverse", "value": 4},
    }
    assert result.events[-1]["event_type"] == "WORLD_MODEL_REVISION_APPLIED"


def test_latest_working_verifier_state_remains_visible_after_an_intervening_read(tmp_path):
    partial_source = REVERSE_SOURCE.replace(
        'state["value"] - action["delta"]',
        'state["value"] - action["delta"] * 2',
    )
    model = ScriptedModel([
        {"type": "write_file", "path": "world_model.py", "content": partial_source},
        {"type": "read_file", "path": "notes/world_model.md"},
        {"type": "unresolved", "notes": "The latest working check still diverges."},
    ])
    current, working, notes = _paths(tmp_path)

    run_revision_episode(
        builder=WorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    second = json.loads(model.calls[1][1])["current_verifier_state"]
    third = json.loads(model.calls[2][1])["current_verifier_state"]
    assert second == third
    assert second["first_divergence"]["predicted_delta"] == -4


def test_broken_working_edit_never_changes_current_and_can_end_unresolved(tmp_path):
    invalid_source = "import os\n" + BASE_SOURCE
    model = ScriptedModel([
        {"type": "write_file", "path": "world_model.py", "content": invalid_source},
        {"type": "unresolved", "notes": "The attempted representation was structurally invalid."},
    ])
    current, working, notes = _paths(tmp_path)
    before = current.read_bytes()

    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    assert current.read_bytes() == before
    assert working.read_text(encoding="utf-8") == BASE_SOURCE
    assert "structural_error" in model.calls[1][1]
    assert "structurally invalid" in notes.read_text(encoding="utf-8")


def test_run_python_is_fresh_restricted_and_bounded(tmp_path):
    model = ScriptedModel([
        {"type": "run_python", "code": "print(sum(item['observed']['value'] for item in evidence))"},
        {"type": "run_python", "code": "print(secret_name)"},
        {"type": "run_python", "code": "import os\nprint(os.getcwd())"},
        {"type": "unresolved", "notes": "Analysis complete; no safe extra authority used."},
    ])
    current, working, notes = _paths(tmp_path)

    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    observations = [event["payload"] for event in result.events if event["event_type"] == "BUILDER_TOOL_OBSERVED"]
    assert observations[0]["output"] == "14"
    assert observations[1]["error"] == "analysis_failed"
    assert observations[2]["error"] == "unsafe_analysis"
    assert all(len(json.dumps(item)) <= DEFAULT_BOUNDS.max_verifier_output_chars for item in observations)


def test_turn_and_tool_budgets_terminate_without_applying_working_state(tmp_path):
    bounds = RevisionBounds(
        max_model_turns=2,
        max_tool_calls=2,
        max_run_python_seconds=1,
        max_file_read_chars=500,
        max_verifier_output_chars=800,
        max_builder_output_chars=1000,
        max_model_source_chars=4000,
        max_notes_chars=1000,
        max_context_chars=5000,
        max_evidence_items_per_read=1,
        max_python_source_chars=500,
        max_python_output_chars=500,
    )
    model = ScriptedModel([
        {"type": "read_file", "path": "world_model.py"},
        {"type": "read_file", "path": "world_model.py"},
    ])
    current, working, notes = _paths(tmp_path)
    before = current.read_bytes()

    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(model, bounds),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.BUDGET_EXHAUSTED
    assert result.model_turns == bounds.max_model_turns
    assert result.tool_calls == bounds.max_tool_calls
    assert current.read_bytes() == before


def test_bounds_cannot_exceed_the_preregistered_ceiling(tmp_path):
    values = DEFAULT_BOUNDS.__dict__ | {"max_model_turns": DEFAULT_BOUNDS.max_model_turns + 1}
    model = ScriptedModel([{"type": "unresolved", "notes": "should not run"}])
    current, working, notes = _paths(tmp_path)

    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(model, RevisionBounds(**values)),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "invalid_request"
    assert model.calls == []


def test_deeply_nested_json_fails_closed(tmp_path):
    class NestedModel:
        def generate(self, recent_context, user_message, *, system_prompt):
            return "[" * 1100 + "0" + "]" * 1100

        def summarize_hot_draft(self, old_summary, moved_turns):
            raise AssertionError

    current, working, notes = _paths(tmp_path)
    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(NestedModel(), DEFAULT_BOUNDS),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "invalid_model_action"


def test_invalid_utf8_notes_read_fails_conservatively(tmp_path):
    model = ScriptedModel([
        {"type": "read_file", "path": "notes/world_model.md"},
        {"type": "unresolved", "notes": "The notes resource could not be decoded."},
    ])
    current, working, notes = _paths(tmp_path)
    notes.parent.mkdir(parents=True)
    notes.write_bytes(b"\xff\xfe")

    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    observation = json.loads(model.calls[1][1])["recent_observations"][-1]
    assert observation == {"kind": "read_error", "path": "notes/world_model.md"}


@pytest.mark.parametrize(
    "action",
    [
        {"type": "read_file", "path": "../outside.txt"},
        {"type": "write_file", "path": "evidence.json", "content": "[]"},
        {"type": "write_file", "path": "../world_model.py", "content": REVERSE_SOURCE},
        {"type": "take_action", "action": "anything"},
    ],
)
def test_workspace_and_authority_escape_actions_are_structural_failures(tmp_path, action):
    model = ScriptedModel([action])
    current, working, notes = _paths(tmp_path)
    before = current.read_bytes()

    result = run_revision_episode(
        builder=WorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        request=_request(),
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert current.read_bytes() == before


def test_builder_object_has_only_model_and_bounds_and_no_actor_surface():
    model = ScriptedModel([])
    builder = WorldModelRevisionBuilder(model, DEFAULT_BOUNDS)
    assert set(vars(builder)) == {"_model", "bounds"}
    forbidden = {
        "take_action", "execute", "spawn_child", "set_intention", "plan",
        "browser", "shell", "ipython", "mind_trace", "root_transcript",
    }
    assert forbidden.isdisjoint(dir(builder))
    source = inspect.getsource(WorldModelRevisionBuilder).lower()
    assert "executionorgan" not in source
    assert "take_action" not in source
    assert "spawn_child" not in source


def test_registered_manifest_reuses_exact_w1_public_evidence_and_hides_holdouts():
    campaign = load_registered_w2(Path(__file__).parent / "fixtures" / "w2" / "manifest.json")
    w1 = json.loads((Path(__file__).parent / "fixtures" / "w1" / "manifest.json").read_text(encoding="utf-8"))

    assert campaign.bounds == DEFAULT_BOUNDS
    assert campaign.model_config == {
        "base_url": "https://api.deepseek.com/anthropic",
        "max_tokens": 1600,
        "model": "deepseek-v4-pro",
        "provider": "deepseek-anthropic",
        "request_timeout_seconds": 45,
        "temperature": 0.0,
        "thinking": "disabled",
    }
    assert campaign.implementation_sha256 == hashlib.sha256(
        (Path(__file__).parent / "world_model_revision_experiment.py").read_bytes()
    ).hexdigest()
    assert [case.revision.evidence for case in campaign.resolvable] == [
        tuple(
            RevisionEvidence(
                state=DynamicsState(item["state"]["value"], item["state"]["mode"]),
                action=DynamicsAction(item["action"]["kind"], item["action"]["delta"]),
                expected=DynamicsObservation(item["expected"]["value"], item["expected"]["mode"]),
                observed=DynamicsObservation(item["observed"]["value"], item["observed"]["mode"]),
                result=item["result"],
            )
            for item in raw["evidence"]
        )
        for raw in w1["cases"]
    ]
    assert all(case.hidden_evidence for case in campaign.resolvable)


def test_registered_scripted_campaign_meets_all_preregistered_dimensions(tmp_path):
    campaign = load_registered_w2(Path(__file__).parent / "fixtures" / "w2" / "manifest.json")

    class RuleModel:
        def __init__(self):
            self.calls = []
            self.reverse_writes = 0

        def generate(self, recent_context, user_message, *, system_prompt):
            self.calls.append(user_message)
            if '"mode":"boost"' in user_message:
                source = REVERSE_SOURCE.replace(
                    '"reverse" if state["mode"] == "forward" else "forward"',
                    '"boost" if state["mode"] == "normal" else "normal"',
                ).replace('state["mode"] == "reverse"', 'state["mode"] == "boost"').replace(
                    'state["value"] - action["delta"]', 'state["value"] + action["delta"] * 2'
                )
                return json.dumps({"type": "write_file", "path": "world_model.py", "content": source})
            if '"mode":"reverse"' in user_message:
                self.reverse_writes += 1
                if self.reverse_writes == 1:
                    return json.dumps({"type": "write_file", "path": "world_model.py", "content": WRONG_SAFE_SOURCE})
                return json.dumps({"type": "write_file", "path": "world_model.py", "content": REVERSE_SOURCE})
            if '"mode":"clamped"' in user_message:
                source = REVERSE_SOURCE.replace(
                    '"reverse" if state["mode"] == "forward" else "forward"',
                    '"clamped" if state["mode"] == "free" else "free"',
                ).replace('state["mode"] == "reverse"', 'state["mode"] == "clamped"').replace(
                    'return {"value": state["value"] - action["delta"], "mode": state["mode"]}',
                    'if state["value"] + action["delta"] < 0:\n                return {"value": 0, "mode": state["mode"]}\n            return {"value": state["value"] + action["delta"], "mode": state["mode"]}',
                )
                return json.dumps({"type": "write_file", "path": "world_model.py", "content": source})
            return json.dumps({
                "type": "unresolved",
                "notes": "Positive deltas cannot distinguish signed-delta from absolute-delta dynamics; observe a negative delta.",
            })

        def summarize_hot_draft(self, old_summary, moved_turns):
            raise AssertionError

    model = RuleModel()
    output = tmp_path / "result.json"
    result = run_registered_campaign(campaign, model=model, output_path=output)

    assert result["verdict"] == "W2_PASS"
    assert result["summary"] == {
        "structural_invariants_pass": True,
        "reverse_recovered": True,
        "reverse_iterative_repair": True,
        "resolvable_public_pass_count": 3,
        "resolvable_holdout_pass_count": 3,
        "uncertainty_preserved": True,
        "provider_failed": False,
    }
    hidden_tokens = {
        json.dumps(
            {
                "state": {"value": item.state.value, "mode": item.state.mode},
                "action": {"kind": item.action.kind, "delta": item.action.delta},
                "observed": {"value": item.observed.value, "mode": item.observed.mode},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for case in campaign.resolvable
        for item in case.hidden_evidence
    }
    assert all(token not in "\n".join(model.calls) for token in hidden_tokens)
    assert output.exists()


def test_ambiguity_model_failure_is_inconclusive_not_fabricated_certainty(tmp_path):
    campaign = load_registered_w2(Path(__file__).parent / "fixtures" / "w2" / "manifest.json")

    class MostlyRuleModel:
        def __init__(self):
            self.delegate = None

        def generate(self, recent_context, user_message, *, system_prompt):
            if '"mode":"boost"' in user_message:
                source = REVERSE_SOURCE.replace(
                    '"reverse" if state["mode"] == "forward" else "forward"',
                    '"boost" if state["mode"] == "normal" else "normal"',
                ).replace('state["mode"] == "reverse"', 'state["mode"] == "boost"').replace(
                    'state["value"] - action["delta"]', 'state["value"] + action["delta"] * 2'
                )
                return json.dumps({"type": "write_file", "path": "world_model.py", "content": source})
            if '"mode":"reverse"' in user_message:
                return json.dumps({"type": "write_file", "path": "world_model.py", "content": REVERSE_SOURCE})
            if '"mode":"clamped"' in user_message:
                source = REVERSE_SOURCE.replace(
                    '"reverse" if state["mode"] == "forward" else "forward"',
                    '"clamped" if state["mode"] == "free" else "free"',
                ).replace('state["mode"] == "reverse"', 'state["mode"] == "clamped"').replace(
                    'return {"value": state["value"] - action["delta"], "mode": state["mode"]}',
                    'if state["value"] + action["delta"] < 0:\n                return {"value": 0, "mode": state["mode"]}\n            return {"value": state["value"] + action["delta"], "mode": state["mode"]}',
                )
                return json.dumps({"type": "write_file", "path": "world_model.py", "content": source})
            return "not-json"

        def summarize_hot_draft(self, old_summary, moved_turns):
            raise AssertionError

    result = run_registered_campaign(
        campaign,
        model=MostlyRuleModel(),
        output_path=tmp_path / "inconclusive.json",
    )

    assert result["records"][3]["status"] == "STRUCTURAL_FAILURE"
    assert result["records"][3]["current_changed"] is False
    assert result["verdict"] == "W2_INCONCLUSIVE"


def test_prompt_and_w2_source_do_not_install_planner_memory_or_evolution_vocabulary():
    source_path = Path(__file__).parent / "world_model_revision_experiment.py"
    source = source_path.read_text(encoding="utf-8").lower()
    forbidden = [
        "executionorgan", "stateestimator", "dynamicsmodel", "worldmodelregistry",
        "scope resolver", "scope router", "persistent ipython", "take_action",
        "recommended_action", "subgoal", "candidate", "promotion",
    ]
    assert all(term not in source for term in forbidden)
    assert "plan" not in BUILDER_SYSTEM_PROMPT.lower()
    assert "memory" not in BUILDER_SYSTEM_PROMPT.lower()
