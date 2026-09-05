from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from Mind import world_model_read_batch_experiment as w5
from Mind import world_model_representation_experiment as w6
from Mind.world_model_latent_state_experiment import (
    BoundedLatentStateBuilder,
    EnvironmentObservation,
    LatentRevisionRequest,
    LatentTrajectory,
    LatentTrajectoryStep,
    W7_BUILDER_SYSTEM_PROMPT,
    W7_SINGLE_VARIABLE,
    _LATENT_CHILD_RUNNER,
    _campaign_verdict,
    _hidden_trajectories_isolated,
    _source_contract_self_check,
    analyze_latent_state,
    evaluate_model_source,
    load_registered_w7,
    run_registered_campaign,
    run_latent_revision_episode,
    validate_model_source,
    verify_trajectories,
)
from Mind.world_model_builder_experiment import DynamicsAction
from Mind.world_model_revision_experiment import DEFAULT_BOUNDS, EpisodeStatus


BASELINE_SOURCE = '''class CanonicalWorldModel:
    version = "wm-latent-v1"

    def init_state(self, observation):
        return {"value": observation["value"]}

    def transition(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"]}
        return {"value": state["value"] + action["delta"]}

    def observe(self, state):
        return {"value": state["value"]}
'''


def _step(kind: str, value: int, delta: int | None = None) -> LatentTrajectoryStep:
    return LatentTrajectoryStep(
        action=DynamicsAction(kind, delta),
        observation=EnvironmentObservation(value),
    )


BOOST_PUBLIC = LatentTrajectory(
    initial_observation=EnvironmentObservation(5),
    steps=(
        _step("step", 7, 2),
        _step("toggle", 7),
        _step("step", 5, -1),
        _step("step", 9, 2),
        _step("step", 5, -2),
        _step("toggle", 5),
        _step("step", 7, 2),
    ),
)

BOOST_HIDDEN = LatentTrajectory(
    initial_observation=EnvironmentObservation(13),
    steps=(
        _step("toggle", 13),
        _step("step", 23, 5),
        _step("step", 19, -2),
        _step("step", 25, 3),
        _step("toggle", 25),
        _step("step", 21, -4),
    ),
)

REVERSE_PUBLIC = LatentTrajectory(
    initial_observation=EnvironmentObservation(5),
    steps=(
        _step("step", 7, 2),
        _step("toggle", 7),
        _step("step", 5, 2),
        _step("step", 3, 2),
        _step("toggle", 3),
        _step("step", 5, 2),
    ),
)

CLAMP_PUBLIC = LatentTrajectory(
    initial_observation=EnvironmentObservation(0),
    steps=(
        _step("step", -3, -3),
        _step("step", 0, 3),
        _step("toggle", 0),
        _step("step", 0, -3),
        _step("step", 2, 2),
        _step("step", 0, -5),
        _step("toggle", 0),
        _step("step", -3, -3),
    ),
)

BOOST_SOURCE = '''class CanonicalWorldModel:
    version = "wm-latent-v1"

    def init_state(self, observation):
        return {"value": observation["value"], "boosted": False}

    def transition(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "boosted": not state["boosted"]}
        factor = 2 if state["boosted"] else 1
        return {"value": state["value"] + action["delta"] * factor, "boosted": state["boosted"]}

    def observe(self, state):
        return {"value": state["value"]}
'''

REVERSE_SOURCE = '''class CanonicalWorldModel:
    version = "wm-latent-v1"

    def init_state(self, observation):
        return {"value": observation["value"], "direction": "forward"}

    def transition(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "direction": "reverse" if state["direction"] == "forward" else "forward"}
        if state["direction"] == "reverse":
            return {"value": state["value"] - action["delta"], "direction": state["direction"]}
        return {"value": state["value"] + action["delta"], "direction": state["direction"]}

    def observe(self, state):
        return {"value": state["value"]}
'''

CLAMP_SOURCE = '''class CanonicalWorldModel:
    version = "wm-latent-v1"

    def init_state(self, observation):
        return {"value": observation["value"], "bounded": False}

    def transition(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "bounded": not state["bounded"]}
        value = state["value"] + action["delta"]
        if state["bounded"]:
            value = max(0, value)
        return {"value": value, "bounded": state["bounded"]}

    def observe(self, state):
        return {"value": state["value"]}
'''

ONE_STEP_PSEUDO_LATENT_SOURCE = '''class CanonicalWorldModel:
    version = "wm-latent-v1"

    def init_state(self, observation):
        return {"value": observation["value"], "boosted": False}

    def transition(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "boosted": True}
        factor = 2 if state["boosted"] else 1
        return {"value": state["value"] + action["delta"] * factor, "boosted": False}

    def observe(self, state):
        return {"value": state["value"]}
'''


def test_baseline_no_latent_model_fails_resolvable_trajectory():
    verified = verify_trajectories(
        BASELINE_SOURCE,
        (BOOST_PUBLIC,),
        expected_version="wm-latent-v1",
    )

    assert verified["accuracy"] < 1.0
    assert verified["first_divergence"] == {
        "trajectory": 0,
        "step": 2,
        "action": {"kind": "step", "delta": -1},
        "model_state": {"value": 7},
        "predicted": {"value": 6},
        "actual": {"value": 5},
    }


def test_model_state_threads_without_actual_observation_reset():
    replay = evaluate_model_source(
        BASELINE_SOURCE,
        (BOOST_PUBLIC,),
        expected_version="wm-latent-v1",
    )[0]

    assert replay["steps"][2]["state_after"] == {"value": 6}
    assert replay["steps"][3]["state_before"] == {"value": 6}


def test_isolated_evaluator_initializes_each_trajectory_exactly_once():
    assert _LATENT_CHILD_RUNNER.count("model.init_state(") == 1


def test_three_method_signatures_are_exact():
    bad_sources = (
        BASELINE_SOURCE.replace("init_state(self, observation)", "init_state(self, observation, state)"),
        BASELINE_SOURCE.replace("transition(self, state, action)", "transition(self, observation, action)"),
        BASELINE_SOURCE.replace("observe(self, state)", "observe(self, state, observation)"),
    )

    for source in bad_sources:
        try:
            validate_model_source(source, expected_version="wm-latent-v1")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid method authority was accepted")


def test_later_actual_observation_is_not_a_model_input():
    cheating = BASELINE_SOURCE.replace(
        'return {"value": state["value"] + action["delta"]}',
        'return {"value": action["observation"]}',
    )

    try:
        validate_model_source(cheating, expected_version="wm-latent-v1")
    except ValueError:
        pass
    else:
        raise AssertionError("future observation became available to transition")


def test_latent_scalar_field_name_is_not_hard_coded_to_mode():
    validate_model_source(BOOST_SOURCE, expected_version="wm-latent-v1")
    initial = evaluate_model_source(
        BOOST_SOURCE,
        (BOOST_PUBLIC,),
        expected_version="wm-latent-v1",
    )[0]["initial_state"]

    assert initial == {"value": 5, "boosted": False}
    assert "mode" not in initial


def test_bounded_flat_state_is_enforced():
    nested = BOOST_SOURCE.replace(
        'return {"value": observation["value"], "boosted": False}',
        'return {"value": observation["value"], "nested": {"x": 1}}',
    )
    too_many = BOOST_SOURCE.replace(
        'return {"value": observation["value"], "boosted": False}',
        'return {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6, "g": 7, "h": 8, "i": 9}',
    )

    for source in (nested, too_many):
        try:
            validate_model_source(source, expected_version="wm-latent-v1")
        except ValueError:
            pass
        else:
            raise AssertionError("unbounded or nested model state was accepted")


def test_input_mutation_is_rejected():
    source = BOOST_SOURCE.replace(
        'if action["kind"] == "toggle":',
        'state["boosted"] = True\n        if action["kind"] == "toggle":',
    )

    try:
        validate_model_source(source, expected_version="wm-latent-v1")
    except ValueError:
        pass
    else:
        raise AssertionError("input mutation was accepted")


def test_dangerous_python_remains_rejected():
    dangerous = (
        'import os',
        'return {"value": open("x", "w")}',
        'return {"value": state.__class__}',
        'return {"value": __import__("os")}',
        'return {"value": sum((1, 2))}',
    )

    for body in dangerous:
        source = BASELINE_SOURCE.replace(
            'return {"value": state["value"]}',
            body,
            1,
        )
        try:
            validate_model_source(source, expected_version="wm-latent-v1")
        except ValueError:
            pass
        else:
            raise AssertionError(f"dangerous source accepted: {body}")


def test_latent_boost_model_passes_public_trajectory():
    assert verify_trajectories(
        BOOST_SOURCE,
        (BOOST_PUBLIC,),
        expected_version="wm-latent-v1",
    )["accuracy"] == 1.0


def test_latent_reverse_model_passes_public_trajectory():
    assert verify_trajectories(
        REVERSE_SOURCE,
        (REVERSE_PUBLIC,),
        expected_version="wm-latent-v1",
    )["accuracy"] == 1.0


def test_latent_clamp_model_passes_public_trajectory():
    assert verify_trajectories(
        CLAMP_SOURCE,
        (CLAMP_PUBLIC,),
        expected_version="wm-latent-v1",
    )["accuracy"] == 1.0


def test_one_step_only_pseudo_latent_fails_persistence_holdout():
    verified = verify_trajectories(
        ONE_STEP_PSEUDO_LATENT_SOURCE,
        (BOOST_HIDDEN,),
        expected_version="wm-latent-v1",
    )

    assert verified["accuracy"] < 1.0
    assert verified["first_divergence"]["step"] == 2


def test_latent_component_is_causal_persistent_and_toggle_updated():
    analysis = analyze_latent_state(
        BOOST_SOURCE,
        (BOOST_PUBLIC,),
        expected_version="wm-latent-v1",
    )

    assert analysis == {
        "causal_fields": ["boosted"],
        "persistent_fields": ["boosted"],
        "toggle_updated_fields": ["boosted"],
        "reconstructed_fields": ["boosted"],
        "reconstructed": True,
    }


def test_observable_only_state_is_not_misclassified_as_latent():
    analysis = analyze_latent_state(
        BASELINE_SOURCE,
        (BOOST_PUBLIC,),
        expected_version="wm-latent-v1",
    )

    assert analysis["reconstructed"] is False
    assert analysis["reconstructed_fields"] == []


class _ScriptedNativeModel:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self.calls = list(calls)

    def complete(self, messages, *, system_prompt, tools):
        call = self.calls.pop(0)
        content = [{
            "type": "tool_use",
            "id": call["id"],
            "name": call["name"],
            "input": call["input"],
        }]
        return SimpleNamespace(
            content=content,
            request_body={"messages": messages, "system": system_prompt, "tools": list(tools)},
            response_body={"content": content},
        )


def _revision(trajectories=(BOOST_PUBLIC,)) -> LatentRevisionRequest:
    return LatentRevisionRequest(
        current_model_version="wm-latent-v1",
        current_model_source=BASELINE_SOURCE,
        trajectories=trajectories,
        revision_reason="Observed trajectory divergence requires review.",
    )


def _episode_paths(tmp_path):
    current = tmp_path / "current" / "world_model.py"
    working = tmp_path / "working" / "world_model.py"
    notes = tmp_path / "working" / "notes" / "world_model.md"
    current.parent.mkdir(parents=True)
    current.write_text(BASELINE_SOURCE, encoding="utf-8")
    return current, working, notes


def test_valid_latent_source_still_requires_and_passes_trajectory_verifier(tmp_path):
    current, working, notes = _episode_paths(tmp_path)
    model = _ScriptedNativeModel([{
        "id": "write-1",
        "name": "write_file",
        "input": {"path": "world_model.py", "content": BOOST_SOURCE},
    }])

    result = run_latent_revision_episode(
        builder=BoundedLatentStateBuilder(model, DEFAULT_BOUNDS),
        revision=_revision(),
        episode_ref="w7-test",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.CONSISTENT_ENOUGH
    assert result.semantic_revisions == 1
    assert current.read_text(encoding="utf-8") == BOOST_SOURCE


def test_invalid_source_never_reaches_current(tmp_path):
    current, working, notes = _episode_paths(tmp_path)
    invalid = BOOST_SOURCE.replace(
        'return {"value": state["value"]}',
        'return {"value": open("escape", "w")}',
        1,
    )
    model = _ScriptedNativeModel([
        {
            "id": "write-bad",
            "name": "write_file",
            "input": {"path": "world_model.py", "content": invalid},
        },
        {
            "id": "stop",
            "name": "unresolved",
            "input": {"notes": "The candidate was structurally unsafe."},
        },
    ])

    result = run_latent_revision_episode(
        builder=BoundedLatentStateBuilder(model, DEFAULT_BOUNDS),
        revision=_revision(),
        episode_ref="w7-invalid",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.semantic_revisions == 0
    assert current.read_text(encoding="utf-8") == BASELINE_SOURCE


def test_ambiguity_unresolved_preserves_simple_current_model(tmp_path):
    current, working, notes = _episode_paths(tmp_path)
    ambiguity = LatentTrajectory(
        initial_observation=EnvironmentObservation(4),
        steps=(_step("step", 5, 1), _step("step", 7, 2)),
    )
    model = _ScriptedNativeModel([{
        "id": "stop",
        "name": "unresolved",
        "input": {"notes": "No latent state is required by these observations."},
    }])

    result = run_latent_revision_episode(
        builder=BoundedLatentStateBuilder(model, DEFAULT_BOUNDS),
        revision=_revision((ambiguity,)),
        episode_ref="w7-ambiguous",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    assert current.read_text(encoding="utf-8") == BASELINE_SOURCE


def test_w7_changes_only_model_owned_trajectory_state_contract():
    expected = w6.W6_BUILDER_SYSTEM_PROMPT.replace(
        "- A single observation is not necessarily the full true state.\n",
        "- A single observation is not necessarily the full true state.\n"
        "- Environment observations may not expose the full state needed for prediction.\n"
        "- The executable World Model owns its internal state representation.\n"
        "- If trajectory evidence cannot be explained by observable fields alone, you may "
        "add compact latent state only when doing so improves falsifiable trajectory prediction.\n"
        "- Do not invent latent variables when the evidence does not require them.\n",
    ).replace(w6._W6_SOURCE_CONTRACT, pytest.importorskip(
        "Mind.world_model_latent_state_experiment"
    )._W7_SOURCE_CONTRACT)

    assert W7_BUILDER_SYSTEM_PROMPT == expected
    assert tuple(tool["name"] for tool in w5.NATIVE_TOOL_SPECS) == (
        "read_file",
        "run_python",
        "write_file",
        "unresolved",
    )
    assert "latent-boost" not in W7_BUILDER_SYSTEM_PROMPT
    assert "latent-reverse" not in W7_BUILDER_SYSTEM_PROMPT
    assert "latent-clamped" not in W7_BUILDER_SYSTEM_PROMPT
    assert "field name" not in W7_BUILDER_SYSTEM_PROMPT.lower()
    assert "model-owned state" in W7_SINGLE_VARIABLE


def test_source_contract_self_check_rejects_authority_and_unbounded_shapes():
    check = _source_contract_self_check()

    assert check["pass"] is True
    assert all(check["accepted"].values())
    assert all(check["rejected"].values())


def test_hidden_trajectory_isolation_detects_a_provider_visible_holdout():
    public_turns = [{
        "provider_request": {
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": "public only"}],
            }]
        },
        "attempted_request": None,
    }]
    leaked_turns = [{
        "provider_request": {
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": '{"action":{"delta":5,"kind":"step"},'
                    '"observation":{"value":23}}',
                }],
            }]
        },
        "attempted_request": None,
    }]

    assert _hidden_trajectories_isolated(
        (BOOST_PUBLIC,), (BOOST_HIDDEN,), public_turns
    ) is True
    assert _hidden_trajectories_isolated(
        (BOOST_PUBLIC,), (BOOST_HIDDEN,), leaked_turns
    ) is False


def test_pairing_or_teacher_forcing_violation_is_a_fail_verdict():
    records = [{
        "evidence_unchanged": True,
        "current_coherent": True,
        "teacher_forcing_absent": False,
    }]
    summary = {
        "authority_isolation_pass": True,
        "context_bound_pass": True,
        "episode_freshness_pass": True,
        "hidden_isolation_pass": True,
        "pairing_integrity_pass": True,
        "provider_failed": False,
        "source_safety_pass": True,
    }

    assert _campaign_verdict(records, summary) == "W7_FAIL"

    records[0]["teacher_forcing_absent"] = True
    summary["pairing_integrity_pass"] = False
    assert _campaign_verdict(records, summary) == "W7_FAIL"


def test_provider_caused_incomplete_pairing_is_inconclusive():
    records = [{
        "evidence_unchanged": True,
        "current_coherent": True,
        "teacher_forcing_absent": True,
    }]
    summary = {
        "authority_isolation_pass": True,
        "context_bound_pass": True,
        "episode_freshness_pass": True,
        "hidden_isolation_pass": True,
        "pairing_integrity_pass": False,
        "provider_failed": True,
        "source_safety_pass": True,
    }

    assert _campaign_verdict(records, summary) == "W7_INCONCLUSIVE"


def test_registered_w7_manifest_is_frozen_and_deepseek_only():
    campaign = load_registered_w7(
        Path(__file__).parent / "fixtures" / "w7" / "manifest.json"
    )

    assert campaign.model_config["provider"] == "deepseek-anthropic"
    assert campaign.model_config["model"] == "deepseek-v4-pro"
    assert campaign.bounds == campaign.w6_campaign.bounds == DEFAULT_BOUNDS
    assert len(campaign.resolvable) == 3
    assert campaign.ambiguity.record_ref == "insufficient-latent-evidence"


def test_registered_campaign_reconstructs_three_latent_mechanisms_without_hidden_leak(
    tmp_path,
):
    campaign = load_registered_w7(
        Path(__file__).parent / "fixtures" / "w7" / "manifest.json"
    )
    model = _ScriptedNativeModel([
        {
            "id": "boost-write",
            "name": "write_file",
            "input": {"path": "world_model.py", "content": BOOST_SOURCE},
        },
        {
            "id": "reverse-write",
            "name": "write_file",
            "input": {"path": "world_model.py", "content": REVERSE_SOURCE},
        },
        {
            "id": "clamp-write",
            "name": "write_file",
            "input": {"path": "world_model.py", "content": CLAMP_SOURCE},
        },
        {
            "id": "ambiguity-stop",
            "name": "unresolved",
            "input": {"notes": "The public trajectory does not require latent state."},
        },
    ])
    output = tmp_path / "w7-result.json"

    result = run_registered_campaign(campaign, model=model, output_path=output)

    assert result["verdict"] == "W7_PASS"
    assert result["summary"]["latent_reconstruction_records"] == 3
    assert result["summary"]["resolvable_public_perfect"] == 3
    assert result["summary"]["resolvable_hidden_perfect"] == 3
    assert result["summary"]["teacher_forcing_absent_pass"] is True
    assert result["summary"]["ambiguity_native_unresolved"] is True
    assert all(record["hidden_isolated"] is True for record in result["records"])
    assert all(
        record["teacher_forcing_absent"] is True for record in result["records"]
    )
    for record in result["records"]:
        visible = repr([turn["provider_request"] for turn in record["turns"]])
        assert record["record_ref"] not in visible

    with pytest.raises(FileExistsError, match="already exists"):
        run_registered_campaign(campaign, model=model, output_path=output)


def test_hidden_only_candidate_failure_is_inconclusive_not_campaign_abort(tmp_path):
    campaign = load_registered_w7(
        Path(__file__).parent / "fixtures" / "w7" / "manifest.json"
    )
    hidden_fragile_source = BOOST_SOURCE.replace(
        'return {"value": observation["value"], "boosted": False}',
        'if observation["value"] > 10:\n'
        '            return {"value": observation["value"]}\n'
        '        return {"value": observation["value"], "boosted": False}',
    )
    model = _ScriptedNativeModel([
        {
            "id": "boost-write",
            "name": "write_file",
            "input": {"path": "world_model.py", "content": hidden_fragile_source},
        },
        {
            "id": "reverse-write",
            "name": "write_file",
            "input": {"path": "world_model.py", "content": REVERSE_SOURCE},
        },
        {
            "id": "clamp-write",
            "name": "write_file",
            "input": {"path": "world_model.py", "content": CLAMP_SOURCE},
        },
        {
            "id": "ambiguity-stop",
            "name": "unresolved",
            "input": {"notes": "The public trajectory does not require latent state."},
        },
    ])

    result = run_registered_campaign(
        campaign,
        model=model,
        output_path=tmp_path / "hidden-failure-result.json",
    )

    assert result["verdict"] == "W7_INCONCLUSIVE"
    assert result["records"][0]["public_accuracy"] == 1.0
    assert result["records"][0]["hidden_accuracy"] == 0.0
    assert result["records"][0]["hidden_replay_error"] == "evaluation_failed"
    assert result["records"][0]["current_coherent"] is True


def test_builder_requested_evidence_contains_public_trajectory_but_no_record_identity(
    tmp_path,
):
    current, working, notes = _episode_paths(tmp_path)
    model = _ScriptedNativeModel([
        {
            "id": "public-read",
            "name": "read_file",
            "input": {"path": "evidence.json", "start": 0, "count": 1},
        },
        {
            "id": "stop",
            "name": "unresolved",
            "input": {"notes": "Inspection complete."},
        },
    ])
    builder = BoundedLatentStateBuilder(model, DEFAULT_BOUNDS)

    result = run_latent_revision_episode(
        builder=builder,
        revision=_revision(),
        episode_ref="w7-public-view",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    visible = repr([turn["provider_request"] for turn in builder.turns])
    assert "latent-boost" not in visible
    assert "record_ref" not in visible
    assert "evidence.json" in visible
    assert "boosted" not in visible
    assert _hidden_trajectories_isolated(
        (BOOST_PUBLIC,), (BOOST_HIDDEN,), builder.turns
    ) is True


def test_isolated_evaluator_rejects_runtime_state_range_escape():
    source = BASELINE_SOURCE.replace(
        'return {"value": state["value"] + action["delta"]}',
        'return {"value": state["value"] + action["delta"] * 2}',
    )
    trajectory = LatentTrajectory(
        initial_observation=EnvironmentObservation(600_000),
        steps=(_step("step", 1_000_000, 600_000),),
    )

    with pytest.raises(ValueError, match="isolated latent model execution failed"):
        evaluate_model_source(
            source,
            (trajectory,),
            expected_version="wm-latent-v1",
        )


@pytest.mark.parametrize(
    "candidate",
    [
        BASELINE_SOURCE + "\nclass Helper:\n    pass\n",
        BASELINE_SOURCE.replace(
            "    def observe(self, state):",
            "    def helper(self, state):\n"
            "        return state\n\n"
            "    def observe(self, state):",
        ),
        BASELINE_SOURCE.replace(
            "class CanonicalWorldModel:",
            "class CanonicalWorldModel[T]:",
        ),
        BASELINE_SOURCE.replace(
            "def transition(self, state, action):",
            "def transition[T](self, state, action):",
        ),
    ],
)
def test_contract_rejects_extra_classes_helpers_and_type_parameters(candidate):
    with pytest.raises(ValueError):
        validate_model_source(candidate, expected_version="wm-latent-v1")
