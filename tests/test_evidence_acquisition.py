from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from types import SimpleNamespace

import httpx
import pytest

from core.evidence_acquisition import SOURCE_TOOLS, acquire_sources
from core.model_client import DEEPSEEK_MODEL, DeepSeekAnthropicModelClient


@dataclass(frozen=True)
class Limits:
    max_chars: int = 5000
    max_searches: int = 3
    max_reads: int = 6


class Reader:
    question = "What changed in the original plan?"
    limits = Limits()

    def __init__(self):
        self.operations = []
        self.literal = []

    def search(self, query):
        self.operations.append(("search", query))
        source = {"role": "USER", "turn": 0, "text": "Original source: the first trial was declined."}
        self.literal.append(source)
        return {"sources": [source], "query": query, "segment_turn_counts": {"segment-one": 4}}

    def read(self, **args):
        self.operations.append(("read", deepcopy(args)))
        source = {"role": "USER", "turn": 2, "text": "Original source: only the revised trial was approved."}
        self.literal.append(source)
        return {"sources": [source], "requested_complete": False,
                "next_range": {"segment_id": "segment-one", "start_turn": 3, "end_turn": 3}}

    def context(self):
        return SimpleNamespace(evidence=tuple(deepcopy(self.literal)),
                               rendered_text="\n".join(x["text"] for x in self.literal))


def tool(tool_id="call-search", name="search_sources", args=None):
    return {"type": "tool_use", "id": tool_id, "name": name,
            "input": {"query": "model-generated search strategy"} if args is None else args}


def response(*blocks, reason="tool_use"):
    return {"content": list(blocks), "stop_reason": reason,
            "usage": {"input_tokens": 12, "output_tokens": 7}}


def finished(text="model-generated end status; not source evidence"):
    return response({"type": "text", "text": text}, reason="end_turn")


def make_client(payloads):
    bodies, wire_bytes = [], []
    pending = iter(payloads)

    def handler(request):
        bodies.append(json.loads(request.content))
        wire_bytes.append(bytes(request.content))
        current = next(pending)
        if isinstance(current, BaseException):
            raise current
        if isinstance(current, httpx.Response):
            return current
        return httpx.Response(200, json=current)

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    client = DeepSeekAnthropicModelClient("synthetic-key", "https://provider.invalid/anthropic",
        DEEPSEEK_MODEL, max_tokens=321, temperature=0.0, http_client=transport)
    return client, bodies, wire_bytes


def acquire(reader, client, **kwargs):
    return acquire_sources(reader, client, [], system_background="Full shared background", **kwargs)


def test_native_tools_continue_with_original_blocks_and_only_literal_context():
    reader = Reader()
    search = response({"type": "text", "text": "Generated strategy: check later corrections."}, tool())
    read_args = {"segment_id": "segment-one", "start_turn": 1, "end_turn": 2,
                 "start_char": 4, "end_char": 31}
    read = response(tool("call-read", "read_sources", read_args))
    end = finished()
    client, bodies, wire = make_client([search, read, end])
    near = [{"role": "user", "text": "Keep the original scope."},
            {"role": "assistant", "text": "The original scope stays relevant."}]
    before = deepcopy(near)
    result = acquire_sources(reader, client, near, system_background="Full shared background")
    assert result.stop_reason == "model_finished" and len(bodies) == len(result.rounds) == 3
    assert near == before
    assert bodies[0]["messages"] == [{"role": x["role"], "content": x["text"]} for x in near] + [
        {"role": "user", "content": reader.question}]
    assert bodies[1]["messages"][-2] == {"role": "assistant", "content": search["content"]}
    assert bodies[2]["messages"][-2] == {"role": "assistant", "content": read["content"]}
    for index in (1, 2):
        native_result = bodies[index]["messages"][-1]
        assert native_result["role"] == "user"
        assert native_result["content"] == list(result.rounds[index-1]["tool_results"])
        assert native_result["content"][0]["type"] == "tool_result"
    assert bodies[2]["messages"][-1]["content"][0]["tool_use_id"] == "call-read"
    assert json.loads(bodies[2]["messages"][-1]["content"][0]["content"])["next_range"]["start_turn"] == 3
    assert reader.operations == [("search", "model-generated search strategy"), ("read", read_args)]
    assert len(result.context.evidence) == 2
    assert "model-generated" not in result.context.rendered_text
    assert "Generated strategy" not in result.context.rendered_text
    assert "What changed" not in result.context.rendered_text
    assert all(round_["request_body"] == body for round_, body in zip(result.rounds, bodies))
    assert all(body["tools"] == SOURCE_TOOLS and body["max_tokens"] == 321
               and body["temperature"] == 0.0 for body in bodies)
    assert len({body["system"] for body in bodies}) == 1
    assert result.native_request_bytes == sum(map(len, wire))


def test_model_can_finish_without_search_or_read():
    reader = Reader()
    client, bodies, _ = make_client([finished()])
    result = acquire(reader, client)
    assert result.stop_reason == "model_finished" and len(bodies) == 1
    assert reader.operations == [] and result.context.evidence == ()


def test_disabled_continuation_still_executes_first_native_batch_only():
    reader = Reader()
    calls = [tool("s"), tool("r", "read_sources", {"segment_id": "segment-one", "start_turn": 1, "end_turn": 2})]
    client, bodies, _ = make_client([response(*calls)])
    result = acquire(reader, client, allow_continuation=False)
    assert result.stop_reason == "continuation_disabled" and len(bodies) == 1
    assert len(reader.operations) == len(result.rounds[0]["tool_results"]) == 2
    assert len(result.context.evidence) == 2


def test_five_valid_reads_in_one_native_batch_use_cumulative_reader_quota():
    reader = Reader()
    calls = [tool(f"read-{i}", "read_sources", {
        "segment_id": "segment-one", "start_turn": i, "end_turn": i,
    }) for i in range(5)]
    assert len(calls) <= reader.limits.max_reads
    client, bodies, _ = make_client([response(*calls), finished()])
    result = acquire(reader, client)
    assert result.stop_reason == "model_finished"
    assert len(bodies) == len(result.rounds) == 2
    assert reader.operations == [("read", call["input"]) for call in calls]
    native_results = bodies[1]["messages"][-1]["content"]
    assert [item["tool_use_id"] for item in native_results] == [call["id"] for call in calls]
    assert native_results == result.rounds[0]["tool_results"]
    assert all(not item["is_error"] for item in native_results)
    assert len(result.context.evidence) == 5
    assert "model-generated" not in result.context.rendered_text


@pytest.mark.parametrize("native,reason", [
    (response(42), "invalid_native_response"),
    (response(tool(), reason="end_turn"), "invalid_native_response"),
    (response(tool("")), "invalid_native_response"),
    (response(tool(None)), "invalid_native_response"),
    (response({"type": "text", "text": "Incomplete"}, reason="max_tokens"), "incomplete_native_response"),
    (response(tool(), reason="max_tokens"), "invalid_native_response"),
])
def test_invalid_native_response_stops_before_dispatch(native, reason):
    reader = Reader()
    client, bodies, _ = make_client([native])
    result = acquire(reader, client)
    assert result.stop_reason == reason and len(bodies) == 1
    assert reader.operations == []


@pytest.mark.parametrize("call,error", [
    (tool(name="delete_history", args={}), "invalid_source_tool"),
    (tool(args={"query": "q", "extra": True}), "invalid_source_tool"),
    (tool(name="read_sources", args={"segment_id": "segment-one"}), "invalid_source_tool"),
    ({**tool(), "input": []}, "invalid_source_tool_arguments"),
])
def test_unknown_or_invalid_tool_reports_error_without_dispatch(call, error):
    reader = Reader()
    client, bodies, _ = make_client([response(call), finished()])
    result = acquire(reader, client)
    assert result.stop_reason == "model_finished" and len(bodies) == 2
    native_result = bodies[1]["messages"][-1]["content"][0]
    assert native_result["is_error"] and json.loads(native_result["content"]) == {"error": error}
    assert reader.operations == [] and result.context.evidence == ()


def test_duplicate_tool_ids_in_one_response_stop_before_any_dispatch():
    reader = Reader()
    client, bodies, _ = make_client([response(tool("duplicate"), tool("duplicate"))])
    result = acquire(reader, client)
    assert result.stop_reason == "invalid_native_response" and len(bodies) == 1
    assert reader.operations == []


def test_duplicate_tool_id_across_rounds_does_not_repeat_dispatch():
    reader = Reader()
    client, bodies, _ = make_client([response(tool("duplicate")), response(tool("new-id"), tool("duplicate")), finished()])
    result = acquire(reader, client)
    assert result.stop_reason == "invalid_native_response" and len(bodies) == 2
    assert len(reader.operations) == len(result.context.evidence) == 1


def test_tool_loop_obeys_model_call_budget_without_automatic_final_answer():
    reader = Reader()
    client, bodies, _ = make_client([response(tool(str(i))) for i in range(3)])
    result = acquire(reader, client, max_model_calls=3)
    assert result.stop_reason == "model_call_budget"
    assert len(bodies) == len(reader.operations) == len(result.rounds) == 3


@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("private transport detail"),
    OSError("private log-write failure after transport observation"),
    httpx.Response(503, json={"private": "provider detail"}),
    httpx.Response(200, content=b"not-json"),
    httpx.Response(200, json={"content": "invalid native body"}),
])
def test_model_or_transport_log_failure_keeps_acquired_evidence_and_never_retries(failure):
    reader = Reader()
    client, bodies, _ = make_client([response(tool()), failure])
    result = acquire(reader, client)
    assert result.stop_reason == "source_model_unavailable"
    assert len(bodies) == len(result.rounds) == 2 and len(reader.operations) == 1
    assert len(result.context.evidence) == 1
    assert result.rounds[-1]["error"] == "source_model_unavailable"
    assert "private" not in repr(result.rounds[-1])


@pytest.mark.parametrize("failure_kind", ["preview_exception", "serialization_failure"])
def test_request_preview_failure_stops_and_preserves_existing_source(monkeypatch, failure_kind):
    reader = Reader()
    reader.search("prior source read")
    client, bodies, _ = make_client([])
    def fail(*_args, **_kwargs):
        if failure_kind == "serialization_failure":
            return {"unserializable": object()}
        raise OSError("private preview/log failure")
    monkeypatch.setattr(client, "exchange_request", fail)
    result = acquire(reader, client)
    assert result.stop_reason == "source_request_unavailable"
    assert bodies == [] and len(result.context.evidence) == 1
    assert result.native_request_chars == result.native_request_bytes == 0


@pytest.mark.parametrize("failure_kind", ["trace_exception", "result_serialization"])
def test_reader_trace_log_failure_stops_without_more_model_or_tool_calls(failure_kind):
    reader = Reader()
    original = reader.search
    def failing_search(query):
        original(query)
        if failure_kind == "result_serialization":
            return {"unserializable": object()}
        raise OSError("private source trace log failure")
    reader.search = failing_search
    client, bodies, _ = make_client([response(tool(), tool("must-not-run"))])
    result = acquire(reader, client)
    assert result.stop_reason == "source_tool_unavailable"
    assert len(bodies) == len(reader.operations) == len(result.context.evidence) == 1
    assert "private" not in repr(result.rounds)


def test_whole_native_request_budget_counts_cumulative_tools_context_and_utf8():
    reader = Reader()
    reader.question = "\u4eca\u5929\u7684\u5b89\u6392\u6539\u4e86\u4ec0\u4e48\uff1f"
    native = response(tool())
    client, bodies, wire = make_client([native, finished()])
    reference = acquire(reader, client)
    sizes = [len(item.decode("utf8")) for item in wire]
    assert sizes == [r["request_chars"] for r in reference.rounds]
    assert [len(item) for item in wire] == [r["request_bytes"] for r in reference.rounds]
    assert reference.native_request_chars == sum(sizes)
    assert reference.native_request_bytes == sum(map(len, wire)) > reference.native_request_chars
    assert sizes[1] > sizes[0]
    for budget, expected_calls in [(sizes[0]-1, 0), (sizes[0], 1), (sum(sizes)-1, 1), (sum(sizes), 2)]:
        limited = Reader()
        limited.question = reader.question
        client, sent, _ = make_client([native, finished()])
        result = acquire(limited, client, max_request_chars=budget)
        assert len(sent) == expected_calls
        assert result.native_request_chars == sum(sizes[:expected_calls]) <= budget
        assert result.stop_reason == ("model_finished" if expected_calls == 2 else "native_request_budget")
        assert len(limited.operations) == min(1, expected_calls)


@pytest.mark.parametrize("kwargs", [
    {"max_model_calls": 0}, {"max_model_calls": True}, {"max_model_calls": 1.5},
    {"max_request_chars": 0}, {"max_request_chars": True}, {"allow_continuation": 1},
])
def test_invalid_hard_limits_rejected_before_transport(kwargs):
    reader = Reader()
    client, bodies, _ = make_client([])
    with pytest.raises(ValueError, match="invalid_acquisition_limits"):
        acquire(reader, client, **kwargs)
    assert bodies == [] and reader.operations == []


def test_native_request_preview_and_caller_inputs_are_independent_copies():
    client, bodies, _ = make_client([finished()])
    messages = [{"role": "user", "content": "original question"}]
    tools = deepcopy(SOURCE_TOOLS)
    preview = client.exchange_request(messages, system_prompt="background", tools=tools)
    preview["messages"][0]["content"] = "changed preview"
    preview["tools"][0]["name"] = "changed preview tool"
    client.exchange(messages, system_prompt="background", tools=tools)
    assert bodies[0]["messages"] == messages and bodies[0]["tools"] == SOURCE_TOOLS


def test_invalid_utf8_request_stops_in_preflight_without_charging_or_sending():
    reader = Reader()
    reader.search("prior valid source")
    reader.question = "\ud800"
    client, bodies, _ = make_client([])
    result = acquire(reader, client)
    assert result.stop_reason == "source_request_unavailable"
    assert bodies == [] and len(result.context.evidence) == 1
    assert result.native_request_chars == result.native_request_bytes == 0
