"""Persistent Mind: owns judgment and analysis, never a business workspace.

Cognition owns atomic commits. This organ owns semantic activities and their
read/analysis continuations. Nervous carries data; Execution owns actions.
"""
from __future__ import annotations

import json
from pathlib import Path
import httpx
from jsonschema import ValidationError
from Nervous.organ import Event
from Nervous.storage import canonical, fingerprint, plain, read_json, write_json
from Nervous.provider import BudgetPause
from Mind.cognition import Cognition, Evidence, MindInput, MindResultEvent
from Mind.contracts import ExecutionObservation
from Mind.model import MindModel, READ_SOURCE_METADATA_FIELDS
from Mind.analysis import Analysis
from Mind.task_view import execution_goal
from Mind.world_model import compare_observation_contract


def reply(event, target, kind, data, suffix=None):
    return Event(kind + '-' + fingerprint([event.event_id, suffix])[:24],
                 event.target, target, kind, data, event.event_id)


def event_document(event):
    return {name: plain(getattr(event, name)) for name in
            ('event_id', 'source', 'target', 'kind', 'data', 'causation_id')}


def context_scope(state):
    if state.get('stage1_authority'):
        return fingerprint([state['stage1_authority']['mind_id'], state.get('background_task')])
    return fingerprint(state['task'])


def background_path(directory, state):
    return (Path(directory) / 'backgrounds' / (context_scope(state) + '.json')
            if state.get('stage1_authority') else Path(directory) / 'background.json')


def background_segments(segments, state):
    if not state.get('stage1_authority'):
        return segments
    return [segment for segment in segments if segment['content'].get('task_ref') == state.get('background_task')]


class MindOrgan:
    """One continuing goal, one active judgment, inert external input."""

    def __init__(self, directory, calls, *, goal=None, execution_protocol='',
                 model=None, analysis=None, context_mode=None, stage1_authority=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path = self.directory / 'mind.json'
        if self._path.exists():
            doc = read_json(self._path)
            if doc['sha256'] != fingerprint(doc['state']):
                raise ValueError('mind_state_integrity_failure')
            self.state = doc['state']
            if self.state['format'] not in {'mind-organ-1', 'mind-organ-stage1'}:
                raise ValueError('unsupported_mind_state')
            if goal is not None and goal != (self.state['task'] or {}).get('business_goal'):
                raise ValueError('goal_identity_conflict')
            if stage1_authority is not None and stage1_authority != self.state.get('stage1_authority'):
                raise ValueError('pursuit_authority_conflict')
        else:
            task = None if stage1_authority else {'business_goal': goal, 'execution_protocol': execution_protocol}
            if task is not None:
                execution_goal(task)
            self.state = {'format': 'mind-organ-1', 'task': task, 'active': None,
                'activities': {}, 'handled': {}, 'owners': [], 'sources': {},
                'predictions': {}, 'unresolved': []}
            if stage1_authority:
                self.state.update(format='mind-organ-stage1', stage1_authority=plain(stage1_authority),
                                  execution_protocol=execution_protocol)
        if context_mode not in (None, 'baseline', 'mask', 'summary'):
            raise ValueError('unsupported_context_mode')
        if (self._path.exists() and context_mode is not None
                and context_mode != self.state.get('context_mode', 'baseline')):
            raise ValueError('context_mode_is_fixed_at_start')
        if context_mode is not None:
            self.state['context_mode'] = context_mode
        self.calls = calls
        self.cognition = Cognition(directory=self.directory / 'cognition',
            model=model or MindModel(calls,
                preflight=self.preflight,
                source_info=self.source_info,
                readable_sources=self.readable_sources),
            available_capabilities=('read_evidence', 'analyze_world_model'),
            prepare_background=self.prepare_background
                               if self.state.get('context_mode', 'baseline') != 'baseline' else None)
        self.analysis = analysis or Analysis(self.directory / 'analysis', calls,
            lambda ref: self.source_record(ref)['text'],
            owner_task=({'ref': 'owner-task:' + fingerprint(self.state['task']),
                        'goal': self.state['task']['business_goal']} if self.state['task'] else None))
        self.save()

    def close(self):
        self.cognition.close()

    def save(self):
        write_json(self._path, {'state': self.state, 'sha256': fingerprint(self.state)})

    def preflight(self, wire):
        if self.state.get('context_mode', 'baseline') != 'baseline':
            from working_context import estimate_request_tokens
            if estimate_request_tokens(wire) + wire['max_tokens'] > 196608:
                raise BudgetPause('mind_context_capacity_exhausted')
        try:
            return self.calls.ensure(wire, role='mind')
        except ValueError as error:
            if str(error) == 'provider_request_too_large' and self.state.get('context_mode', 'baseline') != 'baseline':
                raise BudgetPause('mind_context_capacity_exhausted') from error
            raise

    def retry_context(self):
        from working_context import retry_failed_compaction
        return retry_failed_compaction(background_path(self.directory, self.state), self.calls,
            role='mind', scope=context_scope(self.state),
            segments=background_segments(self.cognition.history_segments(), self.state))

    def prepare_background(self, segments, *, force=False):
        if self.state.get('stage1_authority'):
            task = self.snapshot().get('task_contract')
            self.state['background_task'] = {key: task[key] for key in ('id', 'revision')} if task else None
            self.save()
            segments = background_segments(segments, self.state)
        mode = self.state.get('context_mode', 'baseline')
        summary = None
        if mode == 'summary':
            from working_context import compact
            summary = compact(background_path(self.directory, self.state), self.calls, role='mind',
                scope=context_scope(self.state), segments=segments, force=force,
                retain=1 if force else 6,
                instructions='Preserve why the situation and guidance changed, relevant conditions, '
                'competing explanations and unknowns. Old Mind judgments, hypotheses, calculations '
                'and executor statements are not verified reality. Retain failures and exact source '
                'references. Current accepted cognition and the new event are supplied separately.')
        covered = len(summary['source_refs']) if summary else 0
        masked = []
        if mode == 'mask':
            for segment in segments[:-6]:
                pieces = []
                for piece in segment['content']['pieces']:
                    if piece['event_type'] in {'INITIAL_EXECUTION_OBSERVED', 'CAPABILITY_OBSERVED',
                                                'MODEL_OUTPUT_RECORDED'}:
                        piece = {**piece, 'content': {'masked': True, 'source_ref': piece['ref'],
                            'chars': len(canonical(piece['content'])),
                            'scope': 'Historical result body omitted; read the original history ref.'}}
                    pieces.append(piece)
                masked.append({**segment, 'content': {**segment['content'], 'pieces': pieces}})
        return {'mode': mode,
            'scope': 'Derived historical background, not accepted cognition, current authorization or evidence. '
                     'Read original trace pieces when needed; a summary ref cannot support a belief.',
            'policy': 'Mask old observation and model-output bodies; keep inputs, requests and terminal structure.'
                      if mode == 'mask' else 'Summarize complete old activities and retain the uncovered complete suffix.',
            'summary': summary['summary'] if summary else None,
            'source_refs': summary['source_refs'] if summary else [],
            'masked_activities': masked,
            'recent_activities': segments[-6:] if mode == 'mask' else segments[covered:],
            'catalogue': [{'ref': piece['ref'], 'event_type': piece['event_type'],
                           'chars': len(canonical(piece['content']))}
                          for segment in segments for piece in segment['content']['pieces']]}

    @staticmethod
    def _context_status(directory, state):
        from Nervous.storage import inspect_safely
        from working_context import inspect_context
        return {'context_mode': state.get('context_mode', 'baseline'),
                'background': inspect_safely(lambda: inspect_context(background_path(directory, state),
                    role='mind', scope=context_scope(state)))}

    @classmethod
    def inspect_directory(cls, directory):
        directory = Path(directory)
        if not (directory / 'mind.json').exists():
            return {'status': 'not_initialized'}
        document = read_json(directory / 'mind.json')
        state = document['state']
        if document['sha256'] != fingerprint(state) or state['format'] not in {'mind-organ-1', 'mind-organ-stage1'}:
            raise ValueError('mind_state_integrity_failure')
        from Nervous.storage import inspect_safely
        view = inspect_safely(Cognition.inspect_directory, directory / 'cognition')
        return {'goal': (state['task'] or {}).get('business_goal'), **view, 'active': state['active'],
            'unresolved': [{'activity_id': key, 'status': state['activities'][key]['status'],
                           'error': state['activities'][key].get('error')} for key in state['unresolved']],
            'predictions': list(state['predictions']), **cls._context_status(directory, state)}

    def snapshot(self):
        return self.state['activities'].get(self.state['active'], {}).get('snapshot') or {}

    def readable_sources(self):
        refs = [x['ref'] for x in self.snapshot().get('files', [])]
        repetition = self.snapshot().get('repetition_observation')
        if repetition:
            refs += [repetition[key] for key in ('sample_ref', 'source_versions_ref', 'records_ref')]
        if self.state.get('stage1_authority'):
            refs += ['view:execution.state', 'view:execution.history', 'view:execution.environment',
                     'view:nervous.attention', 'view:mind.intentions']
        return refs

    def attention_view(self, event):
        """Bounded owner projection, without a model call or private trace bodies."""
        if not self.state.get('stage1_authority'):
            return None
        pursuit = self.cognition.pursuit_state()
        task = event.document()['data'].get('snapshot', {}).get('task_contract')
        content = {'authorization_ref': self.state['stage1_authority']['ref'],
                   'task': ({key: task[key] for key in ('id', 'revision')} if task else None),
                   'intentions': [{'id': item['id'], 'revision': item['revision'],
                                   'commitment': item['commitment'],
                                   'understanding_refs': list(item['understanding_refs'])}
                                  for item in (pursuit or {}).get('intentions', {}).values()],
                   'unresolved': list(self.state['unresolved']),
                   'cognitive_catalogue': self.cognition.attention_catalogue()}
        view = {'owner': 'mind', 'revision': self.cognition.inspect().revision,
                'scope': self.state['stage1_authority']['mind_id'], 'content': content,
                'missing': False, 'truncated': False}
        view['ref'] = 'mind-view:' + fingerprint(view)[:24]
        return view

    def intentions_view(self, *, intention_ref=None, task_ref=None):
        value = self.cognition.pursuit_state()
        if intention_ref is not None or task_ref is not None:
            value = ((value or {}).get('intentions', {}).get(intention_ref) if intention_ref is not None
                     else (value or {}).get('tasks', {}).get(task_ref))
        return {'owner': 'mind', 'ref': 'intentions:' + fingerprint(value)[:24],
                'revision': self.cognition.inspect().revision, 'scope': 'current pursuit commitments',
                'content': value, 'missing': value is None, 'truncated': False}

    def cognitive_item_view(self, *, item_ref=None):
        if item_ref is None:
            catalogue = self.cognition.attention_catalogue()
            return {'owner': 'mind', 'ref': 'cognition-catalogue:' + fingerprint(catalogue)[:24],
                    'revision': catalogue['revision'], 'scope': {'kind': 'cognitive_catalogue'},
                    'content': catalogue, 'missing': False, 'truncated': False}
        return self.cognition.cognitive_item_view(item_ref)

    def attention_origin(self, event):
        data = event.document()['data']
        if event.kind == 'execution.snapshot':
            work = self.state['activities'][data['activity_id']]
            original = (work.get('owner_input') or {})
            origin = {'ref': work['origin_event'], 'trigger': work['trigger'],
                      'owner_input': {k: original[k] for k in ('event_id', 'event_type') if k in original}}
            if work.get('event_source'):
                document = json.loads(self.source_record(work['event_source'])['text'])
                signal = document.get('data', {})
                origin['related_source_refs'] = [value['ref'] for key in ('before', 'after')
                    if isinstance((value := signal.get(key)), dict) and value.get('ref')]
            return origin
        return {'ref': event.event_id, 'reason': data.get('reason'),
                'origin_activity_id': data.get('origin_activity_id')}

    def attention_ready(self, event):
        active = self.state['activities'].get(self.state['active'])
        return (event.target != 'mind' or active is None
                or active['origin_event'] == event.event_id)

    def retry_activity(self):
        """Select an unresolved review for an explicit owner retry command."""
        if self.state['active'] is not None:
            raise ValueError('cognitive_activity_pending')
        if not self.state['unresolved']:
            raise ValueError('no_failed_cognitive_activity')
        return self.state['unresolved'][-1]

    def put_source(self, record):
        record = plain(record)
        ref = record['ref']
        previous = self.state['sources'].get(ref)
        if previous:
            existing = read_json(self.directory / 'sources' / previous)
            if any(existing[k] != record[k] for k in ('ref', 'text', 'origin')):
                raise ValueError('source_identity_conflict')
            record = {**record, **existing}
        name = fingerprint(ref) + '.json'
        write_json(self.directory / 'sources' / name, record)
        self.state['sources'][ref] = name
        return ref

    def source(self, text, label, origin='execution', kind='catalogue'):
        return self.put_source({'ref': 'source:' + fingerprint([text, label, origin, kind])[:24],
            'text': text, 'label': label, 'origin': origin, 'source_kind': kind})

    def source_record(self, ref):
        name = self.state['sources'].get(ref)
        return read_json(self.directory / 'sources' / name) if name else plain(self.cognition.read_source(ref))

    def source_info(self, ref):
        # Model projection holds Cognition's lock: never re-enter its read API.
        if self.state.get('stage1_authority') and ref.startswith('view:'):
            from Nervous.views import query_descriptors
            return next((item for item in query_descriptors() if item['ref'] == ref),
                        {'kind': 'owner_view_query', 'label': 'A registered bounded owner query.'})
        name = self.state['sources'].get(ref)
        if not name:
            return {'kind': 'historical_cognitive_source'}
        record = read_json(self.directory / 'sources' / name)
        info = {'kind': record.get('source_kind', 'recorded_text'), 'label': record.get('label', '')}
        if ref.startswith('history:'):
            info.update(scope='Role-projected historical trace fragment; model output is a past judgment, not an owner fact.',
                        offset=record['offset'], total_chars=record['total_chars'], truncated=record['truncated'])
        latest = next((x for x in self.snapshot().get('files', [])
                       if x['file'] == record.get('label')), None)
        if latest:
            info.update(workspace_version='current' if latest['ref'] == ref else 'superseded',
                        current_ref=latest['ref'])
        return info

    def handle(self, event, *, attention=None):
        digest = fingerprint(event_document(event))
        if event.event_id in self.state['handled']:
            record = self.state['handled'][event.event_id]
            if record['input_sha256'] != digest:
                raise ValueError('event_identity_conflict')
            return tuple(Event(**x) for x in record['emitted'])
        authority = {'user.input': ('user', 'mind'), 'mind.retry': ('user', 'mind'),
            'execution.changed': ('execution', 'mind'),
            'execution.snapshot': ('execution', 'mind.results'),
            'evidence.result': ('execution', 'mind.results'),
            'analysis.result': ('mind.analysis', 'mind.results'),
            'execution.receipt': ('execution', 'mind.results'),
            'prediction.receipt': ('execution', 'mind.results')}
        if self.state.get('stage1_authority'):
            authority.update({'attention.signal': ('nervous', 'mind'),
                              'view.result': ('nervous', 'mind.results'),
                              'control.notice': ('nervous', 'mind')})
        if event.kind == 'analysis.request':
            if event.source not in {'mind', 'mind.results'} or event.target != 'mind.analysis':
                raise ValueError('analysis_request_authority_conflict')
        elif authority.get(event.kind) != (event.source, event.target):
            raise ValueError('mind_event_authority_conflict')
        data = plain(event.data)
        if event.kind in {'user.input', 'execution.changed', 'mind.retry', 'attention.signal', 'control.notice'}:
            active = self.state['activities'].get(self.state['active'])
            if active is not None and active['origin_event'] != event.event_id:
                return None
            emitted = self.begin(event, data, attention=attention)
        elif event.kind in {'execution.receipt', 'prediction.receipt'}:
            if data.get('activity_id') in self.state['activities']:
                self.state['activities'][data['activity_id']].setdefault('receipts', []).append(data)
            emitted = ()
        else:
            work = self.state['activities'][data['activity_id']]
            if self.state['active'] != work['id']:
                raise ValueError('stale_cognitive_continuation')
            if attention is not None and 'attention' not in work:
                work['attention'] = plain(attention)
                self.save()
            if event.kind in {'evidence.result', 'view.result'} and data.get('error') is not None:
                if work.get('prefetch'):
                    raise ValueError('declared_observation_source_missing')
                if data['request_ref'] != work['request']['request_ref']:
                    raise ValueError('cognitive_read_identity_conflict')
                emitted = self.receipt(event, work, self.cognition.accept_result(
                    MindResultEvent(data['request_ref'], error=data['error'])))
            elif event.kind == 'execution.snapshot':
                work['snapshot'] = data['snapshot']
                emitted = self.prepare_snapshot(event, work)
            elif event.kind in {'evidence.result', 'view.result'}:
                prefetch = work.get('prefetch')
                request = prefetch or work['request']
                if data['request_ref'] != request['request_ref']:
                    raise ValueError('cognitive_read_identity_conflict')
                observation = data['observation']
                body = json.loads(observation['text'])
                if data.get('aliases'):
                    work.setdefault('resolved_refs', {}).update(data['aliases'])
                if body.get('read_result') == 'sources-v1':
                    metadata = {x['ref']: x for x in data.get('records', [])}
                    for record in body['sources']:
                        extra = metadata.get(record['ref'], {})
                        if any(k in extra and extra[k] != record[k] for k in ('ref', 'text', 'origin')):
                            raise ValueError('source_metadata_conflict')
                        self.put_source({**extra, **record})
                    if prefetch:
                        if set(prefetch['refs']) != {x['ref'] for x in body['sources']}:
                            raise ValueError('prediction_read_incomplete')
                        work.pop('prefetch')
                        emitted = self.prepare_snapshot(event, work)
                    else:
                        emitted = self.continue_request(event, work)
                elif prefetch:
                    work.setdefault('unread_observations', []).extend(prefetch['refs'])
                    work.pop('prefetch')
                    emitted = self.prepare_snapshot(event, work)
                else:
                    if request['payload']['capability'] == 'analyze_world_model':
                        result = MindResultEvent(data['request_ref'], error='observation_too_large')
                    else:
                        result = MindResultEvent(data['request_ref'], observation)
                    emitted = self.receipt(event, work, self.cognition.accept_result(result))
            elif event.kind == 'analysis.request':
                if data['request_ref'] != work['request']['request_ref']:
                    raise ValueError('cognitive_analysis_identity_conflict')
                try:
                    if self.state.get('stage1_authority'):
                        observation = self.analysis.analyze(data['request_ref'], work['request']['payload'],
                                                            owner_task=work['analysis_task'])
                    else:
                        observation = self.analysis.analyze(data['request_ref'], work['request']['payload'])
                except (RuntimeError, ValueError, ValidationError, httpx.HTTPError) as error:
                    expected = {'builder_evidence_bound', 'builder_call_outcome_unknown',
                        'builder_terminal_or_cardinality_failure', 'builder_unknown_tool',
                        'builder_unknown_run', 'builder_report_bound', 'builder_activity_budget_exhausted',
                        'invalid_model_reference', 'unknown_owner_source'}
                    if not isinstance(error, httpx.HTTPError) and str(error) not in expected:
                        raise  # Persistence, integrity and isolation failures remain mechanism stops.
                    write_json(self.directory / 'failures' / (fingerprint(data['request_ref']) + '.json'),
                        {'request_ref': data['request_ref'], 'exception': type(error).__name__,
                         'detail': str(error)[:2000], 'safe_code': 'model_failed'})
                    emitted = (reply(event, 'mind.results', 'analysis.result',
                        {**data, 'error': 'model_failed'}),)
                else:
                    emitted = (reply(event, 'mind.results', 'analysis.result',
                        {**data, 'observation': observation}),)
            else:
                if data['request_ref'] != work['request']['request_ref']:
                    raise ValueError('cognitive_analysis_identity_conflict')
                observation = data.get('observation')
                watches = self.register_prediction(event, work, observation) if observation is not None else ()
                receipt = self.cognition.accept_result(MindResultEvent(data['request_ref'], observation,
                                                                       error=data.get('error')))
                emitted = (*watches, *self.receipt(event, work, receipt))
        self.state['handled'][event.event_id] = {'input_sha256': digest,
            'emitted': [event_document(x) for x in emitted]}
        self.save()
        return tuple(emitted)

    def begin(self, event, data, *, attention=None):
        activity_id = 'mind-' + fingerprint(event.event_id)[:24]
        if event.kind == 'mind.retry':
            if set(data) != {'activity_id'} or data['activity_id'] not in self.state['unresolved']:
                raise ValueError('no_failed_cognitive_activity')
            prior = self.state['activities'][data['activity_id']]
            original, trigger = prior['owner_input'], prior['trigger']
        elif event.kind == 'user.input':
            original = {'event_id': event.event_id, **data}
            if data['event_type'] == 'USER_GOAL' and data['text'] != (self.state['task'] or {}).get('business_goal'):
                raise ValueError('owner_goal_identity_conflict')
            ref = self.source(canonical(original), event.event_id, kind='owner_statement')
            if not any(x['event_id'] == event.event_id for x in self.state['owners']):
                if len(self.state['owners']) >= 32:
                    raise ValueError('owner_input_lifetime_bound')
                self.state['owners'].append({**original, 'ref': ref})
            trigger = 'Interpret this user event in the continuing goal: ' + data['event_type'] + ' [' + ref + ']'
        elif event.kind == 'attention.signal':
            original = None
            signal_ref = self.source(canonical(data), event.event_id, kind='attention_signal')
            trigger = 'Review the observed attention signal [' + signal_ref + ']; its conditions are not a verified business conclusion.'
        elif event.kind == 'control.notice':
            control = data['owner_event']
            if control['source'] != 'user' or control['kind'] != 'user.control':
                raise ValueError('invalid_control_origin')
            original = {'event_id': control['event_id'], **control['data']}
            trigger = 'The owner explicitly changed action admission: ' + control['data']['event_type']
        else:
            origin = self.state['activities'][data['origin_activity_id']] if data.get('origin_activity_id') else None
            original, trigger = origin['owner_input'] if origin else None, data['reason']
        work = self.state['activities'].setdefault(activity_id,
            {'id': activity_id, 'trigger': trigger, 'owner_input': original,
             'snapshot': None, 'status': 'pending', 'origin_event': event.event_id})
        if data.get('origin_activity_id'):
            work['origin_activity_id'] = data['origin_activity_id']
        if event.kind == 'mind.retry':
            work['retry_of'] = data['activity_id']
            prior['superseded_by'] = activity_id
        if attention is not None:
            work.setdefault('attention', plain(attention))
        if self.state.get('stage1_authority'):
            pursuit = self.cognition.pursuit_state()
            work.setdefault('task_contract', pursuit['task'] if pursuit else None)
            work.setdefault('event_source', self.source(canonical(event.document()), event.event_id,
                                                       kind='owner_statement' if event.source == 'user' else 'attention_signal'))
        self.state['active'] = activity_id
        self.save()
        if event.kind == 'execution.changed':
            work['snapshot'] = data['snapshot']
            return self.prepare_snapshot(event, work)
        return (reply(event, 'execution', 'execution.inspect', {'activity_id': activity_id}),)

    def prepare_snapshot(self, event, work):
        """Fetch declared observation bodies before comparing; this is owner I/O, not model inquiry."""
        for record in work['snapshot'].get('sources', []):
            self.put_source(record)
        unread = set(work.get('unread_observations', []))
        missing = [ref for ref in work['snapshot'].get('unread_observation_refs', [])
                   if ref not in self.state['sources'] and ref not in unread]
        if missing:
            refs = missing[:3]
            request_ref = work['id'] + ':observation:' + fingerprint(refs)[:12]
            work['prefetch'] = {'request_ref': request_ref, 'refs': refs}
            self.save()
            return (reply(event, 'execution', 'evidence.read', {
                'activity_id': work['id'], 'request_ref': request_ref,
                'refs': refs, 'purpose': 'analysis'}),)
        return self.activate(event, work)

    def evidence(self, text, label, origin='execution', kind='catalogue'):
        ref = self.source(text, label, origin, kind)
        if len(text) > 1000:
            text = canonical({'source_ref': ref, 'chars': len(text),
                'scope': 'Read this immutable original when its content matters; this directory entry does not establish its claims.'})
            ref = self.source(text, label + ' reference', origin)
        return Evidence(ref, text, origin)

    def activate(self, event, work):
        snapshot = work['snapshot']
        for record in snapshot.get('sources', []):
            self.put_source(record)
        evidence = [self.evidence(canonical(snapshot.get('files', [])), 'workspace catalogue')]
        from Nervous.attention import inline_refs
        available = {item['ref']: self.source_record(item['ref']) for item in snapshot.get('files', [])
                     if item['ref'] in self.state['sources']}
        for ref in (() if self.state.get('stage1_authority') else inline_refs(snapshot.get('files', []), available)):
            record = self.source_record(ref)
            evidence.append(Evidence(ref, record['text'], record['origin']))
        owners = self.state['owners']
        if self.state.get('stage1_authority'):
            owners = [owner for owner in owners if owner['event_id'] == (work.get('owner_input') or {}).get('event_id')]
        for owner in owners:
            record = self.source_record(owner['ref'])
            evidence.append(self.evidence(record['text'], record['label'], kind='owner_statement'))
        if snapshot.get('received_guidance'):
            evidence.append(self.evidence(canonical({'received_guidance': snapshot['received_guidance'],
                'scope': 'Prior advice and receiving decision; delivery is not adoption or correctness.'}),
                'guidance under review'))
        if snapshot.get('repetition_observation'):
            # This owner projection already bounds records and externalizes action
            # bodies/source versions. Deliver the same fact as Execution receives,
            # rather than replacing it with a second directory pointer.
            text = canonical(snapshot['repetition_observation'])
            ref = self.source(text, 'Execution repetition observation', 'execution', 'observed_text')
            evidence.append(Evidence(ref, text, 'execution'))
        comparisons = self.compare_predictions(snapshot)
        if comparisons:
            evidence.append(self.evidence(canonical(comparisons), 'declared prediction feedback',
                                          'computation', 'computation'))
        work['reviewed'] = plain(snapshot.get('reviewed', {'deliveries': [], 'predictions': {}}))
        work['reviewed']['predictions'] = {x['model_ref']: x['reality_source'] for x in comparisons
                                          if not x.get('awaiting_observation_read')}
        observation = snapshot.get('observation')
        pursuit_input = None
        task = self.state['task']
        goal = execution_goal(task) if task else self.state['stage1_authority']['text']
        if self.state.get('stage1_authority'):
            from Mind.intention import PURSUIT_VERSION, task_owner
            authority = self.state['stage1_authority']
            # The authority ref has its own original immutable body, not a inferred task summary.
            self.put_source({'ref': authority['ref'], 'text': authority['text'], 'origin': 'execution',
                             'label': 'original autonomous scope', 'source_kind': 'owner_statement'})
            if not any(item.ref == authority['ref'] for item in evidence):
                evidence.append(Evidence(authority['ref'], authority['text'], 'execution'))
            event_source = self.source_record(work['event_source'])
            evidence.append(self.evidence(event_source['text'], event_source['label'], kind=event_source['source_kind']))
            actual = snapshot.get('task_contract')
            task = task_owner(actual, self.state['execution_protocol']) if actual else None
            goal = execution_goal(task) if task else authority['text']
            work['analysis_task'] = {'ref': 'owner-task:' + fingerprint(actual),
                                    'goal': task['business_goal'] if task else authority['text']}
            pursuit_input = {'version': PURSUIT_VERSION, 'mind_id': authority['mind_id'],
                'authorization': {k: authority[k] for k in ('ref', 'text')},
                'task': work['task_contract'], 'execution_task': actual,
                'task_status': snapshot.get('status') if actual == work['task_contract'] else 'not_accepted'}
            if 'selected_item_ids' in work.get('attention', {}):
                pursuit_input['visible_item_ids'] = work['attention']['selected_item_ids']
        value = MindInput(work['id'], work['trigger'], 'owner-goal', 1,
            goal, snapshot.get('execution_ref'), snapshot.get('status'),
            tuple(evidence), ExecutionObservation(**observation) if observation else None,
            owner_task=task, pursuit=pursuit_input, attention=work.get('attention'))
        self.save()
        return self.receipt(event, work, self.cognition.activate(value))

    def receipt(self, event, work, receipt):
        if receipt.status == 'busy':
            raise ValueError('inconsistent_active_cognition')
        if receipt.request is not None:
            request = receipt.request
            work['request'] = {'request_ref': request.request_ref, 'payload': plain(request.payload)}
            work['resolved_refs'] = {}
            remote = []
            for ref in request.payload.get('refs', []):
                try:
                    record = self.source_record(ref)
                    if ref not in self.state['sources']:
                        self.put_source(record)
                except (ValueError, KeyError):
                    remote.append(ref)
            if remote:
                views = self.state.get('stage1_authority') and any(ref.startswith('view:') for ref in remote)
                return (reply(event, 'nervous' if views else 'execution', 'view.read' if views else 'evidence.read', {
                    'activity_id': work['id'], 'request_ref': request.request_ref, 'refs': remote,
                    'purpose': 'analysis' if request.payload['capability'] == 'analyze_world_model' else 'cognition'}),)
            return self.continue_request(event, work)
        work.update(status='accepted' if receipt.status == 'duplicate' else receipt.status,
                    error=receipt.error, revision=receipt.revision)
        self.state['active'] = None
        if work['status'] != 'accepted':
            if work['id'] not in self.state['unresolved']:
                self.state['unresolved'].append(work['id'])
            return ()
        output = plain(receipt.output)
        work['output'] = output
        if output['type'] == 'decision_intent':
            work['status'] = 'formal_intention_change_requires_owner'
            self.state['unresolved'].append(work['id'])
            return ()
        retried = work.get('retry_of')
        while retried is not None:
            if retried in self.state['unresolved']:
                self.state['unresolved'].remove(retried)
            retried = self.state['activities'][retried].get('retry_of')
        for ref, source in work['reviewed']['predictions'].items():
            self.state['predictions'][ref]['reviewed_source'] = source
        directive = {'id': 'directive-' + fingerprint(work['id'])[:24], 'text': output['text']} if output['type'] == 'directive' else None
        decision = {
            'activity_id': work['id'], 'task': self.state['task'],
            'snapshot': {k: v for k, v in work['snapshot'].items()
                         if k not in {'sources', 'observation', 'received_guidance'}},
            'directive': directive, 'reviewed': work['reviewed'],
            'owner_input': work['owner_input']}
        if self.state.get('stage1_authority'):
            from Mind.intention import task_owner
            pursuit = self.cognition.pursuit_state()
            proposed = pursuit['task']
            self.state['task'] = task_owner(proposed, self.state['execution_protocol']) if proposed else None
            decision.update(task=self.state['task'], task_contract=proposed)
            work['effects'] = plain(receipt.effects) if receipt.effects else {'intentions': [], 'task': None, 'watches': []}
            return (reply(event, 'nervous', 'mind.effects', {
                'activity_id': work['id'], 'effects': work['effects'], 'decision': decision}),)
        return (reply(event, 'execution', 'mind.decision', decision),)

    def continue_request(self, event, work):
        request = work['request']
        if request['payload']['capability'] == 'analyze_world_model':
            return (reply(event, 'mind.analysis', 'analysis.request', {
                'activity_id': work['id'], 'request_ref': request['request_ref']}),)
        records = [self.source_record(work.get('resolved_refs', {}).get(ref, ref)) for ref in request['payload']['refs']]
        body = {'read_result': 'sources-v1', 'sources': [
            {k: r[k] for k in ('ref', 'text', 'origin')} for r in records]}
        metadata = {record['ref']: {key: record[key] for key in READ_SOURCE_METADATA_FIELDS if key in record}
                    for record in records if record['ref'].startswith('history:')}
        if metadata:
            body['source_metadata'] = metadata
        text = canonical(body)
        observation = {'capability': 'read_evidence', 'text': text,
            'origin': 'computation' if any(r['origin'] == 'computation' for r in records) else 'execution'}
        if len(text) > 8000 or len(canonical(observation)) > 9000:
            observation['text'] = canonical({'read_result': 'capacity-v1', 'status': 'not_read',
                'reason': 'response_capacity', 'required_text_chars': len(text),
                'required_observation_chars': len(canonical(observation)), 'max_text_chars': 8000,
                'max_observation_chars': 9000, 'sources': [
                    {'ref': r['ref'], 'text_chars': len(r['text']), 'origin': r['origin']} for r in records]})
        return self.receipt(event, work,
            self.cognition.accept_result(MindResultEvent(request['request_ref'], observation)))

    def register_prediction(self, event, work, observation):
        report = json.loads(observation['text'])
        ref = report.get('model_ref')
        if not ref or report['kind'] != 'COMPUTED_CONDITIONAL':
            return ()
        if ref in self.state['predictions']:
            prediction = self.state['predictions'][ref]
            # Replay the same not-yet-acknowledged watch after a commit cut.
            return (reply(event, 'execution', 'prediction.watch', {k: prediction[k] for k in (
                'ref', 'activity_id', 'before_observation_ref', 'observation_file', 'check_spec',
                *(('task_contract',) if self.state.get('stage1_authority') else ()))}),) if prediction['activity_id'] == work['id'] else ()
        artifact = self.analysis.model(ref)
        if 'check_spec' not in artifact:
            return ()
        before = next((x['ref'] for x in work['snapshot']['files']
                       if x['file'] == artifact['observation_file']), None)
        prediction = {'ref': ref, 'activity_id': work['id'], 'before_observation_ref': before,
                      'observation_file': artifact['observation_file'], 'check_spec': artifact['check_spec']}
        if self.state.get('stage1_authority'):
            prediction['task_contract'] = work['snapshot'].get('task_contract')
        self.state['predictions'][ref] = prediction
        self.save()
        return (reply(event, 'execution', 'prediction.watch', {k: prediction[k] for k in (
                'ref', 'activity_id', 'before_observation_ref', 'observation_file', 'check_spec',
                *(('task_contract',) if self.state.get('stage1_authority') else ()))}),)

    def compare_predictions(self, snapshot):
        reports = []
        for ref, prediction in self.state['predictions'].items():
            if self.state.get('stage1_authority') and ref not in snapshot.get('reviewed', {}).get('predictions', {}):
                continue
            item = next((x for x in snapshot.get('files', [])
                         if x['file'] == prediction['observation_file']), None)
            source_ref = item['ref'] if item else None
            if ('reviewed_source' in prediction and source_ref == prediction['reviewed_source']
                    or 'reviewed_source' not in prediction
                    and source_ref == prediction['before_observation_ref']
                    and ref not in snapshot.get('reviewed', {}).get('predictions', {})):
                continue
            awaiting_read = source_ref in snapshot.get('unread_observation_refs', []) and source_ref not in self.state['sources']
            observed = None
            if source_ref and source_ref in self.state['sources']:
                text = self.source_record(source_ref)['text']
                try:
                    observed = json.loads(text)
                except ValueError:
                    observed = text
            artifact = self.analysis.model(ref)
            alignment = ({'status': 'unverified', 'reason': 'observation_source_not_read',
                          'comparison': None} if awaiting_read else
                         compare_observation_contract(artifact['run'], prediction['check_spec'],
                             observed, fresh=source_ref != prediction['before_observation_ref']))
            report = {'model_ref': ref, 'reality_source': source_ref,
                'scope': 'Declared final observables only; not full trajectory or business success.',
                'awaiting_observation_read': awaiting_read, 'alignment': alignment}
            report['report_ref'] = self.source(canonical(report), 'prediction comparison', 'computation', 'computation')
            reports.append(report)
        return reports

    def status(self):
        view = self.cognition.inspect()
        return {'goal': (self.state['task'] or {}).get('business_goal'), 'revision': view.revision,
            **({'pursuit': self.cognition.pursuit_state()} if self.state.get('stage1_authority') else {}),
            'items': plain(view.items), 'active': self.state['active'],
            'unresolved': [{'activity_id': key, 'status': self.state['activities'][key]['status'],
                           'error': self.state['activities'][key].get('error')}
                          for key in self.state['unresolved']],
            'predictions': list(self.state['predictions']), **self._context_status(self.directory, self.state)}
