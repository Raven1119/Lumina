"""A current cognitive request survives projection without becoming a second goal."""
from dataclasses import replace
import json
import pytest

from Mind.chain import ChainMind
from Mind.execution_checkpoint_fixture import execution_checkpoint
from Mind.organ import Evidence, MindOrgan, MindResultEvent, _apply_updates, _thaw
from Mind.test_chain import LocalTestPython, cognitive, reply
from Mind.test_event_loop import input_value


def test_complete_understanding_replaces_current_state_not_history(tmp_path, monkeypatch):
    # Historical V65 snapshot behavior remains part of the replay contract.
    monkeypatch.setattr('Mind.chain.INTEGRATION_CONTRACT_VERSION', 'cognitive-chain-v65')
    seed = [dict(kind='belief', id='new:old', claim='An old interpretation.', status='open', basis=[]),
            dict(kind='belief', id='new:good', claim='The settled rule.', status='supported', basis=[{'ref': 'source'}])]
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive(updates=seed), thinking=False)) as mind:
        assert mind.activate(input_value()).status == 'accepted'
        old, good = [_thaw(i) for i in mind.inspect().items]
    corrected = {**good, 'claim': 'Only settled entries count.'}
    model = ChainMind(lambda _: cognitive(updates=[corrected]), thinking=False)
    with MindOrgan(directory=tmp_path, model=model) as mind:
        assert mind.activate(replace(input_value(), event_id='replacement')).status == 'accepted'
        assert [_thaw(i) for i in mind.inspect().items] == [corrected]
    with MindOrgan(directory=tmp_path, model=None) as mind:
        assert [_thaw(i) for i in mind.inspect().items] == [corrected]
    assert old['claim'] in (tmp_path/'cognition.json').read_text(encoding='utf-8')


def test_snapshot_failure_is_atomic_and_old_delta_semantics_are_unchanged():
    item = dict(kind='belief', id='item-old', claim='A known rule.', status='supported', basis=[{'ref': 'source'}])
    state = {item['id']: item}
    assert _apply_updates(state, [], {}, 'next', contract='cognitive-chain-v60') == state
    assert _apply_updates(state, [], {}, 'next', contract='cognitive-chain-v61') == {}
    with pytest.raises(ValueError, match='ungrounded_basis'):
        _apply_updates(state, [item], {}, 'next', contract='cognitive-chain-v61')
    assert state == {'item-old': item}


@pytest.mark.parametrize('origin', ['execution', 'computation', 'memory'])
def test_exact_source_origin_survives_without_metadata_callback(tmp_path, origin):
    wires = []
    def answer(wire):
        wires.append(wire)
        return cognitive()
    task = {'business_goal': 'Evaluate the observation.', 'execution_protocol': 'Internal protocol.'}
    from Mind.task_view import execution_goal
    value = replace(input_value(), goal=execution_goal(task), owner_task=task,
                    evidence=(Evidence('source', 'A source observation.', origin),))
    with MindOrgan(directory=tmp_path, model=ChainMind(answer, thinking=False)) as mind:
        assert mind.activate(value).status == 'accepted'
    source = next(r for r in json.loads(wires[0]['messages'][0]['content'])['source_records'] if r['ref'] == 'source')
    assert source['origin'] == origin


def test_sources_precede_prior_judgments_and_owner_goal_is_not_repeated(tmp_path):
    goal = 'Retain the authorized scope and deliver its measured result.'
    task = {'business_goal': goal, 'execution_protocol': 'Internal completion procedure.'}
    from Mind.task_view import execution_goal
    value = replace(input_value(), goal=execution_goal(task), owner_task=task)
    prior = {'kind': 'belief', 'id': 'new:old', 'claim': 'Prior explanation to re-evaluate.',
             'status': 'open', 'basis': []}
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive(updates=[prior]), thinking=False)) as mind:
        assert mind.activate(value).status == 'accepted'
    wires = []
    def answer(wire):
        wires.append(wire)
        return cognitive()
    with MindOrgan(directory=tmp_path, model=ChainMind(answer, thinking=False)) as mind:
        assert mind.activate(replace(value, event_id='reassess', trigger='Reconsider the current explanation.')).status == 'accepted'
    body = wires[0]['messages'][0]['content']
    payload = json.loads(body)
    assert body.count(goal) == 1
    assert payload['current_activity'] == 'Reconsider the current explanation.'
    assert body.index('Only settled entries count.') < body.index(prior['claim'])
    assert payload['goal']['text'] == goal
    assert payload['goal']['ref'].startswith('owner-task:')
    assert payload['cognition']['task_view']['owner_task_sha256']
    assert payload['cognition']['prior_model_judgments'][0]['prior_truth'] is None
    assert 'Internal completion procedure.' not in body


def test_owner_request_is_the_current_activity_and_persists_before_publication(tmp_path, monkeypatch):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'; wires = []
    def transport(role, wire):
        if role == 'mind':
            wires.append(wire)
            return cognitive()
        state = json.loads(wire['messages'][0]['content'][0]['text'])['state']
        return reply('ipython', {'code': 'finish'}) if state['decision_count'] == 0 else reply('claim_complete', {})
    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with execution_checkpoint(workspace=workspace, goal='Deliver the checked result.', **options) as session:
        session.run()
        session.accept_owner_input(('OWNER_REVIEW', 'Inputs are unchanged. Reassess whether the earlier interpretation is warranted.'))
        head, snapshot = session.capture()
        expected_ref = snapshot['owner_events'][-1]['ref']
        def crash(*_):
            raise RuntimeError('publication_cut')
        with monkeypatch.context() as patch:
            patch.setattr(session.nervous, 'publish', crash)
            try:
                session.enqueue(head, snapshot)
            except RuntimeError as error:
                assert str(error) == 'publication_cut'
        saved = session.state['obligation']['activation_data']
        assert 'Reassess whether' in saved['trigger'] and expected_ref in saved['trigger']
        assert session.state['obligation']['event_source'] == 'user'
    before = len(wires)
    with execution_checkpoint(**options) as session:
        session.run()
        assert session.state['obligation']['activation_data'] == saved
        assert session.execution.state.status == 'completed'
    assert len(wires) == before + 1
    payload = json.loads(wires[-1]['messages'][0]['content'])
    assert payload['current_activity'] == saved['trigger']
    source = next(r for r in payload['source_records'] if r['ref'] == expected_ref)
    assert source['kind'] == 'owner_statement'


def test_reassessment_consultation_restarts_with_same_purpose_and_prior_knowledge(tmp_path):
    wires = []
    purpose = 'Reconsider this interpretation using the available measurement.'
    def answer(wire):
        wires.append(wire)
        return (cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': ['source']}, wire=wire)
                if len(wires) == 1 else cognitive())
    value = replace(input_value(), trigger=purpose)
    options = dict(directory=tmp_path, available_capabilities=('read_evidence',))
    with MindOrgan(model=ChainMind(answer, thinking=False), **options) as mind:
        receipt = mind.activate(value)
        assert receipt.status == 'waiting' and mind.inspect().revision == 0
    with MindOrgan(model=ChainMind(answer, thinking=False), **options) as mind:
        result = mind.accept_result(MindResultEvent(receipt.request.request_ref, {
            'capability': 'read_evidence', 'origin': 'execution',
            'text': json.dumps({'read_result': 'sources-v1', 'sources': [
                {'ref': 'source', 'text': 'Only settled entries count.', 'origin': 'execution'}]})}))
        assert result.status == 'accepted' and mind.inspect().revision == 1
    assert json.loads(wires[0]['messages'][0]['content'])['current_activity'] == purpose
    assert wires[1]['messages'][0] == wires[0]['messages'][0]
    assert len([m for m in wires[1]['messages'] if m['role'] == 'assistant']) == 1


def test_mind_effort_is_durable_and_cannot_change_during_an_activity(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    options = dict(directory=tmp_path/'session', ipython=LocalTestPython(workspace),
                   transport=lambda role, _: cognitive() if role == 'mind' else reply('wait', {'event_type': 'OWNER_EVIDENCE'}))
    with execution_checkpoint(workspace=workspace, goal='Interpret the available evidence.', mind_effort='high', **options) as session:
        assert session.status()['mind_effort'] == 'high'
        session.execution.resume()
        head, snapshot = session.capture()
        session.enqueue(head, snapshot)
    with pytest.raises(ValueError, match='quiescent_activity'):
        with execution_checkpoint(mind_effort='low', **options):
            pass
    with execution_checkpoint(**options) as session:
        assert session.status()['mind_effort'] == 'high'
        session.run()
        records = [json.loads(p.read_text()) for p in (tmp_path/'session/calls').glob('*.json')]
        wire = next(r['wire'] for r in records if r['role'] == 'mind')
        assert wire['output_config'] == {'effort': 'high'}


def test_three_consultations_append_only_new_evidence_across_restarts(tmp_path):
    from Mind.task_view import execution_goal
    task = {'business_goal': 'Reassess the measured scope.', 'execution_protocol': 'Internal procedure.'}
    initial = tuple(Evidence(f'e-{i}', f'original-{i}:' + '\u89c2\u6d4b'*400, 'execution') for i in range(36))
    value = replace(input_value(), goal=execution_goal(task), owner_task=task, evidence=initial)
    wires = []
    def answer(wire):
        wires.append(wire)
        if len(wires) <= 3:
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': [f'read-{len(wires)}']}, wire=wire)
        return cognitive(updates=[dict(kind='belief', id='new:observed', claim='The third observation is available.',
            status='supported', basis=[{'ref': 'read-3'}])])
    options = dict(directory=tmp_path, available_capabilities=('read_evidence',))
    with MindOrgan(model=ChainMind(answer, thinking=False), **options) as mind:
        receipt = mind.activate(value)
    for i in range(1, 4):
        assert receipt.status == 'waiting'
        with MindOrgan(model=ChainMind(answer, thinking=False), **options) as mind:
            assert mind.inspect().revision == 0
            receipt = mind.accept_result(MindResultEvent(receipt.request.request_ref, {
                'capability': 'read_evidence', 'origin': 'execution',
                'text': json.dumps({'read_result': 'sources-v1', 'sources': [
                    {'ref': f'read-{i}', 'text': f'fresh-{i}:' + 'r'*6000, 'origin': 'execution'}]})}))
    assert receipt.status == 'accepted' and len(wires) == 4
    with MindOrgan(model=None, **options) as mind:
        assert mind.inspect().revision == 1 and len(mind.inspect().items) == 1
    wire_text = json.dumps(wires[-1], ensure_ascii=False)
    assert wire_text.count(task['business_goal']) == 1
    assert wire_text.count(initial[0].text) == 1
    assert len(wire_text.encode('utf-8')) < 240000
    for i in range(1, 4):
        assert wire_text.count(f'fresh-{i}:' + 'r'*6000) == 1
    assert wires[3]['messages'][:len(wires[2]['messages'])] == wires[2]['messages']
