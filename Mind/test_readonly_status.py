"""Status diagnoses the durable owners without acquiring writers or doing work."""
import json
import pytest

from Mind.cli import main
from Mind.test_core_loop import native, start_organs
from Execution.runtime import Execution
from Mind.organ import MindOrgan
from Nervous.organ import NervousOrgan
from Nervous.provider import ProviderCalls


def files_under(path):
    return {p.relative_to(path).as_posix(): (p.read_bytes() if p.name != 'writer.lock' else p.stat().st_size,
                                          p.stat().st_mtime_ns)
            for p in path.rglob('*') if p.is_file()}


def test_status_never_opens_writers_or_records_new_observations(tmp_path, monkeypatch, capsys):
    workspace, directory = tmp_path / 'workspace', tmp_path / 'state'
    workspace.mkdir()
    goal = 'Record the goal without acting.'
    stack, nervous, mind, execution = start_organs(directory, workspace,
        lambda role, wire: native('cognitive_step', {'type': 'cognitive_step', 'updates': [],
                                                    'next': {'type': 'no_change'}}), goal)
    with stack:
        nervous.initialize(goal=goal, workspace=workspace)
        original = nervous.run(mind, execution)
        (workspace / 'later.txt').write_text('Not yet polled.', encoding='utf-8')
        before = files_under(tmp_path)
        def no_writer(*args, **kwargs):
            raise AssertionError('status constructed a writable runtime')
        for cls in (NervousOrgan, Execution, MindOrgan, ProviderCalls):
            monkeypatch.setattr(cls, '__init__', no_writer)
        assert main(['status', '--state', str(directory)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['mind']['revision'] == original['mind']['revision'] == 1
        assert result['execution']['status'] == 'not_started'
        assert result['cost']['calls'] == original['cost']['calls']
        assert result['pending'] == original['pending']
        assert files_under(tmp_path) == before
        trace, = [p for p in (directory / 'mind' / 'cognition').glob('activation-*.jsonl')
                  if not p.name.endswith('.native.jsonl')]
        with trace.open('ab') as stream:
            stream.write(b'{"unfinished":')
        before = files_under(tmp_path)
        assert main(['status', '--state', str(directory)]) == 0
        damaged = json.loads(capsys.readouterr().out)
        assert damaged['mind']['diagnostic']['issue']
        trace_status, = damaged['mind']['traces'].values()
        assert trace_status['issue']['kind'] == 'incomplete_record'
        assert trace_status['valid_records'] > 0
        assert damaged['execution']['status'] == 'not_started'
        assert files_under(tmp_path) == before


def test_status_keeps_valid_execution_prefix_and_reports_other_damaged_owner(tmp_path, capsys):
    from Execution.test_runtime import runtime, native as execution_native, decision
    from Nervous.storage import write_json
    owner, calls, control = runtime(tmp_path, iter([execution_native('wait', {'event_type': 'INPUT'})]))
    directory = owner.directory.parent
    try:
        owner.handle(decision(owner, 'start', 'Wait for evidence.'))
        owner.advance()
        execution_ref = owner.run_state().execution_id
        events = owner.directory / 'runs' / str(owner.state['active_run']) / 'events.jsonl'
    finally:
        owner.close()
    with events.open('ab') as stream:
        stream.write(b'{"partial":')
    write_json(directory / 'nervous' / 'settings.json', {'format': 'nervous-runtime-1',
        'limits': calls.limits, 'initial_input': {'goal': 'Wait for evidence.', 'workspace': str(tmp_path / 'workspace')}})
    (directory / 'mind').mkdir()
    (directory / 'mind' / 'mind.json').write_text('{broken', encoding='utf-8')
    before = files_under(tmp_path)
    assert main(['status', '--state', str(directory)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['execution']['status'] == 'waiting'
    assert result['execution']['execution_ref'] == execution_ref
    assert result['execution']['diagnostic']['valid_records'] > 0
    assert result['execution']['diagnostic']['issue']['kind'] == 'incomplete_record'
    assert result['mind']['diagnostic']['issue']
    assert result['cost']['calls'] == 0
    assert files_under(tmp_path) == before


def test_status_reports_persisted_unknown_kernel_outcome_after_restart(tmp_path, monkeypatch, capsys):
    from Execution.execution import EventLog, _unsettled_action_start
    from Execution.ipython_control import IPythonResult
    from Execution.test_runtime import runtime, native as execution_native, decision
    from Nervous.storage import read_json, write_json

    owner, calls, control = runtime(tmp_path, iter([execution_native('ipython', {'code': 'pass'})]))
    control.execute = lambda _: IPythonResult(False, error_code='isolated_kernel_failed')
    directory = owner.directory.parent
    try:
        owner.handle(decision(owner, 'start', 'Perform the authorized local action.'))
        assert owner.advance()
        assert not owner.advance()
        events, _ = EventLog.inspect_path(owner.directory / 'runs' / str(owner.state['active_run']) / 'events.jsonl')
        assert any(e.event_type == 'IPYTHON_EXECUTION_FAILED' for e in events)
        assert _unsettled_action_start(events) is None
    finally:
        owner.close()
    unknown = read_json(owner.directory / 'run.json')['state']['unknown_action']
    assert unknown['event_ref'] and unknown['reason'] == 'isolated_kernel_failed'
    write_json(directory / 'nervous' / 'settings.json', {'format': 'nervous-runtime-1',
        'limits': calls.limits, 'initial_input': {'goal': 'Perform the authorized local action.',
                                                'workspace': str(tmp_path / 'workspace')}})
    before = files_under(tmp_path)

    def no_writer(*args, **kwargs):
        raise AssertionError('status constructed a writable runtime')

    for cls in (NervousOrgan, Execution, MindOrgan, ProviderCalls):
        monkeypatch.setattr(cls, '__init__', no_writer)
    assert main(['status', '--state', str(directory)]) == 0
    execution = json.loads(capsys.readouterr().out)['execution']
    assert execution['stop_reason'] == 'execution_action_outcome_requires_owner_check'
    assert execution['recovery']['unknown_action'] == unknown
    assert calls.summary()['calls'] == 1
    assert files_under(tmp_path) == before


@pytest.mark.parametrize('damaged', ['settings', 'provider'])
def test_status_isolates_valid_json_with_invalid_record_shape(tmp_path, capsys, damaged):
    from Nervous.storage import write_json
    directory = tmp_path / 'state'
    settings = directory / 'nervous' / 'settings.json'
    write_json(settings, {'format': 'nervous-runtime-1', 'limits': {'calls': 1}})
    write_json(settings if damaged == 'settings' else directory / 'nervous' / 'calls' / '0001.json', [])
    before = files_under(tmp_path)
    assert main(['status', '--state', str(directory)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['settings_diagnostic']['issue'] if damaged == 'settings' else result['cost']['diagnostic']['issues']
    assert result['execution']['status'] == 'not_initialized'
    assert files_under(tmp_path) == before


@pytest.mark.parametrize('response', [[], None, {'usage': []}],
                         ids=['response-array', 'response-null', 'usage-array'])
def test_status_keeps_valid_cost_and_pending_calls_around_invalid_response(tmp_path, capsys, response):
    from Nervous.provider import CALL_FORMAT
    from Nervous.storage import write_json
    directory = tmp_path / 'state'
    calls = directory / 'nervous' / 'calls'
    write_json(directory / 'nervous' / 'settings.json',
               {'format': 'nervous-runtime-1', 'limits': {'calls': 3}})
    received = {'format': CALL_FORMAT, 'role': 'mind', 'wire': {'max_tokens': 10},
                'request_bytes': 20, 'status': 'received', 'response': {'usage': {
                    'input_tokens': 3, 'output_tokens': 4, 'cache_read_input_tokens': 5,
                    'cache_creation_input_tokens': 6}}}
    write_json(calls / '0001.json', received)
    write_json(calls / '0002.json', {**received, 'response': response})
    write_json(calls / '0003.json', {'format': CALL_FORMAT, 'role': 'builder',
        'wire': {'max_tokens': 30}, 'request_bytes': 60,
        'status': 'reserved', 'operation': 'pending-call'})
    before = files_under(tmp_path)

    assert main(['status', '--state', str(directory)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['cost'] == {
        'calls': 2, 'allocated_output_tokens': 40, 'request_bytes': 80,
        'usage': {'input_tokens': 3, 'output_tokens': 4, 'cache_read_input_tokens': 5,
                  'cache_creation_input_tokens': 6},
        'errors': 1,
        'unresolved': [{'role': 'builder', 'operation': 'pending-call',
                        'purpose': 'decision', 'status': 'reserved'}],
        'diagnostic': {'issues': [{'record': 2, 'kind': 'ValueError'}], 'complete': False}}
    assert result['execution']['status'] == 'not_initialized'
    assert files_under(tmp_path) == before
