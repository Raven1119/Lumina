import json
import os
import time
from collections.abc import Mapping

import pytest

from Execution_lab2.deepseek_model import DeepSeekModel
from Execution_lab2.execution import (
    Checkpoint,
    ClaimComplete,
    EventLog,
    FileContentEquals,
    ModelRequest,
    NativeModelDecision,
    Observation,
    ReadRequest,
    RootAgentProcess,
    SharedEnvironment,
    ShellRequest,
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


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


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

    decision = DeepSeekModel(transport=fake_transport).decide(
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
        "read",
        "write",
        "shell",
        "wait",
        "claim_complete",
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
    model = DeepSeekModel(
        transport=lambda payload: _tool_response(
            f"call_{name}", name, json.dumps(arguments)
        )
    )

    decision = model.decide(ModelRequest("{}", (), ("event-1",)))

    assert decision.action == expected
    assert decision.provider_tool_call_id == f"call_{name}"
    assert decision.failure is None


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
        (
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                _tool_response("call_1", "read", '{"path":"a"}')[
                                    "choices"
                                ][0]["message"]["tool_calls"][0],
                                _tool_response("call_2", "read", '{"path":"b"}')[
                                    "choices"
                                ][0]["message"]["tool_calls"][0],
                            ],
                        }
                    }
                ]
            },
            "model_protocol:multiple_tool_calls",
        ),
    ],
)
def test_invalid_native_decisions_are_rejected_atomically(
    response, expected_failure
):
    decision = DeepSeekModel(transport=lambda payload: response).decide(
        ModelRequest("{}", (), ("event-1",))
    )

    assert decision.action is None
    assert decision.provider_tool_call_id is None
    assert decision.failure == expected_failure


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

    model = DeepSeekModel(transport=fake_transport)
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
        model=DeepSeekModel(
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
        model=DeepSeekModel(
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
        model=DeepSeekModel(
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
    (tmp_path / "large.txt").write_text(content, encoding="utf-8")
    responses = iter(
        [
            _tool_response(
                "call_large",
                "read",
                json.dumps({"path": "large.txt"}),
            ),
            _tool_response("call_complete", "claim_complete", "{}"),
        ]
    )
    payloads = []

    result = RootAgentProcess(
        model=DeepSeekModel(
            transport=lambda payload: payloads.append(payload) or next(responses)
        ),
        tools=ToolHost(SharedEnvironment(tmp_path), max_output_chars=30_000),
        max_decisions=2,
        max_context_chars=768,
    ).run(
        "Inspect large.txt and claim completion.",
        FileContentEquals("large.txt", content),
    )

    canonical = next(
        event.payload["observation"].result
        for event in result.events
        if event.event_type == "TOOL_RESULT"
    )
    visible_tool_result = payloads[1]["messages"][-1]["content"]
    dynamic_context_chars = sum(
        len(message["content"])
        for message in payloads[1]["messages"]
        if message["role"] in {"user", "tool"}
        and isinstance(message.get("content"), str)
    )

    assert result.status == "completed"
    assert canonical.output == content
    assert canonical.truncated is False
    assert hidden_tail not in visible_tool_result
    assert '"truncated":true' in visible_tool_result
    assert dynamic_context_chars <= 768


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
    monkeypatch.setattr("Execution_lab2.deepseek_model.httpx.post", fake_post)

    decision = DeepSeekModel().decide(ModelRequest("{}", (), ("event-1",)))

    assert decision.action == ClaimComplete()
    assert observed["url"] == "https://api.deepseek.com/chat/completions"
    assert observed["authorization"] == f"Bearer {token}"
    assert observed["payload"] == decision.provider_wire_request
    assert observed["timeout"] == 60.0
    assert token not in repr(decision)


_RUN_REAL = (
    os.environ.get("RUN_DEEPSEEK_REAL_TESTS") == "1"
    and bool(os.environ.get("DEEPSEEK_API_KEY"))
)
_REAL_REASON = (
    "set RUN_DEEPSEEK_REAL_TESTS=1 and DEEPSEEK_API_KEY to run DeepSeek experiments"
)


def _usage(result):
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    for frame in result.decision_frames:
        response = frame.raw_provider_response
        if isinstance(response, Mapping):
            usage = response.get("usage")
            if isinstance(usage, Mapping):
                for provider_name, result_name in (
                    ("prompt_tokens", "input_tokens"),
                    ("completion_tokens", "output_tokens"),
                    ("total_tokens", "total_tokens"),
                ):
                    value = usage.get(provider_name)
                    if isinstance(value, int):
                        totals[result_name] += value
    return totals


@pytest.mark.skipif(not _RUN_REAL, reason=_REAL_REASON)
def test_real_deepseek_completes_canonical_task_three_of_three(tmp_path):
    summaries = []
    completed = 0
    for run_number in range(1, 4):
        workspace = tmp_path / f"canonical-{run_number}"
        workspace.mkdir()
        (workspace / "input.txt").write_text("alpha", encoding="utf-8")
        started = time.perf_counter()

        result = RootAgentProcess(
            model=DeepSeekModel(),
            tools=ToolHost(SharedEnvironment(workspace)),
            max_decisions=6,
        ).run(
            "Read input.txt and create output.txt containing its uppercase content.",
            FileContentEquals("output.txt", "ALPHA"),
        )

        elapsed = time.perf_counter() - started
        verified = result.status == "completed"
        output_path = workspace / "output.txt"
        output_match = (
            output_path.is_file()
            and output_path.read_text(encoding="utf-8") == "ALPHA"
        )
        completed += int(verified)
        summaries.append(
            {
                "run": run_number,
                "verified": verified,
                "output_match": output_match,
                "elapsed_seconds": round(elapsed, 3),
                "provider_requests": len(result.decision_frames),
                "model_calls": len(result.decision_frames),
                "tool_calls": sum(
                    event.event_type == "TOOL_CALL_STARTED"
                    for event in result.events
                ),
                "native_call_ids": [
                    frame.provider_tool_call_id
                    for frame in result.decision_frames
                    if frame.provider_tool_call_id is not None
                ],
                **_usage(result),
            }
        )
        assert result.state == fold_execution_state(result.events)

    print("DEEPSEEK_REAL_CANONICAL=" + json.dumps(summaries, sort_keys=True))
    assert completed == 3
    assert all(summary["output_match"] for summary in summaries)


@pytest.mark.skipif(not _RUN_REAL, reason=_REAL_REASON)
def test_real_deepseek_preserves_each_canonical_tool_call_id(tmp_path):
    (tmp_path / "input.txt").write_text("alpha", encoding="utf-8")

    result = RootAgentProcess(
        model=DeepSeekModel(),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=6,
    ).run(
        "Read input.txt and create output.txt containing its uppercase content.",
        FileContentEquals("output.txt", "ALPHA"),
    )

    evidence = []
    tool_steps = [
        step
        for step in result.steps
        if isinstance(step.action, ToolCall)
        and isinstance(step.observation, Observation)
    ]
    for step in tool_steps:
        frame = result.decision_frames[step.decision - 1]
        next_frame = result.decision_frames[step.decision]
        response = _plain(frame.raw_provider_response)
        assert isinstance(response, dict)
        choices = response.get("choices")
        assert isinstance(choices, list) and choices
        message = choices[0].get("message")
        status, provider_call = _assistant_tool_call_evidence(message)
        assert status == "observed"

        next_request = _plain(next_frame.provider_wire_request)
        assert isinstance(next_request, dict)
        messages = next_request.get("messages")
        assert isinstance(messages, list) and len(messages) >= 2
        assistant_status, continued_call = _assistant_tool_call_evidence(messages[-2])
        assert assistant_status == "observed"
        tool_message = messages[-1]
        assert isinstance(tool_message, dict)

        decision_event = next(
            event
            for event in result.events
            if event.event_type == "MODEL_DECISION"
            and event.payload["frame"].decision_id == frame.decision_id
        )
        call_event = next(
            event
            for event in result.events
            if event.event_type == "TOOL_CALL_STARTED"
            and event.source_event_refs == (decision_event.event_id,)
        )
        outcome_event = next(
            event
            for event in result.events
            if event.event_type in {"TOOL_RESULT", "TOOL_FAILED"}
            and event.source_event_refs == (call_event.event_id,)
        )
        call_id = provider_call["id"]
        equal = (
            call_id
            == frame.provider_tool_call_id
            == step.observation.provider_tool_call_id
            == continued_call["id"]
            == tool_message.get("tool_call_id")
        )
        assert equal
        evidence.append(
            {
                "call_id": call_id,
                "tool_name": provider_call["function"]["name"],
                "decision_event_sequence": [
                    decision_event.event_type,
                    call_event.event_type,
                    outcome_event.event_type,
                    "MODEL_DECISION",
                ],
                "equality_result": equal,
            }
        )

    assert result.status == "completed"
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "ALPHA"
    assert len(evidence) == 2
    assert result.state == fold_execution_state(result.events)
    print("DEEPSEEK_REAL_CALL_ID_CONTINUITY=" + json.dumps(evidence, sort_keys=True))


@pytest.mark.skipif(not _RUN_REAL, reason=_REAL_REASON)
def test_real_deepseek_accepts_a_mechanically_seeded_failed_tool_result(tmp_path):
    (tmp_path / "fallback.txt").write_text("fallback", encoding="utf-8")
    seeded_call_id = "call_test_failure_continuation"
    real_transport = DeepSeekModel._post
    real_responses = []
    request_count = 0

    def seeded_then_real(payload):
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return _tool_response(
                seeded_call_id,
                "read",
                json.dumps({"path": "candidate.txt"}),
            )
        response = real_transport(payload)
        real_responses.append(response)
        return response

    result = RootAgentProcess(
        model=DeepSeekModel(transport=seeded_then_real),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
    ).run(
        "Try candidate.txt; if unavailable use fallback.txt and write its "
        "content to output.txt.",
        FileContentEquals("output.txt", "fallback"),
    )

    first_frame, continuation_frame = result.decision_frames
    failure_event = next(
        event for event in result.events if event.event_type == "TOOL_FAILED"
    )
    failure = failure_event.payload["observation"]
    assert first_frame.resulting_action == ToolCall(ReadRequest("candidate.txt"))
    assert first_frame.provider_tool_call_id == seeded_call_id
    assert isinstance(failure, Observation)
    assert failure.request == ReadRequest("candidate.txt")
    assert failure.result.ok is False
    assert failure.result.error_code == "not_found"
    assert failure.provider_tool_call_id == seeded_call_id

    continuation_request = _plain(continuation_frame.provider_wire_request)
    assert isinstance(continuation_request, dict)
    messages = continuation_request.get("messages")
    assert isinstance(messages, list) and len(messages) >= 2
    assistant_status, assistant_call = _assistant_tool_call_evidence(messages[-2])
    assert assistant_status == "observed"
    tool_message = messages[-1]
    assert isinstance(tool_message, dict)
    visible_failure = json.loads(tool_message["content"])
    continuation_equal = (
        assistant_call["id"]
        == failure.provider_tool_call_id
        == tool_message.get("tool_call_id")
        == seeded_call_id
    )
    assert continuation_equal
    assert tool_message.get("role") == "tool"
    assert visible_failure["observation"]["result"]["ok"] is False

    assert len(real_responses) == 1
    response = real_responses[0]
    assert isinstance(response, Mapping)
    choices = response.get("choices")
    assert isinstance(choices, list) and choices
    message = choices[0].get("message")
    response_status, response_call = _assistant_tool_call_evidence(message)
    assert response_status in {"mechanism_absent", "observed"}
    if response_status == "observed":
        assert continuation_frame.provider_tool_call_id == response_call["id"]
        assert continuation_frame.resulting_action is not None

    next_action = continuation_frame.resulting_action
    if isinstance(next_action, ToolCall):
        next_action_name = type(next_action.request).__name__
    else:
        next_action_name = type(next_action).__name__ if next_action else None
    summary = {
        "initial_call_source": "mechanically_seeded_test_apparatus",
        "call_id": seeded_call_id,
        "tool_failure_source": "ToolHost",
        "tool_failure_error_code": failure.result.error_code,
        "provider_continuation_accepted": True,
        "next_response_status": response_status,
        "next_action": next_action_name,
        "continuation_equality_result": continuation_equal,
        "completion_exercised": result.status == "completed",
        **_usage(result),
    }
    print(
        "DEEPSEEK_REAL_MECHANICAL_FAILURE_CONTINUATION="
        + json.dumps(summary, sort_keys=True)
    )


@pytest.mark.skipif(not _RUN_REAL, reason=_REAL_REASON)
def test_real_deepseek_continues_after_a_failed_read(tmp_path):
    (tmp_path / "fallback.txt").write_text("fallback", encoding="utf-8")
    started = time.perf_counter()

    result = RootAgentProcess(
        model=DeepSeekModel(),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=8,
    ).run(
        "Try candidate.txt; if unavailable use fallback.txt and write its "
        "content to output.txt.",
        FileContentEquals("output.txt", "fallback"),
    )

    candidate_failures = [
        (index, event.payload["observation"])
        for index, event in enumerate(result.events)
        if event.event_type == "TOOL_FAILED"
        and event.payload["observation"].request == ReadRequest("candidate.txt")
    ]
    failure_index, failure_observation = (
        candidate_failures[0] if candidate_failures else (-1, None)
    )
    later_decision_events = [
        event
        for index, event in enumerate(result.events)
        if event.event_type == "MODEL_DECISION"
        and index > failure_index
    ]
    continuation_request = (
        _plain(later_decision_events[0].payload["frame"].provider_wire_request)
        if later_decision_events
        else None
    )
    continuation_messages = (
        continuation_request["messages"]
        if isinstance(continuation_request, dict)
        else []
    )
    assistant_evidence, assistant_call = _assistant_tool_call_evidence(
        continuation_messages[-2] if len(continuation_messages) >= 2 else None
    )
    assistant_call_id = (
        assistant_call["id"] if assistant_evidence == "observed" else None
    )
    tool_message = continuation_messages[-1] if continuation_messages else {}
    visible_failure = (
        json.loads(tool_message["content"])
        if isinstance(tool_message, dict) and "content" in tool_message
        else {}
    )
    summary = {
        "verified": result.status == "completed",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "provider_requests": len(result.decision_frames),
        "model_calls": len(result.decision_frames),
        "candidate_read_failures": len(candidate_failures),
        "continued_decisions": len(later_decision_events),
        "continuation_evidence": assistant_evidence,
        "native_call_ids": [
            frame.provider_tool_call_id
            for frame in result.decision_frames
            if frame.provider_tool_call_id is not None
        ],
        **_usage(result),
    }
    print("DEEPSEEK_REAL_FAILURE_CONTINUATION=" + json.dumps(summary, sort_keys=True))

    assert result.status == "completed"
    assert failure_observation is not None, assistant_evidence
    assert failure_observation.result.ok is False
    assert later_decision_events
    assert assistant_evidence == "observed"
    assert assistant_call_id == failure_observation.provider_tool_call_id
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == failure_observation.provider_tool_call_id
    assert visible_failure["observation"]["result"]["ok"] is False
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "fallback"
    assert result.state == fold_execution_state(result.events)
