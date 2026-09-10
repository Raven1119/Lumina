from dataclasses import replace
import copy

import pytest

from Nervous.organ import Event, NervousOrgan


def test_registered_trigger_orders_continuations_and_leaves_unknown_pending(tmp_path):
    from Nervous.triggers import Registry, TriggerSpec, DEFAULT_TRIGGERS
    registry = Registry((*DEFAULT_TRIGGERS, TriggerSpec(
        'test.signal', 1, 'test', 'mind', 'test.signal', 2, 'related',
        lambda data: data == {'ready': True})))
    with NervousOrgan(tmp_path, registry=registry) as nervous:
        ordinary = nervous.submit('continue')
        plugin = Event('plugin', 'test', 'mind', 'test.signal', {'ready': True})
        unknown = Event('unknown', 'external', 'mind', 'unregistered', {})
        continuation = Event('result', 'execution', 'mind.results', 'evidence.result', {})
        for event in (plugin, unknown, continuation):
            nervous.publish(event)
        assert [event.event_id for event in nervous.ready()] == [
            continuation.event_id, ordinary.event_id, plugin.event_id]
        assert unknown in nervous.pending('mind')
        assert registry.match(replace(plugin, source='external')) == ()


def test_attention_is_frozen_before_consumer_and_same_event_reuses_it(tmp_path):
    with NervousOrgan(tmp_path) as nervous:
        event = nervous.submit('check the new observation')
        views = {'event': {'ref': event.event_id}, 'execution': {
            'files': [{'file': 'result.txt', 'ref': 'source:a'}]},
            'mind': {'revision': 1}, 'resources': {'calls_left': 3}}
        frame = nervous.freeze_attention(event, views)
        assert frame['policy'] == 'nervous-attention-v1'
        assert frame['views'] == views
        assert frame['selected_refs'] == ['source:a']
    with NervousOrgan(tmp_path) as nervous:
        newer = {**views, 'mind': {'revision': 2}}
        assert nervous.freeze_attention(event, newer) == frame
        assert nervous.pending('mind') == (event,)


def test_existing_execution_trigger_precedence_and_ordinary_round_quiet():
    from Nervous.triggers import execution_reasons
    facts = dict(requests=False, changed_predictions=False, budget_feedback=False,
                 completion_feedback=False, significant_result=False)
    assert execution_reasons(facts) == ()
    reasons = execution_reasons({**facts, 'requests': True, 'changed_predictions': True})
    assert [spec.id for spec in reasons] == ['execution.request', 'prediction.changed']


def test_source_selection_preserves_original_attribution_and_catalogue():
    from Nervous.attention import inline_refs, delivery_sources
    records = [{'ref': 'source:a', 'text': 'observed', 'origin': 'execution'},
               {'ref': 'source:b', 'text': 'x' * 700, 'origin': 'execution'}]
    files = [{'file': 'a', 'ref': 'source:a', 'kind': 'observed_text'},
             {'file': 'b', 'ref': 'source:b', 'kind': 'observed_text'}]
    selected, unread = delivery_sources(files, {r['ref']: r for r in records}, ())
    assert selected == records[:1] and unread == []
    assert inline_refs(files, {r['ref']: r for r in selected}) == ['source:a']


def cognitive_views():
    def header(identity, basis, task=None):
        return {'id': identity, 'kind': 'belief', 'status': 'supported', 'basis_refs': basis,
                'task_ref': task, 'last_revision': 4}

    return {'event': {'ref': 'event:changed', 'related_source_refs': ['source:new']},
            'mind': {'owner': 'mind', 'revision': 4, 'ref': 'mind-view:4', 'content': {
                'authorization_ref': 'owner:scope', 'task': {'id': 'task-b', 'revision': 2},
                'intentions': [{'id': 'intention:main', 'commitment': 'committed',
                                'understanding_refs': ['belief:global']},
                               {'id': 'intention:paused', 'commitment': 'paused',
                                'understanding_refs': ['belief:paused']}],
                'unresolved': ['activity:uncertain'], 'cognitive_catalogue': {'revision': 4, 'items': [
                    header('belief:old-task', ['source:a'], {'id': 'task-a', 'revision': 1}),
                    header('belief:global', ['owner:rule']),
                    header('belief:current-task', ['source:b'], {'id': 'task-b', 'revision': 2}),
                    header('belief:old-version', ['source:old'], {'id': 'task-b', 'revision': 1}),
                    header('belief:changed', ['source:new']),
                    header('belief:paused', ['source:paused']),
                    header('belief:unscoped', ['source:unrelated']),
                ]}}},
            'execution': {'files': [{'ref': 'source:unrelated', 'file': 'short.txt'},
                                    {'ref': 'source:new', 'file': 'observed.json'}]},
            'execution_state': {'content': {'unknown_action': {'event_id': 'action:unknown'},
                                            'control': {'action': 'stop'}}},
            'resources': {'calls_remaining': 6}, 'observed_at': '2026-09-10T10:00:00Z'}


def selected_frame(views, *, recipe='related'):
    from Nervous.attention import frame
    from Nervous.triggers import TriggerSpec

    event = Event('event:changed', 'execution', 'mind', 'execution.changed', {})
    spec = TriggerSpec('test.selection', 1, 'execution', 'mind', 'execution.changed', 2, recipe, lambda _: True)
    return frame(event, views, [spec])


def test_stage1_selects_by_owner_references_without_promoting_unrelated_short_sources():
    views = cognitive_views()
    original = copy.deepcopy(views)
    selected = selected_frame(views)
    assert selected['policy'] == 'nervous-attention-v2'
    assert selected['selected_item_ids'] == ['belief:changed', 'belief:current-task', 'belief:global']
    assert selected['selection_reasons'] == {
        'belief:changed': ['related_source'], 'belief:current-task': ['current_task'],
        'belief:global': ['intention_understanding']}
    assert selected['selected_sources'] == ['owner:rule', 'source:b', 'source:new']
    assert 'source:unrelated' not in selected['selected_refs']
    assert [item['id'] for item in selected['omitted_items']] == [
        'belief:old-task', 'belief:old-version', 'belief:paused', 'belief:unscoped']
    assert all(item['view_ref'] == 'view:mind.cognition?item_ref=' + item['id']
               for item in selected['omitted_items'])
    assert selected['views'] == original == views
    assert selected_frame(views) == selected


def test_selection_records_all_identity_reasons_and_leaves_an_empty_context_legal():
    views = cognitive_views()
    item = views['mind']['content']['cognitive_catalogue']['items'][2]
    item['basis_refs'] = ['source:new']
    views['mind']['content']['intentions'][0]['understanding_refs'].append(item['id'])
    result = selected_frame(views, recipe='owner')
    assert result['selection_reasons'][item['id']] == [
        'intention_understanding', 'current_task', 'related_source']
    views['mind']['content']['cognitive_catalogue']['items'] = []
    assert selected_frame(views)['selected_item_ids'] == []
    assert selected_frame(views)['selected_sources'] == ['source:new']


def test_continuation_recipe_does_not_select_a_second_attention_frame():
    assert selected_frame(cognitive_views(), recipe='continuation') is None
    with pytest.raises(ValueError, match='unsupported_attention_recipe'):
        selected_frame(cognitive_views(), recipe='unregistered_recipe')


def test_selected_scenario_keeps_its_assumptions_without_selecting_other_old_items():
    views = cognitive_views()
    headers = views['mind']['content']['cognitive_catalogue']['items']
    headers.append({'id': 'scenario:current', 'kind': 'scenario', 'status': 'hypothesis',
        'basis_refs': ['source:scenario'], 'dependencies': ['belief:old-task'],
        'task_ref': {'id': 'task-b', 'revision': 2}, 'last_revision': 4})
    result = selected_frame(views)
    assert 'belief:old-task' in result['selected_item_ids']
    assert result['selection_reasons']['belief:old-task'] == ['selected_dependency']
    assert 'source:a' in result['selected_sources']
    assert 'belief:unscoped' not in result['selected_item_ids']
    assert 'belief:old-task' not in [item['id'] for item in result['omitted_items']]
