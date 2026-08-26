import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields

import pytest
import execution as execution_module

from execution import (
    ClaimComplete,
    FileContentEquals,
    EventLog,
    RootAgentProcess,
    ScriptedModel,
    SharedEnvironment,
    ToolHost,
    ToolCall,
    Wait,
    WriteRequest,
    ShellRequest,
    Checkpoint,
    fold_execution_state,
)


class _PauseAfterFirstToolResult(EventLog):
    def __init__(self, path):
        super().__init__(path)
        self.tool_settled = threading.Event()
        self.interrupt_recorded = threading.Event()
        self.release_runtime = threading.Event()
        self._paused = False

    def append(self, event_type, payload, source_event_refs=()):
        event = super().append(event_type, payload, source_event_refs)
        if event_type == "INTERRUPT_REQUESTED":
            self.interrupt_recorded.set()
        if event_type == "TOOL_RESULT" and not self._paused:
            self._paused = True
            self.tool_settled.set()
            if not self.release_runtime.wait(timeout=2):
                raise TimeoutError("test did not release the settled action")
        return event


class _InterruptRecordingEventLog(EventLog):
    def __init__(self, path):
        super().__init__(path)
        self.interrupt_recorded = threading.Event()

    def append(self, event_type, payload, source_event_refs=()):
        event = super().append(event_type, payload, source_event_refs)
        if event_type == "INTERRUPT_REQUESTED":
            self.interrupt_recorded.set()
        return event


class _BlockingFirstToolHost(ToolHost):
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
                raise TimeoutError("test did not release the in-flight Tool")
        return super().execute(request)


class _PauseBeforeFirstModelDecision(EventLog):
    def __init__(self, path):
        super().__init__(path)
        self.decision_admission = threading.Event()
        self.interrupt_recorded = threading.Event()
        self.release_decision = threading.Event()
        self._paused = False

    def append(self, event_type, payload, source_event_refs=()):
        paused_decision = event_type == 'MODEL_DECISION' and not self._paused
        if paused_decision:
            self._paused = True
            self.decision_admission.set()
            if not self.release_decision.wait(timeout=2):
                raise TimeoutError('test did not release decision')
        event = super().append(event_type, payload, source_event_refs)
        if event_type == 'INTERRUPT_REQUESTED':
            self.interrupt_recorded.set()
        return event


class _PauseBeforeNextModelAdmission(EventLog):
    def __init__(self, path):
        super().__init__(path)
        self.admission_window = threading.Event()
        self.release_runtime = threading.Event()
        self._tool_settled = False
        self._paused = False

    @property
    def events(self):
        events = super().events
        if self._tool_settled and not self._paused:
            self._paused = True
            self.admission_window.set()
            if not self.release_runtime.wait(timeout=2):
                raise TimeoutError('test did not release model admission')
        return events

    def append(self, event_type, payload, source_event_refs=()):
        event = super().append(event_type, payload, source_event_refs)
        if event_type == 'TOOL_RESULT':
            self._tool_settled = True
        return event


def test_interrupt_between_decisions_stops_sampling_until_explicit_resume(tmp_path):
    event_log = _PauseAfterFirstToolResult(tmp_path / 'events.jsonl')
    model = ScriptedModel(
        [
            ToolCall(WriteRequest("result.txt", "A")),
            ToolCall(WriteRequest("result.txt", "B")),
            ClaimComplete(),
        ]
    )
    runtime = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=event_log,
        checkpoint_path=tmp_path / "checkpoint.json",
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future = pool.submit(
            runtime.run,
            "Write B",
            FileContentEquals("result.txt", "B"),
        )
        assert event_log.tool_settled.wait(timeout=2)
        interrupt_future = pool.submit(runtime.interrupt)
        assert event_log.interrupt_recorded.wait(timeout=2)
        event_log.release_runtime.set()
        interrupted = interrupt_future.result(timeout=2)
        run_result = run_future.result(timeout=2)

    assert interrupted.status == run_result.status == "suspended"
    assert len(model.received_requests) == 1
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "A"
    assert [event.event_type for event in run_result.events][-2:] == [
        "INTERRUPT_REQUESTED",
        "ACTOR_SUSPENDED",
    ]
    assert run_result.events[-1].source_event_refs == (
        run_result.events[-2].event_id,
    )

    completed = runtime.resume()

    assert completed.status == "completed"
    assert len(model.received_requests) == 3
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "B"
    resumed = next(
        event for event in completed.events if event.event_type == "ACTOR_RESUMED"
    )
    assert resumed.source_event_refs == (run_result.events[-1].event_id,)
    assert json.loads(model.received_requests[1].context)["lifecycle"] == "resumed"


def test_interrupt_in_pre_model_window_does_not_double_suspend(tmp_path):
    event_log = _PauseBeforeNextModelAdmission(tmp_path / 'events.jsonl')
    model = ScriptedModel(
        [
            ToolCall(WriteRequest('result.txt', 'A')),
            ToolCall(WriteRequest('result.txt', 'B')),
            ClaimComplete(),
        ]
    )
    runtime = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=event_log,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future = pool.submit(
            runtime.run,
            'Write B',
            FileContentEquals('result.txt', 'B'),
        )
        assert event_log.admission_window.wait(timeout=2)
        interrupted = pool.submit(runtime.interrupt).result(timeout=2)
        event_log.release_runtime.set()
        run_result = run_future.result(timeout=2)

    assert interrupted.status == run_result.status == 'suspended'
    assert len(model.received_requests) == 1
    assert (tmp_path / 'result.txt').read_text(encoding='utf-8') == 'A'
    assert [event.event_type for event in run_result.events].count(
        'ACTOR_SUSPENDED'
    ) == 1

    completed = runtime.resume()

    assert completed.status == 'completed'
    assert len(model.received_requests) == 3
    assert (tmp_path / 'result.txt').read_text(encoding='utf-8') == 'B'


def test_interrupt_is_atomic_with_model_decision_admission(tmp_path):
    event_log = _PauseBeforeFirstModelDecision(tmp_path / 'events.jsonl')
    host = _BlockingFirstToolHost(SharedEnvironment(tmp_path))
    host.release.set()
    model = ScriptedModel(
        [ToolCall(WriteRequest('result.txt', 'done')), ClaimComplete()]
    )
    runtime = RootAgentProcess(
        model=model,
        tools=host,
        max_decisions=2,
        event_log=event_log,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future = pool.submit(
            runtime.run,
            'Write done',
            FileContentEquals('result.txt', 'done'),
        )
        assert event_log.decision_admission.wait(timeout=2)
        interrupt_future = pool.submit(runtime.interrupt)
        interrupt_won_admission = event_log.interrupt_recorded.wait(timeout=0.1)
        event_log.release_decision.set()
        interrupted = interrupt_future.result(timeout=2)
        run_result = run_future.result(timeout=2)

    assert interrupted.status == run_result.status == 'suspended'
    assert len(model.received_requests) == 1
    assert len(host.requests) <= 1
    if interrupt_won_admission:
        assert host.requests == []
    assert run_result.state == fold_execution_state(run_result.events)

    completed = runtime.resume()

    assert completed.status == 'completed'
    assert host.requests == [WriteRequest('result.txt', 'done')]
    assert (tmp_path / 'result.txt').read_text(encoding='utf-8') == 'done'


def test_suspended_root_survives_restart_and_events_cannot_wake_it(tmp_path):
    log_path = tmp_path / "events.jsonl"
    checkpoint_path = tmp_path / "checkpoint.json"
    event_log = _PauseAfterFirstToolResult(log_path)
    first_model = ScriptedModel(
        [ToolCall(WriteRequest("result.txt", "A"))]
    )
    runtime = RootAgentProcess(
        model=first_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=event_log,
        checkpoint_path=checkpoint_path,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future = pool.submit(
            runtime.run,
            "Write B",
            FileContentEquals("result.txt", "B"),
        )
        assert event_log.tool_settled.wait(timeout=2)
        interrupt_future = pool.submit(runtime.interrupt)
        assert event_log.interrupt_recorded.wait(timeout=2)
        event_log.release_runtime.set()
        suspended = interrupt_future.result(timeout=2)
        assert run_future.result(timeout=2).status == "suspended"

    execution_id = suspended.state.execution_id
    root_actor_id = suspended.state.root_actor_id
    del runtime

    restored_model = ScriptedModel(
        [
            ToolCall(WriteRequest("result.txt", "B")),
            ClaimComplete(),
        ]
    )
    restored_log = EventLog.load(log_path)
    restored = RootAgentProcess(
        model=restored_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=restored_log,
        checkpoint_path=checkpoint_path,
    )

    reloaded_state = fold_execution_state(restored_log.events)
    assert reloaded_state.status == "suspended"
    assert reloaded_state.execution_id == execution_id
    assert reloaded_state.root_actor_id == root_actor_id
    assert restored_model.received_requests == []

    event_result = restored.deliver_event("continue", "ignored until resume")

    assert event_result.status == "suspended"
    assert event_result.events[-1].event_type == "EXTERNAL_EVENT_RECEIVED"
    assert restored_model.received_requests == []
    assert fold_execution_state(EventLog.load(log_path).events).status == "suspended"

    completed = restored.resume()

    assert completed.status == "completed"
    assert len(restored_model.received_requests) == 2
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "B"


def test_restart_finishes_durable_interrupt_request_before_explicit_resume(tmp_path):
    log_path = tmp_path / 'events.jsonl'
    write_calls = []

    class CrashAfterCommittedWrite:
        def __init__(self):
            self._host = ToolHost(SharedEnvironment(tmp_path))

        def execute(self, request):
            write_calls.append(request)
            result = self._host.execute(request)
            assert result.ok
            raise SystemExit('crash after committed write')

    with pytest.raises(SystemExit, match='crash after committed write'):
        RootAgentProcess(
            model=ScriptedModel(
                [ToolCall(WriteRequest('result.txt', 'committed'))]
            ),
            tools=CrashAfterCommittedWrite(),
            max_decisions=2,
            event_log=EventLog(log_path),
        ).run(
            'Keep the durable interrupt',
            FileContentEquals('result.txt', 'committed'),
        )

    interrupted_log = EventLog.load(log_path)
    started = interrupted_log.events[-1]
    interrupted_log.append(
        'INTERRUPT_REQUESTED',
        {'status': 'requested'},
        (started.event_id,),
    )
    restored_model = ScriptedModel([ClaimComplete()])
    restored = RootAgentProcess(
        model=restored_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog.load(log_path),
    )

    settled_interrupt = restored.resume()

    assert settled_interrupt.status == 'suspended'
    assert restored_model.received_requests == []
    assert write_calls == [WriteRequest('result.txt', 'committed')]
    assert [event.event_type for event in settled_interrupt.events][-3:] == [
        'INTERRUPT_REQUESTED',
        'ACTION_RECONCILED',
        'ACTOR_SUSPENDED',
    ]
    assert settled_interrupt.state == fold_execution_state(
        settled_interrupt.events
    )

    completed = restored.resume()

    assert completed.status == 'completed'
    assert len(restored_model.received_requests) == 1
    assert write_calls == [WriteRequest('result.txt', 'committed')]


def test_interrupt_waits_for_in_flight_tool_then_restart_does_not_replay_it(tmp_path):
    log_path = tmp_path / "events.jsonl"
    event_log = _InterruptRecordingEventLog(log_path)
    host = _BlockingFirstToolHost(SharedEnvironment(tmp_path))
    first_model = ScriptedModel(
        [ToolCall(WriteRequest("result.txt", "A"))]
    )
    runtime = RootAgentProcess(
        model=first_model,
        tools=host,
        max_decisions=3,
        event_log=event_log,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future = pool.submit(
            runtime.run,
            "Write B",
            FileContentEquals("result.txt", "B"),
        )
        assert host.started.wait(timeout=2)
        interrupt_future = pool.submit(runtime.interrupt)
        assert event_log.interrupt_recorded.wait(timeout=2)
        assert len(first_model.received_requests) == 1
        host.release.set()
        suspended = interrupt_future.result(timeout=2)
        assert run_future.result(timeout=2).status == "suspended"

    assert suspended.status == "suspended"
    assert [event.event_type for event in suspended.events][-4:] == [
        "TOOL_CALL_STARTED",
        "INTERRUPT_REQUESTED",
        "TOOL_RESULT",
        "ACTOR_SUSPENDED",
    ]
    assert suspended.events[-2].source_event_refs == (
        suspended.events[-3].event_id,
    )
    assert suspended.events[-1].source_event_refs == (
        suspended.events[-2].event_id,
    )
    del runtime

    restored_model = ScriptedModel(
        [
            ToolCall(WriteRequest("result.txt", "B")),
            ClaimComplete(),
        ]
    )
    restored = RootAgentProcess(
        model=restored_model,
        tools=host,
        max_decisions=3,
        event_log=EventLog.load(log_path),
    )

    completed = restored.resume()

    assert completed.status == "completed"
    assert host.requests == [
        WriteRequest("result.txt", "A"),
        WriteRequest("result.txt", "B"),
    ]


def test_false_completion_is_rejected_before_root_fixes_environment(tmp_path):
    (tmp_path / "result.txt").write_text("wrong", encoding="utf-8")
    model = ScriptedModel(
        [
            ClaimComplete(),
            ToolCall(WriteRequest("result.txt", "correct")),
            ClaimComplete(),
        ]
    )

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        max_context_chars=900,
    ).run(
        "Make result.txt correct",
        FileContentEquals("result.txt", "correct"),
    )

    event_types = [event.event_type for event in result.events]
    rejected = next(
        event for event in result.events if event.event_type == "COMPLETION_REJECTED"
    )
    first_claim = result.events[rejected.sequence - 2]
    verified = next(
        event for event in result.events if event.event_type == "COMPLETION_VERIFIED"
    )
    second_claim = result.events[verified.sequence - 2]
    completed = result.events[-1]
    rejection_context = json.loads(model.received_requests[1].context)

    assert result.status == "completed"
    assert result.output == "verified"
    assert event_types.count("COMPLETION_CLAIMED") == 2
    assert first_claim.event_type == second_claim.event_type == "COMPLETION_CLAIMED"
    assert rejected.source_event_refs == (first_claim.event_id,)
    assert verified.source_event_refs == (second_claim.event_id,)
    assert completed.event_type == "EXECUTION_COMPLETED"
    assert completed.source_event_refs == (verified.event_id,)
    assert rejection_context["observation"]["type"] == "completion_verification"
    assert rejection_context["observation"]["status"] == "rejected"
    assert rejection_context["observation"]["evidence"]["matched"] is False
    assert (
        rejection_context["observation"]["evidence"]["reason"]
        == "content_mismatch"
    )
    assert model.received_requests[1].context_size_chars <= 900
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "correct"
    assert result.state == fold_execution_state(result.events)


def test_true_completion_is_verified_without_an_extra_model_call(tmp_path):
    (tmp_path / "result.txt").write_text("correct", encoding="utf-8")
    model = ScriptedModel([ClaimComplete()])

    result = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    ).run(
        "Accept an already-correct result",
        FileContentEquals("result.txt", "correct"),
    )

    assert result.status == "completed"
    assert len(model.received_requests) == 1
    assert [event.event_type for event in result.events][-3:] == [
        "COMPLETION_CLAIMED",
        "COMPLETION_VERIFIED",
        "EXECUTION_COMPLETED",
    ]
    evidence = result.events[-2].payload["evidence"]
    assert evidence.spec_type == "file_content_equals"
    assert evidence.observed_path == "result.txt"
    assert evidence.matched is True
    assert evidence.reason == "matched"


def test_claim_complete_cannot_override_the_caller_owned_spec(tmp_path):
    spec = FileContentEquals("result.txt", "caller-owned")
    (tmp_path / "result.txt").write_text("caller-owned", encoding="utf-8")

    assert fields(ClaimComplete) == ()
    with pytest.raises(TypeError):
        ClaimComplete(expected_content="model-owned")
    with pytest.raises(TypeError):
        ClaimComplete(success=True)
    with pytest.raises(FrozenInstanceError):
        spec.expected_content = "model-owned"

    result = RootAgentProcess(
        model=ScriptedModel([ClaimComplete()]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
    ).run("Respect caller authority", spec)

    assert result.status == "completed"
    assert result.events[0].payload["completion_spec"] == spec
    assert result.decision_frames[0].resulting_action == ClaimComplete()


@pytest.mark.parametrize(
    ("reality", "expected_reason"),
    [
        ("missing", "missing"),
        ("mismatch", "content_mismatch"),
        ("unreadable", "unreadable"),
    ],
)
def test_unproven_completion_reality_is_rejected(
    tmp_path, monkeypatch, reality, expected_reason
):
    target = tmp_path / "result.txt"
    if reality == "mismatch":
        target.write_text("wrong", encoding="utf-8")
    elif reality == "unreadable":
        target.mkdir()
    log_path = tmp_path / "execution.jsonl"
    event_log = EventLog(log_path)
    durable_append = event_log.append

    def crash_after_rejection(event_type, payload, source_event_refs=()):
        event = durable_append(event_type, payload, source_event_refs)
        if event_type == "COMPLETION_REJECTED":
            raise SystemExit("stop after durable rejection")
        return event

    monkeypatch.setattr(event_log, "append", crash_after_rejection)
    model = ScriptedModel([ClaimComplete()])
    with pytest.raises(SystemExit, match="stop after durable rejection"):
        RootAgentProcess(
            model=model,
            tools=ToolHost(SharedEnvironment(tmp_path)),
            max_decisions=2,
            event_log=event_log,
        ).run(
            "Reject unproven completion",
            FileContentEquals("result.txt", "correct"),
        )

    rejected = EventLog.load(log_path).events[-1]
    assert rejected.event_type == "COMPLETION_REJECTED"
    assert rejected.payload["observation"].evidence.matched is False
    assert rejected.payload["observation"].evidence.reason == expected_reason
    assert len(model.received_requests) == 1


def test_restart_after_completion_rejection_remains_runnable(tmp_path, monkeypatch):
    (tmp_path / "result.txt").write_text("wrong", encoding="utf-8")
    log_path = tmp_path / "execution.jsonl"
    event_log = EventLog(log_path)
    durable_append = event_log.append

    def crash_after_rejection(event_type, payload, source_event_refs=()):
        event = durable_append(event_type, payload, source_event_refs)
        if event_type == "COMPLETION_REJECTED":
            raise SystemExit("crash after rejection")
        return event

    monkeypatch.setattr(event_log, "append", crash_after_rejection)
    with pytest.raises(SystemExit, match="crash after rejection"):
        RootAgentProcess(
            model=ScriptedModel([ClaimComplete()]),
            tools=ToolHost(SharedEnvironment(tmp_path)),
            max_decisions=3,
            event_log=event_log,
        ).run(
            "Recover after rejection",
            FileContentEquals("result.txt", "correct"),
        )

    rejected_log = EventLog.load(log_path)
    rejected_state = fold_execution_state(rejected_log.events)
    execution_id = rejected_state.execution_id
    root_actor_id = rejected_state.root_actor_id
    assert rejected_state.status == "running"
    assert rejected_state.latest_observation.status == "rejected"

    resumed_model = ScriptedModel(
        [ToolCall(WriteRequest("result.txt", "correct")), ClaimComplete()]
    )
    resumed = RootAgentProcess(
        model=resumed_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=rejected_log,
    ).resume()

    first_context = json.loads(resumed_model.received_requests[0].context)
    assert resumed.status == "completed"
    assert resumed.state.execution_id == execution_id
    assert resumed.state.root_actor_id == root_actor_id
    assert first_context["observation"]["status"] == "rejected"
    assert resumed.state == fold_execution_state(resumed.events)


def test_restart_after_verified_completion_does_no_work(tmp_path):
    target = tmp_path / "result.txt"
    target.write_text("correct", encoding="utf-8")
    log_path = tmp_path / "execution.jsonl"
    completed = RootAgentProcess(
        model=ScriptedModel([ClaimComplete()]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(log_path),
    ).run(
        "Finish once",
        FileContentEquals("result.txt", "correct"),
    )
    target.write_text("changed after completion", encoding="utf-8")
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

    resumed_model = ScriptedModel([])
    resumed = RootAgentProcess(
        model=resumed_model,
        tools=RecordingTools(),
        max_decisions=1,
        event_log=EventLog.load(log_path),
    ).resume()

    assert completed.status == resumed.status == "completed"
    assert resumed.events == completed.events
    assert resumed_model.received_requests == []
    assert calls == []


def test_completion_events_reload_with_exact_state_and_causal_refs(tmp_path):
    (tmp_path / "result.txt").write_text("correct", encoding="utf-8")
    log_path = tmp_path / "execution.jsonl"
    completed = RootAgentProcess(
        model=ScriptedModel([ClaimComplete()]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(log_path),
    ).run(
        "Replay completion",
        FileContentEquals("result.txt", "correct"),
    )

    reloaded = EventLog.load(log_path)
    restored = RootAgentProcess(
        model=ScriptedModel([]),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=reloaded,
    ).resume()
    decision, claimed, verified, terminal = reloaded.events[-4:]

    assert reloaded.events == completed.events
    assert restored.state == fold_execution_state(reloaded.events)
    assert restored.state == completed.state
    assert claimed.event_type == "COMPLETION_CLAIMED"
    assert claimed.source_event_refs == (decision.event_id,)
    assert verified.event_type == "COMPLETION_VERIFIED"
    assert verified.source_event_refs == (claimed.event_id,)
    assert terminal.event_type == "EXECUTION_COMPLETED"
    assert terminal.source_event_refs == (verified.event_id,)


def test_jsonl_event_log_reloads_exact_events_and_state(tmp_path):
    path = tmp_path / "execution.jsonl"
    log = EventLog(path)
    log.append(
        "EXECUTION_STARTED",
        {
            "goal": "persist me",
            "execution_id": "execution-1",
            "root_actor_id": "root-1",
            "completion_spec": FileContentEquals("unused.txt", "unused"),
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

    waiting = runtime.run(
        "wait durably", FileContentEquals("unused.txt", "unused")
    )
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
    ).run("restore me", FileContentEquals("unused.txt", "unused"))

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
    ).run("use the checkpoint", FileContentEquals("unused.txt", "unused"))
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
            "completion_spec": FileContentEquals("unused.txt", "unused"),
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

        @property
        def environment(self):
            return self._host.environment

    before_restart = RootAgentProcess(
        model=ScriptedModel(
            [ToolCall(WriteRequest("a.txt", "A")), Wait("CONTINUE")]
        ),
        tools=RecordingTools(),
        max_decisions=5,
        event_log=EventLog(log_path),
        checkpoint_path=checkpoint_path,
    ).run("survive restart", FileContentEquals("b.txt", "B"))
    execution_id = before_restart.state.execution_id
    root_actor_id = before_restart.state.root_actor_id

    after_restart_model = ScriptedModel(
        [ToolCall(WriteRequest("b.txt", "B")), ClaimComplete()]
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
    (tmp_path / "awake.txt").write_text("awake", encoding="utf-8")
    runtime.run("ignore noise", FileContentEquals("awake.txt", "awake"))

    still_waiting = runtime.deliver_event("NOISE", "keep this")

    assert still_waiting.status == "waiting"
    assert len(first_model.received_requests) == 1
    assert still_waiting.events[-1].event_type == "EXTERNAL_EVENT_RECEIVED"
    assert still_waiting.events[-1].payload["event"].event_type == "NOISE"

    second_model = ScriptedModel([ClaimComplete()])
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
    ).run("detect corruption", FileContentEquals("unused.txt", "unused"))
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
                "completion_spec": FileContentEquals("unused.txt", "unused"),
            },
        )

    assert log.events == ()


def test_restart_reconciles_a_committed_write_without_executing_it_again(tmp_path):
    log_path = tmp_path / "execution.jsonl"
    write_calls = []

    class CrashAfterCommittedWrite:
        def __init__(self):
            self._host = ToolHost(SharedEnvironment(tmp_path))

        def execute(self, request):
            write_calls.append(request)
            result = self._host.execute(request)
            assert result.ok
            raise SystemExit("crash after committed write")

    class RecordingRecoveryTools:
        def __init__(self):
            self._host = ToolHost(SharedEnvironment(tmp_path))

        def inspect_write(self, request):
            return self._host.inspect_write(request)

        def execute(self, request):
            write_calls.append(request)
            return self._host.execute(request)

        @property
        def environment(self):
            return self._host.environment

    with pytest.raises(SystemExit, match="crash after committed write"):
        RootAgentProcess(
            model=ScriptedModel([ToolCall(WriteRequest("committed.txt", "value"))]),
            tools=CrashAfterCommittedWrite(),
            max_decisions=3,
            event_log=EventLog(log_path),
        ).run(
            "reconcile a committed write",
            FileContentEquals("committed.txt", "value"),
        )

    before_restart = EventLog.load(log_path)
    started = before_restart.events[-1]
    execution_id = fold_execution_state(before_restart.events).execution_id
    root_actor_id = fold_execution_state(before_restart.events).root_actor_id
    assert started.event_type == "TOOL_CALL_STARTED"
    assert started.payload["request"] == WriteRequest("committed.txt", "value")

    resumed = RootAgentProcess(
        model=ScriptedModel([ClaimComplete()]),
        tools=RecordingRecoveryTools(),
        max_decisions=3,
        event_log=EventLog.load(log_path),
    ).resume()

    reconciled = next(
        event for event in resumed.events if event.event_type == "ACTION_RECONCILED"
    )
    assert resumed.status == "completed"
    assert resumed.state.execution_id == execution_id
    assert resumed.state.root_actor_id == root_actor_id
    assert resumed.state == fold_execution_state(resumed.events)
    assert EventLog.load(log_path).events == resumed.events
    assert write_calls == [WriteRequest("committed.txt", "value")]
    assert (tmp_path / "committed.txt").read_text(encoding="utf-8") == "value"
    assert reconciled.source_event_refs == (started.event_id,)
    assert reconciled.payload["status"] == "confirmed_applied"
    assert reconciled.payload["evidence"]["path"] == "committed.txt"
    assert reconciled.payload["evidence"]["intended_content_sha256"] == (
        reconciled.payload["evidence"]["observed_content_sha256"]
    )


def test_restart_leaves_an_ambiguous_write_unknown_without_replay(tmp_path):
    log_path = tmp_path / "execution.jsonl"

    class CrashBeforeWrite:
        def execute(self, request):
            raise SystemExit("crash before write")

    with pytest.raises(SystemExit, match="crash before write"):
        RootAgentProcess(
            model=ScriptedModel([ToolCall(WriteRequest("unknown.txt", "value"))]),
            tools=CrashBeforeWrite(),
            max_decisions=2,
            event_log=EventLog(log_path),
        ).run(
            "leave a write unknown",
            FileContentEquals("unknown.txt", "value"),
        )
    (tmp_path / "unknown.txt").write_text("different", encoding="utf-8")

    resumed_model = ScriptedModel([ClaimComplete()])
    runtime = RootAgentProcess(
        model=resumed_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog.load(log_path),
    )

    with pytest.raises(ValueError, match="write outcome is unresolved"):
        runtime.resume()

    reloaded = EventLog.load(log_path)
    assert reloaded.events[-1].event_type == "TOOL_CALL_STARTED"
    assert (tmp_path / "unknown.txt").read_text(encoding="utf-8") == "different"
    assert resumed_model.received_requests == []


def test_restart_reports_dangling_shell_as_unsupported(tmp_path):
    log_path = tmp_path / "execution.jsonl"

    class CrashingTools:
        def execute(self, request):
            raise SystemExit("crash around shell")

    with pytest.raises(SystemExit, match="crash around shell"):
        RootAgentProcess(
            model=ScriptedModel(
                [ToolCall(ShellRequest(("python", "-c", "print('side effect')")))]
            ),
            tools=CrashingTools(),
            max_decisions=2,
            event_log=EventLog(log_path),
        ).run(
            "leave shell unresolved",
            FileContentEquals("unused.txt", "unused"),
        )

    resumed_model = ScriptedModel([ClaimComplete()])
    runtime = RootAgentProcess(
        model=resumed_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        event_log=EventLog.load(log_path),
    )

    with pytest.raises(ValueError, match="ShellRequest recovery is unsupported"):
        runtime.resume()

    assert EventLog.load(log_path).events[-1].event_type == "TOOL_CALL_STARTED"
    assert resumed_model.received_requests == []


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
    (tmp_path / "resumed.txt").write_text("resumed", encoding="utf-8")
    runtime.run(
        "finish durable wake", FileContentEquals("resumed.txt", "resumed")
    )
    durable_append = event_log.append

    def crash_before_woken(event_type, payload, source_event_refs=()):
        if event_type == "ROOT_WOKEN":
            raise SystemExit("crash after incoming event")
        return durable_append(event_type, payload, source_event_refs)

    monkeypatch.setattr(event_log, "append", crash_before_woken)
    with pytest.raises(SystemExit, match="crash after incoming event"):
        runtime.deliver_event("CONTINUE")
    assert EventLog.load(log_path).events[-1].event_type == "EXTERNAL_EVENT_RECEIVED"

    resumed_model = ScriptedModel([ClaimComplete()])
    resumed = RootAgentProcess(
        model=resumed_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog.load(log_path),
        checkpoint_path=checkpoint_path,
    ).resume()

    assert resumed.status == "completed"
    assert len(resumed_model.received_requests) == 1
    assert any(event.event_type == "ROOT_WOKEN" for event in resumed.events)


def test_wake_at_the_decision_bound_fails_without_an_extra_model_call(tmp_path):
    model = ScriptedModel([Wait("CONTINUE")])
    runtime = RootAgentProcess(
        model=model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=1,
        event_log=EventLog(tmp_path / "execution.jsonl"),
    )
    runtime.run("bounded wait", FileContentEquals("unused.txt", "unused"))

    result = runtime.deliver_event("CONTINUE")

    assert result.status == "failed"
    assert result.failure == "decision_limit_reached"
    assert len(model.received_requests) == 1


def test_long_typed_wait_condition_cannot_break_the_context_bound(tmp_path):
    event_type = "C" * 20_000
    (tmp_path / "bounded.txt").write_text("bounded", encoding="utf-8")
    resumed_model = ScriptedModel([Wait(event_type), ClaimComplete()])
    runtime = RootAgentProcess(
        model=resumed_model,
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=2,
        max_context_chars=768,
        event_log=EventLog(tmp_path / "execution.jsonl"),
    )
    runtime.run(
        "bounded wait condition",
        FileContentEquals("bounded.txt", "bounded"),
    )

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
    ).run(
        "reject invalid wait", FileContentEquals("unused.txt", "unused")
    )

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
    ).run(
        "preserve raw response", FileContentEquals("unused.txt", "unused")
    )

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
            "completion_spec": FileContentEquals("unused.txt", "unused"),
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
    ).run("typed checkpoint", FileContentEquals("unused.txt", "unused"))
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
