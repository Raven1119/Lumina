import json

import pytest

from Execution_lab2.execution import (
    AgentProcess,
    CHILD_IPYTHON_TOOL_CONTRACTS,
    ClaimComplete,
    EventLog,
    FileContentEquals,
    IPYTHON_TOOL_CONTRACTS,
    IPythonCode,
    Return,
    RootAgentProcess,
    ScriptedModel,
    SharedEnvironment,
    ToolHost,
    fold_execution_state,
)
from Execution_lab2.deepseek_model import DeepSeekModel
from Execution_lab2.ipython_control import PersistentIPython


class _SingleChildAgentProcess(AgentProcess):
    _max_children_per_root = 1


def test_spawn_child_callable_exists_only_with_the_host_bridge(tmp_path):
    root_only = PersistentIPython(tmp_path)
    try:
        absent = root_only.execute("print('spawn_child' in globals())")
        assert absent.ok is True
        assert absent.output.strip() == "False"
    finally:
        root_only.close()

    host_requests = []

    def admit(goal):
        host_requests.append(goal)
        return {"child_actor_id": "child-1", "depth": 1}

    delegated = PersistentIPython(tmp_path, spawn_child_handler=admit)
    try:
        result = delegated.execute(
            "handle = await spawn_child('inspect packet A')\n"
            "print(handle['child_actor_id'], handle['depth'])"
        )
        assert result.ok is True
        assert result.output.strip() == "child-1 1"
        assert host_requests == ["inspect packet A"]
    finally:
        delegated.close()


def test_ipython_spawn_uses_the_existing_child_and_return_lifecycle(tmp_path):
    root_model = ScriptedModel(
        [
            IPythonCode(
                "handle = await spawn_child('inspect packet A')\n"
                "print(handle['child_actor_id'])"
            ),
            ClaimComplete(),
        ]
    )
    root_model.tool_contracts = IPYTHON_TOOL_CONTRACTS
    root = _SingleChildAgentProcess(
        model=root_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        max_depth=1,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )

    spawned = root.run(
        "write the returned value",
        FileContentEquals("answer.txt", "42"),
    )

    assert spawned.status == "child_pending"
    assert spawned.child_ref.local_goal == "inspect packet A"
    assert spawned.child_ref.depth == 1
    assert spawned.child_ref.parent_actor_id == spawned.state.root_actor_id
    assert root_model.received_requests[0].available_tools == IPYTHON_TOOL_CONTRACTS
    assert "spawn_child(goal)" in root_model.received_requests[0].context
    event_types = [event.event_type for event in spawned.events]
    assert event_types[-3:] == [
        "IPYTHON_EXECUTION_STARTED",
        "CHILD_SPAWNED",
        "IPYTHON_EXECUTION_RESULT",
    ]
    ipython_start = spawned.events[-3]
    child_spawned = spawned.events[-2]
    assert child_spawned.source_event_refs == (ipython_start.event_id,)

    child_model = ScriptedModel(
        [
            IPythonCode("open('answer.txt', 'w').write('42')"),
            Return("42"),
        ],
        identifier="child-model",
    )
    child_model.tool_contracts = CHILD_IPYTHON_TOOL_CONTRACTS
    child = _SingleChildAgentProcess.for_child(
        spawned.child_ref,
        model=child_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
    )

    returned = child.run_child()
    root.close()
    restored_root = _SingleChildAgentProcess(
        model=root_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        max_depth=1,
        event_log=EventLog.load(tmp_path / "root.jsonl"),
    )
    completed = restored_root.accept_child(returned)

    assert returned.status == "completed"
    assert completed.status == "completed"
    assert completed.state == fold_execution_state(completed.events)
    assert EventLog.load(tmp_path / "root.jsonl").events == completed.events
    assert "spawn_child" not in child_model.received_requests[0].context
    assert all(
        "spawn_child" not in contract
        for contract in child_model.received_requests[0].available_tools
    )


def test_ipython_spawn_preserves_single_child_and_depth_bounds(tmp_path):
    root_model = ScriptedModel(
        [
            IPythonCode("await spawn_child('first')"),
            IPythonCode("await spawn_child('second')"),
            ClaimComplete(),
        ]
    )
    root_model.tool_contracts = IPYTHON_TOOL_CONTRACTS
    root = _SingleChildAgentProcess(
        model=root_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        max_depth=1,
        event_log=EventLog(tmp_path / "bounded-root.jsonl"),
    )
    first = root.run("use at most one Child", FileContentEquals("answer.txt", "42"))
    child_model = ScriptedModel(
        [
            IPythonCode("print('spawn_child' in globals())"),
            Return("42"),
        ]
    )
    child_model.tool_contracts = CHILD_IPYTHON_TOOL_CONTRACTS
    returned = _SingleChildAgentProcess.for_child(
        first.child_ref,
        model=child_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
    ).run_child()
    (tmp_path / "answer.txt").write_text("42", encoding="utf-8")

    completed = root.accept_child(returned)

    assert completed.status == "completed"
    assert len(completed.state.child_refs) == 1
    assert [event.event_type for event in completed.events].count(
        "CHILD_SPAWNED"
    ) == 1
    assert returned.steps[0].observation.result.output.strip() == "False"
    second_spawn = completed.steps[0].observation.result
    assert second_spawn.error_code == "execution_error"
    assert "child_limit_reached" in second_spawn.error


def test_delegated_root_binds_an_injected_live_persistent_kernel(tmp_path):
    injected = PersistentIPython(tmp_path)
    absent = injected.execute("print('spawn_child' in globals())")
    assert absent.ok is True
    assert absent.output.strip() == "False"

    model = ScriptedModel([IPythonCode("await spawn_child('inspect packet A')")])
    model.tool_contracts = IPYTHON_TOOL_CONTRACTS
    root = _SingleChildAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        max_depth=1,
        event_log=EventLog(tmp_path / "injected-root.jsonl"),
        ipython_control=injected,
    )
    try:
        result = root.run(
            "inspect",
            FileContentEquals("answer.txt", "unused"),
        )
    finally:
        root.close()

    assert result.status == "child_pending"
    assert result.child_ref.local_goal == "inspect packet A"


def test_restart_rejects_a_spawn_committed_before_ipython_cell_settles(tmp_path):
    model = ScriptedModel([IPythonCode("await spawn_child('inspect packet A')")])
    model.tool_contracts = IPYTHON_TOOL_CONTRACTS
    event_log = EventLog(tmp_path / "crashed-root.jsonl")
    append = event_log.append

    def crash_before_ipython_result(event_type, payload, source_event_refs=()):
        if event_type in (
            "IPYTHON_EXECUTION_RESULT",
            "IPYTHON_EXECUTION_FAILED",
        ):
            raise SystemExit("crash before IPython settlement")
        return append(event_type, payload, source_event_refs)

    event_log.append = crash_before_ipython_result
    root = _SingleChildAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        max_depth=1,
        event_log=event_log,
    )
    try:
        with pytest.raises(SystemExit, match="IPython settlement"):
            root.run("inspect", FileContentEquals("answer.txt", "unused"))
    finally:
        root.close()

    durable = EventLog.load(tmp_path / "crashed-root.jsonl")
    assert durable.events[-1].event_type == "CHILD_SPAWNED"
    child_ref = durable.events[-1].payload["child_ref"]

    with pytest.raises(
        ValueError,
        match="interrupted IPython Child spawn recovery is unsupported",
    ):
        _SingleChildAgentProcess(
            model=ScriptedModel([]),
            tools=ToolHost(SharedEnvironment(tmp_path)),
            max_decisions=1,
            max_depth=1,
            event_log=durable,
        )
    assert EventLog.load(tmp_path / "crashed-root.jsonl").events[-1].payload[
        "child_ref"
    ] == child_ref


def test_root_arms_have_identical_provider_schemas_and_truthful_capability_context(
    tmp_path,
):
    payloads = {"A": [], "B": []}

    def transport(arm, code):
        def send(payload):
            payloads[arm].append(payload)
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": f"call-{arm}",
                                    "type": "function",
                                    "function": {
                                        "name": "ipython",
                                        "arguments": json.dumps({"code": code}),
                                    },
                                }
                            ]
                        }
                    }
                ]
            }

        return send

    root_only_workspace = tmp_path / "root-only"
    root_only_workspace.mkdir()
    root_only = RootAgentProcess(
        model=DeepSeekModel(
            transport=transport(
                "A", "print('spawn_child' in globals())"
            )
        ),
        tools=ToolHost(SharedEnvironment(root_only_workspace)),
        max_decisions=1,
        event_log=EventLog(root_only_workspace / "root.jsonl"),
    )
    root_only_result = root_only.run(
        "inspect",
        FileContentEquals("answer.txt", "unused"),
    )

    delegated_workspace = tmp_path / "delegated"
    delegated_workspace.mkdir()
    delegated = _SingleChildAgentProcess(
        model=DeepSeekModel(
            transport=transport("B", "await spawn_child('inspect packet A')")
        ),
        tools=ToolHost(SharedEnvironment(delegated_workspace)),
        max_decisions=1,
        max_depth=1,
        event_log=EventLog(delegated_workspace / "root.jsonl"),
    )
    try:
        delegated_result = delegated.run(
            "inspect",
            FileContentEquals("answer.txt", "unused"),
        )
    finally:
        delegated.close()

    a_tools = payloads["A"][0]["tools"]
    b_tools = payloads["B"][0]["tools"]
    assert a_tools == b_tools
    assert [tool["function"]["name"] for tool in a_tools] == [
        "ipython",
        "wait",
        "claim_complete",
    ]
    assert "spawn_child(goal)" not in root_only_result.decision_frames[0].actual_request.context
    assert "spawn_child(goal)" in delegated_result.decision_frames[0].actual_request.context
    assert root_only_result.steps[0].observation.result.output.strip() == "False"
    assert delegated_result.status == "child_pending"
