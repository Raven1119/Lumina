from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from Mind.world_model_builder_experiment import (
    DynamicsAction,
    DynamicsObservation,
    DynamicsState,
    RevisionEvidence,
    WorldModelRevisionRequest,
    _validate_model_source as validate_w1_model_source,
)
from Mind.world_model_native_tool_experiment import (
    DeepSeekAnthropicNativeToolClient,
)
from Mind.world_model_representation_experiment import (
    BoundedRepresentationBuilder,
    W6_BUILDER_SYSTEM_PROMPT,
    evaluate_model_source,
    load_registered_w6,
    run_representation_revision_episode,
    run_registered_campaign,
    validate_model_source,
)
from Mind import world_model_read_batch_experiment as w5
from Mind import world_model_representation_experiment as w6
from Mind.world_model_native_tool_experiment import NATIVE_TOOL_SPECS
from Mind.world_model_revision_experiment import DEFAULT_BOUNDS, EpisodeStatus


def _source(body: str) -> str:
    return (
        'class CanonicalWorldModel:\n'
        '    version = "wm-v1"\n\n'
        '    def predict(self, state, action):\n'
        + textwrap.indent(textwrap.dedent(body).strip(), "        ")
        + "\n"
    )


def test_local_assignment_is_allowed():
    validate_model_source(
        _source(
            '''
            factor = 2 if state["mode"] == "boost" else 1
            return {
                "value": state["value"] + action["delta"] * factor,
                "mode": state["mode"],
            }
            '''
        ),
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    )


@pytest.mark.parametrize(
    "body",
    [
        '''
        value = state["value"] + action["delta"]
        if state["mode"] == "clamped":
            value = max(0, value)
        return {"value": value, "mode": state["mode"]}
        ''',
        '''
        value = state["value"] + action["delta"]
        if state["mode"] == "clamped":
            if value < 0:
                value = 0
        return {"value": value, "mode": state["mode"]}
        ''',
    ],
)
def test_if_statement_is_allowed(body):
    validate_model_source(
        _source(body),
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    )


def test_if_expression_is_allowed():
    validate_model_source(
        _source(
            '''
            factor = 2 if state["mode"] == "boost" else 1
            return {"value": factor, "mode": state["mode"]}
            '''
        ),
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    )


def test_boolean_expression_is_allowed():
    validate_model_source(
        _source(
            '''
            should_clamp = state["mode"] == "clamped" and action["kind"] == "step"
            value = state["value"] + action["delta"]
            if should_clamp and value < 0:
                value = 0
            return {"value": value, "mode": state["mode"]}
            '''
        ),
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    )


def _validate_pure_expression(expression: str) -> None:
    validate_model_source(
        _source(
            f'''
            value = state["value"] + action["delta"]
            value = {expression}
            return {{"value": value, "mode": state["mode"]}}
            '''
        ),
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    )


def test_pure_max_is_allowed():
    _validate_pure_expression("max(0, value)")


def test_pure_min_is_allowed_if_authorized():
    _validate_pure_expression("min(0, value)")


def test_pure_abs_is_allowed():
    _validate_pure_expression("abs(value)")


def _assert_source_rejected(body: str) -> None:
    with pytest.raises(ValueError):
        validate_model_source(
            _source(body),
            class_name="CanonicalWorldModel",
            expected_version="wm-v1",
        )


def test_import_is_rejected():
    _assert_source_rejected('import os\nreturn {"value": 0, "mode": "x"}')


def test_open_is_rejected():
    _assert_source_rejected(
        'return {"value": open("x", "w"), "mode": "x"}'
    )


def test_dunder_access_is_rejected():
    _assert_source_rejected(
        'return {"value": state.__class__, "mode": "x"}'
    )


def test_arbitrary_call_is_rejected():
    _assert_source_rejected(
        'return {"value": sum((1, 2)), "mode": "x"}'
    )


def test_input_mutation_is_rejected():
    _assert_source_rejected(
        'state["value"] = 123\nreturn {"value": 0, "mode": "x"}'
    )


@pytest.mark.parametrize(
    "body",
    [
        "return {\"value\": __import__(\"os\"), \"mode\": \"x\"}",
        "return {\"value\": getattr(state, \"__class__\"), \"mode\": \"x\"}",
        "exec(\"pass\")\nreturn {\"value\": 0, \"mode\": \"x\"}",
        "for item in state:\n    pass\nreturn {\"value\": 0, \"mode\": \"x\"}",
        "value = [item for item in state]\nreturn {\"value\": value, \"mode\": \"x\"}",
    ],
)
def test_dangerous_or_out_of_scope_source_is_rejected(body):
    _assert_source_rejected(body)


def test_attribute_assignment_is_rejected():
    with pytest.raises(ValueError, match="assignment target"):
        validate_model_source(
            _source(
                '''
                self.value = 3
                return {"value": 0, "mode": "x"}
                '''
            ),
            class_name="CanonicalWorldModel",
            expected_version="wm-v1",
        )


def test_extra_class_is_rejected():
    source = _source('return {"value": 0, "mode": "x"}') + "\nclass Escape:\n    pass\n"
    with pytest.raises(ValueError, match="exactly one class"):
        validate_model_source(
            source,
            class_name="CanonicalWorldModel",
            expected_version="wm-v1",
        )


def test_extra_function_is_rejected():
    source = _source('return {"value": 0, "mode": "x"}').replace(
        "    def predict(self, state, action):\n",
        "    def helper(self):\n        return 1\n\n    def predict(self, state, action):\n",
    )
    with pytest.raises(ValueError, match="only version and predict"):
        validate_model_source(
            source,
            class_name="CanonicalWorldModel",
            expected_version="wm-v1",
        )


def test_class_type_parameters_are_rejected():
    source = _source('return {"value": 0, "mode": "x"}').replace(
        "class CanonicalWorldModel:",
        "class CanonicalWorldModel[T]:",
    )
    with pytest.raises(ValueError, match="class contract"):
        validate_model_source(
            source,
            class_name="CanonicalWorldModel",
            expected_version="wm-v1",
        )


def test_function_type_parameters_are_rejected():
    source = _source('return {"value": 0, "mode": "x"}').replace(
        "def predict(self, state, action):",
        "def predict[T](self, state, action):",
    )
    with pytest.raises(ValueError, match="predict signature"):
        validate_model_source(
            source,
            class_name="CanonicalWorldModel",
            expected_version="wm-v1",
        )


BOOST_W5_SOURCE = _source(
    '''
    if action["kind"] == "toggle":
        return {"value": state["value"], "mode": "boost" if state["mode"] == "normal" else "normal"}
    factor = 2 if state["mode"] == "boost" else 1
    return {"value": state["value"] + action["delta"] * factor, "mode": state["mode"]}
    '''
)

CLAMPED_W5_SOURCE = _source(
    '''
    if action["kind"] == "toggle":
        return {"value": state["value"], "mode": "clamped" if state["mode"] == "free" else "free"}
    value = state["value"] + action["delta"]
    if state["mode"] == "clamped" and value < 0:
        value = 0
    return {"value": value, "mode": state["mode"]}
    '''
)

REVERSE_W5_SOURCE = _source(
    '''
    if action["kind"] == "toggle":
        return {"value": state["value"], "mode": "reverse" if state["mode"] == "forward" else "forward"}
    if state["mode"] == "reverse":
        return {"value": state["value"] - action["delta"], "mode": state["mode"]}
    return {"value": state["value"] + action["delta"], "mode": state["mode"]}
    '''
)


def test_w5_boost_source_moves_from_w1_reject_to_w6_accept():
    with pytest.raises(ValueError, match="statement is not permitted"):
        validate_w1_model_source(
            BOOST_W5_SOURCE,
            class_name="CanonicalWorldModel",
            expected_version="wm-v1",
        )
    validate_model_source(
        BOOST_W5_SOURCE,
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    )


def test_w5_clamped_source_moves_from_w1_reject_to_w6_accept():
    with pytest.raises(ValueError, match="statement is not permitted"):
        validate_w1_model_source(
            CLAMPED_W5_SOURCE,
            class_name="CanonicalWorldModel",
            expected_version="wm-v1",
        )
    validate_model_source(
        CLAMPED_W5_SOURCE,
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    )


def test_reverse_w5_source_still_valid():
    validate_w1_model_source(
        REVERSE_W5_SOURCE,
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    )
    validate_model_source(
        REVERSE_W5_SOURCE,
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    )


def test_boost_style_general_rule_can_execute():
    evidence = (
        RevisionEvidence(
            state=DynamicsState(10, "boost"),
            action=DynamicsAction("step", -3),
            expected=DynamicsObservation(7, "boost"),
            observed=DynamicsObservation(4, "boost"),
            result="ERROR",
        ),
    )
    assert evaluate_model_source(
        BOOST_W5_SOURCE,
        evidence,
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    ) == [{"value": 4, "mode": "boost"}]


def test_clamped_style_general_rule_can_execute():
    evidence = (
        RevisionEvidence(
            state=DynamicsState(2, "clamped"),
            action=DynamicsAction("step", -5),
            expected=DynamicsObservation(-3, "clamped"),
            observed=DynamicsObservation(0, "clamped"),
            result="ERROR",
        ),
    )
    assert evaluate_model_source(
        CLAMPED_W5_SOURCE,
        evidence,
        class_name="CanonicalWorldModel",
        expected_version="wm-v1",
    ) == [{"value": 0, "mode": "clamped"}]


def test_evaluator_rejects_unbounded_input_before_execution():
    evidence = (
        RevisionEvidence(
            state=DynamicsState(1_000_001, "normal"),
            action=DynamicsAction("step", 1),
            expected=DynamicsObservation(1_000_002, "normal"),
            observed=DynamicsObservation(1_000_002, "normal"),
            result="MATCHED",
        ),
    )
    with pytest.raises(ValueError, match="evidence exceeds bounds"):
        evaluate_model_source(
            BASE_SOURCE,
            evidence,
            class_name="CanonicalWorldModel",
            expected_version="wm-v1",
        )


BASE_SOURCE = _source(
    '''
    if action["kind"] == "toggle":
        return {"value": state["value"], "mode": "boost" if state["mode"] == "normal" else "normal"}
    return {"value": state["value"] + action["delta"], "mode": state["mode"]}
    '''
)


def _boost_request() -> WorldModelRevisionRequest:
    return WorldModelRevisionRequest(
        current_model_version="wm-v1",
        current_model_source=BASE_SOURCE,
        evidence=(
            RevisionEvidence(
                state=DynamicsState(4, "normal"),
                action=DynamicsAction("step", 2),
                expected=DynamicsObservation(6, "normal"),
                observed=DynamicsObservation(6, "normal"),
                result="MATCHED",
            ),
            RevisionEvidence(
                state=DynamicsState(4, "boost"),
                action=DynamicsAction("step", 2),
                expected=DynamicsObservation(6, "boost"),
                observed=DynamicsObservation(8, "boost"),
                result="ERROR",
            ),
        ),
        revision_reason="Repair objective predictive dynamics.",
    )


def _response(call_id: str, name: str, arguments: dict[str, object]):
    return {
        "content": [
            {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": arguments,
            }
        ],
        "stop_reason": "tool_use",
    }


class _ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, body):
        self.requests.append(body)
        return self.responses.pop(0)


def _run_episode(tmp_path: Path, responses):
    transport = _ScriptedTransport(responses)
    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=transport,
    )
    current = tmp_path / "current" / "world_model.py"
    working = tmp_path / "working" / "world_model.py"
    notes = tmp_path / "working" / "notes" / "world_model.md"
    current.parent.mkdir(parents=True)
    current.write_text(BASE_SOURCE, encoding="utf-8")
    result = run_representation_revision_episode(
        builder=BoundedRepresentationBuilder(client, DEFAULT_BOUNDS),
        revision=_boost_request(),
        episode_ref="w6-test",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )
    return result, current, transport


def test_invalid_source_never_reaches_current(tmp_path):
    invalid = _source(
        '''
        import os
        return {"value": 8, "mode": state["mode"]}
        '''
    )
    result, current, _ = _run_episode(
        tmp_path,
        [
            _response("bad", "write_file", {"path": "world_model.py", "content": invalid}),
            _response("stop", "unresolved", {"notes": "Unsafe proposal rejected."}),
        ],
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.semantic_revisions == 0
    assert current.read_text(encoding="utf-8") == BASE_SOURCE
    assert not any(
        event["event_type"] == "WORLD_MODEL_WORKING_REVISION_VERIFIED"
        for event in result.events
    )


def test_valid_source_still_requires_verifier(tmp_path):
    safe_but_wrong = _source(
        '''
        return {"value": state["value"], "mode": state["mode"]}
        '''
    )
    result, current, _ = _run_episode(
        tmp_path,
        [
            _response(
                "wrong",
                "write_file",
                {"path": "world_model.py", "content": safe_but_wrong},
            ),
            _response("stop", "unresolved", {"notes": "The candidate was falsified."}),
        ],
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.semantic_revisions == 1
    assert current.read_text(encoding="utf-8") == BASE_SOURCE
    verified = [
        event
        for event in result.events
        if event["event_type"] == "WORLD_MODEL_WORKING_REVISION_VERIFIED"
    ]
    assert verified[0]["payload"]["accuracy"] == 0.0


def test_source_valid_and_verified_revision_applies_atomically(tmp_path):
    result, current, _ = _run_episode(
        tmp_path,
        [
            _response(
                "boost",
                "write_file",
                {"path": "world_model.py", "content": BOOST_W5_SOURCE},
            )
        ],
    )

    assert result.status is EpisodeStatus.CONSISTENT_ENOUGH
    assert result.semantic_revisions == 1
    assert result.final_public_accuracy == 1.0
    assert current.read_text(encoding="utf-8") == BOOST_W5_SOURCE


def test_unresolved_does_not_write_model(tmp_path):
    result, current, _ = _run_episode(
        tmp_path,
        [_response("stop", "unresolved", {"notes": "Evidence is insufficient."})],
    )

    assert result.status is EpisodeStatus.UNRESOLVED
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_w6_changes_only_the_prompt_source_contract():
    expected = w5.W5_BUILDER_SYSTEM_PROMPT.replace(
        "The model may use only the tiny executable grammar already present in the current file.",
        "It must remain a bounded pure predictive function under the provided source contract. "
        "Local pure computation is allowed; imports, external I/O, reflection, process/network "
        "access, and mutation of inputs are forbidden.",
    )

    assert W6_BUILDER_SYSTEM_PROMPT == expected
    assert tuple(tool["name"] for tool in NATIVE_TOOL_SPECS) == (
        "read_file",
        "run_python",
        "write_file",
        "unresolved",
    )
    assert "use max" not in W6_BUILDER_SYSTEM_PROMPT.lower()
    assert "boost" not in W6_BUILDER_SYSTEM_PROMPT.lower()
    assert "clamped" not in W6_BUILDER_SYSTEM_PROMPT.lower()


def test_hidden_holdout_remains_isolated_and_campaign_reaches_all_w6_causal_layers(
    tmp_path,
):
    campaign = load_registered_w6(
        Path(__file__).parent / "fixtures" / "w6" / "manifest.json"
    )

    class _RuleTransport:
        def __init__(self):
            self.episode = -1
            self.call_index = 0

        def __call__(self, body):
            self.call_index += 1
            assistant_turns = sum(
                message["role"] == "assistant" for message in body["messages"]
            )
            prefix = f"call-{self.call_index}"
            if assistant_turns == 0:
                self.episode += 1
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"{prefix}-model",
                            "name": "read_file",
                            "input": {"path": "world_model.py"},
                        },
                        {
                            "type": "tool_use",
                            "id": f"{prefix}-notes",
                            "name": "read_file",
                            "input": {"path": "notes/world_model.md"},
                        },
                        {
                            "type": "tool_use",
                            "id": f"{prefix}-evidence",
                            "name": "read_file",
                            "input": {"path": "evidence.json", "start": 0, "count": 2},
                        },
                    ],
                    "stop_reason": "tool_use",
                }
            if self.episode == 3:
                return _response(
                    f"{prefix}-stop",
                    "unresolved",
                    {"notes": "Public evidence does not distinguish signed from absolute delta."},
                )
            source = (BOOST_W5_SOURCE, REVERSE_W5_SOURCE, CLAMPED_W5_SOURCE)[self.episode]
            return _response(
                f"{prefix}-write",
                "write_file",
                {"path": "world_model.py", "content": source},
            )

    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=_RuleTransport(),
    )
    result = run_registered_campaign(
        campaign,
        model=client,
        output_path=tmp_path / "w6-result.json",
    )

    assert result["verdict"] == "W6_PASS"
    assert result["summary"]["source_safety_pass"] is True
    assert result["summary"]["boost_clamped_source_valid_records"] == 2
    assert result["summary"]["resolvable_verifier_records"] == 3
    assert result["summary"]["resolvable_public_perfect"] == 3
    assert result["summary"]["resolvable_hidden_perfect"] == 3
    assert result["summary"]["ambiguity_native_unresolved"] is True
    assert all(record["hidden_isolated"] is True for record in result["records"])


def test_registered_campaign_output_is_one_shot(tmp_path):
    campaign = load_registered_w6(
        Path(__file__).parent / "fixtures" / "w6" / "manifest.json"
    )
    output = tmp_path / "exists.json"
    output.write_text("already present", encoding="utf-8")
    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=_ScriptedTransport([]),
    )

    with pytest.raises(FileExistsError, match="already exists"):
        run_registered_campaign(campaign, model=client, output_path=output)


def test_pairing_violation_is_a_fail_verdict():
    records = [{"evidence_unchanged": True, "current_coherent": True}]
    summary = {
        "authority_isolation_pass": True,
        "context_bound_pass": True,
        "episode_freshness_pass": True,
        "hidden_isolation_pass": True,
        "pairing_integrity_pass": False,
        "provider_failed": False,
        "source_safety_pass": True,
    }

    assert w6._campaign_verdict(records, summary) == "W6_FAIL"


def test_provider_caused_incomplete_pairing_is_inconclusive():
    records = [{"evidence_unchanged": True, "current_coherent": True}]
    summary = {
        "authority_isolation_pass": True,
        "context_bound_pass": True,
        "episode_freshness_pass": True,
        "hidden_isolation_pass": True,
        "pairing_integrity_pass": False,
        "provider_failed": True,
        "source_safety_pass": True,
    }

    assert w6._campaign_verdict(records, summary) == "W6_INCONCLUSIVE"
