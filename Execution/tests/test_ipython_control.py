import gc
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import Execution.ipython_control as ipython_control_module
from Execution.deepseek_model import DeepSeekModel
from Execution.ipython_control import PersistentIPython
from Execution.execution import (
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


def test_ipython_retains_workspace_and_process_capabilities(tmp_path):
    (tmp_path / "a.txt").write_text("ALPHA", encoding="utf-8")
    (tmp_path / "b.txt").write_text("BETA", encoding="utf-8")
    control = PersistentIPython(tmp_path)
    try:
        result = control.execute(
            "from pathlib import Path\n"
            "import subprocess, sys\n"
            "paths = sorted(Path('.').glob('*.txt'))\n"
            "combined = '|'.join("
            "path.read_text(encoding='utf-8') for path in paths)\n"
            "process = subprocess.run("
            "[sys.executable, '-c', \"print('PROCESS')\"], "
            "capture_output=True, text=True, check=True)\n"
            "Path('result.txt').write_text("
            "combined + '|' + process.stdout.strip(), encoding='utf-8')"
        )

        assert result.ok is True
        assert sorted(path.name for path in tmp_path.iterdir()) == [
            "a.txt",
            "b.txt",
            "result.txt",
        ]
        assert (tmp_path / "result.txt").read_text(encoding="utf-8") == (
            "ALPHA|BETA|PROCESS"
        )
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


def test_deepseek_default_exposes_only_the_programmable_surface():
    payloads = []

    def transport(payload):
        payloads.append(payload)
        return _tool_response(
            "call_ipython",
            "ipython",
            json.dumps({"code": "print(42)"}),
        )

    decision = DeepSeekModel(transport=transport).decide(
        ModelRequest("{}", IPYTHON_TOOL_CONTRACTS, ("event-1",))
    )

    assert isinstance(decision, NativeModelDecision)
    assert decision.action == IPythonCode("print(42)")
    assert decision.provider_tool_call_id == "call_ipython"
    assert [tool["function"]["name"] for tool in payloads[0]["tools"]] == [
        "ipython",
        "wait",
        "claim_complete",
    ]
    assert payloads[0]["messages"][0]["content"] == (
        "Use the provided functions to act on the environment. "
        "Claim completion only through claim_complete."
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
        DeepSeekModel(transport=transport),
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
        DeepSeekModel(transport=transport),
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
