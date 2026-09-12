"""Current cognition stays readable and revisable without inlining its evidence."""
import json
import pytest

from Mind.cognition import Cognition, MindResultEvent
from Mind.model import MindModel
from Mind.test_cognition_core import Script, response, step
from Mind.test_intention_core import AUTHORITY, pursuit_input
from Nervous.storage import canonical, plain
from Nervous.views import read_views


def test_large_scenario_can_be_read_revised_and_restored_without_inlining_assumptions(tmp_path):
    beliefs = [{'id': 'new:' + name, 'kind': 'belief', 'claim': name * 3600,
                'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]} for name in ('a', 'b')]
    scenario = {'id': 'new:scenario', 'kind': 'scenario', 'status': 'active',
                'assumptions': [b['id'] for b in beliefs], 'unknowns': [],
                'steps': [{key: 'x' * 200 for key in ('state', 'actors', 'action', 'external', 'outcome')}]}
    script = Script(*(response(step(updates=[belief])) for belief in beliefs))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(pursuit_input()).status == 'accepted'
        assert mind.activate(pursuit_input('second-belief')).status == 'accepted'
        scenario['assumptions'] = [item['id'] for item in mind.inspect().items]
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step(updates=[scenario]))))) as mind:
        assert mind.activate(pursuit_input('scenario')).status == 'accepted'
        saved = mind.inspect().items
        item = plain(saved[-1])
    updated = {key: value for key, value in item.items() if key != 'analysis_status'}
    updated['unknowns'] = ['The later observation is not available yet.']
    query = 'view:mind.cognition?item_ref=' + item['id']
    event = pursuit_input('large-scenario-review', visible_item_ids=[])
    script = Script(response({'refs': [query]}, 'read_evidence'), response(step(updates=[updated])))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        pending = mind.activate(event)
        result = read_views([query], mind=mind, execution=None, attention_view=None)
        assert json.loads(result['observation']['text'])['read_result'] == 'sources-v1'
        assert all(b['claim'] not in result['observation']['text'] for b in beliefs)
        assert mind.accept_result(MindResultEvent(pending.request.request_ref, result['observation'])).status == 'accepted'
        assert mind.inspect().items[:2] == saved[:2]
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        assert mind.activate(event).status == 'duplicate'
        assert mind.inspect().items[-1]['unknowns'] == tuple(updated['unknowns'])


@pytest.mark.parametrize('retire_visible', [False, True])
def test_compact_scenario_does_not_authorize_missing_or_retired_assumptions(retire_visible):
    from Mind.cognition import apply_cognitive_commit
    belief = {'id': 'item-belief', 'kind': 'belief', 'claim': 'Scoped premise.',
              'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]}
    scenario = {'id': 'item-scenario', 'kind': 'scenario', 'status': 'active',
                'assumptions': [belief['id']], 'unknowns': [],
                'steps': [{key: 'Conditional.' for key in ('state', 'actors', 'action', 'external', 'outcome')}]}
    items = {scenario['id']: scenario}
    if retire_visible:
        items[belief['id']] = belief
        updates = [{**belief, 'status': 'archived'}]
    else:
        updates = [{**scenario, 'assumptions': ['item-not-read']}]
    with pytest.raises(ValueError, match='unknown_scenario_assumption'):
        apply_cognitive_commit(items, step(updates=updates), {AUTHORITY['ref']: AUTHORITY},
                               'review', {'items': list(items.values())})


def observation(source):
    return {'capability': 'read_evidence', 'origin': 'execution', 'text': canonical({
        'read_result': 'sources-v1', 'sources': [source]})}


def test_hidden_cognition_with_large_basis_can_be_read_revised_and_restored(tmp_path):
    sources = [{'ref': 'source:a', 'text': 'a' * 6000, 'origin': 'execution'},
               {'ref': 'source:b', 'text': 'b' * 6000, 'origin': 'execution'}]
    old = {'id': 'new:question', 'kind': 'question', 'text': 'Does the later result prove the earlier cause?',
           'status': 'open', 'basis': [{'ref': s['ref']} for s in sources]}
    script = Script(*(response({'refs': [s['ref']]}, 'read_evidence') for s in sources),
                    response(step(updates=[old])))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        receipt = mind.activate(pursuit_input())
        for source in sources:
            assert receipt.status == 'waiting'
            receipt = mind.accept_result(MindResultEvent(receipt.request.request_ref, observation(source)))
        assert receipt.status == 'accepted'
        identity = mind.inspect().items[0]['id']
        catalogue = mind.attention_catalogue()
        assert catalogue['items'][0]['preview'] == {'text': old['text'], 'truncated': False}
        assert all(s['text'] not in canonical(catalogue) for s in sources)
    updated = {**old, 'id': identity, 'text': 'The original cause remains unestablished.',
               'basis': [{'ref': AUTHORITY['ref']}]}
    event = pursuit_input('later-review', visible_item_ids=[])
    script = Script(response({'refs': ['view:mind.cognition?item_ref=' + identity]}, 'read_evidence'),
                    response(step(updates=[updated])))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=('read_evidence',)) as mind:
        receipt = mind.activate(event)
        result = read_views(['view:mind.cognition?item_ref=' + identity],
                            mind=mind, execution=None, attention_view=None)
        assert json.loads(result['observation']['text'])['read_result'] == 'sources-v1'
        assert all(s['text'] not in result['observation']['text'] for s in sources)
        assert mind.accept_result(MindResultEvent(receipt.request.request_ref, result['observation'])).status == 'accepted'
        assert mind.inspect().items[0]['text'] == updated['text']
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        assert mind.activate(event).status == 'duplicate'
        assert mind.inspect().items[0]['text'] == updated['text']
        for source in sources:
            assert dict(mind.read_source(source['ref'])) == source


def test_new_pursuit_commit_has_one_update_path_and_explicit_retirement(tmp_path):
    claim = {'id': 'new:fact', 'kind': 'belief', 'claim': 'The observation has limited scope.',
             'status': 'supported', 'basis': [{'ref': AUTHORITY['ref']}]}
    script = Script(response(step(updates=[claim])))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(pursuit_input()).status == 'accepted'
        schema = script.wires[0]['tools'][0]['input_schema']
        assert 'current' not in schema['properties']
        identity = mind.inspect().items[0]['id']
    script = Script(response(step(updates=[{**claim, 'id': identity, 'status': 'archived'},
                                          {**claim, 'id': 'new:replacement'}])))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(pursuit_input('replace')).status == 'accepted'
        assert len(mind.inspect().items) == 1
        assert mind.inspect().items[0]['id'] != identity
