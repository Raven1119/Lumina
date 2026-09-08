"""Functional boundary tests; scripted provider calls prove mechanism, not cognition."""
import json
from collections import Counter
from pathlib import Path

import pytest

from Execution.ipython_control import IPythonResult
from Mind.chain import Session, Calls, Builder, BudgetPause, workspace_path, read_json, write_json
from Mind.execution_checkpoint_fixture import execution_checkpoint
from Mind import world_model as wm
from Mind.task_view import fingerprint


def reply(name, value):
    return {'content': [{'type': 'tool_use', 'id': 'call-1', 'name': name, 'input': value}],
            'stop_reason': 'tool_use', 'usage': {'input_tokens': 20, 'output_tokens': 30}}


def cognitive(next_value=None, updates=(), *, wire=None):
    if (wire is not None and next_value and next_value.get('type') == 'capability_request'
            and any(t['name'] == next_value['capability'] for t in wire['tools'])):
        assert not updates, 'Native consultation has no provisional cognition.'
        return reply(next_value['capability'], {k: v for k, v in next_value.items()
                                               if k not in {'type', 'capability'}})
    return reply('cognitive_step', {'type': 'cognitive_step', 'updates': list(updates),
                                  'next': next_value or {'type': 'no_change'}})


def prior_updates(items):
    """Scripted fixtures serialize the model view into the unchanged commit DTO."""
    updates = []
    for item in items:
        update = {('status' if k == 'prior_status' else k): v
                  for k, v in item.items() if k != 'prior_truth'}
        if 'prior_truth' in item:
            update['status'] = {True: 'supported', False: 'contradicted', None: 'open'}[item['prior_truth']]
        updates.append(update)
    return updates


@pytest.mark.parametrize('body', ['', ' \n\t'])
def test_blank_workspace_source_stays_readable_without_invalid_inline_evidence(tmp_path, body):
    workspace=tmp_path/'workspace';workspace.mkdir()
    (workspace/'blank.txt').write_bytes(body.encode('utf8'))
    calls=[]
    def answer(role, wire):
        assert role=='mind'
        calls.append(wire)
        ref=session.file_source('blank.txt')['ref']
        if len(calls)==1:
            return reply('read_evidence', {'refs':[ref]})
        return cognitive(updates=[{'kind':'belief','id':'new:blank',
            'claim':'The observed file contains no non-whitespace text.',
            'status':'supported','basis':[{'ref':ref}]}])
    with Session(tmp_path/'session',workspace=workspace,goal='Assess the file contents; no action is needed.',
                 transport=answer,ipython=LocalTestPython(workspace)) as session:
        result=session.run()
        assert result['mind_revision']==1 and len(calls)==2
        ref=session.file_source('blank.txt')['ref']
        assert session.mind.read_source(ref)['text']==body
        assert session.read_source(ref)==body
        assert session.run()['cost']==result['cost']


class LocalTestPython:
    """Only deterministic test-owned code is passed here; real runs use Docker."""
    def __init__(self, workspace):
        self.workspace = workspace

    def execute(self, code):
        # Explicit literal op identifiers avoid running any generated code in tests.
        if code == 'request_review':
            (self.workspace/'.mind-request.json').write_text(json.dumps({'question': 'Predict adding two items.'}), encoding='utf-8')
        elif code == 'finish':
            (self.workspace/'result.json').write_text('{"count":5}', encoding='utf-8')
            (self.workspace/'.lumina-complete').write_text('done', encoding='utf-8')
        return IPythonResult(True, output='Workspace update finished.')

    def close(self):
        pass

    def interrupt(self):
        return True


def test_execution_checkpoint_preserves_real_run_without_cognitive_history(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    options = dict(directory=tmp_path/'session', ipython=LocalTestPython(workspace),
                   transport=lambda *_: pytest.fail('checkpoint construction called provider'))
    with execution_checkpoint(workspace=workspace, goal='Preserve the old running checkpoint.', **options) as session:
        assert session.state['profile_history'][-1]['from'] == 'cognitive-chain-v62'
        assert 'owner_inputs' not in session.state
        assert session.mind.inspect().revision == session.calls.summary()['calls'] == 0
        assert not list((session.directory/'mind').glob('activation-*.jsonl'))
        assert session.execution.state.decision_count == 0
        run = session.execution.state.execution_id
        history = session.directory/'execution.jsonl'
        events = history.read_bytes()
    with execution_checkpoint(**options) as session:
        assert session.execution.state.execution_id == run
        assert history.read_bytes() == events
        assert session.mind.inspect().revision == session.calls.summary()['calls'] == 0


def fake_compute(monkeypatch):
    monkeypatch.setattr(wm, '_compute', lambda request: json.dumps({
        'initial': {'observation': request['initial_observation'], 'outcome': 'ongoing'},
        'steps': [{'observation': {'count': 5}, 'outcome': 'complete'}]}).encode())


@pytest.mark.parametrize('repair_phase', [0, 1, 2, 3])
def test_three_step_activity_retains_both_sources_and_restarts_between_consults(tmp_path, repair_phase):
    from Mind.chain import ChainMind
    from Mind.organ import MindOrgan, MindInput, MindResultEvent, Evidence
    from Mind.trace import MindTrace, replay_activation, CAPABILITY_OBSERVED
    import re
    wires = []
    planned = [(phase, repair) for phase in (1, 2, 3)
               for repair in ([True, False] if phase == repair_phase else [False])]
    answers = iter(planned)
    def answer(wire):
        wires.append(wire)
        phase, repair = next(answers)
        if repair:
            return reply('cognitive_step', {'type': 'cognitive_step', 'next': {'type': 'no_change'}})
        if phase == 1:
            assert 'read version A' not in json.dumps(wire)
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': ['source']}, wire=wire)
        if phase == 2:
            assert 'read version A' in json.dumps(wire)
            assert 'computed result B' not in json.dumps(wire)
            return cognitive({'type': 'capability_request', 'capability': 'analyze_world_model',
                'question': 'Compare the sourced conditions.', 'refs': ['source'], 'model_ref': '', 'observation_file': 'result.json'}, wire=wire)
        content = wire['messages'][-1]['content'][0]['content']
        if content.startswith('{'):
            # A repair appends its errors after the original final-step projection.
            content = wire['messages'][-3]['content'][0]['content']
        source_records = json.loads(content.split('\n', 1)[1])['source_records']
        refs = [r['ref'] for r in source_records if r['ref'].startswith('activation:observation')]
        assert len(refs) == 1
        assert json.dumps(wire).count('read version A') == 1  # Still present in the preceding tool result.
        return cognitive({'type': 'directive', 'text': 'Use the conditions supported by the new evidence.'}, [
            {'kind': 'belief', 'id': 'new:read', 'claim': 'The read returned version A.', 'status': 'supported',
             'basis': [{'ref': 'source'}]},
            {'kind': 'belief', 'id': 'new:model', 'claim': 'The computation reported result B.', 'status': 'supported',
             'basis': [{'ref': refs[0]}]}])
    responses = []
    def transport(wire):
        previous = [m['content'] for m in wire['messages'] if m['role']=='assistant']
        assert previous == [r['content'] for r in responses]
        assert len(json.dumps(wire).encode()) < 240000
        response = answer(wire)
        response['content'].insert(0, {'type':'thinking', 'thinking':'opaque ' * 4800,
                                       'signature':'provider-signature'})
        responses.append(response)
        return response
    options = dict(directory=tmp_path, available_capabilities=('read_evidence', 'analyze_world_model'))
    value = MindInput('multi', 'Review current conditions.', 'task', 1, 'Deliver the result.', 'run', 'waiting',
                      (Evidence('catalogue', 'Available source ref: source.', 'execution'),))
    with MindOrgan(model=ChainMind(transport), **options) as mind:
        first = mind.activate(value)
        assert first.status == 'waiting' and mind.inspect().revision == 0
    first_result = MindResultEvent(first.request.request_ref,
        {'capability': 'read_evidence', 'text': json.dumps({'read_result': 'sources-v1', 'sources': [{'ref': 'source', 'text': 'read version A', 'origin': 'execution'}]}), 'origin': 'execution'})
    with MindOrgan(model=ChainMind(transport), **options) as mind:
        assert mind.activate(value).request.request_ref == first.request.request_ref
        second = mind.accept_result(first_result)
        assert second.status == 'waiting' and second.request.request_ref != first.request.request_ref
        assert mind.inspect().revision == 0
        count = len(wires)
        assert mind.accept_result(first_result).request.request_ref == second.request.request_ref
        assert len(wires) == count
    with MindOrgan(model=ChainMind(transport), **options) as mind:
        assert mind.activate(value).request.request_ref == second.request.request_ref
        final = mind.accept_result(MindResultEvent(second.request.request_ref,
            {'capability': 'analyze_world_model', 'text': '{"answer":"computed result B"}', 'origin': 'computation'}))
        assert final.status == 'accepted' and mind.inspect().revision == 1
        items = mind.inspect().items
        assert len({item['basis'][0]['ref'] for item in items}) == 2
        for item in items:
            assert mind.read_source(item['basis'][0]['ref'])['text'] in ('read version A', 'answer: computed result B')
    path, = [p for p in tmp_path.glob('activation-*.jsonl') if '.native.' not in p.name]
    trace = MindTrace.reopen(path)
    assert len(replay_activation(trace.events).model_requests) == 3
    assert sum(e.event_type == CAPABILITY_OBSERVED for e in trace.events) == 2
    assert len(wires) == 3 + bool(repair_phase)


@pytest.mark.parametrize('observation, alignment, quantity_status, meaning', [
    ({'count': 5}, 'unverified', None, 'Final count'),
    ({'count': 5, 'add': 2, 'observation_object': 'result.json', 'observation_time': 'after_add'}, 'compared', 'matched', 'Final count'),
    ({'count': 6, 'add': 2, 'observation_object': 'result.json', 'observation_time': 'after_add'}, 'compared', 'mismatch', 'Final count'),
    ({'count': 5, 'add': 3, 'observation_object': 'result.json', 'observation_time': 'after_add'}, 'not_applicable', None, 'Final count'),
])
def test_automatic_compute_delivery_feedback_and_quiet_restart(tmp_path, monkeypatch, observation, alignment, quantity_status, meaning):
    fake_compute(monkeypatch)
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    rules = 'Initial count is 3. Adding two items increases count by 2.'
    (workspace/'rules.txt').write_text(rules, encoding='utf-8')
    ref = 'source:' + fingerprint(['rules.txt', rules, 'execution', 'observed_text'])[:24]
    counts = Counter()
    wires = []

    class ObservedTestPython(LocalTestPython):
        def execute(self, code):
            result = super().execute(code)
            if code == 'finish':
                write_json(self.workspace/'result.json', observation)
            return result

    def transport(role, wire):
        counts[role] += 1
        wires.append((role, wire))
        if role == 'execution':
            n = counts[role]
            if n == 1:
                return reply('ipython', {'code': 'request_review'})
            if n == 2:
                return reply('wait', {'event_type': 'MIND_REVIEW'})
            if n == 3:
                assert 'The forecast is five' in json.dumps(wire)
                assert 'BUILDER_PRIVATE_SOURCE' not in json.dumps(wire)
                return reply('ipython', {'code': 'finish'})
            if n == 4:
                assert 'claim_complete' in {t['name'] for t in wire['tools']}  # Registered observation was already reviewed.
                return reply('wait', {'event_type': 'MIND_REVIEW'})
            assert 'claim_complete' in {t['name'] for t in wire['tools']}
            return reply('claim_complete', {})
        if role == 'mind':
            assert 'BUILDER_PRIVATE_SOURCE' not in json.dumps(wire)
            assert '.lumina-complete' not in json.dumps(wire)
            if counts[role] == 1:
                return cognitive({'type': 'capability_request', 'capability': 'analyze_world_model',
                    'question': 'Predict final count after adding two items.', 'refs': [ref], 'model_ref': '',
                    'observation_file': 'result.json'}, wire=wire)
            if counts[role] == 2:
                return cognitive({'type': 'directive', 'text': 'The forecast is five under the additive rule; preserve that count as the delivery acceptance.'},
                    [{'kind': 'belief', 'id': 'new:forecast', 'claim': 'The conditional forecast is five.',
                      'status': 'supported', 'basis': [{'ref': 'activation:observation'}]}])
            assert 'model feedback' in json.dumps(wire) or 'first_divergence' in json.dumps(wire)
            data=json.loads(wire['messages'][0]['content'].split('\n\nExact source catalogue')[0])
            feedback_source = next(source for source in data['source_records'] if source.get('label') == 'model feedback')
            feedback_ref = feedback_source['ref']
            feedback_text = feedback_source['text']
            feedback, = json.loads(feedback_text)
            assert len(feedback_text) <= 1000
            assert feedback['action_horizon_alignment'] == alignment
            assert feedback['quantity_status'] == quantity_status
            assert 'dynamics_status' not in feedback  # A declared final-field comparison is not a trajectory check.
            if quantity_status and len(meaning) < 100:
                assert feedback['quantities']['count'] == {
                    'predicted': 5, 'observed': observation['count'], 'meaning': meaning, 'unit': 'items'}
            elif quantity_status:
                assert 'quantities' not in feedback
                assert feedback['quantities_at'] == feedback['check_ref']
            else:
                assert feedback['reason'] == ('unknown_action_or_conditions' if alignment == 'unverified'
                                              else 'recorded_action_or_conditions_differ')
                assert 'quantities' not in feedback
            return cognitive(updates=prior_updates(data['cognition']['prior_model_judgments']))
        assert 'persistent high-level Mind' not in wire['system']
        assert 'cognition' not in wire['messages'][0]['content']
        if counts[role] == 1:
            return reply('compute', {'source': '# BUILDER_PRIVATE_SOURCE', 'initial_observation': {'count': 3},
                'actions': [{'add': 2}], 'observation_file': 'result.json', 'check_spec': {
                    'action': {'add': 2}, 'conditions': {}, 'object': 'result.json', 'when': 'after_add',
                    'quantities': {'count': {'meaning': meaning, 'unit': 'items'}}}})
        result = json.loads(wire['messages'][-1]['content'][0]['content'])
        return reply('report', {'run_ref': result['run_ref'], 'answer': 'Conditional final count is five.',
                               'assumptions': 'The sourced additive rule applies.', 'unknowns': 'Future execution has not occurred.'})

    directory = tmp_path/'session'
    with execution_checkpoint(directory, workspace=workspace, goal='Deliver result.json with the final count.',
                 limits={'calls':40,'output_tokens':300000,'request_bytes':2800000},
                 transport=transport, ipython=ObservedTestPython(workspace)) as session:
        status = session.run()
        assert status['execution_status'] == 'completed'
        assert status['mind_revision'] == 2
        assert status['deliveries'][0]['status'] == 'execution_returned'
        assert status['predictions'][0]['receipt'] is not None
        assert status['cognition'][0]['claim'] == 'The conditional forecast is five.'
        comparisons = [read_json(p) for p in (directory/'comparisons').glob('*.json')]
        terminal = next(c for c in comparisons if c['reality_head'] == status['obligation']['head'])
        assert terminal['alignment']['status'] == alignment
        if quantity_status:
            assert terminal['quantity_comparison']['status'] == quantity_status
            assert terminal['alignment']['quantities']['count']['meaning'] == meaning
        else:
            assert 'quantity_comparison' not in terminal
        assert terminal['check']['dynamics']['status'] == 'unobserved'
        assert terminal['check']['outcome']['checks'][-1]['status'] == 'unobserved'
        assert terminal['check']['outcome']['status'] == 'unobserved'
        assert not session.completion_review_required()
        old_result = (workspace/'result.json').read_bytes()
        (workspace/'result.json').write_text('{"count":6}', encoding='utf-8')
        assert session.completion_review_required()
        (workspace/'result.json').write_bytes(old_result)
        assert not session.completion_review_required()
        old_request = (workspace/'.mind-request.json').read_bytes()
        (workspace/'.mind-request.json').write_text('{"question":"A new concern needs review."}', encoding='utf-8')
        assert not session.completion_review_required()  # An uncommitted legacy file is not a new actor event.
        (workspace/'.mind-request.json').write_bytes(old_request)
        assert not session.completion_review_required()
        count = status['cost']['calls']
    with execution_checkpoint(directory, transport=lambda *_: pytest.fail('quiescent restart called provider'),
                 ipython=LocalTestPython(workspace)) as session:
        restarted = session.run()
        assert restarted['cost']['calls'] == count
        assert restarted['cognition'] == status['cognition']


def test_completion_claim_yields_to_feedback_without_hiding_the_tool(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    counts = Counter()
    def transport(role, wire):
        counts[role] += 1
        if role == 'mind':
            return (cognitive({'type':'directive', 'text':'Preserve the agreed count in the final result.'})
                    if counts[role] == 1 else cognitive())
        if counts[role] == 1: return reply('ipython', {'code':'request_review'})
        if counts[role] == 2: return reply('wait', {'event_type':'MIND_REVIEW'})
        if counts[role] == 3: return reply('ipython', {'code':'finish'})
        assert 'claim_complete' in {t['name'] for t in wire['tools']}
        if counts[role] == 5:
            doc = json.loads(wire['messages'][0]['content'][0]['text'])
            assert doc['cognitive_feedback']['status'] == 'accepted'
            assert doc['cognitive_feedback']['completion_review_required'] is False
        return reply('claim_complete', {})
    directory = tmp_path/'session'
    with execution_checkpoint(directory, workspace=workspace, goal='Deliver the final count.', transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        status = session.run()
        assert status['execution_status'] == 'completed'
        assert counts == {'execution': 5, 'mind': 2}
        assert not session.completion_review_required()
        events = session.execution.result.events
        assert sum(e.event_type == 'COMPLETION_DEFERRED' for e in events) == 1
        assert sum(e.event_type == 'ROOT_WAITING' for e in events) == 1


def test_completion_handoff_recovers_pending_review_before_another_execution_call(tmp_path, monkeypatch):
    from Mind import chain
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'
    counts = Counter()
    def transport(role, wire):
        counts[role] += 1
        if role == 'mind':
            return (cognitive({'type':'directive','text':'Deliver the verified result.'})
                    if counts[role] == 1 else cognitive())
        if counts[role] == 1: return reply('wait', {'event_type':'MIND_REVIEW'})
        if counts[role] == 2: return reply('ipython', {'code':'finish'})
        return reply('claim_complete', {})
    original = chain.run_mind_once
    def pause_review(nervous, mind):
        if counts['mind'] == 1:
            raise BudgetPause('test_interrupted_review')
        return original(nervous, mind)
    monkeypatch.setattr(chain, 'run_mind_once', pause_review)
    with execution_checkpoint(directory, workspace=workspace, goal='Deliver the result.', transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        first = session.run()
        assert first['execution_status'] == 'running'
        assert first['obligation']['status'] == 'pending'
        assert counts == {'execution':3, 'mind':1}
    monkeypatch.setattr(chain, 'run_mind_once', original)
    order = []
    def resumed_transport(role, wire):
        order.append(role)
        return transport(role, wire)
    with execution_checkpoint(directory, transport=resumed_transport, ipython=LocalTestPython(workspace)) as session:
        result = session.run()
        assert result['execution_status'] == 'completed'
        assert order == ['mind', 'execution']
        before = result['cost']['calls']
    with execution_checkpoint(directory, transport=resumed_transport, ipython=LocalTestPython(workspace)) as session:
        assert session.run()['cost']['calls'] == before


def test_received_guidance_survives_action_window_and_restart_without_redelivery(tmp_path, monkeypatch):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'
    guidance = 'Use the conditional estimate of 41 units; keep its unmeasured scope in the delivery.'
    counts = Counter()
    native_deliveries = []
    stop_after = [13]
    original_call = Session.call_execution
    def pause_at_safe_boundary(session, wire, **kwargs):
        if counts['execution'] == stop_after[0]:
            raise BudgetPause('test_checkpoint')  # Before provider reservation; no unknown action.
        return original_call(session, wire, **kwargs)
    monkeypatch.setattr(Session, 'call_execution', pause_at_safe_boundary)

    def transport(role, wire):
        counts[role] += 1
        if role == 'mind':
            return cognitive({'type': 'directive', 'text': guidance})
        n = counts[role]
        if n == 1: return reply('ipython', {'code': 'request_review'})
        if n == 2: return reply('wait', {'event_type': 'MIND_REVIEW'})
        explicit = [m for m in wire['messages'] if m['role'] == 'user'
                    and isinstance(m['content'], str) and m['content'].startswith('[Mind Supervisor Directive]')]
        native_deliveries.extend(explicit)
        if n > 3:
            document = json.loads(wire['messages'][0]['content'][0]['text'])
            history = document['recent_execution_history']
            retained = [json.loads(m['content']) for m in wire['messages'] if m['role'] == 'user'
                        and isinstance(m['content'], str) and m['content'].startswith('{')]
            assert retained[-1]['received_guidance'][0]['text'] == guidance
            assert guidance not in json.dumps(history)
            assert sum(len(json.dumps(item, ensure_ascii=False)) for item in history) <= 8000
            assert len(history) <= 6
            assert all('action' not in entry and 'native_messages' not in entry for entry in history)
            assert sum(m['role'] == 'assistant' for m in wire['messages']) <= 6
            assert not explicit  # Remembering received input is not a new Directive delivery.
        return reply('ipython', {'code': 'ordinary_action_' + str(n)})

    with execution_checkpoint(directory, workspace=workspace, goal='Deliver an evidence-based estimate.',
                 limits={'calls': 40, 'output_tokens': 240000, 'request_bytes': 2800000},
                 transport=transport, ipython=LocalTestPython(workspace)) as session:
        assert session.run()['stop_reason'] == 'test_checkpoint'
        assert counts['execution'] == 13  # Original guidance is outside the six-action window.
        assert not session.nervous.pending('host', 1)
    with execution_checkpoint(directory, transport=transport, ipython=LocalTestPython(workspace)) as session:
        stop_after[0] = 15
        assert session.run()['stop_reason'] == 'test_checkpoint'
        assert len(session.state['deliveries']) == 1
    assert len(native_deliveries) == 1


def test_budget_pause_before_guidance_receipt_keeps_delivery_obligation(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    calls = Counter()
    def transport(role, wire):
        calls[role] += 1
        if role == 'mind':
            return cognitive({'type': 'directive', 'text': 'Preserve the conditional estimate in the delivery.'})
        return reply('ipython', {'code': 'request_review'}) if calls[role] == 1 else reply('wait', {'event_type': 'MIND_REVIEW'})
    with execution_checkpoint(tmp_path/'session', workspace=workspace, goal='Deliver a conditional estimate.',
                 limits={'calls': 3, 'output_tokens': 100000, 'request_bytes': 1000000},
                 transport=transport, ipython=LocalTestPython(workspace)) as session:
        status = session.run()
        assert status['stop_reason'] == 'chain_budget_exhausted'
        assert status['deliveries'][0]['status'] == 'bound'
        assert session.nervous.pending('host', 1)
        assert status['obligation']['head'] not in session.state['handled']


def test_rejected_multi_tool_response_executes_nothing_and_resumes_same_decision(tmp_path, monkeypatch):
    import Mind.chain as chain
    from Mind.event_loop import ExecutionModel
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'
    both = reply('ipython', {'code':'finish'})
    both['content'].append({'type':'tool_use','id':'second','name':'ipython','input':{'code':'finish'}})
    with monkeypatch.context() as patch:
        # Reproduce V22's adapter, retaining its explicitly rejected response.
        patch.setattr(chain, 'ExecutionModel', lambda *a, **kw: ExecutionModel(*a, **{**kw,'native_batches':False,'no_tool_repair':False}))
        with execution_checkpoint(directory, workspace=workspace, goal='Deliver the final count.',
                     transport=lambda *_:both, ipython=LocalTestPython(workspace)) as session:
            with pytest.raises(ValueError, match='execution_tool_cardinality'):
                session.run()
            assert session.execution.state.decision_count == 0
            assert not list(workspace.iterdir())
            assert len(read_json(directory/'calls/0001.json')['response']['content']) == 2
    counts = Counter()
    def transport(role, wire):
        counts[role] += 1
        if role == 'mind': return cognitive()
        if counts[role] == 1: return both
        results = [b for m in wire['messages'] if isinstance(m['content'],list)
                   for b in m['content'] if b.get('type')=='tool_result']
        assert {b['tool_use_id'] for b in results} == {'call-1','second'}
        result_turns = [m for m in wire['messages'] if isinstance(m['content'],list)
                        and any(b.get('type')=='tool_result' for b in m['content'])]
        assert len(result_turns) == 1  # Anthropic requires all sibling results together.
        assert all(b['type']=='tool_result' for b in result_turns[0]['content'])
        history = json.loads(wire['messages'][0]['content'][0]['text'])['recent_execution_history']
        assert [entry['call_ref'] for entry in history] == ['execution-call:0002']
        return reply('claim_complete', {})
    with execution_checkpoint(directory, transport=transport, ipython=LocalTestPython(workspace)) as session:
        result = session.run()
        assert result['execution_status'] == 'completed'
        assert session.execution.state.decision_count == 2
        assert result['cost']['calls'] == 4  # Original rejection remains charged.
        assert len(read_json(directory/'calls/0001.json')['response']['content']) == 2


@pytest.mark.parametrize('kind', ['mixed_control', 'duplicate_id', 'too_many'])
def test_native_batch_owner_rejects_entire_invalid_batch_before_side_effects(tmp_path, kind):
    workspace=tmp_path/'workspace'; workspace.mkdir()
    value=reply('ipython',{'code':'finish'})
    for i in range(4 if kind=='too_many' else 1):
        value['content'].append({'type':'tool_use','id':'call-1' if kind=='duplicate_id' else 'extra'+str(i),
            'name':'claim_complete' if kind=='mixed_control' else 'ipython',
            'input':{} if kind=='mixed_control' else {'code':'finish'}})
    with execution_checkpoint(tmp_path/'session',workspace=workspace,goal='Deliver a count.',
                 transport=lambda role,_:value if role=='execution' else cognitive(),
                 ipython=LocalTestPython(workspace)) as session:
        result=session.run()
        assert result['execution_status']=='failed'
        assert not list(workspace.iterdir())


@pytest.mark.parametrize('siblings', [False, True])
@pytest.mark.parametrize('length', [1800, 10000])
def test_current_tool_output_uses_existing_context_budget(tmp_path, siblings, length):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    source = 'x'*(length-22) + 'FINAL ACCEPTANCE CLAUSE'
    class OutputPython(LocalTestPython):
        def execute(self, code):
            return IPythonResult(True, output=source, original_output_chars=len(source))
    count = 0
    def transport(role, wire):
        nonlocal count
        count += 1
        if count == 1:
            response = reply('ipython', {'code':'read_owned_input'})
            if siblings:
                response['content'].append({'type':'tool_use','id':'second','name':'ipython',
                    'input':{'code':'read_owned_input'}})
            return response
        results = [json.loads(b['content'])['observation']['result']['output']
            for m in wire['messages'] if isinstance(m['content'],list)
            for b in m['content'] if b.get('type')=='tool_result']
        assert len(results) == (2 if siblings else 1)
        for output in results:
            assert output['text'] == source[:4000]
            assert output['original_chars'] == len(source)
            assert output['truncated'] == (len(source)>4000)
        return reply('wait', {'event_type':'OWNER_EVIDENCE'})
    with execution_checkpoint(tmp_path/'session',workspace=workspace,goal='Preserve all acceptance conditions.',
                 transport=transport,ipython=OutputPython(workspace)) as session:
        result = session.run()
        assert result['execution_status'] == 'waiting'


def test_review_projection_preserves_question_and_scope_without_host_digest(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    request={'question':'Preserve this conditional question. '+('q'*610),
             'evidence_files':['inputs.json','task.txt'],'model_ref':'candidate analysis'}
    (workspace/'.mind-request.json').write_text(json.dumps(request),encoding='utf-8')
    with execution_checkpoint(tmp_path/'session',workspace=workspace,goal='Assess the available evidence.',
                 transport=lambda *_:reply('wait',{'event_type':'OWNER_EVIDENCE'}),
                 ipython=LocalTestPython(workspace)) as session:
        session.run()
        _, snapshot=session.capture()
        observation=session.observation(snapshot)
        value=json.loads(observation.recent_outcome)
        historical=value['historical_execution_request']
        if 'ref' in historical:
            historical=json.loads(session.read_source(historical['ref']))
        assert historical['request']==request
        assert historical['origin']=={'first_observed_execution_ref':session.execution.state.execution_id}
        assert snapshot['request_origin']['digest']==fingerprint(request)
        assert len(observation.recent_outcome)<=1000


@pytest.mark.parametrize('length', [25, 5000])
def test_review_separates_historical_request_from_latest_tool_result_after_restart(tmp_path, monkeypatch, length):
    monkeypatch.setattr(Session, 'completion_review_required', lambda self: True)
    workspace=tmp_path/'workspace';workspace.mkdir()
    request={'question':'Old judgment: condition established.', 'evidence_files':[], 'model_ref':''}
    (workspace/'.mind-request.json').write_text(json.dumps(request), encoding='utf-8')
    output='Actual condition unresolved. '+('x'*length)
    class ResultPython(LocalTestPython):
        def execute(self, code):
            (self.workspace/'.lumina-complete').write_text('done', encoding='utf-8')
            return IPythonResult(True, output=output, original_output_chars=len(output))
    decisions=iter([reply('ipython', {'code':'measure'}), reply('claim_complete', {})])
    directory=tmp_path/'session'
    with execution_checkpoint(directory, workspace=workspace, goal='Assess the condition.',
                 transport=lambda *_:next(decisions), ipython=ResultPython(workspace)) as session:
        session.execution.resume()
        session.execution.resume()
        head,snapshot=session.capture()
        before=session.observation(snapshot)
        assert session.capture()==(head,snapshot)  # Projection is not a new event/head.
        session.save()
    with execution_checkpoint(directory, transport=lambda *_:pytest.fail('inspection called model'),
                 ipython=ResultPython(workspace)) as session:
        assert session.capture()==(head,snapshot)
        assert session.observation(snapshot)==before
        value=json.loads(before.recent_outcome)
        assert value['completion_review_pending'] is True
        assert value['execution_ref']==session.execution.state.execution_id
        assert value['state_version']==session.execution.state.version
        latest=value['last_tool_result']
        if 'ref' in latest:
            latest=json.loads(session.read_source(latest['ref']))
        assert latest['output']==output
        historical=value['historical_execution_request']
        if 'ref' in historical:
            historical=json.loads(session.read_source(historical['ref']))
        assert historical['request']==request
        assert 'execution_request' not in value
        assert len(before.recent_outcome)<=1000


def test_guidance_with_unchanged_business_files_gets_one_result_review(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    (workspace/'result.json').write_text('{"count":5}', encoding='utf-8')
    (workspace/'.lumina-complete').write_text('done', encoding='utf-8')
    counts = Counter()
    def transport(role, wire):
        counts[role] += 1
        if role == 'mind':
            if counts[role] == 1:
                return cognitive({'type':'directive', 'text':'Keep the agreed result if its count is five.'})
            return cognitive()
        if counts[role] == 1: return reply('ipython', {'code':'request_review'})
        if counts[role] in (2, 3): return reply('wait', {'event_type':'MIND_REVIEW'})
        assert 'claim_complete' in {t['name'] for t in wire['tools']}
        return reply('claim_complete', {})
    with execution_checkpoint(tmp_path/'session', workspace=workspace, goal='Deliver the final count.', transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        result = session.run()
        assert result['execution_status'] == 'completed'
        assert counts == {'execution':4, 'mind':2}
        assert session.run()['cost'] == result['cost']


def test_cognitive_failure_remains_pending_without_infinite_resume_calls(tmp_path):
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    n = 0
    def transport(role, wire):
        nonlocal n
        if role == 'execution':
            n += 1
            return reply('ipython', {'code': 'finish'}) if n == 1 else reply('claim_complete', {})
        return {'content': [], 'stop_reason': 'max_tokens'}
    directory = tmp_path/'session'
    with execution_checkpoint(directory, workspace=workspace, goal='Deliver final count.', transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        status = session.run()
        assert status['obligation']['status'] == 'failed'
        assert status['obligation']['attempt'] == 1  # A new activity requires explicit versioned retry.
        assert status['mind_revision'] == 0
        count = status['cost']['calls']
    with execution_checkpoint(directory, transport=lambda *_: pytest.fail('exhausted obligation retried'),
                 ipython=LocalTestPython(workspace)) as session:
        assert session.run()['cost']['calls'] == count
        assert not session.status()['obligation']['understanding_updated']


def test_whole_wire_budget_reservation_counts_unknown_calls(tmp_path):
    calls = Calls(tmp_path, {'calls': 1, 'output_tokens': 5000, 'request_bytes': 10000},
                  lambda *_: (_ for _ in ()).throw(OSError('unknown response')))
    wire = {'model': 'deepseek-v4-pro', 'max_tokens': 2000, 'messages': [{'role': 'user', 'content': 'full request'}]}
    with pytest.raises(OSError):
        calls.call('mind', wire)
    with pytest.raises(BudgetPause, match='budget_exhausted'):
        calls.call('mind', wire)
    assert calls.summary()['allocated_output_tokens'] == 2000
    assert calls.summary()['errors'] == 1


def test_path_authority_and_session_separation(tmp_path):
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    with pytest.raises(ValueError, match='escape'):
        workspace_path(workspace, '../outside.json')
    with pytest.raises(ValueError, match='disjoint'):
        Session(workspace/'session', workspace=workspace, goal='A goal')
    builder = Builder(tmp_path/'models', None, None)
    with pytest.raises(ValueError, match='reference'):
        builder.model('model:' + '../'*10 + 'xx')

def test_review_cannot_forge_owner_approval_and_explicit_owner_event_resumes(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    counts = Counter()
    def transport(role, wire):
        counts[role] += 1
        if role == 'execution':
            n = counts[role]
            if n == 1: return reply('ipython', {'code': 'request_review'})
            if n == 2: return reply('wait', {'event_type': 'MIND_REVIEW'})
            if n == 3: return reply('wait', {'event_type': 'USER_APPROVAL'})
            if n == 4: return reply('ipython', {'code': 'finish'})
            if n == 5: return reply('wait', {'event_type': 'MIND_REVIEW'})
            return reply('claim_complete', {})
        if counts[role] == 2:
            return cognitive({'type': 'directive', 'text': 'Proceed with the final delivery when the owner approves.'})
        return cognitive()
    directory = tmp_path/'session'
    with execution_checkpoint(directory, workspace=workspace, goal='Deliver only after user approval.', transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        status = session.run()
        assert session.execution.state.waiting_for == 'USER_APPROVAL'
        assert counts['mind'] == 1
        (workspace/'new-condition.txt').write_text('Owner approval is still required.',encoding='utf-8')
        status = session.run()
        assert status['obligation']['status'] == 'accepted'  # Unrelated files do not wake Mind while awaiting approval.
        assert counts['mind'] == 1
        assert not status['deliveries']
        assert session.run()['cost']['calls'] == status['cost']['calls']
        final = session.run(owner_event=('USER_APPROVAL', 'Owner approves this workspace delivery.'))
        assert final['execution_status'] == 'completed'


@pytest.mark.parametrize("crash_before", [True, False])
def test_bound_directive_survives_crash_around_root_woken(tmp_path, monkeypatch, crash_before):
    from Execution.execution import EventLog
    class ProcessCrash(BaseException):
        pass
    workspace = tmp_path/'workspace'; workspace.mkdir()
    counts = Counter()
    def transport(role, wire):
        counts[role] += 1
        if role == 'execution':
            n = counts[role]
            if n == 1: return reply('ipython', {'code': 'request_review'})
            if n == 2: return reply('wait', {'event_type': 'MIND_REVIEW'})
            if n == 3:
                assert 'Preserve the approved final count.' in json.dumps(wire)
                return reply('ipython', {'code': 'finish'})
            if n == 4: return reply('wait', {'event_type': 'MIND_REVIEW'})
            return reply('claim_complete', {})
        return cognitive({'type': 'directive', 'text': 'Preserve the approved final count.'}) if counts[role] == 1 else cognitive()
    original = EventLog.append
    def append(log, event_type, *args, **kwargs):
        if event_type == 'ROOT_WOKEN' and crash_before:
            raise ProcessCrash()
        event = original(log, event_type, *args, **kwargs)
        if event_type == 'ROOT_WOKEN':
            raise ProcessCrash()
        return event
    directory = tmp_path/'session'
    with monkeypatch.context() as patch:
        patch.setattr(EventLog, 'append', append)
        with execution_checkpoint(directory, workspace=workspace, goal='Deliver the final count.', transport=transport,
                     ipython=LocalTestPython(workspace)) as session:
            with pytest.raises(ProcessCrash):
                session.run()
    with execution_checkpoint(directory, transport=transport, ipython=LocalTestPython(workspace)) as session:
        status = session.run()
        assert status['execution_status'] == 'completed'
        assert counts['execution'] == 5
        assert counts['mind'] == 2
        assert len(status['deliveries']) == 1
        assert status['deliveries'][0]['status'] in {'execution_returned', 'execution_returned_after_recovery'}


def test_computation_source_reread_keeps_its_origin(tmp_path):
    from Mind.host import activation_event, run_mind_once
    from Mind.organ import MindInput, Evidence
    from Mind.task_view import execution_goal
    workspace = tmp_path/'workspace'; workspace.mkdir()
    n = 0
    chosen_ref = None
    def transport(role, wire):
        nonlocal n
        n += 1
        if n == 1:
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': [chosen_ref]}, wire=wire)
        return cognitive(updates=[{'kind': 'belief', 'id': 'new:forecast', 'claim': 'This is a conditional forecast.',
            'status': 'supported', 'basis': [{'ref': chosen_ref}]}])
    with Session(tmp_path/'session', workspace=workspace, goal='Understand a conditional prediction.',
                 transport=transport, ipython=LocalTestPython(workspace)) as session:
        chosen_ref = session.source('Predicted count is five.', 'historical model report', 'computation')
        value = MindInput('read-once', 'Read forecast.', 'owner-goal', 1, execution_goal(session.state['task']),
            'run-test', 'waiting', (Evidence('catalogue', chosen_ref, 'execution'),), owner_task=session.state['task'])
        session.nervous.publish(activation_event(value))
        assert run_mind_once(session.nervous, session.mind).status == 'waiting'
        session.consult(session.nervous.pending('mind.requests',1)[0])
        assert run_mind_once(session.nervous, session.mind).status == 'accepted'
        item = session.mind.inspect().items[0]
        source = session.mind.read_source(item['basis'][0]['ref'])
        assert source['origin'] == 'computation'
        assert source['text'] == 'Predicted count is five.' and source['ref'] == chosen_ref


def test_traversal_is_bounded_before_any_provider_work(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    for i in range(129):
        (workspace/str(i)).mkdir()
    with Session(tmp_path/'session', workspace=workspace, goal='A bounded task.',
                 transport=lambda *_: pytest.fail('provider before preflight'),
                 ipython=LocalTestPython(workspace)) as session:
        with pytest.raises(ValueError, match='entry_bound'):
            session.run()
        assert session.calls.summary()['calls'] == 0


def test_long_owner_goal_and_completion_protocol_reach_actual_execution_wire(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    goal='Maintain all stated business constraints. ' * 16 + 'Owner approval must never be fabricated.'
    n=0
    def transport(role, wire):
        nonlocal n
        if role == 'mind': return cognitive()
        document=json.loads(wire['messages'][0]['content'][0]['text'])
        from Mind.chain import PROTOCOL
        from Mind.task_view import execution_goal
        expected=execution_goal({'business_goal':goal,'execution_protocol':PROTOCOL})
        assert document['goal']['text'] == expected
        assert not document['goal']['truncated']
        n+=1
        return reply('ipython',{'code':'finish'}) if n==1 else reply('claim_complete',{})
    with execution_checkpoint(tmp_path/'session',workspace=workspace,goal=goal,transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        assert session.run()['execution_status']=='completed'


def test_versioned_native_length_repair_keeps_original_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr("Mind.chain.INTEGRATION_CONTRACT_VERSION", "cognitive-chain-v66")
    from Mind.chain import ChainMind
    from Mind.organ import MindOrgan, MindInput
    calls=[]
    def transport(wire):
        calls.append(wire)
        assert wire['thinking']=={'type':'enabled'}
        assert wire['output_config']=={'effort':'low'}
        assert wire['max_tokens']==16384
        assert wire['tool_choice']=={'type':'auto'}
        assert 'temperature' not in wire
        if len(calls)==1:
            response=cognitive({'type':'directive','text':'x'*1001})
            response['content'].insert(0,{'type':'thinking','thinking':'opaque provider continuation','signature':'test'})
            return response
        assert wire['messages'][-2]['content'][0]['thinking']=='opaque provider continuation'
        assert 'maxLength' in json.dumps(wire['messages'][-1])
        return cognitive({'type':'directive','text':'Retain the current acceptance constraints.'})
    with MindOrgan(directory=tmp_path/'mind',model=ChainMind(transport)) as mind:
        receipt=mind.activate(MindInput('event','Review goal.','goal',1,'Deliver a valid artifact.','run','waiting'))
        assert receipt.status=='accepted'
        assert len(calls)==2
        assert receipt.output['text']=='Retain the current acceptance constraints.'
    records=[json.loads(line) for p in (tmp_path/'mind').glob('*.native.jsonl') for line in p.read_text(encoding='utf-8').splitlines()]
    assert [r['accepted'] for r in records if r['kind']=='result']==[False,True]


def test_complete_owner_goal_identity_checked_before_resume_provider(tmp_path):
    from Mind.chain import PROTOCOL
    workspace=tmp_path/'workspace'; workspace.mkdir()
    goal='Preserve the common constraints. '*20 + 'Tail restriction A.'
    directory=tmp_path/'session'
    def transport(role, wire):
        return reply('wait', {'event_type':'OWNER_EVIDENCE'})
    with execution_checkpoint(directory, workspace=workspace, goal=goal, transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        session.run()
    document=read_json(directory/'session.json')
    document['state']['task']['business_goal']=goal[:-2]+'B.'
    assert len(goal)==len(document['state']['task']['business_goal'])
    # Valid envelope alone is not authority to replace the persisted Execution goal.
    document['sha256']=fingerprint(document['state'])
    write_json(directory/'session.json',document)
    with pytest.raises(ValueError,match='execution_owner_goal_conflict'):
        execution_checkpoint(directory, transport=lambda *_: pytest.fail('identity conflict reached provider'),
                ipython=LocalTestPython(workspace))


def test_builder_bounded_independent_batch_keeps_complete_tool_continuation(tmp_path, monkeypatch):
    fake_compute(monkeypatch)
    n=0
    def transport(role,wire):
        nonlocal n
        n+=1
        if n==1:
            value={'source':'# private model', 'initial_observation':{'count':3},
                   'actions':[{'add':2}], 'observation_file':'result.json'}
            first=reply('compute',value)
            first['content'].insert(0,{'type':'thinking','thinking':'private','signature':'test-signature'})
            first['content'].append({'type':'tool_use','id':'call-2','name':'compute','input':value})
            return first
        assert wire['messages'][-2]['content'][0]['type']=='thinking'
        results=wire['messages'][-1]['content']
        assert len(results)==2
        assert {r['tool_use_id'] for r in results}=={'call-1','call-2'}
        result=json.loads(results[-1]['content'])
        return reply('report',{'run_ref':result['run_ref'],'answer':'Conditional count is five.',
            'assumptions':'Additive rule.','unknowns':'Real actions have not occurred.'})
    calls=Calls(tmp_path/'calls',{'calls':3,'output_tokens':2*16384,'request_bytes':100000},transport)
    builder=Builder(tmp_path/'models',calls,lambda ref:'Count starts at three.')
    result=builder.analyze('request',{'question':'Predict count.', 'refs':['rule'], 'model_ref':''})
    assert result['origin']=='computation'
    assert 'private model' not in result['text']
    assert len(list((tmp_path/'models').glob('model-*.json')))==2
    assert builder.analyze('request',{'question':'Predict count.', 'refs':['rule'], 'model_ref':''})==result
    assert n==2


def test_consultation_protocol_failure_is_durable_and_bounded(tmp_path,monkeypatch):
    workspace=tmp_path/'workspace'; workspace.mkdir()
    counts=Counter()
    def transport(role,wire):
        counts[role]+=1
        if role=='execution':
            return reply('wait',{'event_type':'MIND_REVIEW'})
        return cognitive({'type':'capability_request','capability':'read_evidence','refs':['unknown']}, wire=wire)
    directory=tmp_path/'session'
    with execution_checkpoint(directory,workspace=workspace,goal='Determine whether evidence suffices.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        def fail(request):
            raise ValueError('builder_terminal_or_cardinality_failure')
        monkeypatch.setattr(session,'consult',fail)
        status=session.run()
        assert status['obligation']['status']=='failed'
        assert status['obligation']['error']=='model_failed'
        assert status['obligation']['attempt']==1
        assert not status['obligation']['understanding_updated']
        assert len(list((directory/'failures').glob('*.json')))==1
        assert counts['mind']==1
    with execution_checkpoint(directory,transport=lambda *_:pytest.fail('exhausted consultation retried'),
                 ipython=LocalTestPython(workspace)) as session:
        assert session.run()['obligation']['status']=='failed'


def test_current_wake_event_and_decision_survive_native_tool_continuation(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    n=0
    def transport(role,wire):
        nonlocal n
        if role=='mind':return cognitive()
        n+=1
        document=json.loads(wire['messages'][0]['content'][0]['text'])
        assert document['state']['decision_count']==n-1
        if n>1:
            assert document['incoming_event']['event_type']['text']=='MIND_REVIEW'
            assert 'MIND_REVIEW' in wire['messages'][-1]['content'][0]['content']
        return reply('wait',{'event_type':'MIND_REVIEW' if n==1 else 'OWNER_EVIDENCE'})
    with execution_checkpoint(tmp_path/'session',workspace=workspace,goal='Wait for owner evidence after review.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        session.run()
        assert session.execution.state.waiting_for=='OWNER_EVIDENCE'
        assert n==2


@pytest.mark.parametrize('error_code',['container_cleanup_failed','container_output_incomplete'])
def test_builder_isolation_lifecycle_failure_stops_and_stays_stopped(tmp_path,error_code):
    from Mind.world_model import ModelComputationError
    counts=Counter()
    def transport(role,wire):
        counts['provider']+=1
        return reply('compute',{'source':'source','initial_observation':{'count':3},'actions':[{'add':2}],
            'observation_file':'result.json'})
    def compute(*_):
        counts['compute']+=1
        raise ModelComputationError(error_code)
    calls=Calls(tmp_path/'calls',{'calls':3,'output_tokens':25000,'request_bytes':100000},transport)
    for _ in range(2):
        builder=Builder(tmp_path/'models',calls,lambda ref:'Initial count is three.',compute)
        with pytest.raises(RuntimeError,match=error_code):
            builder.analyze('request',{'question':'Predict.','refs':['rule'],'model_ref':''})
    assert counts=={'provider':1,'compute':1}


@pytest.mark.parametrize('thinking', [False, True])
def test_builder_resume_reuses_computations_and_repairs_reference_without_guessing(tmp_path,monkeypatch,thinking):
    from Mind import chain
    fake_compute(monkeypatch)
    counts=Counter()
    def compute(*args):
        counts['compute']+=1
        return wm.run_model(*args)
    def transport(role,wire):
        counts['provider']+=1
        assert wire['thinking'] == {'type': 'enabled' if thinking else 'disabled'}
        if not thinking:
            assert wire['temperature'] == 0 and wire['tool_choice'] == {'type':'any'}
            assert 'output_config' not in wire
        if counts['provider']==1:
            return reply('compute',{'source':'model','initial_observation':{'count':3},'actions':[{'add':2}],
                'observation_file':'result.json'})
        refs=wire['tools'][1]['input_schema']['properties']['run_ref']['enum']
        assert len(refs)==2
        return reply('report',{'run_ref':refs[-1] if counts['provider']==3 else refs[-1]+'; invalid',
            'answer':'Conditional count five.','assumptions':'Additive rule.','unknowns':'Future actions.'})
    calls=Calls(tmp_path/'calls',{'calls':4,'output_tokens':3*16384,'request_bytes':100000},transport)
    request={'question':'Predict.','refs':['rule'],'model_ref':''}
    destination=tmp_path/'models'/(fingerprint('request')+'.json')
    original=chain.write_json
    class Crash(BaseException):pass
    def write(path,value):
        if Path(path)==destination:raise Crash()
        original(path,value)
    with monkeypatch.context() as patch:
        patch.setattr(chain,'write_json',write)
        with pytest.raises(Crash):
            Builder(tmp_path/'models',calls,lambda ref:'Initial count three.',compute,thinking=thinking).analyze('request',request)
    result=Builder(tmp_path/'models',calls,lambda ref:'Initial count three.',compute,thinking=thinking).analyze('request',request)
    assert json.loads(result['text'])['kind']=='COMPUTED_CONDITIONAL'
    assert counts=={'provider':3,'compute':1}


def test_exhausted_execution_budget_returns_status_and_explicit_extension_resumes(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    n=0
    def transport(role,wire):
        nonlocal n
        if role=='mind':return cognitive()
        n+=1
        return reply('ipython',{'code':'finish'}) if n==1 else reply('claim_complete',{})
    directory=tmp_path/'session'
    with execution_checkpoint(directory,workspace=workspace,goal='Deliver final count.',transport=transport,
                 limits={'calls':1,'output_tokens':10000,'request_bytes':100000},
                 ipython=LocalTestPython(workspace)) as session:
        assert session.run()['stop_reason']=='chain_budget_exhausted'
        assert session.run()['cost']['calls']==1
    with execution_checkpoint(directory,transport=transport,ipython=LocalTestPython(workspace)) as session:
        session.extend_budget(2, output_tokens=24000, request_bytes=240000)
        result=session.run()
        assert result['execution_status']=='completed'
        assert result['stop_reason'] is None
        assert result['cost']['calls']==3


def test_mind_and_builder_budget_pause_before_native_reservation_can_resume(tmp_path,monkeypatch):
    fake_compute(monkeypatch)
    workspace=tmp_path/'workspace';workspace.mkdir()
    counts=Counter()
    def transport(role,wire):
        counts[role]+=1
        n=counts[role]
        if role=='execution':
            if n==1:return reply('ipython',{'code':'request_review'})
            if n==2:return reply('wait',{'event_type':'MIND_REVIEW'})
            if n==3:return reply('ipython',{'code':'finish'})
            if n==4:return reply('wait',{'event_type':'MIND_REVIEW'})
            return reply('claim_complete',{})
        if role=='mind':
            if n==1:
                refs=wire['tools'][0]['input_schema']['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum']
                return cognitive({'type':'capability_request','capability':'analyze_world_model',
                    'question':'Predict count.','refs':[refs[0]],'model_ref':'','observation_file':'result.json'}, wire=wire)
            if n==2:return cognitive({'type':'directive','text':'The forecast is five under the given additive rule.'})
            return cognitive()
        if n==1:return reply('compute',{'source':'model','initial_observation':{'count':3},'actions':[{'add':2}],
            'observation_file':'result.json', 'check_spec': {'action': {'add':2}, 'conditions': {},
                'object':'result.json','when':'after_add', 'quantities':{'count':{'meaning':'Final count','unit':'items'}}}})
        result=json.loads(wire['messages'][-1]['content'][0]['content'])
        return reply('report',{'run_ref':result['run_ref'],'answer':'Conditional five.','assumptions':'Additive rule.','unknowns':'Actual outcome.'})
    directory=tmp_path/'session'
    with execution_checkpoint(directory,workspace=workspace,goal='Deliver count.',transport=transport,
                 limits={'calls':2,'output_tokens':100000,'request_bytes':1000000},ipython=LocalTestPython(workspace)) as session:
        result=session.run()
        assert result['stop_reason']=='chain_budget_exhausted'
        assert result['obligation']['status']=='pending'
        event=result['obligation']['event_id']
        assert counts['mind']==0
    with execution_checkpoint(directory,transport=transport,ipython=LocalTestPython(workspace)) as session:
        session.extend_budget(1)
        assert session.run()['stop_reason']=='chain_budget_exhausted'
        assert not list((directory/'models').glob('*.turn-*.json'))
        assert counts['mind']==1
        session.extend_budget(2)
        assert session.run()['stop_reason']=='chain_budget_exhausted'
        assert counts['builder']==2
        assert session.state['obligation']['event_id']==event
        assert session.state['obligation']['attempt']==1
    with execution_checkpoint(directory,transport=transport,ipython=LocalTestPython(workspace)) as session:
        session.extend_budget(20, output_tokens=200000)
        result=session.run()
        assert result['execution_status']=='completed'
        assert result['stop_reason'] is None
        assert counts=={'execution':5,'mind':3,'builder':2}


def test_execution_recent_history_is_bounded_and_excludes_other_roles(tmp_path):
    calls=Calls(tmp_path,{'calls':20,'output_tokens':100000,'request_bytes':1000000},
                lambda role,wire:reply('ipython',{'code':'local_action()'}) if role=='execution' else cognitive())
    for i in range(8):
        wire={'model':'deepseek-v4-pro','max_tokens':2000,'messages':[{'role':'user','content':[{'type':'text',
            'text':json.dumps({'state':{'decision_count':i},'observation':{'output':'local result'}})}]}]}
        if i:
            wire['messages'] += [{'role':'assistant','content':reply('ipython',{'code':'local_action()'})['content']},
                {'role':'user','content':[{'type':'tool_result','tool_use_id':'call-1',
                    'content':json.dumps({'observation':{'output':'local result'}})}]}]
        calls.call('execution',wire)
    calls.call('mind',{'model':'deepseek-v4-pro','max_tokens':2000,'messages':[{'role':'user','content':'PRIVATE MIND BELIEF'}]})
    calls.call('builder',{'model':'deepseek-v4-pro','max_tokens':2000,'messages':[{'role':'user','content':'PRIVATE MODEL DEBUG'}]})
    committed = [(f'decision-{i+1:06d}', fingerprint([('call-1','ipython','{"code":"local_action()"}')])) for i in range(8)]
    assert calls.execution_history() == []  # No owner attestation, no action history.
    history=calls.execution_history(8, committed_calls=committed)
    assert [h['decision'] for h in history]==list(range(2,8))
    assert 'PRIVATE' not in json.dumps(history)
    assert len(json.dumps(history))<60000


def test_execution_history_binds_batch_outcomes_from_failed_later_owner_wire(tmp_path):
    # Same shape as archived calls 0100/0101: plural observations and two native
    # siblings, including the decisive small result that the old history lost.
    calls = Calls(tmp_path, {}, lambda *_: pytest.fail('history must not call a model'))
    batch = [{'type': 'tool_use', 'id': 'read', 'name': 'ipython', 'input': {'code': 'read ' + 'x' * 9000}},
             {'type': 'tool_use', 'id': 'compare', 'name': 'ipython', 'input': {'code': 'compare levels'}}]
    outputs = ['bundle.zip size: 27528',
               'level 1: ctor=27528 writestr=31025\nlevel 6: ctor=27528 writestr=27528\n'
               'level 9: ctor=27528 writestr=27519']
    observations = [{'result': {'ok': True, 'error_code': None, 'error': None,
        'output': {'text': output, 'original_chars': len(output), 'truncated': False}}} for output in outputs]
    committed = [('decision-000001', fingerprint([(b['id'], b['name'],
        json.dumps(b['input'], sort_keys=True, separators=(',', ':'))) for b in batch]))]

    def record(index, run, count, native=(), results=(), response=None):
        document = {'state': {'execution_id': run, 'decision_count': count},
                    'observations': observations, 'observation': {'output': 'PREVIOUS UNRELATED RESULT'}}
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': json.dumps(document)}]}]
        if native:
            messages += [{'role': 'assistant', 'content': list(native)}, {'role': 'user', 'content': list(results)}]
        value = {'role': 'execution', 'wire': {'messages': messages}, 'status': 'failed'}
        if response is not None:
            value.update(response={'content': response}, status='received')
        write_json(calls.directory/f'{index:04}.json', value)

    def result_blocks(value):
        return [{'type': 'tool_result', 'tool_use_id': block['id'],
                 'content': json.dumps({'observation': observation})}
                for block, observation in zip(batch, value)]

    record(1, 'run', 0, response=batch)
    record(2, 'run', 1, batch, result_blocks(observations))  # Actual owner result; provider failed.
    wrong = [{'result': {'ok': True, 'output': 'WRONG SOURCE'}}] * 2
    record(3, 'foreign-run', 1, batch, result_blocks(wrong))
    rejected = [{**batch[0], 'input': {'code': 'uncommitted action'}}, batch[1]]
    record(4, 'run', 1, rejected, result_blocks(wrong))  # Same IDs cannot authenticate a different batch.
    mismatched = result_blocks(wrong)
    mismatched[1]['tool_use_id'] = 'another-call'
    record(5, 'run', 1, batch, mismatched)
    history = calls.execution_history(1, 'run', committed)
    assert len(history) == 1
    entry = history[0]
    assert entry['call_ref'] == 'execution-call:0001'
    assert entry['native_messages'][0]['content'] == batch
    assert entry['native_messages'][1]['content'] == result_blocks(observations)
    assert entry['result_call_ref'] == 'execution-call:0002'
    assert '27519' in json.dumps(history) and 'level 1' in json.dumps(history)
    assert 'WRONG SOURCE' not in json.dumps(history) and 'PREVIOUS UNRELATED' not in json.dumps(history)
    assert Calls(tmp_path, {}, None).execution_history(1, 'run', committed) == history

    latest = [{'type': 'tool_use', 'id': 'latest', 'name': 'ipython', 'input': {'code': 'next_action'}}]
    record(6, 'run', 1, response=latest)
    committed.append(('decision-000002', fingerprint([('latest', 'ipython', '{"code":"next_action"}')])))
    newest = calls.execution_history(2, 'run', committed)[-1]
    assert newest['native_messages'] is None
    assert newest['result_call_ref'] is None  # The current adapter supplies this newest result.
    assert 'PREVIOUS UNRELATED' not in json.dumps(newest)


def test_execution_history_keeps_whole_recent_rounds_without_guidance_mixed_in(tmp_path):
    calls = Calls(tmp_path, {}, None)
    guidance = '[Mind Supervisor Directive]\nKeep the conditional estimate and its limited scope.'
    committed, previous = [], None
    for count in range(10):
        batch = [{'type': 'tool_use', 'id': f'action-{count}', 'name': 'ipython',
                  'input': {'code': 'large action ' + 'x' * 20000 if count >= 7 else 'small action'}}]
        document = {'state': {'execution_id': 'run', 'decision_count': count}}
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': json.dumps(document)}]}]
        if previous:
            observation = {'result': {'ok': False, 'error_code': 'probe_error',
                'error': {'text': 'known failure ' + 'e' * 4000, 'original_chars': 8013, 'truncated': True},
                'output': {'text': 'known observation ' + 'o' * 4000, 'original_chars': 8018, 'truncated': True},
                'canonical_truncated': True, 'original_output_chars': 8018}}
            messages += [{'role': 'assistant', 'content': previous}, {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': previous[0]['id'],
                 'content': json.dumps({'observation': observation})}]}]
        if count == 0:
            messages.append({'role': 'user', 'content': guidance})
        write_json(calls.directory/f'{count+1:04}.json', {'role': 'execution', 'status': 'received',
            'wire': {'messages': messages}, 'response': {'content': batch}})
        committed.append((f'decision-{count+1:06d}', fingerprint([(batch[0]['id'], 'ipython',
            json.dumps(batch[0]['input'], sort_keys=True, separators=(',', ':')))])))
        previous = batch
    history = calls.execution_history(10, 'run', committed)
    assert guidance not in json.dumps(history)
    actions = [item for item in history if 'native_messages' in item]
    assert actions[-1]['decision'] == 9
    assert [item['decision'] for item in actions] == list(range(actions[0]['decision'], 10))
    assert actions[0]['decision'] > 0  # Old small actions do not fill holes left by large rounds.
    complete = [item for item in actions if item['native_messages']]
    assert isinstance(complete[-1]['native_messages'][0]['content'][0]['input']['code'], str)
    observed = json.loads(complete[-1]['native_messages'][1]['content'][0]['content'])['observation']['result']
    assert observed['ok'] is False and observed['error_code'] == 'probe_error'
    assert observed['output']['truncated'] is True and observed['output']['original_chars'] == 8018
    assert observed['error']['truncated'] is True and observed['error']['original_chars'] == 8013
    assert len(actions) <= 6 and sum(len(json.dumps(item, ensure_ascii=False)) for item in actions) <= 60000
    assert Calls(tmp_path, {}, None).execution_history(10, 'run', committed) == history


@pytest.mark.parametrize('crash_at',['native_result','logical_output','terminal_trace','cognition_commit'])
def test_free_mind_replay_completes_when_budget_is_exactly_exhausted(tmp_path,monkeypatch,crash_at):
    from Mind.organ import MindOrgan
    from Mind.trace import MindTrace
    workspace=tmp_path/'workspace';workspace.mkdir()
    counts=Counter()
    def transport(role,wire):
        counts[role]+=1
        if role=='mind':return cognitive()
        return reply('ipython',{'code':'finish'}) if counts[role]==1 else reply('claim_complete',{})
    class Crash(BaseException):pass
    original_commit=MindOrgan._append
    original_native=MindTrace.append_native
    original_trace=MindTrace.append
    def append(trace,event_type,*args,**kwargs):
        result=original_trace(trace,event_type,*args,**kwargs)
        if (event_type=="MODEL_OUTPUT_RECORDED" and crash_at=="logical_output") or (event_type=="ACTIVATION_FINISHED" and crash_at=="terminal_trace"):
            raise Crash()
        return result
    def commit(mind,record):
        result=original_commit(mind,record)
        if record['kind']=='accepted' and crash_at=='cognition_commit':raise Crash()
        return result
    def native(trace,**record):
        result=original_native(trace,**record)
        if record['kind']=='result' and record.get('accepted') and crash_at=='native_result':raise Crash()
        return result
    directory=tmp_path/'session'
    with monkeypatch.context() as patch:
        patch.setattr(MindOrgan,'_append',commit)
        patch.setattr(MindTrace,'append_native',native)
        patch.setattr(MindTrace,'append',append)
        with execution_checkpoint(directory,workspace=workspace,goal='Deliver final count.',transport=transport,
                     limits={'calls':3,'output_tokens':32768,'request_bytes':1000000},
                     ipython=LocalTestPython(workspace)) as session:
            with pytest.raises(Crash):session.run()
    with execution_checkpoint(directory,transport=lambda *_:pytest.fail('free replay dispatched a call'),
                 ipython=LocalTestPython(workspace)) as session:
        result=session.run()
        assert result['execution_status']=='completed'
        assert result['obligation']['status']=='accepted'
        assert result['mind_revision']==1
        assert result['cost']['calls']==3
        assert result['stop_reason'] is None


def test_owner_event_exact_source_survives_execution_paraphrase_and_repeated_wires(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    counts=Counter()
    literal='No additional observations are currently available.'
    def transport(role,wire):
        counts[role]+=1
        if role=='execution':
            n=counts[role]
            if n==1:return reply('wait',{'event_type':'OWNER_EVIDENCE'})
            if n in {2,3}:return reply('ipython',{'code':'request_review'})
            return reply('wait',{'event_type':'MIND_REVIEW' if n==4 else 'OWNER_EVIDENCE'})
        text=wire['messages'][0]['content']
        if counts['mind'] > 1:
            assert literal in text
        assert 'owner event' not in wire['system'] or 'owner events' in wire['system']
        return cognitive()
    with execution_checkpoint(tmp_path/'session',workspace=workspace,goal='Request judgment when ambiguous.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        session.run()
        result=session.run(owner_event=('OWNER_EVIDENCE',literal))
        assert result['mind_revision']==3  # initial wait, owner input, changed review request
        assert session.execution.state.waiting_for=='OWNER_EVIDENCE'
        owners=session.calls.owner_events()
        assert len(owners)==1
        assert owners[0]['data']==literal
        before=session.capture()[1]['owner_events']
        assert session.run()['cost']['calls']==result['cost']['calls']
        assert session.capture()[1]['owner_events']==before


def test_feedback_allocation_is_reserved_before_guidance_execution(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    n=0
    def transport(role,wire):
        nonlocal n
        if role=='mind':return cognitive({'type':'directive','text':'Retain the specified final count.'})
        n+=1
        if n==1:return reply('wait',{'event_type':'MIND_REVIEW'})
        return reply('ipython',{'code':'finish'})
    with execution_checkpoint(tmp_path/'session',workspace=workspace,goal='Deliver final count.',transport=transport,
                 limits={'calls':3,'output_tokens':32768,'request_bytes':1000000},ipython=LocalTestPython(workspace)) as session:
        result=session.run()
        assert result['stop_reason']=='feedback_budget_reserved'
        assert result['deliveries'][0]['status']=='bound'
        assert not result['deliveries'][0].get('call_ref')
        assert result['cost']['calls']==2
        assert session.run()['cost']['calls']==2


def test_repeated_owner_statement_after_change_remains_latest(tmp_path):
    calls=Calls(tmp_path,{'calls':10,'output_tokens':20000,'request_bytes':100000},lambda *_:reply('wait',{'event_type':'OWNER_EVIDENCE'}))
    for i,data in enumerate(['A','A','B','B','A']):
        wire={'model':'deepseek-v4-pro','max_tokens':2000,'messages':[{'role':'user','content':[{'type':'text',
            'text':json.dumps({'state':{'decision_count':i},'incoming_event':{
                'event_type':{'text':'OWNER_EVIDENCE','truncated':False},'data':{'text':data,'truncated':False}}})}]}]}
        calls.call('execution',wire)
    owners=calls.owner_events()
    assert [e['data'] for e in owners]==['A','B','A']
    assert [e['call_ref'] for e in owners]==['execution-call:0001','execution-call:0003','execution-call:0005']
    committed = [(f'decision-{i+1:06d}', fingerprint([('call-1','wait','{"event_type":"OWNER_EVIDENCE"}')])) for i in range(5)]
    assert len(calls.execution_history(2, committed_calls=committed))==2


def test_answer_before_owner_decision_commit_stops_without_losing_guidance(tmp_path,monkeypatch):
    workspace=tmp_path/'workspace';workspace.mkdir()
    counts=Counter()
    def transport(role,wire):
        counts[role]+=1
        if role=='mind':return cognitive({'type':'directive','text':'Retain the specified count.'})
        if counts[role]==1:return reply('wait',{'event_type':'MIND_REVIEW'})
        return reply('ipython',{'code':'finish'})
    original=Calls.call
    class Crash(BaseException):pass
    def call(calls,role,wire,**kwargs):
        response=original(calls,role,wire,**kwargs)
        if role=='execution' and counts[role]==2:raise Crash()
        return response
    directory=tmp_path/'session'
    with monkeypatch.context() as patch:
        patch.setattr(Calls,'call',call)
        with execution_checkpoint(directory,workspace=workspace,goal='Deliver count.',transport=transport,
                     ipython=LocalTestPython(workspace)) as session:
            with pytest.raises(Crash):session.run()
    with execution_checkpoint(directory,transport=lambda *_:pytest.fail('uncommitted answered decision was resampled'),
                 ipython=LocalTestPython(workspace)) as session:
        with pytest.raises(RuntimeError,match='execution_response_pending_owner_commit'):session.run()
        assert session.state['deliveries'][0]['status']=='bound'
        assert session.state['deliveries'][0]['text']=='Retain the specified count.'
        assert session.calls.summary()['calls']==3


@pytest.mark.parametrize('old_version', ['cognitive-chain-v32', 'cognitive-chain-v45'])
def test_queued_unstarted_event_migrates_without_consuming_it(tmp_path, old_version):
    workspace=tmp_path/'workspace';workspace.mkdir()
    directory=tmp_path/'session'
    with execution_checkpoint(directory,workspace=workspace,goal='Keep original inputs.',transport=lambda *_:pytest.fail('no call'),
                 ipython=LocalTestPython(workspace)) as session:
        head,snapshot=session.capture()
        session.enqueue(head,snapshot)
        event=session.state['obligation']['event_id']
        session.state['version']=old_version
        session.save()
    with execution_checkpoint(directory,transport=lambda *_:pytest.fail('no call'),ipython=LocalTestPython(workspace)) as session:
        assert session.state['profile_history'][-1]['from']==old_version
        assert session.nervous.pending('mind',1)[0].event_id==event
        assert session.mind.inspect().revision==0
        assert session.calls.summary()['calls']==0


def test_explicit_budget_extension_is_bounded_per_grant_and_preserves_spend(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'
    with Session(directory, workspace=workspace, goal='Keep inputs.',
                 limits={'calls':200, 'output_tokens':2000000, 'request_bytes':20000000},
                 transport=lambda *_: cognitive(), ipython=LocalTestPython(workspace)) as session:
        session.calls.call('mind', {'model':'deepseek-v4-pro', 'max_tokens':100, 'messages':[]})
        before = session.calls.summary()
        session.extend_budget(48, output_tokens=786432, request_bytes=11520000)
        assert session.calls.summary() == before
        assert session.state['limits']['calls'] == 248
        for amount in (0, -1, True, 201):
            with pytest.raises(ValueError, match='invalid_explicit_budget_extension'):
                session.extend_budget(amount)
        assert len(session.state['budget_extensions']) == 1
    with Session(directory, transport=lambda *_: pytest.fail('restart called provider'),
                 ipython=LocalTestPython(workspace)) as session:
        assert session.calls.summary() == before
        assert session.state['limits']['calls'] == 248


def test_large_feedback_keeps_comparison_status_and_readable_exact_details(tmp_path, monkeypatch):
    from Mind.trace import _thaw
    workspace=tmp_path/'workspace'; workspace.mkdir()
    report = {'model_ref':'model:'+'a'*32, 'action_horizon_alignment':'compared',
        'comparison_kind':'declared_final_observables_only', 'reality_source':'observed-source',
        'alignment': {'reason':'recorded_bindings_match; differences_are_not_automatic_causal_refutations',
            'comparison':{'status':'mismatch'}, 'quantities':{
                str(i): {'predicted':1, 'observed':2, 'meaning':'long field meaning '*10, 'unit':'items'}
                for i in range(8)}}}
    with execution_checkpoint(tmp_path/'session', workspace=workspace, goal='Retain observations.',
                 transport=lambda *_:pytest.fail('no provider'), ipython=LocalTestPython(workspace)) as session:
        monkeypatch.setattr(session, 'feedback', lambda head, snapshot=None:[report])
        head,snapshot=session.capture(); session.enqueue(head,snapshot)
        evidence = _thaw(session.nervous.pending('mind',1)[0].data['evidence'])
        summary, = json.loads(evidence[-1]['text'])
        assert len(evidence[-1]['text']) <= 1000
        assert summary['quantity_status'] == 'mismatch' and 'quantities' not in summary
        assert summary['quantities_at'] == summary['check_ref']
        assert json.loads(session.read_source(summary['check_ref'])) == report


def test_later_owner_events_fit_catalogue_without_losing_sources_or_order(tmp_path):
    from Mind.trace import _thaw
    workspace=tmp_path/'workspace'; workspace.mkdir()
    for index in range(9):
        (workspace/f'document{index}.json').write_text('{}',encoding='utf-8')
    with execution_checkpoint(tmp_path/'session', workspace=workspace, goal='Retain all owner conditions.',
                 transport=lambda *_:pytest.fail('no provider'), ipython=LocalTestPython(workspace)) as session:
        for index in range(8):
            session.accept_owner_input(('OWNER_EVIDENCE',f'Owner observation {index}. Retain earlier constraints.'))
        head,snapshot=session.capture(); session.enqueue(head,snapshot)
        evidence = _thaw(session.nervous.pending('mind',1)[0].data['evidence'])
        catalogue = json.loads(evidence[0]['text'])
        assert len(evidence[0]['text']) <= 1000
        assert catalogue == snapshot['files']
        assert len(snapshot['owner_events']) == 8
        visible = {item['ref']:item['text'] for item in evidence}
        for index,owner in enumerate(snapshot['owner_events']):
            ref = owner['ref']
            assert f'Owner observation {index}.' in session.read_source(ref)
            assert visible[ref] == session.read_source(ref)
            assert session.source_info(ref)['event_ref'] == f'owner-input:{index+1}'


def test_second_consultation_local_source_stays_bound_to_its_historical_activity(tmp_path):
    import re
    from Mind.host import run_mind_once
    from Mind.trace import _thaw
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    old_text, new_text = 'Original signed setting is 4.', 'Revised signed setting is 9.'
    (workspace/'rules.txt').write_text(old_text, encoding='utf-8')
    counts = Counter()
    source_ref = inherited = None
    def transport(role, wire):
        counts[role] += 1
        if role == 'execution':
            return reply('wait', {'event_type': 'MIND_REVIEW' if counts[role] == 1 else 'OWNER_EVIDENCE'})
        assert role == 'mind'
        phase = counts[role]
        if phase in {1, 4}:
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': [source_ref]}, wire=wire)
        if phase == 2:
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence',
                              'refs': [source_ref]}, wire=wire)
        if phase == 3:
            content = wire['messages'][-1]['content'][0]['content']
            refs = re.findall(r'SOURCE (activation:observation[^\n]*)\n', content)
            assert not refs  # Repeated reads retain the original content identity.
            return cognitive(updates=[{'kind': 'belief', 'id': 'new:historical-rule',
                'claim': 'The earlier source recorded a signed setting of 4.', 'status': 'supported',
                'basis': [{'ref': source_ref}]}])
        assert phase == 5
        assert new_text in json.dumps(wire)
        return cognitive(updates=[inherited])
    directory = tmp_path/'session'
    with execution_checkpoint(directory, workspace=workspace, goal='Retain source versions and await owner evidence.',
                 transport=transport, ipython=LocalTestPython(workspace)) as session:
        source_ref = session.source(old_text, 'rules.txt')
        old_ref = source_ref
        first = session.run()
        assert first['mind_revision'] == 1 and counts['mind'] == 3
        item = _thaw(session.mind.inspect().items[0])
        inherited = {key: item[key] for key in ('kind', 'id', 'claim', 'status', 'basis')}
        historical_ref = inherited['basis'][0]['ref']
        original_event = session.state['obligation']['event_id']
    (workspace/'rules.txt').write_text(new_text, encoding='utf-8')
    with execution_checkpoint(directory, transport=transport, ipython=LocalTestPython(workspace)) as session:
        source_ref = session.source(new_text, 'rules.txt')
        owner_event = ('OWNER_EVIDENCE', 'A revised signed rules file is available.')
        session.accept_owner_input(owner_event)
        session.execution.deliver_event(*owner_event, defer_actions=True)
        head, snapshot = session.capture()
        session.enqueue(head, snapshot)
        assert run_mind_once(session.nervous, session.mind).status == 'waiting'
        session.consult(session.nervous.pending('mind.requests', 1)[0])
        assert run_mind_once(session.nervous, session.mind).status == 'accepted'
        session.receipt(session.nervous.pending('host', 1)[0])
        assert session.mind.inspect().revision == 2 and counts['mind'] == 5
        assert session.state['obligation']['event_id'] != original_event
        assert new_text == session.source_record(source_ref)['text']
        assert historical_ref == old_ref
        original_source = session.source_info(historical_ref)
        assert original_source['workspace_version'] == 'superseded'
        assert original_source['current_ref'] == source_ref
        recovered = session.source_record(old_ref)
        assert old_text == recovered['text'] and new_text not in recovered['text']
        assert recovered['origin'] == 'execution'



@pytest.mark.parametrize('control', ['wait', 'claim_complete'])
def test_execution_history_separates_control_continuation_from_prior_measurement(tmp_path, control):
    from Execution.execution import FileContentEquals
    from Execution.organ import ExecutionOrgan
    from Mind.event_loop import ExecutionModel
    from Mind.task_view import execution_goal

    workspace = tmp_path/'workspace'
    workspace.mkdir()
    answers = iter([
        reply('ipython', {'code': 'measure'}),
        reply(control, {'event_type': 'OWNER_EVIDENCE'} if control == 'wait' else {}),
        reply('wait', {'event_type': 'DONE'}),
    ])
    calls = Calls(tmp_path/'calls', {'calls': 3, 'output_tokens': 6000, 'request_bytes': 100000},
                  lambda role, wire: next(answers))
    owner_task = {'business_goal': 'Measure the artifact and await further evidence.',
                  'execution_protocol': 'Completion requires result.txt to contain done.'}
    model = ExecutionModel(lambda wire: calls.call('execution', wire), owner_task=owner_task,
                           incoming_event_pending=lambda: organ.has_unhandled_external_event())

    class Measurement(LocalTestPython):
        def execute(self, code):
            assert code == 'measure'
            return IPythonResult(True, output='Measured artifact bytes: 27528')

    organ = ExecutionOrgan(workspace=workspace, event_log_path=tmp_path/'execution.jsonl',
        max_decisions=5, max_context_chars=12000, max_decisions_per_advance=1,
        model=model, ipython_control=Measurement(workspace))
    try:
        organ.run_goal(execution_goal(owner_task), FileContentEquals('result.txt', 'done'))
        second = organ.resume()
        if control == 'wait':
            assert second.status == 'waiting'
            # A real wake does not turn the earlier IPython result into a Wait result.
            organ.deliver_event('OWNER_EVIDENCE', 'A new signed observation arrived.', defer_actions=True)
        else:
            assert second.status == 'running'  # The owner rejected the completion claim.
        organ.resume()
        committed = organ.committed_tool_calls()
        history = calls.execution_history(3, organ.state.execution_id, committed)
        measurement, continuation = history[:2]
        observed = json.loads(measurement['native_messages'][1]['content'][0]['content'])['observation']
        assert observed['type'] == 'ipython_execution'
        assert observed['result']['output']['text'] == 'Measured artifact bytes: 27528'
        assert continuation['result_scope'] == 'control_continuation_not_action_observation'
        assert continuation['result_call_ref'] == 'execution-call:0003'
        result, = continuation['native_messages'][1]['content']
        assert result['tool_use_id'] == 'call-1'
        owner_wire = read_json(tmp_path/'calls/0003.json')['wire']
        assert owner_wire['tool_choice'] == {'type': 'any'}
        assert 'wait' in {tool['name'] for tool in owner_wire['tools']}
        assert continuation['native_messages'] == owner_wire['messages'][1:3]
        value = json.loads(result['content'])
        if control == 'wait':
            assert 'A new signed observation arrived.' in json.dumps(value['incoming_event'])
            # Original control continuation is retained, not relabelled as a new measurement.
            assert 'Measured artifact bytes' in json.dumps(value['observation'])
        else:
            assert value['observation']['type'] == 'completion_verification'
            assert value['observation']['evidence']['matched'] is False
        assert Calls(tmp_path/'calls', {}, None).execution_history(
            3, organ.state.execution_id, committed) == history
    finally:
        organ.shutdown()


def test_history_retains_attributed_local_judgment_after_intermediate_read_and_restart(tmp_path):
    calls = Calls(tmp_path, {}, None)
    committed, previous = [], None
    for count, comment in enumerate(('A scoped diagnosis; next evidence may revise it.', '', '')):
        batch = [{'type': 'tool_use', 'id': str(count), 'name': 'ipython', 'input': {'code': 'read()'}}]
        document = {'state': {'execution_id': 'run', 'decision_count': count}}
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': json.dumps(document)}]}]
        if previous:
            messages += [{'role':'assistant', 'content':previous}, {'role':'user','content':[
                {'type':'tool_result','tool_use_id':str(count-1),'content':'{"observation":{"text":"owner observation"}}'}]}]
        previous = [{'type': 'text', 'text': comment}, *batch]
        write_json(tmp_path/f'{count+1:04}.json', {'role': 'execution', 'status': 'received',
            'wire': {'messages': messages}, 'response': {'content': previous}})
        committed.append((f'decision-{count+1:06d}', fingerprint([(str(count), 'ipython', '{"code":"read()"}')])) )
    history = calls.execution_history(3, 'run', committed)
    judgment = history[0]['native_messages'][0]
    assert judgment['role'] == 'assistant'
    assert judgment['content'][0]['text'] == 'A scoped diagnosis; next evidence may revise it.'
    assert history[0]['call_ref']=='execution-call:0001'
    assert history[0]['native_messages'][1]['role'] == 'user'
    assert history[-1]['native_messages'] is None
    assert Calls(tmp_path, {}, None).execution_history(3, 'run', committed)==history
    assert calls.execution_history(3, 'other', committed)==[]
    assert calls.execution_history(3, 'run', committed[1:])[0]['call_ref']=='execution-call:0002'


def test_mind_continuation_capacity_is_bounded_without_expanding_other_roles(tmp_path):
    calls=Calls(tmp_path, {'calls':1,'output_tokens':20000,'request_bytes':300000})
    wire={'model':'deepseek-v4-pro','max_tokens':16384,'messages':[{'role':'user','content':'x'*160000}]}
    calls.ensure(wire, role='mind')
    for role in ('builder','execution',None):
        with pytest.raises(ValueError,match='provider_request_too_large'): calls.ensure(wire,role=role)
    wire['messages'][0]['content']='x'*240000
    with pytest.raises(ValueError,match='provider_request_too_large'): calls.ensure(wire,role='mind')
    assert calls.summary()['calls']==0


def test_execution_preserves_terminal_judgment_and_correction_before_dispatch(tmp_path, monkeypatch):
    from Mind.chain import BudgetPause
    workspace=tmp_path/'workspace'; workspace.mkdir()
    with Session(tmp_path/'session',workspace=workspace,goal='Deliver a bounded result.',
                 limits={'calls':20,'output_tokens':200000,'request_bytes':480000},
                 transport=lambda *_: pytest.fail('feedback reserve was spent'),
                 ipython=LocalTestPython(workspace)) as session:
        monkeypatch.setattr(session,'review_dependencies',lambda: ([{'text':'prior guidance'}],[]))
        with pytest.raises(BudgetPause,match='feedback_budget_reserved'):
            session.call_execution({'model':'deepseek-v4-pro','max_tokens':8192,'messages':[]})
        assert session.calls.summary()['calls']==0


def test_provider_rejection_retains_bounded_body_without_headers_or_retry(tmp_path):
    import httpx
    request=httpx.Request('POST','https://api.deepseek.com/anthropic/v1/messages',headers={'x-api-key':'TEST_SECRET'})
    response=httpx.Response(400,request=request,text='missing native thinking content '+('x'*5000))
    def reject(*_): response.raise_for_status()
    calls=Calls(tmp_path,{'calls':1,'output_tokens':8192,'request_bytes':150000},reject)
    wire={'model':'deepseek-v4-pro','max_tokens':8192,'messages':[]}
    with pytest.raises(httpx.HTTPStatusError): calls.call('execution_diagnostic',wire)
    record=read_json(tmp_path/'0001.json')
    assert record['provider_rejection']=={'status_code':400,'body':response.text[:4000],
                                        'original_chars':len(response.text),'truncated':True}
    assert record['status']=='failed' and 'response' not in record
    assert 'TEST_SECRET' not in json.dumps(record)
    with pytest.raises(BudgetPause): calls.call('execution_diagnostic',wire)
    assert calls.summary()['calls']==1


def test_profile_upgrade_retains_terminal_guidance_receipt_without_redelivery(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    directory=tmp_path/'session'
    def transport(role,wire):
        return cognitive({'type':'directive','text':'Preserve the specified acceptance conditions.'}) if role=='mind' else reply('wait',{'event_type':'MIND_REVIEW'})
    with execution_checkpoint(directory,workspace=workspace,goal='Deliver a valid result.',transport=transport,
                 limits={'calls':3,'output_tokens':40000,'request_bytes':1000000},
                 ipython=LocalTestPython(workspace)) as session:
        before=session.run()
        assert before['stop_reason']=='feedback_budget_reserved'
        receipt,=session.nervous.pending('host',1)
        assert receipt.data['status']=='accepted'
        assert session.state['deliveries'][0]['status']=='bound'
        session.state['version']='cognitive-chain-v41';session.save()
    with execution_checkpoint(directory,transport=lambda *_:pytest.fail('upgrade dispatched a call'),
                 ipython=LocalTestPython(workspace)) as session:
        assert session.state['profile_history'][-1]['from']=='cognitive-chain-v41'
        assert session.nervous.pending('host',1)[0]==receipt
        after=session.run()
        assert after['cost']==before['cost']
        assert after['cognition']==before['cognition']
        assert after['deliveries']==before['deliveries']


def test_profile_upgrade_still_rejects_begun_cognitive_consultation(tmp_path):
    from Mind.host import run_mind_once
    from Mind.chain import execution_goal
    from Execution import FileContentEquals
    workspace=tmp_path/'workspace';workspace.mkdir()
    directory=tmp_path/'session'
    with Session(directory,workspace=workspace,goal='Preserve unresolved evidence.',
                 transport=lambda *_:reply('read_evidence', {'refs':['source-pending']}),
                 ipython=LocalTestPython(workspace)) as session:
        session.execution.run_goal(execution_goal(session.state['task']),FileContentEquals('result','done'),defer_actions=True)
        head,snapshot=session.capture();session.enqueue(head,snapshot)
        assert run_mind_once(session.nervous,session.mind).status=='waiting'
        assert session.nervous.pending('mind.requests',1)
        session.state['version']='cognitive-chain-v41';session.save()
    with pytest.raises(ValueError,match='previous_profile_has_pending_activity'):
        Session(directory,transport=lambda *_:pytest.fail('old activity replayed under a new profile'),ipython=LocalTestPython(workspace))


def test_explicit_execution_thinking_setting_persists_across_quiet_restart(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    directory=tmp_path/'session';wires=[]
    def transport(role,wire):
        if role=='mind':return cognitive()
        wires.append(wire)
        response=reply('wait',{'event_type':'OWNER_EVIDENCE'})
        if wire['thinking']['type']=='enabled':
            response['content'].insert(0,{'type':'thinking','thinking':'Scoped local judgment.','signature':'provider-signature'})
        return response
    with execution_checkpoint(directory,workspace=workspace,goal='Wait for the authorized observation.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        session.run();run=session.execution.state.execution_id
        assert not session.status()['execution_thinking']
    with execution_checkpoint(directory,execution_thinking=True,transport=transport,ipython=LocalTestPython(workspace)) as session:
        result=session.run(owner_event=('OWNER_EVIDENCE','The current collection is still pending.'))
        assert result['execution_thinking'] and session.execution.state.execution_id==run
        count=result['cost']['calls']
    assert wires[0]['thinking']['type']=='disabled' and wires[-1]['thinking']['type']=='enabled'
    with execution_checkpoint(directory,transport=lambda *_:pytest.fail('quiet mode restart called provider'),ipython=LocalTestPython(workspace)) as session:
        assert session.run()['cost']['calls']==count
        assert session.status()['execution_thinking'] is True


def test_native_history_preserves_complete_committed_rounds_and_their_own_results(tmp_path):
    calls = Calls(tmp_path, {}, None)
    committed, previous, previous_pairs = [], None, []
    for count in range(9):
        # Repeated IDs and arguments must not let an older replayed pair become
        # the result of the latest owner decision.
        batch = [{'type': 'tool_use', 'id': 'same', 'name': 'ipython', 'input': {'code': 'read_package()'}}]
        content = [{'type': 'thinking', 'thinking': f'local reasoning {count}',
                    'signature': f'opaque-{count}'}, *batch]
        document = {'state': {'execution_id': 'run', 'decision_count': count}}
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': json.dumps(document)}]}]
        if previous is not None:
            result = {'type': 'tool_result', 'tool_use_id': 'same', 'content': json.dumps({
                'observation': {'result': {'ok': True, 'output': {
                    'text': 'header ' * 400 + f'\nactual_parameter_line_for_decision_{count}',
                    'original_chars': 2900, 'truncated': False}}}})}
            pair = [{'role': 'assistant', 'content': previous}, {'role': 'user', 'content': [result]}]
            messages += [*previous_pairs, *pair]
            previous_pairs = [*previous_pairs, *pair]
        write_json(tmp_path/f'{count+1:04}.json', {'role': 'execution', 'status': 'received',
            'wire': {'messages': messages}, 'response': {'content': content}})
        committed.append((f'decision-{count+1:06d}', fingerprint([('same', 'ipython', '{"code":"read_package()"}')])) )
        previous = content
    history = calls.execution_history(9, 'run', committed)
    complete = [item for item in history if item.get('native_messages')]
    assert complete
    for item in complete:
        assistant, result = item['native_messages']
        assert assistant['content'][0]['signature'] == f"opaque-{item['decision']}"
        body = json.loads(result['content'][0]['content'])
        assert body['observation']['result']['output']['text'].endswith(
            f"actual_parameter_line_for_decision_{item['decision']+1}")
    assert len(history) <= 6
    assert Calls(tmp_path, {}, None).execution_history(9, 'run', committed) == history


def test_complete_native_execution_rounds_survive_restart_and_preserve_old_events(tmp_path, monkeypatch):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'
    responses, results, wires = {}, {}, []
    guidance = 'Preserve the acceptance conditions while implementing the local repair.'
    stop_after = [5]
    actual_call = Session.call_execution
    def bounded_call(session, wire, **kwargs):
        if len(wires) == stop_after[0]:
            raise BudgetPause('test_checkpoint')
        return actual_call(session, wire, **kwargs)
    monkeypatch.setattr(Session, 'call_execution', bounded_call)

    class ReadPython(LocalTestPython):
        def execute(self, code):
            return IPythonResult(True, output='source header\n' * 220 + '\nparameter_to_repair = member_level\n')

    def transport(role, wire):
        if role == 'mind':
            return cognitive({'type':'directive', 'text':guidance})
        wires.append(wire)
        pairs = [(a, b) for a, b in zip(wire['messages'], wire['messages'][1:]) if a['role'] == 'assistant']
        assert len(pairs) <= 6
        assert sum(len(json.dumps(pair,ensure_ascii=False,separators=(',',':'))) for pair in pairs) <= 60000
        for assistant, result in pairs:
            call_id = next(b['id'] for b in assistant['content'] if b['type']=='tool_use')
            assert assistant['content'] == responses[call_id]['content']
            if call_id in results:
                assert result == results[call_id]  # Later current events never rewrite historical results.
            else:
                results[call_id] = json.loads(json.dumps(result))
        if len(wires) >= 4:
            assert any('parameter_to_repair' in json.dumps(pair) for pair in pairs[:-1])
        view = json.loads(wire['messages'][0]['content'][0]['text'])
        assert all('action' not in item and 'native_messages' not in item for item in view['recent_execution_history'])
        answer = reply('wait', {'event_type':'MIND_REVIEW'}) if len(wires)==1 else reply('ipython', {'code':'read_source'})
        answer['content'][0]['id'] = 'native-' + str(len(wires))
        answer['content'].insert(0, {'type':'thinking', 'thinking':'Scoped local reasoning '+str(len(wires)),
                                     'signature':'opaque-signature-'+str(len(wires))})
        responses['native-'+str(len(wires))] = answer
        return answer

    options = dict(transport=transport, ipython=ReadPython(workspace))
    with execution_checkpoint(directory, workspace=workspace, goal='Repair the scoped artifact from its source.',
                 execution_thinking=True, limits={'calls':40,'output_tokens':500000,'request_bytes':5000000}, **options) as session:
        assert session.run()['stop_reason']=='test_checkpoint'
        run = session.execution.state.execution_id
    stop_after[0] = 10
    with execution_checkpoint(directory, **options) as session:
        assert session.run()['stop_reason']=='test_checkpoint'
        assert session.execution.state.execution_id == run
        assert len(session.state['deliveries']) == 1
    assert len(wires)==10
    assert sum(any(isinstance(m['content'],str) and m['content'].endswith(guidance)
                   for m in wire['messages']) for wire in wires)==1
    assert sum(json.loads(line)['event_type']=='MIND_DIRECTIVE_APPLIED'
               for path in (directory/'mind').glob('activation-*.jsonl') if '.native.' not in path.name
               for line in path.read_text(encoding='utf-8').splitlines())==1


@pytest.mark.parametrize('interrupted_before_guard', [False, True])
def test_transport_failure_stops_calls_and_requires_owner_check_across_restart(tmp_path, interrupted_before_guard):
    workspace=tmp_path/'workspace'; workspace.mkdir()
    directory=tmp_path/'session'; actions=[]
    class BrokenControl(LocalTestPython):
        failure_diagnostic=None
        def execute(self,code):
            actions.append(code)
            self.failure_diagnostic={'reason':'container_output_unavailable','stderr':'private startup details'}
            return IPythonResult(False,error_code='isolated_kernel_failed',error='RuntimeError')
    with execution_checkpoint(directory,workspace=workspace,goal='Perform the authorized work.',
                 ipython=BrokenControl(workspace),transport=lambda *_:reply('ipython',{'code':'uncertain_action'})) as session:
        if interrupted_before_guard:
            def interrupted_capture():
                raise RuntimeError('stop after committed owner result')
            session.capture=interrupted_capture
            with pytest.raises(RuntimeError,match='stop after committed owner result'):
                session.run()
            assert 'execution_transport_failure' not in session.state
        else:
            result=session.run()
            assert result['stop_reason']=='execution_action_outcome_requires_owner_check'
            assert 'private startup details' not in json.dumps(result)
            diagnostic=session.state['execution_transport_failure']['diagnostic_ref']
            assert read_json(directory/'failures'/diagnostic)['stderr']=='private startup details'
        assert session.calls.summary()['calls']==1 and actions==['uncertain_action']
    with execution_checkpoint(directory,ipython=LocalTestPython(workspace),
                 transport=lambda *_:pytest.fail('unverified action replayed')) as session:
        assert session.run()['stop_reason']=='execution_action_outcome_requires_owner_check'
        assert session.calls.summary()['calls']==1
    with execution_checkpoint(directory,ipython=LocalTestPython(workspace),transport=lambda role,wire:
                 cognitive() if role=='mind' else reply('wait',{'event_type':'OWNER_EVIDENCE'})) as session:
        result=session.run(owner_event=('OWNER_EVIDENCE','The owner inspected the actual state after the transport failure. Continue from the recorded outcome.'))
        assert 'execution_transport_failure' not in session.state
        assert result['execution_status']=='waiting'
        assert actions==['uncertain_action']

    with execution_checkpoint(directory,ipython=LocalTestPython(workspace),
                 transport=lambda *_:pytest.fail('acknowledged failure reappeared after Wait')) as session:
        after=session.run()
        assert after['stop_reason'] is None
        assert after['execution_status']=='waiting'
        assert 'execution_transport_failure' not in session.state


@pytest.mark.parametrize('role', ['mind', 'builder'])
def test_mind_mode_persists_and_cannot_change_an_unfinished_activity(tmp_path, role):
    workspace=tmp_path/'workspace';workspace.mkdir();directory=tmp_path/'session'
    seen=[]
    def transport(role,wire):
        if role=='mind':
            seen.append(wire)
            assert wire['thinking']=={'type':'disabled'} and wire['temperature']==0
            assert 'output_config' not in wire
            return cognitive()
        return reply('wait',{'event_type':'OWNER_EVIDENCE'})
    with execution_checkpoint(directory,workspace=workspace,goal='Wait for actual observations.',
                 mind_thinking=False,**({'builder_thinking':False} if role=='builder' else {}),
                 ipython=LocalTestPython(workspace),transport=transport) as session:
        before=session.run()
        assert seen and before[role+'_thinking'] is False
    with execution_checkpoint(directory,ipython=LocalTestPython(workspace),transport=lambda *_:pytest.fail('quietrestartcalledmodel')) as session:
        assert session.run()['cost']==before['cost']
        assert session.status()[role+'_thinking'] is False
        session.state['obligation']['status']='pending';session.save()
    with pytest.raises(ValueError,match=role+'_configuration_requires_quiescent_activity'):
        execution_checkpoint(directory,**{role+'_thinking':True},ipython=LocalTestPython(workspace),transport=transport)


def no_tool_response():
    return {'content': [{'type': 'text', 'text': 'Reasoning without an action. ' * 80}],
            'stop_reason': 'max_tokens', 'usage': {'input_tokens': 19, 'output_tokens': 8192}}


@pytest.mark.parametrize('corrected', [True, False])
def test_execution_no_tool_correction_is_one_owner_decision_with_truthful_cost_and_history(tmp_path, corrected):
    from Mind.chain import execution_goal
    from Execution import FileContentEquals
    workspace=tmp_path/'workspace';workspace.mkdir();directory=tmp_path/'session'
    wires=[]
    def transport(role, wire):
        assert role == 'execution';wires.append(wire)
        if len(wires)==1 or not corrected:return no_tool_response()
        assert wire['messages'][-2] == {'role':'assistant','content':no_tool_response()['content']}
        assert 'only protocol correction' in wire['messages'][-1]['content']
        assert wire['messages'][:-2] == wires[0]['messages']
        assert wire['tools'] == wires[0]['tools'] and 'wait' in {t['name'] for t in wire['tools']}
        return reply('wait',{'event_type':'DONE'})
    with Session(directory,workspace=workspace,goal='Wait for an observed input.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        result=session.execution.run_goal(execution_goal(session.state['task']),FileContentEquals('done','yes'))
        assert result.status == ('waiting' if corrected else 'failed')
        assert session.execution.state.decision_count == 1 and list(workspace.iterdir()) == []
        cost=session.calls.summary()
        assert cost['calls']==2 and cost['allocated_output_tokens']==16384
        assert cost['usage']['output_tokens']==8192+(30 if corrected else 8192)
        first,second=[read_json(directory/'calls'/f'{i:04d}.json') for i in (1,2)]
        assert first['response']==no_tool_response()
        assert second['wire']['messages'][-2]['content']==first['response']['content']
        assert second['execution_attempt']['base_wire_sha256']==fingerprint(first['wire'])
        history=session.calls.execution_history(1,session.execution.state.execution_id,session.execution.committed_tool_calls())
        assert [h['call_ref'] for h in history]==(['execution-call:0002'] if corrected else [])
    before={p.name:p.read_bytes() for p in (directory/'calls').glob('*.json')}
    with Session(directory,transport=lambda *_:pytest.fail('terminal decision retried'),ipython=LocalTestPython(workspace)) as session:
        assert session.execution.state.decision_count==1
    assert before=={p.name:p.read_bytes() for p in (directory/'calls').glob('*.json')} and len(wires)==2


@pytest.mark.parametrize('context_changed', [False, True])
def test_execution_no_tool_budget_pause_reuses_original_and_spends_one_remaining_correction(tmp_path, context_changed):
    workspace=tmp_path/'workspace';workspace.mkdir();directory=tmp_path/'session';wires=[]
    def transport(role,wire):
        assert role=='execution';wires.append(wire)
        return no_tool_response() if len(wires)==1 else reply('wait',{'event_type':'DONE'})
    with execution_checkpoint(directory,workspace=workspace,goal='Wait for a later input.',transport=transport,
                 limits={'calls':1,'output_tokens':8192,'request_bytes':150000},ipython=LocalTestPython(workspace)) as session:
        paused=session.run()
        assert paused['stop_reason']=='chain_budget_exhausted' and paused['cost']['calls']==1
        assert session.execution.state.decision_count==0
    original=(directory/'calls/0001.json').read_bytes()
    with execution_checkpoint(directory,transport=transport,ipython=LocalTestPython(workspace)) as session:
        if context_changed:
            projection = session.execution_context
            session.execution_context = lambda count: {**projection(count), 'version': 'test-current-projection'}
            session.execution.shutdown()
            session.open_execution()
        assert session.run()['cost']['calls']==1
        session.extend_budget(1,output_tokens=8192,request_bytes=150000)
        result=session.run()
        assert result['execution_status']=='waiting' and result['cost']['calls']==2
        assert session.execution.state.decision_count==1
        if context_changed:
            correction = read_json(directory/'calls/0002.json')
            assert correction['execution_attempt']['protocol'] == 'execution-correction-v2'
            assert correction['execution_attempt']['original_wire_sha256'] == fingerprint(json.loads(original)['wire'])
            doc = json.loads(correction['wire']['messages'][0]['content'][0]['text'])
            assert doc['decision_context_version'] == 'test-current-projection'
    assert len(wires)==2 and (directory/'calls/0001.json').read_bytes()==original


@pytest.mark.parametrize('failed_attempt,reserved', [(1,False),(2,False),(1,True),(2,True)])
def test_execution_no_tool_provider_exception_never_retries_unknown_attempt_after_restart(tmp_path,failed_attempt,reserved):
    import httpx
    class ProcessLoss(BaseException):pass
    workspace=tmp_path/'workspace';workspace.mkdir();directory=tmp_path/'session';seen=[]
    def transport(role,wire):
        assert role=='execution';seen.append(wire)
        if len(seen)==failed_attempt:
            if reserved:raise ProcessLoss()
            raise httpx.ReadTimeout('unknown received outcome')
        return no_tool_response()
    with execution_checkpoint(directory,workspace=workspace,goal='Wait for input.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        with pytest.raises(ProcessLoss if reserved else httpx.ReadTimeout):session.run()
        assert session.execution.state.decision_count==0
    with execution_checkpoint(directory,transport=lambda *_:pytest.fail('unknown attempt retried'),ipython=LocalTestPython(workspace)) as session:
        result=session.run()
        assert result['stop_reason']=='execution_model_outcome_unknown'
        assert result['cost']['calls']==failed_attempt and session.execution.state.decision_count==0
    assert len(seen)==failed_attempt


@pytest.mark.parametrize('crash_after', [1,2])
def test_execution_no_tool_received_attempt_replays_after_crash_without_another_call(tmp_path,monkeypatch,crash_after):
    workspace=tmp_path/'workspace';workspace.mkdir();directory=tmp_path/'session';seen=[]
    class Crash(BaseException):pass
    def transport(role,wire):
        assert role=='execution';seen.append(wire)
        return no_tool_response() if len(seen)==1 else reply('wait',{'event_type':'DONE'})
    original=Calls.call
    def call(calls,role,wire,**kwargs):
        value=original(calls,role,wire,**kwargs)
        if len(seen)==crash_after:raise Crash()
        return value
    with monkeypatch.context() as patch:
        patch.setattr(Calls,'call',call)
        with execution_checkpoint(directory,workspace=workspace,goal='Wait for input.',transport=transport,
                     ipython=LocalTestPython(workspace)) as session:
            with pytest.raises(Crash):session.run()
    before={p.name:p.read_bytes() for p in (directory/'calls').glob('*.json')}
    with execution_checkpoint(directory,transport=transport,ipython=LocalTestPython(workspace)) as session:
        result=session.run()
        assert result['execution_status']=='waiting' and session.execution.state.decision_count==1
        assert result['cost']['calls']==2
    assert len(seen)==2
    assert all((directory/'calls'/name).read_bytes()==value for name,value in before.items())


@pytest.mark.parametrize('incomplete', [False,True])
def test_execution_tool_blocks_never_receive_no_tool_correction_or_partial_dispatch(tmp_path,incomplete):
    workspace=tmp_path/'workspace';workspace.mkdir();seen=[]
    response=reply('ipython',{'code':'finish'})
    if incomplete:response['stop_reason']='max_tokens'
    else:response['content'].append({'type':'tool_use','id':'control','name':'wait','input':{'event_type':'DONE'}})
    def transport(role,wire):
        assert role=='execution';seen.append(wire);return response
    with Session(tmp_path/'session',workspace=workspace,goal='Preserve the inputs.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        from Mind.chain import execution_goal
        from Execution import FileContentEquals
        result=session.execution.run_goal(execution_goal(session.state['task']),FileContentEquals('done','yes'))
        assert result.status=='failed' and session.calls.summary()['calls']==1
    assert len(seen)==1 and list(workspace.iterdir())==[]


@pytest.mark.parametrize('pause', [False,True,'received'])
def test_execution_no_tool_correction_keeps_guidance_once_and_binds_final_call(tmp_path,monkeypatch,pause):
    workspace=tmp_path/'workspace';workspace.mkdir();directory=tmp_path/'session';seen=[];counts=Counter()
    directive='Preserve the signed receipt while awaiting further evidence.'
    def transport(role,wire):
        counts[role]+=1
        if role=='mind':return cognitive({'type':'directive','text':directive} if counts[role]==1 else None)
        seen.append(wire)
        if counts[role]==1:return reply('wait',{'event_type':'MIND_REVIEW'})
        guidance=[m for m in wire['messages'] if m['role']=='user' and isinstance(m['content'],str)
                  and m['content'].startswith('[Mind Supervisor Directive]')]
        assert len(guidance)==1 and guidance[0]['content'].endswith(directive)
        return no_tool_response() if counts[role]==2 else reply('wait',{'event_type':'OWNER_EVIDENCE'})
    class Crash(BaseException):pass
    original=Calls.call
    def call(calls,role,wire,**kwargs):
        result=original(calls,role,wire,**kwargs)
        if role=='execution' and counts[role]==3:raise Crash()
        return result
    if pause=='received':monkeypatch.setattr(Calls,'call',call)
    with execution_checkpoint(directory,workspace=workspace,goal='Retain the signed receipt.',transport=transport,
                 limits={'calls':5 if pause is True else 30,'output_tokens':400000,'request_bytes':5000000},ipython=LocalTestPython(workspace)) as session:
        if pause=='received':
            with pytest.raises(Crash):session.run()
        else:result=session.run()
        if pause is True:
            assert result['stop_reason']=='feedback_budget_reserved' and counts['execution']==2
            assert session.state['deliveries'][0]['status']=='bound'
    with execution_checkpoint(directory,transport=transport,ipython=LocalTestPython(workspace)) as session:
        if pause is True:session.extend_budget(1,output_tokens=8192,request_bytes=150000)
        result=session.run()
        assert session.execution.state.decision_count==2 and counts['execution']==3
        delivery,=session.state['deliveries']
        assert delivery['call_ref']=='execution-call:0004'
        guidance=session.execution_context(2)['received_guidance']
        assert len(guidance)==1 and guidance[0]['call_ref']=='execution-call:0004'
        cost=result['cost']
    with execution_checkpoint(directory,transport=lambda *_:pytest.fail('quiet guidance restart called provider'),ipython=LocalTestPython(workspace)) as session:
        assert session.run()['cost']==cost
    events=[json.loads(line) for path in (directory/'mind').glob('activation-*.jsonl') if '.native.' not in path.name
            for line in path.read_text(encoding='utf8').splitlines()]
    assert sum(e['event_type']=='MIND_DIRECTIVE_APPLIED' for e in events)==1
