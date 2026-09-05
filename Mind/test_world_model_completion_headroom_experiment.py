from pathlib import Path
from types import SimpleNamespace

import pytest

from Mind import world_model_latent_state_experiment as w7
from Mind.world_model_completion_headroom_experiment import (
    NATIVE_TOOL_SPECS,
    W8Blocked,
    W8_BUILDER_SYSTEM_PROMPT,
    W8_MAX_TOKENS,
    _OneShotCampaignModel,
    _hidden_trajectories_isolated,
    _teacher_forcing_absent,
    load_registered_w8,
    preflight_registered_w8,
    run_registered_campaign,
    validate_model_source,
    verify_trajectories,
)


MANIFEST_PATH = Path(__file__).parent / "fixtures" / "w8" / "manifest.json"

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
            direction = "reverse" if state["direction"] == "forward" else "forward"
            return {"value": state["value"], "direction": direction}
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


class _ScriptedNativeModel:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.request_count = 0

    def complete(self, messages, *, system_prompt, tools):
        response = self.responses.pop(0)
        self.request_count += 1
        content = response["content"]
        return SimpleNamespace(
            content=content,
            request_body={
                "max_tokens": W8_MAX_TOKENS,
                "messages": messages,
                "system": system_prompt,
                "tools": list(tools),
            },
            response_body={
                "content": content,
                "stop_reason": response.get("stop_reason", "tool_use"),
                "usage": {
                    "input_tokens": response.get("input_tokens", 100),
                    "output_tokens": response.get("output_tokens", 100),
                },
            },
        )


def _response(
    call_id: str,
    name: str,
    arguments: dict[str, object],
    *,
    output_tokens: int = 100,
    stop_reason: str = "tool_use",
    text: str = "",
) -> dict[str, object]:
    content: list[dict[str, object]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.append({
        "type": "tool_use",
        "id": call_id,
        "name": name,
        "input": arguments,
    })
    return {
        "content": content,
        "output_tokens": output_tokens,
        "stop_reason": stop_reason,
    }


def _passing_model() -> _ScriptedNativeModel:
    return _ScriptedNativeModel([
        _response(
            "boost-write",
            "write_file",
            {"path": "world_model.py", "content": BOOST_SOURCE},
        ),
        _response(
            "reverse-write",
            "write_file",
            {"path": "world_model.py", "content": REVERSE_SOURCE},
        ),
        _response(
            "clamp-write",
            "write_file",
            {"path": "world_model.py", "content": CLAMP_SOURCE},
            output_tokens=2001,
            text="The toggle controls a persistent clamp-at-zero mode.",
        ),
        _response(
            "ambiguity-stop",
            "unresolved",
            {"notes": "The public trajectory does not require latent state."},
        ),
    ])


def test_w8_max_tokens_is_2400_while_w7_remains_1600():
    campaign = load_registered_w8(MANIFEST_PATH)

    assert W8_MAX_TOKENS == 2400
    assert campaign.model_config["max_tokens"] == 2400
    assert campaign.w7_campaign.model_config["max_tokens"] == 1600


def test_only_provider_max_tokens_differs_from_w7():
    campaign = load_registered_w8(MANIFEST_PATH)
    differences = {
        key: (campaign.w7_campaign.model_config.get(key), value)
        for key, value in campaign.model_config.items()
        if campaign.w7_campaign.model_config.get(key) != value
    }

    assert differences == {"max_tokens": (1600, 2400)}


def test_w8_prompt_tools_fixtures_and_bounds_equal_w7():
    campaign = load_registered_w8(MANIFEST_PATH)

    assert W8_BUILDER_SYSTEM_PROMPT == w7.W7_BUILDER_SYSTEM_PROMPT
    assert NATIVE_TOOL_SPECS == w7.w5.NATIVE_TOOL_SPECS
    assert campaign.prompt_sha256 == campaign.w7_campaign.prompt_sha256
    assert campaign.tools_sha256 == campaign.w7_campaign.tools_sha256
    assert campaign.fixture_sha256 == campaign.w7_campaign.fixture_sha256
    assert campaign.bounds == campaign.w7_campaign.bounds


def test_w8_turn_tool_and_context_budgets_remain_frozen():
    bounds = load_registered_w8(MANIFEST_PATH).bounds

    assert bounds.max_model_turns == 8
    assert bounds.max_tool_calls == 8
    assert bounds.max_context_chars == 16000
    assert bounds.max_builder_output_chars == 6000
    assert bounds.max_model_source_chars == 4000


def test_w8_source_verifier_and_safety_guards_are_the_w7_symbols():
    assert validate_model_source is w7.validate_model_source
    assert verify_trajectories is w7.verify_trajectories
    assert _teacher_forcing_absent is w7._teacher_forcing_absent
    assert _hidden_trajectories_isolated is w7._hidden_trajectories_isolated


def test_w8_ambiguity_contract_is_the_w7_contract():
    campaign = load_registered_w8(MANIFEST_PATH)

    assert campaign.ambiguity is campaign.w7_campaign.ambiguity
    assert campaign.ambiguity.record_ref == "insufficient-latent-evidence"
    assert campaign.ambiguity.hidden_trajectories == ()


def test_max_tokens_response_with_complete_write_can_continue(tmp_path):
    campaign = load_registered_w8(MANIFEST_PATH)
    model = _passing_model()

    result = run_registered_campaign(
        campaign,
        model=model,
        output_path=tmp_path / "w8-result.json",
    )

    clamp = result["records"][2]
    completion = clamp["turns"][0]["completion"]
    assert result["verdict"] == "W8_PASS"
    assert model.request_count == 4
    assert completion["output_tokens"] == 2001
    assert completion["max_tokens"] == 2400
    assert completion["stop_reason"] == "tool_use"
    assert completion["assistant_text_chars"] > 0
    assert completion["tool_use_count"] == 1
    assert completion["tool_name"] == "write_file"
    assert completion["tool_arguments_present"] is True
    assert completion["tool_arguments_complete"] is True
    assert result["summary"]["max_output_tokens_observed"] == 2001
    assert result["summary"]["max_tokens_stop_count"] == 0
    assert result["summary"]["truncated_tool_call_count"] == 0
    assert result["summary"]["empty_tool_input_count"] == 0
    assert result["summary"]["valid_write_after_long_reasoning_count"] == 1


def test_truncated_empty_tool_input_fails_closed(tmp_path):
    campaign = load_registered_w8(MANIFEST_PATH)
    model = _passing_model()
    model.responses[0] = _response(
        "truncated-write",
        "write_file",
        {},
        output_tokens=2400,
        stop_reason="max_tokens",
        text="A candidate was forming but the tool input was truncated.",
    )

    result = run_registered_campaign(
        campaign,
        model=model,
        output_path=tmp_path / "truncated-result.json",
    )

    first = result["records"][0]
    completion = first["turns"][0]["completion"]
    assert result["verdict"] == "W8_INCONCLUSIVE"
    assert first["current_changed"] is False
    assert first["semantic_revisions"] == 0
    assert completion["tool_arguments_present"] is False
    assert completion["tool_arguments_complete"] is False
    assert result["summary"]["max_tokens_stop_count"] == 1
    assert result["summary"]["truncated_tool_call_count"] == 1
    assert result["summary"]["empty_tool_input_count"] == 1


def test_preflight_zero_provider_calls_does_not_consume_campaign(
    tmp_path,
    monkeypatch,
):
    campaign = load_registered_w8(MANIFEST_PATH)
    calls = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present-for-read-only-preflight")
    monkeypatch.setattr(
        "Mind.world_model_completion_headroom_experiment.DeepSeekAnthropicNativeToolClient",
        lambda **kwargs: calls.append(kwargs),
    )
    output = tmp_path / "not-created.json"
    reservation = tmp_path / "campaign-started.json"

    report = preflight_registered_w8(
        campaign,
        output_path=output,
        reservation_path=reservation,
    )

    assert report == {
        "campaign_consumed": False,
        "provider_request_count": 0,
        "ready": True,
    }
    assert calls == []
    assert not output.exists()
    assert not reservation.exists()


def test_first_request_reservation_survives_crash_and_blocks_restart(tmp_path):
    campaign = load_registered_w8(MANIFEST_PATH)
    reservation = tmp_path / "campaign-started.json"

    class CrashingDelegate:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, *, system_prompt, tools):
            self.calls += 1
            assert reservation.exists()
            raise KeyboardInterrupt

    first = CrashingDelegate()
    with pytest.raises(KeyboardInterrupt):
        _OneShotCampaignModel(first, campaign, reservation).complete(
            [],
            system_prompt="frozen",
            tools=(),
        )

    second = CrashingDelegate()
    with pytest.raises(W8Blocked, match="campaign_already_consumed"):
        _OneShotCampaignModel(second, campaign, reservation).complete(
            [],
            system_prompt="frozen",
            tools=(),
        )

    assert first.calls == 1
    assert second.calls == 0


def test_provider_failure_turn_still_records_frozen_max_tokens(tmp_path):
    campaign = load_registered_w8(MANIFEST_PATH)

    class FailingProvider:
        def complete(self, messages, *, system_prompt, tools):
            raise RuntimeError("provider unavailable")

    result = run_registered_campaign(
        campaign,
        model=FailingProvider(),
        output_path=tmp_path / "provider-failure.json",
    )

    completion = result["records"][0]["turns"][0]["completion"]
    assert result["verdict"] == "W8_INCONCLUSIVE"
    assert result["summary"]["provider_failed"] is True
    assert completion["max_tokens"] == 2400
    assert completion["input_tokens"] is None
    assert completion["output_tokens"] is None


def test_registered_campaign_does_not_overwrite_result(tmp_path):
    campaign = load_registered_w8(MANIFEST_PATH)
    output = tmp_path / "w8-result.json"
    output.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        run_registered_campaign(campaign, model=_passing_model(), output_path=output)

    assert output.read_text(encoding="utf-8") == "preserve"
