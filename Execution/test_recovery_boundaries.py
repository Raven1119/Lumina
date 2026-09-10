"""Saved decisions retain their authority and budgets across control suffixes."""
import json

import pytest

from Execution.execution import EventLog, FileContentEquals, IPythonCode, NativeModelDecision, Wait
from Execution.ipython_control import IPythonResult
from Execution.runtime import Execution
from Execution.test_execution_organ import _organ, _ScriptedModel
from Execution.test_runtime import decision, native, runtime
from Nervous.storage import canonical, plain


class _Kernel:
    def __init__(self, workspace, epoch):
        self.workspace, self.kernel_epoch = workspace, epoch
        self.actions = []

    def execute(self, code):
        self.actions.append(code)
        exec(code, {'workspace': self.workspace})
        return IPythonResult(True)

    def close(self):
        pass


def _crash_after_persist(monkeypatch, event_type):
    persist = EventLog._persist

    def crash(log, event):
        persist(log, event)
        if event.event_type == event_type:
            raise SystemExit('saved ' + event_type)

    monkeypatch.setattr(EventLog, '_persist', crash)


def test_cold_unstarted_python_stays_barred_after_paused_external_event(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    old = _Kernel(workspace, 'old-kernel')
    model = _ScriptedModel([IPythonCode("(workspace / 'stale.txt').write_text('old plan')")])
    organ = _organ(tmp_path, model, ipython_control=old, max_decisions_per_advance=1)
    try:
        with monkeypatch.context() as patch:
            _crash_after_persist(patch, 'MODEL_DECISION')
            with pytest.raises(SystemExit, match='saved MODEL_DECISION'):
                organ.run_goal('Continue from the current conditions.', FileContentEquals('answer.txt', '42'))
    finally:
        organ.shutdown()
    assert old.actions == []

    cold = _Kernel(workspace, 'new-kernel')
    resumed_model = _ScriptedModel([Wait('CURRENT')])
    organ = _organ(tmp_path, resumed_model, ipython_control=cold, max_decisions_per_advance=1)
    try:
        organ.interrupt()
        organ.deliver_event('OWNER_EVIDENCE', 'The prior condition has changed.', defer_actions=True)
        result = organ.resume()
        assert cold.actions == [] and not (workspace / 'stale.txt').exists()
        assert organ.retired_decisions() == ('decision-000001',)
        assert not any(event.event_type == 'IPYTHON_EXECUTION_STARTED' for event in result.events)
        assert not resumed_model.received_requests
        result = organ.resume()
        assert result.state.waiting_for == 'CURRENT'
        assert len(resumed_model.received_requests) == 1
        assert 'The prior condition has changed.' in resumed_model.received_requests[0].context
    finally:
        organ.shutdown()


@pytest.mark.parametrize('cut', ['COMPLETION_CLAIMED', 'COMPLETION_VERIFIED'])
def test_saved_completion_claim_rechecks_later_owner_input(tmp_path, monkeypatch, cut):
    answers = ([native('claim_complete', {}, 'first-claim')] if cut == 'COMPLETION_VERIFIED' else [])
    owner, calls, control = runtime(tmp_path, iter(answers + [
        native('claim_complete', {}, 'old-claim'),
        native('wait', {'event_type': 'NEW_REQUIREMENT'}, 'new-control'),
    ]))
    owner_text = 'Additional work is required; the previous completion assumption no longer holds.'
    try:
        (owner.workspace / '.lumina-complete').write_text('done', encoding='utf-8')
        owner.handle(decision(owner, 'start', 'Complete under the original condition.'))
        if cut == 'COMPLETION_VERIFIED':
            owner.advance()
            owner.handle(decision(owner, 'review-result', None))
        initial_calls = 2 if cut == 'COMPLETION_VERIFIED' else 1
        with monkeypatch.context() as patch:
            _crash_after_persist(patch, cut)
            with pytest.raises(SystemExit, match='saved ' + cut):
                owner.advance()
        assert calls.summary()['calls'] == initial_calls and control.actions == []
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        owner.handle(decision(owner, 'new-requirement', None, owner_input={
            'event_id': 'later-owner', 'event_type': 'OWNER_EVIDENCE', 'text': owner_text,
        }))
        owner.advance()
        assert owner.run_state().status != 'completed'
        if owner.run_state().waiting_for != 'NEW_REQUIREMENT':
            owner.advance()
        assert owner.run_state().waiting_for == 'NEW_REQUIREMENT'
        assert calls.summary()['calls'] == initial_calls + 1 and control.actions == []
        latest_wire = calls.records(role='execution')[-1][1]['wire']
        assert owner_text in canonical(plain(latest_wire))
        events = EventLog.load(owner.directory / 'runs' / str(owner.state['active_run']) / 'events.jsonl').events
        assert sum(event.event_type == 'COMPLETION_CLAIMED' for event in events) == initial_calls
        assert sum(event.event_type == 'COMPLETION_VERIFIED' for event in events) == (cut == 'COMPLETION_VERIFIED')
        assert not any(event.event_type == 'EXECUTION_COMPLETED' for event in events)
    finally:
        owner.close()


def test_retired_final_decision_preserves_decision_limit_failure(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    model = _ScriptedModel([IPythonCode('pass')])
    organ = _organ(tmp_path, model, max_decisions=1, ipython_control=_Kernel(workspace, 'old-kernel'))
    try:
        with monkeypatch.context() as patch:
            _crash_after_persist(patch, 'MODEL_DECISION')
            with pytest.raises(SystemExit, match='saved MODEL_DECISION'):
                organ.run_goal('Continue within the fixed decision budget.', FileContentEquals('answer.txt', '42'))
    finally:
        organ.shutdown()

    cold = _Kernel(workspace, 'new-kernel')
    resumed_model = _ScriptedModel([])
    organ = _organ(tmp_path, resumed_model, max_decisions=1, ipython_control=cold)
    try:
        assert organ.resume().status == 'running'
        result = organ.resume()
        assert result.status == 'failed' and result.failure == 'decision_limit_reached'
        assert result.state.decision_count == 1
        assert len(model.received_requests) == 1 and not resumed_model.received_requests
        assert cold.actions == []
        assert sum(event.event_type == 'DECISION_RETIRED' for event in result.events) == 1
        assert sum(event.event_type == 'EXECUTION_FAILED' for event in result.events) == 1
    finally:
        organ.shutdown()


def test_unknown_python_outcome_cannot_hide_behind_pause_and_external_input(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    old = _Kernel(workspace, 'old-kernel')
    organ = _organ(tmp_path, _ScriptedModel([IPythonCode('pass')]), ipython_control=old)
    try:
        with monkeypatch.context() as patch:
            _crash_after_persist(patch, 'IPYTHON_EXECUTION_STARTED')
            with pytest.raises(SystemExit):
                organ.run_goal('Continue safely.', FileContentEquals('answer.txt', '42'))
    finally:
        organ.shutdown()
    cold = _Kernel(workspace, 'cold-kernel')
    model = _ScriptedModel([Wait('UNSAFE')])
    organ = _organ(tmp_path, model, ipython_control=cold)
    try:
        organ.interrupt()
        organ.deliver_event('OWNER_EVIDENCE', 'New material arrived.', defer_actions=True)
        with pytest.raises(ValueError, match='interrupted IPython execution recovery is unsupported'):
            organ.resume()
        assert cold.actions == [] and not model.received_requests
    finally:
        organ.shutdown()


def test_cold_partial_python_batch_keeps_known_prefix_and_retires_only_unstarted_suffix(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    old = _Kernel(workspace, 'old-kernel')
    actions = (IPythonCode("(workspace / 'known.txt').write_text('known')"),
               IPythonCode("(workspace / 'stale.txt').write_text('stale')"))
    batch = NativeModelDecision(actions, None, None, provider_tool_call_id=('known', 'unstarted'))
    organ = _organ(tmp_path, _ScriptedModel([batch]), ipython_control=old)
    try:
        with monkeypatch.context() as patch:
            _crash_after_persist(patch, 'IPYTHON_EXECUTION_RESULT')
            with pytest.raises(SystemExit):
                organ.run_goal('Continue safely.', FileContentEquals('answer.txt', '42'))
    finally:
        organ.shutdown()

    assert len(old.actions) == 1 and (workspace / 'known.txt').read_text() == 'known'
    cold = _Kernel(workspace, 'cold-kernel')
    model = _ScriptedModel([Wait('FRESH')])
    organ = _organ(tmp_path, model, ipython_control=cold, max_decisions_per_advance=1)
    try:
        result = organ.resume()
        assert cold.actions == [] and not model.received_requests
        assert organ.retired_decisions() == ('decision-000001',)
        assert sum(e.event_type == 'IPYTHON_EXECUTION_RESULT' for e in result.events) == 1
        assert sum(e.event_type == 'IPYTHON_EXECUTION_STARTED' for e in result.events) == 1
        assert (workspace / 'known.txt').read_text() == 'known' and not (workspace / 'stale.txt').exists()
        assert organ.resume().state.waiting_for == 'FRESH'
        assert 'cold-kernel' in model.received_requests[0].context
        assert model.received_requests[0].native_tool_continuation is None
    finally:
        organ.shutdown()


@pytest.mark.parametrize('pause_at', ['response', 'first_result'])
def test_runtime_pause_and_cold_resume_do_not_replay_known_native_work(tmp_path, pause_at):
    from Nervous.provider import BudgetPause, ProviderCalls
    batch = {'stop_reason': 'tool_use', 'content': [
        {'type': 'tool_use', 'id': 'first', 'name': 'ipython',
         'input': {'code': "(workspace / 'known.txt').write_text('known')"}},
        {'type': 'tool_use', 'id': 'unstarted', 'name': 'ipython',
         'input': {'code': "(workspace / 'stale.txt').write_text('stale')"}}]}
    owner, calls, control = runtime(tmp_path, iter([]))
    def transport(role, wire):
        if pause_at == 'response':
            calls.request_pause()
        return batch
    calls.transport = transport
    execute = control.execute
    def first_result(code):
        result = execute(code)
        calls.request_pause()
        return result
    if pause_at == 'first_result':
        control.execute = first_result
    try:
        owner.handle(decision(owner, 'start', 'Use the current evidence.'))
        with pytest.raises(BudgetPause, match='user_pause_requested'):
            owner.advance()
        assert len(control.actions) == (pause_at == 'first_result')
        assert calls.summary()['calls'] == 1
    finally:
        owner.close()
    resumed_calls = ProviderCalls(calls.directory, calls.limits,
        lambda *_: native('wait', {'event_type': 'NEW_INPUT'}, 'fresh'))
    cold = _Kernel(tmp_path / 'workspace', 'cold')
    owner = Execution(owner.directory, resumed_calls, ipython=cold)
    try:
        assert owner.advance()  # Retire only the unstarted suffix, no new call.
        assert resumed_calls.summary()['calls'] == 1 and cold.actions == []
        assert owner.advance()
        assert owner.run_state().waiting_for == 'NEW_INPUT'
        assert resumed_calls.summary()['calls'] == 2 and cold.actions == []
        assert not (owner.workspace / 'stale.txt').exists()
        assert (owner.workspace / 'known.txt').exists() == (pause_at == 'first_result')
        if pause_at == 'first_result':
            assert owner.state['deliveries'][0]['decision'] == 'decision-000001'
            assert owner.state['deliveries'][0]['status'] == 'received'
            assert not owner.state['deliveries'][0].get('retired_bindings')
    finally:
        owner.close()


@pytest.mark.parametrize('owner_changed', [False, True])
def test_known_wait_response_survives_only_mechanical_pause_without_resampling(tmp_path, monkeypatch, owner_changed):
    owner, calls, control = runtime(tmp_path, iter([native('wait', {'event_type': 'INPUT'}, 'known-wait'),
                                                   native('wait', {'event_type': 'NEW'}, 'fresh-wait')]))
    append = EventLog.append
    def before_frame(log, event_type, *args, **kwargs):
        if event_type == 'MODEL_DECISION':
            raise SystemExit('response saved before frame')
        return append(log, event_type, *args, **kwargs)
    try:
        owner.handle(decision(owner, 'start', 'Await the declared observation.'))
        with monkeypatch.context() as patch:
            patch.setattr(EventLog, 'append', before_frame)
            with pytest.raises(SystemExit):
                owner.advance()
        original = calls.records(role='execution')[0][1]['wire']
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        if owner_changed:
            owner.handle(decision(owner, 'changed', None, owner_input={
                'event_id': 'new-condition', 'event_type': 'OWNER_EVIDENCE', 'text': 'The requested observation has changed.'}))
        owner.actor.interrupt()
        result = owner.actor.resume()
        if owner_changed:
            assert result.status == 'running' and owner.actor.retired_decisions() == ('decision-000001',)
            assert calls.summary()['calls'] == 1
            owner.advance()
            assert owner.run_state().waiting_for == 'NEW' and calls.summary()['calls'] == 2
            return
        assert result.status == 'waiting' and result.state.waiting_for == 'INPUT'
        assert calls.summary()['calls'] == 1 and control.actions == []
        assert not owner.actor.retired_decisions()
        frame, = result.decision_frames
        assert plain(frame.provider_wire_request) == original
        assert frame.actual_request.source_event_refs != frame.source_event_refs
    finally:
        owner.close()


@pytest.mark.parametrize('cut', ['IPYTHON_EXECUTION_STARTED', 'IPYTHON_EXECUTION_RESULT'])
def test_process_termination_keeps_known_effect_and_stops_unknown_outcome(tmp_path, cut):
    import subprocess
    import sys
    from Nervous.provider import ProviderCalls
    script = '''
import os, sys
from pathlib import Path
from Execution.execution import EventLog
from Execution.test_runtime import runtime, decision, native
owner, calls, control = runtime(Path(sys.argv[1]), iter([native('ipython', {
    'code': "p = workspace / 'effects.txt'; p.write_text((p.read_text() if p.exists() else '') + 'effect')"})]))
persist = EventLog._persist
def crash(log, event):
    persist(log, event)
    if event.event_type == sys.argv[2]:
        os._exit(27)
EventLog._persist = crash
owner.handle(decision(owner, 'start', 'Perform one authorized local action.'))
owner.advance()
'''
    ended = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(tmp_path), cut],
                           capture_output=True, text=True, timeout=30)
    assert ended.returncode == 27, ended.stderr
    directory, workspace = tmp_path / 'private' / 'execution', tmp_path / 'workspace'
    view = Execution.inspect_directory(directory)
    assert view['diagnostic']['issue'] is None
    assert bool(view['recovery']['unknown_action']) == (cut == 'IPYTHON_EXECUTION_STARTED')
    effect = workspace / 'effects.txt'
    before = (effect.read_bytes(), effect.stat().st_mtime_ns) if effect.exists() else None
    calls = ProviderCalls(tmp_path / 'private' / 'calls',
        {'calls': 40, 'output_tokens': 1000000, 'request_bytes': 10000000},
        lambda *_: native('wait', {'event_type': 'LATER'}, 'next'))
    owner = Execution(directory, calls, ipython=_Kernel(workspace, 'cold'))
    try:
        if cut == 'IPYTHON_EXECUTION_STARTED':
            assert not owner.advance() and not owner.advance()
            assert owner.status()['stop_reason'] == 'execution_action_outcome_requires_owner_check'
            assert calls.summary()['calls'] == 1 and not effect.exists()
        else:
            assert owner.advance() and owner.run_state().waiting_for == 'LATER'
            assert calls.summary()['calls'] == 2
            assert (effect.read_bytes(), effect.stat().st_mtime_ns) == before
            assert effect.read_text() == 'effect'
    finally:
        owner.close()


def test_known_response_is_recovered_before_constructing_a_new_context(tmp_path, monkeypatch):
    import Execution.execution as engine
    owner, calls, control = runtime(tmp_path, iter([native('wait', {'event_type': 'INPUT'}, 'known')]))
    append = EventLog.append
    def before_frame(log, event_type, *args, **kwargs):
        if event_type == 'MODEL_DECISION':
            raise SystemExit('known response, missing Frame')
        return append(log, event_type, *args, **kwargs)
    try:
        owner.handle(decision(owner, 'start', 'Await the observation.'))
        with monkeypatch.context() as patch:
            patch.setattr(EventLog, 'append', before_frame)
            with pytest.raises(SystemExit):
                owner.advance()
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        with monkeypatch.context() as patch:
            patch.setattr(engine, '_build_model_request',
                          lambda *a, **k: pytest.fail('Rebuilt context before claiming the known response'))
            owner.advance()
        assert owner.run_state().waiting_for == 'INPUT' and calls.summary()['calls'] == 1
    finally:
        owner.close()


@pytest.mark.parametrize('pause', [False, True])
@pytest.mark.parametrize('retirement_cut', [None, 'before', 'after'])
@pytest.mark.parametrize('pending_change', [None, 'replace', 'withdraw'])
def test_public_resume_preserves_changed_guidance_when_retiring_known_response(
        tmp_path, monkeypatch, pause, retirement_cut, pending_change):
    from Execution.model import ExecutionHistory, ExecutionModel
    from Nervous.provider import ProviderCalls
    old_guidance, new_guidance = 'Wait for OLD.', 'OLD is obsolete; wait for NEW.'
    replies = iter([native('wait', {'event_type': 'OLD'}, 'old-response'),
                    native('wait', {'event_type': 'NEW'}, 'new-response')])
    calls = ProviderCalls(tmp_path / 'calls',
        {'calls': 2, 'output_tokens': 200, 'request_bytes': 200000}, lambda *_: next(replies))

    def model():
        history = ExecutionHistory(calls)
        def send(wire, **kwargs):
            previous, metadata = history.attempt(wire, **kwargs)
            return previous['response'] if previous else calls.call('execution', wire, metadata=metadata)
        return ExecutionModel(send, history=history, max_output_tokens=100)

    append = EventLog.append
    def before_frame(log, event_type, *args, **kwargs):
        if event_type == 'MODEL_DECISION':
            raise SystemExit('known response, missing frame')
        return append(log, event_type, *args, **kwargs)

    organ = _organ(tmp_path, model())
    try:
        with monkeypatch.context() as patch:
            patch.setattr(EventLog, 'append', before_frame)
            with pytest.raises(SystemExit):
                organ.run_goal('Follow the current condition.', FileContentEquals('answer.txt', '42'),
                               decision_advisory=('decision-000001', old_guidance))
    finally:
        organ.shutdown()
    original_wire = calls.records()[0][1]['wire']
    organ = _organ(tmp_path, model())
    try:
        if pause:
            organ.interrupt()
        if retirement_cut:
            def stop_retirement(log, event_type, *args, **kwargs):
                if event_type == 'DECISION_RETIRED' and retirement_cut == 'before':
                    raise SystemExit('before retirement')
                result = append(log, event_type, *args, **kwargs)
                if event_type == 'DECISION_RETIRED':
                    raise SystemExit('after retirement')
                return result
            with monkeypatch.context() as patch:
                patch.setattr(EventLog, 'append', stop_retirement)
                with pytest.raises(SystemExit):
                    organ.resume(decision_advisory=('decision-000001', new_guidance))
        else:
            assert organ.resume(decision_advisory=('decision-000001', new_guidance)).status == 'running'
        assert calls.summary()['calls'] == 1
    finally:
        organ.shutdown()

    organ = _organ(tmp_path, model())
    try:
        if retirement_cut == 'before':
            # The interrupted call did not return: explicitly retry its guidance
            # against the next decision while finishing the barred old frame.
            assert organ.resume(decision_advisory=('decision-000002', new_guidance)).status == 'running'
        expected_guidance = new_guidance
        if pending_change:
            if pause:
                organ.interrupt()
            if pending_change == 'withdraw':
                organ.resume(decision_advisory=('decision-000002', None))
                expected_guidance = None
            else:
                expected_guidance = 'Wait for NEW and include its supporting evidence.'
                def after_redirect(log, event_type, *args, **kwargs):
                    result = append(log, event_type, *args, **kwargs)
                    if event_type == 'ROOT_REDIRECTED':
                        raise SystemExit('replacement accepted, not yet consumed')
                    return result
                with monkeypatch.context() as patch:
                    patch.setattr(EventLog, 'append', after_redirect)
                    with pytest.raises(SystemExit):
                        organ.resume(decision_advisory=('decision-000002', expected_guidance))
            organ.shutdown()
            organ = _organ(tmp_path, model())
        result = organ.resume()
        assert result.state.waiting_for == 'NEW' and calls.summary()['calls'] == 2
        assert organ.retired_decisions() == ('decision-000001',)
        old_frame, new_frame = result.decision_frames
        assert plain(old_frame.provider_wire_request) == original_wire
        assert new_guidance not in old_frame.actual_request.context
        assert json.loads(new_frame.actual_request.context).get('mind_supervisor_directive') == expected_guidance
        assert sum(e.event_type == 'ROOT_WAITING' for e in result.events) == 1
        assert sum(e.event_type == 'DECISION_RETIRED' for e in result.events) == 1
    finally:
        organ.shutdown()
