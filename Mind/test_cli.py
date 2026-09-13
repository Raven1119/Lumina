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
@pytest.mark.parametrize('mode', ['baseline', 'summary'])
def test_start_cuts_resume_from_original_input_without_resubmission(tmp_path, monkeypatch, capsys, cut, mode):
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
                      '--context-mode', mode, '--goal', 'Record the requested understanding; no action is required.'])
    assert calls == []
    assert cli.main(['status', '--state', str(state)]) == 0
    assert json.loads(capsys.readouterr().out)['initialization'] == 'pending'
    assert cli.main(['resume', '--state', str(state)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mind']['revision'] == 1 and result['execution']['status'] == 'not_started'
    for organ, filename in [('mind', 'mind.json'), ('execution', 'run.json')]:
        assert json.loads((state / organ / filename).read_text(encoding='utf-8'))['state']['context_mode'] == mode
    assert not any(result['pending'].values())
    assert cli.main(['resume', '--state', str(state)]) == 0
    assert cli.main(['status', '--state', str(state)]) == 0
    assert calls == ['mind']


def test_explicit_context_retry_keeps_failed_attempt_and_resumes_same_activity(tmp_path, monkeypatch, capsys):
    workspace, state = tmp_path / 'work', tmp_path / 'private'
    workspace.mkdir()
    wires, summaries = [], []
    original_provider = ProviderCalls.__init__
    def provider(self, directory, limits, transport=None):
        def scripted(role, wire):
            assert role == 'mind'
            wires.append(wire)
            if wire.get('tools'):
                return native_nochange()
            summaries.append(wire)
            if len(summaries) == 1:
                return {'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '{"summary":"partial'}]}
            payload = json.loads(wire['messages'][0]['content'])
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({
                'summary': 'The earlier events required no action; preserve their scope.',
                'source_refs': [payload['segments'][0]['ref']]})}]}
        original_provider(self, directory, limits, scripted)
    monkeypatch.setattr(ProviderCalls, '__init__', provider)
    assert cli.main(['start', '--state', str(state), '--workspace', str(workspace),
        '--goal', 'Review the supplied observations without taking action.', '--context-mode', 'summary',
        '--max-calls', '40', '--max-output-tokens', '1000000']) == 0
    capsys.readouterr()
    for i in range(12):
        assert cli.main(['resume', '--state', str(state), '--message', f'Observation {i}.']) == 0
        before = json.loads(capsys.readouterr().out)
    assert before['stop_reason'] == 'working_context_summary_incomplete'
    assert before['mind']['revision'] == 12 and len(summaries) == 1
    failed = state / 'nervous' / 'calls' / '0013.json'
    failed_bytes = failed.read_bytes()
    assert cli.main(['resume', '--state', str(state)]) == 0
    unchanged = json.loads(capsys.readouterr().out)
    assert unchanged['cost']['calls'] == before['cost']['calls']
    assert cli.main(['resume', '--state', str(state), '--retry-context']) == 0
    after = json.loads(capsys.readouterr().out)
    assert after['mind']['revision'] == 13 and after['mind']['active'] is None
    assert after['cost']['calls'] == before['cost']['calls'] + 2
    assert len(summaries) == 2 and summaries[1]['max_tokens'] == 8192
    assert failed.read_bytes() == failed_bytes
    assert list((state / 'mind').glob('background.failed-*.json'))
    assert not any(after['pending'].values())
    assert cli.main(['resume', '--state', str(state), '--retry-context']) == 0
    quiet = json.loads(capsys.readouterr().out)
    assert quiet['cost']['calls'] == after['cost']['calls']


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


@pytest.mark.parametrize('mode', [None, 'off', 'execution', 'mind'])
def test_repetition_launch_mode_is_opt_in_and_immutable_after_restart(tmp_path, mode):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    launch = {'goal': 'Inspect the supplied material.', 'workspace': workspace}
    expected = {'goal': launch['goal'], 'workspace': str(workspace)}
    if mode not in (None, 'off'):
        expected['repetition_mode'] = mode
    with NervousOrgan(tmp_path / 'nervous') as nervous:
        assert nervous.initialize(**launch, repetition_mode=mode) == expected
        original_event, = nervous.pending('mind')
    with NervousOrgan(tmp_path / 'nervous') as nervous:
        assert nervous.initialize() == expected
        assert nervous.initialize(**launch) == expected
        assert nervous.pending('mind') == (original_event,)
        changed = 'off' if mode in ('execution', 'mind') else 'execution'
        with pytest.raises(ValueError, match='initial_input_identity_conflict'):
            nervous.initialize(**launch, repetition_mode=changed)
        assert nervous.initialize() == expected


def test_invalid_repetition_mode_does_not_persist_launch_or_publish_input(tmp_path):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    with NervousOrgan(tmp_path / 'nervous') as nervous:
        with pytest.raises(ValueError, match='unsupported_repetition_mode'):
            nervous.initialize(goal='Inspect the supplied material.', workspace=workspace,
                               repetition_mode='automatic')
        assert 'initial_input' not in nervous.settings
        assert nervous.pending('mind') == ()


@pytest.mark.parametrize('mode', [None, 'off', 'execution', 'mind'])
def test_cli_retains_repetition_mode_before_execution_construction(tmp_path, monkeypatch, mode):
    workspace, state = tmp_path / 'work', tmp_path / 'private'
    workspace.mkdir()
    received = []

    class ConstructionCut(BaseException):
        pass

    def execution(*args, **kwargs):
        received.append(kwargs['repetition_mode'])
        raise ConstructionCut()

    monkeypatch.setattr(cli, 'Execution', execution)
    args = ['start', '--state', str(state), '--workspace', str(workspace),
            '--goal', 'Inspect the supplied material.']
    if mode is not None:
        args.extend(['--repetition-mode', mode])
    with pytest.raises(ConstructionCut):
        cli.main(args)
    with pytest.raises(ConstructionCut):
        cli.main(['resume', '--state', str(state)])
    assert received == [mode or 'off', mode or 'off']
    for action in ('resume', 'status'):
        with pytest.raises(SystemExit) as stopped:
            cli.main([action, '--state', str(state), '--repetition-mode', mode or 'off'])
        assert stopped.value.code == 2
    assert len(received) == 2


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


@pytest.mark.parametrize('identified', [False, True])
@pytest.mark.parametrize('input_args', [['--message', 'Continue.'], ['--event', 'INPUT_READY', '--data', 'Continue.']])
def test_repeated_cli_text_reaches_mind_but_submission_retry_does_not(tmp_path, monkeypatch, capsys, identified, input_args):
    workspace, state = tmp_path / 'work', tmp_path / 'private'
    workspace.mkdir()
    wires = []
    original_provider = ProviderCalls.__init__
    def provider(self, directory, limits, transport=None):
        def scripted(role, wire):
            assert role == 'mind'
            wires.append(wire)
            return native_nochange()
        original_provider(self, directory, limits, scripted)
    monkeypatch.setattr(ProviderCalls, '__init__', provider)
    assert cli.main(['start', '--state', str(state), '--workspace', str(workspace),
                     '--goal', 'Review the current observation without workspace actions.']) == 0
    capsys.readouterr()
    resume = ['resume', '--state', str(state), *input_args]
    first_key = ['--submission-id', 'click-1'] if identified else []
    second_key = ['--submission-id', 'click-2'] if identified else []
    assert cli.main(resume + first_key) == 0
    assert json.loads(capsys.readouterr().out)['mind']['revision'] == 2
    (workspace / 'observation.txt').write_text('Observed revision 2.', encoding='utf-8')
    if identified:
        assert cli.main(resume + first_key) == 0
        assert json.loads(capsys.readouterr().out)['mind']['revision'] == 2
        assert len(wires) == 2
    assert cli.main(resume + second_key) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mind']['revision'] == 3 and not any(result['pending'].values())
    assert 'Observed revision 2.' in json.dumps(wires[-1])
    assert cli.main(['resume', '--state', str(state)]) == 0
    assert len(wires) == 3
