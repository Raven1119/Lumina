from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from Conversation_Memory.adapter.models import MemoryContext
from Mind.organ import Evidence, MindInput, MindOrgan, MindResultEvent, ModelFeedback, ModelProbe
from Mind.test_experiment_a import FakeMemoryRetriever, ScriptedModel
from Mind.trace import MIND_DIRECTIVE_ISSUED, MindTrace, replay_activation


def _event(event_id="event-1", **changes):
    value = MindInput(
        event_id=event_id, trigger="Investigate why the request failed.",
        intention_ref="investigation-1", intention_revision=1,
        goal="Explain the failure and identify a useful next observation.",
        execution_ref="execution-1", execution_status="waiting",
        evidence=(Evidence("result-1", "Request failed; the cause is unknown.", "execution"),),
    )
    return replace(value, **changes)


def _belief(identity="new:network", *, status="open", ref="result-1",
            quote="Request failed", claim="The network may be unavailable."):
    return {"kind": "belief", "id": identity, "claim": claim, "status": status,
            "basis": [{"ref": ref, "quote": quote}],
            "discriminator": "Whether the same endpoint responds with fresh credentials."}


def _response(updates=(), output=None):
    return json.dumps({"type": "cognitive_step", "updates": list(updates),
                       "next": output or {"type": "no_change"}})


def _organ(path, model):
    return MindOrgan(directory=path, model=model, available_capabilities=("recall_memory",))


def _activate_with_synthetic_host(mind, event, memory=None):
    """Legacy mechanical cases now explicitly exchange a result outside Mind."""
    from Mind.experiment_a import ActivationFailure, _parse_output, _resolve_capability, _execution_observation
    from Mind.trace import _thaw
    receipt = mind.activate(event)
    if receipt.status != "waiting":
        return receipt
    request = receipt.request
    parsed = _parse_output(json.dumps({"type": "capability_request", **_thaw(request.payload)}), compute=True)
    observation = _resolve_capability(parsed, memory_retriever=memory,
        prepared_execution_observation=(_execution_observation(event.execution_observation)
                                        if event.execution_observation else None),
        probe=_thaw(request.model_probe), allow_model_computation=True)
    return mind.accept_result(MindResultEvent(request.request_ref,
        error=observation.code) if isinstance(observation, ActivationFailure)
        else MindResultEvent(request.request_ref, observation))


def test_understanding_survives_restart_and_new_evidence_revises_the_same_hypothesis(tmp_path):
    first_model = ScriptedModel([_response([_belief()])])
    with _organ(tmp_path, first_model) as mind:
        first = mind.activate(_event())
        assert first.status == "accepted"
        assert first.output == {"type": "no_change"}
        original_view = mind.inspect()
        hypothesis_id = original_view.items[0]["id"]
        assert original_view.revision == 1
        with pytest.raises(TypeError):
            original_view.items[0]["claim"] = "mutate accepted state"
    second_event = _event("event-2", evidence=(
        Evidence("result-2", "The endpoint responded after replacing the expired token.", "execution"),
    ))
    second_model = ScriptedModel([_response([
        _belief(hypothesis_id, status="contradicted", ref="result-2", quote="The endpoint responded"),
        _belief("new:credentials", status="supported", ref="result-2", quote="replacing the expired token",
                claim="The failure is consistent with expired credentials."),
        {"kind": "question", "id": "new:lifetime", "text": "Why did credential renewal stop?",
         "status": "open", "basis": [{"ref": "result-2", "quote": "expired token"}]},
    ], {"type": "directive", "text": "Investigate credential renewal; the endpoint is reachable."})])
    with _organ(tmp_path, second_model) as mind:
        assert mind.inspect() == original_view
        result = mind.activate(second_event)
        assert result.status == "accepted"
        view = mind.inspect()
        assert view.revision == 2
        assert next(item for item in view.items if item["id"] == hypothesis_id)["status"] == "contradicted"
        assert any(item["kind"] == "question" for item in view.items)
        actual_input = json.loads(second_model.calls[0]["user_message"])
        assert actual_input["cognition"]["items"][0]["id"] == hypothesis_id
        assert mind.activate(second_event).status == "duplicate"
        assert len(second_model.calls) == 1
        arguments = dict(execution_ref="execution-1", decision_id="decision-2",
                         intention_ref="investigation-1", intention_revision=1)
        assert mind.prepare_directive("event-2", **{**arguments, "execution_ref": "other-run"}) is None
        application = mind.prepare_directive("event-2", **arguments)
        assert application is not None
        assert application.decision_id == "execution-1:root:decision-2"
        assert mind.prepare_directive("event-2", **arguments) == application
        assert mind.prepare_directive("event-2", **{**arguments, "decision_id": "decision-3"}) is None
    with _organ(tmp_path, ScriptedModel([])) as mind:
        assert mind.inspect() == view


def test_recall_is_optional_and_actual_requests_replay_with_durable_observation_refs(tmp_path):
    model = ScriptedModel([
        _response(output={"type": "capability_request", "capability": "recall_memory",
                          "query": "previous endpoint failure"}),
        _response([_belief(ref="activation:observation", quote="Credentials expired.")]),
        _response(),
    ])
    memory = FakeMemoryRetriever(MemoryContext(query="", rendered_text="Credentials expired."))
    with MindOrgan(directory=tmp_path, model=model, available_capabilities=("recall_memory",)) as mind:
        assert _activate_with_synthetic_host(mind, _event(), memory).status == "accepted"
        view = mind.inspect()
        basis_ref = view.items[0]["basis"][0]["ref"]
        assert basis_ref.startswith("activation-") and basis_ref.endswith(":observation")
        trace_path = next(tmp_path.glob("activation-*.jsonl"))
        replay = replay_activation(MindTrace.reopen(trace_path).events)
        assert [request.as_model_call() for request in replay.model_requests] == model.calls
        assert len(memory.calls) == 1 and len(model.calls) == 2
        assert _activate_with_synthetic_host(mind, _event("event-2")).status == "accepted"
        input_two = json.loads(model.calls[-1]["user_message"])
        assert any(item["ref"] == basis_ref and item["text"] == "Credentials expired."
                   for item in input_two["cognition"]["evidence"])
        assert len(memory.calls) == 1


def test_qualitative_scenario_depends_on_explicit_hypotheses(tmp_path):
    scenario = {"kind": "scenario", "id": "new:renew", "assumptions": ["new:network"],
                "steps": [{"state": "Failure unresolved", "actors": "Service and client",
                           "action": "Client retries", "external": "Network remains unavailable",
                           "outcome": "The request still fails"}],
                "unknowns": ["Actual network availability"], "status": "active"}
    model = ScriptedModel([_response([_belief(), scenario])])
    with _organ(tmp_path, model) as mind:
        assert mind.activate(_event()).status == "accepted"
        view = mind.inspect()
        scenario_view = next(item for item in view.items if item["kind"] == "scenario")
        belief_view = next(item for item in view.items if item["kind"] == "belief")
        assert scenario_view["assumptions"] == (belief_view["id"],)
        assert scenario_view["analysis_status"] == "QUALITATIVE"
        assert "probability" not in scenario_view


@pytest.mark.parametrize("bad_update", [
    _belief(ref="invented-ref"),
    _belief(quote="This sentence was not observed."),
    {**_belief(), "probability": 0.95},
])
def test_invalid_cognition_is_atomic_and_issued_directive_cannot_escape(tmp_path, bad_update):
    model = ScriptedModel([_response([bad_update], {"type": "directive", "text": "Change direction."})])
    with _organ(tmp_path, model) as mind:
        result = mind.activate(_event())
        assert result.status == "failed"
        assert mind.inspect().revision == 0 and not mind.inspect().items
        assert mind.prepare_directive("event-1", execution_ref="execution-1", decision_id="decision-1",
                                      intention_ref="investigation-1", intention_revision=1) is None
        assert mind.activate(_event()).status == "failed"
        assert len(model.calls) == 1


def test_identity_goal_and_source_conflicts_do_not_start_a_model_call(tmp_path):
    model = ScriptedModel([_response([_belief()])])
    with _organ(tmp_path, model) as mind:
        mind.activate(_event())
        with pytest.raises(ValueError, match="event_identity_conflict"):
            mind.activate(_event(trigger="Different event payload"))
        with pytest.raises(ValueError, match="intention_conflict"):
            mind.activate(_event("event-2", intention_revision=2))
        with pytest.raises(ValueError, match="evidence_identity_conflict"):
            mind.activate(_event("event-2", evidence=(Evidence("result-1", "Changed source.", "execution"),)))
        assert len(model.calls) == 1


def test_persisted_final_output_is_finished_after_crash_without_resampling(tmp_path, monkeypatch):
    model = ScriptedModel([_response([_belief()], {"type": "directive", "text": "Resolve the competing explanations."})])
    append = MindTrace.append
    def crash_at_terminal(self, event_type, *args, **kwargs):
        if event_type == MIND_DIRECTIVE_ISSUED:
            raise KeyboardInterrupt("simulated process crash after model output persistence")
        return append(self, event_type, *args, **kwargs)
    with _organ(tmp_path, model) as mind:
        with monkeypatch.context() as patch:
            patch.setattr(MindTrace, "append", crash_at_terminal)
            with pytest.raises(KeyboardInterrupt):
                mind.activate(_event())
    with _organ(tmp_path, ScriptedModel([])) as mind:
        receipt = mind.activate(_event())
        assert receipt.status == "accepted"
        assert mind.inspect().revision == 1
        assert mind.activate(_event()).status == "duplicate"
    assert len(model.calls) == 1


def test_commit_write_failure_can_recover_and_has_no_premature_delivery(tmp_path, monkeypatch):
    model = ScriptedModel([_response([_belief()], {"type": "directive", "text": "Resolve the competing explanations."})])
    with _organ(tmp_path, model) as mind:
        append = mind._append
        def fail_commit(record):
            if record["kind"] == "accepted":
                raise OSError("simulated storage failure")
            return append(record)
        with monkeypatch.context() as patch:
            patch.setattr(mind, "_append", fail_commit)
            with pytest.raises(OSError):
                mind.activate(_event())
        assert mind.inspect().revision == 0
        assert mind.prepare_directive("event-1", execution_ref="execution-1", decision_id="decision-1",
                                      intention_ref="investigation-1", intention_revision=1) is None
        assert mind.activate(_event()).status == "accepted"
        assert len(model.calls) == 1


def test_second_writer_is_rejected_and_malformed_history_never_reaches_model(tmp_path):
    model = ScriptedModel([_response()])
    with _organ(tmp_path, model) as mind:
        with pytest.raises(OSError):
            _organ(tmp_path, ScriptedModel([]))
        mind.activate(_event())
    path = tmp_path / "cognition.json"
    original = path.read_bytes()
    path.write_bytes(original[:-8])
    with pytest.raises(ValueError):
        _organ(tmp_path, ScriptedModel([]))
    assert len(model.calls) == 1


def test_provisional_updates_continue_through_read_but_only_final_updates_are_accepted(tmp_path):
    proposal = _belief()
    final = _belief("new:credentials", ref="activation:observation", quote="Credentials expired.",
                    claim="Expired credentials may explain the failure.")
    memory = FakeMemoryRetriever(MemoryContext(query="", rendered_text="Credentials expired."))

    class ObservingModel(ScriptedModel):
        def generate(self, *args, **kwargs):
            records = json.loads((tmp_path / "cognition.json").read_text(encoding="utf-8"))["records"]
            assert [record["kind"] for record in records] == (["started"] if not self.calls else ["started", "result_received"])
            return super().generate(*args, **kwargs)

    model = ObservingModel([
        _response([proposal], {"type": "capability_request", "capability": "recall_memory", "query": "endpoint"}),
        _response([final]),
    ])
    with MindOrgan(directory=tmp_path, model=model, available_capabilities=("recall_memory",)) as mind:
        result = _activate_with_synthetic_host(mind, _event(), memory)
        assert result.status == "accepted"
        assert [item["claim"] for item in mind.inspect().items] == [final["claim"]]
        first, second = [json.loads(call["user_message"]) for call in model.calls]
        assert first["available_capabilities"] == ["recall_memory"]
        assert second["available_capabilities"] == []
        assert second["pending_updates"] == [proposal]
        assert second["cognition"]["revision"] == 0 and second["cognition"]["items"] == []
        replay = replay_activation(MindTrace.reopen(next(tmp_path.glob("activation-*.jsonl"))).events)
        assert [request.as_model_call() for request in replay.model_requests] == model.calls
    with _organ(tmp_path, ScriptedModel([])) as mind:
        assert mind.inspect().revision == 1


def test_failed_final_step_discards_candidates_and_cannot_expand_read_budget(tmp_path):
    read = {"type": "capability_request", "capability": "recall_memory", "query": "endpoint"}
    model = ScriptedModel([_response([_belief()], read), _response([_belief()], read)])
    memory = FakeMemoryRetriever()
    with MindOrgan(directory=tmp_path, model=model, available_capabilities=("recall_memory",)) as mind:
        receipt = _activate_with_synthetic_host(mind, _event(), memory)
        assert receipt.status == "failed" and receipt.error == "capability_limit_exceeded"
        assert mind.inspect().revision == 0 and mind.inspect().items == ()
        assert len(memory.calls) == 1 and len(model.calls) == 2
        assert _activate_with_synthetic_host(mind, _event(), memory).status == "failed" and len(model.calls) == 2


def test_unavailable_read_is_not_advertised_or_executed(tmp_path):
    model = ScriptedModel([_response([_belief()], {"type": "capability_request", "capability": "inspect_execution"})])
    with MindOrgan(directory=tmp_path, model=model, memory_retriever=None) as mind:
        receipt = mind.activate(_event())
        assert receipt.error == "capability_not_available"
        assert json.loads(model.calls[0]["user_message"])["available_capabilities"] == []
        assert len(model.calls) == 1 and mind.inspect().revision == 0


def _synthetic_legacy_replay(path, version, capabilities=()):
    """Exercise persisted old contracts without retaining real provider logs."""
    from Mind.trace import ACTIVATION_STARTED, MODEL_OUTPUT_RECORDED, ACTIVATION_FAILED, project_model_request
    payload = {
        "activation": {"execution_goal_snapshot": "Investigate the synthetic request failure.",
                       "execution_status": "waiting", "trigger": "Synthetic legacy replay input."},
        "information_acquisition_allowed": bool(capabilities),
        "projector_version": f"mind-cognitive-projector-v{version}",
        "prompt_version": f"mind-cognitive-prompt-v{version}",
        "cognitive_context": {"revision": 0, "event_id": "synthetic-legacy-event", "items": [], "evidence": []},
    }
    if version >= 2:
        payload["available_capabilities"] = list(capabilities)
    trace = MindTrace.create(path, activation_id=f"synthetic-legacy-v{version}")
    started = trace.append(ACTIVATION_STARTED, payload, source_event_seqs=())
    original_request = project_model_request(trace.events).as_model_call()
    output = trace.append(MODEL_OUTPUT_RECORDED, {"call_index": 1, "text": "{"},
                          source_event_seqs=(started.seq,))
    trace.append(ACTIVATION_FAILED, {"code": "invalid_model_output"}, source_event_seqs=(output.seq,))
    replay = replay_activation(MindTrace.reopen(path).events)
    assert replay.model_requests[0].as_model_call() == original_request
    assert replay.model_outputs == ("{",)
    assert replay.failure_code == "invalid_model_output"
    return original_request


def test_synthetic_v1_trace_replays_with_its_legacy_prompt(tmp_path):
    from Mind.trace import COGNITIVE_SYSTEM_PROMPT
    request = _synthetic_legacy_replay(tmp_path / "v1.jsonl", 1)
    assert request["system_prompt"] == COGNITIVE_SYSTEM_PROMPT
    assert "available_capabilities" not in json.loads(request["user_message"])


@pytest.mark.skipif(os.environ.get("LUMINA_TEST_WORLD_MODEL_DOCKER") != "1",
                    reason="explicit integrated Docker validation only")
def test_mind_computes_retains_and_independently_checks_model_across_process_restart(tmp_path):
    from Mind.test_world_model import SERVICE_MODEL, INITIAL, ACTIONS, OBSERVATIONS, OUTCOMES
    compute = {"type": "capability_request", "capability": "run_world_model", "source": SERVICE_MODEL,
               "initial_observation": INITIAL, "actions": ACTIONS}
    update = {"kind": "model", "id": "new:readiness", "status": "active",
              "scope": "Readiness changes after two checks; promotion requires readiness.",
              "basis": [{"ref": "service-trace", "quote": "Two checks preceded readiness."}],
              "artifact_ref": "activation:model", "unknowns": ["Readiness under unrelated service failures"]}
    model = ScriptedModel([_response(output=compute), _response([update]), _response()])
    memory = FakeMemoryRetriever()
    event = _event(evidence=(Evidence("service-trace", "Two checks preceded readiness.", "execution"),))
    with MindOrgan(directory=tmp_path, model=model, available_capabilities=("recall_memory",), allow_model_computation=True) as mind:
        assert _activate_with_synthetic_host(mind, event, memory).status == "accepted"
        item = mind.inspect().items[0]
        assert item["analysis_status"] == "COMPUTED"
        assert item["artifact"]["run"]["request"]["source"] == SERVICE_MODEL
        checked = mind.verify_model(item["id"], OBSERVATIONS, observed_outcomes=OUTCOMES)
        assert checked["dynamics"]["status"] == checked["outcome"]["status"] == "matched"
        assert not memory.calls and len(model.calls) == 2
        assert _activate_with_synthetic_host(mind, _event("event-2", evidence=event.evidence)).status == "accepted"
        context = json.loads(model.calls[-1]["user_message"])["cognition"]
        assert context["items"][0]["artifact"]["run"]["request"]["source"] == SERVICE_MODEL
        assert all(source["origin"] in {"memory", "execution"} for source in context["evidence"])
        assert all(source["ref"] != "activation:observation" for source in context["evidence"])
        model_id, digest = item["id"], item["artifact"]["run"]["source_digest"]
    probe = """import json,sys
from Mind.organ import MindOrgan
with MindOrgan(directory=sys.argv[1], model=None, memory_retriever=None) as mind:
    view = mind.inspect()
    print(json.dumps([view.revision, view.items[0]['id'], view.items[0]['artifact']['run']['source_digest']]))
"""
    restarted = subprocess.run([sys.executable, "-c", probe, str(tmp_path)],
                               cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(restarted.stdout) == [2, model_id, digest]


def test_model_failure_or_simulated_quote_cannot_become_reality_evidence(tmp_path, monkeypatch):
    from Mind import experiment_a
    from Mind.world_model import ModelComputationError
    compute = {"type": "capability_request", "capability": "run_world_model",
               "source": "raise RuntimeError('bad candidate')", "initial_observation": {"status": "unknown"}, "actions": []}

    def failed(**request):
        raise ModelComputationError("model_load_error")

    monkeypatch.setattr(experiment_a, "run_model", failed)
    model = ScriptedModel([_response(output=compute),
                           _response([_belief(ref="activation:observation", quote="failed")])])
    with MindOrgan(directory=tmp_path, model=model, memory_retriever=None, allow_model_computation=True) as mind:
        receipt = _activate_with_synthetic_host(mind, _event())
        assert receipt.error == "ungrounded_basis"
        assert mind.inspect().revision == 0
        observation = json.loads(model.calls[1]["user_message"])["observation"]
        assert observation["status"] == "failed" and observation["result"] is None


@pytest.mark.parametrize("version,capabilities,prompt_name", [
    (2, (), "COGNITIVE_STEP_SYSTEM_PROMPT"),
    (3, ("run_world_model",), "COGNITIVE_MODEL_SYSTEM_PROMPT"),
    (4, ("run_world_model",), "COGNITIVE_COMPACT_SYSTEM_PROMPT"),
])
def test_model_capability_is_explicit_and_synthetic_legacy_versions_replay(tmp_path, version, capabilities, prompt_name):
    from Mind import trace
    request = _synthetic_legacy_replay(tmp_path / "legacy.jsonl", version, capabilities)
    assert request["system_prompt"] == getattr(trace, prompt_name)
    assert json.loads(request["user_message"])["available_capabilities"] == list(capabilities)
    model = ScriptedModel([_response()])
    with _organ(tmp_path / "ordinary", model) as mind:
        assert mind.activate(_event()).status == "accepted"
        assert "run_world_model" not in json.loads(model.calls[0]["user_message"])["available_capabilities"]


def test_frozen_probe_and_feedback_bind_actual_input_and_historical_artifact(tmp_path, monkeypatch):
    from Mind import world_model
    from Mind.test_world_model import SERVICE_MODEL, INITIAL, ACTIONS, PREDICTION, OBSERVATIONS, OUTCOMES
    requests = []

    def compute(request):
        requests.append(json.loads(json.dumps(request)))
        return json.dumps(PREDICTION).encode()

    monkeypatch.setattr(world_model, "_compute", compute)
    probe = ModelProbe("readiness-probe", INITIAL, tuple(ACTIONS))
    update = {"kind": "model", "id": "new:readiness", "status": "active", "scope": "Conditional service readiness",
              "basis": [{"ref": "result-1", "quote": "Request failed"}], "artifact_ref": "activation:model", "unknowns": []}
    request = {"type": "capability_request", "capability": "evaluate_model", "probe_ref": probe.ref, "source": SERVICE_MODEL}
    model = ScriptedModel([_response(output=request), _response([update])])
    with MindOrgan(directory=tmp_path, model=model, memory_retriever=None, allow_model_computation=True) as mind:
        event = _event(model_probe=probe)
        assert _activate_with_synthetic_host(mind, event).status == "accepted"
        old = mind.inspect().items[0]
        old_ref = old["artifact_ref"]
        assert requests == [{"source": SERVICE_MODEL, "initial_observation": INITIAL, "actions": ACTIONS}]
        assert json.loads(model.calls[0]["user_message"])["available_capabilities"] == ["evaluate_model"]
        assert _activate_with_synthetic_host(mind, event).status == "duplicate" and len(requests) == 1
        with pytest.raises(ValueError, match="model_probe_identity_conflict"):
            _activate_with_synthetic_host(mind, _event("bad-probe", model_probe=replace(probe, actions=tuple(ACTIONS[:-1]))))
        model._responses.extend([_response(output={**request, "source": SERVICE_MODEL + "\n# revised"}),
                              _response([{**update, "id": old["id"]}])])
        assert _activate_with_synthetic_host(mind, _event("event-2", model_probe=probe)).status == "accepted"
        assert mind.inspect().items[0]["artifact_ref"] != old_ref
        changed = [*OBSERVATIONS[:-1], {"version": "v2", "status": "failed"}]
        feedback = ModelFeedback(old_ref, tuple(changed), (*OUTCOMES[:-1], "failed"), ("new-observation",))
        model._responses.append(_response())
        event = _event("event-3", model_feedback=feedback,
                       evidence=(Evidence("new-observation", "Promotion actually failed.", "execution"),))
        assert _activate_with_synthetic_host(mind, event).status == "accepted"
        context = json.loads(model.calls[-1]["user_message"])["cognition"]
        report = context["model_feedback"]
        assert report["artifact_ref"] == old_ref
        assert report["source_digest"] == old["artifact"]["run"]["source_digest"]
        assert report["dynamics"]["status"] == report["outcome"]["status"] == "mismatch"
        assert all(source["ref"] != old_ref for source in context["evidence"])
        assert len(requests) == 2  # Feedback never reruns a model program.
        assert _activate_with_synthetic_host(mind, event).status == "duplicate" and len(model.calls) == 5


def test_frozen_probe_does_not_accept_model_supplied_replacement_actions(tmp_path, monkeypatch):
    from Mind import experiment_a
    from Mind.test_world_model import INITIAL, ACTIONS, SERVICE_MODEL
    monkeypatch.setattr(experiment_a, "run_model", lambda **kwargs: pytest.fail("altered probe was executed"))
    probe = ModelProbe("fixed", INITIAL, tuple(ACTIONS))
    replacement = {"type": "capability_request", "capability": "evaluate_model", "probe_ref": "fixed",
                   "source": SERVICE_MODEL, "actions": ACTIONS[:-1]}
    model = ScriptedModel([_response(output=replacement)])
    with MindOrgan(directory=tmp_path, model=model, memory_retriever=None, allow_model_computation=True) as mind:
        receipt = mind.activate(_event(model_probe=probe))
        assert receipt.error == "invalid_model_output" and mind.inspect().revision == 0


def test_feedback_requires_reality_sources_and_a_durable_artifact(tmp_path):
    model = ScriptedModel([])
    feedback = ModelFeedback("unknown:model", (), (None,), ("missing",))
    with _organ(tmp_path, model) as mind:
        with pytest.raises(ValueError, match="invalid_model_feedback"):
            mind.activate(_event(model_feedback=feedback))
        with pytest.raises(ValueError, match="unknown_model_artifact"):
            mind.activate(_event(model_feedback=replace(feedback, source_refs=("result-1",))))
        assert model.calls == [] and not (tmp_path / "cognition.json").exists()


def _synthetic_result(request_ref):
    return MindResultEvent(request_ref, {
        "capability": "recall_memory", "rendered_evidence": "Credentials expired.",
        "status": "available", "truncated": False, "safe_error_code": None,
    })


def _read_step():
    return _response([_belief()], {"type": "capability_request", "capability": "recall_memory", "query": "prior failure"})


def test_deferred_request_and_result_identity_prevent_duplicate_inference(tmp_path, monkeypatch):
    model = ScriptedModel([_read_step(), _response()])
    with pytest.raises(ValueError, match="memory_requires_event_dispatch"):
        MindOrgan(directory=tmp_path, model=model, memory_retriever=FakeMemoryRetriever())
    with _organ(tmp_path, model) as mind:
        waiting = mind.activate(_event())
        assert waiting.status == "waiting" and len(model.calls) == 1
        assert mind.inspect().revision == 0 and not mind.inspect().items
        assert mind.activate(_event()).request == waiting.request
        assert mind.activate(_event("queued")).status == "busy"
        result = _synthetic_result(waiting.request.request_ref)
        before = (tmp_path / "cognition.json").read_bytes()
        with pytest.raises(ValueError, match="result_request_conflict"):
            mind.accept_result(replace(result, request_ref=result.request_ref + "0"))
        with pytest.raises(ValueError):
            mind.accept_result(replace(result, observation={**result.observation, "unexpected": "field"}))
        assert (tmp_path / "cognition.json").read_bytes() == before and len(model.calls) == 1
        original_append = mind._append
        def fail_receipt(record):
            if record["kind"] == "result_received":
                raise OSError("result receipt persistence failed")
            original_append(record)
        with monkeypatch.context() as patch:
            patch.setattr(mind, "_append", fail_receipt)
            with pytest.raises(OSError):
                mind.accept_result(result)
        assert mind.activate(_event()).request == waiting.request and len(model.calls) == 1
        assert mind.accept_result(result).status == "accepted"
        assert mind.accept_result(result).status == "duplicate" and len(model.calls) == 2
        with pytest.raises(ValueError, match="result_identity_conflict"):
            mind.accept_result(replace(result, observation={**result.observation, "rendered_evidence": "Changed."}))
    with pytest.raises(ValueError, match="mind_is_closed"):
        mind.accept_result(result)


def test_request_recovers_from_durable_first_output_without_another_call(tmp_path, monkeypatch):
    from Mind.trace import CAPABILITY_REQUESTED
    append = MindTrace.append
    def interrupted_request(self, event_type, *args, **kwargs):
        if event_type == CAPABILITY_REQUESTED:
            raise SystemExit("process exit before request append")
        return append(self, event_type, *args, **kwargs)
    with _organ(tmp_path, ScriptedModel([_read_step()])) as mind:
        with monkeypatch.context() as patch:
            patch.setattr(MindTrace, "append", interrupted_request)
            with pytest.raises(SystemExit):
                mind.activate(_event())
    model = ScriptedModel([_response()])
    with _organ(tmp_path, model) as mind:
        waiting = mind.activate(_event())
        assert waiting.status == "waiting" and not model.calls
        assert mind.accept_result(_synthetic_result(waiting.request.request_ref)).status == "accepted"
        assert len(model.calls) == 1


def test_nervous_computation_roundtrip_keeps_predictions_separate_from_evidence(tmp_path, monkeypatch):
    from Nervous.organ import NervousOrgan
    from Mind import world_model
    from Mind.host import activation_event, run_mind_once, run_computation_once
    from Mind.test_world_model import SERVICE_MODEL, INITIAL, ACTIONS, PREDICTION, OBSERVATIONS, OUTCOMES
    if os.environ.get("LUMINA_TEST_WORLD_MODEL_DOCKER") != "1":
        monkeypatch.setattr(world_model, "_compute", lambda request: json.dumps(PREDICTION).encode())
    update = {"kind": "model", "id": "new:readiness", "status": "active", "scope": "Conditional readiness",
              "basis": [{"ref": "result-1", "quote": "Request failed"}], "artifact_ref": "activation:model", "unknowns": []}
    event = _event(model_probe=ModelProbe("fixed-probe", INITIAL, tuple(ACTIONS)))
    request = {"type": "capability_request", "capability": "evaluate_model", "probe_ref": "fixed-probe", "source": SERVICE_MODEL}
    model = ScriptedModel([_response(output=request), _response([update])])
    with NervousOrgan(tmp_path / "nervous") as nervous, MindOrgan(
            directory=tmp_path / "mind", model=model, allow_model_computation=True) as mind:
        nervous.publish(activation_event(event))
        assert run_mind_once(nervous, mind).status == "waiting"
        assert mind.inspect().revision == 0 and len(model.calls) == 1
        assert run_computation_once(nervous)
        assert len(model.calls) == 1 and mind.inspect().revision == 0
        assert not run_computation_once(nervous)
        assert run_mind_once(nervous, mind).status == "accepted"
        item, = mind.inspect().items
        assert item["analysis_status"] == "COMPUTED"
        assert item["artifact"]["run"]["request"]["source"] == SERVICE_MODEL
        checked = mind.verify_model(item["id"], OBSERVATIONS, observed_outcomes=OUTCOMES)
        assert checked["dynamics"]["status"] == checked["outcome"]["status"] == "matched"
        journal = json.loads((tmp_path / "mind/cognition.json").read_text(encoding="utf-8"))
        assert [record["kind"] for record in journal["records"]] == ["started", "result_received", "accepted"]
        assert all(source["origin"] == "execution" for source in journal["records"][0]["context"]["evidence"])
        assert len(model.calls) == 2 and run_mind_once(nervous, mind) is None


def test_result_and_probe_identity_distinguish_boolean_and_integer_inputs(tmp_path, monkeypatch):
    from Mind import world_model
    from Mind.test_world_model import SERVICE_MODEL, INITIAL, ACTIONS, PREDICTION
    monkeypatch.setattr(world_model, "_compute", lambda request: json.dumps(PREDICTION).encode())
    initial = {**INITIAL, "flag": 1}
    probe = ModelProbe("fixed", initial, tuple(ACTIONS))
    request = {"type": "capability_request", "capability": "evaluate_model", "source": SERVICE_MODEL, "probe_ref": "fixed"}
    model = ScriptedModel([_response(output=request), _response()])
    with MindOrgan(directory=tmp_path, model=model, allow_model_computation=True) as mind:
        waiting = mind.activate(_event(model_probe=probe))
        wrong = world_model.run_model(source=SERVICE_MODEL, initial_observation={**initial, "flag": True}, actions=ACTIONS)
        result = MindResultEvent(waiting.request.request_ref, {
            "capability": "evaluate_model", "status": "computed", "result": wrong, "safe_error_code": None})
        with pytest.raises(ValueError, match="computation_request_mismatch"):
            mind.accept_result(result)
        assert len(model.calls) == 1
        correct = world_model.run_model(source=SERVICE_MODEL, initial_observation=initial, actions=ACTIONS)
        assert mind.accept_result(replace(result, observation={**result.observation, "result": correct})).status == "accepted"
        with pytest.raises(ValueError, match="model_probe_identity_conflict"):
            mind.activate(_event("changed-probe", model_probe=replace(probe, initial_observation={**initial, "flag": True})))


@pytest.mark.parametrize("crash_after_output", [False, True])
def test_result_recovery_never_resamples_uncertain_inference(tmp_path, monkeypatch, crash_after_output):
    from Mind import experiment_a
    with _organ(tmp_path, ScriptedModel([_read_step()])) as mind:
        waiting = mind.activate(_event())
    result = _synthetic_result(waiting.request.request_ref)

    class InterruptedModel(ScriptedModel):
        def generate(self, *args, **kwargs):
            if not crash_after_output:
                raise SystemExit("process interrupted during provider call")
            return super().generate(*args, **kwargs)

    with _organ(tmp_path, InterruptedModel([_response()])) as mind:
        with monkeypatch.context() as patch:
            if crash_after_output:
                def interrupted_terminal(*args):
                    raise SystemExit("process interrupted after durable model output")
                patch.setattr(experiment_a, "_record_terminal", interrupted_terminal)
            with pytest.raises(SystemExit):
                mind.accept_result(result)
    recovery_model = ScriptedModel([])
    with _organ(tmp_path, recovery_model) as mind:
        recovered = mind.accept_result(result)
        assert recovered.status == ("accepted" if crash_after_output else "failed")
        assert recovered.error == (None if crash_after_output else "interrupted")
        assert not recovery_model.calls
        assert mind.inspect().revision == (1 if crash_after_output else 0)
        assert mind.accept_result(result).status == ("duplicate" if crash_after_output else "failed")


def test_nervous_retries_mind_commit_without_repeating_model_call(tmp_path, monkeypatch):
    from Nervous.organ import NervousOrgan
    from Mind.host import activation_event, run_mind_once
    model = ScriptedModel([_response([_belief()])])
    with NervousOrgan(tmp_path / "nervous") as nervous, _organ(tmp_path / "mind", model) as mind:
        event = activation_event(_event())
        nervous.publish(event)
        def fail_commit(_):
            raise OSError("Nervous failed after Mind committed")
        with monkeypatch.context() as patch:
            patch.setattr(nervous, "_save", fail_commit)
            with pytest.raises(OSError):
                run_mind_once(nervous, mind)
        assert mind.inspect().revision == 1
        assert nervous.pending("mind") == (event,) and nervous.pending("host") == ()
        assert run_mind_once(nervous, mind).status == "duplicate"
        assert len(model.calls) == 1
        output, = nervous.pending("host")
        assert output.data["status"] == "accepted" and output.data["revision"] == 1
        assert run_mind_once(nervous, mind) is None


def test_nervous_pending_result_resumes_mind_in_a_new_process_ahead_of_new_inputs(tmp_path):
    from Nervous.organ import NervousOrgan
    from Mind.host import activation_event, run_mind_once
    with NervousOrgan(tmp_path / "nervous") as nervous, _organ(tmp_path / "mind", ScriptedModel([_read_step()])) as mind:
        nervous.publish(activation_event(_event()))
        waiting = run_mind_once(nervous, mind)
        assert waiting.status == "waiting" and len(nervous.pending("mind.requests")) == 1
        for index in range(40):
            nervous.publish(activation_event(_event(f"queued-{index}")))
        assert run_mind_once(nervous, mind).status == "busy"
    code = '''
import os, sys
from pathlib import Path
from Nervous.organ import NervousOrgan
from Mind.organ import MindOrgan
from Mind.host import result_event, run_mind_once
from Mind.test_experiment_a import ScriptedModel
root=Path(sys.argv[1])
n=NervousOrgan(root/'nervous')
request,=n.pending('mind.requests')
# Synthetic data only: no Memory or Execution owner is constructed/called.
observation={'capability':'recall_memory','rendered_evidence':'Credentials expired.',
             'status':'available','truncated':False,'safe_error_code':None}
n.complete(request.event_id,request.target,emitted=(result_event(request,observation),))
m=MindOrgan(directory=root/'mind',model=ScriptedModel(['{"type":"cognitive_step","updates":[],"next":{"type":"no_change"}}']))
assert run_mind_once(n,m).status=='accepted'
assert m.inspect().revision==1
assert n.pending('mind.results')==()
assert len(n.pending('mind'))==32
os._exit(0)
'''
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1]))
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=environment,
                   capture_output=True, text=True, timeout=20, check=True)
    with NervousOrgan(tmp_path / "nervous") as nervous, _organ(tmp_path / "mind", ScriptedModel([])) as mind:
        assert mind.inspect().revision == 1 and mind.activate(_event()).status == "duplicate"
        assert not nervous.pending("mind.requests") and not nervous.pending("mind.results")
        receipt, = nervous.pending("host")
        assert receipt.data["revision"] == 1
        assert nervous.pending("mind", 1)[0].event_id == "queued-0"
