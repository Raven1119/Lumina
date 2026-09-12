"""Small registered owner queries transported as immutable read-evidence results."""
import copy
from urllib.parse import parse_qsl

from Nervous.storage import canonical, fingerprint


QUERY_PARAMETERS = {
    'execution.state': frozenset({'execution_ref'}),
    'execution.history': frozenset({'execution_ref', 'event_ref', 'offset', 'limit'}),
    'execution.environment': frozenset(),
    'mind.intentions': frozenset({'intention_ref', 'task_ref'}),
    'mind.cognition': frozenset({'item_ref'}),
    'nervous.attention': frozenset({'offset', 'limit', 'event_ref'}),
}


def query_descriptors():
    return [
        {'ref': 'view:execution.state', 'kind': 'owner_view_query',
         'label': 'Saved execution state; optional ?execution_ref=ID. Does not sample files.'},
        {'ref': 'view:execution.history', 'kind': 'owner_view_query',
         'label': 'Saved projected action history; ?execution_ref=ID&offset=0&limit=4, limit 1..8. '
                  'Add event_ref=ID for one original event body; offset counts characters, limit 1..6000.'},
        {'ref': 'view:execution.environment', 'kind': 'owner_view_query',
         'label': 'Explicitly refresh authorized environment observations and source catalogue.'},
        {'ref': 'view:mind.intentions', 'kind': 'owner_view_query',
         'label': 'Current persistent pursuit commitments; optional ?intention_ref=ID or ?task_ref=ID '
                  'retrieves the requested owner record with its version.'},
        {'ref': 'view:mind.cognition', 'kind': 'owner_view_query',
         'label': 'Accepted cognition catalogue with bounded text previews for choosing relevant items; '
                  '?item_ref=ID retrieves the complete current item before revision. Its basis refs name '
                  'historical evidence; read those sources separately when needed. A preview is not a new judgment.'},
        {'ref': 'view:nervous.attention', 'kind': 'owner_view_query',
         'label': 'Resources and pending/deferred catalogue; ?offset=0&limit=4, limit 1..8. '
                  'Use ?event_ref=ID to retrieve the original attributed event, including completed history.'},
    ]


def query_spec(ref):
    """Decode only registered names and bounded arguments; no expressions/DSL."""
    if not isinstance(ref, str) or not ref.startswith('view:') or len(ref) > 256 or '#' in ref:
        raise ValueError('unsupported_view_query')
    name, separator, query = ref[5:].partition('?')
    allowed = QUERY_PARAMETERS.get(name)
    if allowed is None or (separator and not query):
        raise ValueError('unsupported_view_query')
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=4) if query else []
    except ValueError:
        raise ValueError('unsupported_view_query') from None
    params = dict(pairs)
    if len(params) != len(pairs) or not set(params) <= allowed:
        raise ValueError('unsupported_view_query')
    if name == 'mind.intentions' and {'intention_ref', 'task_ref'} <= set(params):
        raise ValueError('unsupported_view_query')
    for key, value in list(params.items()):
        if key in {'offset', 'limit'}:
            if not value.isascii() or not value.isdecimal() or len(value) > 6:
                raise ValueError('unsupported_view_query')
            params[key] = int(value)
            maximum = 6000 if name == 'execution.history' and 'event_ref' in params else 8
            if key == 'limit' and not 1 <= params[key] <= maximum:
                raise ValueError('unsupported_view_query')
        elif not value or len(value) > 128:
            raise ValueError('unsupported_view_query')
    return name, params


def _environment(execution):
    snapshot = execution.refresh_environment()
    return {'owner': 'execution', 'ref': snapshot['catalogue_ref'],
            'revision': snapshot['catalogue_ref'],
            'scope': {'execution_ref': snapshot.get('execution_ref'),
                      'sampling': 'explicit_environment_refresh'},
            'content': snapshot, 'missing': False, 'truncated': False}


def read_views(refs, *, mind, execution, attention_view, normal_read=None):
    """Return the existing sources-v1 protocol plus query-to-result alias bindings.

    Aliases request fresh views; they are never evidence identities. Owners
    retain their original scope and truncation markers in the result text. The
    protocol origin is `execution` for compatibility, not semantic certification
    of a Mind judgment or a statement merely printed by an execution action.
    """
    if (not isinstance(refs, (tuple, list)) or not 1 <= len(refs) <= 16
            or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs)):
        raise ValueError('invalid_evidence_refs')
    # Validate the entire query before an explicit observation may occur.
    queries = {ref: query_spec(ref) for ref in refs if ref.startswith('view:')}
    if normal_read is None and len(queries) != len(refs):
        raise ValueError('unsupported_view_query')
    readers = {'execution.state': lambda **params: execution.view('execution.state', **params),
               'execution.history': lambda **params: execution.view('execution.history', **params),
               'execution.environment': lambda: _environment(execution),
               'mind.intentions': lambda **params: mind.intentions_view(**params),
               'mind.cognition': lambda **params: mind.cognitive_item_view(**params),
               'nervous.attention': attention_view}
    records, aliases = {}, {}
    for ref in refs:
        if ref in queries:
            name, params = queries[ref]
            envelope = copy.deepcopy(readers[name](**params))
            source_ref = 'view-result:' + fingerprint(envelope)[:24]
            record = {'ref': source_ref, 'text': canonical(envelope), 'origin': 'execution',
                      'label': ref, 'source_kind': 'owner_view'}
            aliases[ref] = source_ref
        else:
            record = copy.deepcopy(normal_read(ref))
            if record['ref'] != ref:
                raise ValueError('source_identity_conflict')
        existing = records.get(record['ref'])
        if existing and any(existing[key] != record[key] for key in ('ref', 'text', 'origin')):
            raise ValueError('source_identity_conflict')
        records.setdefault(record['ref'], record)
    records = list(records.values())
    text = canonical({'read_result': 'sources-v1', 'sources': [
        {key: record[key] for key in ('ref', 'text', 'origin')} for record in records]})
    observation = {'capability': 'read_evidence', 'text': text, 'origin': 'execution'}
    metadata = [{key: value for key, value in record.items() if key != 'text'} for record in records]
    content = {'observation': observation, 'records': metadata, 'aliases': aliases}
    if len(text) > 8000 or len(canonical(observation)) > 9000 or len(canonical(content).encode('utf-8')) > 28000:
        observation['text'] = canonical({'read_result': 'capacity-v1', 'status': 'not_read',
            'reason': 'response_capacity', 'required_text_chars': len(text),
            'required_observation_chars': len(canonical(observation)),
            'max_text_chars': 8000, 'max_observation_chars': 9000, 'max_event_content_bytes': 28000,
            'sources': [{'ref': record['ref'], 'text_chars': len(record['text']), 'origin': record['origin']}
                        for record in records]})
        return {'observation': observation, 'records': [], 'aliases': {}}
    return content
