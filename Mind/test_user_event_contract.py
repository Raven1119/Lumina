"""Cognitive events need no Execution run; only trusted delivery can bind one."""
import json
from dataclasses import replace

import pytest

from Mind.event_loop import CognitiveModel
from Mind.experiment_a import ActivationInput, ExecutionObservation, _valid_activation
from Mind.organ import Evidence, MindInput, MindOrgan, MindResultEvent
from Mind.test_event_loop import step
from Mind.trace import (
    ACTIVITY_NATIVE_PROTOCOL_VERSION, ACTIVATION_STARTED, MIND_DIRECTIVE_APPLIED,
    MindTrace, TraceError, _thaw, replay_activation,
)


def input_value(event="owner-event"):
    return MindInput(event, "Assess the available information.", "owner-goal", 1,
                     "Understand the reported conditions.", None, None,
                     (Evidence("source", "The observation is incomplete.", "memory"),))


def model(transport):
    value = CognitiveModel(transport, contract="cognitive-chain-v62", thinking=False)
    value.native_protocol_version = ACTIVITY_NATIVE_PROTOCOL_VERSION
    return value


def belief(ref="source", identity="new:condition"):
    return {"kind": "belief", "id": identity, "claim": "The observation is incomplete.",
            "status": "supported", "basis": [{"ref": ref}]}


def trace_path(directory):
    return next(p for p in directory.glob("activation-*.jsonl") if ".native." not in p.name)


@pytest.mark.parametrize("fields", [
    {"execution_status": "running"},
    {"execution_ref": "run"},
    {"execution_observation": ExecutionObservation("Understand the reported conditions.", "running")},
])
def test_incomplete_execution_identity_is_rejected_before_any_call(tmp_path, fields):
    with MindOrgan(directory=tmp_path, model=model(lambda _: pytest.fail("model called"))) as mind:
        with pytest.raises(ValueError):
            mind.activate(replace(input_value(), **fields))
        assert mind.inspect().revision == 0
    assert not (tmp_path / "cognition.json").exists()


def test_no_execution_consultation_commits_after_restart_with_exact_source(tmp_path):
    wires = []
    def answer(wire):
        wires.append(wire)
        if len(wires) == 1:
            return step({"type": "capability_request", "capability": "read_evidence", "refs": ["read-source"]},
                        [belief(identity="new:provisional")])
        return step({"type": "no_change"}, [belief("read-source")])

    options = dict(directory=tmp_path, available_capabilities=("inspect_execution", "read_evidence"))
    value = input_value()
    with MindOrgan(model=model(answer), **options) as mind:
        pending = mind.activate(value)
        assert pending.status == "waiting" and mind.inspect().revision == 0
        assert not mind.inspect().items
    start = MindTrace.reopen(trace_path(tmp_path)).events[0]
    assert start.payload["activation"]["execution_status"] is None
    assert start.payload["cognitive_context"]["execution_ref"] is None
    assert start.payload["available_capabilities"] == ("read_evidence",)
    choices = wires[0]["tools"][0]["input_schema"]["properties"]["next"]["oneOf"]
    assert not any(c["properties"].get("capability", {}).get("enum") == ["inspect_execution"] for c in choices)
    observation = {"capability": "read_evidence", "origin": "execution",
                   "text": json.dumps({"read_result": "sources-v1", "sources": [
                       {"ref": "read-source", "text": "The observation is incomplete.", "origin": "memory"}]})}
    with MindOrgan(model=model(answer), **options) as mind:
        with pytest.raises(ValueError):
            mind.accept_result(MindResultEvent("unrelated-request", observation))
        assert mind.inspect().revision == 0
        accepted = mind.accept_result(MindResultEvent(pending.request.request_ref, observation))
        assert accepted.status == "accepted" and mind.inspect().revision == 1
        assert [_thaw(i)["basis"] for i in mind.inspect().items] == [[{"ref": "read-source"}]]
        assert mind.read_source("read-source")["origin"] == "memory"
    with MindOrgan(model=model(lambda _: pytest.fail("replay dispatched")), **options) as mind:
        assert mind.activate(value).status == "duplicate"
        assert mind.inspect().revision == 1 and len(mind.inspect().items) == 1
    assert len(wires) == 2
    assert wires[1]["messages"][0] == wires[0]["messages"][0]
    assert replay_activation(MindTrace.reopen(trace_path(tmp_path)).events).failure_code is None


def test_no_execution_directive_binds_once_to_a_real_first_decision_after_restart(tmp_path):
    advice = "The available evidence warrants a bounded investigation."
    value = input_value()
    with MindOrgan(directory=tmp_path, model=model(lambda _: step({"type": "directive", "text": advice}))) as mind:
        assert mind.activate(value).status == "accepted"
    args = dict(execution_ref="actual-run", decision_id="decision-000001",
                intention_ref="owner-goal", intention_revision=1)
    with MindOrgan(directory=tmp_path, model=None) as mind:
        for wrong in ({"decision_id": "decision-000002"}, {"intention_ref": "other-goal"},
                      {"intention_revision": 2}, {"successor_of": "fictional-predecessor"}):
            assert mind.prepare_directive(value.event_id, **{**args, **wrong}) is None
        application = mind.prepare_directive(value.event_id, **args)
        assert application.text == advice
    with MindOrgan(directory=tmp_path, model=None) as mind:
        assert mind.prepare_directive(value.event_id, **args) == application
        assert mind.prepare_directive(value.event_id, **{**args, "execution_ref": "different-run"}) is None
    events = MindTrace.reopen(trace_path(tmp_path)).events
    assert sum(e.event_type == MIND_DIRECTIVE_APPLIED for e in events) == 1
    with MindOrgan(directory=tmp_path, model=model(lambda _: step({"type": "no_change"}))) as mind:
        assert mind.activate(replace(value, event_id="later-owner-event")).status == "accepted"
        assert mind.prepare_directive(value.event_id, **args) is None
        assert mind.prepare_directive("later-owner-event", **args) is None


def test_failure_without_execution_preserves_prior_state_and_cannot_bind(tmp_path):
    with MindOrgan(directory=tmp_path, model=model(lambda _: step({"type": "no_change"}, [belief()]))) as mind:
        assert mind.activate(input_value()).status == "accepted"
        before = mind.inspect()
    def fail(_):
        raise RuntimeError("synthetic model failure")
    with MindOrgan(directory=tmp_path, model=model(fail)) as mind:
        failed = mind.activate(input_value("failed-event"))
        assert failed.status == "failed" and failed.output is None
        assert mind.inspect() == before
        assert mind.prepare_directive("failed-event", execution_ref="actual-run", decision_id="decision-000001",
                                      intention_ref="owner-goal", intention_revision=1) is None
    with MindOrgan(directory=tmp_path, model=None) as mind:
        assert mind.inspect() == before


def test_null_status_requires_explicit_new_trace_input_shape(tmp_path):
    with MindOrgan(directory=tmp_path / "mind", model=model(lambda _: step({"type": "no_change"}))) as mind:
        assert mind.activate(input_value()).status == "accepted"
    payload = _thaw(MindTrace.reopen(trace_path(tmp_path / "mind")).events[0].payload)
    legacy = json.loads(json.dumps(payload))
    legacy["cognitive_context"].pop("execution_ref")
    with pytest.raises(TraceError):
        MindTrace.create(tmp_path / "invalid-old.jsonl", activation_id="invalid-old").append(
            ACTIVATION_STARTED, legacy, source_event_seqs=())
    legacy["activation"]["execution_status"] = "running"
    MindTrace.create(tmp_path / "valid-old.jsonl", activation_id="valid-old").append(
        ACTIVATION_STARTED, legacy, source_event_seqs=())
    payload["activation"]["execution_status"] = "running"
    with pytest.raises(TraceError, match="invalid_execution_context"):
        MindTrace.create(tmp_path / "conflicting.jsonl", activation_id="conflicting").append(
            ACTIVATION_STARTED, payload, source_event_seqs=())
    assert not _valid_activation(ActivationInput("Owner question", "Goal", None))


def test_retained_sources_project_in_stable_order_without_rewriting_old_trace(tmp_path):
    evidence = (Evidence("z-source", "The observation is incomplete.", "memory"),
                Evidence("a-source", "The observation is incomplete.", "memory"))
    value = replace(input_value(), evidence=evidence)
    with MindOrgan(directory=tmp_path, model=model(lambda _: step({"type": "no_change"},
            [belief("z-source", "new:z"), belief("a-source", "new:a")]))) as mind:
        assert mind.activate(value).status == "accepted"
        saved = [_thaw(i) for i in mind.inspect().items]
    old_trace = trace_path(tmp_path)
    original_bytes = old_trace.read_bytes()
    with MindOrgan(directory=tmp_path, model=model(lambda _: step({"type": "no_change"}, saved))) as mind:
        assert mind.activate(replace(value, event_id="second", evidence=())).status == "accepted"
    journal = json.loads((tmp_path / "cognition.json").read_text(encoding="utf-8"))
    second = next(r for r in journal["records"] if r["kind"] == "started" and r["event_id"] == "second")
    assert [s["ref"] for s in second["context"]["evidence"]] == ["a-source", "z-source"]
    assert old_trace.read_bytes() == original_bytes
