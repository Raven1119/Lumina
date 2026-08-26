import sys

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

    failure = model.seen_contexts[1].observation
    assert failure is not None
    assert failure.result.ok is False
    assert failure.result.error_code == "not_found"
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
    assert len(model.seen_contexts) == 3
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
