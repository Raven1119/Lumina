"""Whole-commit pursuit invariants through native preflight and durable cognition."""
from dataclasses import replace
import json

import pytest

from Mind.cognition import Cognition
from Mind.model import MindModel
from Mind.test_cognition_core import Script, response, step
from Mind.test_intention_core import (AUTHORITY, intention, proposal, pursuit_input, task_input,
                                      legacy_current_interface)


def effects(*intentions):
    return {'intentions': list(intentions), 'task': None, 'watches': []}


@pytest.mark.parametrize('status', ['running', 'waiting'])
def test_closing_intention_with_live_accepted_task_is_rejected_before_commit(tmp_path, status):
    initial = step(effects={**effects(intention()), 'task': proposal()},
                   next={'type': 'directive', 'text': 'Assess the authorized source coverage.'})
    with Cognition(directory=tmp_path, model=MindModel(Script(response(initial)))) as mind:
        receipt = mind.activate(pursuit_input())
        task = receipt.effects['task']
        identity = task['intention_id']
    script = Script(response(step(effects=effects(intention(identity, 1, 'closed')))), response(step()))
    event = replace(task_input('close-active', task, status), execution_ref='run-a', execution_status=status)
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(event).status == 'accepted'
        assert mind.pursuit_state()['intentions'][identity]['commitment'] == 'committed'
        assert len(script.wires) == 2
        assert 'closed_intention_has_active_task' in str(script.wires[-1])
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        assert mind.activate(event).status == 'duplicate'
        assert mind.pursuit_state()['intentions'][identity]['commitment'] == 'committed'


@pytest.mark.parametrize('retirement', ['current', 'archived'])
@pytest.mark.parametrize('read_hidden', [False, True])
def test_retiring_understanding_requires_same_commit_intention_repair(tmp_path, retirement, read_hidden, request):
    if retirement == 'current':
        request.getfixturevalue('legacy_current_interface')
    belief = {'id': 'new:rule', 'kind': 'belief', 'claim': 'The supplied rule has bounded scope.',
              'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]}
    linked = {**intention(), 'understanding_refs': ['new:rule']}
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step(
            updates=[belief], effects=effects(linked)))))) as mind:
        assert mind.activate(pursuit_input()).status == 'accepted'
        record = next(iter(mind.pursuit_state()['intentions'].values()))
        item_id, = record['understanding_refs']
    retire = {'current': []} if retirement == 'current' else {
        'updates': [{**belief, 'id': item_id, 'status': 'archived'}]}
    fixed = step(**retire, effects=effects(intention(record['id'], 1)))
    read = [response({'refs': ['view:mind.cognition?item_ref=' + item_id]}, 'read_evidence')] if read_hidden else []
    script = Script(*read, response(step(**retire)), response(fixed))
    event = pursuit_input('retire-rule', **({'visible_item_ids': []} if read_hidden else {}))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        result = mind.activate(event)
        if read_hidden:
            from Mind.cognition import MindResultEvent
            from Nervous.storage import canonical, fingerprint
            assert result.status == 'waiting' and mind.inspect().items
            view = mind.cognitive_item_view(item_id)
            observation = {'capability': 'read_evidence', 'origin': 'execution', 'text': canonical({
                'read_result': 'sources-v1', 'sources': [{'ref': 'view-result:' + fingerprint(view)[:24],
                    'text': canonical(view), 'origin': 'execution'}]})}
            result = mind.accept_result(MindResultEvent(result.request.request_ref, observation))
        assert result.status == 'accepted'
        assert len(script.wires) == 2 + read_hidden
        assert 'unknown_pursuit_understanding' in str(script.wires[-1])
        assert not mind.inspect().items
        assert mind.pursuit_state()['intentions'][record['id']]['understanding_refs'] == []
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        assert mind.activate(event).status == 'duplicate'
        assert not mind.inspect().items
        assert len(mind.history_segments()) == 2


@pytest.mark.parametrize('status,commitment', [('completed', 'closed'), ('failed', 'closed'), ('waiting', 'paused')])
def test_settled_task_closure_and_live_task_pause_remain_legal(tmp_path, status, commitment):
    initial = step(effects={**effects(intention()), 'task': proposal()},
                   next={'type': 'directive', 'text': 'Assess source coverage.'})
    with Cognition(directory=tmp_path, model=MindModel(Script(response(initial)))) as mind:
        task = mind.activate(pursuit_input()).effects['task']
    update = intention(task['intention_id'], 1, commitment)
    script = Script(response(step(effects=effects(update))))
    event = replace(task_input('settled-or-paused', task, status), execution_ref='run-a', execution_status=status)
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(event).status == 'accepted'
        assert len(script.wires) == 1
        assert mind.pursuit_state()['intentions'][task['intention_id']]['commitment'] == commitment
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        assert mind.activate(event).status == 'duplicate'


def test_real_execution_status_outweighs_unaccepted_proposal_status(tmp_path):
    initial = step(effects={**effects(intention()), 'task': proposal()},
                   next={'type': 'directive', 'text': 'Assess source coverage.'})
    with Cognition(directory=tmp_path, model=MindModel(Script(response(initial)))) as mind:
        task = mind.activate(pursuit_input()).effects['task']
    script = Script(response(step(effects=effects(intention(task['intention_id'], 1, 'closed')))), response(step()))
    event = replace(task_input('not-accepted-proposal', task, 'not_accepted'),
                    execution_ref='actual-run', execution_status='waiting')
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(event).status == 'accepted'
        assert len(script.wires) == 2
        assert mind.pursuit_state()['intentions'][task['intention_id']]['commitment'] == 'committed'


def test_hidden_understanding_is_not_mistaken_for_retired_knowledge(tmp_path, legacy_current_interface):
    belief = {'id': 'new:rule', 'kind': 'belief', 'claim': 'Retain this scoped understanding.',
              'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]}
    linked = {**intention(), 'understanding_refs': ['new:rule']}
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step(
            updates=[belief], effects=effects(linked)))))) as mind:
        mind.activate(pursuit_input())
        original = mind.pursuit_state()
        original_items = mind.inspect().items
    script = Script(response(step(current=[])))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(pursuit_input('hidden', visible_item_ids=[])).status == 'accepted'
        assert len(script.wires) == 1
        assert mind.pursuit_state() == original and mind.inspect().items == original_items


def test_legacy_success_and_pending_activity_recover_without_new_rule_reinterpretation(tmp_path, monkeypatch):
    # Reproduce the old writer's exact absence of a commit rule marker. This is
    # explicitly a legacy fixture, not a new model producing valid new behavior.
    original_append = Cognition._append
    def legacy_append(self, record):
        if record['kind'] == 'started':
            record['context'].pop('commit_integrity', None)
        return original_append(self, record)
    with monkeypatch.context() as patch:
        patch.setattr(Cognition, '_append', legacy_append)
        initial = step(effects={**effects(intention()), 'task': proposal()},
                       next={'type': 'directive', 'text': 'Assess source coverage.'})
        with Cognition(directory=tmp_path, model=MindModel(Script(response(initial)))) as mind:
            task = mind.activate(pursuit_input()).effects['task']
        event = replace(task_input('legacy-close', task, 'running'), execution_ref='run-a', execution_status='running')
        script = Script(response(step(effects=effects(intention(task['intention_id'], 1, 'closed')))),
                        response({'refs': [AUTHORITY['ref']]}, 'read_evidence'))
        with Cognition(directory=tmp_path, model=MindModel(script),
                       available_capabilities=('read_evidence',)) as mind:
            assert mind.activate(event).status == 'accepted'
            pending = mind.activate(replace(event, event_id='legacy-pending'))
            assert pending.status == 'waiting'
    frozen = {p: p.read_bytes() for p in tmp_path.glob('*.jsonl')}
    from Mind.cognition import MindResultEvent
    observation = {'capability': 'read_evidence', 'origin': 'execution',
                   'text': json.dumps({'read_result': 'sources-v1', 'sources': [
                       {**AUTHORITY, 'origin': 'execution'}]})}
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step()))),
                   available_capabilities=('read_evidence',)) as mind:
        assert mind.pursuit_state()['intentions'][task['intention_id']]['commitment'] == 'closed'
        assert mind.accept_result(MindResultEvent(pending.request.request_ref, observation)).status == 'accepted'
    assert all(path.read_bytes().startswith(body) for path, body in frozen.items())
    final = {p: p.read_bytes() for p in tmp_path.glob('*.jsonl')}
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        assert mind.activate(event).status == 'duplicate'
    assert all(path.read_bytes() == body for path, body in final.items())


@pytest.mark.parametrize('defect', ['close_task', 'retire_understanding'])
def test_final_owner_rejects_incomplete_commit_without_native_preflight(tmp_path, defect):
    belief = {'id': 'new:rule', 'kind': 'belief', 'claim': 'The rule has bounded scope.',
              'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]}
    initial = step(updates=[belief], effects={
        **effects({**intention(), 'understanding_refs': ['new:rule']}), 'task': proposal()},
        next={'type': 'directive', 'text': 'Assess source coverage.'})
    with Cognition(directory=tmp_path, model=MindModel(Script(response(initial)))) as mind:
        task = mind.activate(pursuit_input()).effects['task']
        before, pursuit = mind.inspect(), mind.pursuit_state()
    closed = {**intention(task['intention_id'], 1, 'closed'),
              'understanding_refs': pursuit['intentions'][task['intention_id']]['understanding_refs']}
    submission = step(effects=effects(closed)) if defect == 'close_task' else step(
        updates=[{**belief, 'id': before.items[0]['id'], 'status': 'archived'}])
    class RawModel:
        def generate_from_trace(self, trace, projection):
            return json.dumps(submission)
    event = replace(task_input('invalid-final', task, 'running'), execution_ref='run-a', execution_status='running')
    with Cognition(directory=tmp_path, model=RawModel()) as mind:
        rejected = mind.activate(event)
        assert rejected.status == 'failed' and rejected.output is None
        assert rejected.error == 'invalid_cognitive_update'
        assert mind.inspect() == before and mind.pursuit_state() == pursuit
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        assert mind.inspect() == before and mind.pursuit_state() == pursuit
