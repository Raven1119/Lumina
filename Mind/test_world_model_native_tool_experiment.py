from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from Mind.world_model_builder_experiment import (
    DynamicsAction,
    DynamicsObservation,
    DynamicsState,
    RevisionEvidence,
    WorldModelRevisionRequest,
)
from Mind.world_model_native_tool_experiment import (
    NATIVE_TOOL_SPECS,
    DeepSeekAnthropicNativeToolClient,
    NativeWorldModelRevisionBuilder,
    W4_BUILDER_SYSTEM_PROMPT,
    W4_SINGLE_VARIABLE,
    _canonical_predict_changed,
    load_registered_w4,
    run_native_revision_episode,
    run_registered_campaign,
)
from Mind.world_model_revision_experiment import (
    BUILDER_SYSTEM_PROMPT,
    DEFAULT_BOUNDS,
    EpisodeStatus,
    RevisionBounds,
)


BASE_SOURCE = '''class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "reverse" if state["mode"] == "forward" else "forward"}
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

CLAMPED_MAX_SOURCE = BASE_SOURCE.replace(
    'state["value"] + action["delta"]',
    'max(0, state["value"] + action["delta"])',
)

BOOST_SOURCE = '''class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "boost" if state["mode"] == "normal" else "normal"}
        if state["mode"] == "boost":
            return {"value": state["value"] + action["delta"] * 2, "mode": state["mode"]}
        return {"value": state["value"] + action["delta"], "mode": state["mode"]}
'''

CLAMPED_SOURCE = '''class CanonicalWorldModel:
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


def _request() -> WorldModelRevisionRequest:
    return WorldModelRevisionRequest(
        current_model_version="wm-v1",
        current_model_source=BASE_SOURCE,
        evidence=(
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
        ),
        revision_reason="Repair objective predictive dynamics.",
    )


def _paths(tmp_path: Path):
    current = tmp_path / "current" / "world_model.py"
    working = tmp_path / "working" / "world_model.py"
    notes = tmp_path / "working" / "notes" / "world_model.md"
    current.parent.mkdir(parents=True)
    current.write_text(BASE_SOURCE, encoding="utf-8")
    return current, working, notes


def _response(call_id: str, name: str, arguments: dict[str, object], text: str = ""):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    content.append({
        "type": "tool_use",
        "id": call_id,
        "name": name,
        "input": arguments,
    })
    return {"content": content, "stop_reason": "tool_use"}


class ScriptedTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def __call__(self, body: dict[str, object]) -> object:
        self.requests.append(body)
        return self.responses.pop(0)


def _run(
    tmp_path: Path,
    responses: list[dict[str, object]],
    bounds: RevisionBounds = DEFAULT_BOUNDS,
):
    transport = ScriptedTransport(responses)
    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=transport,
    )
    current, working, notes = _paths(tmp_path)
    builder = NativeWorldModelRevisionBuilder(client, bounds)
    result = run_native_revision_episode(
        builder=builder,
        revision=_request(),
        episode_ref="w4-test",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )
    return result, transport, current, builder


def test_native_read_file_tool_dispatch(tmp_path):
    assert [tool["name"] for tool in NATIVE_TOOL_SPECS] == [
        "read_file",
        "run_python",
        "write_file",
        "unresolved",
    ]
    result, transport, _, _ = _run(tmp_path, [
        _response("call-read", "read_file", {"path": "world_model.py"}),
        _response("call-end", "unresolved", {"notes": "Need another observation."}),
    ])

    assert result.status is EpisodeStatus.UNRESOLVED
    second_messages = transport.requests[1]["messages"]
    tool_result = second_messages[-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert json.loads(tool_result["content"][0]["text"])["recent_observations"][-1] == {
        "content": BASE_SOURCE,
        "kind": "file",
        "path": "world_model.py",
    }


def test_tool_call_id_pairing(tmp_path):
    result, transport, _, _ = _run(tmp_path, [
        _response("provider-call-123", "read_file", {"path": "world_model.py"}),
        _response("provider-call-456", "unresolved", {"notes": "Still ambiguous."}),
    ])

    assert result.status is EpisodeStatus.UNRESOLVED
    assistant = transport.requests[1]["messages"][-2]
    tool_result = transport.requests[1]["messages"][-1]["content"][0]
    assert assistant["content"][-1]["id"] == "provider-call-123"
    assert tool_result["tool_use_id"] == "provider-call-123"


def test_text_plus_unresolved_tool_is_valid(tmp_path):
    result, transport, current, _ = _run(tmp_path, [
        _response(
            "call-unresolved",
            "unresolved",
            {"notes": "Toggle behavior is not identified by step-only evidence."},
            text="The supplied observations do not identify the toggle branch.",
        ),
    ])

    assert result.status is EpisodeStatus.UNRESOLVED
    assert current.read_text(encoding="utf-8") == BASE_SOURCE
    assert transport.requests[0]["tools"] == list(NATIVE_TOOL_SPECS)


def test_native_write_file_tool_dispatch(tmp_path):
    result, transport, _, _ = _run(tmp_path, [
        _response(
            "call-notes",
            "write_file",
            {"path": "notes/world_model.md", "content": "Need more evidence."},
        ),
        _response("call-end", "unresolved", {"notes": "Need more evidence."}),
    ])

    assert result.status is EpisodeStatus.UNRESOLVED
    context = json.loads(
        transport.requests[1]["messages"][-1]["content"][0]["content"][0]["text"]
    )
    assert context["recent_observations"][-1] == {
        "bytes": 19,
        "kind": "write",
        "path": "notes/world_model.md",
    }


def test_native_unresolved_terminal(tmp_path):
    result, _, current, builder = _run(tmp_path, [
        _response(
            "call-unresolved",
            "unresolved",
            {"notes": "Two dynamics remain observationally equivalent."},
        ),
    ])

    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.tool_calls == 0
    assert current.read_text(encoding="utf-8") == BASE_SOURCE
    assert builder.turns[0]["tool_name"] == "unresolved"


def test_tool_result_visible_next_turn(tmp_path):
    _, transport, _, builder = _run(tmp_path, [
        _response("call-read", "read_file", {"path": "world_model.py"}),
        _response("call-end", "unresolved", {"notes": "Need more evidence."}),
    ])

    assert builder.turns[1]["tool_result_visible"] is True
    assert transport.requests[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_evidence_count_schema_bound(tmp_path):
    read_schema = NATIVE_TOOL_SPECS[0]["input_schema"]
    assert read_schema["properties"]["count"]["maximum"] == 2
    result, _, current, builder = _run(tmp_path, [
        _response(
            "call-too-wide",
            "read_file",
            {"path": "evidence.json", "start": 0, "count": 3},
        ),
    ])

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "invalid_model_action"
    assert builder.turns[0]["schema_valid"] is False
    assert builder.turns[0]["host_valid"] is False
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_schema_valid_and_host_valid_are_separate(tmp_path):
    result, _, current, builder = _run(tmp_path, [
        _response(
            "call-missing-range",
            "read_file",
            {"path": "evidence.json"},
        ),
    ])

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "invalid_model_action"
    assert builder.turns[0]["schema_valid"] is True
    assert builder.turns[0]["host_valid"] is False
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_invalid_tool_args_fail_closed(tmp_path):
    result, _, current, builder = _run(tmp_path, [
        _response("call-escape", "read_file", {"path": "../secret.txt"}),
    ])

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "invalid_model_action"
    assert builder.turns[0]["host_valid"] is False
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_hidden_never_enters_tool_result(tmp_path):
    marker = "HIDDEN_HOLDOUT_MARKER"
    campaign = load_registered_w4(
        Path(__file__).parent / "fixtures" / "w4" / "manifest.json"
    )
    w2_campaign = campaign.w3_campaign.w2_campaign
    hidden = RevisionEvidence(
        state=DynamicsState(111, marker),
        action=DynamicsAction("step", 1),
        expected=DynamicsObservation(112, marker),
        observed=DynamicsObservation(112, marker),
        result="MATCHED",
    )
    first = replace(w2_campaign.resolvable[0], hidden_evidence=(hidden,))
    w2_campaign = replace(
        w2_campaign,
        resolvable=(first, *w2_campaign.resolvable[1:]),
    )
    campaign = replace(
        campaign,
        w3_campaign=replace(campaign.w3_campaign, w2_campaign=w2_campaign),
    )
    transport = ScriptedTransport([
        _response(f"call-hidden-{index}", "unresolved", {"notes": "No revision."})
        for index in range(4)
    ])
    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=transport,
    )
    result = run_registered_campaign(
        campaign,
        model=client,
        output_path=tmp_path / "hidden-isolation-result.json",
    )

    assert marker not in json.dumps(transport.requests, ensure_ascii=False)
    assert result["records"][0]["hidden_isolated"] is True


def test_native_world_model_write_still_auto_verifies(tmp_path):
    result, _, current, builder = _run(tmp_path, [
        _response(
            "call-write",
            "write_file",
            {"path": "world_model.py", "content": REVERSE_SOURCE},
        ),
    ])

    assert result.status is EpisodeStatus.CONSISTENT_ENOUGH
    assert result.semantic_revisions == 1
    assert result.final_public_accuracy == 1.0
    assert current.read_text(encoding="utf-8") == REVERSE_SOURCE
    assert builder.turns[0]["host_valid"] is True
    assert any(
        event["event_type"] == "WORLD_MODEL_WORKING_REVISION_VERIFIED"
        for event in result.events
    )


def test_ast_contract_unchanged(tmp_path):
    one_turn = RevisionBounds(**(
        DEFAULT_BOUNDS.__dict__ | {"max_model_turns": 1, "max_tool_calls": 1}
    ))
    result, _, current, builder = _run(tmp_path, [
        _response(
            "call-max",
            "write_file",
            {"path": "world_model.py", "content": CLAMPED_MAX_SOURCE},
        ),
    ], one_turn)

    assert builder.turns[0]["schema_valid"] is True
    assert builder.turns[0]["host_valid"] is True
    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "working_source_invalid_at_bound"
    assert result.semantic_revisions == 0
    assert current.read_text(encoding="utf-8") == BASE_SOURCE
    assert _canonical_predict_changed(CLAMPED_MAX_SOURCE, BASE_SOURCE, "wm-v1") is True


def test_episode_freshness_preserved(tmp_path):
    marker = "EPISODE_A_PRIVATE_MARKER"
    transport = ScriptedTransport([
        _response("call-a-read", "read_file", {"path": "world_model.py"}, text=marker),
        _response("call-a-end", "unresolved", {"notes": marker}),
        _response("call-b-end", "unresolved", {"notes": "Episode B is fresh."}),
    ])
    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=transport,
    )
    builder = NativeWorldModelRevisionBuilder(client, DEFAULT_BOUNDS)
    current, working, notes = _paths(tmp_path)
    for episode_ref in ("episode-a", "episode-b"):
        result = run_native_revision_episode(
            builder=builder,
            revision=_request(),
            episode_ref=episode_ref,
            current_path=current,
            working_path=working,
            notes_path=notes,
        )
        assert result.status is EpisodeStatus.UNRESOLVED

    episode_b_request = transport.requests[2]
    assert len(episode_b_request["messages"]) == 1
    assert marker not in json.dumps(episode_b_request, ensure_ascii=False)


def test_prompt_changes_only_the_protocol_instruction():
    assert "Return exactly one JSON object" in BUILDER_SYSTEM_PROMPT
    assert "Return exactly one JSON object" not in W4_BUILDER_SYSTEM_PROMPT
    assert "Use the provided tools" in W4_BUILDER_SYSTEM_PROMPT
    frozen_tail = (
        "world_model.py must retain exactly class CanonicalWorldModel, its existing "
        "version string, and predict(self, state, action)."
    )
    assert frozen_tail in BUILDER_SYSTEM_PROMPT
    assert frozen_tail in W4_BUILDER_SYSTEM_PROMPT
    for doctrine in (
        "Reality Evidence is authoritative",
        "Prefer one compact shared mechanic",
        "Fix the first meaningful divergence first",
        "Use unresolved",
        "You cannot act in an environment",
    ):
        assert doctrine in W4_BUILDER_SYSTEM_PROMPT


def test_context_cap_remains_16000(tmp_path):
    assert DEFAULT_BOUNDS.max_context_chars == 16_000
    actions = [
        _response(f"call-{index}", "read_file", {"path": "world_model.py"})
        for index in range(8)
    ]
    result, _, current, builder = _run(tmp_path, actions)

    assert result.status is EpisodeStatus.BUDGET_EXHAUSTED
    assert current.read_text(encoding="utf-8") == BASE_SOURCE
    assert len(builder.turns) == 8
    assert max(turn["history_chars"] for turn in builder.turns) <= 16_000


def test_native_builder_has_no_execution_authority():
    forbidden = {
        "execute",
        "take_action",
        "spawn_child",
        "shell",
        "browser",
        "ipython",
        "set_intention",
    }
    assert forbidden.isdisjoint(dir(NativeWorldModelRevisionBuilder))
    assert [tool["name"] for tool in NATIVE_TOOL_SPECS] == [
        "read_file",
        "run_python",
        "write_file",
        "unresolved",
    ]


def test_registered_campaign_uses_native_protocol_and_records_metrics(tmp_path):
    campaign = load_registered_w4(
        Path(__file__).parent / "fixtures" / "w4" / "manifest.json"
    )

    class RuleTransport:
        def __init__(self) -> None:
            self.call_index = 0

        def __call__(self, body):
            self.call_index += 1
            messages = body["messages"]
            assistant_turns = sum(message["role"] == "assistant" for message in messages)
            call_id = f"native-{self.call_index}"
            if assistant_turns == 0:
                return _response(call_id, "read_file", {"path": "world_model.py"})
            if assistant_turns == 1:
                return _response(
                    call_id,
                    "read_file",
                    {"path": "evidence.json", "start": 0, "count": 2},
                )
            block = messages[-1]["content"][0]
            context = json.loads(block["content"][0]["text"])
            first = context["current_verifier_state"]["first_divergence"]
            if first is None:
                return _response(
                    call_id,
                    "unresolved",
                    {"notes": "Step-only evidence leaves toggle dynamics unresolved."},
                    text="The observed steps do not identify toggle behavior.",
                )
            source = {
                "boost": BOOST_SOURCE,
                "reverse": REVERSE_SOURCE,
                "clamped": CLAMPED_SOURCE,
            }[first["state"]["mode"]]
            return _response(
                call_id,
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
        transport=RuleTransport(),
    )
    result = run_registered_campaign(
        campaign,
        model=client,
        output_path=tmp_path / "w4-result.json",
    )

    assert result["single_variable"] == W4_SINGLE_VARIABLE
    assert result["verdict"] == "W4_PASS"
    assert result["summary"]["resolvable_native_world_model_writes"] == 3
    assert result["summary"]["accepted_revision_count"] == 3
    assert result["summary"]["reverse_post_edit_verifier"] is True
    assert result["summary"]["ambiguity_native_unresolved"] is True
    assert result["summary"]["invalid_action_envelope_count"] == 0
    assert result["summary"]["hidden_isolation_pass"] is True
    assert all(
        record["metrics"]["first_accepted_semantic_revision_turn"] == 3
        for record in result["records"][:3]
    )
    assert all(
        next(
            turn["source_proposal_predict_body_changed"]
            for turn in record["turns"]
            if turn["tool_name"] == "write_file"
            and turn["tool_arguments"]["path"] == "world_model.py"
        ) is True
        for record in result["records"][:3]
    )
    assert all(
        next(
            turn["source_proposal_semantic_review_required"]
            for turn in record["turns"]
            if turn["tool_name"] == "write_file"
            and turn["tool_arguments"]["path"] == "world_model.py"
        ) is True
        for record in result["records"][:3]
    )
    assert result["records"][3]["status"] == "UNRESOLVED"
    assert all(
        turn["provider_request"]["tools"] == list(NATIVE_TOOL_SPECS)
        for record in result["records"]
        for turn in record["turns"]
    )
    assert all(
        any(block["type"] == "tool_use" for block in turn["provider_response"]["content"])
        for record in result["records"]
        for turn in record["turns"]
    )
