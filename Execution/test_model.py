"""Current native dialogue and crash safety, without historical harness imports."""
import json

import httpx
import pytest

from Execution import ExecutionOrgan, FileContentEquals
from Execution.execution import ModelRequest, Wait
from Execution.ipython_control import IPythonResult
from Execution.model import ExecutionHistory, ExecutionModel, correction_wire, execution_goal
from Nervous.provider import BudgetPause, MODEL, ProviderCalls
from Nervous.storage import canonical, fingerprint


def response(name='wait', value=None):
    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': 'tool-1',
        'name': name, 'input': value or {'event_type': 'INPUT'}}],
        'usage': {'input_tokens': 10, 'output_tokens': 5}}


def no_tool():
    return {'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': 'Still considering the request.'}],
        'usage': {'input_tokens': 10, 'output_tokens': 20}}


def wire():
    return {'model': MODEL, 'max_tokens': 100, 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': canonical({'state': {'execution_id': 'run-1', 'decision_count': 0}})}]}]}


def dispatch(calls, request, *, correction_of=None):
    previous, metadata = ExecutionHistory(calls).attempt(request, correction_of)
    return previous['response'] if previous else calls.call('execution', request, metadata=metadata)


@pytest.mark.parametrize('error', [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
@pytest.mark.parametrize('repair', [False, True])
def test_known_pre_send_failure_retains_cost_and_resumes_the_same_attempt(tmp_path, error, repair):
    limits = {'calls': 3, 'output_tokens': 300, 'request_bytes': 100000}
    calls = ProviderCalls(tmp_path, limits, lambda *_: no_tool())
    base = request = wire()
    if repair:
        dispatch(calls, base)
        request = correction_wire(base, no_tool())
    cause = fingerprint(base) if repair else None
    def failed(*_):
        raise error('No connection established.')
    calls.transport = failed
    with pytest.raises(error):
        dispatch(calls, request, correction_of=cause)
    before = {path.name: path.read_bytes() for path in tmp_path.glob('*.json')}
    calls = ProviderCalls(tmp_path, limits, lambda *_: response())
    assert dispatch(calls, request, correction_of=cause) == response()
    assert calls.summary()['calls'] == 2 + int(repair)
    assert calls.summary()['errors'] == 1
    assert all((tmp_path / name).read_bytes() == data for name, data in before.items())


@pytest.mark.parametrize('error', [httpx.ReadTimeout, httpx.WriteTimeout])
def test_unknown_outcome_is_not_redispatched(tmp_path, error):
    def failed(*_):
        raise error('Outcome unknown.')
    limits = {'calls': 4, 'output_tokens': 400, 'request_bytes': 100000}
    calls = ProviderCalls(tmp_path, limits, failed)
    with pytest.raises(error): dispatch(calls, wire())
    calls = ProviderCalls(tmp_path, limits, lambda *_: pytest.fail('Unknown call repeated'))
    with pytest.raises(BudgetPause, match='execution_model_outcome_unknown'):
        dispatch(calls, wire())
    assert calls.summary()['calls'] == 1


def test_one_no_tool_correction_is_durable_and_cannot_refresh_on_restart(tmp_path):
    answers = iter([no_tool(), no_tool()])
    calls = ProviderCalls(tmp_path, {'calls': 2, 'output_tokens': 200, 'request_bytes': 100000}, lambda *_: next(answers))
    request = ModelRequest(canonical({'state': {'execution_id': 'run-1', 'decision_count': 0}}),
                           ExecutionModel.tool_contracts, ())
    model = ExecutionModel(lambda w, **kwargs: dispatch(calls, w, **kwargs), max_output_tokens=100)
    first = model.decide(request)
    assert first.failure == 'model_native:incomplete_response'
    assert calls.summary()['calls'] == 2
    reopened = ProviderCalls(tmp_path, calls.limits, lambda *_: pytest.fail('Correction quota refreshed'))
    again = ExecutionModel(lambda w, **kwargs: dispatch(reopened, w, **kwargs), max_output_tokens=100).decide(request)
    assert again.failure == first.failure
    assert reopened.summary()['calls'] == 2


def test_native_sibling_batch_and_owner_authenticated_rounds_survive_projection(tmp_path):
    class Python:
        def __init__(self): self.actions = []
        def execute(self, code):
            self.actions.append(code)
            return IPythonResult(True, output='observed ' + code)
        def close(self): pass
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    python = Python()
    batch = {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': name,
        'name': 'ipython', 'input': {'code': name}} for name in ('first', 'second')]}
    answers = iter([batch, response()])
    calls = ProviderCalls(tmp_path / 'calls', {'calls': 3, 'output_tokens': 300, 'request_bytes': 100000}, lambda *_: next(answers))
    task = {'business_goal': 'Exercise two native sibling actions.', 'execution_protocol': 'Use the ordinary completion protocol.'}
    model = ExecutionModel(lambda w, **kwargs: dispatch(calls, w, **kwargs), owner_task=task, max_output_tokens=100)
    organ = ExecutionOrgan(workspace=workspace, event_log_path=tmp_path/'actions.jsonl', model=model,
                           max_decisions=2, ipython_control=python, max_context_chars=12000)
    try:
        result = organ.run_goal(execution_goal(task), FileContentEquals('done', 'yes'))
        assert result.status == 'waiting'
        assert python.actions == ['first', 'second']
        rounds = ExecutionHistory(calls).history(2, result.state.execution_id, organ.committed_tool_calls())
        pair = rounds[0]['native_messages']
        assert [b['id'] for b in pair[0]['content']] == ['first', 'second']
        assert [b['tool_use_id'] for b in pair[1]['content']] == ['first', 'second']
        assert ExecutionHistory(calls).history(2, result.state.execution_id, ()) == []
    finally:
        organ.shutdown()


def test_exact_owner_goal_and_original_advice_are_not_replaced_by_the_native_history(tmp_path):
    task = {'business_goal': 'Preserve the full owner requirement. ' * 40, 'execution_protocol': 'Use the normal runtime.'}
    goal = execution_goal(task)
    context = {'state': {'execution_id': 'run-1', 'decision_count': 0},
        'goal': {'text': goal[:200], 'original_chars': len(goal), 'truncated': True},
        'mind_supervisor_directive': 'Only conclude what the observation establishes.'}
    wires = []
    def transport(w, **_):
        wires.append(w)
        return response()
    result = ExecutionModel(transport, owner_task=task).decide(ModelRequest(canonical(context), ExecutionModel.tool_contracts, ()))
    assert result.action == Wait('INPUT')
    visible = json.loads(wires[0]['messages'][0]['content'][0]['text'])
    assert visible['goal'] == {'text': goal, 'original_chars': len(goal), 'truncated': False}
    assert wires[0]['messages'][-1]['content'] == context['mind_supervisor_directive']
