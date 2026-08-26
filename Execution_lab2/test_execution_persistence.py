import json

import pytest
import execution as execution_module

from execution import (
    EventLog,
    RootAgentProcess,
    ScriptedModel,
    SharedEnvironment,
    ToolHost,
    ToolCall,
    Wait,
    WriteRequest,
    Complete,
    Checkpoint,
    fold_execution_state,
)


def test_jsonl_event_log_reloads_exact_events_and_state(tmp_path):
    path = tmp_path / "execution.jsonl"
    log = EventLog(path)
    log.append(
        "EXECUTION_STARTED",
        {
            "goal": "persist me",
            "execution_id": "execution-1",
            "root_actor_id": "root-1",
        },
    )
    reloaded = EventLog.load(path)

    assert reloaded.events == log.events
    assert fold_execution_state(reloaded.events) == fold_execution_state(log.events)
    assert reloaded.events[-1].sequence == 1


def test_wait_persists_and_resume_does_not_sample_without_an_event(tmp_path):
    model = ScriptedModel([Wait("CONTINUE")])
    runtime = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog(tmp_path / "execution.jsonl"),
        checkpoint_path=tmp_path / "checkpoint.json",
    )

    waiting = runtime.run("wait durably")
    still_waiting = runtime.resume()

    assert waiting.status == still_waiting.status == "waiting"
    assert waiting.state.waiting_for == "CONTINUE"
    assert len(model.received_requests) == 1
    assert [event.event_type for event in waiting.events][-1] == "ROOT_WAITING"


def test_checkpoint_restore_equals_full_replay_and_missing_falls_back(tmp_path):
    log_path = tmp_path / "execution.jsonl"
    checkpoint_path = tmp_path / "checkpoint.json"
    original = RootAgentProcess(
        model=ScriptedModel([Wait("CONTINUE")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog(log_path),
        checkpoint_path=checkpoint_path,
    ).run("restore me")

    reloaded_log = EventLog.load(log_path)
    restored = RootAgentProcess(
        model=ScriptedModel([]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=reloaded_log,
        checkpoint_path=checkpoint_path,
    ).resume()

    assert checkpoint_path.is_file()
    assert restored.state == fold_execution_state(reloaded_log.events)
    assert restored.state == original.state
    assert restored.state.execution_id == original.state.execution_id
    assert restored.state.root_actor_id == original.state.root_actor_id

    checkpoint_path.unlink()
    without_checkpoint = RootAgentProcess(
        model=ScriptedModel([]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog.load(log_path),
        checkpoint_path=checkpoint_path,
    ).resume()
    assert without_checkpoint.state == original.state


def test_valid_checkpoint_recovery_folds_only_the_durable_tail(
    tmp_path, monkeypatch
):
    log_path = tmp_path / "execution.jsonl"
    checkpoint_path = tmp_path / "checkpoint.json"
    RootAgentProcess(
        model=ScriptedModel([Wait("CONTINUE")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog(log_path),
        checkpoint_path=checkpoint_path,
    ).run("use the checkpoint")
    reloaded_log = EventLog.load(log_path)
    real_fold = execution_module.fold_execution_state
    fold_calls = []

    def tail_only_fold(events, *, initial_state=None, prior_event_ids=()):
        materialized_events = tuple(events)
        fold_calls.append((len(materialized_events), initial_state is not None))
        if initial_state is None:
            raise AssertionError("valid checkpoint recovery performed full replay")
        return real_fold(
            materialized_events,
            initial_state=initial_state,
            prior_event_ids=prior_event_ids,
        )

    monkeypatch.setattr(execution_module, "fold_execution_state", tail_only_fold)
    restored = RootAgentProcess(
        model=ScriptedModel([]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=reloaded_log,
        checkpoint_path=checkpoint_path,
    ).resume()

    assert restored.status == "waiting"
    assert fold_calls
    assert all(used_checkpoint for _, used_checkpoint in fold_calls)


def test_checkpoint_capture_derives_its_snapshot_from_events(tmp_path):
    log = EventLog(tmp_path / "execution.jsonl")
    log.append(
        "EXECUTION_STARTED",
        {
            "goal": "derive checkpoint",
            "execution_id": "execution-1",
            "root_actor_id": "root-1",
        },
    )

    checkpoint = Checkpoint.capture(log.events)

    assert checkpoint.state == fold_execution_state(log.events)
    assert checkpoint.last_applied_event_sequence == log.events[-1].sequence


def test_full_restart_wake_resumes_after_settled_tool_without_replaying_it(tmp_path):
    log_path = tmp_path / "execution.jsonl"
    checkpoint_path = tmp_path / "checkpoint.json"
    calls = []

    class RecordingTools:
        def __init__(self):
            self._host = ToolHost(SharedEnvironment(tmp_path))

        def execute(self, request):
            calls.append(request)
            return self._host.execute(request)

    before_restart = RootAgentProcess(
        model=ScriptedModel(
            [ToolCall(WriteRequest("a.txt", "A")), Wait("CONTINUE")]
        ),
        tools=RecordingTools(),
        max_decisions=5,
        event_log=EventLog(log_path),
        checkpoint_path=checkpoint_path,
    ).run("survive restart")
    execution_id = before_restart.state.execution_id
    root_actor_id = before_restart.state.root_actor_id

    after_restart_model = ScriptedModel(
        [ToolCall(WriteRequest("b.txt", "B")), Complete("done")]
    )
    after_restart = RootAgentProcess(
        model=after_restart_model,
        tools=RecordingTools(),
        max_decisions=5,
        event_log=EventLog.load(log_path),
        checkpoint_path=checkpoint_path,
    ).deliver_event("CONTINUE")

    assert after_restart.status == "completed"
    assert after_restart.state.execution_id == execution_id
    assert after_restart.state.root_actor_id == root_actor_id
    assert calls.count(WriteRequest("a.txt", "A")) == 1
    assert calls.count(WriteRequest("b.txt", "B")) == 1
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "A"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "B"
    assert "CONTINUE" in after_restart_model.received_requests[0].context


def test_irrelevant_event_is_durable_but_does_not_wake_or_sample(tmp_path):
    log_path = tmp_path / "execution.jsonl"
    checkpoint_path = tmp_path / "checkpoint.json"
    first_model = ScriptedModel([Wait("CONTINUE")])
    runtime = RootAgentProcess(
        model=first_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog(log_path),
        checkpoint_path=checkpoint_path,
    )
    runtime.run("ignore noise")

    still_waiting = runtime.deliver_event("NOISE", "keep this")

    assert still_waiting.status == "waiting"
    assert len(first_model.received_requests) == 1
    assert still_waiting.events[-1].event_type == "EXTERNAL_EVENT_RECEIVED"
    assert still_waiting.events[-1].payload["event"].event_type == "NOISE"

    second_model = ScriptedModel([Complete("awake")])
    completed = RootAgentProcess(
        model=second_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog.load(log_path),
        checkpoint_path=checkpoint_path,
    ).deliver_event("CONTINUE")

    assert completed.status == "completed"
    assert len(second_model.received_requests) == 1
    assert [event.event_type for event in completed.events].count(
        "EXTERNAL_EVENT_RECEIVED"
    ) == 2
    assert [event.event_type for event in completed.events].count("ROOT_WOKEN") == 1
    assert completed.state == fold_execution_state(completed.events)


def test_malformed_event_log_and_corrupt_checkpoint_fail_explicitly(tmp_path):
    malformed_log = tmp_path / "malformed.jsonl"
    malformed_log.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed event log at line 1"):
        EventLog.load(malformed_log)

    log_path = tmp_path / "execution.jsonl"
    checkpoint_path = tmp_path / "checkpoint.json"
    RootAgentProcess(
        model=ScriptedModel([Wait("CONTINUE")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog(log_path),
        checkpoint_path=checkpoint_path,
    ).run("detect corruption")
    checkpoint_path.write_text("{not json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed checkpoint"):
        RootAgentProcess(
            model=ScriptedModel([]),
            tools=ToolHost(SharedEnvironment(tmp_path)),
            max_decisions=2,
            event_log=EventLog.load(log_path),
            checkpoint_path=checkpoint_path,
        )


def test_failed_durable_append_never_becomes_an_authoritative_event(
    tmp_path, monkeypatch
):
    log = EventLog(tmp_path / "execution.jsonl")

    def fail_persist(event):
        raise OSError("disk unavailable")

    monkeypatch.setattr(log, "_persist", fail_persist)
    with pytest.raises(OSError, match="disk unavailable"):
        log.append(
            "EXECUTION_STARTED",
            {
                "goal": "do not accept",
                "execution_id": "execution-1",
                "root_actor_id": "root-1",
            },
        )

    assert log.events == ()


def test_restart_rejects_an_unsettled_tool_call_without_unknown_reconciliation(
    tmp_path,
):
    log_path = tmp_path / "execution.jsonl"

    class CrashingTools:
        def execute(self, request):
            raise SystemExit("simulated process death")

    with pytest.raises(SystemExit, match="simulated process death"):
        RootAgentProcess(
            model=ScriptedModel([ToolCall(WriteRequest("unsafe.txt", "value"))]),
            tools=CrashingTools(),
            max_decisions=2,
            event_log=EventLog(log_path),
        ).run("leave an unsettled call")

    assert EventLog.load(log_path).events[-1].event_type == "TOOL_CALL_STARTED"
    with pytest.raises(ValueError, match="unsettled action recovery is not implemented"):
        RootAgentProcess(
            model=ScriptedModel([]),
            tools=ToolHost(SharedEnvironment(tmp_path)),
            max_decisions=2,
            event_log=EventLog.load(log_path),
        )


def test_restart_finishes_wake_when_matching_event_was_already_durable(
    tmp_path, monkeypatch
):
    log_path = tmp_path / "execution.jsonl"
    checkpoint_path = tmp_path / "checkpoint.json"
    event_log = EventLog(log_path)
    runtime = RootAgentProcess(
        model=ScriptedModel([Wait("CONTINUE")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=event_log,
        checkpoint_path=checkpoint_path,
    )
    runtime.run("finish durable wake")
    durable_append = event_log.append

    def crash_before_woken(event_type, payload, source_event_refs=()):
        if event_type == "ROOT_WOKEN":
            raise SystemExit("crash after incoming event")
        return durable_append(event_type, payload, source_event_refs)

    monkeypatch.setattr(event_log, "append", crash_before_woken)
    with pytest.raises(SystemExit, match="crash after incoming event"):
        runtime.deliver_event("CONTINUE")
    assert EventLog.load(log_path).events[-1].event_type == "EXTERNAL_EVENT_RECEIVED"

    resumed_model = ScriptedModel([Complete("resumed")])
    resumed = RootAgentProcess(
        model=resumed_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog.load(log_path),
        checkpoint_path=checkpoint_path,
    ).resume()

    assert resumed.status == "completed"
    assert len(resumed_model.received_requests) == 1
    assert resumed.events[-3].event_type == "ROOT_WOKEN"


def test_wake_at_the_decision_bound_fails_without_an_extra_model_call(tmp_path):
    model = ScriptedModel([Wait("CONTINUE")])
    runtime = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(tmp_path / "execution.jsonl"),
    )
    runtime.run("bounded wait")

    result = runtime.deliver_event("CONTINUE")

    assert result.status == "failed"
    assert result.failure == "decision_limit_reached"
    assert len(model.received_requests) == 1


def test_long_typed_wait_condition_cannot_break_the_context_bound(tmp_path):
    event_type = "C" * 20_000
    resumed_model = ScriptedModel([Wait(event_type), Complete("bounded")])
    runtime = RootAgentProcess(
        model=resumed_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        max_context_chars=768,
        event_log=EventLog(tmp_path / "execution.jsonl"),
    )
    runtime.run("bounded wait condition")

    result = runtime.deliver_event(event_type)

    assert result.status == "completed"
    assert resumed_model.received_requests[1].context_size_chars <= 768
    assert '"original_chars":20000' in resumed_model.received_requests[1].context


def test_invalid_wait_condition_settles_as_an_explicit_failure(tmp_path):
    path = tmp_path / "execution.jsonl"
    result = RootAgentProcess(
        model=ScriptedModel([Wait("")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(path),
    ).run("reject invalid wait")

    assert result.status == "failed"
    assert result.failure == "unknown_action:Wait"
    assert result.events[-1].event_type == "EXECUTION_FAILED"
    assert EventLog.load(path).events == result.events


def test_durable_codec_preserves_non_string_mapping_keys_exactly(tmp_path):
    path = tmp_path / "execution.jsonl"
    raw_response = {1: "integer key", "1": "string key", (2,): "tuple key"}
    original = RootAgentProcess(
        model=ScriptedModel([raw_response]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(path),
    ).run("preserve raw response")

    reloaded = EventLog.load(path)

    assert reloaded.events == original.events
    assert dict(reloaded.events[1].payload["frame"].raw_model_response) == raw_response


def test_boolean_schema_metadata_is_rejected_instead_of_treated_as_integer(
    tmp_path,
):
    event_path = tmp_path / "event.jsonl"
    event_log = EventLog(event_path)
    event_log.append(
        "EXECUTION_STARTED",
        {
            "goal": "typed metadata",
            "execution_id": "execution-1",
            "root_actor_id": "root-1",
        },
    )
    event_record = json.loads(event_path.read_text(encoding="utf-8"))
    event_record["schema_version"] = True
    event_path.write_text(json.dumps(event_record) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported durable event schema"):
        EventLog.load(event_path)

    log_path = tmp_path / "execution.jsonl"
    checkpoint_path = tmp_path / "checkpoint.json"
    RootAgentProcess(
        model=ScriptedModel([Wait("CONTINUE")]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog(log_path),
        checkpoint_path=checkpoint_path,
    ).run("typed checkpoint")
    checkpoint_record = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint_record["schema_version"] = True
    checkpoint_path.write_text(
        json.dumps(checkpoint_record) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="invalid checkpoint metadata"):
        RootAgentProcess(
            model=ScriptedModel([]),
            tools=ToolHost(SharedEnvironment(tmp_path)),
            max_decisions=2,
            event_log=EventLog.load(log_path),
            checkpoint_path=checkpoint_path,
        )
