from __future__ import annotations

import dataclasses
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from Execution import ExecutionOrgan, FileContentEquals, RealityEvidence
from Execution.execution import (
    ChildRef,
    ClaimComplete,
    ExecutionEvent,
    EventLog,
    IPythonCode,
    NativeModelDecision,
    Return,
    Wait,
    fold_execution_state,
)


ROOT_SURFACE = (
    "ipython(code: str)",
    "wait(event_type: str)",
    "claim_complete()",
)
CHILD_SURFACE = (
    "ipython(code: str)",
    "wait(event_type: str)",
    "return(local_result: str)",
)


class _ScriptedModel:
    def __init__(self, actions, *, tool_contracts=ROOT_SURFACE, identifier="scripted"):
        self._actions = iter(actions)
        self.tool_contracts = tool_contracts
        self.identifier = identifier
        self.received_requests = []

    def decide(self, request):
        self.received_requests.append(request)
        return next(self._actions)


def _organ(tmp_path, model, **overrides):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    options = {
        "workspace": workspace,
        "event_log_path": tmp_path / "state" / "execution.jsonl",
        "model": model,
        "max_decisions": 4,
    }
    options.update(overrides)
    return ExecutionOrgan(**options)


@pytest.mark.parametrize('action,cut,status', [
    (Wait('INPUT'), 'MODEL_DECISION', 'waiting'),
    (ClaimComplete(), 'MODEL_DECISION', 'completed'),
    (ClaimComplete(), 'COMPLETION_CLAIMED', 'completed'),
    (ClaimComplete(), 'COMPLETION_VERIFIED', 'completed'),
])
@pytest.mark.parametrize('pause_after_restart', [False, True])
def test_restart_settles_saved_control_without_resampling(tmp_path, monkeypatch, action, cut, status, pause_after_restart):
    persist = EventLog._persist
    def crash_after_decision(log, event):
        persist(log, event)
        if event.event_type == cut:
            raise SystemExit('saved decision, action not started')
    model = _ScriptedModel([action])
    with monkeypatch.context() as patch:
        patch.setattr(EventLog, '_persist', crash_after_decision)
        organ = _organ(tmp_path, model)
        (tmp_path / 'workspace' / 'answer.txt').write_text('42', encoding='utf-8')
        try:
            with pytest.raises(SystemExit, match='saved decision'):
                organ.run_goal('Wait for actual input.', FileContentEquals('answer.txt', '42'))
        finally:
            organ.shutdown()
    reopened_model = _ScriptedModel([])
    organ = _organ(tmp_path, reopened_model)
    try:
        if pause_after_restart:
            organ.interrupt()
        result = organ.resume()
        assert result.status == status
        if status == 'waiting':
            assert result.state.waiting_for == 'INPUT'
        assert result.state.decision_count == 1
        assert len(model.received_requests) == 1 and not reopened_model.received_requests
        assert sum(e.event_type == 'MODEL_DECISION' for e in result.events) == 1
        assert sum(e.event_type == ('ROOT_WAITING' if status == 'waiting' else 'COMPLETION_VERIFIED')
                   for e in result.events) == 1
    finally:
        organ.shutdown()


def test_cold_kernel_retires_unstarted_python_without_replaying_known_work(tmp_path, monkeypatch):
    from pathlib import Path
    from Execution.ipython_control import IPythonResult
    class Namespace:
        def __init__(self, epoch):
            self.kernel_epoch = epoch
            self.values, self.actions = {'Path': Path, 'workspace': tmp_path / 'workspace'}, []
        def execute(self, code):
            self.actions.append(code)
            exec(code, self.values)
            return IPythonResult(True)
        def close(self):
            pass
    first = Namespace('first-kernel')
    model = _ScriptedModel([IPythonCode('x = 41'),
                           IPythonCode("(workspace / 'answer.txt').write_text(str(x + 1))")])
    organ = _organ(tmp_path, model, max_decisions=6, max_decisions_per_advance=1, ipython_control=first)
    persist = EventLog._persist
    def crash(log, event):
        persist(log, event)
        if event.event_type == 'MODEL_DECISION' and event.payload['frame'].decision_id == 'decision-000002':
            raise SystemExit('saved Python plan, no action start')
    try:
        organ.run_goal('Deliver the checked answer.', FileContentEquals('answer.txt', '42'))
        with monkeypatch.context() as patch:
            patch.setattr(EventLog, '_persist', crash)
            with pytest.raises(SystemExit):
                organ.resume()
    finally:
        organ.shutdown()
    assert first.actions == ['x = 41']
    cold = Namespace('second-kernel')
    resumed_model = _ScriptedModel([IPythonCode("(workspace / 'answer.txt').write_text('42')"), ClaimComplete()])
    organ = _organ(tmp_path, resumed_model, max_decisions=6, max_decisions_per_advance=1, ipython_control=cold)
    try:
        retired = organ.resume()
        assert retired.status == 'running' and not cold.actions and not resumed_model.received_requests
        assert organ.retired_decisions() == ('decision-000002',)
        assert sum(e.event_type == 'IPYTHON_EXECUTION_RESULT' for e in retired.events) == 1
        assert organ.resume().status == 'running'
        assert organ.resume().status == 'completed'
        assert len(cold.actions) == 1 and 'x + 1' not in cold.actions[0]
        assert 'second-kernel' in resumed_model.received_requests[0].context
        assert (tmp_path / 'workspace' / 'answer.txt').read_text() == '42'
    finally:
        organ.shutdown()


def test_root_ipython_completion_uses_the_frozen_production_surface(tmp_path):
    model = _ScriptedModel(
        [
            IPythonCode(
                "from pathlib import Path\n"
                "Path('answer.txt').write_text('42', encoding='utf-8')"
            ),
            ClaimComplete(),
        ]
    )
    organ = _organ(tmp_path, model, max_decisions=2)

    try:
        result = organ.run_goal(
            "Write the answer.",
            FileContentEquals("answer.txt", "42"),
        )
    finally:
        organ.shutdown()

    assert result.status == "completed"
    assert organ.state == result.state
    assert organ.result == result
    assert tuple(model.received_requests[0].available_tools) == ROOT_SURFACE
    assert (tmp_path / "workspace" / "answer.txt").read_text(encoding="utf-8") == "42"


def test_finite_advance_preserves_kernel_and_is_not_a_business_event(tmp_path):
    model = _ScriptedModel([
        IPythonCode("value = 41"),
        IPythonCode("open('answer.txt', 'w').write(str(value + 1))"),
        ClaimComplete(),
    ])
    organ = _organ(tmp_path, model, max_decisions_per_advance=1)
    try:
        first = organ.run_goal("Write the answer.", FileContentEquals("answer.txt", "42"))
        assert first.status == "running" and first.state.decision_count == 1
        second = organ.resume()
        assert second.status == "running" and second.state.decision_count == 2
        assert second.state.execution_id == first.state.execution_id
        third = organ.resume()
        assert third.status == "completed"
        assert not {"ROOT_WAITING", "EXTERNAL_EVENT", "ACTOR_SUSPENDED"}.intersection(
            e.event_type for e in third.events)
    finally:
        organ.shutdown()


@pytest.mark.parametrize('pause_after_restart', [False, True])
def test_completion_handoff_survives_restart_without_finishing_or_replaying(tmp_path, pause_after_restart):
    pending = [True]
    model = _ScriptedModel([ClaimComplete()])
    checkpoint = tmp_path/'state'/'checkpoint.json'
    organ = _organ(tmp_path, model, completion_review_required=lambda: pending[0], checkpoint_path=checkpoint)
    (tmp_path/'workspace'/'answer.txt').write_text('42', encoding='utf-8')
    first = organ.run_goal('Deliver the answer.', FileContentEquals('answer.txt', '42'))
    assert first.status == 'running' and organ.completion_review_pending()
    assert first.state.latest_observation.status == 'review_pending'
    assert first.state.latest_observation.evidence.matched
    run_id = first.state.execution_id
    saved = (tmp_path/'state'/'execution.jsonl').read_bytes()
    organ.shutdown()
    resumed_model = _ScriptedModel([])
    organ = _organ(tmp_path, resumed_model, completion_review_required=lambda: pending[0], checkpoint_path=checkpoint)
    try:
        assert organ.completion_review_pending()
        assert len(resumed_model.received_requests) == 0
        assert organ.resume().state == first.state  # Outstanding review is quiet.
        assert (tmp_path/'state'/'execution.jsonl').read_bytes() == saved
        if pause_after_restart:
            organ.interrupt()
            assert organ.completion_review_pending()
        pending[0] = False  # The trusted host accepted its result review.
        result = organ.resume()
        assert result.status == 'completed' and result.state.execution_id == run_id
        assert sum(e.event_type == 'COMPLETION_DEFERRED' for e in result.events) == 1
        assert sum(e.event_type == 'COMPLETION_CLAIMED' for e in result.events) == 1
        assert not resumed_model.received_requests
        assert result.state.decision_count == 1
        assert fold_execution_state(EventLog.load(tmp_path/'state'/'execution.jsonl').events) == result.state
        assert (tmp_path/'state'/'execution.jsonl').read_bytes().startswith(saved)
        assert not any(e.event_type == 'ROOT_WAITING' for e in result.events)
    finally:
        organ.shutdown()


def test_deferred_completion_rechecks_environment_instead_of_trusting_the_old_match(tmp_path):
    pending = [True]
    model = _ScriptedModel([ClaimComplete(), Wait('REPLACEMENT')])
    organ = _organ(tmp_path, model, completion_review_required=lambda: pending[0])
    output = tmp_path/'workspace'/'answer.txt'
    output.write_text('42', encoding='utf-8')
    try:
        original = organ.run_goal('Deliver the answer.', FileContentEquals('answer.txt', '42'))
        output.write_text('not the requested answer', encoding='utf-8')
        pending[0] = False
        result = organ.resume()
        assert result.state.waiting_for == 'REPLACEMENT'
        assert result.events[:len(original.events)] == original.events
        rejection, = [e for e in result.events if e.event_type == 'COMPLETION_REJECTED']
        assert not rejection.payload['observation'].evidence.matched
        assert rejection.source_event_refs == (original.events[-1].event_id,)
        assert sum(e.event_type == 'COMPLETION_CLAIMED' for e in result.events) == 1
        assert not any(e.event_type == 'EXECUTION_COMPLETED' for e in result.events)
        assert fold_execution_state(EventLog.load(tmp_path/'state'/'execution.jsonl').events) == result.state
    finally:
        organ.shutdown()


def test_deferred_claim_is_not_resumed_after_a_later_completed_action(tmp_path):
    pending = [True]
    model = _ScriptedModel([ClaimComplete(), IPythonCode('pass')])
    organ = _organ(tmp_path, model, completion_review_required=lambda: pending[0], max_decisions_per_advance=1)
    (tmp_path/'workspace'/'answer.txt').write_text('42', encoding='utf-8')
    try:
        original = organ.run_goal('Deliver the answer.', FileContentEquals('answer.txt', '42'))
        pending[0] = False
        after_action = organ.resume(decision_advisory=('decision-000002', 'Investigate the newly raised condition.'))
        assert after_action.status == 'running' and not organ.completion_review_pending()
        assert after_action.state.decision_count == 2
    finally:
        organ.shutdown()
    resumed_model = _ScriptedModel([Wait('CURRENT_INPUT')])
    organ = _organ(tmp_path, resumed_model, completion_review_required=lambda: False)
    try:
        result = organ.resume()
        assert result.state.waiting_for == 'CURRENT_INPUT'
        assert result.events[:len(after_action.events)] == after_action.events
        assert result.events[:len(original.events)] == original.events
        assert sum(e.event_type == 'COMPLETION_CLAIMED' for e in result.events) == 1
        assert not any(e.event_type == 'EXECUTION_COMPLETED' for e in result.events)
        assert len(resumed_model.received_requests) == 1
    finally:
        organ.shutdown()


def test_failed_completion_policy_read_leaves_a_recoverable_decision(tmp_path):
    def unavailable():
        raise ValueError('policy_source_unavailable')
    model = _ScriptedModel([ClaimComplete()])
    organ = _organ(tmp_path, model, completion_review_required=unavailable)
    (tmp_path/'workspace'/'answer.txt').write_text('42', encoding='utf-8')
    try:
        with pytest.raises(ValueError, match='policy_source_unavailable'):
            organ.run_goal('Deliver the answer.', FileContentEquals('answer.txt', '42'))
        assert not model.received_requests
        assert organ.state.decision_count == 0
    finally:
        organ.shutdown()
    organ = _organ(tmp_path, model, completion_review_required=lambda: False)
    try:
        assert organ.resume().status == 'completed'
        assert len(model.received_requests) == 1
    finally:
        organ.shutdown()


def test_finite_advance_settles_native_batch_before_returning(tmp_path):
    actions = (
        IPythonCode("value = 40\nprint(value)"),
        IPythonCode("value += 1\nprint(value)"),
    )
    call_ids = ("call_set", "call_increment")
    model = _ScriptedModel([
        NativeModelDecision(
            action=actions,
            provider_wire_request={},
            raw_provider_response={"role": "assistant", "content": [
                {"type": "tool_use", "id": call_id, "name": "ipython",
                 "input": {"code": action.code}}
                for call_id, action in zip(call_ids, actions, strict=True)
            ]},
            provider_tool_call_id=call_ids,
        ),
        IPythonCode("value += 1\nopen('answer.txt', 'w').write(str(value))"),
        ClaimComplete(),
    ])
    organ = _organ(tmp_path, model, max_decisions_per_advance=1)
    try:
        first = organ.run_goal("Write the answer.", FileContentEquals("answer.txt", "42"))
        assert first.status == "running" and first.state.decision_count == 1
        assert len(model.received_requests) == 1
        assert tuple(step.action for step in first.steps) == actions
        assert all(step.observation.ok for step in first.steps)
        assert tuple(step.observation.provider_tool_call_id for step in first.steps) == call_ids
        assert tuple(step.observation.result.output for step in first.steps) == ("40\n", "41\n")
        assert first.events[-1].event_type == "IPYTHON_EXECUTION_RESULT"

        second = organ.resume()
        assert second.status == "running" and second.state.decision_count == 2
        assert second.state.execution_id == first.state.execution_id
        assert (tmp_path / "workspace" / "answer.txt").read_text(encoding="utf-8") == "42"
        request = model.received_requests[1]
        assert request.native_tool_continuation.provider_tool_call_id == call_ids
        observations = json.loads(request.context)["observations"]
        assert [item["result"]["output"]["text"] for item in observations] == ["40\n", "41\n"]

        completed = organ.resume()
        assert completed.status == "completed"
        assert sum(event.event_type == "IPYTHON_EXECUTION_STARTED" for event in completed.events) == 3
        assert not {"ROOT_WAITING", "ROOT_WOKEN", "EXTERNAL_EVENT_RECEIVED",
                    "INTERRUPT_REQUESTED", "ACTOR_SUSPENDED"}.intersection(
            event.event_type for event in completed.events)
    finally:
        organ.shutdown()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_finite_advance_rejects_invalid_host_budget(tmp_path, limit):
    with pytest.raises(ValueError, match="max_decisions_per_advance"):
        _organ(tmp_path, _ScriptedModel([]), max_decisions_per_advance=limit)


def test_external_wake_is_consumed_by_decision_and_new_equal_text_survives_restart(tmp_path):
    first_model = _ScriptedModel([Wait("evidence")])
    first = _organ(tmp_path, first_model, max_decisions=6, max_decisions_per_advance=1)
    try:
        assert not first.has_unhandled_external_event()
        first.run_goal("Review each new observation.", FileContentEquals("answer.txt", "done"))
        assert not first.has_unhandled_external_event()
        first.deliver_event("unrelated", "same text", defer_actions=True)
        assert not first.has_unhandled_external_event()
        woken = first.deliver_event("evidence", "same text", defer_actions=True)
        first_wake = woken.events[-1]
        assert first.has_unhandled_external_event()
        assert len(first_model.received_requests) == 1
    finally:
        first.shutdown()

    model = _ScriptedModel([
        IPythonCode("value = 7"), IPythonCode("print(value)"), Wait("evidence"),
    ])
    restored = _organ(tmp_path, model, max_decisions=6, max_decisions_per_advance=1)
    try:
        before = restored.state
        assert restored.has_unhandled_external_event()
        assert restored.has_unhandled_external_event()
        assert restored.state == before and not model.received_requests
        restored.resume()
        assert not restored.has_unhandled_external_event()
        second = restored.resume()
        assert second.steps[0].observation.result.output == "7\n"
        assert not restored.has_unhandled_external_event()
        restored.resume()
        assert restored.state.status == "waiting"
        assert not restored.has_unhandled_external_event()
        woken = restored.deliver_event("evidence", "same text", defer_actions=True)
        assert woken.events[-1].payload == first_wake.payload
        assert woken.events[-1].event_id != first_wake.event_id
        assert restored.has_unhandled_external_event()
    finally:
        restored.shutdown()

    final_model = _ScriptedModel([Wait("finished")])
    final = _organ(tmp_path, final_model, max_decisions=6, max_decisions_per_advance=1)
    try:
        assert final.has_unhandled_external_event()
        final.resume()
        assert not final.has_unhandled_external_event()
        assert len(final_model.received_requests) == 1
    finally:
        final.shutdown()


@pytest.mark.parametrize("guidance", ["Use the existing evidence to finish the answer.", ("Sourced condition. " * 300).strip()], ids=["short", "long"])
def test_waiting_root_redirect_keeps_context_and_consumes_guidance_once(tmp_path, guidance):
    model = _ScriptedModel([
        IPythonCode("value = 41"), Wait("original_evidence"),
        IPythonCode("open('answer.txt', 'w').write(str(value + 1))"), Wait("later"),
    ])
    organ = _organ(tmp_path, model, max_decisions=6, max_decisions_per_advance=1, max_context_chars=16000)
    advisory = ("decision-000003", guidance)
    try:
        started = organ.run_goal("Write the answer.", FileContentEquals("answer.txt", "42"))
        waiting = organ.resume()
        assert waiting.status == "waiting"
        assert organ.resume().events == waiting.events
        advanced = organ.resume(decision_advisory=advisory)
        assert advanced.state.execution_id == started.state.execution_id
        assert advanced.state.root_actor_id == started.state.root_actor_id
        assert advanced.state.goal == started.state.goal
        assert advanced.state.decision_count == 3
        assert (tmp_path / "workspace" / "answer.txt").read_text() == "42"
        redirected = advanced.events[len(waiting.events)]
        assert redirected.event_type == "ROOT_REDIRECTED"
        assert redirected.payload == {"advisory": advisory}
        assert redirected.source_event_refs == (waiting.events[-1].event_id,)
        request = model.received_requests[2]
        assert request.source_event_refs == (redirected.event_id,)
        assert json.loads(request.context)["mind_supervisor_directive"] == advisory[1]
        assert json.loads(request.context)["incoming_event"] is None
        assert not organ.has_unhandled_external_event()
        assert not {"ROOT_WOKEN", "EXTERNAL_EVENT_RECEIVED"}.intersection(
            event.event_type for event in advanced.events)
        assert organ.resume().status == "waiting"
        assert "mind_supervisor_directive" not in json.loads(model.received_requests[3].context)
        settled = organ.state
        assert organ.resume(decision_advisory=advisory).state == settled
        assert len(model.received_requests) == 4
    finally:
        organ.shutdown()


@pytest.mark.parametrize("advisory", [
    None, ("decision-000001", "Old direction"), ("decision-000003", "Future direction"),
    ("another-run:decision-000002", "Wrong target"), ("decision-000002", ""),
    ("decision-000002", " padded "), ("decision-000002", "x" * 6201),
    ["decision-000002", "Wrong shape"],
])
def test_invalid_waiting_redirect_never_appends_or_calls_model(tmp_path, advisory):
    model = _ScriptedModel([Wait("original_evidence")])
    organ = _organ(tmp_path, model)
    try:
        waiting = organ.run_goal("Wait.", FileContentEquals("answer.txt", "42"))
        before = (tmp_path / "state" / "execution.jsonl").read_bytes()
        result = organ.resume(decision_advisory=advisory)
        assert result.state == waiting.state and result.events == waiting.events
        assert (tmp_path / "state" / "execution.jsonl").read_bytes() == before
        assert len(model.received_requests) == 1
    finally:
        organ.shutdown()


@pytest.mark.parametrize("interrupt_before_restart", [False, True])
def test_waiting_redirect_replays_after_restart_before_its_decision(tmp_path, interrupt_before_restart):
    unavailable = [False]

    def completion_policy():
        if unavailable[0]:
            raise ValueError("policy_unavailable")
        return False

    first_model = _ScriptedModel([Wait("original_evidence")])
    organ = _organ(tmp_path, first_model, completion_review_required=completion_policy)
    advisory = ("decision-000002", "Reconsider the direction using current evidence.")
    try:
        waiting = organ.run_goal("Reconsider.", FileContentEquals("answer.txt", "42"))
        unavailable[0] = True
        with pytest.raises(ValueError, match="policy_unavailable"):
            organ.resume(decision_advisory=advisory)
        assert organ.state.status == "running"
        assert len(first_model.received_requests) == 1
        if interrupt_before_restart:
            assert organ.interrupt().status == "suspended"
    finally:
        organ.shutdown()

    model = _ScriptedModel([IPythonCode("print('reconsidered')"), Wait("later")])
    restored = _organ(tmp_path, model, max_decisions_per_advance=1)
    try:
        result = restored.resume()
        assert result.state.execution_id == waiting.state.execution_id
        assert json.loads(model.received_requests[0].context)["mind_supervisor_directive"] == advisory[1]
        assert sum(event.event_type == "ROOT_REDIRECTED" for event in result.events) == 1
        assert result.events[:len(waiting.events)] == waiting.events
        assert restored.resume().status == "waiting"
        assert "mind_supervisor_directive" not in json.loads(model.received_requests[1].context)
        assert len(model.received_requests) == 2
    finally:
        restored.shutdown()


def test_redirect_validator_and_reducer_reject_a_wrong_decision(tmp_path):
    organ = _organ(tmp_path, _ScriptedModel([Wait("original_evidence")]))
    try:
        waiting = organ.run_goal("Wait.", FileContentEquals("answer.txt", "42"))
        payload = {"advisory": ("decision-000001", "Old direction")}
        refs = (waiting.events[-1].event_id,)
        with pytest.raises(ValueError, match="redirect"):
            organ._event_log.append("ROOT_REDIRECTED", payload, refs)
        sequence = len(waiting.events) + 1
        forged = ExecutionEvent(f"event-{sequence:06d}", sequence, "ROOT_REDIRECTED", payload, refs)
        with pytest.raises(ValueError, match="redirect"):
            fold_execution_state((*waiting.events, forged))
        assert organ.state == waiting.state
    finally:
        organ.shutdown()


def test_waiting_child_rejects_root_redirect_at_facade_and_trace(tmp_path):
    child_ref = ChildRef("child-run", "child-actor", "root-actor", "Local task.", None)
    model = _ScriptedModel([Wait("local_evidence")], tool_contracts=CHILD_SURFACE)
    child = _organ(tmp_path, model, _child_ref=child_ref)
    try:
        waiting = child.run_child()
        advisory = ("decision-000002", "Change the direction.")
        with pytest.raises(ValueError, match="Child execution"):
            child.resume(decision_advisory=advisory)
        with pytest.raises(ValueError, match="redirect"):
            child._event_log.append("ROOT_REDIRECTED", {"advisory": advisory},
                                    (waiting.events[-1].event_id,))
        assert child.state == waiting.state
        assert len(model.received_requests) == 1
    finally:
        child.shutdown()


def test_redirect_preserves_an_unmatched_external_event_without_certifying_it(tmp_path):
    model = _ScriptedModel([Wait("original_evidence"), Wait("different_dependency")])
    organ = _organ(tmp_path, model)
    try:
        organ.run_goal("Review direction.", FileContentEquals("answer.txt", "42"))
        waiting = organ.deliver_event("unrelated", "A real unrelated observation.")
        result = organ.resume(decision_advisory=(organ.next_root_decision_id, "Reassess direction."))
        redirected = result.events[len(waiting.events)]
        assert redirected.source_event_refs == (waiting.events[-1].event_id,)
        assert redirected.event_type == "ROOT_REDIRECTED"
        assert not any(event.event_type == "ROOT_WOKEN" for event in result.events)
        context = json.loads(model.received_requests[-1].context)
        assert context["incoming_event"]["event_type"]["text"] == "unrelated"
        assert result.state.waiting_for == "different_dependency"
    finally:
        organ.shutdown()


def test_redirect_never_calls_model_if_accepted_guidance_would_be_omitted(tmp_path):
    model = _ScriptedModel([Wait("original_evidence")])
    organ = _organ(tmp_path, model, max_context_chars=1000)
    try:
        organ.run_goal("Wait.", FileContentEquals("answer.txt", "42"))
        with pytest.raises(ValueError, match="guidance exceeds"):
            organ.resume(decision_advisory=("decision-000002", "x" * 1200))
        assert len(model.received_requests) == 1
        assert organ.state.decision_count == 1
        with pytest.raises(ValueError, match="guidance exceeds"):
            organ.resume()
        assert len(model.received_requests) == 1
    finally:
        organ.shutdown()


@pytest.mark.parametrize("withdraw", [False, True])
@pytest.mark.parametrize("suspended", [False, True])
@pytest.mark.parametrize("restart", [False, True])
def test_pending_redirect_replacement_and_withdrawal_are_durable(tmp_path, withdraw, suspended, restart):
    unavailable = [False]

    def policy():
        if unavailable[0]:
            raise ValueError("policy_unavailable")
        return False

    model = _ScriptedModel([Wait("original_evidence"), Wait("later")])
    organ = _organ(tmp_path, model, completion_review_required=policy)
    old = ("decision-000002", "Continue the old direction.")
    revised = ("decision-000002", None if withdraw else "Use the revised direction.")
    try:
        started = organ.run_goal("Review direction.", FileContentEquals("answer.txt", "42"))
        unavailable[0] = True
        with pytest.raises(ValueError, match="policy_unavailable"):
            organ.resume(decision_advisory=old)
        if suspended:
            assert organ.interrupt().status == "suspended"
        if withdraw:
            cancelled = organ.resume(decision_advisory=revised)
            assert cancelled.status == ("suspended" if suspended else "waiting")
            assert cancelled.state.waiting_for == "original_evidence"
        else:
            with pytest.raises(ValueError, match="policy_unavailable"):
                organ.resume(decision_advisory=revised)
        assert len(model.received_requests) == 1
        redirects = [e for e in organ._event_log.events if e.event_type == "ROOT_REDIRECTED"]
        assert [e.payload["advisory"] for e in redirects] == [old, revised]
        unavailable[0] = False
        if restart:
            organ.shutdown()
            model = _ScriptedModel([Wait("later")])
            organ = _organ(tmp_path, model)
        before = len(model.received_requests)
        result = organ.resume()
        assert result.state.execution_id == started.state.execution_id
        if withdraw:
            assert result.status == "waiting" and result.state.waiting_for == "original_evidence"
            assert len(model.received_requests) == before
            # Only the actual outside event may now satisfy the restored wait.
            result = organ.deliver_event("original_evidence", "The original dependency arrived.")
            assert "mind_supervisor_directive" not in json.loads(model.received_requests[-1].context)
        else:
            assert json.loads(model.received_requests[-1].context)["mind_supervisor_directive"] == revised[1]
        assert result.status == "waiting" and result.state.waiting_for == "later"
        assert len(model.received_requests) == before + 1
        assert sum(e.event_type == "ROOT_REDIRECTED" for e in result.events) == 2
    finally:
        organ.shutdown()


@pytest.mark.parametrize("invalid", [
    ("decision-000001", None), ("decision-000003", "Wrong target"),
    ("decision-000002", ""), ("decision-000002", "x" * 6201),
])
def test_invalid_pending_redirect_change_does_not_advance(tmp_path, invalid):
    unavailable = [False]
    def policy():
        if unavailable[0]:
            raise ValueError("policy_unavailable")
        return False
    model = _ScriptedModel([Wait("original_evidence")])
    organ = _organ(tmp_path, model, completion_review_required=policy)
    try:
        organ.run_goal("Wait.", FileContentEquals("answer.txt", "42"))
        unavailable[0] = True
        with pytest.raises(ValueError, match="policy_unavailable"):
            organ.resume(decision_advisory=("decision-000002", "Original guidance."))
        before = organ.state
        raw = (tmp_path / "state" / "execution.jsonl").read_bytes()
        assert organ.resume(decision_advisory=invalid).state == before
        assert (tmp_path / "state" / "execution.jsonl").read_bytes() == raw
        assert len(model.received_requests) == 1
    finally:
        organ.shutdown()


def test_withdrawal_without_pending_direction_cannot_recreate_an_old_wait(tmp_path):
    model = _ScriptedModel([Wait("original_evidence")])
    organ = _organ(tmp_path, model)
    try:
        organ.run_goal("Wait.", FileContentEquals("answer.txt", "42"))
        woken = organ.deliver_event("original_evidence", "Arrived.", defer_actions=True)
        result = organ.resume(decision_advisory=("decision-000002", None))
        assert result.events == woken.events and result.state == woken.state
        assert result.status == "running" and result.state.waiting_for is None
        assert len(model.received_requests) == 1
        with pytest.raises(ValueError, match="redirect"):
            organ._event_log.append("ROOT_REDIRECTED", {"advisory": ("decision-000002", None)},
                                   (result.events[-1].event_id,))
    finally:
        organ.shutdown()


def test_reality_projection_is_execution_owned_and_immutable(tmp_path):
    organ = _organ(tmp_path, _ScriptedModel([Wait("continue")]))

    try:
        result = organ.run_goal(
            "Wait for one external event.",
            FileContentEquals("answer.txt", "done"),
        )
        evidence = organ.reality_evidence()
    finally:
        organ.shutdown()

    assert result.status == "waiting"
    assert len(evidence) == 1
    assert isinstance(evidence[0], RealityEvidence)
    assert evidence[0].kind == "PRE_OUTCOME"
    assert evidence[0].sequence < len(result.events) + 1
    assert evidence[0].source_event_refs[-1] == "event-000003"
    with pytest.raises(TypeError):
        evidence[0].payload["execution_status"] = "mutated"
    with pytest.raises(dataclasses.FrozenInstanceError):
        evidence[0].kind = "OUTCOME"

    with pytest.raises(TypeError):
        RealityEvidence(
            evidence_ref="forged",
            execution_ref="forged",
            source_event_refs=("event-000001",),
            kind="PRE_OUTCOME",
            sequence=1,
            payload={},
        )


def test_reality_projection_is_bounded_and_cursor_ordered(tmp_path):
    waits = [Wait(f"wake-{index}") for index in range(10)]
    organ = _organ(tmp_path, _ScriptedModel(waits), max_decisions=12)

    try:
        organ.run_goal(
            "Wait repeatedly.",
            FileContentEquals("answer.txt", "done"),
        )
        for index in range(9):
            organ.deliver_event(f"wake-{index}")
        first_page = organ.reality_evidence()
        second_page = organ.reality_evidence(
            after_sequence=first_page[-1].sequence
        )
    finally:
        organ.shutdown()

    assert len(first_page) == 8
    assert len(second_page) == 2
    assert tuple(item.sequence for item in first_page) == tuple(
        sorted(item.sequence for item in first_page)
    )
    assert all(len(item.source_event_refs) <= 16 for item in (*first_page, *second_page))
    assert all(
        len(json.dumps(dict(item.payload), separators=(",", ":"), sort_keys=True))
        <= 128
        for item in (*first_page, *second_page)
    )


def test_reality_projection_keeps_late_first_wait_refs_in_sequence_order(tmp_path):
    actions = [
        IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('done')"),
        *(IPythonCode(f"value_{index} = {index}") for index in range(4)),
    ]
    actions.extend((Wait("continue"), ClaimComplete()))
    organ = _organ(tmp_path, _ScriptedModel(actions), max_decisions=7)

    try:
        waiting = organ.run_goal(
            "Do local computation, wait, then complete.",
            FileContentEquals("answer.txt", "done"),
        )
        completed = organ.deliver_event("continue")
        outcome = organ.reality_evidence()[-1]
    finally:
        organ.shutdown()

    assert waiting.status == "waiting"
    assert completed.status == "completed"
    assert len(outcome.source_event_refs) == 16
    assert outcome.source_event_refs == tuple(
        sorted(
            outcome.source_event_refs,
            key=lambda event_ref: int(event_ref.removeprefix("event-")),
        )
    )


def test_reality_projection_is_redacted_deterministic_and_replayable(tmp_path):
    secret_path = str((tmp_path / "private" / "secret.txt").resolve())
    secret_code = "open('secret.txt').read() # RAW_CODE_SENTINEL"
    model = _ScriptedModel(
        [
            NativeModelDecision(
                action=Wait("continue"),
                provider_wire_request={
                    "provider_body": "PROVIDER_SENTINEL",
                    "path": secret_path,
                },
                raw_provider_response={
                    "reasoning": secret_code,
                    "file_content": "FILE_CONTENT_SENTINEL",
                },
            )
        ]
    )
    organ = _organ(tmp_path, model)

    try:
        organ.run_goal(
            "Keep PRIVATE_GOAL_SENTINEL internal.",
            FileContentEquals(secret_path, "FILE_CONTENT_SENTINEL"),
        )
        first = organ.reality_evidence()
        second = organ.reality_evidence()
    finally:
        organ.shutdown()

    replay_organ = _organ(tmp_path, _ScriptedModel([]))
    try:
        replayed = replay_organ.reality_evidence()
    finally:
        replay_organ.shutdown()
    serialized = json.dumps(
        [
            {
                "evidence_ref": item.evidence_ref,
                "execution_ref": item.execution_ref,
                "source_event_refs": item.source_event_refs,
                "kind": item.kind,
                "sequence": item.sequence,
                "payload": dict(item.payload),
            }
            for item in first
        ],
        sort_keys=True,
    )

    assert first == second == replayed
    assert len(serialized) < 1_024
    for forbidden in (
        "PROVIDER_SENTINEL",
        "RAW_CODE_SENTINEL",
        "FILE_CONTENT_SENTINEL",
        "PRIVATE_GOAL_SENTINEL",
        secret_path,
    ):
        assert forbidden not in serialized


def test_restart_replays_completed_state_without_model_or_action_replay(tmp_path):
    first_model = _ScriptedModel(
        [
            IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('done')"),
            ClaimComplete(),
        ]
    )
    first = _organ(tmp_path, first_model, max_decisions=2)
    completed = first.run_goal(
        "Write done.",
        FileContentEquals("answer.txt", "done"),
    )
    execution_id = completed.state.execution_id
    root_actor_id = completed.state.root_actor_id
    first.shutdown()

    class _NoCallModel:
        identifier = "must-not-run"
        tool_contracts = ROOT_SURFACE

        def __init__(self):
            self.calls = 0

        def decide(self, request):
            self.calls += 1
            raise AssertionError("completed execution was sampled again")

    no_call_model = _NoCallModel()
    restored = _organ(tmp_path, no_call_model, max_decisions=2)
    replayed = restored.run_goal(
        "Write done.",
        FileContentEquals("answer.txt", "done"),
    )
    restored.shutdown()

    assert replayed.status == "completed"
    assert replayed.events == completed.events
    assert replayed.state.execution_id == execution_id
    assert replayed.state.root_actor_id == root_actor_id
    assert no_call_model.calls == 0


def test_orphan_checkpoint_fails_before_the_first_durable_event(tmp_path):
    checkpoint_path = tmp_path / "state" / "checkpoint.json"
    checkpoint_path.parent.mkdir()
    checkpoint_path.write_text("{malformed", encoding="utf-8")
    event_log_path = tmp_path / "state" / "execution.jsonl"
    organ = _organ(
        tmp_path,
        _ScriptedModel([ClaimComplete()]),
        event_log_path=event_log_path,
        checkpoint_path=checkpoint_path,
    )

    try:
        with pytest.raises(ValueError, match="checkpoint"):
            organ.run_goal(
                "Do not mutate history.",
                FileContentEquals("answer.txt", "done"),
            )
    finally:
        organ.shutdown()

    assert not event_log_path.exists() or event_log_path.stat().st_size == 0


class _BlockingModel:
    identifier = "blocking-scripted"
    tool_contracts = ROOT_SURFACE

    def __init__(self):
        self.calls = 0
        self.second_call_started = threading.Event()
        self.release_second_call = threading.Event()

    def decide(self, request):
        self.calls += 1
        if self.calls == 1:
            return IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('A')")
        if self.calls == 2:
            self.second_call_started.set()
            if not self.release_second_call.wait(timeout=3):
                raise TimeoutError("test did not release model")
            return IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('B')")
        if self.calls == 3:
            return IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('B')")
        return ClaimComplete()


def test_explicit_interrupt_suspends_then_resumes_the_same_root(tmp_path):
    model = _BlockingModel()
    organ = _organ(tmp_path, model, max_decisions=3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future = pool.submit(
            organ.run_goal,
            "Write B.",
            FileContentEquals("answer.txt", "B"),
        )
        assert model.second_call_started.wait(timeout=3)
        interrupt_future = pool.submit(organ.interrupt)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = organ.state
            if state is not None and state.lifecycle_notice == "interrupt_requested":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("interrupt was not durably recorded")
        model.release_second_call.set()
        interrupted = interrupt_future.result(timeout=3)
        run_result = run_future.result(timeout=3)

    assert interrupted.status == run_result.status == "suspended"
    assert model.calls == 2
    completed = organ.resume()
    organ.shutdown()

    assert completed.status == "completed"
    assert model.calls == 4
    assert (tmp_path / "workspace" / "answer.txt").read_text(encoding="utf-8") == "B"


def test_ipython_child_return_is_driven_through_the_same_organ_seam(tmp_path):
    root_model = _ScriptedModel(
        [
            IPythonCode(
                "handle = await spawn_child('derive the local answer')\n"
                "print(handle['depth'])"
            ),
            ClaimComplete(),
        ]
    )
    root = _organ(tmp_path, root_model, max_decisions=2, max_depth=1)

    pending = root.run_goal(
        "Write the returned answer.",
        FileContentEquals("answer.txt", "42"),
    )
    child_model = _ScriptedModel(
        [
            IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('42')"),
            Return("42"),
        ],
        tool_contracts=CHILD_SURFACE,
        identifier="scripted-child",
    )
    child = root.open_child(
        pending.child_ref,
        model=child_model,
        max_decisions=2,
    )
    returned = child.run_child()
    child.shutdown()
    completed = root.accept_child(returned)
    root.shutdown()

    assert returned.status == "completed"
    assert completed.status == "completed"
    assert pending.child_ref.depth == 1
    assert pending.child_ref.parent_actor_id == completed.state.root_actor_id
    assert tuple(root_model.received_requests[0].available_tools) == ROOT_SURFACE
    assert tuple(child_model.received_requests[0].available_tools) == CHILD_SURFACE
    assert "spawn_child(goal)" in root_model.received_requests[0].context
    assert "spawn_child" not in child_model.received_requests[0].context


def test_custom_ipython_control_does_not_advertise_unbound_child_helper(tmp_path):
    class IsolatedControl:
        def close(self): pass
        def interrupt(self): return True
        def execute(self,code): raise AssertionError('No action expected')
    workspace=tmp_path/'workspace';workspace.mkdir()
    model=_ScriptedModel([Wait('OWNER_EVIDENCE')])
    organ=ExecutionOrgan(workspace=workspace,model=model,max_decisions=2,ipython_control=IsolatedControl(),
        event_log_path=tmp_path/'events.jsonl',checkpoint_path=tmp_path/'checkpoint.json')
    try:
        organ.run_goal('Wait for authorized evidence.',FileContentEquals('result','done'))
        assert 'spawn_child' not in model.received_requests[0].context
    finally:organ.shutdown()


def test_deferred_start_never_calls_model_without_its_accepted_guidance(tmp_path):
    model = _ScriptedModel([Wait("outside")])
    advisory = ("decision-000001", "sourced condition " * 200 + "end")
    organ = _organ(tmp_path, model, max_context_chars=1000)
    try:
        started = organ.run_goal("Wait.", FileContentEquals("answer.txt", "42"), defer_actions=True)
        with pytest.raises(ValueError, match="guidance exceeds"):
            organ.resume(decision_advisory=advisory)
        assert not model.received_requests
        assert organ.state.decision_count == 0
        assert organ.state.execution_id == started.state.execution_id
    finally:
        organ.shutdown()
    # The trusted caller retains the original advisory and can retry before any action.
    restored = _organ(tmp_path, model, max_context_chars=16000)
    try:
        result = restored.resume(decision_advisory=advisory)
        assert result.state.execution_id == started.state.execution_id
        assert len(model.received_requests) == 1
        assert json.loads(model.received_requests[0].context)["mind_supervisor_directive"] == advisory[1]
    finally:
        restored.shutdown()
