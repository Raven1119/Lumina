"""Persistent pursuit contracts, using scripted inference to test ownership only."""
import json
from dataclasses import replace

import pytest

from Mind.cognition import Cognition, Evidence, MindInput, MindResultEvent
from Mind.model import MindModel
from Mind.test_cognition_core import Script, response, step
from Mind.intention import task_owner
from Mind.task_view import execution_goal
from Mind.trace import MindTrace


AUTHORITY = {'ref': 'owner:research', 'text': 'Investigate the supplied records; choose bounded follow-up work within this workspace.'}


@pytest.fixture
def legacy_current_interface(monkeypatch):
    """Retain the old selection interface in explicitly historical fixtures."""
    original = Cognition._append
    def append(self, record):
        if record['kind'] == 'started':
            record['context'].pop('cognitive_interface', None)
        return original(self, record)
    monkeypatch.setattr(Cognition, '_append', append)


def pursuit_input(event_id='review-1', **extra):
    pursuit = {'version': 'intention-stage1-v1', 'mind_id': 'mind-research',
               'authorization': AUTHORITY, 'task': None, 'task_status': None, **extra}
    return MindInput(event_id, 'Consider the authorized research scope.', 'mind-research', 0,
                     'One bounded review; no current Execution task.', None, None,
                     (Evidence(AUTHORITY['ref'], AUTHORITY['text'], 'execution'),), pursuit=pursuit)


def intention(identity='new:research', base_revision=0, commitment='committed'):
    return {'id': identity, 'base_revision': base_revision,
            'aim': 'Make assessments independently checkable.',
            'why': 'The owner authorized investigation and follow-up.',
            'commitment': commitment, 'origin_refs': [AUTHORITY['ref']],
            'understanding_refs': [], 'influence_refs': []}


def proposal(identity='new:task-a', intention_id='new:research', base_revision=0,
             goal='Check source coverage.', acceptance='Deliver a sourced coverage table.'):
    return {'id': identity, 'base_revision': base_revision, 'intention_id': intention_id,
            'authority_ref': AUTHORITY['ref'], 'goal': goal, 'acceptance': acceptance}


def task_input(event_id, task, status='completed', **extra):
    owner = task_owner(task, 'Return the exact per-task completion marker.')
    return replace(pursuit_input(event_id, task=task, task_status=status, **extra),
                   goal=execution_goal(owner), owner_task=owner)


def test_taskless_nochange_can_commit_pursuit_and_restore_same_mind(tmp_path):
    script = Script(response(step(effects={'intentions': [intention()], 'task': None, 'watches': []})))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        receipt = mind.activate(pursuit_input())
        assert receipt.status == 'accepted'
        assert receipt.output == {'type': 'no_change'}
        saved = mind.pursuit_state()
        assert saved['mind_id'] == 'mind-research'
        assert saved['task'] is None
        record = next(iter(saved['intentions'].values()))
        assert record['aim'] == 'Make assessments independently checkable.'
        assert record['revision'] == 1 and record['commitment'] == 'committed'
        assert receipt.effects['intentions'] == [record]
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        assert mind.pursuit_state() == saved
        duplicate = mind.activate(pursuit_input())
        assert duplicate.status == 'duplicate' and duplicate.effects == receipt.effects
    wire = json.loads(script.wires[0]['messages'][0]['content'])
    assert wire['cognition']['pursuit']['task'] is None
    assert 'execution_goal_sha256' not in wire['cognition']


def test_two_distinct_tasks_keep_mind_and_pursuit_but_not_completion_identity(tmp_path):
    first = step(effects={'intentions': [intention()], 'task': proposal(), 'watches': []},
                 next={'type': 'directive', 'text': 'Establish which sources cover the question and report gaps.'})
    script = Script(response(first))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        receipt = mind.activate(pursuit_input())
        assert receipt.status == 'accepted'
        task_a = receipt.effects['task']
        pursuit_id = receipt.effects['intentions'][0]['id']
    task_b_proposal = proposal('new:task-b', pursuit_id, goal='Resolve the remaining source discrepancy.',
                              acceptance='Deliver the corrected interpretation with limits.')
    script = Script(response(step(effects={'intentions': [], 'task': task_b_proposal, 'watches': []},
        next={'type': 'directive', 'text': 'Resolve the evidenced discrepancy, preserving unknowns.'})))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        accepted = mind.activate(task_input('result-a', task_a))
        assert accepted.status == 'accepted'
        task_b = accepted.effects['task']
        state = mind.pursuit_state()
        assert state['mind_id'] == 'mind-research' and list(state['intentions']) == [pursuit_id]
        assert task_b['id'] != task_a['id'] and task_b['goal'] != task_a['goal']
        assert state['tasks'][task_a['id']] == task_a
        assert task_b['intention_id'] == task_a['intention_id'] == pursuit_id
        assert mind.inspect().revision == 2
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step())))) as mind:
        assert mind.activate(task_input('check-b', task_b, 'running')).status == 'accepted'
        assert mind.pursuit_state()['intentions'][pursuit_id]['revision'] == 1
        assert mind.pursuit_state()['task'] == task_b
        with pytest.raises(ValueError, match='pursuit_task_identity_conflict'):
            mind.activate(task_input('stale-a', task_a))
        forged = task_owner(task_b, 'Return the exact per-task completion marker.')
        forged['business_goal'] = 'A different task without a submitted version.'
        with pytest.raises(ValueError, match='pursuit_task_projection_conflict'):
            mind.activate(replace(task_input('forged-b', task_b), goal=execution_goal(forged), owner_task=forged))


def test_consulted_reason_is_durable_across_later_activities(tmp_path):
    consultation = {'question': 'Which condition changes the research question?',
                    'refs': [AUTHORITY['ref']], 'model_ref': ''}
    update = intention()
    update['origin_refs'] = ['activation:observation']
    script = Script(response(consultation, 'analyze_world_model'),
                    response(step(effects={'intentions': [update], 'task': None, 'watches': []})),
                    response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script),
                   available_capabilities=('analyze_world_model',)) as mind:
        pending = mind.activate(pursuit_input())
        assert pending.status == 'waiting' and mind.pursuit_state()['intentions'] == {}
    observation = {'capability': 'analyze_world_model', 'origin': 'computation',
                   'text': json.dumps({'answer': 'A source-limited explanation remains conditional.'})}
    with Cognition(directory=tmp_path, model=MindModel(script),
                   available_capabilities=('analyze_world_model',)) as mind:
        accepted = mind.accept_result(MindResultEvent(pending.request.request_ref, observation))
        assert accepted.status == 'accepted'
        basis = accepted.effects['intentions'][0]['origin_refs'][0]
        assert basis.startswith('activation-') and basis.endswith(':observation')
        assert mind.read_source(basis)['origin'] == 'computation'
        assert mind.activate(pursuit_input('next-event')).status == 'accepted'
        assert next(iter(mind.pursuit_state()['intentions'].values()))['origin_refs'] == [basis]


@pytest.mark.parametrize('cut', ['native_result', 'accepted'])
def test_pursuit_commit_and_effect_replay_survive_crash_without_resampling(tmp_path, monkeypatch, cut):
    class Crash(BaseException):
        pass
    script = Script(response(step(effects={'intentions': [intention()], 'task': proposal(), 'watches': []},
        next={'type': 'directive', 'text': 'Establish source coverage and its remaining gaps.'})))
    original_append, original_native = Cognition._append, MindTrace.append_native
    hit = False
    def committed(self, record):
        nonlocal hit
        result = original_append(self, record)
        if not hit and cut == 'accepted' and record['kind'] == 'accepted':
            hit = True
            raise Crash()
        return result
    def native(self, **payload):
        nonlocal hit
        result = original_native(self, **payload)
        if not hit and cut == 'native_result' and payload['kind'] == 'result':
            hit = True
            raise Crash()
        return result
    monkeypatch.setattr(Cognition, '_append', committed)
    monkeypatch.setattr(MindTrace, 'append_native', native)
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        with pytest.raises(Crash):
            mind.activate(pursuit_input())
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        accepted = mind.activate(pursuit_input())
        duplicate = mind.activate(pursuit_input())
        assert accepted.status in {'accepted', 'duplicate'}
        assert duplicate.effects == accepted.effects
        assert mind.inspect().revision == 1
        assert len(mind.pursuit_state()['tasks']) == 1
    assert hit and len(script.wires) == 1


@pytest.mark.parametrize('invalid', ['revision', 'basis', 'authority', 'no_direction', 'running_task'])
def test_invalid_effects_do_not_partially_change_knowledge_or_pursuit(tmp_path, invalid):
    first = step(effects={'intentions': [intention()], 'task': proposal(), 'watches': []},
                 next={'type': 'directive', 'text': 'Investigate the source coverage.'})
    with Cognition(directory=tmp_path, model=MindModel(Script(response(first)))) as mind:
        assert mind.activate(pursuit_input()).status == 'accepted'
        before = mind.pursuit_state()
    intended = next(iter(before['intentions'].values()))
    change = intention(intended['id'], 1, 'paused')
    effects = {'intentions': [change], 'task': None, 'watches': []}
    next_action = {'type': 'no_change'}
    if invalid == 'revision':
        change['base_revision'] = 0
    elif invalid == 'basis':
        change['origin_refs'] = ['invented:source']
    else:
        effects['intentions'] = []
        effects['task'] = proposal('new:task-b', intended['id'])
        if invalid != 'no_direction':
            next_action = {'type': 'directive', 'text': 'Address the newly identified source gap.'}
        if invalid == 'authority':
            effects['task']['authority_ref'] = 'invented:authorization'
    submitted = step([{'kind': 'belief', 'id': 'new:partial', 'claim': 'This must not be accepted partially.',
                       'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]}],
                     next=next_action, effects=effects)
    class Raw:
        def generate_from_trace(self, trace, projection):
            return json.dumps(submitted)
    with Cognition(directory=tmp_path, model=Raw()) as mind:
        receipt = mind.activate(task_input('invalid-review', before['task'],
                                          'running' if invalid == 'running_task' else 'completed'))
        assert receipt.status == 'failed' and receipt.effects is None and receipt.output is None
        assert mind.pursuit_state() == before
        assert not mind.inspect().items and mind.inspect().revision == 1


def test_unseen_cognition_survives_visible_current_selection(tmp_path, legacy_current_interface):
    facts = [{'kind': 'belief', 'id': 'new:' + name, 'claim': 'Scoped observation ' + name,
              'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]} for name in ('a', 'b')]
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step(facts))))) as mind:
        assert mind.activate(pursuit_input()).status == 'accepted'
        visible, hidden = [dict(item) for item in mind.inspect().items]
    script = Script(response(step(current=[])))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(pursuit_input('focused-review', visible_item_ids=[visible['id']])).status == 'accepted'
        assert [dict(item) for item in mind.inspect().items] == [hidden]
    actual = json.loads(script.wires[0]['messages'][0]['content'])
    assert [x['id'] for x in actual['cognition']['prior_model_judgments']] == [visible['id']]


def watch(identity='new:watch', intention_id='new:research', base_revision=0, status='active'):
    return {'id': identity, 'base_revision': base_revision, 'spec_id': 'source.changed',
            'spec_version': 1, 'params': {'file': 'observations.json'}, 'intention_id': intention_id,
            'status': status, 'reason': 'New observations may resolve the uncertainty.',
            'origin_refs': [AUTHORITY['ref']]}


def test_watch_and_intention_versions_change_atomically_and_close_together(tmp_path):
    script = Script(response(step(effects={'intentions': [intention()], 'task': None, 'watches': [watch()]})))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        first = mind.activate(pursuit_input())
        assert first.status == 'accepted'
        intended, observed = first.effects['intentions'][0], first.effects['watches'][0]
        assert observed['intention_id'] == intended['id'] and observed['revision'] == 1
    effects = {'intentions': [intention(intended['id'], 1, 'closed')], 'task': None,
               'watches': [watch(observed['id'], intended['id'], 1, 'cancelled')]}
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step(effects=effects))))) as mind:
        result = mind.activate(pursuit_input('close-review'))
        assert result.status == 'accepted' and result.output == {'type': 'no_change'}
        assert result.effects['intentions'][0]['revision'] == 2
        assert result.effects['watches'][0]['revision'] == 2
        assert mind.pursuit_state()['watches'][observed['id']]['status'] == 'cancelled'


def test_same_task_can_be_revised_at_known_wait_boundary(tmp_path):
    first = step(effects={'intentions': [intention()], 'task': proposal(), 'watches': []},
                 next={'type': 'directive', 'text': 'Investigate the source coverage.'})
    with Cognition(directory=tmp_path, model=MindModel(Script(response(first)))) as mind:
        result = mind.activate(pursuit_input())
        task = result.effects['task']
    change = proposal(task['id'], task['intention_id'], 1,
                      goal='Report only the available source subset.', acceptance='Show which subset remains unknown.')
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step(
            effects={'intentions': [], 'task': change, 'watches': []},
            next={'type': 'directive', 'text': 'Limit the report to the available subset and retain the gap.'}))))) as mind:
        revised = mind.activate(task_input('new-condition', task, 'waiting'))
        assert revised.status == 'accepted'
        assert revised.effects['task']['id'] == task['id']
        assert revised.effects['task']['revision'] == 2
        assert revised.effects['task']['acceptance'] != task['acceptance']


def test_attention_and_current_owner_versions_are_frozen_in_native_request(tmp_path):
    frame = {'policy': 'attention-v1', 'event_ref': 'event:review-1',
             'resources': {'calls_remaining': 7}, 'blockers': [], 'selected': ['owner:research']}
    initial = replace(pursuit_input(), attention=frame)
    script = Script(response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(initial).status == 'accepted'
    payload = json.loads(script.wires[0]['messages'][0]['content'])
    assert payload['cognition']['attention'] == frame
    assert payload['goal'] is None
    assert payload['cognition']['execution_task'] is None
    assert Cognition.inspect_directory(tmp_path)['pursuit']['mind_id'] == 'mind-research'


def test_unaccepted_task_proposal_does_not_become_actual_execution_goal(tmp_path):
    first = step(effects={'intentions': [intention()], 'task': proposal(), 'watches': []},
                 next={'type': 'directive', 'text': 'Investigate coverage.'})
    script = Script(response(first), response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        accepted = mind.activate(pursuit_input())
        proposed = accepted.effects['task']
        event = pursuit_input('pending-task', task=proposed, task_status='not_accepted', execution_task=None)
        assert mind.activate(event).status == 'accepted'
    wire = json.loads(script.wires[-1]['messages'][0]['content'])
    assert wire['cognition']['pursuit']['task'] == proposed
    assert wire['cognition']['execution_task'] is None
    assert wire['goal'] is None


def test_taskless_direction_gets_structural_feedback_without_creating_an_action(tmp_path):
    script = Script(response(step(next={'type': 'directive', 'text': 'Start work without a Task contract.'})),
                    response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        result = mind.activate(pursuit_input())
        assert result.status == 'accepted' and result.output == {'type': 'no_change'}
        assert mind.pursuit_state()['task'] is None
    assert len(script.wires) == 2
    feedback = script.wires[1]['messages'][-1]['content'][0]
    assert feedback['is_error'] and 'directive_requires_task' in feedback['content']


def test_long_registered_view_query_continues_original_activity(tmp_path):
    ref = ('view:execution.history?execution_ref=execution-' + 'a' * 32
           + '&event_ref=history:execution-' + 'a' * 32 + ':event-000019&offset=100&limit=6000')
    assert 128 < len(ref) <= 256
    script = Script(response({'refs': [ref]}, 'read_evidence'), response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        pending = mind.activate(pursuit_input())
        assert pending.status == 'waiting' and pending.request.payload['refs'] == (ref,)
        observed = {'capability': 'read_evidence', 'origin': 'execution', 'text': json.dumps({
            'read_result': 'sources-v1', 'sources': [{'ref': 'view-result:history', 'origin': 'execution',
            'text': '{"owner":"execution","content":"retained history range","truncated":true}'}]})}
        assert mind.accept_result(MindResultEvent(pending.request.request_ref, observed)).status == 'accepted'
        assert len(script.wires) == 2 and mind.inspect().revision == 1


@pytest.mark.parametrize('wrong_ref', [False, True])
def test_long_evidence_allowance_requires_exact_authority_identity_and_body(tmp_path, wrong_ref):
    text = 'The original bounded authorization. ' * 40
    authority = {'ref': AUTHORITY['ref'], 'text': text}
    event = pursuit_input(authorization=authority)
    evidence = Evidence('source:other' if wrong_ref else authority['ref'],
                        text if wrong_ref else text + ' Changed.', 'execution')
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        with pytest.raises(ValueError, match='invalid_evidence'):
            mind.activate(replace(event, evidence=(evidence,)))


def test_task_selection_omits_old_claim_and_basis_then_explicit_read_allows_revision(tmp_path):
    first = step(effects={'intentions': [intention()], 'task': proposal(), 'watches': []},
                 next={'type': 'directive', 'text': 'Check the coverage of source A.'})
    with Cognition(directory=tmp_path, model=MindModel(Script(response(first)))) as mind:
        task_a = mind.activate(pursuit_input()).effects['task']
    old_source = Evidence('source:only-a', 'MEASURED ONLY FOR TASK A; it says nothing about B.', 'execution')
    old_claim = {'kind': 'belief', 'id': 'new:only-a', 'claim': 'OLD TASK A CLAIM WITH EXACT LIMITED SCOPE',
                 'status': 'supported', 'basis': [{'ref': old_source.ref}]}
    second = step([old_claim], effects={'intentions': [], 'watches': [],
        'task': proposal('new:b', task_a['intention_id'], goal='Assess source B.', acceptance='Show B-specific limits.')},
        next={'type': 'directive', 'text': 'Assess B using B-specific evidence.'})
    with Cognition(directory=tmp_path, model=MindModel(Script(response(second)))) as mind:
        event_a = task_input('finish-a', task_a)
        task_b = mind.activate(replace(event_a, evidence=(*event_a.evidence, old_source))).effects['task']
        from Nervous.storage import plain
        old = plain(mind.inspect().items[0])
        catalogue = mind.attention_catalogue()
        assert catalogue['items'] == [{'id': old['id'], 'kind': 'belief', 'status': 'supported',
            'basis_refs': [old_source.ref], 'task_ref': {'id': task_a['id'], 'revision': 1}, 'last_revision': 2,
            'preview': {'text': old_claim['claim'], 'truncated': False}}]
        assert old_source.text not in json.dumps(catalogue)
    query = 'view:mind.cognition?item_ref=' + old['id']
    corrected = {**old, 'claim': 'The result remains limited to A; source B needs separate evidence.'}
    script = Script(response({'refs': [query]}, 'read_evidence'),
                    response({'refs': [old_source.ref]}, 'read_evidence'), response(step([corrected])))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        waiting = mind.activate(task_input('check-b', task_b, 'running', visible_item_ids=[]))
        assert waiting.status == 'waiting'
        first_wire = json.dumps(script.wires[0])
        assert old_claim['claim'] not in first_wire and old_source.text not in first_wire
        payload = json.loads(script.wires[0]['messages'][0]['content'])
        assert payload['cognition']['pursuit']['tasks'][task_a['id']] == {
            'id': task_a['id'], 'revision': 1, 'view_ref': 'view:mind.intentions?task_ref=' + task_a['id']}
        assert task_a['goal'] not in first_wire and task_a['acceptance'] not in first_wire
        assert payload['cognition']['pursuit']['task'] == task_b
        view = mind.cognitive_item_view(old['id'])
        from Nervous.storage import canonical, fingerprint
        observation = {'capability': 'read_evidence', 'origin': 'execution', 'text': canonical({
            'read_result': 'sources-v1', 'sources': [{'ref': 'view-result:' + fingerprint(view)[:24],
                'text': canonical(view), 'origin': 'execution'}]})}
        reading_basis = mind.accept_result(MindResultEvent(waiting.request.request_ref, observation))
        assert reading_basis.status == 'waiting'
        assert old_source.text not in canonical(script.wires[-1])
        observation = {'capability': 'read_evidence', 'origin': 'execution', 'text': canonical({
            'read_result': 'sources-v1', 'sources': [dict(mind.read_source(old_source.ref))]})}
        accepted = mind.accept_result(MindResultEvent(reading_basis.request.request_ref, observation))
        assert accepted.status == 'accepted' and not mind.inspect().items[0]['claim'].startswith('OLD')
        assert len(script.wires) == 3
        assert script.wires[1]['messages'][0] == script.wires[0]['messages'][0]
        assert old_source.text in json.dumps(script.wires[2])
        assert mind.read_source(old_source.ref)['text'] == old_source.text
        assert mind.attention_catalogue()['items'][0]['task_ref'] == {'id': task_b['id'], 'revision': 1}


@pytest.mark.parametrize('invalid', ['body', 'revision', 'hash', 'extra_forged'])
def test_cognitive_read_requires_exact_current_owner_result(tmp_path, invalid):
    from Nervous.storage import canonical, fingerprint, plain

    claim = {'kind': 'belief', 'id': 'new:retained', 'claim': 'The original scoped judgment.',
             'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]}
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step([claim]))))) as mind:
        assert mind.activate(pursuit_input()).status == 'accepted'
        old = plain(mind.inspect().items[0])
    query = 'view:mind.cognition?item_ref=' + old['id']
    script = Script(response({'refs': [query]}, 'read_evidence'), response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        pending = mind.activate(pursuit_input('selected-review', visible_item_ids=[]))
        exact = mind.cognitive_item_view(old['id'])
        changed = json.loads(canonical(exact))
        if invalid == 'revision':
            changed['revision'] -= 1
        else:
            changed['content']['item']['claim'] = 'An altered owner judgment.'
        record = lambda view: {'ref': 'view-result:' + fingerprint(view)[:24],
                               'text': canonical(view), 'origin': 'execution'}
        sources = [record(changed)]
        if invalid == 'hash':
            sources[0]['ref'] = record(exact)['ref']
        if invalid == 'extra_forged':
            sources.insert(0, record(exact))
        observed = {'capability': 'read_evidence', 'origin': 'execution',
                    'text': canonical({'read_result': 'sources-v1', 'sources': sources})}
        with pytest.raises(ValueError, match='cognitive_view_identity_conflict'):
            mind.accept_result(MindResultEvent(pending.request.request_ref, observed))
        assert plain(mind.inspect().items[0]) == old and len(script.wires) == 1
        observed['text'] = canonical({'read_result': 'sources-v1', 'sources': [record(exact)]})
        assert mind.accept_result(MindResultEvent(pending.request.request_ref, observed)).status == 'accepted'


def test_owner_rejects_unread_item_mutation_without_native_schema(tmp_path):
    from Nervous.storage import plain

    claim = {'kind': 'belief', 'id': 'new:retained', 'claim': 'The original scoped judgment.',
             'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]}
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step([claim]))))) as mind:
        assert mind.activate(pursuit_input()).status == 'accepted'
        old = plain(mind.inspect().items[0])
    class Raw:
        def generate_from_trace(self, trace, projection):
            return json.dumps(step([{**old, 'status': 'archived'}]))
    with Cognition(directory=tmp_path, model=Raw()) as mind:
        rejected = mind.activate(pursuit_input('unread-review', visible_item_ids=[]))
        assert rejected.status == 'failed' and mind.inspect().revision == 1
        assert plain(mind.inspect().items[0]) == old


def test_attention_catalogue_exposes_dependencies_and_bounded_previews(tmp_path):
    belief = {'kind': 'belief', 'id': 'new:premise', 'claim': 'A scoped premise.',
              'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]}
    scenario = {'kind': 'scenario', 'id': 'new:scenario', 'status': 'active',
                'assumptions': ['new:premise'], 'unknowns': [], 'steps': [
                    {key: 'Conditional scenario detail.' for key in ('state', 'actors', 'action', 'external', 'outcome')}]}
    script = Script(response(step([belief, scenario])), response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(pursuit_input()).status == 'accepted'
        catalogue = mind.attention_catalogue()
        premise, conditional = catalogue['items']
        assert conditional['dependencies'] == [premise['id']]
        assert 'dependencies' not in premise
        assert premise['preview']['text'] == 'A scoped premise.'
        assert 'Conditional scenario detail.' in conditional['preview']['text']
        assert all(len(header['preview']['text']) <= 240 for header in catalogue['items'])
        assert AUTHORITY['text'] not in json.dumps(catalogue)
        assert mind.activate(pursuit_input('dependent-review', visible_item_ids=[
            conditional['id'], *conditional['dependencies']])).status == 'accepted'


@pytest.mark.parametrize('revise', [False, True])
def test_reading_omitted_scenario_preserves_its_dependencies_and_allows_commit(tmp_path, revise):
    from Nervous.storage import canonical, fingerprint, plain

    source = Evidence('source:premise', 'The original observation supporting only this premise.', 'execution')
    unrelated_source = Evidence('source:unrelated', 'UNRELATED OBSERVATION MUST REMAIN OMITTED.', 'execution')
    belief = {'kind': 'belief', 'id': 'new:premise', 'claim': 'The scoped observed premise.',
              'status': 'supported', 'basis': [{'ref': source.ref}]}
    unrelated = {**belief, 'id': 'new:unrelated', 'claim': 'UNRELATED BELIEF MUST REMAIN OMITTED.',
                 'basis': [{'ref': unrelated_source.ref}]}
    scenario = {'kind': 'scenario', 'id': 'new:scenario', 'status': 'active',
                'assumptions': ['new:premise'], 'unknowns': [], 'steps': [
                    {key: 'Conditional scenario detail.' for key in ('state', 'actors', 'action', 'external', 'outcome')}]}
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step([belief, unrelated, scenario]))))) as mind:
        initial = pursuit_input()
        assert mind.activate(replace(initial, evidence=(*initial.evidence, source, unrelated_source))).status == 'accepted'
        premise, hidden, conditional = [plain(item) for item in mind.inspect().items]

    query = 'view:mind.cognition?item_ref=' + conditional['id']
    update = {key: value for key, value in conditional.items() if key != 'analysis_status'}
    update['unknowns'] = ['Whether the observed condition generalizes.']
    script = Script(response({'refs': [query]}, 'read_evidence'), response(step([update] if revise else [])))
    review = pursuit_input('read-omitted-scenario', visible_item_ids=[])
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        pending = mind.activate(review)
        assert pending.status == 'waiting'
        assert source.text not in canonical(script.wires[0])
        view = mind.cognitive_item_view(conditional['id'])
        observation = {'capability': 'read_evidence', 'origin': 'execution', 'text': canonical({
            'read_result': 'sources-v1', 'sources': [{'ref': 'view-result:' + fingerprint(view)[:24],
                'text': canonical(view), 'origin': 'execution'}]})}
        assert mind.accept_result(MindResultEvent(pending.request.request_ref, observation)).status == 'accepted'
        assert len(script.wires) == 2
        assert script.wires[1]['messages'][0] == script.wires[0]['messages'][0]
        assert source.text not in canonical(script.wires[1])
        assert premise['id'] in canonical(script.wires[1])
        assert premise['claim'] not in canonical(script.wires[1])
        assert hidden['claim'] not in canonical(script.wires[1])
        assert unrelated_source.text not in canonical(script.wires[1])
        saved = [plain(item) for item in mind.inspect().items]
        assert saved[:2] == [premise, hidden]
        assert saved[2] == ({**conditional, 'unknowns': update['unknowns']} if revise else conditional)
        assert plain(mind.read_source(source.ref)) == {'ref': source.ref, 'text': source.text, 'origin': source.origin}
    with Cognition(directory=tmp_path, model=MindModel(Script()), available_capabilities=('read_evidence',)) as mind:
        assert [plain(item) for item in mind.inspect().items] == saved
        assert mind.activate(review).status == 'duplicate'


@pytest.mark.parametrize('route', ['event', 'read'])
def test_unselected_basis_keeps_immutable_source_identity(tmp_path, route):
    from Nervous.storage import canonical, plain

    original = {'ref': 'source:only-previous-read', 'text': 'The original scoped observation.', 'origin': 'execution'}
    observed = {'capability': 'read_evidence', 'origin': 'execution',
                'text': canonical({'read_result': 'sources-v1', 'sources': [original]})}
    belief = {'kind': 'belief', 'id': 'new:retained', 'claim': 'The accepted judgment.',
              'status': 'supported', 'basis': [{'ref': original['ref']}]}
    script = Script(response({'refs': [original['ref']]}, 'read_evidence'), response(step([belief])))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        waiting = mind.activate(pursuit_input())
        assert mind.accept_result(MindResultEvent(waiting.request.request_ref, observed)).status == 'accepted'
        old = plain(mind.inspect().items[0])
    changed = {**original, 'text': 'Changed body under the same immutable source identity.'}
    script = Script(response({'refs': [original['ref']]}, 'read_evidence'), response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        event = pursuit_input('hidden-source-review', visible_item_ids=[])
        if route == 'event':
            with pytest.raises(ValueError, match='evidence_identity_conflict'):
                mind.activate(replace(event, evidence=(*event.evidence, Evidence(**changed))))
            assert not script.wires
        else:
            waiting = mind.activate(event)
            observed['text'] = canonical({'read_result': 'sources-v1', 'sources': [changed]})
            rejected = mind.accept_result(MindResultEvent(waiting.request.request_ref, observed))
            assert rejected.status == 'failed' and rejected.error == 'evidence_identity_conflict'
        assert mind.inspect().revision == 1 and plain(mind.inspect().items[0]) == old
        assert mind.read_source(original['ref'])['text'] == original['text']
