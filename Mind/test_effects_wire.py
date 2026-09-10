"""The emitted effects schema must describe the identity contract the owner accepts."""
import copy

import pytest
from jsonschema import Draft202012Validator

from Mind.intention import apply_effects, bind_pursuit, effects_schema
from Mind.test_intention_core import AUTHORITY, intention, proposal, watch


def bundle():
    return {'intentions': [intention()], 'task': proposal(), 'watches': [watch()]}


def errors_at(schema, value):
    def leaves(error):
        if error.context:
            return [leaf for child in error.context for leaf in leaves(child)]
        return [tuple(error.absolute_path)]
    return [path for error in Draft202012Validator(schema).iter_errors(value) for path in leaves(error)]


@pytest.mark.parametrize(('path', 'invalid'), [
    (('intentions', 0, 'id'), 'lumina-verification'),
    (('watches', 0, 'id'), 'watch-observations'),
    (('task', 'id'), 'task-assessment'),
    (('task', 'intention_id'), 'lumina-verification'),
    (('watches', 0, 'intention_id'), 'lumina-verification'),
])
def test_archived_malformed_ids_are_rejected_at_the_id_field(path, invalid):
    effects = bundle()
    parent = effects
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = invalid
    assert path in errors_at(effects_schema(), effects)


@pytest.mark.parametrize('kind', ['intentions', 'task', 'watches'])
def test_creation_and_update_revision_convention_is_expressed_by_schema(kind):
    effects = bundle()
    record = effects[kind] if kind == 'task' else effects[kind][0]
    prefix = {'intentions': 'intention', 'task': 'task', 'watches': 'watch'}[kind]
    path = (kind, 'base_revision') if kind == 'task' else (kind, 0, 'base_revision')
    record['base_revision'] = 1
    assert path in errors_at(effects_schema(), effects)
    record.update(id=prefix + '-' + 'a' * 20, base_revision=0)
    assert path in errors_at(effects_schema(), effects)


def test_legal_same_bundle_labels_and_generated_existing_ids_keep_state_semantics():
    pursuit = bind_pursuit({'version': 'intention-stage1-v1', 'mind_id': 'mind-test',
                           'authorization': AUTHORITY, 'task': None, 'task_status': None})
    effects = bundle()
    assert Draft202012Validator(effects_schema()).is_valid(effects)
    submitted = {'next': {'type': 'directive', 'text': 'Assess the supplied source coverage.'}, 'effects': effects}
    accepted, resolved = apply_effects(pursuit, submitted, [AUTHORITY['ref']], {}, 'create-event')
    update = {'intentions': [intention(resolved['intentions'][0]['id'], 1)],
              'task': proposal(resolved['task']['id'], resolved['intentions'][0]['id'], 1),
              'watches': [watch(resolved['watches'][0]['id'], resolved['intentions'][0]['id'], 1)]}
    assert Draft202012Validator(effects_schema()).is_valid(update)
    revised, output = apply_effects(accepted, {**submitted, 'effects': update}, [AUTHORITY['ref']], {},
                                    'update-event', task_status='completed')
    assert output['intentions'][0]['revision'] == output['task']['revision'] == output['watches'][0]['revision'] == 2
    assert revised['mind_id'] == accepted['mind_id']
    missing = copy.deepcopy(update)
    missing['intentions'][0]['id'] = 'intention-' + 'f' * 20
    assert Draft202012Validator(effects_schema()).is_valid(missing)
    with pytest.raises(ValueError, match='stale_pursuit_revision'):
        apply_effects(accepted, {**submitted, 'effects': missing}, [AUTHORITY['ref']], {},
                      'missing-event', task_status='completed')
