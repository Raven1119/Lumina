import json
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor

import pytest

from Execution.deepseek_model import DeepSeekModel
from Execution.execution import (
    AgentProcess,
    Checkpoint,
    ClaimComplete,
    ChildRef,
    EventLog,
    FileContentEquals,
    ModelRequest,
    NativeModelDecision,
    Observation,
    ReadRequest,
    Return,
    ROOT_TOOL_CONTRACTS,
    RootAgentProcess,
    ScriptedModel,
    SharedEnvironment,
    ShellRequest,
    SpawnChild,
    ToolCall,
    ToolHost,
    ToolResult,
    Wait,
    WriteRequest,
    fold_execution_state,
)


def _tool_response(call_id, name, arguments):
    return {
        "id": "chatcmpl-fixture",
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
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }


def _tool_calls_response(*calls):
    response = _tool_response(*calls[0])
    response["choices"][0]["message"]["tool_calls"] = [
        _tool_response(*call)["choices"][0]["message"]["tool_calls"][0]
        for call in calls
    ]
    return response


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class _NativeToolTestModel(DeepSeekModel):
    """Explicit test-only access to historical native tool contracts."""

    @property
    def tool_contracts(self):
        return ROOT_TOOL_CONTRACTS + ("return(local_result: str)",)


def test_two_homogeneous_spawn_calls_form_one_ordered_root_decision(tmp_path):
    response = _tool_calls_response(
        ("call_a", "spawn_child", json.dumps({"goal": "inspect a.txt"})),
        ("call_b", "spawn_child", json.dumps({"goal": "inspect b.txt"})),
    )
    result = AgentProcess(
        _NativeToolTestModel(transport=lambda payload: response),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(tmp_path / "root.jsonl"),
    ).run(
        "delegate two independent reads",
        FileContentEquals("answer.txt", "42"),
    )

    assert result.status == "child_pending"
    assert len(result.decision_frames) == 1
    frame = result.decision_frames[0]
    assert frame.resulting_action == (
        SpawnChild("inspect a.txt", "call_a"),
        SpawnChild("inspect b.txt", "call_b"),
    )
    assert frame.provider_tool_call_id == ("call_a", "call_b")
    assert [child.local_goal for child in result.state.child_refs] == [
        "inspect a.txt",
        "inspect b.txt",
    ]
    assert [child.provider_tool_call_id for child in result.state.child_refs] == [
        "call_a",
        "call_b",
    ]
    assert len({child.child_actor_id for child in result.state.child_refs}) == 2
    assert {child.parent_actor_id for child in result.state.child_refs} == {
        result.state.root_actor_id
    }
    spawned = [
        event for event in result.events if event.event_type == "CHILD_SPAWNED"
    ]
    decision_event = next(
        event for event in result.events if event.event_type == "MODEL_DECISION"
    )
    assert len(spawned) == 2
    assert all(
        event.source_event_refs == (decision_event.event_id,) for event in spawned
    )


def _child_result(child_ref, tools, local_result):
    return AgentProcess.for_child(
        child_ref,
        model=ScriptedModel([Return(local_result)]),
        tools=tools,
        max_decisions=1,
    ).run_child()


def test_three_homogeneous_spawn_calls_are_admitted_in_provider_order(tmp_path):
    response = _tool_calls_response(
        ("call_a", "spawn_child", json.dumps({"goal": "A"})),
        ("call_b", "spawn_child", json.dumps({"goal": "B"})),
        ("call_c", "spawn_child", json.dumps({"goal": "C"})),
    )
    result = AgentProcess(
        _NativeToolTestModel(transport=lambda payload: response),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(tmp_path / "root.jsonl"),
    ).run("delegate three", FileContentEquals("answer.txt", "42"))

    assert result.status == "child_pending"
    assert [child.local_goal for child in result.state.child_refs] == [
        "A",
        "B",
        "C",
    ]
    assert [
        child.provider_tool_call_id for child in result.state.child_refs
    ] == ["call_a", "call_b", "call_c"]
    assert sum(
        event.event_type == "MODEL_DECISION" for event in result.events
    ) == 1


def test_spawn_batch_over_remaining_capacity_is_rejected_before_new_identity(
    tmp_path,
):
    responses = iter(
        [
            _tool_calls_response(
                ("call_a", "spawn_child", json.dumps({"goal": "A"})),
                ("call_b", "spawn_child", json.dumps({"goal": "B"})),
            ),
            _tool_calls_response(
                ("call_c", "spawn_child", json.dumps({"goal": "C"})),
                ("call_d", "spawn_child", json.dumps({"goal": "D"})),
            ),
        ]
    )
    tools = ToolHost(SharedEnvironment(tmp_path))
    root = AgentProcess(
        _NativeToolTestModel(transport=lambda payload: next(responses)),
        tools,
        max_decisions=2,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    initial = root.run(
        "delegate within capacity",
        FileContentEquals("answer.txt", "42"),
    )
    first, second = initial.state.child_refs

    after_first = root.accept_child(_child_result(first, tools, "A"))
    result = root.accept_child(_child_result(second, tools, "B"))

    assert after_first.status == "child_pending"
    assert result.status == "failed"
    assert result.failure == "child_limit_reached"
    assert result.state.child_refs == (first, second)
    assert sum(
        event.event_type == "CHILD_SPAWNED" for event in result.events
    ) == 2


def test_duplicate_spawn_provider_id_is_rejected_before_any_child(tmp_path):
    response = _tool_calls_response(
        ("call_same", "spawn_child", json.dumps({"goal": "A"})),
        ("call_same", "spawn_child", json.dumps({"goal": "B"})),
    )
    result = AgentProcess(
        DeepSeekModel(transport=lambda payload: response),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(tmp_path / "root.jsonl"),
    ).run("reject duplicate identity", FileContentEquals("answer.txt", "42"))

    assert result.status == "failed"
    assert result.failure == "model_protocol:duplicate_tool_call_id"
    assert result.state.child_refs == ()
    assert not any(
        event.event_type == "CHILD_SPAWNED" for event in result.events
    )


def test_spawn_batch_without_provider_ids_is_rejected_before_any_child(
    tmp_path,
):
    result = AgentProcess(
        ScriptedModel(
            [
                NativeModelDecision(
                    (
                        SpawnChild("A"),
                        SpawnChild("B"),
                    ),
                    {},
                    {"fixture": "missing provider ids"},
                )
            ]
        ),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(tmp_path / "root.jsonl"),
    ).run("require batch identities", FileContentEquals("answer.txt", "42"))

    assert result.status == "failed"
    assert result.failure == "model_protocol:invalid_spawn_batch"
    assert result.state.child_refs == ()
    assert not any(
        event.event_type == "CHILD_SPAWNED" for event in result.events
    )


@pytest.mark.parametrize(
    ("second_name", "second_arguments"),
    [
        ("read", json.dumps({"path": "a.txt"})),
        ("wait", json.dumps({"event_type": "ready"})),
        ("claim_complete", "{}"),
    ],
)
def test_spawn_mixed_with_another_action_rejects_the_whole_decision(
    tmp_path,
    second_name,
    second_arguments,
):
    (tmp_path / "a.txt").write_text("17", encoding="utf-8")
    response = _tool_calls_response(
        ("call_spawn", "spawn_child", json.dumps({"goal": "A"})),
        ("call_other", second_name, second_arguments),
    )
    result = AgentProcess(
        _NativeToolTestModel(transport=lambda payload: response),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(tmp_path / "root.jsonl"),
    ).run("reject mixed controls", FileContentEquals("answer.txt", "42"))

    assert result.status == "failed"
    assert result.failure == "model_protocol:mixed_control_tool_calls"
    assert result.state.child_refs == ()
    assert not any(
        event.event_type
        in ("CHILD_SPAWNED", "TOOL_CALL_STARTED", "ROOT_WAITING")
        for event in result.events
    )


@pytest.mark.parametrize(
    ("calls", "expected_failure"),
    [
        (
            (
                ("call_a", "wait", json.dumps({"event_type": "A"})),
                ("call_b", "wait", json.dumps({"event_type": "B"})),
            ),
            "model_protocol:mixed_control_tool_calls",
        ),
        (
            (
                ("call_a", "claim_complete", "{}"),
                ("call_b", "claim_complete", "{}"),
            ),
            "model_protocol:mixed_control_tool_calls",
        ),
        (
            (
                ("call_spawn", "spawn_child", json.dumps({"goal": "A"})),
                ("call_return", "return", json.dumps({"local_result": "A"})),
            ),
            "model_protocol:unknown_tool",
        ),
    ],
)
def test_other_multi_control_responses_remain_wholly_rejected(
    tmp_path,
    calls,
    expected_failure,
):
    result = AgentProcess(
        DeepSeekModel(
            transport=lambda payload: _tool_calls_response(*calls)
        ),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(tmp_path / "root.jsonl"),
    ).run("reject control batch", FileContentEquals("answer.txt", "42"))

    assert result.status == "failed"
    assert result.failure == expected_failure
    assert result.state.child_refs == ()
    assert not any(
        event.event_type
        in ("CHILD_SPAWNED", "TOOL_CALL_STARTED", "ROOT_WAITING")
        for event in result.events
    )


def test_spawn_batch_child_results_continue_with_matching_native_call_ids(
    tmp_path,
):
    (tmp_path / "answer.txt").write_text("42", encoding="utf-8")
    responses = iter(
        [
            _tool_calls_response(
                (
                    "call_a",
                    "spawn_child",
                    json.dumps({"goal": "inspect a.txt"}),
                ),
                (
                    "call_b",
                    "spawn_child",
                    json.dumps({"goal": "inspect b.txt"}),
                ),
            ),
            _tool_response("call_claim", "claim_complete", "{}"),
        ]
    )
    payloads = []

    def transport(payload):
        payloads.append(payload)
        return next(responses)

    tools = ToolHost(SharedEnvironment(tmp_path))
    root = AgentProcess(
        _NativeToolTestModel(transport=transport),
        tools,
        max_decisions=2,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    initial = root.run(
        "delegate and integrate",
        FileContentEquals("answer.txt", "42"),
    )
    first, second = initial.state.child_refs

    after_first = root.accept_child(_child_result(first, tools, "17"))
    assert after_first.status == "child_pending"
    assert len(payloads) == 1

    result = root.accept_child(_child_result(second, tools, "25"))

    assert result.status == "completed"
    assert any(
        event.event_type == "COMPLETION_VERIFIED" for event in result.events
    )
    messages = payloads[1]["messages"]
    assistant = next(
        message for message in messages if message["role"] == "assistant"
    )
    tool_messages = [
        message for message in messages if message["role"] == "tool"
    ]
    assert [
        call["id"] for call in assistant["tool_calls"]
    ] == ["call_a", "call_b"]
    assert [
        message["tool_call_id"] for message in tool_messages
    ] == ["call_a", "call_b"]
    assert [
        json.loads(message["content"])["observation"]["result"]["local_result"][
            "text"
        ]
        for message in tool_messages
    ] == ["17", "25"]


def test_restart_preserves_spawn_batch_identities_and_waits_for_all_results(
    tmp_path,
):
    (tmp_path / "answer.txt").write_text("42", encoding="utf-8")
    first_response = _tool_calls_response(
        ("call_a", "spawn_child", json.dumps({"goal": "A"})),
        ("call_b", "spawn_child", json.dumps({"goal": "B"})),
    )
    root_path = tmp_path / "root.jsonl"
    tools = ToolHost(SharedEnvironment(tmp_path))
    initial = AgentProcess(
        _NativeToolTestModel(transport=lambda payload: first_response),
        tools,
        max_decisions=2,
        event_log=EventLog(root_path),
    ).run("restart a batch", FileContentEquals("answer.txt", "42"))
    original_refs = initial.state.child_refs
    original_execution_id = initial.state.execution_id
    original_root_actor_id = initial.state.root_actor_id
    continuation_payloads = []

    def continuation_transport(payload):
        continuation_payloads.append(payload)
        return _tool_response("call_claim", "claim_complete", "{}")

    restored_root = AgentProcess(
        _NativeToolTestModel(transport=continuation_transport),
        tools,
        max_decisions=2,
        event_log=EventLog.load(root_path),
    )
    restored = restored_root.resume()

    assert restored.status == "child_pending"
    assert restored.state.child_refs == original_refs
    assert restored.state.execution_id == original_execution_id
    assert restored.state.root_actor_id == original_root_actor_id
    assert continuation_payloads == []
    assert sum(
        event.event_type == "CHILD_SPAWNED" for event in restored.events
    ) == 2

    after_first = restored_root.accept_child(
        _child_result(original_refs[0], tools, "17")
    )
    assert after_first.status == "child_pending"
    assert continuation_payloads == []

    result = restored_root.accept_child(
        _child_result(original_refs[1], tools, "25")
    )

    assert result.status == "completed"
    assert result.state.child_refs == original_refs
    assert fold_execution_state(result.events) == result.state
    assert len(continuation_payloads) == 1
    assert [
        message["tool_call_id"]
        for message in continuation_payloads[0]["messages"]
        if message["role"] == "tool"
    ] == ["call_a", "call_b"]


def test_restart_finishes_only_the_uncommitted_spawn_batch_suffix(tmp_path):
    class CrashBeforeSecondSpawn(EventLog):
        def __init__(self, path):
            super().__init__(path)
            self.spawn_count = 0

        def append(self, event_type, payload, source_event_refs=()):
            if event_type == "CHILD_SPAWNED":
                self.spawn_count += 1
                if self.spawn_count == 2:
                    raise RuntimeError("crash before second spawn append")
            return super().append(event_type, payload, source_event_refs)

    response = _tool_calls_response(
        ("call_a", "spawn_child", json.dumps({"goal": "A"})),
        ("call_b", "spawn_child", json.dumps({"goal": "B"})),
    )
    root_path = tmp_path / "root.jsonl"
    with pytest.raises(RuntimeError, match="before second spawn"):
        AgentProcess(
            _NativeToolTestModel(transport=lambda payload: response),
            ToolHost(SharedEnvironment(tmp_path)),
            max_decisions=2,
            event_log=CrashBeforeSecondSpawn(root_path),
        ).run(
            "survive partial batch admission",
            FileContentEquals("answer.txt", "42"),
        )

    crashed = EventLog.load(root_path)
    first_ref = next(
        event.payload["child_ref"]
        for event in crashed.events
        if event.event_type == "CHILD_SPAWNED"
    )
    model_calls = []
    restored_root = AgentProcess(
        DeepSeekModel(
            transport=lambda payload: model_calls.append(payload)
            or _tool_response("call_claim", "claim_complete", "{}")
        ),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=crashed,
    )
    restored = restored_root.resume()

    assert restored.status == "child_pending"
    assert len(restored.state.child_refs) == 2
    assert restored.state.child_refs[0] == first_ref
    assert [
        child.provider_tool_call_id for child in restored.state.child_refs
    ] == ["call_a", "call_b"]
    assert len(
        {
            child.child_execution_id
            for child in restored.state.child_refs
        }
    ) == 2
    assert sum(
        event.event_type == "CHILD_SPAWNED" for event in restored.events
    ) == 2
    assert sum(
        event.event_type == "MODEL_DECISION" for event in restored.events
    ) == 1
    assert model_calls == []


def _assistant_tool_call_evidence(message):
    if (
        not isinstance(message, Mapping)
        or message.get("role") != "assistant"
        or "content" not in message
        or not isinstance(message["content"], (str, type(None)))
    ):
        return "malformed", None
    calls = message.get("tool_calls")
    if calls is None or calls == []:
        return "mechanism_absent", None
    if not isinstance(calls, list) or len(calls) != 1:
        return "malformed", None
    call = calls[0]
    if not isinstance(call, Mapping):
        return "malformed", None
    function = call.get("function")
    if (
        not isinstance(call.get("id"), str)
        or not call["id"]
        or call.get("type") != "function"
        or not isinstance(function, Mapping)
        or not isinstance(function.get("name"), str)
        or not function["name"]
        or not isinstance(function.get("arguments"), str)
    ):
        return "malformed", None
    return "observed", call


def test_evidence_extractor_distinguishes_absent_from_malformed_tool_calls():
    assert _assistant_tool_call_evidence(
        {"role": "assistant", "content": "done"}
    ) == ("mechanism_absent", None)
    assert _assistant_tool_call_evidence(
        {"role": "assistant", "content": None}
    ) == ("mechanism_absent", None)
    assert _assistant_tool_call_evidence(
        {"content": "missing role"}
    ) == ("malformed", None)
    assert _assistant_tool_call_evidence(
        {"role": "assistant"}
    ) == ("malformed", None)
    assert _assistant_tool_call_evidence(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_incomplete"}],
        }
    ) == ("malformed", None)
    assert _assistant_tool_call_evidence(
        {"role": "assistant", "content": None, "tool_calls": [7]}
    ) == ("malformed", None)
    call = _tool_response("call_observed", "read", '{"path":"input.txt"}')[
        "choices"
    ][0]["message"]["tool_calls"][0]
    assert _assistant_tool_call_evidence(
        {"role": "assistant", "content": None, "tool_calls": [call]}
    ) == ("observed", call)


def test_native_read_call_maps_to_the_existing_typed_action():
    payloads = []

    def fake_transport(payload):
        payloads.append(payload)
        return _tool_response("call_read", "read", json.dumps({"path": "input.txt"}))

    decision = _NativeToolTestModel(transport=fake_transport).decide(
        ModelRequest("{}", ("read(path: str) -> ToolResult",), ("event-1",))
    )

    assert isinstance(decision, NativeModelDecision)
    assert decision.action == ToolCall(ReadRequest("input.txt"))
    assert decision.provider_tool_call_id == "call_read"
    assert decision.failure is None
    assert payloads[0]["model"] == "deepseek-v4-pro"
    assert payloads[0]["thinking"] == {"type": "disabled"}
    assert payloads[0]["stream"] is False
    assert [tool["function"]["name"] for tool in payloads[0]["tools"]] == [
        "read"
    ]


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        (
            "write",
            {"path": "output.txt", "content": "ALPHA"},
            ToolCall(WriteRequest("output.txt", "ALPHA")),
        ),
        (
            "shell",
            {"argv": ["python", "-c", "print('ok')"]},
            ToolCall(ShellRequest(("python", "-c", "print('ok')"))),
        ),
        ("wait", {"event_type": "CONTINUE"}, Wait("CONTINUE")),
        ("claim_complete", {}, ClaimComplete()),
    ],
)
def test_native_function_calls_map_to_existing_actions(name, arguments, expected):
    model = _NativeToolTestModel(
        transport=lambda payload: _tool_response(
            f"call_{name}", name, json.dumps(arguments)
        )
    )

    decision = model.decide(ModelRequest("{}", (), ("event-1",)))

    assert decision.action == expected
    assert decision.provider_tool_call_id == f"call_{name}"
    assert decision.failure is None


def test_default_root_surface_is_prime_like_in_frame_and_provider_request(
    tmp_path,
):
    payloads = []
    root = AgentProcess(
        DeepSeekModel(
            transport=lambda payload: (
                payloads.append(payload)
                or _tool_response('call_claim', 'claim_complete', '{}')
            ),
        ),
        ToolHost(SharedEnvironment(tmp_path)),
        event_log=EventLog(tmp_path / 'root.jsonl'),
        max_decisions=1,
        max_depth=2,
    )
    try:
        result = root.run(
            'Complete the task.',
            FileContentEquals('outcome.txt', 'done'),
        )
    finally:
        root.close()

    assert [
        contract.partition('(')[0]
        for contract in result.decision_frames[0].actual_request.available_tools
    ] == ['ipython', 'wait', 'claim_complete']
    assert [
        tool['function']['name'] for tool in payloads[0]['tools']
    ] == ['ipython', 'wait', 'claim_complete']


@pytest.mark.parametrize(
    ('depth', 'expected_names'),
    [
        (1, ['ipython', 'wait', 'return']),
        (2, ['ipython', 'wait', 'return']),
    ],
)
def test_default_child_surface_is_prime_like_and_respects_depth_admission(
    tmp_path,
    depth,
    expected_names,
):
    log_path = tmp_path / f'child-{depth}.jsonl'
    payloads = []
    child_ref = ChildRef(
        child_execution_id=f'execution-child-{depth}',
        child_actor_id=f'child-{depth}',
        parent_actor_id='parent',
        local_goal='Return one local result.',
        event_log_path=str(log_path),
        provider_tool_call_id='call_spawn',
        depth=depth,
        max_depth=2,
    )
    child = AgentProcess.for_child(
        child_ref,
        model=DeepSeekModel(
            transport=lambda payload: (
                payloads.append(payload)
                or _tool_response(
                    'call_return',
                    'return',
                    json.dumps({'local_result': 'done'}),
                )
            ),
        ),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    )
    try:
        result = child.run_child()
    finally:
        child.close()

    assert [
        contract.partition('(')[0]
        for contract in result.decision_frames[0].actual_request.available_tools
    ] == expected_names
    assert [
        tool['function']['name'] for tool in payloads[0]['tools']
    ] == expected_names


def test_two_ordinary_siblings_execute_in_model_order_and_continue_together(
    tmp_path,
):
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    payloads = []
    responses = iter(
        [
            _tool_calls_response(
                ("call_a", "read", json.dumps({"path": "a.txt"})),
                ("call_b", "read", json.dumps({"path": "b.txt"})),
            ),
            _tool_response("call_complete", "claim_complete", "{}"),
        ]
    )
    execution_order = []

    class RecordingTools(ToolHost):
        def execute(self, request):
            execution_order.append(request)
            return super().execute(request)

    result = RootAgentProcess(
        _NativeToolTestModel(
            transport=lambda payload: (
                payloads.append(payload) or next(responses)
            )
        ),
        RecordingTools(SharedEnvironment(tmp_path)),
        event_log=EventLog(tmp_path / "siblings.jsonl"),
        max_decisions=2,
    ).run(
        "Read a.txt, then b.txt.",
        FileContentEquals("b.txt", "B"),
    )

    assert result.status == "completed"
    assert execution_order == [
        ReadRequest("a.txt"),
        ReadRequest("b.txt"),
    ]
    first_frame = result.decision_frames[0]
    assert first_frame.resulting_action == (
        ToolCall(ReadRequest("a.txt")),
        ToolCall(ReadRequest("b.txt")),
    )
    assert first_frame.provider_tool_call_id == ("call_a", "call_b")
    decision_event = result.events[1]
    starts = [
        event for event in result.events if event.event_type == "TOOL_CALL_STARTED"
    ]
    settled = [
        event
        for event in result.events
        if event.event_type in ("TOOL_RESULT", "TOOL_FAILED")
    ]
    assert [event.source_event_refs for event in starts] == [
        (decision_event.event_id,),
        (decision_event.event_id,),
    ]
    assert [event.source_event_refs for event in settled] == [
        (starts[0].event_id,),
        (starts[1].event_id,),
    ]
    assert [
        event.payload["observation"].provider_tool_call_id
        for event in settled
    ] == ["call_a", "call_b"]
    assistant = payloads[1]["messages"][-3]
    tool_results = payloads[1]["messages"][-2:]
    assert [call["id"] for call in assistant["tool_calls"]] == [
        "call_a",
        "call_b",
    ]
    assert [message["tool_call_id"] for message in tool_results] == [
        "call_a",
        "call_b",
    ]
    reloaded = EventLog.load(tmp_path / "siblings.jsonl")
    assert reloaded.events == result.events
    assert fold_execution_state(reloaded.events) == result.state


def test_interrupt_stops_unstarted_sibling_until_explicit_resume(tmp_path):
    responses = iter(
        [
            _tool_calls_response(
                (
                    "call_a",
                    "write",
                    json.dumps({"path": "a.txt", "content": "A"}),
                ),
                (
                    "call_b",
                    "write",
                    json.dumps({"path": "b.txt", "content": "B"}),
                ),
            ),
            _tool_response("call_complete", "claim_complete", "{}"),
        ]
    )
    payloads = []

    class BlockingFirstTools(ToolHost):
        def __init__(self, environment):
            super().__init__(environment)
            self.started = threading.Event()
            self.release = threading.Event()
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                self.started.set()
                if not self.release.wait(timeout=2):
                    raise TimeoutError("test did not release first sibling")
            return super().execute(request)

    tools = BlockingFirstTools(SharedEnvironment(tmp_path))
    event_log = EventLog(tmp_path / "siblings.jsonl")
    runtime = RootAgentProcess(
        _NativeToolTestModel(
            transport=lambda payload: (
                payloads.append(payload) or next(responses)
            )
        ),
        tools,
        event_log=event_log,
        max_decisions=2,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future = pool.submit(
            runtime.run,
            "Write both files",
            FileContentEquals("b.txt", "B"),
        )
        assert tools.started.wait(timeout=2)
        interrupt_future = pool.submit(runtime.interrupt)
        deadline = time.monotonic() + 2
        while (
            "INTERRUPT_REQUESTED"
            not in [event.event_type for event in event_log.events]
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        tools.release.set()
        interrupted = interrupt_future.result(timeout=2)
        assert run_future.result(timeout=2).status == "suspended"

    assert interrupted.status == "suspended"
    assert tools.requests == [WriteRequest("a.txt", "A")]
    assert len(payloads) == 1

    completed = runtime.resume()

    assert completed.status == "completed"
    assert tools.requests == [
        WriteRequest("a.txt", "A"),
        WriteRequest("b.txt", "B"),
    ]
    assert len(payloads) == 2


def test_next_sibling_cannot_start_before_previous_sibling_settles(tmp_path):
    response = _tool_calls_response(
        ("call_a", "read", json.dumps({"path": "a.txt"})),
        ("call_b", "read", json.dumps({"path": "b.txt"})),
    )
    event_log = EventLog()

    class CrashBeforeRead(ToolHost):
        def execute(self, request):
            raise RuntimeError("crash before first result")

    process = RootAgentProcess(
        _NativeToolTestModel(transport=lambda payload: response),
        CrashBeforeRead(SharedEnvironment(tmp_path)),
        event_log=event_log,
        max_decisions=1,
    )
    with pytest.raises(RuntimeError, match="crash before first result"):
        process.run("Read both files.", FileContentEquals("b.txt", "B"))

    decision_event = event_log.events[1]
    with pytest.raises(ValueError, match="previous sibling must settle"):
        event_log.append(
            "TOOL_CALL_STARTED",
            {
                "request": ReadRequest("b.txt"),
                "provider_tool_call_id": "call_b",
            },
            (decision_event.event_id,),
        )


def test_sibling_start_rejects_an_unknown_decision_reference():
    event_log = EventLog()
    event_log.append(
        "EXECUTION_STARTED",
        {
            "goal": "Read one file.",
            "completion_spec": FileContentEquals("done.txt", "done"),
        },
    )

    with pytest.raises(ValueError, match="must match its model decision"):
        event_log.append(
            "TOOL_CALL_STARTED",
            {"request": ReadRequest("missing.txt")},
            ("event-999999",),
        )


def test_restart_resumes_only_the_unstarted_sibling_suffix(
    tmp_path, monkeypatch
):
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    log_path = tmp_path / "execution.jsonl"
    event_log = EventLog(log_path)
    execution_order = []

    class RecordingTools(ToolHost):
        def execute(self, request):
            execution_order.append(request)
            return super().execute(request)

    first_response = _tool_calls_response(
        ("call_a", "read", json.dumps({"path": "a.txt"})),
        ("call_b", "read", json.dumps({"path": "b.txt"})),
    )
    append = event_log.append

    def crash_after_first_result(event_type, payload, source_event_refs=()):
        event = append(event_type, payload, source_event_refs)
        if event_type == "TOOL_RESULT":
            raise SystemExit("crash after first sibling settled")
        return event

    monkeypatch.setattr(event_log, "append", crash_after_first_result)
    with pytest.raises(SystemExit, match="first sibling settled"):
        RootAgentProcess(
            _NativeToolTestModel(transport=lambda payload: first_response),
            RecordingTools(SharedEnvironment(tmp_path)),
            event_log=event_log,
            max_decisions=2,
        ).run("Read both files.", FileContentEquals("b.txt", "B"))

    before = fold_execution_state(EventLog.load(log_path).events)
    payloads = []
    resumed = RootAgentProcess(
        _NativeToolTestModel(
            transport=lambda payload: (
                payloads.append(payload)
                or _tool_response("call_complete", "claim_complete", "{}")
            )
        ),
        RecordingTools(SharedEnvironment(tmp_path)),
        event_log=EventLog.load(log_path),
        max_decisions=2,
    ).resume()

    assert resumed.status == "completed"
    assert execution_order == [ReadRequest("a.txt"), ReadRequest("b.txt")]
    assert resumed.state.execution_id == before.execution_id
    assert resumed.state.root_actor_id == before.root_actor_id
    assert resumed.state == fold_execution_state(resumed.events)
    assert [
        message["tool_call_id"]
        for message in payloads[0]["messages"]
        if message["role"] == "tool"
    ] == ["call_a", "call_b"]


def test_restart_reconciles_committed_write_then_resumes_sibling_suffix(
    tmp_path,
):
    log_path = tmp_path / "execution.jsonl"
    execution_order = []

    class CrashAfterFirstWrite(ToolHost):
        def execute(self, request):
            execution_order.append(request)
            result = super().execute(request)
            raise SystemExit("crash after committed sibling write")

    first_response = _tool_calls_response(
        (
            "call_a",
            "write",
            json.dumps({"path": "a.txt", "content": "A"}),
        ),
        (
            "call_b",
            "write",
            json.dumps({"path": "b.txt", "content": "B"}),
        ),
    )
    with pytest.raises(SystemExit, match="committed sibling write"):
        RootAgentProcess(
            _NativeToolTestModel(transport=lambda payload: first_response),
            CrashAfterFirstWrite(SharedEnvironment(tmp_path)),
            event_log=EventLog(log_path),
            max_decisions=2,
        ).run("Write both files.", FileContentEquals("b.txt", "B"))

    before = fold_execution_state(EventLog.load(log_path).events)
    payloads = []

    class RecordingTools(ToolHost):
        def execute(self, request):
            execution_order.append(request)
            return super().execute(request)

    resumed = RootAgentProcess(
        _NativeToolTestModel(
            transport=lambda payload: (
                payloads.append(payload)
                or _tool_response("call_complete", "claim_complete", "{}")
            )
        ),
        RecordingTools(SharedEnvironment(tmp_path)),
        event_log=EventLog.load(log_path),
        max_decisions=2,
    ).resume()

    assert resumed.status == "completed"
    assert execution_order == [
        WriteRequest("a.txt", "A"),
        WriteRequest("b.txt", "B"),
    ]
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "A"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "B"
    assert resumed.state.execution_id == before.execution_id
    assert resumed.state.root_actor_id == before.root_actor_id
    assert sum(
        event.event_type == "ACTION_RECONCILED"
        for event in resumed.events
    ) == 1
    assert [
        message["tool_call_id"]
        for message in payloads[0]["messages"]
        if message["role"] == "tool"
    ] == ["call_a", "call_b"]
    assert resumed.state == fold_execution_state(resumed.events)


@pytest.mark.parametrize(
    ("response", "expected_failure"),
    [
        (
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "I am done.",
                            "tool_calls": [],
                        }
                    }
                ]
            },
            "model_protocol:no_tool_call",
        ),
        (
            _tool_response("call_unknown", "delete", "{}"),
            "model_protocol:unknown_tool",
        ),
        (
            _tool_response("call_json", "read", "{not json"),
            "model_protocol:invalid_json",
        ),
        (
            _tool_response("call_args", "read", json.dumps({"path": 7})),
            "model_protocol:invalid_arguments",
        ),
    ],
)
def test_invalid_native_decisions_are_rejected_atomically(
    response, expected_failure
):
    decision = _NativeToolTestModel(transport=lambda payload: response).decide(
        ModelRequest("{}", (), ("event-1",))
    )

    assert decision.action is None
    assert decision.provider_tool_call_id is None
    assert decision.failure == expected_failure


def test_batch_preflight_rejects_an_invalid_third_call_before_writes(tmp_path):
    executed = []

    class RecordingTools(ToolHost):
        def execute(self, request):
            executed.append(request)
            return super().execute(request)

    response = _tool_calls_response(
        (
            "call_a",
            "write",
            json.dumps({"path": "a.txt", "content": "A"}),
        ),
        (
            "call_b",
            "write",
            json.dumps({"path": "b.txt", "content": "B"}),
        ),
        ("call_invalid", "invalid_tool", "{}"),
    )
    result = RootAgentProcess(
        _NativeToolTestModel(transport=lambda payload: response),
        RecordingTools(SharedEnvironment(tmp_path)),
        max_decisions=1,
    ).run(
        "Write two files.",
        FileContentEquals("b.txt", "B"),
    )

    assert result.status == "failed"
    assert result.failure == "model_protocol:unknown_tool"
    assert executed == []
    assert not (tmp_path / "a.txt").exists()
    assert not (tmp_path / "b.txt").exists()


def test_over_bound_sibling_batch_is_rejected_before_any_effect(tmp_path):
    executed = []

    class RecordingTools(ToolHost):
        def execute(self, request):
            executed.append(request)
            return super().execute(request)

    response = _tool_calls_response(
        *[
            (f"call_{index}", "read", json.dumps({"path": f"{index}.txt"}))
            for index in range(5)
        ]
    )
    runtime = RootAgentProcess(
        _NativeToolTestModel(transport=lambda payload: response),
        RecordingTools(SharedEnvironment(tmp_path)),
        max_decisions=1,
    )
    result = runtime.run("Read the files.", FileContentEquals("done.txt", "done"))
    assert result.status == "failed"
    assert result.failure == "model_protocol:too_many_tool_calls"
    assert executed == []


@pytest.mark.parametrize(
    ("response", "expected_failure", "model_type"),
    [
        (
            _tool_calls_response(
                ("call_same", "read", json.dumps({"path": "a.txt"})),
                ("call_same", "read", json.dumps({"path": "b.txt"})),
            ),
            "model_protocol:duplicate_tool_call_id",
            _NativeToolTestModel,
        ),
        (
            _tool_calls_response(
                ("call_read", "read", json.dumps({"path": "a.txt"})),
                ("call_complete", "claim_complete", "{}"),
            ),
            "model_protocol:mixed_control_tool_calls",
            _NativeToolTestModel,
        ),
        (
            _tool_calls_response(
                ("call_python", "ipython", json.dumps({"code": "x = 1"})),
                (
                    "call_wait",
                    "wait",
                    json.dumps({"event_type": "CONTINUE"}),
                ),
            ),
            "model_protocol:mixed_control_tool_calls",
            DeepSeekModel,
        ),
    ],
)
def test_invalid_sibling_batch_is_rejected_before_any_effect(
    tmp_path, response, expected_failure, model_type
):
    executed = []

    class RecordingTools(ToolHost):
        def execute(self, request):
            executed.append(request)
            return super().execute(request)

    class RejectingIPython:
        def execute(self, code):
            raise AssertionError(f"unexpected IPython execution: {code}")

        def close(self):
            pass

    result = RootAgentProcess(
        model_type(transport=lambda payload: response),
        RecordingTools(SharedEnvironment(tmp_path)),
        ipython_control=RejectingIPython(),
        max_decisions=1,
    ).run("Handle calls.", FileContentEquals("done.txt", "done"))

    assert result.status == "failed"
    assert result.failure == expected_failure
    assert executed == []


def test_failed_sibling_is_committed_without_erasing_later_calls(tmp_path):
    (tmp_path / "first.txt").write_text("FIRST", encoding="utf-8")
    (tmp_path / "third.txt").write_text("THIRD", encoding="utf-8")
    payloads = []
    responses = iter(
        [
            _tool_calls_response(
                (
                    "call_first",
                    "read",
                    json.dumps({"path": "first.txt"}),
                ),
                (
                    "call_missing",
                    "read",
                    json.dumps({"path": "missing.txt"}),
                ),
                (
                    "call_third",
                    "read",
                    json.dumps({"path": "third.txt"}),
                ),
            ),
            _tool_response("call_complete", "claim_complete", "{}"),
        ]
    )
    result = RootAgentProcess(
        _NativeToolTestModel(
            transport=lambda payload: (
                payloads.append(payload) or next(responses)
            )
        ),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
    ).run(
        "Read first.txt, missing.txt, and third.txt.",
        FileContentEquals("third.txt", "THIRD"),
    )

    settled = [
        event
        for event in result.events
        if event.event_type in ("TOOL_RESULT", "TOOL_FAILED")
    ]
    assert result.status == "completed"
    assert [event.event_type for event in settled] == [
        "TOOL_RESULT",
        "TOOL_FAILED",
        "TOOL_RESULT",
    ]
    assert [
        event.payload["observation"].provider_tool_call_id
        for event in settled
    ] == ["call_first", "call_missing", "call_third"]
    assert [step.observation.ok for step in result.steps[:3]] == [
        True,
        False,
        True,
    ]
    assistant = payloads[1]["messages"][-4]
    tool_results = payloads[1]["messages"][-3:]
    assert [call["id"] for call in assistant["tool_calls"]] == [
        "call_first",
        "call_missing",
        "call_third",
    ]
    assert [message["tool_call_id"] for message in tool_results] == [
        "call_first",
        "call_missing",
        "call_third",
    ]
    assert json.loads(tool_results[1]["content"])["observation"]["result"][
        "error_code"
    ] == "not_found"


def test_runtime_preserves_native_call_id_into_the_tool_result_continuation(
    tmp_path,
):
    (tmp_path / "input.txt").write_text("alpha", encoding="utf-8")
    read_response = _tool_response(
        "call_123", "read", json.dumps({"path": "input.txt"})
    )
    read_response["choices"][0]["message"]["reasoning_content"] = "hidden"
    responses = iter(
        [
            read_response,
            _tool_response("call_456", "claim_complete", "{}"),
        ]
    )
    payloads = []

    def fake_transport(payload):
        payloads.append(payload)
        return next(responses)

    model = _NativeToolTestModel(transport=fake_transport)
    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
    ).run(
        "Read input.txt and then claim completion.",
        FileContentEquals("input.txt", "alpha"),
    )

    assistant_call = payloads[1]["messages"][-2]["tool_calls"][0]
    tool_result = payloads[1]["messages"][-1]
    first_frame = result.decision_frames[0]
    first_observation = next(
        event.payload["observation"]
        for event in result.events
        if event.event_type == "TOOL_RESULT"
    )

    assert result.status == "completed"
    assert assistant_call["id"] == "call_123"
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call_123"
    assert json.loads(tool_result["content"])["observation"]["result"]["output"][
        "text"
    ] == "alpha"
    assert _plain(first_frame.provider_wire_request) == payloads[0]
    assert first_frame.provider_tool_call_id == "call_123"
    assert first_observation.provider_tool_call_id == "call_123"
    assert first_frame.raw_provider_response["choices"][0]["message"].get(
        "reasoning_content"
    ) is None
    assert "Authorization" not in json.dumps(_plain(first_frame.provider_wire_request))
    assert not hasattr(model, "messages")


@pytest.mark.parametrize(
    ("transport_outcome", "expected_failure"),
    [
        (TimeoutError("slow"), "model_provider:timeout"),
        (OSError("network down"), "model_provider:error"),
        ({}, "model_protocol:malformed_response"),
    ],
)
def test_provider_failures_are_explicit_and_execute_no_tool(
    tmp_path, transport_outcome, expected_failure
):
    calls = []

    class RecordingTools:
        def __init__(self):
            self._host = ToolHost(SharedEnvironment(tmp_path))

        @property
        def environment(self):
            return self._host.environment

        def execute(self, request):
            calls.append(request)
            return self._host.execute(request)

    def fake_transport(payload):
        if isinstance(transport_outcome, BaseException):
            raise transport_outcome
        return transport_outcome

    result = RootAgentProcess(
        model=DeepSeekModel(transport=fake_transport),
        tools=RecordingTools(),
        max_decisions=1,
    ).run(
        "Do not fabricate an action.",
        FileContentEquals("unused.txt", "unused"),
    )

    assert result.status == "failed"
    assert result.failure == expected_failure
    assert calls == []
    assert result.events[-1].event_type == "EXECUTION_FAILED"
    assert result.decision_frames[0].provider_wire_request is not None


def test_wait_restart_rebuilds_native_continuation_from_durable_facts(tmp_path):
    (tmp_path / "done.txt").write_text("done", encoding="utf-8")
    log_path = tmp_path / "execution.jsonl"
    first_payloads = []
    first_model = DeepSeekModel(
        transport=lambda payload: (
            first_payloads.append(payload)
            or _tool_response(
                "call_wait", "wait", json.dumps({"event_type": "CONTINUE"})
            )
        )
    )
    before_restart = RootAgentProcess(
        model=first_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog(log_path),
    ).run(
        "Wait for CONTINUE, then claim completion.",
        FileContentEquals("done.txt", "done"),
    )

    second_payloads = []
    second_model = DeepSeekModel(
        transport=lambda payload: (
            second_payloads.append(payload)
            or _tool_response("call_complete", "claim_complete", "{}")
        )
    )
    after_restart = RootAgentProcess(
        model=second_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog.load(log_path),
    ).deliver_event("CONTINUE", "wake now")

    assistant_call = second_payloads[0]["messages"][-2]["tool_calls"][0]
    tool_result = second_payloads[0]["messages"][-1]
    incoming = json.loads(tool_result["content"])["incoming_event"]

    assert before_restart.status == "waiting"
    assert after_restart.status == "completed"
    assert after_restart.state.execution_id == before_restart.state.execution_id
    assert after_restart.state.root_actor_id == before_restart.state.root_actor_id
    assert assistant_call["id"] == "call_wait"
    assert tool_result["tool_call_id"] == "call_wait"
    assert incoming["event_type"]["text"] == "CONTINUE"
    assert after_restart.state == fold_execution_state(after_restart.events)
    assert EventLog.load(log_path).events == after_restart.events
    assert not hasattr(second_model, "messages")


def test_reconciled_native_write_preserves_call_id_into_observation_and_continuation(
    tmp_path,
):
    log_path = tmp_path / "execution.jsonl"
    checkpoint_path = tmp_path / "execution.checkpoint.json"

    class CrashAfterWrite(ToolHost):
        def execute(self, request):
            result = super().execute(request)
            raise RuntimeError("simulated crash after committed write")

    first = RootAgentProcess(
        model=_NativeToolTestModel(
            transport=lambda payload: _tool_response(
                "call_write",
                "write",
                json.dumps({"path": "output.txt", "content": "ALPHA"}),
            )
        ),
        tools=CrashAfterWrite(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog(log_path),
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        first.run(
            "Write output.txt and claim completion.",
            FileContentEquals("output.txt", "ALPHA"),
        )
    crashed_events = EventLog.load(log_path).events
    Checkpoint.capture(crashed_events).save(checkpoint_path)

    payloads = []
    result = RootAgentProcess(
        model=_NativeToolTestModel(
            transport=lambda payload: (
                payloads.append(payload)
                or _tool_response("call_complete", "claim_complete", "{}")
            )
        ),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog.load(log_path),
        checkpoint_path=checkpoint_path,
    ).resume()

    observation = result.state.latest_observation
    assert result.status == "completed"
    assert isinstance(observation, Observation)
    assert observation.provider_tool_call_id == "call_write"
    assert payloads[0]["messages"][-2]["tool_calls"][0]["id"] == "call_write"
    assert payloads[0]["messages"][-1]["tool_call_id"] == "call_write"
    assert result.state == fold_execution_state(result.events)


def test_event_log_rejects_a_tool_result_with_a_different_provider_call_id(
    tmp_path,
):
    event_log = EventLog()

    class CrashBeforeRead(ToolHost):
        def execute(self, request):
            raise RuntimeError("simulated crash before read")

    process = RootAgentProcess(
        model=_NativeToolTestModel(
            transport=lambda payload: _tool_response(
                "call_read", "read", json.dumps({"path": "input.txt"})
            )
        ),
        tools=CrashBeforeRead(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=event_log,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        process.run(
            "Read input.txt.",
            FileContentEquals("input.txt", "alpha"),
        )

    call_event = event_log.events[-1]
    with pytest.raises(ValueError, match="tool result must match"):
        event_log.append(
            "TOOL_RESULT",
            {
                "observation": Observation(
                    ReadRequest("input.txt"),
                    ToolResult(ok=True, output="alpha"),
                    "different_call_id",
                )
            },
            (call_event.event_id,),
        )


def test_native_tool_result_uses_the_bounded_projection_not_canonical_output(
    tmp_path,
):
    hidden_tail = "NATIVE_MODEL_MUST_NOT_SEE_THIS_TAIL"
    content = ("x" * 20_000) + hidden_tail
    second_tail = "SECOND_RESULT_MUST_ALSO_BE_BOUNDED"
    second_content = ("y" * 20_000) + second_tail
    (tmp_path / "large.txt").write_text(content, encoding="utf-8")
    (tmp_path / "second.txt").write_text(second_content, encoding="utf-8")
    responses = iter(
        [
            _tool_calls_response(
                (
                    "call_large",
                    "read",
                    json.dumps({"path": "large.txt"}),
                ),
                (
                    "call_second",
                    "read",
                    json.dumps({"path": "second.txt"}),
                ),
            ),
            _tool_response("call_complete", "claim_complete", "{}"),
        ]
    )
    payloads = []

    result = RootAgentProcess(
        model=_NativeToolTestModel(
            transport=lambda payload: payloads.append(payload) or next(responses)
        ),
        tools=ToolHost(SharedEnvironment(tmp_path), max_output_chars=30_000),
        max_decisions=2,
        max_context_chars=768,
    ).run(
        "Inspect large.txt and claim completion.",
        FileContentEquals("large.txt", content),
    )

    canonical = [
        event.payload["observation"].result
        for event in result.events
        if event.event_type == "TOOL_RESULT"
    ]
    visible_tool_results = [
        message["content"]
        for message in payloads[1]["messages"]
        if message["role"] == "tool"
    ]
    dynamic_context_chars = sum(
        len(message["content"])
        for message in payloads[1]["messages"]
        if message["role"] in {"user", "tool"}
        and isinstance(message.get("content"), str)
    )

    assert result.status == "completed"
    assert [result.output for result in canonical] == [content, second_content]
    assert all(result.truncated is False for result in canonical)
    assert hidden_tail not in "".join(visible_tool_results)
    assert second_tail not in "".join(visible_tool_results)
    assert all('"truncated":true' in item for item in visible_tool_results)
    assert dynamic_context_chars <= 768


def test_four_siblings_fit_the_allocated_context_bound(tmp_path):
    calls = []
    for name in ("a", "b", "c", "d"):
        (tmp_path / f"{name}.txt").write_text(name.upper(), encoding="utf-8")
        calls.append(
            (
                f"call_{name}",
                "read",
                json.dumps({"path": f"{name}.txt"}),
            )
        )
    responses = iter(
        [
            _tool_calls_response(*calls),
            _tool_response("call_complete", "claim_complete", "{}"),
        ]
    )
    payloads = []
    result = RootAgentProcess(
        _NativeToolTestModel(
            transport=lambda payload: (
                payloads.append(payload) or next(responses)
            )
        ),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        max_context_chars=2000,
    ).run("Read four files.", FileContentEquals("d.txt", "D"))

    dynamic_chars = sum(
        len(message["content"])
        for message in payloads[1]["messages"]
        if message["role"] in {"user", "tool"}
    )
    assert result.status == "completed"
    assert sum(
        event.event_type == "TOOL_CALL_STARTED"
        for event in result.events
    ) == 4
    assert [
        message["tool_call_id"] for message in payloads[1]["messages"]
        if message["role"] == "tool"
    ] == ["call_a", "call_b", "call_c", "call_d"]
    visible = json.loads(result.decision_frames[1].actual_request.context)
    assert visible["state"]["execution_id"] == result.state.execution_id
    assert visible["goal"]["text"] == result.state.goal
    assert visible["completion_spec"]["type"] == "file_content_equals"
    assert dynamic_chars <= 2000


def test_four_failed_siblings_fit_the_allocated_context_bound(tmp_path):
    (tmp_path / "done.txt").write_text("done", encoding="utf-8")
    calls = tuple(
        (
            f"call_{name}",
            "read",
            json.dumps({"path": f"missing-{name}.txt"}),
        )
        for name in ("a", "b", "c", "d")
    )
    responses = iter(
        [
            _tool_calls_response(*calls),
            _tool_response("call_complete", "claim_complete", "{}"),
        ]
    )
    payloads = []
    result = RootAgentProcess(
        _NativeToolTestModel(
            transport=lambda payload: (
                payloads.append(payload) or next(responses)
            )
        ),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        max_context_chars=2000,
    ).run("Read four missing files.", FileContentEquals("done.txt", "done"))

    dynamic_chars = sum(
        len(message["content"])
        for message in payloads[1]["messages"]
        if message["role"] in {"user", "tool"}
    )
    assert result.status == "completed"
    assert sum(event.event_type == "TOOL_FAILED" for event in result.events) == 4
    assert [
        message["tool_call_id"]
        for message in payloads[1]["messages"]
        if message["role"] == "tool"
    ] == ["call_a", "call_b", "call_c", "call_d"]
    visible = json.loads(result.decision_frames[1].actual_request.context)
    assert visible["state"]["execution_id"] == result.state.execution_id
    assert visible["goal"]["text"] == result.state.goal
    assert visible["completion_spec"]["type"] == "file_content_equals"
    assert dynamic_chars <= 2000


def test_undersized_sibling_context_fails_before_next_provider_or_action(tmp_path):
    for name in ("a", "b", "c", "d"):
        (tmp_path / f"{name}.txt").write_text(name.upper(), encoding="utf-8")
    calls = tuple(
        (f"call_{name}", "read", json.dumps({"path": f"{name}.txt"}))
        for name in ("a", "b", "c", "d")
    )
    log_path = tmp_path / "events.jsonl"
    root = RootAgentProcess(
        _NativeToolTestModel(transport=lambda payload: _tool_calls_response(*calls)),
        ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        max_context_chars=2000,
        max_decisions_per_advance=1,
        event_log=EventLog(log_path),
    )
    settled = root.run("Read four files.", FileContentEquals("d.txt", "D"))
    root.close()
    assert settled.status == "running"
    assert sum(event.event_type == "TOOL_RESULT" for event in settled.events) == 4
    before = log_path.read_bytes()

    def unexpected_provider(payload):
        pytest.fail("An insufficient context must not reach the next provider call")

    class NoMoreActions(ToolHost):
        def execute(self, request):
            pytest.fail("An insufficient context must not replay settled actions")

    restored = RootAgentProcess(
        _NativeToolTestModel(transport=unexpected_provider),
        NoMoreActions(SharedEnvironment(tmp_path)),
        max_decisions=2,
        max_context_chars=768,
        event_log=EventLog.load(log_path),
    )
    try:
        with pytest.raises(ValueError, match="too small for context metadata"):
            restored.resume()
    finally:
        restored.close()
    assert log_path.read_bytes() == before


def test_production_adapter_requires_the_environment_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    decision = DeepSeekModel().decide(
        ModelRequest("{}", (), ("event-1",))
    )

    assert decision.action is None
    assert decision.failure == "model_provider:missing_credentials"
    assert decision.raw_provider_response is None
    assert "DEEPSEEK_API_KEY" not in json.dumps(decision.provider_wire_request)
    with pytest.raises(TypeError):
        DeepSeekModel(api_key="must-not-be-accepted")


def test_production_transport_keeps_the_environment_key_out_of_the_decision(
    monkeypatch,
):
    token = "test-only-token"
    observed = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return _tool_response("call_http", "claim_complete", "{}")

    def fake_post(url, *, headers, json, timeout):
        observed.update(
            url=url,
            authorization=headers["Authorization"],
            payload=json,
            timeout=timeout,
        )
        return FakeResponse()

    monkeypatch.setenv("DEEPSEEK_API_KEY", token)
    monkeypatch.setattr("Execution.deepseek_model.httpx.post", fake_post)

    decision = DeepSeekModel().decide(ModelRequest("{}", (), ("event-1",)))

    assert decision.action == ClaimComplete()
    assert observed["url"] == "https://api.deepseek.com/chat/completions"
    assert observed["authorization"] == f"Bearer {token}"
    assert observed["payload"] == decision.provider_wire_request
    assert observed["timeout"] == 60.0
    assert token not in repr(decision)
