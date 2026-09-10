"""Deterministic bounded selection from read-only owner views; no semantic judge."""
import copy

from Nervous.storage import canonical, fingerprint

POLICY = 'nervous-attention-v1'
SELECTION_POLICY = 'nervous-attention-v2'


def delivery_sources(files, records, watched_files):
    """Owner delivery envelope recipe, preserving the baseline transfer budget."""
    sources, unread, size = [], [], 0
    watched = set(watched_files)
    for item in sorted(files, key=lambda item: item['file'] not in watched):
        record = records[item['ref']]
        length = len(canonical(record).encode('utf-8'))
        observation = item['file'] in watched
        if ((observation and size + length <= 18000)
                or (len(record['text']) <= 600 and size + length <= 6000 and len(sources) < 6)):
            sources.append(record)
            size += length
        elif observation and item['kind'] == 'observed_text':
            unread.append(item['ref'])
    return sources, unread


def inline_refs(files, records):
    """Legacy single-task inline recipe; all omitted files remain in its catalogue."""
    result = []
    for item in files:
        record = records.get(item['ref'])
        if record and record['text'].strip() and len(record['text']) <= 1000:
            result.append(item['ref'])
            if len(result) == 4:
                break
    return result


def frame(event, views, specs):
    recipes = {spec.recipe for spec in specs}
    if recipes - {'owner', 'related', 'control', 'continuation'}:
        raise ValueError('unsupported_attention_recipe')
    if not recipes or recipes == {'continuation'}:
        return None
    frozen = copy.deepcopy(views)
    files = frozen.get('execution', {}).get('files', [])
    value = {'policy': POLICY, 'event_id': event.event_id,
             'triggers': [{'id': s.id, 'version': s.version, 'recipe': s.recipe} for s in specs],
             'views': frozen, 'selected_refs': [f['ref'] for f in files],
             'scope': 'Owner-local samples, not a globally atomic observation.'}
    content = frozen.get('mind', {}).get('content', {})
    if 'cognitive_catalogue' in content:
        # The Mind owner supplies headers only. Identity links govern the fixed
        # policy; claim meaning, truth and new direction remain Mind's judgment.
        headers = sorted(content['cognitive_catalogue']['items'], key=lambda item: item['id'])
        related = set(frozen.get('event', {}).get('related_source_refs', []))
        understanding = {ref for intention in content.get('intentions', [])
                         if intention['commitment'] == 'committed'
                         for ref in intention.get('understanding_refs', [])}
        task = content.get('task')
        reasons, omitted, sources = {}, [], set(related)
        for item in headers:
            selected = []
            if item['id'] in understanding:
                selected.append('intention_understanding')
            scope = item.get('task_ref')
            if task and scope and all(scope.get(key) == task.get(key) for key in ('id', 'revision')):
                selected.append('current_task')
            if related.intersection(item.get('basis_refs', [])):
                selected.append('related_source')
            if selected:
                reasons[item['id']] = selected
                sources.update(item.get('basis_refs', []))
        # Scenario assumptions are structural dependencies, not an independent
        # semantic relevance judgment. Keep them with the selected scenario.
        by_id = {item['id']: item for item in headers}
        pending = list(reasons)
        while pending:
            for ref in by_id[pending.pop()].get('dependencies', []):
                if ref not in by_id:
                    raise ValueError('cognitive_catalogue_dependency_missing')
                if ref not in reasons:
                    reasons[ref] = ['selected_dependency']
                    sources.update(by_id[ref].get('basis_refs', []))
                    pending.append(ref)
        omitted = [{'id': item['id'], 'view_ref': 'view:mind.cognition?item_ref=' + item['id'],
                    'reason': 'No current intention, task-version or event-source link.'}
                   for item in headers if item['id'] not in reasons]
        value.update(policy=SELECTION_POLICY, selected_item_ids=list(reasons),
                     selection_reasons=reasons, omitted_items=omitted,
                     selected_sources=sorted(sources), selected_refs=sorted(sources))
    value['ref'] = 'attention:' + fingerprint(value)[:24]
    return value
