"""Current native dialogue and crash safety, without historical harness imports."""
import json

import httpx
import pytest

from Execution import ExecutionOrgan, FileContentEquals
from Execution.execution import ModelRequest, Wait, _encode_value
from Execution.ipython_control import IPythonResult
from Execution.model import ExecutionHistory, ExecutionModel, correction_wire, execution_goal
from Nervous.provider import BudgetPause, MODEL, ProviderCalls
from Nervous.storage import canonical, fingerprint, write_json


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


@pytest.mark.parametrize('thinking', [False, True])
def test_received_tool_response_recovers_frozen_wire_before_owner_decision(tmp_path, thinking):
    answer = response()
    if thinking:
        answer['content'].insert(0, {'type': 'thinking', 'thinking': 'private', 'signature': 'original'})
    calls = ProviderCalls(tmp_path, {'calls': 1, 'output_tokens': 100, 'request_bytes': 100000},
                          lambda *_: answer)
    request = ModelRequest(canonical({'state': {'execution_id': 'run-1', 'decision_count': 0}}),
                           ExecutionModel.tool_contracts, ())
    history = ExecutionHistory(calls)
    def send(wire, **kwargs):
        previous, metadata = history.attempt(wire, **kwargs)
        return previous['response'] if previous else calls.call('execution', wire, metadata=metadata)
    original = ExecutionModel(send, history=history,
                              max_output_tokens=100, thinking=thinking).decide(request)
    before = calls.summary()
    calls = ProviderCalls(tmp_path, calls.limits, lambda *_: pytest.fail('Known decision was resampled'))
    # A changed adapter prompt must not be paired with yesterday's response.
    recovered = ExecutionModel(lambda w, **kw: dispatch(calls, w, **kw),
        history=ExecutionHistory(calls), role_prompt='New role wording after restart.', max_output_tokens=100).decide(request)
    assert recovered.action == Wait('INPUT')
    assert recovered.provider_wire_request == original.provider_wire_request
    assert recovered.raw_provider_response == original.raw_provider_response
    assert recovered.provider_tool_call_id == original.provider_tool_call_id
    assert calls.summary() == before


@pytest.mark.parametrize('valid_hash', [False, True])
def test_recovery_rejects_corrupt_or_cross_run_owner_request(tmp_path, valid_hash):
    calls = ProviderCalls(tmp_path, {'calls': 2, 'output_tokens': 200, 'request_bytes': 100000},
                          lambda *_: response())
    history = ExecutionHistory(calls)
    def send(w, **kwargs):
        _, metadata = history.attempt(w, **kwargs)
        return calls.call('execution', w, metadata=metadata)
    request = ModelRequest(canonical({'state': {'execution_id': 'run-1', 'decision_count': 0}}),
                           ExecutionModel.tool_contracts, ())
    model = ExecutionModel(send, history=history, max_output_tokens=100)
    model.decide(request)
    path, record = calls.records()[0]
    replacement = ModelRequest(canonical({'state': {'execution_id': 'other-run', 'decision_count': 55}}),
                               request.available_tools, ())
    record['metadata']['owner_request'] = _encode_value(replacement)
    if valid_hash:
        record['metadata']['owner_request_sha256'] = fingerprint(record['metadata']['owner_request'])
    write_json(path, record)
    with pytest.raises(ValueError, match='execution_owner_request_(integrity|identity)_'):
        model.decide(request)
    assert calls.summary()['calls'] == 1


def test_legacy_known_no_action_keeps_its_single_correction_and_recovery(tmp_path):
    calls = ProviderCalls(tmp_path, {'calls': 2, 'output_tokens': 200, 'request_bytes': 100000},
                          lambda *_: no_tool())
    request = ModelRequest(canonical({'state': {'execution_id': 'run-1', 'decision_count': 0}}),
                           ExecutionModel.tool_contracts, ())
    def old_send(w, *, correction_of=None):
        if correction_of is not None:
            raise BudgetPause('correction not dispatched')
        return dispatch(calls, w)
    with pytest.raises(BudgetPause):
        ExecutionModel(old_send, max_output_tokens=100).decide(request)
    calls.transport = lambda *_: response()
    history = ExecutionHistory(calls)
    def send(w, **kwargs):
        previous, metadata = history.attempt(w, **kwargs)
        return previous['response'] if previous else calls.call('execution', w, metadata=metadata)
    result = ExecutionModel(send, history=history, max_output_tokens=100).decide(request)
    assert result.action == Wait('INPUT')
    assert calls.summary()['calls'] == 2
    calls.transport = lambda *_: pytest.fail('Saved correction was resampled')
    recovered = ExecutionModel(send, history=ExecutionHistory(calls), max_output_tokens=100).decide(request)
    assert recovered.raw_provider_response == result.raw_provider_response
    assert calls.summary()['calls'] == 2


def test_pending_no_tool_correction_keeps_its_frozen_request_when_current_projection_changes(tmp_path):
    calls = ProviderCalls(tmp_path, {'calls': 2, 'output_tokens': 200, 'request_bytes': 100000},
                          lambda *_: no_tool())
    request = ModelRequest(canonical({'state': {'execution_id': 'run-1', 'decision_count': 0}}),
                           ExecutionModel.tool_contracts, ())
    original_history = ExecutionHistory(calls)
    def first_send(wire, *, correction_of=None):
        if correction_of is not None:
            raise BudgetPause('correction not dispatched')
        previous, metadata = original_history.attempt(wire)
        assert previous is None
        return calls.call('execution', wire, metadata=metadata)
    with pytest.raises(BudgetPause, match='correction not dispatched'):
        ExecutionModel(first_send, history=original_history, max_output_tokens=100).decide(request)
    path, saved = calls.records()[0]
    original_bytes = path.read_bytes()
    current_history = ExecutionHistory(calls)
    calls.transport = lambda *_: response()
    def resumed_send(wire, **kwargs):
        previous, metadata = current_history.attempt(wire, **kwargs)
        return previous['response'] if previous else calls.call('execution', wire, metadata=metadata)
    result = ExecutionModel(resumed_send, history=current_history, max_output_tokens=100,
        role_prompt='Updated completion lifecycle explanation.',
        execution_context=lambda *a, **k: pytest.fail('Frozen correction consulted a fresh projection')).decide(request)
    assert result.action == Wait('INPUT')
    assert result.provider_wire_request == correction_wire(saved['wire'], saved['response'])
    assert path.read_bytes() == original_bytes and calls.summary()['calls'] == 2


def test_fully_legacy_correction_chain_recovers_only_its_exact_request(tmp_path):
    answers = iter([no_tool(), response()])
    calls = ProviderCalls(tmp_path, {'calls': 2, 'output_tokens': 200, 'request_bytes': 100000},
                          lambda *_: next(answers))
    request = ModelRequest(canonical({'state': {'execution_id': 'run-1', 'decision_count': 0}}),
                           ExecutionModel.tool_contracts, ())
    original = ExecutionModel(lambda w, **kw: dispatch(calls, w, **kw), max_output_tokens=100).decide(request)
    before = calls.summary()
    def no_dispatch(*_, **__):
        pytest.fail('Saved legacy correction was resampled')
    restored = ExecutionModel(no_dispatch, history=ExecutionHistory(calls), max_output_tokens=100).decide(request)
    assert restored.raw_provider_response == original.raw_provider_response
    assert restored.provider_wire_request == original.provider_wire_request
    assert calls.summary() == before
    with pytest.raises(BudgetPause, match='execution_owner_request_unavailable'):
        ExecutionModel(no_dispatch, history=ExecutionHistory(calls), role_prompt='Changed role',
                       max_output_tokens=100).decide(request)


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
    assert wires[0]['messages'][-2]['content'] == context['mind_supervisor_directive']
    assert json.loads(wires[0]['messages'][-1]['content'])['state'] == context['state']


@pytest.mark.parametrize('new_advice', [None, 'Preserve the observed scope when concluding.'])
def test_current_checkpoint_follows_native_history_and_prior_guidance(new_advice):
    task = {'business_goal': 'Finish the authorized report.', 'execution_protocol': 'Use the ordinary completion protocol.'}
    old_advice = {'directive_id': 'direction-1', 'decision_id': 'decision-1',
                 'text': 'Reassess the report against the new evidence.', 'owner_sequence': 1}
    native_pair = [
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'claim-1',
                                         'name': 'claim_complete', 'input': {}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'claim-1',
                                    'content': canonical({'status': 'review_pending'})}]}]
    goal = execution_goal(task)
    context = {'goal': {'text': goal, 'original_chars': len(goal), 'truncated': False},
        'state': {'execution_id': 'run-1', 'decision_count': 3, 'version': 12, 'status': 'running'},
        'observation': {'report.txt': 'Corrections are present.'}, 'incoming_event': None,
        'lifecycle': {'completion': None}}
    feedback = {'completion_review_required': False,
        'completion': {'run_status': 'running', 'deferred_claim_at_current_boundary': False}}
    owner_inputs = [{'sequence': 2, 'text': 'The correction was applied; conclude within the authorized scope.'}]
    projection = {'version': 'execution-context-1', 'mode': 'baseline',
        'rounds': [{'decision': 1, 'native_messages': native_pair}],
        'received_guidance': [old_advice], 'guidance_scope': 'Previously delivered advice, not a new delivery.',
        'owner_inputs': owner_inputs, 'cognitive_feedback': feedback}
    if new_advice is not None:
        context['mind_supervisor_directive'] = new_advice
    result = ExecutionModel(lambda *_: response(), owner_task=task,
        execution_context=lambda _: projection).decide(ModelRequest(canonical(context), ExecutionModel.tool_contracts, ()))
    messages = result.provider_wire_request['messages']
    initial = json.loads(messages[0]['content'][0]['text'])
    assert initial['received_guidance'] == [old_advice]
    assert initial['guidance_scope'] == projection['guidance_scope']
    assert initial['owner_inputs'] == owner_inputs
    assert messages[1:3] == native_pair
    checkpoint = json.loads(messages[-1]['content'])
    assert checkpoint == {**{key: context[key] for key in ('state', 'observation', 'incoming_event', 'lifecycle')},
                          'cognitive_feedback': feedback}
    assert len(messages) == 4 + int(new_advice is not None)
    if new_advice is not None:
        assert messages[-2] == {'role': 'user', 'content': new_advice}
