"""Versioned pursuit commits. This module validates data; it grants no action authority."""
from __future__ import annotations

import copy
import re
from datetime import datetime
from pathlib import PurePosixPath

from jsonschema import Draft202012Validator
from Nervous.storage import canonical, fingerprint

PURSUIT_VERSION = 'intention-stage1-v1'
COGNITIVE_VERSION = 'mind-cognition-stage1-v1'
EFFECTS_SCHEMA_VERSION = 'intention-effects-wire-v2'
COMMIT_INTEGRITY_VERSION = 'pursuit-commit-v2'
_UNSPECIFIED = object()
MAX_INTENTIONS = 8
MAX_TASKS = 32
MAX_WATCHES = 32


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def _text(limit):
    return {'type': 'string', 'minLength': 1, 'maxLength': limit, 'pattern': r'\S'}


def _record_id(prefix):
    return {**_text(64), 'pattern': rf'^(new:[A-Za-z0-9_-]{{1,32}}|{prefix}-[0-9a-f]{{20}})$',
            'description': f'Create with new:label (1-32 letters, digits, underscore or hyphen); '
                           f'update using the exact accepted {prefix}- plus 20 lowercase hex digits. '
                           'Do not invent an existing ID.'}


def _update_schema(prefix, fields):
    result = _object({'id': _record_id(prefix),
        'base_revision': {'type': 'integer', 'minimum': 0,
                         'description': 'New new:label records use 0. Existing IDs use their actual accepted revision.'},
        **fields})
    result['allOf'] = [{'if': {'properties': {'id': {'pattern': '^new:'}}},
                        'then': {'properties': {'base_revision': {'const': 0}}},
                        'else': {'properties': {'base_revision': {'minimum': 1}}}}]
    return result


def effects_schema(sources=()):
    ref = _text(128)
    refs = {'type': 'array', 'uniqueItems': True, 'maxItems': 8, 'items': ref}
    basis = {**refs, 'minItems': 1}
    if sources:
        basis = {**basis, 'items': {**ref, 'enum': list(sources)}}
    intention_ref = {**_record_id('intention'),
                     'description': 'Exact accepted intention ID, or its new:label declared in this same effects bundle.'}
    intention = _update_schema('intention', {'aim': _text(1000), 'why': _text(1000),
        'commitment': {'enum': ['candidate', 'committed', 'paused', 'closed']},
        'origin_refs': basis, 'understanding_refs': refs, 'influence_refs': refs})
    task = _update_schema('task', {'intention_id': intention_ref, 'authority_ref': ref,
                    'goal': _text(1600), 'acceptance': _text(1600)})
    watch = _update_schema('watch', {'spec_id': {'enum': ['source.changed', 'review.due']},
        'spec_version': {'const': 1}, 'params': {'oneOf': [
            _object({'file': _text(128)}), _object({'due_at': _text(64)})]},
        'intention_id': {**intention_ref, 'type': ['string', 'null']},
        'status': {'enum': ['active', 'cancelled']}, 'reason': _text(1000), 'origin_refs': basis})
    return {**_object({'intentions': {'type': 'array', 'maxItems': MAX_INTENTIONS, 'items': intention},
                      'task': {'oneOf': [{'type': 'null'}, task]},
                      'watches': {'type': 'array', 'maxItems': MAX_WATCHES, 'items': watch}}),
            'description': EFFECTS_SCHEMA_VERSION + ': explicit creation labels and accepted-version updates.'}


def bind_pursuit(value, previous=None):
    """Check an owner-produced activity snapshot against already accepted identity."""
    fields = {'version', 'mind_id', 'authorization', 'task', 'task_status'}
    if (type(value) is not dict or set(value) - {'visible_item_ids', 'execution_task'} != fields
            or value['version'] != PURSUIT_VERSION):
        raise ValueError('invalid_pursuit_input')
    authority = value['authorization']
    if (type(authority) is not dict or set(authority) != {'ref', 'text'}
            or not Draft202012Validator(_text(128)).is_valid(authority['ref'])
            or not Draft202012Validator(_text(4000)).is_valid(authority['text'])
            or not Draft202012Validator(_text(128)).is_valid(value['mind_id'])
            or (value['task_status'] is not None and not isinstance(value['task_status'], str))):
        raise ValueError('invalid_pursuit_authority')
    if 'visible_item_ids' in value:
        selected = value['visible_item_ids']
        if (type(selected) is not list or any(type(ref) is not str for ref in selected)
                or len(selected) != len(set(selected))):
            raise ValueError('invalid_visible_cognition')
    if previous is None:
        if value['task'] is not None:
            raise ValueError('uncommitted_task')
        return {'version': PURSUIT_VERSION, 'mind_id': value['mind_id'],
                'authorization': copy.deepcopy(authority), 'intentions': {},
                'tasks': {}, 'task': None, 'watches': {}}
    if (value['mind_id'] != previous['mind_id'] or authority != previous['authorization']):
        raise ValueError('pursuit_authority_conflict')
    if value['task'] != previous['task']:
        raise ValueError('pursuit_task_identity_conflict')
    return copy.deepcopy(previous)


def task_owner(task, execution_protocol):
    """Project business acceptance without claiming the Task is already accepted by Execution."""
    return {'business_goal': task['goal'] + '\n\nAcceptance:\n' + task['acceptance'],
            'execution_protocol': execution_protocol.replace('exact content done',
                'exact content done:' + fingerprint(task)[:24])}


def validate_task_projection(pursuit_input, owner_task):
    task = pursuit_input.get('execution_task', pursuit_input['task'])
    if task is None:
        if owner_task is not None:
            raise ValueError('pursuit_task_projection_conflict')
    elif owner_task is None or owner_task['business_goal'] != task_owner(task, '')['business_goal']:
        raise ValueError('pursuit_task_projection_conflict')


def _identity(update, previous, event_id, prefix):
    identity = update['id']
    if identity.startswith('new:'):
        if update['base_revision'] != 0 or re.fullmatch(r'new:[A-Za-z0-9_-]{1,32}', identity) is None:
            raise ValueError('invalid_new_identity')
        return prefix + '-' + fingerprint([event_id, identity])[:20], 1
    if identity not in previous or update['base_revision'] != previous[identity]['revision']:
        raise ValueError('stale_pursuit_revision')
    return identity, previous[identity]['revision'] + 1


def validate_commit_integrity(pursuit, items, execution_task, execution_status):
    if pursuit is None:
        return
    if any(ref not in items for intention in pursuit['intentions'].values()
           for ref in intention['understanding_refs']):
        raise ValueError('unknown_pursuit_understanding')
    if execution_task is not None and execution_status not in {'completed', 'failed'}:
        intention = pursuit['intentions'].get(execution_task['intention_id'])
        if intention and intention['commitment'] == 'closed':
            raise ValueError('closed_intention_has_active_task')


def apply_effects(pursuit, submitted, sources, items, event_id, *, task_status=None, activation_id=None,
                  check_integrity=True, execution_task=_UNSPECIFIED, execution_status=None):
    """Return an atomic candidate and resolved outbox data, without side effects."""
    effects = submitted.get('effects')
    if execution_task is _UNSPECIFIED:
        execution_task = pursuit['task'] if pursuit else None
    execution_status = task_status if execution_status is None else execution_status
    if effects is None:
        if pursuit is not None and pursuit['task'] is None and submitted['next']['type'] == 'directive':
            raise ValueError('directive_requires_task')
        if check_integrity:
            validate_commit_integrity(pursuit, items, execution_task, execution_status)
        return copy.deepcopy(pursuit), None
    if pursuit is None or not Draft202012Validator(effects_schema()).is_valid(effects):
        raise ValueError('invalid_pursuit_effects')
    candidate = copy.deepcopy(pursuit)
    resolved = {'intentions': [], 'task': None, 'watches': []}
    labels = {}
    for change in effects['intentions']:
        if change['id'] in labels:
            raise ValueError('duplicate_intention_update')
        identity, revision = _identity(change, candidate['intentions'], event_id, 'intention')
        labels[change['id']] = identity
        if any(ref not in sources for ref in [*change['origin_refs'], *change['influence_refs']]):
            raise ValueError('ungrounded_pursuit_basis')
        understanding = [ref if not ref.startswith('new:') else
                         'item-' + fingerprint([event_id, ref])[:20]
                         for ref in change['understanding_refs']]
        if any(ref not in items for ref in understanding):
            raise ValueError('unknown_pursuit_understanding')
        record = {key: copy.deepcopy(value) for key, value in change.items() if key != 'base_revision'}
        record.update(id=identity, revision=revision, understanding_refs=understanding)
        candidate['intentions'][identity] = record
        resolved['intentions'].append(record)
    if len(candidate['intentions']) > MAX_INTENTIONS:
        raise ValueError('intention_capacity_exhausted')
    if sum(item['commitment'] == 'committed' for item in candidate['intentions'].values()) > 1:
        raise ValueError('multiple_committed_intentions')
    proposal = effects['task']
    if proposal is not None:
        if submitted['next']['type'] != 'directive':
            raise ValueError('task_requires_direction')
        revising_wait = (candidate['task'] is not None and task_status == 'waiting'
                        and proposal['id'] == candidate['task']['id'])
        if (candidate['task'] is not None and not revising_wait
                and task_status not in {'completed', 'failed', 'not_accepted'}):
            raise ValueError('prior_task_not_settled')
        identity, revision = _identity(proposal, candidate['tasks'], event_id, 'task')
        intention_id = labels.get(proposal['intention_id'], proposal['intention_id'])
        intention = candidate['intentions'].get(intention_id)
        if intention is None or intention['commitment'] != 'committed':
            raise ValueError('task_intention_not_committed')
        if proposal['authority_ref'] != candidate['authorization']['ref']:
            raise ValueError('task_authority_conflict')
        task = {key: copy.deepcopy(value) for key, value in proposal.items() if key != 'base_revision'}
        task.update(id=identity, revision=revision, intention_id=intention_id,
                    intention_revision=intention['revision'])
        candidate['tasks'][identity] = task
        candidate['task'] = task
        resolved['task'] = task
        if len(candidate['tasks']) > MAX_TASKS:
            raise ValueError('task_capacity_exhausted')
    changed_watches = set()
    for change in effects['watches']:
        identity, revision = _identity(change, candidate['watches'], event_id, 'watch')
        if identity in changed_watches:
            raise ValueError('duplicate_watch_update')
        changed_watches.add(identity)
        if any(ref not in sources for ref in change['origin_refs']):
            raise ValueError('ungrounded_pursuit_basis')
        intention_id = labels.get(change['intention_id'], change['intention_id'])
        intention = candidate['intentions'].get(intention_id)
        if intention_id is not None and (intention is None or
                (change['status'] == 'active' and intention['commitment'] == 'closed')):
            raise ValueError('watch_intention_not_applicable')
        if change['spec_id'] == 'source.changed':
            if set(change['params']) != {'file'}:
                raise ValueError('invalid_watch_parameters')
            path = PurePosixPath(change['params']['file'])
            if path.is_absolute() or '..' in path.parts or '\\' in str(path) or ':' in str(path):
                raise ValueError('invalid_watch_source')
        else:
            if set(change['params']) != {'due_at'}:
                raise ValueError('invalid_watch_parameters')
            try:
                due = datetime.fromisoformat(change['params']['due_at'].replace('Z', '+00:00'))
                if due.tzinfo is None or due.utcoffset().total_seconds() != 0:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError('invalid_watch_due_time') from None
        record = {key: copy.deepcopy(value) for key, value in change.items() if key != 'base_revision'}
        record.update(id=identity, revision=revision, intention_id=intention_id)
        candidate['watches'][identity] = record
        resolved['watches'].append(record)
    if len(candidate['watches']) > MAX_WATCHES:
        raise ValueError('watch_capacity_exhausted')
    if activation_id is not None:
        for record in [*candidate['intentions'].values(), *candidate['watches'].values()]:
            for key in ('origin_refs', 'influence_refs'):
                if key in record:
                    record[key] = [ref.replace('activation:', activation_id + ':', 1)
                                   if ref.startswith('activation:observation') else ref for ref in record[key]]
    # Subscription lifecycle is applied by Nervous from the same accepted bundle.
    # A closed pursuit cannot retain an active subscription as current intent.
    for identity, watch in candidate['watches'].items():
        intention = candidate['intentions'].get(watch['intention_id'])
        if watch['status'] == 'active' and intention and intention['commitment'] == 'closed':
            raise ValueError('closed_intention_has_active_watch')
    if len(canonical(candidate)) > 24000:
        raise ValueError('pursuit_capacity_exhausted')
    if candidate['task'] is None and submitted['next']['type'] == 'directive':
        raise ValueError('directive_requires_task')
    if check_integrity:
        validate_commit_integrity(candidate, items, execution_task, execution_status)
    return candidate, resolved


def source_refs(pursuit):
    if pursuit is None:
        return set()
    return {pursuit['authorization']['ref']} | {
        ref for record in [*pursuit['intentions'].values(), *pursuit['watches'].values()]
        for key in ('origin_refs', 'influence_refs') for ref in record.get(key, [])}
