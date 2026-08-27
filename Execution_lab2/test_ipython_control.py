import gc
import json
import os
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import Execution_lab2.ipython_control as ipython_control_module
from Execution_lab2.deepseek_model import DeepSeekModel
from Execution_lab2.ipython_control import PersistentIPython
from Execution_lab2.execution import (
    ClaimComplete,
    EventLog,
    FileContentEquals,
    IPYTHON_TOOL_CONTRACTS,
    IPythonCode,
    IPythonObservation,
    ModelRequest,
    NativeModelDecision,
    RootAgentProcess,
    ScriptedModel,
    SharedEnvironment,
    ToolHost,
    Wait,
    fold_execution_state,
)


class _IPythonScriptedModel(ScriptedModel):
    tool_contracts = IPYTHON_TOOL_CONTRACTS


class _RetryableShutdownManager:
    def __init__(self):
        self.alive = True
        self.fail_shutdown = True
        self.cleanup_calls = 0

    def is_alive(self):
        return self.alive

    def shutdown_kernel(self, *, now):
        assert now is True
        if self.fail_shutdown:
            raise RuntimeError("shutdown failed")
        self.alive = False

    def cleanup_resources(self):
        self.cleanup_calls += 1


class _InterruptRecordingEventLog(EventLog):
    def __init__(self, path):
        super().__init__(path)
        self.execution_started = threading.Event()
        self.interrupt_recorded = threading.Event()

    def append(self, event_type, payload, source_event_refs=()):
        event = super().append(event_type, payload, source_event_refs)
        if event_type == "IPYTHON_EXECUTION_STARTED":
            self.execution_started.set()
        if event_type == "INTERRUPT_REQUESTED":
            self.interrupt_recorded.set()
        return event


class _InterruptCountingIPython(PersistentIPython):
    def __init__(self, workspace):
        super().__init__(workspace, execution_timeout_seconds=2)
        self.interrupt_calls = 0

    def interrupt(self):
        self.interrupt_calls += 1
        return super().interrupt()


def test_in_flight_ipython_interrupt_records_actual_failure_then_suspends(tmp_path):
    event_log = _InterruptRecordingEventLog(tmp_path / "events.jsonl")
    control = _InterruptCountingIPython(tmp_path)
    model = _IPythonScriptedModel(
        [
            IPythonCode(
                "from pathlib import Path; "
                "Path('ipython-started.txt').write_text('started'); "
                "import time; time.sleep(10); print('late success')"
            )
        ]
    )
    runtime = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=event_log,
        ipython_control=control,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future = pool.submit(
            runtime.run,
            "Interrupt Python",
            FileContentEquals("unused.txt", "unused"),
        )
        assert event_log.execution_started.wait(timeout=2)
        marker = tmp_path / "ipython-started.txt"
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.read_text(encoding="utf-8") == "started"
        interrupt_future = pool.submit(runtime.interrupt)
        assert event_log.interrupt_recorded.wait(timeout=2)
        interrupted = interrupt_future.result(timeout=4)
        run_result = run_future.result(timeout=4)

    assert control.interrupt_calls == 1
    assert interrupted.status == run_result.status == "suspended"
    assert [event.event_type for event in run_result.events][-4:] == [
        "IPYTHON_EXECUTION_STARTED",
        "INTERRUPT_REQUESTED",
        "IPYTHON_EXECUTION_FAILED",
        "ACTOR_SUSPENDED",
    ]
    observation = run_result.events[-2].payload["observation"]
    assert observation.result.ok is False
    assert observation.result.error_code in ("execution_error", "timeout")
    if observation.result.error_code == "execution_error":
        assert "KeyboardInterrupt" in observation.result.error
    else:
        assert "exceeded 2" in observation.result.error
    assert run_result.state == fold_execution_state(run_result.events)
    runtime.close()


def _tool_response(call_id, name, arguments):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            }
        ]
    }


def _tool_calls_response(*calls):
    response = _tool_response(*calls[0])
    response["choices"][0]["message"]["tool_calls"] = [
        {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }
        for call_id, name, arguments in calls
    ]
    return response


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _usage(result):
    totals = {"input_tokens": 0, "output_tokens": 0}
    for frame in result.decision_frames:
        response = frame.raw_provider_response
        if not isinstance(response, Mapping):
            continue
        usage = response.get("usage")
        if not isinstance(usage, Mapping):
            continue
        for source, target in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
        ):
            value = usage.get(source)
            if isinstance(value, int):
                totals[target] += value
    return totals


def _ab_metrics(result, elapsed_seconds, expected_path, expected_content):
    requests = [
        _plain(frame.provider_wire_request)
        for frame in result.decision_frames
        if frame.provider_wire_request is not None
    ]
    serialized_requests = [
        json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        for request in requests
    ]
    tool_result_chars = sum(
        len(message.get("content", ""))
        for request in requests
        for message in request.get("messages", [])
        if isinstance(message, dict)
        and message.get("role") == "tool"
        and isinstance(message.get("content"), str)
    )
    output_path = expected_path
    return {
        "verified_success": (
            result.status == "completed"
            and output_path.is_file()
            and output_path.read_text(encoding="utf-8") == expected_content
        ),
        "provider_calls": len(result.decision_frames),
        "model_decisions": len(result.decision_frames),
        **_usage(result),
        "wall_seconds": round(elapsed_seconds, 3),
        "model_visible_result_chars": tool_result_chars,
        "max_request_chars": max(map(len, serialized_requests), default=0),
        "ipython_executions": sum(
            event.event_type == "IPYTHON_EXECUTION_STARTED"
            for event in result.events
        ),
        "native_tool_calls": sum(
            event.event_type == "TOOL_CALL_STARTED"
            for event in result.events
        ),
        "python_runtime_failures": sum(
            event.event_type == "IPYTHON_EXECUTION_FAILED"
            for event in result.events
        ),
        "runtime_status": result.status,
        "runtime_failure": result.failure,
    }


def test_ipython_namespace_persists_across_model_decisions(tmp_path):
    control = PersistentIPython(tmp_path)
    try:
        assert control.execute("x = 41").ok is True

        result = control.execute("print(x + 1)")

        assert result.ok is True
        assert result.output == "42\n"
    finally:
        control.close()


def test_ipython_kernel_starts_in_the_shared_workspace(tmp_path):
    control = PersistentIPython(tmp_path)
    try:
        result = control.execute("import os\nprint(os.getcwd())")

        assert result.ok is True
        assert Path(result.output.strip()).resolve() == tmp_path.resolve()
    finally:
        control.close()


def test_root_records_ipython_workspace_effect_and_closes_kernel(tmp_path):
    (tmp_path / "input.txt").write_text("alpha", encoding="utf-8")
    control = PersistentIPython(tmp_path)
    model = _IPythonScriptedModel(
        [
            IPythonCode(
                "from pathlib import Path\n"
                "text = Path('input.txt').read_text(encoding='utf-8')\n"
                "Path('output.txt').write_text(text.upper(), encoding='utf-8')"
            ),
            ClaimComplete(),
        ]
    )

    result = RootAgentProcess(
        model,
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        ipython_control=control,
    ).run(
        "Create output.txt containing the uppercase input.",
        FileContentEquals("output.txt", "ALPHA"),
    )

    assert result.status == "completed"
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "ALPHA"
    assert [event.event_type for event in result.events] == [
        "EXECUTION_STARTED",
        "MODEL_DECISION",
        "IPYTHON_EXECUTION_STARTED",
        "IPYTHON_EXECUTION_RESULT",
        "MODEL_DECISION",
        "COMPLETION_CLAIMED",
        "COMPLETION_VERIFIED",
        "EXECUTION_COMPLETED",
    ]
    observation = result.events[3].payload["observation"]
    assert isinstance(observation, IPythonObservation)
    assert observation.result.ok is True
    assert result.decision_frames[0].actual_tools_exposed == IPYTHON_TOOL_CONTRACTS
    assert result.state == fold_execution_state(result.events)
    assert control.is_alive is False


def test_ipython_failure_is_bounded_observation_and_root_can_continue(tmp_path):
    control = PersistentIPython(tmp_path, max_output_chars=64)
    model = _IPythonScriptedModel(
        [
            IPythonCode("raise RuntimeError('probe')"),
            IPythonCode(
                "from pathlib import Path\n"
                "Path('done.txt').write_text('done', encoding='utf-8')"
            ),
            ClaimComplete(),
        ]
    )

    result = RootAgentProcess(
        model,
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        ipython_control=control,
    ).run(
        "Create done.txt containing done.",
        FileContentEquals("done.txt", "done"),
    )

    failed = next(
        event
        for event in result.events
        if event.event_type == "IPYTHON_EXECUTION_FAILED"
    )
    observation = failed.payload["observation"]
    assert observation.result.error_code == "execution_error"
    assert observation.result.error == "RuntimeError: probe"
    assert "RuntimeError: probe" in model.received_requests[1].context
    assert result.status == "completed"
    assert control.is_alive is False


def test_ipython_output_is_truthfully_counted_and_bounded_for_context(tmp_path):
    control = PersistentIPython(tmp_path, max_output_chars=32)
    model = _IPythonScriptedModel(
        [
            IPythonCode("print('x' * 200)"),
            IPythonCode(
                "from pathlib import Path\n"
                "Path('done.txt').write_text('done', encoding='utf-8')"
            ),
            ClaimComplete(),
        ]
    )

    result = RootAgentProcess(
        model,
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        max_context_chars=768,
        ipython_control=control,
    ).run(
        "Create done.txt containing done.",
        FileContentEquals("done.txt", "done"),
    )

    observation = next(
        event.payload["observation"]
        for event in result.events
        if event.event_type == "IPYTHON_EXECUTION_RESULT"
    )
    assert observation.result.output == "x" * 32
    assert observation.result.original_output_chars == 201
    assert observation.result.truncated is True
    assert len(model.received_requests[1].context) <= 768
    assert result.status == "completed"


def test_ipython_rejects_code_over_the_declared_limit_without_starting(tmp_path):
    control = PersistentIPython(tmp_path, max_code_chars=4)

    result = control.execute("12345")

    assert result.ok is False
    assert result.error_code == "code_limit"
    assert control.is_alive is False


def test_ipython_timeout_is_explicit_and_shutdown_leaves_no_kernel(tmp_path):
    control = PersistentIPython(tmp_path, execution_timeout_seconds=0.5)
    try:
        result = control.execute("while True: pass")

        assert result.ok is False
        assert result.error_code == "timeout"
    finally:
        control.close()
    assert control.is_alive is False


def test_kernel_startup_has_an_independent_budget_before_execution_timeout(
    tmp_path, monkeypatch
):
    real_start_new_kernel = ipython_control_module.start_new_kernel
    observed = {}

    def delayed_start_new_kernel(*, startup_timeout, **kwargs):
        observed["startup_timeout"] = startup_timeout
        time.sleep(0.75)
        return real_start_new_kernel(
            startup_timeout=startup_timeout,
            **kwargs,
        )

    monkeypatch.setattr(
        ipython_control_module,
        "start_new_kernel",
        delayed_start_new_kernel,
    )
    control = PersistentIPython(
        tmp_path,
        kernel_startup_timeout_seconds=10,
        execution_timeout_seconds=0.5,
    )
    try:
        result = control.execute("while True: pass")
    finally:
        control.close()

    assert observed["startup_timeout"] == 10
    assert result.error_code == "timeout"
    assert control.is_alive is False


def test_kernel_readiness_failure_is_explicit_and_leaves_no_kernel(
    tmp_path, monkeypatch
):
    def fail_readiness(*, startup_timeout, **kwargs):
        assert startup_timeout == 3
        raise RuntimeError("readiness failed")

    monkeypatch.setattr(
        ipython_control_module,
        "start_new_kernel",
        fail_readiness,
    )
    control = PersistentIPython(
        tmp_path,
        kernel_startup_timeout_seconds=3,
        execution_timeout_seconds=0.5,
    )

    result = control.execute("print('never executed')")

    assert result.ok is False
    assert result.error_code == "kernel_startup_error"
    assert result.error == "RuntimeError: readiness failed"
    assert control.is_alive is False
    control.close()
    assert control.is_alive is False


def test_failed_kernel_shutdown_remains_visible_and_can_be_retried(tmp_path):
    control = PersistentIPython(tmp_path)
    manager = _RetryableShutdownManager()
    control._manager = manager

    with pytest.raises(RuntimeError, match="shutdown failed"):
        control.close()

    assert control.is_alive is True
    manager.fail_shutdown = False
    control.close()
    assert control.is_alive is False
    assert manager.cleanup_calls == 2


def test_restart_preserves_execution_but_starts_a_fresh_namespace(tmp_path):
    log_path = tmp_path / "execution.jsonl"
    first_model = _IPythonScriptedModel(
        [IPythonCode("x = 41"), Wait("CONTINUE")]
    )
    before = RootAgentProcess(
        first_model,
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=4,
        event_log=EventLog(log_path),
    )

    waiting = before.run(
        "Create done.txt after CONTINUE.",
        FileContentEquals("done.txt", "done"),
    )
    execution_id = waiting.state.execution_id
    root_actor_id = waiting.state.root_actor_id
    assert waiting.status == "waiting"
    assert before._ipython_control.is_alive is True
    first_manager = before._ipython_control._manager
    del before
    gc.collect()
    assert first_manager.is_alive() is False

    second_control = PersistentIPython(tmp_path)
    second_model = _IPythonScriptedModel(
        [
            IPythonCode(
                "from pathlib import Path\n"
                "print('x' in globals())\n"
                "_ = Path('done.txt').write_text('done', encoding='utf-8')"
            ),
            ClaimComplete(),
        ]
    )
    after = RootAgentProcess(
        second_model,
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=4,
        event_log=EventLog.load(log_path),
        ipython_control=second_control,
    ).deliver_event("CONTINUE")

    assert after.status == "completed"
    assert after.state.execution_id == execution_id
    assert after.state.root_actor_id == root_actor_id
    assert second_model.received_requests[0].source_event_refs
    restarted_observation = [
        event.payload["observation"]
        for event in after.events
        if event.event_type == "IPYTHON_EXECUTION_RESULT"
    ][-1]
    assert restarted_observation.result.output == "False\n"
    assert after.state == fold_execution_state(after.events)
    assert second_control.is_alive is False


def test_deepseek_ipython_mode_exposes_only_the_programmable_surface():
    payloads = []

    def transport(payload):
        payloads.append(payload)
        return _tool_response(
            "call_ipython",
            "ipython",
            json.dumps({"code": "print(42)"}),
        )

    decision = DeepSeekModel(
        tool_mode="ipython",
        transport=transport,
    ).decide(ModelRequest("{}", IPYTHON_TOOL_CONTRACTS, ("event-1",)))

    assert isinstance(decision, NativeModelDecision)
    assert decision.action == IPythonCode("print(42)")
    assert decision.provider_tool_call_id == "call_ipython"
    assert [tool["function"]["name"] for tool in payloads[0]["tools"]] == [
        "ipython",
        "wait",
        "claim_complete",
    ]
    assert payloads[0]["messages"][0]["content"] == (
        "Use the persistent IPython environment to inspect and modify the "
        "workspace. Use claim_complete when the task is finished."
    )
    assert decision.provider_wire_request == payloads[0]


def test_runtime_preserves_ipython_call_id_into_native_continuation(tmp_path):
    responses = iter(
        [
            _tool_response(
                "call_code",
                "ipython",
                json.dumps(
                    {
                        "code": (
                            "from pathlib import Path\n"
                            "_ = Path('done.txt').write_text("
                            "'done', encoding='utf-8')"
                        )
                    }
                ),
            ),
            _tool_response("call_complete", "claim_complete", "{}"),
        ]
    )
    payloads = []

    def transport(payload):
        payloads.append(payload)
        return next(responses)

    control = PersistentIPython(tmp_path)
    result = RootAgentProcess(
        DeepSeekModel(tool_mode="ipython", transport=transport),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        ipython_control=control,
    ).run(
        "Create done.txt containing done.",
        FileContentEquals("done.txt", "done"),
    )

    frame = result.decision_frames[0]
    observation = next(
        event.payload["observation"]
        for event in result.events
        if event.event_type == "IPYTHON_EXECUTION_RESULT"
    )
    assistant_call = payloads[1]["messages"][-2]["tool_calls"][0]
    tool_result = payloads[1]["messages"][-1]
    assert result.status == "completed"
    assert (
        frame.provider_tool_call_id
        == observation.provider_tool_call_id
        == assistant_call["id"]
        == tool_result["tool_call_id"]
        == "call_code"
    )
    assert tool_result["role"] == "tool"
    assert json.loads(tool_result["content"])["observation"]["type"] == (
        "ipython_execution"
    )
    assert frame.resulting_action.__class__ is IPythonCode
    assert frame.actual_tools_exposed == IPYTHON_TOOL_CONTRACTS
    assert control.is_alive is False


def test_ipython_siblings_share_one_kernel_and_return_in_model_order(tmp_path):
    (tmp_path / "done.txt").write_text("done", encoding="utf-8")
    responses = iter(
        [
            _tool_calls_response(
                (
                    "call_set",
                    "ipython",
                    json.dumps({"code": "shared_value = 41"}),
                ),
                (
                    "call_use",
                    "ipython",
                    json.dumps({"code": "print(shared_value + 1)"}),
                ),
            ),
            _tool_response("call_complete", "claim_complete", "{}"),
        ]
    )
    payloads = []

    def transport(payload):
        payloads.append(payload)
        return next(responses)

    control = PersistentIPython(tmp_path)
    result = RootAgentProcess(
        DeepSeekModel(tool_mode="ipython", transport=transport),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        ipython_control=control,
    ).run("Use one Python session.", FileContentEquals("done.txt", "done"))

    settled = [
        event
        for event in result.events
        if event.event_type == "IPYTHON_EXECUTION_RESULT"
    ]
    starts = [
        event
        for event in result.events
        if event.event_type == "IPYTHON_EXECUTION_STARTED"
    ]
    frame = result.decision_frames[0]
    assistant = payloads[1]["messages"][-3]
    tool_results = payloads[1]["messages"][-2:]

    assert result.status == "completed"
    assert frame.provider_tool_call_id == ("call_set", "call_use")
    assert all(isinstance(action, IPythonCode) for action in frame.resulting_action)
    assert [event.source_event_refs for event in starts] == [
        (result.events[1].event_id,),
        (result.events[1].event_id,),
    ]
    assert settled[1].payload["observation"].result.output == "42\n"
    assert [call["id"] for call in assistant["tool_calls"]] == [
        "call_set",
        "call_use",
    ]
    assert [message["tool_call_id"] for message in tool_results] == [
        "call_set",
        "call_use",
    ]
    assert result.state == fold_execution_state(result.events)
    assert control.is_alive is False


_RUN_REAL = (
    os.environ.get("RUN_DEEPSEEK_REAL_TESTS") == "1"
    and bool(os.environ.get("DEEPSEEK_API_KEY"))
)


def _conditional_fixture(workspace):
    (workspace / "config.txt").write_text("mode=A", encoding="utf-8")
    (workspace / "a.txt").write_text("ALPHA", encoding="utf-8")
    (workspace / "b.txt").write_text("BETA", encoding="utf-8")
    return (
        "Read config.txt. If it contains mode=A, copy the exact content of "
        "a.txt to output.txt; if it contains mode=B, copy b.txt instead.",
        FileContentEquals("output.txt", "ALPHA"),
        workspace / "output.txt",
        "ALPHA",
    )


def _aggregation_fixture(workspace):
    for number in range(1, 19):
        (workspace / f"data-{number:02d}.txt").write_text(
            str(number),
            encoding="utf-8",
        )
    return (
        "Among data-*.txt, sum the integers from files whose integer is "
        "divisible by 3. Write only the decimal total to answer.txt.",
        FileContentEquals("answer.txt", "63"),
        workspace / "answer.txt",
        "63",
    )


@pytest.mark.skipif(
    not _RUN_REAL,
    reason="set RUN_DEEPSEEK_REAL_TESTS=1 and DEEPSEEK_API_KEY",
)
def test_real_deepseek_historical_multi_tool_blocker_three_runs(tmp_path):
    summaries = []
    for run_number in range(1, 4):
        workspace = tmp_path / f"multi-tool-{run_number}"
        workspace.mkdir()
        goal, spec, expected_path, expected_content = _aggregation_fixture(
            workspace
        )
        result = RootAgentProcess(
            DeepSeekModel(),
            ToolHost(SharedEnvironment(workspace)),
            max_decisions=10,
            max_context_chars=2_000,
        ).run(goal, spec)
        decision_events = [
            event
            for event in result.events
            if event.event_type == "MODEL_DECISION"
        ]
        multi_decisions = []
        for index, frame in enumerate(result.decision_frames):
            response = frame.raw_provider_response
            if not isinstance(response, Mapping):
                continue
            calls = response["choices"][0]["message"].get("tool_calls", [])
            if len(calls) <= 1:
                continue
            call_ids = [call["id"] for call in calls]
            decision_event = decision_events[index]
            started_ids = [
                event.payload["provider_tool_call_id"]
                for event in result.events
                if event.event_type
                in ("TOOL_CALL_STARTED", "IPYTHON_EXECUTION_STARTED")
                and event.source_event_refs == (decision_event.event_id,)
            ]
            settled_ids = [
                event.payload["observation"].provider_tool_call_id
                for event in result.events
                if event.event_type
                in (
                    "TOOL_RESULT",
                    "TOOL_FAILED",
                    "IPYTHON_EXECUTION_RESULT",
                    "IPYTHON_EXECUTION_FAILED",
                )
                and event.payload["observation"].provider_tool_call_id
                in call_ids
            ]
            assert started_ids == call_ids
            assert settled_ids == call_ids
            multi_decisions.append(
                {
                    "tool_count": len(calls),
                    "call_ids": call_ids,
                    "tools": [
                        call["function"]["name"] for call in calls
                    ],
                    "arguments": [
                        json.loads(call["function"]["arguments"])
                        for call in calls
                    ],
                    "execution_order": started_ids,
                }
            )
        summaries.append(
            {
                "run": run_number,
                "multi_decisions": multi_decisions,
                "status": result.status,
                "failure": result.failure,
                "completion_verified": (
                    result.status == "completed"
                    and expected_path.is_file()
                    and expected_path.read_text(encoding="utf-8")
                    == expected_content
                ),
            }
        )
        assert result.state == fold_execution_state(result.events)
        assert result.failure != "model_protocol:multiple_tool_calls"

    print("MULTI_TOOL_FRESH " + json.dumps(summaries, sort_keys=True))


@pytest.mark.skipif(
    not _RUN_REAL,
    reason="set RUN_DEEPSEEK_REAL_TESTS=1 and DEEPSEEK_API_KEY",
)
def test_real_deepseek_native_vs_persistent_ipython_three_runs_each(tmp_path):
    summaries = []
    for task_name, fixture in (
        ("conditional", _conditional_fixture),
        ("aggregation", _aggregation_fixture),
    ):
        for arm in ("native", "ipython"):
            for run_number in range(1, 4):
                workspace = tmp_path / f"{task_name}-{arm}-{run_number}"
                workspace.mkdir()
                goal, spec, expected_path, expected_content = fixture(workspace)
                control = (
                    PersistentIPython(workspace)
                    if arm == "ipython"
                    else None
                )
                process = RootAgentProcess(
                    DeepSeekModel(tool_mode=arm),
                    ToolHost(SharedEnvironment(workspace)),
                    max_decisions=10,
                    max_context_chars=2_000,
                    ipython_control=control,
                )
                started = time.perf_counter()
                try:
                    result = process.run(goal, spec)
                    elapsed = time.perf_counter() - started
                finally:
                    process.close()
                assert result.state == fold_execution_state(result.events)
                if control is not None:
                    assert control.is_alive is False
                summaries.append(
                    {
                        "task": task_name,
                        "arm": arm,
                        "run": run_number,
                        **_ab_metrics(
                            result,
                            elapsed,
                            expected_path,
                            expected_content,
                        ),
                    }
                )

    print("DEEPSEEK_IPYTHON_AB=" + json.dumps(summaries, sort_keys=True))
    assert len(summaries) == 12
