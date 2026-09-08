"""Connection setup failures retain their cost while a normal resume may dispatch again."""
import httpx
import pytest

from Mind.chain import read_json, write_json
from Mind.execution_checkpoint_fixture import execution_checkpoint
from Mind.test_chain import LocalTestPython, no_tool_response, reply


@pytest.mark.parametrize('failure', [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
@pytest.mark.parametrize('failed_attempt', [1, 2])
def test_pre_send_failure_can_resume_first_or_correction_attempt_without_refunding_cost(tmp_path, failure, failed_attempt):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    directory = tmp_path / 'session'
    wires = []

    def transport(role, wire):
        assert role == 'execution'
        wires.append(wire)
        if len(wires) == failed_attempt:
            raise failure('Connection setup did not complete.')
        if len(wires) < 3:
            return no_tool_response()
        assert wire['messages'][-2]['content'] == no_tool_response()['content']
        return reply('wait', {'event_type': 'DONE'})

    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with execution_checkpoint(workspace=workspace, goal='Wait for an observed input.',
                              limits={'calls': 3, 'output_tokens': 3 * 8192, 'request_bytes': 450000}, **options) as session:
        with pytest.raises(failure):
            session.run()
        assert len(wires) == failed_attempt and session.execution.state.decision_count == 0
        cost = session.calls.summary()
        assert cost['calls'] == failed_attempt and cost['errors'] == 1
        assert cost['allocated_output_tokens'] == failed_attempt * 8192
    before = {path.name: path.read_bytes() for path in (directory / 'calls').glob('*.json')}
    failed = read_json(directory / 'calls' / f'{failed_attempt:04d}.json')
    assert failed['status'] == 'failed' and failed['error'] == failure.__name__ and 'response' not in failed
    with execution_checkpoint(**options) as session:
        result = session.run()
        assert result['execution_status'] == 'waiting' and session.execution.state.decision_count == 1
        cost = session.calls.summary()
        assert cost['calls'] == 3 and cost['errors'] == 1
        assert cost['allocated_output_tokens'] == 3 * 8192
        assert cost['request_bytes'] == sum(read_json(path)['request_bytes'] for path in (directory / 'calls').glob('*.json'))
        assert cost['usage']['output_tokens'] == 8192 + 30
    assert len(wires) == 3
    assert all((directory / 'calls' / name).read_bytes() == value for name, value in before.items())
    final = read_json(directory / 'calls/0003.json')
    assert final['execution_attempt']['repair'] is True
    with execution_checkpoint(directory, transport=lambda *_: pytest.fail('A committed decision was called again.'),
                              ipython=LocalTestPython(workspace)) as session:
        assert session.run()['cost']['calls'] == 3


@pytest.mark.parametrize('failure', ['ReadTimeout', 'WriteTimeout', 'ReadError', 'WriteError', 'HTTPStatusError',
                                    'reserved', 'with_response', 'provider_rejection'])
@pytest.mark.parametrize('failed_attempt', [1, 2])
def test_unknown_or_contradictory_attempt_remains_blocked_after_restart(tmp_path, failure, failed_attempt):
    class ProcessLoss(BaseException):
        pass

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    directory = tmp_path / 'session'
    seen = []
    if failure == 'reserved':
        error = ProcessLoss()
    elif failure == 'HTTPStatusError':
        request = httpx.Request('POST', 'https://api.deepseek.com/anthropic/v1/messages')
        error = httpx.HTTPStatusError('Provider rejected the request.', request=request,
                                      response=httpx.Response(503, request=request, text='Unavailable'))
    else:
        error = getattr(httpx, failure if failure not in {'with_response', 'provider_rejection'} else 'ConnectError')('Unknown outcome.')

    def transport(_, wire):
        seen.append(wire)
        if len(seen) == failed_attempt:
            raise error
        return no_tool_response()

    with execution_checkpoint(directory, workspace=workspace, goal='Wait for input.', transport=transport,
                              ipython=LocalTestPython(workspace)) as session:
        with pytest.raises(type(error)):
            session.run()
        assert session.execution.state.decision_count == 0 and len(seen) == failed_attempt
    if failure in {'with_response', 'provider_rejection'}:
        path = directory / 'calls' / f'{failed_attempt:04d}.json'
        record = read_json(path)
        if failure == 'with_response':
            record['response'] = reply('wait', {'event_type': 'DONE'})
        else:
            record['provider_rejection'] = {'status_code': 503, 'body': 'Unavailable'}
        write_json(path, record)
    before = {path.name: path.read_bytes() for path in (directory / 'calls').glob('*.json')}
    with execution_checkpoint(directory, transport=lambda *_: pytest.fail('Unknown attempt was retried.'),
                              ipython=LocalTestPython(workspace)) as session:
        result = session.run()
        assert result['stop_reason'] == 'execution_model_outcome_unknown'
        assert result['cost']['calls'] == failed_attempt and session.execution.state.decision_count == 0
    assert before == {path.name: path.read_bytes() for path in (directory / 'calls').glob('*.json')}


@pytest.mark.parametrize('failed_attempt', [1, 2])
def test_pre_send_failure_does_not_bypass_the_existing_session_budget(tmp_path, failed_attempt):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    directory = tmp_path / 'session'
    seen = []
    def transport(_, wire):
        seen.append(wire)
        if len(seen) == failed_attempt:
            raise httpx.ConnectError('Connection unavailable.')
        return no_tool_response()
    with execution_checkpoint(directory, workspace=workspace, goal='Wait for input.', transport=transport,
                              limits={'calls': failed_attempt, 'output_tokens': failed_attempt * 8192,
                                      'request_bytes': 300000}, ipython=LocalTestPython(workspace)) as session:
        with pytest.raises(httpx.ConnectError):
            session.run()
    before = {path.name: path.read_bytes() for path in (directory / 'calls').glob('*.json')}
    with execution_checkpoint(directory, transport=lambda *_: pytest.fail('Budget was refunded.'),
                              ipython=LocalTestPython(workspace)) as session:
        result = session.run()
        assert result['stop_reason'] == 'chain_budget_exhausted'
        assert result['cost']['calls'] == failed_attempt and result['cost']['errors'] == 1
    assert before == {path.name: path.read_bytes() for path in (directory / 'calls').glob('*.json')}
