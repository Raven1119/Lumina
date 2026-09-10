"""Cooperative stop admits no new work and preserves already received outcomes."""
import json
import signal

import pytest

from Nervous.provider import BudgetPause, MODEL, ProviderCalls


def test_provider_pause_is_before_reservation_but_known_response_is_claimable(tmp_path):
    wire = {'model': MODEL, 'max_tokens': 10, 'messages': []}
    calls = ProviderCalls(tmp_path, {'calls': 2, 'output_tokens': 20, 'request_bytes': 10000},
                          lambda *_: {'content': []})
    known = calls.call('mind', wire, operation='first')
    calls.request_pause()
    with pytest.raises(BudgetPause, match='user_pause_requested'):
        calls.call('mind', wire, operation='second')
    assert calls.summary()['calls'] == 1
    assert calls.call('mind', wire, operation='first') == known


def test_mind_local_call_without_provider_reservation_can_resume_original_wire(tmp_path, monkeypatch):
    from Mind.test_model import begin, commit
    from Mind.model import MindModel
    from Mind.trace import MindTrace
    from Mind.activity import resume_native_activity
    from Mind.contracts import NoChange
    calls = ProviderCalls(tmp_path / 'calls', {'calls': 1, 'output_tokens': 16384, 'request_bytes': 240000},
                          lambda *_: commit())
    append = MindTrace.append_native
    def pause_after_local_call(trace, **value):
        append(trace, **value)
        if value['kind'] == 'call':
            calls.request_pause()
    with monkeypatch.context() as patch:
        patch.setattr(MindTrace, 'append_native', pause_after_local_call)
        with pytest.raises(BudgetPause):
            begin(tmp_path, MindModel(calls))
    assert calls.summary()['calls'] == 0
    trace = MindTrace.reopen_for_native(tmp_path / 'activity.jsonl', allow_pending=True)
    original = trace.native_records()[0]['wire']
    def transport(role, wire):
        assert wire == original
        return commit()
    resumed = ProviderCalls(calls.directory, calls.limits, transport)
    assert isinstance(resume_native_activity(trace, MindModel(resumed)), NoChange)
    assert resumed.summary()['calls'] == 1
    assert len(trace.native_records()) == 2


def test_cli_sigint_saves_inflight_mind_result_then_quiet_resume(tmp_path, monkeypatch, capsys):
    from Mind import cli
    from Mind.test_cli import native_nochange
    from Nervous.storage import read_json
    workspace, state = tmp_path / 'work', tmp_path / 'state'
    workspace.mkdir()
    original = ProviderCalls.__init__
    calls = []
    old_handler = signal.getsignal(signal.SIGINT)
    def provider(self, directory, limits, transport=None):
        def respond(role, wire):
            calls.append(role)
            signal.raise_signal(signal.SIGINT)
            return native_nochange()
        original(self, directory, limits, respond)
    monkeypatch.setattr(ProviderCalls, '__init__', provider)
    assert cli.main(['start', '--state', str(state), '--workspace', str(workspace), '--goal', 'Understand only.']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['stop_reason'] == 'user_pause_requested'
    assert result['mind']['revision'] == 1
    assert result['pending']['execution'] and result['execution']['status'] == 'not_started'
    assert calls == ['mind'] and signal.getsignal(signal.SIGINT) == old_handler
    assert read_json(state / 'nervous' / 'settings.json')['foreground_stop_reason'] == 'user_pause_requested'
    assert cli.main(['status', '--state', str(state)]) == 0
    assert json.loads(capsys.readouterr().out)['stop_reason'] == 'user_pause_requested'
    assert cli.main(['resume', '--state', str(state)]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert not any(resumed['pending'].values()) and calls == ['mind']
    assert resumed['mind']['revision'] == 1 and resumed['stop_reason'] is None


@pytest.mark.parametrize('saved_status', ['received', 'reserved'])
def test_legacy_pending_mind_call_without_operation_is_not_resampled(tmp_path, monkeypatch, saved_status):
    from Mind.test_model import begin, commit
    from Mind.model import MindModel
    from Mind.trace import MindTrace
    from Mind.activity import resume_native_activity
    from Mind.contracts import ActivationFailure

    def legacy_transport(*_):
        if saved_status == 'reserved':
            raise SystemExit('legacy provider outcome unknown')
        return commit()

    calls = ProviderCalls(tmp_path / 'calls',
        {'calls': 2, 'output_tokens': 32768, 'request_bytes': 480000}, legacy_transport)
    append = MindTrace.append_native

    def stop_before_local_result(trace, **value):
        if value['kind'] == 'result':
            raise SystemExit('legacy local result not saved')
        return append(trace, **value)

    with monkeypatch.context() as patch:
        patch.setattr(MindTrace, 'append_native', stop_before_local_result)
        with pytest.raises(SystemExit):
            # The baseline MindOrgan dispatched through this exact callable seam.
            begin(tmp_path, MindModel(lambda wire: calls.call('mind', wire)))
    path, original = calls.records()[0]
    assert original['status'] == saved_status and 'operation' not in original
    original_bytes = path.read_bytes()
    trace = MindTrace.reopen_for_native(tmp_path / 'activity.jsonl', allow_pending=True)
    assert len(trace.native_records()) == 1
    resumed = ProviderCalls(calls.directory, calls.limits, lambda *_: commit())
    try:
        result = resume_native_activity(trace, MindModel(resumed))
    except BudgetPause:
        result = None
    assert resumed.summary()['calls'] == 1
    assert path.read_bytes() == original_bytes
    assert result is None or isinstance(result, ActivationFailure)
