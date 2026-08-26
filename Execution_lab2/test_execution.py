import json
import sys

import pytest

from Execution_lab2.execution import (
    Complete,
    ReadRequest,
    RootAgentProcess,
    ScriptedModel,
    SharedEnvironment,
    ShellRequest,
    ToolCall,
    ToolHost,
    WriteRequest,
    fold_execution_state,
)


def test_root_completes_multistep_filesystem_task(tmp_path):
    (tmp_path / "source.txt").write_text("alpha\n", encoding="utf-8")
    model = ScriptedModel(
        [
            ToolCall(ReadRequest("source.txt")),
            ToolCall(WriteRequest("output.txt", "ALPHA\n")),
            ToolCall(ReadRequest("output.txt")),
            Complete("finished"),
        ]
    )

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=4,
    ).run("Uppercase source.txt into output.txt and inspect it")

    assert result.status == "completed"
    assert result.output == "finished"
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "ALPHA\n"
    assert [step.observation.ok for step in result.steps[:-1]] == [True, True, True]
    assert result.steps[-1].observation is None
    assert result.state == fold_execution_state(result.events)


def test_execution_state_is_an_exact_replay_of_an_append_only_event_log(tmp_path):
    model = ScriptedModel(
        [ToolCall(WriteRequest("output.txt", "done\n")), Complete("finished")]
    )

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
    ).run("Create output.txt")

    assert result.state == fold_execution_state(result.events)
    assert [event.sequence for event in result.events] == list(
        range(1, len(result.events) + 1)
    )
    assert [event.event_type for event in result.events] == [
        "EXECUTION_STARTED",
        "MODEL_DECISION",
        "TOOL_CALL_STARTED",
        "TOOL_RESULT",
        "MODEL_DECISION",
        "EXECUTION_COMPLETED",
    ]
    assert result.events[3].source_event_refs == (result.events[2].event_id,)
    assert result.state.status == "completed"
    assert result.state.goal == "Create output.txt"
    assert result.state.decision_count == 2
    assert result.state.completion == "finished"
    with pytest.raises(TypeError):
        result.events[0].payload["goal"] = "changed"


def test_decision_frames_capture_the_exact_requests_and_capabilities(tmp_path):
    first_action = ToolCall(WriteRequest("output.txt", "done\n"))
    second_action = Complete("finished")
    model = ScriptedModel([first_action, second_action], identifier="fixture-model")

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
    ).run("Create output.txt")

    assert model.received_requests == [
        frame.actual_request for frame in result.decision_frames
    ]
    assert all(
        request is frame.actual_request
        for request, frame in zip(model.received_requests, result.decision_frames)
    )
    assert [frame.model_identifier for frame in result.decision_frames] == [
        "fixture-model",
        "fixture-model",
    ]
    assert [frame.state_version for frame in result.decision_frames] == [1, 4]
    assert result.decision_frames[0].source_event_refs == (
        result.events[0].event_id,
    )
    assert result.decision_frames[0].actual_tools_exposed == (
        result.decision_frames[0].actual_request.available_tools
    )
    assert result.decision_frames[0].raw_model_response is first_action
    assert result.decision_frames[0].resulting_action is first_action
    decision_event = next(
        event for event in result.events if event.event_type == "MODEL_DECISION"
    )
    assert decision_event.payload["frame"] is result.decision_frames[0]


def test_state_derived_context_does_not_grow_with_the_full_event_history(tmp_path):
    interaction_count = 24
    goal = "Write a sequence of small files and finish"
    actions = [
        ToolCall(WriteRequest(f"item-{number}.txt", f"value-{number}"))
        for number in range(interaction_count)
    ] + [Complete("finished")]
    model = ScriptedModel(actions)

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=interaction_count + 1,
        max_context_chars=900,
    ).run(goal)

    full_history_sizes = []
    observations = []
    for frame in result.decision_frames:
        full_history_sizes.append(
            len(
                json.dumps(
                    {
                        "goal": goal,
                        "observations": observations,
                        "tools": frame.actual_tools_exposed,
                    }
                )
            )
        )
        if frame.resulting_action and isinstance(frame.resulting_action, ToolCall):
            step = result.steps[len(observations)]
            observations.append(repr(step.observation))

    bounded_sizes = [
        frame.actual_request.context_size_chars
        for frame in result.decision_frames
    ]
    assert result.status == "completed"
    assert result.output == "finished"
    assert full_history_sizes[-1] > full_history_sizes[1]
    assert bounded_sizes[-1] < full_history_sizes[-1]
    assert max(bounded_sizes) <= 900
    assert max(bounded_sizes[1:]) - min(bounded_sizes[1:]) < 200


def test_large_canonical_tool_result_is_truthful_but_model_projection_is_bounded(
    tmp_path,
):
    hidden_tail = "MODEL_MUST_NOT_SEE_THIS_TAIL"
    large_output = ("x" * 20_000) + hidden_tail
    (tmp_path / "large.txt").write_text(large_output, encoding="utf-8")
    model = ScriptedModel(
        [ToolCall(ReadRequest("large.txt")), Complete("inspected")]
    )

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path), max_output_chars=30_000),
        max_decisions=2,
        max_context_chars=900,
    ).run("Inspect large.txt")

    result_event = next(
        event for event in result.events if event.event_type == "TOOL_RESULT"
    )
    canonical = result_event.payload["observation"].result
    visible_request = model.received_requests[1]
    visible_result = json.loads(visible_request.context)["observation"]["result"]
    assert canonical.output == large_output
    assert canonical.truncated is False
    assert len(canonical.output) == len(large_output)
    assert visible_request.context_size_chars <= 900
    assert visible_result["output"]["truncated"] is True
    assert visible_result["output"]["original_chars"] == len(large_output)
    assert len(visible_result["output"]["text"]) < len(large_output)
    assert hidden_tail not in visible_request.context


def test_missing_file_failure_flows_event_state_context_frame_then_model_action(
    tmp_path,
):
    alternative = ToolCall(WriteRequest("recovered.txt", "fallback\n"))
    model = ScriptedModel(
        [ToolCall(ReadRequest("missing.txt")), alternative, Complete("recovered")]
    )

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
    ).run("Recover from a missing input")

    failed_event = next(
        event for event in result.events if event.event_type == "TOOL_FAILED"
    )
    call_event = result.events[failed_event.sequence - 2]
    state_at_failure = fold_execution_state(result.events[: failed_event.sequence])
    next_frame = result.decision_frames[1]
    next_context = json.loads(next_frame.actual_request.context)
    next_decision_event = result.events[failed_event.sequence]

    assert call_event.event_type == "TOOL_CALL_STARTED"
    assert failed_event.source_event_refs == (call_event.event_id,)
    assert state_at_failure.latest_observation is failed_event.payload["observation"]
    assert state_at_failure.last_result.error_code == "not_found"
    assert next_frame.state_version == failed_event.sequence
    assert next_frame.source_event_refs == (failed_event.event_id,)
    assert next_decision_event.event_type == "MODEL_DECISION"
    assert next_decision_event.source_event_refs == (failed_event.event_id,)
    assert next_context["observation"]["result"]["error_code"] == "not_found"
    assert next_frame.raw_model_response is alternative
    assert next_frame.resulting_action is alternative
    assert result.status == "completed"
    assert (tmp_path / "recovered.txt").read_text(encoding="utf-8") == "fallback\n"


def test_tool_failure_is_returned_to_the_model_for_recovery(tmp_path):
    model = ScriptedModel(
        [
            ToolCall(ReadRequest("missing.txt")),
            ToolCall(WriteRequest("recovered.txt", "fallback\n")),
            Complete("recovered"),
        ]
    )

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
    ).run("Recover from a missing input")

    failure = json.loads(model.received_requests[1].context)["observation"]
    assert failure is not None
    assert failure["result"]["ok"] is False
    assert failure["result"]["error_code"] == "not_found"
    assert result.status == "completed"
    assert (tmp_path / "recovered.txt").read_text(encoding="utf-8") == "fallback\n"


def test_tool_host_rejects_relative_and_absolute_workspace_escape(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("unchanged\n", encoding="utf-8")
    tools = ToolHost(SharedEnvironment(tmp_path))

    relative = tools.execute(WriteRequest(f"../{outside.name}", "escaped\n"))
    absolute = tools.execute(WriteRequest(str(outside), "escaped\n"))

    assert relative.ok is False
    assert relative.error_code == "workspace_boundary"
    assert absolute.ok is False
    assert absolute.error_code == "workspace_boundary"
    assert outside.read_text(encoding="utf-8") == "unchanged\n"


def test_root_stops_at_the_hard_decision_bound(tmp_path):
    model = ScriptedModel(
        [
            ToolCall(WriteRequest("count.txt", str(number)))
            for number in range(1, 6)
        ]
    )

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
    ).run("Never complete")

    assert result.status == "failed"
    assert result.failure == "decision_limit_reached"
    assert len(result.steps) == 3
    assert len(model.received_requests) == 3
    assert (tmp_path / "count.txt").read_text(encoding="utf-8") == "3"


def test_shell_uses_argv_workspace_cwd_and_structured_process_results(tmp_path):
    tools = ToolHost(SharedEnvironment(tmp_path))

    success = tools.execute(
        ShellRequest(
            (
                sys.executable,
                "-c",
                "from pathlib import Path; print(Path.cwd().name)",
            )
        )
    )
    failure = tools.execute(
        ShellRequest(
            (
                sys.executable,
                "-c",
                "import sys; print('bad', file=sys.stderr); raise SystemExit(7)",
            )
        )
    )

    assert success.ok is True
    assert success.output.strip() == tmp_path.name
    assert success.exit_code == 0
    assert failure.ok is False
    assert failure.error_code == "process_failed"
    assert failure.exit_code == 7
    assert "bad" in failure.error


def test_tool_output_and_shell_runtime_are_bounded(tmp_path):
    (tmp_path / "large.txt").write_text("0123456789", encoding="utf-8")
    tools = ToolHost(
        SharedEnvironment(tmp_path),
        shell_timeout_seconds=0.05,
        max_output_chars=8,
    )

    read = tools.execute(ReadRequest("large.txt"))
    timed_out = tools.execute(
        ShellRequest((sys.executable, "-c", "import time; time.sleep(1)"))
    )

    assert read.output == "01234567"
    assert read.truncated is True
    assert timed_out.ok is False
    assert timed_out.error_code == "shell_timeout"


def test_unknown_action_and_tool_fail_explicitly(tmp_path):
    tools = ToolHost(SharedEnvironment(tmp_path))
    unknown_tool = tools.execute(object())
    result = RootAgentProcess(
        model=ScriptedModel([object()]),
        tools=tools,
        max_decisions=1,
    ).run("Reject an unknown action")

    assert unknown_tool.ok is False
    assert unknown_tool.error_code == "unknown_tool"
    assert result.status == "failed"
    assert result.failure == "unknown_action:object"


def test_tool_host_validates_typed_request_fields_before_io(tmp_path):
    tools = ToolHost(SharedEnvironment(tmp_path))

    empty_path = tools.execute(ReadRequest(""))
    invalid_content = tools.execute(WriteRequest("output.txt", object()))

    assert empty_path.ok is False
    assert empty_path.error_code == "invalid_request"
    assert invalid_content.ok is False
    assert invalid_content.error_code == "invalid_request"
    assert not (tmp_path / "output.txt").exists()
