import json
from dataclasses import replace

import pytest

from Execution_lab2.deepseek_model import DeepSeekModel
from Execution_lab2.execution import (
    AgentProcess,
    CHILD_IPYTHON_TOOL_CONTRACTS,
    ChildRef,
    ClaimComplete,
    EventLog,
    FileContentEquals,
    IPythonCode,
    IPythonObservation,
    ROOT_IPYTHON_TOOL_CONTRACTS,
    ReadRequest,
    Return,
    ScriptedModel,
    SharedEnvironment,
    SpawnChild,
    ToolHost,
    ToolCall,
    WriteRequest,
    fold_execution_state,
)


def test_spawn_returns_one_durable_child_handle_not_an_answer(tmp_path):
    runtime = AgentProcess(
        model=ScriptedModel([SpawnChild("inspect facts.txt")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )

    spawned = runtime.run(
        "use one child, then finish",
        FileContentEquals("answer.txt", "42"),
    )

    child_ref = spawned.child_ref
    assert spawned.status == "child_pending"
    assert isinstance(child_ref, ChildRef)
    assert child_ref.parent_actor_id == spawned.state.root_actor_id
    assert child_ref.child_actor_id != spawned.state.root_actor_id
    assert child_ref.local_goal == "inspect facts.txt"
    assert not hasattr(child_ref, "local_result")
    assert [event.event_type for event in spawned.events][-1] == "CHILD_SPAWNED"
    assert EventLog.load(tmp_path / "root.jsonl").events == spawned.events


def test_child_is_an_independent_process_with_shared_environment(tmp_path):
    (tmp_path / "facts.txt").write_text("VALUE=42", encoding="utf-8")
    root = AgentProcess(
        model=ScriptedModel(
            [
                SpawnChild("read facts.txt and return only its VALUE"),
                ToolCall(ReadRequest("child-evidence.txt")),
                ClaimComplete(),
            ]
        ),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    spawned = root.run(
        "ROOT_CONTEXT_SENTINEL use one child",
        FileContentEquals("child-evidence.txt", "observed 42"),
    )
    child_model = ScriptedModel(
        [
            ToolCall(WriteRequest("child-evidence.txt", "observed 42")),
            Return("42"),
        ],
        identifier="child-model",
    )

    child = AgentProcess.for_child(
        spawned.child_ref,
        model=child_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
    )
    returned = child.run_child()
    completed_root = root.accept_child(returned)

    assert returned.status == "completed"
    assert completed_root.status == "completed"
    assert returned.output == "42"
    assert returned.state.actor_id == spawned.child_ref.child_actor_id
    assert returned.state.parent_actor_id == spawned.state.root_actor_id
    assert returned.state.execution_id == spawned.child_ref.child_execution_id
    assert returned.events[-1].event_type == "CHILD_RETURNED"
    assert returned.decision_frames[0].goal == spawned.child_ref.local_goal
    assert "ROOT_CONTEXT_SENTINEL" not in returned.decision_frames[0].actual_request.context
    assert "return(local_result: str)" in child_model.received_requests[0].available_tools
    assert all(
        "spawn_child" not in tool and "claim_complete" not in tool
        for tool in child_model.received_requests[0].available_tools
    )
    assert (tmp_path / "child-evidence.txt").read_text(encoding="utf-8") == (
        "observed 42"
    )
    root_read = next(
        step.observation
        for step in completed_root.steps
        if isinstance(step.action, ToolCall)
        and isinstance(step.action.request, ReadRequest)
    )
    assert root_read.result.output == "observed 42"
    assert EventLog.load(spawned.child_ref.event_log_path).events == returned.events


def test_child_return_is_delivered_once_then_root_continues_and_completes(tmp_path):
    root_model = ScriptedModel(
        [
            SpawnChild("obtain the VALUE"),
            ToolCall(WriteRequest("answer.txt", "42")),
            ClaimComplete(),
        ]
    )
    root = AgentProcess(
        model=root_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=4,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    spawned = root.run(
        "write the Child's value",
        FileContentEquals("answer.txt", "42"),
    )
    child = AgentProcess.for_child(
        spawned.child_ref,
        model=ScriptedModel([Return("42")], identifier="child-model"),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
    )

    returned = child.run_child()
    completed = root.accept_child(returned)

    assert completed.status == "completed"
    assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "42"
    assert [event.event_type for event in completed.events].count(
        "CHILD_RETURNED"
    ) == 1
    returned_event = next(
        event
        for event in completed.events
        if event.event_type == "CHILD_RETURNED"
    )
    spawned_event = next(
        event
        for event in completed.events
        if event.event_type == "CHILD_SPAWNED"
    )
    assert returned_event.source_event_refs == (spawned_event.event_id,)
    assert '"status":"returned"' in root_model.received_requests[1].context
    assert '"local_result":{"text":"42"' in root_model.received_requests[1].context
    assert EventLog.load(tmp_path / "root.jsonl").events == completed.events


def test_root_and_child_terminal_authority_is_role_limited(tmp_path):
    root_return = AgentProcess(
        model=ScriptedModel([Return("not allowed")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    ).run("Root cannot Return", FileContentEquals("unused.txt", "unused"))
    root_for_child = AgentProcess(
        model=ScriptedModel([SpawnChild("test Child authority")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(tmp_path / "authority-root.jsonl"),
    ).run("create the Child", FileContentEquals("unused.txt", "unused"))
    child_claim = AgentProcess.for_child(
        root_for_child.child_ref,
        model=ScriptedModel([ClaimComplete()]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(),
    ).run_child()
    second_ref = ChildRef(
        child_execution_id="execution-child-2",
        child_actor_id="child-2",
        parent_actor_id=root_for_child.state.root_actor_id,
        local_goal="cannot recurse",
        event_log_path=None,
    )
    child_spawn = AgentProcess.for_child(
        second_ref,
        model=ScriptedModel([SpawnChild("grandchild")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(),
    ).run_child()

    assert root_return.status == child_claim.status == child_spawn.status == "failed"
    assert root_return.failure == "unauthorized_action:Return"
    assert child_claim.failure == "unauthorized_action:ClaimComplete"
    assert child_spawn.failure == "unauthorized_action:SpawnChild"
    assert all(
        "CHILD_SPAWNED"
        not in [event.event_type for event in result.events]
        for result in (child_claim, child_spawn)
    )


def test_root_can_spawn_a_second_sibling_after_the_first_returns(tmp_path):
    root = AgentProcess(
        model=ScriptedModel(
            [SpawnChild("first"), SpawnChild("second")]
        ),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    first = root.run(
        "one Child only",
        FileContentEquals("unused.txt", "unused"),
    )
    returned = AgentProcess.for_child(
        first.child_ref,
        model=ScriptedModel([Return("done")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    ).run_child()

    second = root.accept_child(returned)

    assert second.status == "child_pending"
    assert second.failure is None
    assert [child.local_goal for child in second.state.child_refs] == [
        "first",
        "second",
    ]
    assert [event.event_type for event in second.events].count(
        "CHILD_SPAWNED"
    ) == 2


def test_child_failure_becomes_one_root_observation_without_retry(tmp_path):
    (tmp_path / "answer.txt").write_text("ready", encoding="utf-8")
    root_model = ScriptedModel(
        [SpawnChild("fail explicitly"), ClaimComplete()]
    )
    root = AgentProcess(
        model=root_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    spawned = root.run(
        "observe the Child failure",
        FileContentEquals("answer.txt", "ready"),
    )
    child_model = ScriptedModel([{"unexpected": "decision"}])
    child = AgentProcess.for_child(
        spawned.child_ref,
        model=child_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    )

    failed_child = child.run_child()
    completed_root = root.accept_child(failed_child)

    assert failed_child.status == "failed"
    assert failed_child.failure == "unknown_action:dict"
    assert completed_root.status == "completed"
    assert [event.event_type for event in completed_root.events].count(
        "CHILD_FAILED"
    ) == 1
    assert [event.event_type for event in completed_root.events].count(
        "CHILD_SPAWNED"
    ) == 1
    assert len(child_model.received_requests) == 1
    assert '"status":"failed"' in root_model.received_requests[1].context
    assert "unknown_action:dict" in root_model.received_requests[1].context


def test_restart_after_return_does_not_respawn_or_redeliver(tmp_path):
    class CrashBeforeNextRootDecision:
        identifier = "crashing-root"

        def __init__(self):
            self.received_requests = []

        def decide(self, request):
            self.received_requests.append(request)
            if len(self.received_requests) == 1:
                return SpawnChild("return 42")
            raise SystemExit("crash after durable Child return")

    root_log_path = tmp_path / "root.jsonl"
    crashing_model = CrashBeforeNextRootDecision()
    root = AgentProcess(
        model=crashing_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=4,
        event_log=EventLog(root_log_path),
    )
    spawned = root.run(
        "survive Child delivery",
        FileContentEquals("answer.txt", "42"),
    )
    original_execution_id = spawned.state.execution_id
    original_root_actor_id = spawned.state.root_actor_id
    child = AgentProcess.for_child(
        spawned.child_ref,
        model=ScriptedModel([Return("42")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    )
    returned = child.run_child()

    with pytest.raises(SystemExit, match="crash after durable Child return"):
        root.accept_child(returned)

    restored_model = ScriptedModel(
        [
            ToolCall(WriteRequest("answer.txt", "42")),
            ClaimComplete(),
        ]
    )
    restored = AgentProcess(
        model=restored_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=4,
        event_log=EventLog.load(root_log_path),
    )
    with pytest.raises(ValueError, match="no pending Child"):
        restored.accept_child(returned)
    completed = restored.resume()

    event_types = [event.event_type for event in completed.events]
    assert completed.status == "completed"
    assert event_types.count("CHILD_SPAWNED") == 1
    assert event_types.count("CHILD_RETURNED") == 1
    assert completed.state.execution_id == original_execution_id
    assert completed.state.root_actor_id == original_root_actor_id
    assert completed.state == fold_execution_state(completed.events)
    assert len(restored_model.received_requests) == 2


def test_parent_uses_only_canonical_child_history_for_delivery(tmp_path):
    root = AgentProcess(
        model=ScriptedModel(
            [
                SpawnChild("return canonical"),
                ToolCall(WriteRequest("answer.txt", "canonical")),
                ClaimComplete(),
            ]
        ),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    spawned = root.run(
        "reject forged Child result fields",
        FileContentEquals("answer.txt", "canonical"),
    )
    child_result = AgentProcess.for_child(
        spawned.child_ref,
        model=ScriptedModel([Return("canonical")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    ).run_child()

    with pytest.raises(ValueError, match="canonical Child history"):
        root.accept_child(replace(child_result, output="forged"))

    assert EventLog.load(tmp_path / "root.jsonl").events[-1].event_type == (
        "CHILD_SPAWNED"
    )
    completed = root.accept_child(child_result)
    assert completed.status == "completed"
    return_index = next(
        index
        for index, event in enumerate(completed.events)
        if event.event_type == "CHILD_RETURNED"
    )
    returned_state = fold_execution_state(
        completed.events[: return_index + 1]
    )
    assert returned_state.latest_observation.local_result == "canonical"


def test_spawn_requires_a_durable_parent_and_child_log(tmp_path):
    result = AgentProcess(
        model=ScriptedModel([SpawnChild("cannot be durable")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    ).run(
        "do not admit an in-memory Child",
        FileContentEquals("unused.txt", "unused"),
    )

    assert result.status == "failed"
    assert result.failure == "child_persistence_required"
    assert "CHILD_SPAWNED" not in [
        event.event_type for event in result.events
    ]


def test_deepseek_adapter_exposes_and_parses_role_specific_actions(tmp_path):
    root_payloads = []

    def root_transport(payload):
        root_payloads.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "root-spawn-1",
                                "type": "function",
                                "function": {
                                    "name": "ipython",
                                    "arguments": json.dumps(
                                        {
                                            "code": (
                                                "await spawn_child("
                                                "'read facts.txt')"
                                            )
                                        }
                                    ),
                                },
                            }
                        ]
                    }
                }
            ]
        }

    root = AgentProcess(
        model=DeepSeekModel(transport=root_transport),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    spawned = root.run(
        "use one Child",
        FileContentEquals("answer.txt", "42"),
    )
    child_payloads = []

    def child_transport(payload):
        child_payloads.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "child-return-1",
                                "type": "function",
                                "function": {
                                    "name": "return",
                                    "arguments": '{"local_result":"42"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }

    child = AgentProcess.for_child(
        spawned.child_ref,
        model=DeepSeekModel(transport=child_transport),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    )
    returned = child.run_child()

    root_tools = {
        tool["function"]["name"] for tool in root_payloads[0]["tools"]
    }
    child_tools = {
        tool["function"]["name"] for tool in child_payloads[0]["tools"]
    }
    assert returned.output == "42"
    assert "ipython" in root_tools
    assert "spawn_child" not in root_tools
    assert "return" not in root_tools
    assert "return" in child_tools
    assert "spawn_child" not in child_tools
    assert "claim_complete" not in child_tools


def test_root_and_child_have_separate_live_ipython_namespaces(tmp_path):
    from Execution_lab2.ipython_control import PersistentIPython

    root_model = ScriptedModel(
        [
            IPythonCode("root_only = 'root'"),
            SpawnChild("set child_only and report Root namespace visibility"),
            IPythonCode("print(root_only, 'child_only' in globals())"),
            ToolCall(WriteRequest("answer.txt", "isolated")),
            ClaimComplete(),
        ]
    )
    root_model.tool_contracts = ROOT_IPYTHON_TOOL_CONTRACTS
    root_control = PersistentIPython(tmp_path)
    child_control = PersistentIPython(tmp_path)
    try:
        root = AgentProcess(
            model=root_model,
            tools=ToolHost(SharedEnvironment(tmp_path)),
            max_decisions=5,
            event_log=EventLog(tmp_path / "root.jsonl"),
            ipython_control=root_control,
        )
        spawned = root.run(
            "prove independent live namespaces",
            FileContentEquals("answer.txt", "isolated"),
        )
        child_model = ScriptedModel(
            [
                IPythonCode(
                    "child_only = 'child'; "
                    "print('root_only' in globals())"
                ),
                Return("namespaces isolated"),
            ]
        )
        child_model.tool_contracts = CHILD_IPYTHON_TOOL_CONTRACTS
        child = AgentProcess.for_child(
            spawned.child_ref,
            model=child_model,
            tools=ToolHost(SharedEnvironment(tmp_path)),
            max_decisions=2,
            ipython_control=child_control,
        )

        child_result = child.run_child()
        root_result = root.accept_child(child_result)

        child_python = next(
            step.observation
            for step in child_result.steps
            if isinstance(step.observation, IPythonObservation)
        )
        root_python = [
            step.observation
            for step in root_result.steps
            if isinstance(step.observation, IPythonObservation)
        ]
        assert child_python.result.output.strip() == "False"
        assert root_python[0].result.output.strip() == "root False"
        assert root_result.status == "completed"
    finally:
        child_control.close()
        root_control.close()

    assert child_control.is_alive is False
    assert root_control.is_alive is False
