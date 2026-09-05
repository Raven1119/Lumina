from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from Mind import world_model_read_batch_experiment as w5
from Mind.world_model_builder_experiment import (
    DynamicsAction,
    DynamicsObservation,
    DynamicsState,
    RevisionEvidence,
    WorldModelRevisionRequest,
)
from Mind.world_model_native_tool_experiment import (
    DeepSeekAnthropicNativeToolClient,
    NATIVE_TOOL_SPECS,
    W4_BUILDER_SYSTEM_PROMPT,
)
from Mind.world_model_read_batch_experiment import (
    BoundedReadBatchBuilder,
    MAX_READ_BATCH,
    W5_BUILDER_SYSTEM_PROMPT,
    load_registered_w5,
    run_read_batch_revision_episode,
    run_registered_campaign,
)
from Mind.world_model_revision_experiment import DEFAULT_BOUNDS, EpisodeStatus


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


def _response(*calls: tuple[str, str, dict[str, object]], text: str = ""):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    content.extend(
        {
            "type": "tool_use",
            "id": call_id,
            "name": name,
            "input": arguments,
        }
        for call_id, name, arguments in calls
    )
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
    bounds=DEFAULT_BOUNDS,
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
    builder = BoundedReadBatchBuilder(client, bounds)
    result = run_read_batch_revision_episode(
        builder=builder,
        revision=_request(),
        episode_ref="w5-test",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )
    return result, transport, current, builder


def test_three_read_tool_uses_are_accepted(tmp_path):
    result, transport, current, builder = _run(tmp_path, [
        _response(
            ("read-model", "read_file", {"path": "world_model.py"}),
            ("read-notes", "read_file", {"path": "notes/world_model.md"}),
            (
                "read-evidence",
                "read_file",
                {"path": "evidence.json", "start": 0, "count": 2},
            ),
            text="Inspect the three bounded resources.",
        ),
        _response(
            (
                "finish",
                "unresolved",
                {"notes": "Another observation is required."},
            ),
        ),
    ])

    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.tool_calls == 3
    assert len(transport.requests) == 2
    assert builder.turns[0]["envelope_kind"] == "multi_read"
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_three_read_results_keep_original_ids(tmp_path):
    _, transport, _, _ = _run(tmp_path, [
        _response(
            ("id-a", "read_file", {"path": "world_model.py"}),
            ("id-b", "read_file", {"path": "notes/world_model.md"}),
            (
                "id-c",
                "read_file",
                {"path": "evidence.json", "start": 0, "count": 2},
            ),
        ),
        _response(
            ("finish", "unresolved", {"notes": "Need more evidence."}),
        ),
    ])

    results = [
        block
        for block in transport.requests[1]["messages"][-1]["content"]
        if block["type"] == "tool_result"
    ]
    assert [block["tool_use_id"] for block in results] == ["id-a", "id-b", "id-c"]
    assert len({block["tool_use_id"] for block in results}) == 3


def test_three_reads_count_as_three_tool_calls(tmp_path):
    result, transport, _, _ = _run(tmp_path, [
        _response(
            ("id-a", "read_file", {"path": "world_model.py"}),
            ("id-b", "read_file", {"path": "notes/world_model.md"}),
            (
                "id-c",
                "read_file",
                {"path": "evidence.json", "start": 0, "count": 2},
            ),
        ),
        _response(("finish", "unresolved", {"notes": "Need more evidence."})),
    ])

    next_context = json.loads(
        transport.requests[1]["messages"][-1]["content"][-1]["text"]
    )
    assert result.tool_calls == 3
    assert next_context["remaining"]["tool_calls"] == 5


def test_two_read_tool_uses_are_accepted(tmp_path):
    result, transport, _, builder = _run(tmp_path, [
        _response(
            ("id-a", "read_file", {"path": "world_model.py"}),
            ("id-b", "read_file", {"path": "notes/world_model.md"}),
        ),
        _response(("finish", "unresolved", {"notes": "Need more evidence."})),
    ])

    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.tool_calls == 2
    assert builder.turns[0]["envelope_kind"] == "multi_read"
    assert builder.turns[1]["tool_result_count"] == 2
    assert len(transport.requests) == 2


def test_duplicate_reads_execute_without_deduplication(tmp_path):
    result, transport, _, _ = _run(tmp_path, [
        _response(
            ("first", "read_file", {"path": "world_model.py"}),
            ("second", "read_file", {"path": "world_model.py"}),
        ),
        _response(("finish", "unresolved", {"notes": "Need more evidence."})),
    ])

    results = [
        block
        for block in transport.requests[1]["messages"][-1]["content"]
        if block["type"] == "tool_result"
    ]
    assert result.tool_calls == 2
    assert [block["tool_use_id"] for block in results] == ["first", "second"]
    assert results[0]["content"] == results[1]["content"]


def test_duplicate_tool_use_ids_reject_the_entire_batch(tmp_path):
    result, _, current, builder = _run(tmp_path, [
        _response(
            ("same-id", "read_file", {"path": "world_model.py"}),
            ("same-id", "read_file", {"path": "notes/world_model.md"}),
        ),
    ])

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "invalid_multi_tool_envelope"
    assert result.tool_calls == 0
    assert builder.turns[0]["envelope_valid"] is False
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_batch_over_remaining_tool_budget_executes_nothing(tmp_path):
    bounds = replace(DEFAULT_BOUNDS, max_tool_calls=2)
    result, transport, current, _ = _run(tmp_path, [
        _response(
            ("id-a", "read_file", {"path": "world_model.py"}),
            ("id-b", "read_file", {"path": "notes/world_model.md"}),
            ("id-c", "read_file", {"path": "evidence.json", "start": 0, "count": 2}),
        ),
    ], bounds)

    assert result.status is EpisodeStatus.BUDGET_EXHAUSTED
    assert result.failure_reason == "tool_budget_exhausted"
    assert result.tool_calls == 0
    assert len(transport.requests) == 1
    assert not any(event["event_type"] == "BUILDER_TOOL_OBSERVED" for event in result.events)
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_read_batch_executes_deterministically(tmp_path):
    _, transport, _, _ = _run(tmp_path, [
        _response(
            ("evidence", "read_file", {"path": "evidence.json", "start": 0, "count": 1}),
            ("model", "read_file", {"path": "world_model.py"}),
            ("notes", "read_file", {"path": "notes/world_model.md"}),
        ),
        _response(("finish", "unresolved", {"notes": "Need more evidence."})),
    ])

    results = [
        json.loads(block["content"][0]["text"])
        for block in transport.requests[1]["messages"][-1]["content"]
        if block["type"] == "tool_result"
    ]
    assert [(item["kind"], item["path"]) for item in results] == [
        ("evidence", "evidence.json"),
        ("file", "world_model.py"),
        ("file", "notes/world_model.md"),
    ]


def test_batch_does_not_expand_read_bounds(tmp_path):
    result, transport, current, builder = _run(tmp_path, [
        _response(
            ("model", "read_file", {"path": "world_model.py"}),
            (
                "too-wide",
                "read_file",
                {"path": "evidence.json", "start": 0, "count": 3},
            ),
        ),
    ])

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "invalid_multi_tool_envelope"
    assert result.tool_calls == 0
    assert len(transport.requests) == 1
    assert builder.turns[0]["calls"][1]["schema_valid"] is False
    assert builder.turns[0]["calls"][1]["host_valid"] is False
    assert not any(event["event_type"] == "BUILDER_TOOL_OBSERVED" for event in result.events)
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_four_read_calls_fail_closed(tmp_path):
    result, _, current, builder = _run(tmp_path, [
        _response(*(
            (f"read-{index}", "read_file", {"path": "world_model.py"})
            for index in range(4)
        )),
    ])

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "invalid_multi_tool_envelope"
    assert result.tool_calls == 0
    assert builder.turns[0]["native_tool_call_count"] == 4
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_read_plus_write_multi_call_fails_closed(tmp_path):
    result, _, current, _ = _run(tmp_path, [
        _response(
            ("read", "read_file", {"path": "world_model.py"}),
            ("write", "write_file", {"path": "world_model.py", "content": BASE_SOURCE}),
        ),
    ])

    assert result.failure_reason == "invalid_multi_tool_envelope"
    assert result.tool_calls == 0
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_two_write_calls_fail_closed(tmp_path):
    result, _, current, _ = _run(tmp_path, [
        _response(
            ("write-a", "write_file", {"path": "world_model.py", "content": BASE_SOURCE}),
            ("write-b", "write_file", {"path": "notes/world_model.md", "content": "notes"}),
        ),
    ])

    assert result.failure_reason == "invalid_multi_tool_envelope"
    assert result.tool_calls == 0
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_read_plus_unresolved_fails_closed(tmp_path):
    result, _, current, _ = _run(tmp_path, [
        _response(
            ("read", "read_file", {"path": "world_model.py"}),
            ("stop", "unresolved", {"notes": "Ambiguous."}),
        ),
    ])

    assert result.failure_reason == "invalid_multi_tool_envelope"
    assert result.tool_calls == 0
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_read_plus_run_python_fails_closed(tmp_path):
    result, _, current, _ = _run(tmp_path, [
        _response(
            ("read", "read_file", {"path": "world_model.py"}),
            ("analyze", "run_python", {"code": "print(1)"}),
        ),
    ])

    assert result.failure_reason == "invalid_multi_tool_envelope"
    assert result.tool_calls == 0
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_read_plus_unknown_tool_fails_closed(tmp_path):
    result, _, current, builder = _run(tmp_path, [
        _response(
            ("read", "read_file", {"path": "world_model.py"}),
            ("unknown", "shell", {"command": "whoami"}),
        ),
    ])

    assert result.failure_reason == "invalid_multi_tool_envelope"
    assert result.tool_calls == 0
    assert builder.turns[0]["calls"][1]["host_valid"] is False
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_single_write_still_uses_w4_semantics(tmp_path):
    result, _, current, builder = _run(tmp_path, [
        _response((
            "write",
            "write_file",
            {"path": "world_model.py", "content": REVERSE_SOURCE},
        )),
    ])

    assert builder.turns[0]["envelope_kind"] == "single"
    assert result.status is EpisodeStatus.CONSISTENT_ENOUGH
    assert result.tool_calls == 1
    assert result.semantic_revisions == 1
    assert result.final_public_accuracy == 1.0
    assert current.read_text(encoding="utf-8") == REVERSE_SOURCE


def test_single_unresolved_still_terminates(tmp_path):
    result, _, current, builder = _run(tmp_path, [
        _response((
            "stop",
            "unresolved",
            {"notes": "The public evidence leaves two dynamics possible."},
        )),
    ])

    assert builder.turns[0]["envelope_kind"] == "single"
    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.tool_calls == 0
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_single_read_still_uses_w4_semantics(tmp_path):
    result, transport, _, builder = _run(tmp_path, [
        _response(("read", "read_file", {"path": "world_model.py"})),
        _response(("stop", "unresolved", {"notes": "Read completed."})),
    ])

    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.tool_calls == 1
    assert builder.turns[0]["envelope_kind"] == "single"
    results = [
        block
        for block in transport.requests[1]["messages"][-1]["content"]
        if block["type"] == "tool_result"
    ]
    assert [block["tool_use_id"] for block in results] == ["read"]


def test_single_run_python_still_uses_w4_semantics(tmp_path):
    result, transport, _, builder = _run(tmp_path, [
        _response((
            "analyze",
            "run_python",
            {"code": "print(sum(item['observed']['value'] for item in evidence))"},
        )),
        _response(("stop", "unresolved", {"notes": "Analysis completed."})),
    ])

    assert result.status is EpisodeStatus.UNRESOLVED
    assert result.tool_calls == 1
    assert builder.turns[0]["envelope_kind"] == "single"
    assert "8" in transport.requests[1]["messages"][-1]["content"][0]["content"][0]["text"]


def test_multi_tool_results_visible_next_turn(tmp_path):
    _, transport, _, builder = _run(tmp_path, [
        _response(
            ("id-a", "read_file", {"path": "world_model.py"}),
            ("id-b", "read_file", {"path": "notes/world_model.md"}),
            ("id-c", "read_file", {"path": "evidence.json", "start": 0, "count": 2}),
            text="Inspect the bounded inputs.",
        ),
        _response(("stop", "unresolved", {"notes": "Need more evidence."})),
    ])

    messages = transport.requests[1]["messages"]
    assert [block["type"] for block in messages[-2]["content"]] == [
        "text", "tool_use", "tool_use", "tool_use",
    ]
    assert [block["type"] for block in messages[-1]["content"]] == [
        "tool_result", "tool_result", "tool_result", "text",
    ]
    assert builder.turns[1]["tool_result_count"] == 3


def test_batch_events_share_model_parent_and_pair_observations(tmp_path):
    result, _, _, _ = _run(tmp_path, [
        _response(
            ("id-a", "read_file", {"path": "world_model.py"}),
            ("id-b", "read_file", {"path": "notes/world_model.md"}),
            ("id-c", "read_file", {"path": "evidence.json", "start": 0, "count": 2}),
        ),
        _response(("stop", "unresolved", {"notes": "Enough inspection."})),
    ])

    requests = [event for event in result.events if event["event_type"] == "BUILDER_ACTION_REQUESTED"][:3]
    observations = [event for event in result.events if event["event_type"] == "BUILDER_TOOL_OBSERVED"][:3]
    assert [event["source_event_seqs"] for event in requests] == [[1], [1], [1]]
    assert [event["source_event_seqs"] for event in observations] == [
        [requests[0]["seq"]],
        [requests[1]["seq"]],
        [requests[2]["seq"]],
    ]


def test_final_turn_read_batch_has_incomplete_pairing(tmp_path):
    bounds = replace(DEFAULT_BOUNDS, max_model_turns=1)
    result, _, _, builder = _run(tmp_path, [
        _response(
            ("id-a", "read_file", {"path": "world_model.py"}),
            ("id-b", "read_file", {"path": "notes/world_model.md"}),
            ("id-c", "read_file", {"path": "evidence.json", "start": 0, "count": 2}),
        ),
    ], bounds)
    turns = w5._finalize_turns(builder.turns, result.events, BASE_SOURCE, "wm-v1")
    metrics = w5._record_metrics(turns, result.events)

    assert result.status is EpisodeStatus.BUDGET_EXHAUSTED
    assert turns[0]["result_required_tool_call_ids"] == ["id-a", "id-b", "id-c"]
    assert turns[0]["pairing_incomplete"] is True
    assert metrics["pairing_integrity"] is False


def test_terminal_applied_write_does_not_require_another_result_turn(tmp_path):
    result, _, _, builder = _run(tmp_path, [
        _response((
            "write",
            "write_file",
            {"path": "world_model.py", "content": REVERSE_SOURCE},
        )),
    ])
    turns = w5._finalize_turns(builder.turns, result.events, BASE_SOURCE, "wm-v1")

    assert result.status is EpisodeStatus.CONSISTENT_ENOUGH
    assert turns[0]["executed_tool_call_ids"] == ["write"]
    assert turns[0]["result_required_tool_call_ids"] == []
    assert turns[0]["pairing_incomplete"] is False


def test_context_bound_includes_all_results(tmp_path):
    bounds = replace(DEFAULT_BOUNDS, max_context_chars=1_200)
    result, transport, current, builder = _run(tmp_path, [
        _response(
            ("id-a", "read_file", {"path": "world_model.py"}),
            ("id-b", "read_file", {"path": "notes/world_model.md"}),
            ("id-c", "read_file", {"path": "evidence.json", "start": 0, "count": 2}),
        ),
    ], bounds)

    assert result.status is EpisodeStatus.BUDGET_EXHAUSTED
    assert result.failure_reason == "context_bound_exhausted"
    assert result.tool_calls == 3
    assert len(transport.requests) == 1
    assert builder.turns[0]["history_chars"] <= bounds.max_context_chars
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_episode_history_is_fresh_for_each_activation(tmp_path):
    transport = ScriptedTransport([
        _response(("first-stop", "unresolved", {"notes": "First episode."})),
        _response(("second-stop", "unresolved", {"notes": "Second episode."})),
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
    builder = BoundedReadBatchBuilder(client, DEFAULT_BOUNDS)
    for episode in ("first", "second"):
        current, working, notes = _paths(tmp_path / episode)
        result = run_read_batch_revision_episode(
            builder=builder,
            revision=_request(),
            episode_ref=episode,
            current_path=current,
            working_path=working,
            notes_path=notes,
        )
        assert result.status is EpisodeStatus.UNRESOLVED

    assert len(transport.requests) == 2
    assert all(len(request["messages"]) == 1 for request in transport.requests)
    assert all(
        request["messages"][0]["content"][0]["type"] == "text"
        for request in transport.requests
    )


def test_w5_changes_only_prompt_cardinality_and_not_tool_contract():
    expected = W4_BUILDER_SYSTEM_PROMPT.replace(
        "Choose exactly one tool per turn. Natural-language explanation may accompany a tool call;\n"
        "the structured tool call alone selects the authority-bearing action.\n",
        "Choose one tool per turn. A response may instead contain two or three read_file calls;\n"
        "no other multi-tool response is valid. Natural-language explanation may accompany tool calls;\n"
        "the structured tool calls alone select the authority-bearing actions.\n",
    )

    assert MAX_READ_BATCH == 3
    assert W5_BUILDER_SYSTEM_PROMPT == expected
    assert "use three" not in W5_BUILDER_SYSTEM_PROMPT.lower()
    assert tuple(tool["name"] for tool in NATIVE_TOOL_SPECS) == (
        "read_file", "run_python", "write_file", "unresolved",
    )


def test_current_model_unavailable_keeps_w2_failure_semantics(tmp_path):
    transport = ScriptedTransport([])
    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=transport,
    )
    result = run_read_batch_revision_episode(
        builder=BoundedReadBatchBuilder(client, DEFAULT_BOUNDS),
        revision=_request(),
        episode_ref="missing-current",
        current_path=tmp_path / "current" / "world_model.py",
        working_path=tmp_path / "working" / "world_model.py",
        notes_path=tmp_path / "working" / "notes" / "world_model.md",
    )

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "current_model_unavailable"
    assert transport.requests == []


def test_current_model_mismatch_keeps_w2_failure_semantics(tmp_path):
    current, working, notes = _paths(tmp_path)
    current.write_text(BASE_SOURCE + "\n", encoding="utf-8")
    transport = ScriptedTransport([])
    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=transport,
    )
    result = run_read_batch_revision_episode(
        builder=BoundedReadBatchBuilder(client, DEFAULT_BOUNDS),
        revision=_request(),
        episode_ref="mismatched-current",
        current_path=current,
        working_path=working,
        notes_path=notes,
    )

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "current_model_mismatch"
    assert transport.requests == []


def test_context_projection_failure_keeps_w2_failure_semantics(tmp_path):
    bounds = replace(DEFAULT_BOUNDS, max_context_chars=1)
    result, transport, current, _ = _run(tmp_path, [], bounds)

    assert result.status is EpisodeStatus.STRUCTURAL_FAILURE
    assert result.failure_reason == "context_projection_failed"
    assert transport.requests == []
    assert current.read_text(encoding="utf-8") == BASE_SOURCE


def test_hidden_never_appears_in_batch_results(tmp_path):
    campaign = load_registered_w5(
        Path(__file__).parent / "fixtures" / "w5" / "manifest.json"
    )
    responses = []
    for index in range(4):
        responses.extend((
            _response(
                (f"model-{index}", "read_file", {"path": "world_model.py"}),
                (f"notes-{index}", "read_file", {"path": "notes/world_model.md"}),
                (
                    f"evidence-{index}",
                    "read_file",
                    {"path": "evidence.json", "start": 0, "count": 2},
                ),
            ),
            _response((f"stop-{index}", "unresolved", {"notes": "No revision."})),
        ))
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
    result = run_registered_campaign(
        campaign,
        model=client,
        output_path=tmp_path / "w5-hidden-result.json",
    )

    assert all(record["hidden_isolated"] is True for record in result["records"])


def test_partial_hidden_compound_surface_is_detected():
    hidden = RevisionEvidence(
        state=DynamicsState(111, "hidden-mode"),
        action=DynamicsAction("step", 17),
        expected=DynamicsObservation(128, "hidden-mode"),
        observed=DynamicsObservation(127, "hidden-mode"),
        result="ERROR",
    )
    turns = [{
        "provider_request": {
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": json.dumps({"state": {"value": 111, "mode": "hidden-mode"}}),
                }],
            }],
        },
        "attempted_request": None,
    }]

    assert w5._hidden_evidence_isolated((), (hidden,), turns) is False


def test_mutated_nested_campaign_is_rejected_before_provider_call(tmp_path):
    campaign = load_registered_w5(
        Path(__file__).parent / "fixtures" / "w5" / "manifest.json"
    )
    w2_campaign = campaign.w4_campaign.w3_campaign.w2_campaign
    mutated = replace(w2_campaign, resolvable=tuple(reversed(w2_campaign.resolvable)))
    campaign = replace(
        campaign,
        w4_campaign=replace(
            campaign.w4_campaign,
            w3_campaign=replace(campaign.w4_campaign.w3_campaign, w2_campaign=mutated),
        ),
    )
    transport = ScriptedTransport([])
    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=transport,
    )

    with pytest.raises(RuntimeError, match="preregistration changed"):
        run_registered_campaign(
            campaign,
            model=client,
            output_path=tmp_path / "mutated-result.json",
        )
    assert transport.requests == []


def test_first_call_provider_failure_is_inconclusive(tmp_path):
    campaign = load_registered_w5(
        Path(__file__).parent / "fixtures" / "w5" / "manifest.json"
    )

    def fail(_body):
        raise RuntimeError("provider unavailable")

    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=fail,
    )
    result = run_registered_campaign(
        campaign,
        model=client,
        output_path=tmp_path / "provider-failure.json",
    )

    assert result["verdict"] == "W5_INCONCLUSIVE"
    assert result["summary"]["provider_failed"] is True


def test_failed_continuation_preserves_attempted_results_and_incomplete_pairing(tmp_path):
    campaign = load_registered_w5(
        Path(__file__).parent / "fixtures" / "w5" / "manifest.json"
    )

    class FailSecondCall:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _body):
            self.calls += 1
            if self.calls == 1:
                return _response(
                    ("id-a", "read_file", {"path": "world_model.py"}),
                    ("id-b", "read_file", {"path": "notes/world_model.md"}),
                    ("id-c", "read_file", {"path": "evidence.json", "start": 0, "count": 2}),
                )
            raise RuntimeError("continuation unavailable")

    client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=FailSecondCall(),
    )
    result = run_registered_campaign(
        campaign,
        model=client,
        output_path=tmp_path / "continuation-failure.json",
    )

    turns = result["records"][0]["turns"]
    attempted = turns[1]["attempted_request"]
    result_ids = [
        block["tool_use_id"]
        for block in attempted["messages"][-1]["content"]
        if block["type"] == "tool_result"
    ]
    assert result["verdict"] == "W5_INCONCLUSIVE"
    assert result_ids == ["id-a", "id-b", "id-c"]
    assert turns[0]["pairing_incomplete"] is True
    assert result["summary"]["pairing_integrity_pass"] is False


def test_existing_campaign_output_refuses_a_second_run(tmp_path):
    campaign = load_registered_w5(
        Path(__file__).parent / "fixtures" / "w5" / "manifest.json"
    )
    output = tmp_path / "once.json"
    responses = [
        _response((f"stop-{index}", "unresolved", {"notes": "No revision."}))
        for index in range(4)
    ]
    first_transport = ScriptedTransport(responses)
    first_client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=first_transport,
    )
    run_registered_campaign(campaign, model=first_client, output_path=output)
    second_transport = ScriptedTransport([])
    second_client = DeepSeekAnthropicNativeToolClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
        max_tokens=1600,
        temperature=0.0,
        timeout=45.0,
        transport=second_transport,
    )

    with pytest.raises(FileExistsError, match="already exists"):
        run_registered_campaign(campaign, model=second_client, output_path=output)
    assert second_transport.requests == []


def test_registered_real_runner_rejects_noncanonical_output(tmp_path):
    with pytest.raises(w5.W5Blocked, match="canonical_result_path"):
        w5.run_registered_w5(tmp_path / "alternate.json")


def test_authority_escape_is_a_fail_verdict():
    records = [{"evidence_unchanged": True, "current_coherent": True}]
    summary = {
        "authority_isolation_pass": False,
        "context_bound_pass": True,
        "episode_freshness_pass": True,
        "hidden_isolation_pass": True,
        "provider_failed": False,
    }

    assert w5._campaign_verdict(records, summary) == "W5_FAIL"


def test_registered_campaign_passes_native_batch_milestone(tmp_path):
    campaign = load_registered_w5(
        Path(__file__).parent / "fixtures" / "w5" / "manifest.json"
    )

    class RuleTransport:
        def __init__(self) -> None:
            self.call_index = 0

        def __call__(self, body):
            self.call_index += 1
            assistant_turns = sum(
                message["role"] == "assistant" for message in body["messages"]
            )
            prefix = f"call-{self.call_index}"
            if assistant_turns == 0:
                return _response(
                    (f"{prefix}-model", "read_file", {"path": "world_model.py"}),
                    (f"{prefix}-notes", "read_file", {"path": "notes/world_model.md"}),
                    (
                        f"{prefix}-evidence",
                        "read_file",
                        {"path": "evidence.json", "start": 0, "count": 2},
                    ),
                )
            context = json.loads(body["messages"][-1]["content"][-1]["text"])
            first = context["current_verifier_state"]["first_divergence"]
            if first is None:
                return _response((
                    f"{prefix}-stop",
                    "unresolved",
                    {"notes": "Step-only evidence leaves toggle dynamics unresolved."},
                ))
            source = {
                "boost": BOOST_SOURCE,
                "reverse": REVERSE_SOURCE,
                "clamped": CLAMPED_SOURCE,
            }[first["state"]["mode"]]
            return _response((
                f"{prefix}-write",
                "write_file",
                {"path": "world_model.py", "content": source},
            ))

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
        output_path=tmp_path / "w5-result.json",
    )

    assert result["verdict"] == "W5_PASS"
    assert result["summary"]["multi_read_envelopes_accepted"] == 4
    assert result["summary"]["multi_read_calls_executed"] == 12
    assert result["summary"]["max_calls_in_one_response"] == 3
    assert result["summary"]["tool_use_count"] == 16
    assert result["summary"]["tool_result_count"] == 12
    assert result["summary"]["pairing_integrity_pass"] is True
    assert result["summary"]["native_loop_continuation_records"] == 4
    assert result["summary"]["resolvable_native_world_model_writes"] == 3
    assert result["summary"]["reverse_post_edit_verifier"] is True
    assert result["summary"]["ambiguity_native_unresolved"] is True
    assert all(
        record["metrics"]["first_real_tool_result_turn"] == 2
        for record in result["records"]
    )
