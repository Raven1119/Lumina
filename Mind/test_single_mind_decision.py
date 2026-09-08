"""Selective cognitive revisions preserve knowledge without a second judge."""
from dataclasses import replace
import json

import pytest

from Mind.chain import ChainMind
from Mind.organ import MindOrgan, MindResultEvent, _thaw
from Mind.test_chain import cognitive, reply
from Mind.test_event_loop import input_value


def belief(identity, claim, status='supported'):
    return dict(kind='belief', id=identity, claim=claim, status=status,
                basis=[{'ref': 'source'}])


def test_prior_assessment_view_does_not_repair_or_rewrite_accepted_cognition(tmp_path):
    # A deliberately inconsistent old record. Only a later model may revise it.
    value = input_value()
    seed = belief('new:earlier', 'The earlier statement was incorrect.', 'contradicted')
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive(updates=[seed]))) as mind:
        assert mind.activate(value).status == 'accepted'
        original = [_thaw(i) for i in mind.inspect().items]
    observed = []
    def inspect_and_keep(wire):
        payload = json.loads(wire['messages'][0]['content'])
        prior, = payload['cognition']['prior_model_judgments']
        assert prior['claim'] == original[0]['claim']
        assert prior['prior_truth'] is False
        assert 'prior_status' not in prior
        observed.append(wire)
        return cognitive()
    with MindOrgan(directory=tmp_path, model=ChainMind(inspect_and_keep)) as mind:
        assert mind.activate(replace(value, event_id='review')).status == 'accepted'
        assert [_thaw(i) for i in mind.inspect().items] == original
    assert len(observed) == 1
    with MindOrgan(directory=tmp_path, model=None) as mind:
        assert [_thaw(i) for i in mind.inspect().items] == original


def test_selective_revision_survives_consultation_restart_and_unrelated_event(tmp_path):
    seed = [belief('new:stable', 'The observed subset covers 24 entries, not the full collection.'),
            belief('new:wrong', 'All entries are settled.'),
            dict(kind='question', id='new:pending', text='Which entries remain unsettled?',
                 status='open', basis=[])]
    value = input_value()
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive(updates=seed))) as mind:
        assert mind.activate(value).status == 'accepted'
        stable, wrong, pending = [_thaw(item) for item in mind.inspect().items]
    snapshot = (tmp_path/'cognition.json').read_bytes()
    wires = []

    def answer(wire):
        wires.append(wire)
        if len(wires) == 1:
            return reply('read_evidence', {'refs': ['source']})
        return cognitive(updates=[{**wrong, 'claim': 'Only settled entries count.'},
                                  {**pending, 'status': 'closed'}])

    event = replace(value, event_id='revision')
    options = dict(directory=tmp_path, available_capabilities=('read_evidence',))
    with MindOrgan(model=ChainMind(answer), **options) as mind:
        waiting = mind.activate(event)
        assert waiting.status == 'waiting'
        assert [_thaw(i) for i in mind.inspect().items] == [stable, wrong, pending]
    with MindOrgan(model=ChainMind(answer), **options) as mind:
        assert mind.activate(event).request == waiting.request
        assert len(wires) == 1
        observed = MindResultEvent(waiting.request.request_ref, {'capability': 'read_evidence',
            'origin': 'execution', 'text': json.dumps({'read_result': 'sources-v1', 'sources': [
                {'ref': 'source', 'origin': 'execution', 'text': 'Only settled entries count.'}]})})
        receipt = mind.accept_result(observed)
        assert receipt.status == 'accepted' and receipt.output['type'] == 'no_change'
        current = {i['id']: _thaw(i) for i in mind.inspect().items}
        assert current[stable['id']] == stable
        assert current[wrong['id']]['claim'] == 'Only settled entries count.'
        assert current[pending['id']]['status'] == 'closed'
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive())) as mind:
        assert mind.activate(replace(value, event_id='unrelated', trigger='No relevant change.')).status == 'accepted'
        assert {i['id']: _thaw(i) for i in mind.inspect().items} == current
    with MindOrgan(directory=tmp_path, model=None) as mind:
        assert {i['id']: _thaw(i) for i in mind.inspect().items} == current
    history = (tmp_path/'cognition.json').read_text(encoding='utf-8')
    assert wrong['claim'] in history and wrong['claim'].encode() in snapshot


def test_explicit_retirement_and_invalid_replacement_are_atomic(tmp_path):
    seed = [belief('new:stable', 'A condition still applies.'), belief('new:obsolete', 'An earlier judgment.')]
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive(updates=seed))) as mind:
        assert mind.activate(input_value()).status == 'accepted'
        stable, obsolete = [_thaw(i) for i in mind.inspect().items]
    received = []

    def answer(wire):
        received.append(wire)
        if len(received) == 1:
            return cognitive(updates=[{**obsolete, 'basis': [{'ref': 'unseen'}], 'status': 'archived'}])
        response = cognitive()
        response['content'][0]['input']['current'] = [stable['id']]
        return response

    with MindOrgan(directory=tmp_path, model=ChainMind(answer)) as mind:
        result = mind.activate(replace(input_value(), event_id='retire'))
        assert result.status == 'accepted'
        assert [_thaw(i) for i in mind.inspect().items] == [stable]
    assert len(received) == 2
    feedback = received[-1]['messages'][-1]['content'][0]
    assert feedback['is_error']
    assert 'rejected_before_commit' in feedback['content']
    assert obsolete['claim'] in (tmp_path/'cognition.json').read_text(encoding='utf-8')


def test_new_profile_preserves_old_full_snapshot_replay(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr('Mind.chain.INTEGRATION_CONTRACT_VERSION', 'cognitive-chain-v65')
        with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive(updates=[
                belief('new:old', 'Historical temporary understanding.')]))) as mind:
            assert mind.activate(input_value()).status == 'accepted'
        with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive())) as mind:
            assert mind.activate(replace(input_value(), event_id='old-clear')).status == 'accepted'
            assert not mind.inspect().items
    trace_bytes = {p.name: p.read_bytes() for p in tmp_path.glob('*.jsonl')}
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: pytest.fail('Replay called model'))) as mind:
        assert not mind.inspect().items
        assert mind.activate(input_value()).status == 'duplicate'
    assert {p.name: p.read_bytes() for p in tmp_path.glob('*.jsonl')} == trace_bytes


@pytest.mark.parametrize('contract', ['cognitive-chain-v66', 'cognitive-chain-v67'])
def test_directive_budget_reaches_execution_and_survives_restart(tmp_path, monkeypatch, contract):
    from Mind.execution_steering_experiment import decision_advisory_for_execution
    from Execution.execution import _decision_advisory_for, _context_with_decision_advisory
    from Mind.trace import MindTrace
    from Mind.organ import cognitive_step_schema
    from jsonschema import Draft202012Validator

    text = 'Preserve the sourced scope and uncertainty. ' * 50
    assert 1000 < len(text.strip()) < 6000
    response = cognitive({'type': 'directive', 'text': text.strip()})
    schema = cognitive_step_schema(['source'], contract=contract)
    errors = list(Draft202012Validator(schema).iter_errors(response['content'][0]['input']))
    assert bool(errors) == (contract == 'cognitive-chain-v66')
    monkeypatch.setattr('Mind.chain.INTEGRATION_CONTRACT_VERSION', contract)
    calls = []
    def answer(wire):
        calls.append(wire)
        return response if len(calls) == 1 or contract.endswith('67') else cognitive()
    with MindOrgan(directory=tmp_path, model=ChainMind(answer)) as mind:
        result = mind.activate(input_value())
        assert result.status == 'accepted'
        if contract.endswith('66'):
            assert len(calls) == 2 and result.output['type'] == 'no_change'
            return
        app = mind.prepare_directive('first', execution_ref='run', decision_id='decision-000001',
                                     intention_ref='task', intention_revision=1)
        advisory = decision_advisory_for_execution(app, execution_ref='run',
                                                   decision_id='decision-000001', contract=contract)
        assert advisory is not None and text.strip() in advisory[1]
        assert decision_advisory_for_execution(app, execution_ref='other',
                                               decision_id='decision-000001', contract=contract) is None
        accepted = _decision_advisory_for(advisory, 'decision-000001')
        request = json.loads(_context_with_decision_advisory('{}', accepted, 16000))
        assert request['mind_supervisor_directive'] == advisory[1]
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: pytest.fail('Restart called model'))) as mind:
        again = mind.prepare_directive('first', execution_ref='run', decision_id='decision-000001',
                                       intention_ref='task', intention_revision=1)
        assert again == app
    path, = [p for p in tmp_path.glob('*.jsonl') if '.native.' not in p.name]
    trace = MindTrace.reopen(path)
    assert sum(e.event_type == 'MIND_DIRECTIVE_APPLIED' for e in trace.events) == 1
