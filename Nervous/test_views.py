"""Registered owner views reuse the existing immutable read-evidence result."""
import json

import pytest

from Nervous.views import query_descriptors, query_spec, read_views


class ExecutionViews:
    def __init__(self):
        self.calls = []
        self.environment_calls = 0

    def view(self, name, *, execution_ref=None, offset=0, limit=4):
        self.calls.append((name, execution_ref, offset, limit))
        return {'owner': 'execution', 'ref': 'owner:page-1', 'revision': 4,
                'scope': {'execution_ref': execution_ref, 'sampling': 'saved_owner_state'},
                'content': [{'ref': 'action:7', 'text': 'The executed command returned code 0.'}],
                'missing': False, 'truncated': True, 'next_offset': 1}

    def refresh_environment(self):
        self.environment_calls += 1
        return {'state_version': 4, 'execution_ref': 'run-1', 'catalogue_ref': 'sources:3',
                'files': [{'file': 'result.txt', 'ref': 'source:observed-result', 'chars': 6}]}


class MindViews:
    def intentions_view(self):
        return {'owner': 'mind', 'ref': 'intentions:1', 'revision': 2,
                'scope': 'mind-1', 'content': {'research': {'commitment': 'committed'}},
                'missing': False, 'truncated': False}


def attention_view(*, offset=0, limit=4):
    return {'owner': 'nervous', 'ref': f'attention:{offset}:{limit}', 'revision': 5,
            'scope': 'pending signals', 'content': {'pending': ['event:4']},
            'missing': False, 'truncated': False}


def read(refs, execution=None, **kwargs):
    return read_views(refs, mind=MindViews(), execution=execution or ExecutionViews(),
                      attention_view=attention_view, **kwargs)


def test_saved_history_query_returns_owned_versioned_evidence_not_alias_fact():
    execution = ExecutionViews()
    alias = 'view:execution.history?execution_ref=run-1&offset=3&limit=2'
    result = read([alias], execution)
    assert execution.calls == [('execution.history', 'run-1', 3, 2)]
    assert execution.environment_calls == 0
    source, = json.loads(result['observation']['text'])['sources']
    assert source['ref'].startswith('view-result:')
    assert source['ref'] != alias
    assert result['aliases'] == {alias: source['ref']}
    owner_view = json.loads(source['text'])
    assert owner_view['owner'] == 'execution'
    assert owner_view['revision'] == 4
    assert owner_view['truncated'] is True
    assert owner_view['next_offset'] == 1
    assert result['records'][0]['source_kind'] == 'owner_view'


def test_all_registered_queries_preserve_owner_identity_and_refresh_is_explicit():
    execution = ExecutionViews()
    result = read(['view:execution.state', 'view:mind.intentions',
                   'view:nervous.attention?offset=0&limit=2'], execution)
    sources = json.loads(result['observation']['text'])['sources']
    assert {json.loads(source['text'])['owner'] for source in sources} == {'execution', 'mind', 'nervous'}
    assert execution.environment_calls == 0
    environment = read(['view:execution.environment'], execution)
    source, = json.loads(environment['observation']['text'])['sources']
    view = json.loads(source['text'])
    assert execution.environment_calls == 1
    assert view['scope']['sampling'] == 'explicit_environment_refresh'
    assert view['revision'] == 'sources:3'  # environment version, not the unchanged Run checkpoint
    assert view['content']['files'][0]['ref'] == 'source:observed-result'
    assert set(item['ref'] for item in query_descriptors()) == {
        'view:execution.state', 'view:execution.history', 'view:execution.environment',
        'view:mind.intentions', 'view:mind.cognition', 'view:nervous.attention'}


def test_saved_view_and_ordinary_evidence_share_one_result_without_relabelling():
    original = {'ref': 'model:conditional', 'text': 'Conditional calculation, not observed.',
                'origin': 'computation', 'source_kind': 'conditional_prediction', 'label': 'Model 1'}
    result = read(['view:mind.intentions', original['ref']], normal_read=lambda ref: original)
    sources = json.loads(result['observation']['text'])['sources']
    assert sources[-1] == {key: original[key] for key in ('ref', 'text', 'origin')}
    assert result['records'][-1]['source_kind'] == 'conditional_prediction'
    assert set(result['aliases']) == {'view:mind.intentions'}


def test_same_owner_view_has_same_immutable_ref_and_capacity_is_not_success():
    assert read(['view:mind.intentions']) == read(['view:mind.intentions'])
    large = {'ref': 'source:large', 'text': 'x' * 8000, 'origin': 'execution', 'label': 'Large input'}
    result = read(['view:mind.intentions', large['ref']], normal_read=lambda ref: large)
    capacity = json.loads(result['observation']['text'])
    assert capacity['read_result'] == 'capacity-v1'
    assert capacity['status'] == 'not_read'
    assert capacity['required_text_chars'] > capacity['max_text_chars']
    assert result['records'] == []
    assert result['aliases'] == {}
    assert capacity['sources'][-1]['ref'] == 'source:large'


@pytest.mark.parametrize('alias', [
    'view:not-registered', 'view:execution.state?offset=0',
    'view:execution.history?offset=1&offset=2', 'view:execution.history?limit=0',
    'view:execution.history?limit=9', 'view:execution.history?offset=-1',
    'view:execution.history?offset=1.5', 'view:execution.history?execution_ref=',
    'view:mind.intentions?execute=1', 'view:execution.environment?refresh=false',
    'view:execution.state#hidden', 'view:execution.history?',
])
def test_invalid_aliases_are_rejected_before_environment_refresh(alias):
    execution = ExecutionViews()
    with pytest.raises(ValueError, match='unsupported_view_query'):
        read(['view:execution.environment', alias], execution)
    assert execution.environment_calls == 0
    assert execution.calls == []


def test_owner_failure_remains_a_failure_and_is_not_an_absent_fact():
    class Broken(ExecutionViews):
        def view(self, *args, **kwargs):
            raise ValueError('execution_state_integrity_failure')

    with pytest.raises(ValueError, match='execution_state_integrity_failure'):
        read(['view:execution.state'], Broken())


def test_history_event_body_query_uses_bounded_character_pagination():
    name, params = query_spec('view:execution.history?execution_ref=run-1&event_ref=event-7&offset=2000&limit=4000')
    assert name == 'execution.history'
    assert params == {'execution_ref': 'run-1', 'event_ref': 'event-7', 'offset': 2000, 'limit': 4000}
    with pytest.raises(ValueError, match='unsupported_view_query'):
        query_spec('view:execution.history?event_ref=event-7&limit=6001')


def test_nervous_original_event_query_can_expand_completed_history(tmp_path):
    from Nervous.organ import NervousOrgan

    with NervousOrgan(tmp_path) as nervous:
        event = nervous.submit('An unrelated source may contain counterevidence.', submission_id='original-input')
        nervous.complete(event.event_id, 'mind')
        alias = 'view:nervous.attention?event_ref=' + event.event_id
        result = read_views([alias], mind=MindViews(), execution=ExecutionViews(),
                            attention_view=nervous.attention_view)
        source, = json.loads(result['observation']['text'])['sources']
        original = json.loads(source['text'])
        assert original['owner'] == 'nervous'
        assert original['content'] == event.document()
        assert original['disposition'] == 'completed'
        assert original['missing'] is False
        assert result['aliases'] == {alias: source['ref']}
        assert nervous.calls.summary()['calls'] == 0


def test_omitted_mind_items_and_task_versions_are_available_by_owner_queries():
    class MindQueries:
        def __init__(self):
            self.calls = []

        def cognitive_item_view(self, *, item_ref=None):
            self.calls.append(('cognition', item_ref))
            return {'owner': 'mind', 'ref': 'owner:item:5', 'revision': 5, 'scope': item_ref,
                    'content': {'id': item_ref, 'claim': 'An earlier, still accepted scoped judgment.'},
                    'missing': False, 'truncated': False}

        def intentions_view(self, *, intention_ref=None, task_ref=None):
            self.calls.append(('pursuit', intention_ref, task_ref))
            return {'owner': 'mind', 'ref': 'owner:pursuit:5', 'revision': 5,
                    'scope': intention_ref or task_ref, 'content': {'requested': intention_ref or task_ref},
                    'missing': False, 'truncated': False}

    mind = MindQueries()
    aliases = ['view:mind.cognition?item_ref=belief:older',
               'view:mind.intentions?intention_ref=intention:paused',
               'view:mind.intentions?task_ref=task:a']
    result = read_views(aliases, mind=mind, execution=ExecutionViews(), attention_view=attention_view)
    assert mind.calls == [('cognition', 'belief:older'),
                          ('pursuit', 'intention:paused', None), ('pursuit', None, 'task:a')]
    sources = json.loads(result['observation']['text'])['sources']
    assert len(sources) == 3
    assert json.loads(sources[0]['text'])['content']['claim'] == 'An earlier, still accepted scoped judgment.'
    assert set(result['aliases']) == set(aliases)
    with pytest.raises(ValueError, match='unsupported_view_query'):
        query_spec('view:mind.intentions?intention_ref=intention:paused&task_ref=task:a')
