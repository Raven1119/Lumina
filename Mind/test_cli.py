"""Normal CLI input and interruption recovery, using native injected responses."""
import json
import pytest
from Mind import cli
from Nervous.organ import NervousOrgan
from Nervous.provider import ProviderCalls


def native_nochange():
    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': 'commit',
        'name': 'cognitive_step', 'input': {'type': 'cognitive_step', 'updates': [],
                                         'next': {'type': 'no_change'}}}]}


@pytest.mark.parametrize('cut', ['initial_event', 'execution_construct', 'mind_construct'])
def test_start_cuts_resume_from_original_input_without_resubmission(tmp_path, monkeypatch, capsys, cut):
    workspace, state = tmp_path / 'work', tmp_path / 'private'
    workspace.mkdir()
    calls = []
    original_provider = ProviderCalls.__init__
    def provider(self, directory, limits, transport=None):
        def scripted(role, wire):
            calls.append(role)
            return native_nochange()
        original_provider(self, directory, limits, scripted)
    monkeypatch.setattr(ProviderCalls, '__init__', provider)
    class Crash(BaseException):
        pass
    with monkeypatch.context() as patch:
        if cut == 'initial_event':
            patch.setattr(NervousOrgan, 'publish', lambda *args: (_ for _ in ()).throw(Crash()))
        elif cut == 'execution_construct':
            patch.setattr(cli, 'Execution', lambda *args, **kwargs: (_ for _ in ()).throw(Crash()))
        else:
            patch.setattr(cli, 'MindOrgan', lambda *args, **kwargs: (_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):
            cli.main(['start', '--state', str(state), '--workspace', str(workspace),
                      '--goal', 'Record the requested understanding; no action is required.'])
    assert calls == []
    assert cli.main(['status', '--state', str(state)]) == 0
    assert json.loads(capsys.readouterr().out)['initialization'] == 'pending'
    assert cli.main(['resume', '--state', str(state)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mind']['revision'] == 1 and result['execution']['status'] == 'not_started'
    assert not any(result['pending'].values())
    assert cli.main(['resume', '--state', str(state)]) == 0
    assert cli.main(['status', '--state', str(state)]) == 0
    assert calls == ['mind']


def test_original_launch_and_budget_are_not_replaced_on_reopen(tmp_path):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    with NervousOrgan(tmp_path / 'nervous', limits={
            'calls': 4, 'output_tokens': 65536, 'request_bytes': 1000000}) as nervous:
        assert nervous.initialize(goal='Understand the supplied material.', workspace=workspace)['workspace'] == str(workspace)
        assert len(nervous.pending('mind')) == 1
        with pytest.raises(ValueError, match='initial_input_identity_conflict'):
            nervous.initialize(goal='A different task.', workspace=workspace)
        nervous.extend_budget(2, output_tokens=32768, request_bytes=500000)
    with NervousOrgan(tmp_path / 'nervous', limits={
            'calls': 100, 'output_tokens': 1000000, 'request_bytes': 10000000}) as nervous:
        assert nervous.initialize()['goal'] == 'Understand the supplied material.'
        assert len(nervous.pending('mind')) == 1
        assert nervous.calls.limits['calls'] == 6


def test_explicit_cli_retry_retains_failure_and_closes_obligation(tmp_path, monkeypatch, capsys):
    workspace, state = tmp_path / 'work', tmp_path / 'private'
    workspace.mkdir()
    calls = []
    original_provider = ProviderCalls.__init__
    def provider(self, directory, limits, transport=None):
        def scripted(role, wire):
            calls.append(role)
            return ({'stop_reason': 'end_turn', 'content': []} if len(calls) == 1
                    else native_nochange())
        original_provider(self, directory, limits, scripted)
    monkeypatch.setattr(ProviderCalls, '__init__', provider)
    assert cli.main(['start', '--state', str(state), '--workspace', str(workspace),
                     '--goal', 'Understand the supplied request without taking action.']) == 0
    before = json.loads(capsys.readouterr().out)
    assert before['mind']['unresolved']
    assert cli.main(['resume', '--state', str(state), '--retry-review']) == 0
    after = json.loads(capsys.readouterr().out)
    assert not after['mind']['unresolved'] and after['mind']['revision'] == 1
    saved = json.loads((state / 'mind' / 'mind.json').read_text(encoding='utf-8'))['state']
    assert len(saved['activities']) == 2
    assert any(activity.get('retry_of') for activity in saved['activities'].values())
    assert cli.main(['resume', '--state', str(state)]) == 0
    assert calls == ['mind', 'mind']
