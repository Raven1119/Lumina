from __future__ import annotations

import json
import inspect
from pathlib import Path

from Mind.world_model_builder_experiment import (
    DynamicsAction,
    DynamicsObservation,
    DynamicsState,
    RevisionEvidence,
    WorldModelRevisionRequest,
)
from Mind.world_model_revision_experiment import DEFAULT_BOUNDS, EpisodeStatus, RevisionBounds
from Mind.world_model_stateful_revision_experiment import (
    W3_SINGLE_VARIABLE,
    StatefulWorldModelRevisionBuilder,
    load_registered_w3,
    run_registered_campaign,
    run_stateful_revision_episode,
)


BASE_SOURCE = '''class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "reverse" if state["mode"] == "forward" else "forward"}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''

WRONG_SAFE_SOURCE = BASE_SOURCE.replace(
    'return {"value": state["value"] + action["delta"], "mode": state["mode"]}',
    'if state["mode"] == "reverse":\n            return {"value": state["value"] + action["delta"], "mode": state["mode"]}\n        return {"value": state["value"] + action["delta"], "mode": state["mode"]}',
)

REVERSE_SOURCE = BASE_SOURCE.replace(
    'state["value"] + action["delta"]',
    'state["value"] - action["delta"]',
)


def _boost_source() -> str:
    return '''class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "boost" if state["mode"] == "normal" else "normal"}
        if state["mode"] == "boost":
            return {"value": state["value"] + action["delta"] * 2, "mode": state["mode"]}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''


def _clamped_source() -> str:
    return '''class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "clamped" if state["mode"] == "free" else "free"}
        if state["mode"] == "clamped":
            if state["value"] + action["delta"] < 0:
                return {"value": 0, "mode": state["mode"]}
            return {"value": state["value"] + action["delta"], "mode": state["mode"]}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''


def _request():
    evidence = (
        RevisionEvidence(
            state=DynamicsState(4, "forward"),
            action=DynamicsAction("step", 2),
            expected=DynamicsObservation(6, "forward"),
            observed=DynamicsObservation(6, "forward"),
            result="MATCHED",
        ),
        RevisionEvidence(
            state=DynamicsState(4, "reverse"),
            action=DynamicsAction("step", 2),
            expected=DynamicsObservation(6, "reverse"),
            observed=DynamicsObservation(2, "reverse"),
            result="ERROR",
        ),
    )
    return WorldModelRevisionRequest(
        current_model_version="wm-v1",
        current_model_source=BASE_SOURCE,
        evidence=evidence,
        revision_reason="Repair the predictive dynamics from objective evidence.",
    )


class RecordingModel:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[list[dict[str, str]], str, str]] = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        return json.dumps(self.outputs.pop(0), separators=(",", ":"))

    def summarize_hot_draft(self, old_summary, moved_turns):
        raise AssertionError


def _paths(tmp_path: Path):
    current = tmp_path / "current" / "world_model.py"
    working = tmp_path / "working" / "world_model.py"
    notes = tmp_path / "working" / "notes" / "world_model.md"
    current.parent.mkdir(parents=True)
    current.write_text(BASE_SOURCE, encoding="utf-8")
    return current, working, notes


def test_prior_assistant_action_visible_next_turn(tmp_path):
    first_action = {"type": "read_file", "path": "world_model.py"}
    model = RecordingModel([
        first_action,
        {"type": "unresolved", "notes": "Need a discriminating observation."},
    ])
    current, working, notes = _paths(tmp_path)

    result = run_stateful_revision_episode(
        builder=StatefulWorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        revision=_request(),
        episode_ref="w3-visible-action",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    second_recent_context = model.calls[1][0]
    assert second_recent_context[-1] == {
        "role": "assistant",
        "text": json.dumps(first_action, separators=(",", ":")),
    }


def test_prior_tool_observation_visible_next_turn(tmp_path):
    model = RecordingModel([
        {"type": "read_file", "path": "world_model.py"},
        {"type": "unresolved", "notes": "Need a discriminating observation."},
    ])
    current, working, notes = _paths(tmp_path)

    run_stateful_revision_episode(
        builder=StatefulWorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        revision=_request(),
        episode_ref="w3-visible-observation",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    second_message = json.loads(model.calls[1][1])
    assert second_message["recent_observations"][-1] == {
        "kind": "file",
        "path": "world_model.py",
        "content": BASE_SOURCE,
    }


def test_context_cap_remains_16000(tmp_path):
    assert DEFAULT_BOUNDS.max_context_chars == 16_000
    bounds = RevisionBounds(**(DEFAULT_BOUNDS.__dict__ | {"max_context_chars": 2_000}))
    model = RecordingModel([
        {
            "type": "write_file",
            "path": "notes/world_model.md",
            "content": "x" * 1_000,
        },
    ])
    current, working, notes = _paths(tmp_path)
    before = current.read_bytes()

    result = run_stateful_revision_episode(
        builder=StatefulWorldModelRevisionBuilder(model, bounds),
        revision=_request(),
        episode_ref="w3-context-bound",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.BUDGET_EXHAUSTED
    assert result.failure_reason == "context_bound_exhausted"
    assert len(model.calls) == 1
    assert current.read_bytes() == before


def test_multiple_turns_accumulate_within_bound(tmp_path):
    first_action = {"type": "read_file", "path": "world_model.py"}
    second_action = {"type": "read_file", "path": "evidence.json", "start": 0, "count": 2}
    model = RecordingModel([
        first_action,
        second_action,
        {"type": "unresolved", "notes": "The inspected evidence remains ambiguous."},
    ])
    current, working, notes = _paths(tmp_path)

    run_stateful_revision_episode(
        builder=StatefulWorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        revision=_request(),
        episode_ref="w3-accumulation",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    third_recent, third_message, _ = model.calls[2]
    assert [item["role"] for item in third_recent] == [
        "user", "assistant", "user", "assistant",
    ]
    assert third_recent[1]["text"] == json.dumps(first_action, separators=(",", ":"))
    prior_model_read = json.loads(third_recent[2]["text"])["recent_observations"][-1]
    assert prior_model_read["content"] == BASE_SOURCE
    assert third_recent[3]["text"] == json.dumps(second_action, separators=(",", ":"))
    assert json.loads(third_message)["recent_observations"][-1]["kind"] == "evidence"
    assert all(
        len(message) + sum(len(item["text"]) for item in recent) <= DEFAULT_BOUNDS.max_context_chars
        for recent, message, _ in model.calls
    )


def test_episode_b_does_not_see_episode_a(tmp_path):
    marker = "episode-a-private-marker"
    model = RecordingModel([
        {"type": "write_file", "path": "notes/world_model.md", "content": marker},
        {"type": "unresolved", "notes": marker},
        {"type": "unresolved", "notes": "Episode B remains independent."},
    ])
    builder = StatefulWorldModelRevisionBuilder(model, DEFAULT_BOUNDS)
    current, working, notes = _paths(tmp_path)

    run_stateful_revision_episode(
        builder=builder,
        revision=_request(),
        episode_ref="episode-a",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )
    run_stateful_revision_episode(
        builder=builder,
        revision=_request(),
        episode_ref="episode-b",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    episode_b_recent, episode_b_message, _ = model.calls[2]
    assert episode_b_recent == []
    assert marker not in episode_b_message


def test_auto_verifier_feedback_visible_next_turn(tmp_path):
    edit = {"type": "write_file", "path": "world_model.py", "content": WRONG_SAFE_SOURCE}
    model = RecordingModel([
        edit,
        {"type": "unresolved", "notes": "The first edit still diverges."},
    ])
    current, working, notes = _paths(tmp_path)

    result = run_stateful_revision_episode(
        builder=StatefulWorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        revision=_request(),
        episode_ref="w3-verifier-visible",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.semantic_revisions == 1
    second_recent, second_message, _ = model.calls[1]
    assert second_recent[-1] == {
        "role": "assistant",
        "text": json.dumps(edit, separators=(",", ":")),
    }
    feedback = json.loads(second_message)["recent_observations"][-1]
    assert feedback["kind"] == "verifier"
    assert feedback["first_divergence"]["observed_delta"] == -2


def test_budget_exhaustion_preserves_current(tmp_path):
    budget_bounds = RevisionBounds(**(
        DEFAULT_BOUNDS.__dict__ | {"max_model_turns": 2, "max_tool_calls": 2}
    ))
    budget_model = RecordingModel([
        {"type": "read_file", "path": "world_model.py"},
        {"type": "read_file", "path": "world_model.py"},
    ])
    current, working, notes = _paths(tmp_path / "budget")
    before = current.read_bytes()
    exhausted = run_stateful_revision_episode(
        builder=StatefulWorldModelRevisionBuilder(budget_model, budget_bounds),
        revision=_request(),
        episode_ref="w3-budget",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert exhausted.status is EpisodeStatus.BUDGET_EXHAUSTED
    assert current.read_bytes() == before


def test_unresolved_preserves_current(tmp_path):
    model = RecordingModel([
        {"type": "unresolved", "notes": "Competing rules remain observationally equivalent."},
    ])
    current, working, notes = _paths(tmp_path)
    before = current.read_bytes()

    unresolved = run_stateful_revision_episode(
        builder=StatefulWorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        revision=_request(),
        episode_ref="w3-unresolved",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert unresolved.status is EpisodeStatus.UNRESOLVED
    assert current.read_bytes() == before


def test_builder_surface_does_not_gain_mind_root_or_execution_authority():
    builder = StatefulWorldModelRevisionBuilder(RecordingModel([]), DEFAULT_BOUNDS)
    forbidden = {
        "execute", "take_action", "spawn_child", "shell", "browser", "ipython",
        "mind_trace", "root_transcript", "set_intention",
    }
    assert forbidden.isdisjoint(dir(builder))
    source = inspect.getsource(StatefulWorldModelRevisionBuilder).lower()
    assert "executionorgan" not in source
    assert "spawn_child" not in source


def test_mind_root_history_never_enters_history(tmp_path):
    marker = "PRIVATE_MIND_ROOT_TRANSCRIPT_MARKER"
    model = RecordingModel([
        {"type": "unresolved", "notes": "No transcript authority is available."},
    ])
    current, working, notes = _paths(tmp_path)

    run_stateful_revision_episode(
        builder=StatefulWorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        revision=_request(),
        episode_ref=marker,
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert marker not in model.calls[0][1]
    assert model.calls[0][0] == []


def test_hidden_holdout_never_enters_history(tmp_path):
    campaign = load_registered_w3(Path(__file__).parent / "fixtures" / "w3" / "manifest.json")

    class UnresolvedModel:
        def generate(self, recent_context, user_message, *, system_prompt):
            return json.dumps({
                "type": "unresolved",
                "notes": "The bounded public evidence does not justify a rewrite.",
            })

        def summarize_hot_draft(self, old_summary, moved_turns):
            raise AssertionError

    result = run_registered_campaign(
        campaign,
        model=UnresolvedModel(),
        output_path=tmp_path / "hidden-isolation.json",
    )

    assert result["summary"]["hidden_isolation_pass"] is True
    assert all(record["hidden_isolated"] is True for record in result["records"])


def test_eight_turn_frozen_fixture_history_stays_within_16000(tmp_path):
    campaign = load_registered_w3(Path(__file__).parent / "fixtures" / "w3" / "manifest.json")
    revision = campaign.w2_campaign.resolvable[2].revision
    actions = [
        {"type": "read_file", "path": "world_model.py"},
        {"type": "read_file", "path": "evidence.json", "start": 0, "count": 2},
    ] * 4
    model = RecordingModel(actions)
    current = tmp_path / "current" / "world_model.py"
    working = tmp_path / "working" / "world_model.py"
    notes = tmp_path / "working" / "notes" / "world_model.md"
    current.parent.mkdir(parents=True)
    current.write_text(revision.current_model_source, encoding="utf-8")

    result = run_stateful_revision_episode(
        builder=StatefulWorldModelRevisionBuilder(model, DEFAULT_BOUNDS),
        revision=revision,
        episode_ref="w3-eight-turn-bound",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.BUDGET_EXHAUSTED
    assert len(model.calls) == 8
    assert max(
        len(message) + sum(len(item["text"]) for item in recent)
        for recent, message, _ in model.calls
    ) <= 16_000


def test_registered_campaign_reuses_w2_and_records_progression_without_hidden_leak(tmp_path):
    campaign = load_registered_w3(Path(__file__).parent / "fixtures" / "w3" / "manifest.json")

    class StatefulRuleModel:
        def generate(self, recent_context, user_message, *, system_prompt):
            assistant_turns = sum(item["role"] == "assistant" for item in recent_context)
            if assistant_turns == 0:
                return json.dumps({"type": "read_file", "path": "world_model.py"})
            if assistant_turns == 1:
                return json.dumps({
                    "type": "read_file", "path": "evidence.json", "start": 0, "count": 2,
                })
            current = json.loads(user_message)
            latest = current["recent_observations"][-1]
            first = current["current_verifier_state"]["first_divergence"]
            mode = first["state"]["mode"] if first else "uncertain"
            if mode == "boost":
                return json.dumps({
                    "type": "write_file", "path": "world_model.py", "content": _boost_source(),
                })
            if mode == "reverse":
                source = REVERSE_SOURCE if latest.get("kind") == "verifier" else WRONG_SAFE_SOURCE
                return json.dumps({
                    "type": "write_file", "path": "world_model.py", "content": source,
                })
            if mode == "clamped":
                return json.dumps({
                    "type": "write_file", "path": "world_model.py", "content": _clamped_source(),
                })
            return json.dumps({
                "type": "unresolved",
                "notes": (
                    "Positive deltas fit multiple rules; observe a negative delta or a "
                    "toggle followed by step."
                ),
            })

        def summarize_hot_draft(self, old_summary, moved_turns):
            raise AssertionError

    result = run_registered_campaign(
        campaign,
        model=StatefulRuleModel(),
        output_path=tmp_path / "w3-result.json",
    )

    assert result["single_variable"] == W3_SINGLE_VARIABLE
    assert result["verdict"] == "W3_PASS"
    assert result["summary"] == {
        "structural_invariants_pass": True,
        "episode_freshness_pass": True,
        "continuity_pass": True,
        "context_bound_pass": True,
        "hidden_isolation_pass": True,
        "resolvable_records_with_revision": 3,
        "semantic_useful_count": 2,
        "reverse_post_edit_verifier": True,
        "uncertainty_preserved": True,
        "provider_failed": False,
    }
    resolvable = result["records"][:3]
    assert all(record["metrics"]["first_semantic_revision_turn"] == 3 for record in resolvable)
    assert resolvable[1]["metrics"]["second_semantic_revision_turn"] == 4
    assert all(record["hidden_isolated"] is True for record in result["records"])
    assert all(
        turn["history_chars"] <= DEFAULT_BOUNDS.max_context_chars
        for record in result["records"]
        for turn in record["turns"]
    )
    assert result["records"][3]["status"] == "UNRESOLVED"
    assert result["records"][3]["current_changed"] is False
