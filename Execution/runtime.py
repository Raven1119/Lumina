"""Execution's event-facing owner: actions, attributed observations and receipts.

Mind supplies direction through inert events. This owner does not call Mind or
interpret beliefs; ordinary action boundaries simply return control to Nervous.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from Nervous.organ import Event
from Nervous.provider import BudgetPause
from Nervous.storage import canonical, fingerprint, plain, read_json, write_json
from Execution.evidence import EvidenceStore, workspace_path
from Execution.execution import FileContentEquals
from Execution.model import EXECUTION_PROTOCOL, EXECUTION_ROLE, ExecutionHistory, ExecutionModel, execution_goal, no_tool_response
from Execution.organ import ExecutionOrgan
from Execution.sandbox import DockerIPython


def reply(event, kind, data):
    return Event(kind + '-' + fingerprint(event.event_id)[:24], 'execution',
                 'mind.results', kind, data, event.event_id)


def advisory(text):
    return ('[Mind Supervisor Directive]\nTreat this high-level guidance as a strong advisory prior, '
            'not an order or execution plan.\n' + text)


class Execution:
    """One authorized workspace; handlers persist intent before any action."""
    def __init__(self, directory, calls, workspace=None, ipython=None, *, context_mode=None,
                 stage1_authority=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'run.json'
        self.calls, self.history, self._ipython = calls, ExecutionHistory(calls), ipython
        self.actor = self.control = None
        if self.path.exists():
            document = read_json(self.path)
            if document['sha256'] != fingerprint(document['state']):
                raise ValueError('execution_state_integrity_failure')
            self.state = document['state']
            if self.state['format'] != 'execution-runtime-1':
                raise ValueError('unsupported_execution_runtime')
            if workspace is not None and str(Path(workspace).resolve()) != self.state['workspace']:
                raise ValueError('execution_workspace_identity_conflict')
        else:
            if workspace is None:
                raise ValueError('execution_workspace_required')
            self.state = {'format': 'execution-runtime-1', 'workspace': str(Path(workspace).resolve(strict=True)),
                'task': None, 'active_run': None, 'prior_runs': [], 'deliveries': [], 'predictions': [],
                'owners': [], 'handled': {}, 'outbox': [], 'announced': [], 'handled_requests': [],
                'last_reviewed': None, 'stop_reason': None, 'initializing': None}
        if stage1_authority is not None:
            if (type(stage1_authority) is not dict or set(stage1_authority) != {'ref', 'text', 'mind_id'}
                    or any(type(value) is not str or not value for value in stage1_authority.values())):
                raise ValueError('invalid_execution_authority')
            if self.path.exists() and self.state.get('stage1_authority') != stage1_authority:
                raise ValueError('execution_authority_conflict')
            self.state.setdefault('stage1_authority', dict(stage1_authority))
            self.state.setdefault('task_contract', None)
            self.state.setdefault('task_contracts', [])
        if context_mode not in (None, 'baseline', 'mask', 'summary'):
            raise ValueError('unsupported_context_mode')
        if self.path.exists() and context_mode is not None and context_mode != self.state.get('context_mode', 'baseline'):
            raise ValueError('context_mode_is_fixed_at_start')
        if context_mode is not None:
            self.state['context_mode'] = context_mode
        self.workspace = Path(self.state['workspace']).resolve(strict=True)
        if (not self.workspace.is_dir() or self.directory.is_relative_to(self.workspace)
                or self.workspace.is_relative_to(self.directory)
                or calls.directory.resolve().is_relative_to(self.workspace)):
            raise ValueError('execution_workspace_must_be_disjoint_from_private_state')
        self.evidence = EvidenceStore(self.directory / 'sources', self.workspace)
        if self.state['active_run'] is not None:
            self.open_actor()
        self.save()

    def save(self):
        write_json(self.path, {'state': self.state, 'sha256': fingerprint(self.state)})

    @classmethod
    def inspect_directory(cls, directory):
        from Execution.execution import EventLog, fold_execution_state, _unsettled_action_start
        directory = Path(directory)
        if not (directory / 'run.json').exists():
            return {'status': 'not_initialized'}
        document = read_json(directory / 'run.json')
        owner = document['state']
        if document['sha256'] != fingerprint(owner) or owner['format'] != 'execution-runtime-1':
            raise ValueError('execution_state_integrity_failure')
        state, diagnostic, events = None, {'valid_records': 0, 'issue': None}, ()
        if owner['active_run'] is not None:
            run = str(owner['active_run'])
            if not run.isdecimal():
                raise ValueError('invalid_run_directory')
            events, diagnostic = EventLog.inspect_path(directory / 'runs' / run / 'events.jsonl')
            if events:
                state = fold_execution_state(events)
        pending = _unsettled_action_start(events)
        # A recorded transport failure can still leave the action outcome unknown.
        unknown = dict(owner.get('unknown_action') or {})
        if pending:
            unknown.update(event_id=pending.event_id,
                decision_event_id=pending.source_event_refs[0],
                provider_tool_call_id=pending.payload.get('provider_tool_call_id'))
        context = {'mode': owner.get('context_mode', 'baseline')}
        if state is not None and context['mode'] == 'summary':
            from working_context import inspect_context
            from Nervous.storage import inspect_safely
            context.update(inspect_safely(lambda: inspect_context(
                directory / 'runs' / str(owner['active_run']) / 'handoff.json',
                role='execution', scope=state.execution_id)))
        return {'status': state.status if state else ('unavailable' if diagnostic['issue'] else 'not_started'),
            **({'control': owner.get('control'), 'task_contract': owner.get('task_contract')}
               if 'stage1_authority' in owner else {}),
            'working_context': context,
            'execution_ref': state.execution_id if state else None,
            'decision_count': state.decision_count if state else 0,
            'waiting_for': state.waiting_for if state else None,
            'stop_reason': owner['stop_reason'], 'diagnostic': diagnostic,
            'recovery': {'last_event_type': events[-1].event_type if events else None,
                         'unknown_action': unknown or None},
            'deliveries': [{k: v for k, v in d.items() if k not in {'text', 'files'}} for d in owner['deliveries']],
            'predictions': [{'ref': p['ref'], 'reviewed_source': p.get('reviewed_source')}
                            for p in owner['predictions']],
            'outbox': [e['event_id'] for e in owner['outbox']],
            'observation_poll': 'not_performed_by_status'}

    def open_actor(self):
        directory = self.directory / 'runs' / str(self.state['active_run'])
        history_directory = None
        if self.state.get('context_mode', 'baseline') != 'baseline':
            history_directory = directory / 'history'
            history_directory.mkdir(parents=True, exist_ok=True)
        self.control = self._ipython or DockerIPython(self.workspace, history_directory=history_directory)
        self.actor = ExecutionOrgan(workspace=self.workspace,
            event_log_path=directory / 'events.jsonl', checkpoint_path=directory / 'checkpoint.json',
            max_decisions=120, max_depth=1, max_context_chars=16000, max_decisions_per_advance=1,
            completion_review_required=self.feedback_required,
            changed_decision_context=self.changed_decision_context,
            before_dispatch=self.calls.check_pause,
            model=ExecutionModel(self.call_execution, owner_task=self.state['task'],
                execution_context=self.execution_context, role_prompt=EXECUTION_ROLE,
                history=self.history,
                recovery_context=self.recovery_context,
                incoming_event_pending=lambda: self.actor.has_unhandled_external_event()),
            ipython_control=self.control)
        state = self.actor.state
        if state is not None and state.goal != execution_goal(self.state['task']):
            raise ValueError('execution_owner_goal_conflict')

    def close(self):
        if self.actor is not None:
            self.actor.shutdown()
            self.actor = None

    def run_state(self):
        return self.actor.state if self.actor is not None else None

    def task_binding(self):
        contract = self.state.get('task_contract')
        return fingerprint(contract) if contract else None

    def task_scoped(self, records):
        if 'stage1_authority' not in self.state:
            return records
        return [item for item in records if item.get('task_binding') == self.task_binding()]

    def current_review(self):
        review = self.state['last_reviewed']
        return review if not review or self.task_scoped([review]) else None

    def original_authorization(self):
        authority = self.state.get('stage1_authority')
        if authority is None:
            return None
        return {'kind': 'original_authorization', 'source_ref': authority['ref'], 'text': authority['text'],
                'source_kind': 'owner_statement',
                'scope': 'Immutable owner authorization shared across Tasks. A Task or guidance does not '
                         'expand this scope, workspace, tools or cumulative budget.'}

    def view(self, name, *, execution_ref=None, event_ref=None, offset=0, limit=4):
        """Read saved owner facts; no environment sampling or Actor construction."""
        from Execution.execution import EventLog, fold_execution_state, _encode_value, _unsettled_action_start
        if name not in {'execution.state', 'execution.history'}:
            raise ValueError('unknown_execution_view')
        if (type(offset) is not int or offset < 0 or type(limit) is not int
                or not 1 <= limit <= (6000 if event_ref is not None else 8)
                or event_ref is not None and (name != 'execution.history' or type(event_ref) is not str)):
            raise ValueError('invalid_execution_view_range')
        document = read_json(self.path)
        owner = document['state']
        if document['sha256'] != fingerprint(owner):
            raise ValueError('execution_state_integrity_failure')
        candidates = [(owner['active_run'], owner.get('task_contract'))]
        if execution_ref is not None:
            candidates.extend((item['run'], item.get('task_contract')) for item in owner['prior_runs'])
        state, events, task = None, (), owner.get('task_contract')
        for run, contract in candidates:
            if run is None:
                continue
            if not str(run).isdecimal():
                raise ValueError('invalid_run_directory')
            path = self.directory / 'runs' / str(run) / 'events.jsonl'
            if not path.exists():
                continue
            candidate_events = EventLog.load(path).events
            if not candidate_events:
                continue
            candidate = fold_execution_state(candidate_events)
            if execution_ref is None or candidate.execution_id == execution_ref:
                state, events, task = candidate, candidate_events, contract
                break
        missing = state is None
        next_offset, truncated = None, False
        if name == 'execution.state':
            unknown = dict(owner.get('unknown_action') or {})
            if state is not None and unknown.get('execution_ref') != state.execution_id:
                unknown = {}
            pending = _unsettled_action_start(events)
            if pending:
                unknown.update(event_id=pending.event_id, decision_event_id=pending.source_event_refs[0],
                               provider_tool_call_id=pending.payload.get('provider_tool_call_id'))
            historical = next((item for item in owner['prior_runs']
                               if state and item['execution_ref'] == state.execution_id), None)
            content = {'execution_ref': state.execution_id if state else None,
                'status': state.status if state else 'not_started',
                'waiting_for': state.waiting_for if state else None,
                'decision_count': state.decision_count if state else 0,
                'completion_scope': 'Runtime acknowledgment, not business acceptance.',
                'unknown_action': unknown or None,
                'stop_reason': owner['stop_reason'] if historical is None else None,
                'control': owner.get('control') if historical is None else None,
                'historical': historical is not None,
                'retirement': historical.get('retirement') if historical else None}
        else:
            allowed = {'IPYTHON_EXECUTION_STARTED', 'IPYTHON_EXECUTION_RESULT', 'IPYTHON_EXECUTION_FAILED',
                'TOOL_CALL_STARTED', 'TOOL_RESULT', 'TOOL_FAILED', 'ACTION_RECONCILED',
                'ROOT_WAITING', 'ROOT_WOKEN', 'COMPLETION_VERIFIED', 'COMPLETION_REJECTED',
                'COMPLETION_DEFERRED', 'DECISION_RETIRED', 'EXECUTION_COMPLETED', 'EXECUTION_FAILED'}
            selected = [event for event in events if event.event_type in allowed]
            content, chars = [], 0
            for event in selected[offset:offset + limit]:
                text = canonical(_encode_value(event.payload))
                record = {'ref': event.event_id, 'sequence': event.sequence, 'kind': event.event_type,
                    'causes': list(event.source_event_refs), 'text': text[:2000],
                    'original_chars': len(text), 'truncated': len(text) > 2000}
                size = len(canonical(record))
                if content and chars + size > 6000:
                    break
                content.append(record)
                chars += size
            next_offset = offset + len(content) if offset + len(content) < len(selected) else None
            truncated = next_offset is not None or any(record['truncated'] for record in content)
            if event_ref is not None:
                event = next((item for item in selected if item.event_id == event_ref), None)
                text = canonical(_encode_value(event.payload)) if event else ''
                missing = event is None
                content = {'ref': event_ref, 'text': text[offset:offset + limit],
                           'original_chars': len(text), 'offset': offset}
                next_offset = offset + limit if offset + limit < len(text) else None
                truncated = next_offset is not None or offset > 0
        sequence = events[-1].sequence if events else 0
        result = {'owner': 'execution', 'revision': fingerprint([document['sha256'], sequence]),
            'as_of': {'owner_state': document['sha256'], 'execution_sequence': sequence},
            'scope': {'execution_ref': state.execution_id if state else execution_ref,
                      'task': {'id': task['id'], 'revision': task['revision']} if task else None,
                      'sampling': 'saved_owner_state'},
            'content': content, 'missing': missing, 'truncated': truncated, 'next_offset': next_offset}
        return {**result, 'ref': 'execution-view:' + fingerprint([name, event_ref, offset, limit, result])[:24]}

    def read_source(self, ref):
        """Return a verified immutable source through its owning interface."""
        return self.evidence.read(ref)

    def refresh_environment(self):
        """Explicitly sample the authorized workspace, unlike saved views/status."""
        return self.capture()

    def source_observation(self, relative):
        """Sample one authorized source; Nervous owns watch occurrence identity."""
        path = workspace_path(self.workspace, relative)
        lexical = self.workspace / relative
        if any(part.is_symlink() for part in (lexical, *lexical.parents) if part != self.workspace):
            raise ValueError('workspace_symlink_not_authorized')
        if not path.exists():
            return {'file': relative, 'ref': None, 'missing': True}
        if not path.is_file():
            raise ValueError('workspace_source_is_not_file')
        return {**self.evidence.file(relative), 'missing': False}

    def watched(self):
        return tuple(p['observation_file'] for p in self.task_scoped(self.state['predictions']))

    def dependencies(self, files=None):
        state = self.run_state()
        run = state.execution_id if state else None
        deliveries = [d for d in self.state['deliveries'] if d['execution_ref'] == run
                      and not d['status'].startswith('expired') and not d.get('reviewed_by')]
        predictions = []
        for p in self.task_scoped(self.state['predictions']):
            if p.get('execution_ref') != run:
                continue
            source = self.observation_ref(p, files)
            if 'reviewed_source' not in p or source != p['reviewed_source']:
                predictions.append(p)
        return deliveries, predictions

    def feedback_required(self):
        return any(self.dependencies())

    def observation_ref(self, prediction, files=None):
        if files is not None:
            return next((x['ref'] for x in files if x['file'] == prediction['observation_file']), None)
        relative = prediction['observation_file']
        path = workspace_path(self.workspace, relative)
        return self.evidence.file(relative)['ref'] if path.is_file() else None

    def received_guidance(self, count):
        state = self.run_state()
        result = []
        for d in self.state['deliveries']:
            if (state is None or d['execution_ref'] != state.execution_id or not d.get('call_ref')
                    or d['status'].startswith('expired') or int(d['decision'][9:]) > count):
                continue
            result.append({key: d[key] for key in
                ('id', 'activity_id', 'execution_ref', 'decision', 'text', 'call_ref', 'owner_sequence')})
            result[-1]['later_owner_input_sequences'] = [x['sequence'] for x in self.state['owners']
                                                        if x['sequence'] > d['owner_sequence']
                                                        and self.task_scoped([x])]
        return result

    def retry_context(self):
        state = self.run_state()
        if state is None:
            return False
        from working_context import retry_failed_compaction
        return retry_failed_compaction(
            self.directory / 'runs' / str(self.state['active_run']) / 'handoff.json', self.calls,
            role='execution', scope=state.execution_id, segments=self.actor.history_segments())

    def execution_context(self, count, *, force=False):
        state = self.run_state()
        authorization = self.original_authorization()
        owners = [authorization] if authorization else []
        for item in self.task_scoped(self.state['owners']):
            value = {k: v for k, v in item.items() if k != 'text'}
            if len(item['text']) <= 2000:
                value['text'] = item['text']
            else:
                value['text_ref'] = item['source_ref']
                value['text_chars'] = len(item['text'])
                value['scope'] = 'Exact owner statement retained externally; request Mind if its full text matters.'
            owners.append(value)
        mode = self.state.get('context_mode', 'baseline')
        projection = {}
        limit = 6 if mode == 'baseline' else 120
        rounds = self.history.history(count, state.execution_id,
            self.actor.committed_tool_calls(limit=limit), limit=limit,
            max_chars=60000 if mode == 'baseline' else 10000000)
        if mode != 'baseline':
            segments = self.actor.history_segments()
            directory = self.directory / 'runs' / str(self.state['active_run'])
            for segment in segments:
                path = directory / 'history' / (fingerprint(segment['ref']) + '.json')
                if path.exists():
                    if read_json(path) != segment:
                        raise ValueError('execution_history_projection_conflict')
                else:
                    write_json(path, segment)
            covered = set()
            if mode == 'summary':
                from working_context import compact
                summary = compact(directory / 'handoff.json', self.calls, role='execution',
                    scope=state.execution_id, segments=segments, force=force,
                    retain=1 if force else 6,
                    instructions='Write an operational handoff, not a new plan or action. Preserve exact relevant '
                    'parameters, known completed effects, failures and their conditions, unfinished work and unknown '
                    'outcomes with their references. Earlier commands or advice are historical, not current authority. '
                    'Python variables may be lost after restart. Do not repeat old requests or invent results.')
                if summary:
                    covered = set(summary['source_refs'])
                    projection['derived_history_handoff'] = summary
            recent = {s['content']['decision'] for s in segments[-6:]}
            if mode == 'mask':
                def mask_outputs(value):
                    if isinstance(value, dict):
                        return {k: ({'omitted_chars': len(v), 'scope': 'Read original history for output.'}
                                    if k == 'output' and isinstance(v, str) else mask_outputs(v)) for k, v in value.items()}
                    return [mask_outputs(v) for v in value] if isinstance(value, list) else value
                projection['masked_execution_history'] = [dict(s, content=mask_outputs(s['content']))
                    for s in segments if s['content']['decision'] not in recent]
                rounds = [r for r in rounds if f'decision-{r["decision"] + 1:06d}' in recent]
            else:
                rounds = [r for r in rounds if 'execution-history:' + state.execution_id
                          + f':decision-{r["decision"] + 1:06d}' not in covered]
            paired = {f'decision-{r["decision"] + 1:06d}' for r in rounds if r.get('native_messages')}
            projection['unpaired_execution_history'] = [s for s in segments
                if s['ref'] not in covered and s['content']['decision'] not in paired
                and (mode == 'summary' or s['content']['decision'] in recent)]
            projection['history_catalogue'] = [s['ref'] for s in segments]
            projection['history_read'] = 'read_history(ref, offset=0, limit=8000) in IPython reads only this Run\'s '
            projection['history_read'] += 'saved action/result projection. Source output truncation remains explicit; no hidden tail is recoverable.'
        return {'version': 'execution-context-1' if mode == 'baseline' else 'execution-working-context-1',
            'mode': mode, 'rounds': rounds, **projection,
            'received_guidance': self.received_guidance(count), 'owner_inputs': owners,
            'cognitive_feedback': {'last_reviewed': self.current_review(),
                                  'completion_review_required': self.feedback_required(),
                                  'completion': {
                                      'run_status': state.status,
                                      'deferred_claim_at_current_boundary': self.actor.completion_review_pending(),
                                      'scope': 'Current runtime facts, not business acceptance. A cleared Mind review '
                                          'or a matching marker alone does not finish a running Run. claim_complete '
                                          'submits completion for runtime verification; text replies are not actions. '
                                          'A historical review_pending result is not a fresh review requirement.'}},
            'guidance_scope': 'Exact prior advice in receiving-decision order, not a new delivery or verified fact. '
                'NoChange does not revoke advice. Consider later guidance and owner inputs for current applicability.'}

    def recovery_context(self):
        # These owner facts are projected after ModelRequest construction.
        # NoChange does not make a newly received owner statement disappear.
        return {'owner_inputs': [item['source_ref'] for item in self.task_scoped(self.state['owners'])],
                **({'original_authorization': self.original_authorization()}
                   if 'stage1_authority' in self.state else {}),
                'files': self.evidence.snapshot(self.watched())['files'],
                'guidance': [item['id'] for item in self.task_scoped(self.state['deliveries'])
                             if not item['status'].startswith('expired')]}

    def changed_decision_context(self, frame):
        records = self.history.restore_request(frame.actual_request)
        if not records or 'execution_binding' not in records[-1]['metadata']:
            return None  # The baseline did not record these additional owner facts.
        if records[-1]['wire'] != plain(frame.provider_wire_request):
            raise ValueError('execution_decision_wire_conflict')
        before, after = records[-1]['metadata']['execution_binding'], self.recovery_context()
        return {'before': before, 'after': after} if before != after else None

    def preserve_unknown_action(self):
        outcome = self.actor.latest_transport_failure() if self.actor else None
        failure = getattr(self.control, 'failure_diagnostic', None)
        if failure or outcome:
            if not self.state.get('unknown_action'):
                self.state['unknown_action'] = {'execution_ref': self.run_state().execution_id,
                    'event_ref': outcome[0] if outcome else None,
                    'reason': outcome[1] if outcome else 'isolated_kernel_failed'}
                write_json(self.directory / 'action-failure.json', failure or self.state['unknown_action'])
                self.save()
        return bool(self.state.get('unknown_action'))

    def call_execution(self, wire, *, correction_of=None):
        if self.preserve_unknown_action():
            raise BudgetPause('execution_action_outcome_requires_owner_check')
        try:
            previous, metadata = self.history.attempt(wire, correction_of)
        except RuntimeError as error:
            raise BudgetPause(str(error)) from error
        if previous is not None:
            return previous['response']
        used, size = self.calls.ensure(wire, role='execution')
        if self.feedback_required():
            reserves = {'calls': (1, 2), 'output_tokens': (wire['max_tokens'], 2 * 16384),
                        'request_bytes': (size, 2 * 240000)}
            if any(used['allocated_output_tokens' if key == 'output_tokens' else key] + current + reserve
                   > self.calls.limits[key] for key, (current, reserve) in reserves.items()):
                raise BudgetPause('feedback_budget_reserved')
        return self.calls.call('execution', wire, metadata=metadata)

    def reconcile_deliveries(self):
        state = self.run_state()
        if state is None:
            return
        retired = self.actor.retired_decisions()
        committed = {decision for decision, _ in self.actor.committed_tool_calls()}
        for delivery in self.state['deliveries']:
            if delivery['status'] != 'bound':
                continue
            if (delivery['execution_ref'] == state.execution_id
                    and delivery['decision'] in retired and delivery['decision'] not in committed):
                delivery.setdefault('retired_bindings', []).append(delivery['decision'])
                delivery['decision'] = self.actor.next_root_decision_id
                continue  # Rebinding is not receipt or adoption of the guidance.
            for path, record in self.calls.records(role='execution'):
                if record.get('purpose', 'decision') != 'decision':
                    continue
                if record['status'] != 'received' or no_tool_response(record.get('response')):
                    continue
                document = json.loads(record['wire']['messages'][0]['content'][0]['text'])
                checkpoint = document['state']
                if (checkpoint.get('execution_id') != delivery['execution_ref']
                        or f"decision-{checkpoint['decision_count'] + 1:06d}" != delivery['decision']):
                    continue
                if not any(m['role'] == 'user' and m['content'] == advisory(delivery['text'])
                           for m in record['wire']['messages']):
                    continue
                if state.decision_count <= checkpoint['decision_count']:
                    continue  # A received provider response is not yet an owner action.
                delivery.update(status='received', call_ref='execution-call:' + path.stem)
                break

    def capture(self):
        self.reconcile_deliveries()
        state = self.run_state()
        content = self.evidence.snapshot(self.watched())
        latest_request = self.actor.cognitive_requests()[-1] if self.actor and self.actor.cognitive_requests() else None
        request = json.loads(latest_request[1]) if latest_request else None
        request_ref = (self.evidence.put(latest_request[1], 'actor request ' + latest_request[0],
            kind='execution_judgment')['ref'] if latest_request else None)
        result = ({k: v for k, v in asdict(state.last_result).items() if k != 'cognitive_request'}
                  if state and state.last_result else None)
        outcome = {'execution_ref': state.execution_id if state else None,
            'state_version': state.version if state else None, 'status': state.status if state else None,
            'decision_count': state.decision_count if state else 0,
            'completion_review_pending': self.actor.completion_review_pending() if self.actor else False,
            'completion_scope': 'Runtime acknowledgment only, not business acceptance.',
            'historical_execution_request': {'ref': request_ref, 'event_ref': latest_request[0],
                'scope': 'Earlier actor judgment, not a latest observation.'} if latest_request else None,
            'last_tool_result': result,
            'result_scope': 'Observed tool output; its printed assertions are not independent reality verification.'}
        if len(canonical(outcome)) > 1800 and result is not None:
            record = self.evidence.put(canonical(result),
                f"tool result {state.execution_id} at state {state.version}")
            outcome['last_tool_result'] = {'ref': record['ref'], 'chars': len(record['text'])}
        deliveries, predictions = self.dependencies(content['files'])
        guidance = self.received_guidance(state.decision_count if state else 0)
        compact_guidance = []
        for item in guidance:
            record = self.evidence.put(item['text'], 'directive ' + item['id'], kind='mind_judgment')
            compact_guidance.append({**{k: v for k, v in item.items() if k != 'text'},
                                     'text_ref': record['ref'], 'text_chars': len(item['text'])})
            if len(item['text']) <= 800:
                compact_guidance[-1]['text'] = item['text']
        content.update(execution_ref=state.execution_id if state else None,
            decision=self.actor.next_root_decision_id if self.actor else 'decision-000001',
            decision_count=state.decision_count if state else 0,
            state_version=state.version if state else None, status=state.status if state else None,
            waiting_for=state.waiting_for if state else None,
            completion_review_pending=self.actor.completion_review_pending() if self.actor else False,
            request=request, request_ref=request_ref, request_event=latest_request[0] if latest_request else None,
            received_guidance=compact_guidance,
            reviewed={'deliveries': [d['id'] for d in deliveries if d.get('call_ref')],
                      'predictions': {p['ref']: self.observation_ref(p, content['files']) for p in predictions}},
            observation={'goal': execution_goal(self.state['task']), 'status': state.status,
                         'recent_outcome': canonical(outcome), 'failure': state.failure} if state else None)
        if 'stage1_authority' in self.state:
            content['task_contract'] = self.state['task_contract']
        return content

    @staticmethod
    def target(snapshot):
        result = {key: snapshot.get(key) for key in
                  ('execution_ref', 'decision', 'state_version', 'status', 'files')}
        if 'task_contract' in snapshot:
            result['task_contract'] = snapshot['task_contract']
        return result

    def outcome_identity(self, snapshot):
        # A waiting Actor can keep the same checkpoint across many reviews.
        # The accepted review distinguishes a later return to old content from
        # retries of the pending notification, with or without an Actor.
        review = self.state['last_reviewed']
        return fingerprint([self.target(snapshot), snapshot['request_event'],
                            review['activity_id'] if review else None])

    def handle(self, event):
        routed_decision = ('stage1_authority' in self.state and event.source == 'nervous'
                           and event.kind in {'mind.decision', 'execution.control'})
        if event.target != 'execution' or (event.source not in {'mind', 'mind.results'} and not routed_decision):
            raise ValueError('execution_event_authority_conflict')
        digest = fingerprint(event.document())
        previous = self.state['handled'].get(event.event_id)
        if previous:
            if previous['digest'] != digest:
                raise ValueError('execution_event_identity_conflict')
            return tuple(Event(**x) for x in previous['responses'])
        data = plain(event.data)
        if event.kind == 'execution.control':
            if (not routed_decision or set(data) != {'action', 'control_ref'}
                    or data['action'] not in {'stop', 'resume', 'revoke'}
                    or type(data['control_ref']) is not str or not data['control_ref']):
                raise ValueError('invalid_execution_control')
            if data['action'] != 'resume' or (self.state.get('control') or {}).get('action') != 'revoke':
                self.state['control'] = data
            emitted = ()
        elif event.kind == 'execution.inspect':
            emitted = (reply(event, 'execution.snapshot', {'activity_id': data['activity_id'], 'snapshot': self.capture()}),)
        elif event.kind == 'evidence.read':
            try:
                result = self.evidence.read_result(data['refs'], analysis=data.get('purpose') == 'analysis')
            except ValueError as error:
                if str(error) not in {'unknown_execution_source', 'invalid_evidence_refs'}:
                    raise
                write_json(self.directory / 'failures' / (fingerprint(event.event_id) + '.json'),
                    {'event': event.document(), 'detail': str(error), 'safe_code': 'model_failed'})
                result = {'error': 'model_failed'}
            emitted = (reply(event, 'evidence.result', {'activity_id': data['activity_id'],
                'request_ref': data['request_ref'], **result}),)
        elif event.kind == 'prediction.watch':
            if 'stage1_authority' in self.state:
                if 'task_contract' not in data:
                    raise ValueError('prediction_task_contract_required')
                if data['task_contract'] != self.state['task_contract']:
                    emitted = (reply(event, 'prediction.receipt', {'activity_id': data['activity_id'],
                        'ref': data['ref'], 'status': 'stale_task'}),)
                    self.state['handled'][event.event_id] = {'digest': digest,
                        'responses': [item.document() for item in emitted]}
                    self.save()
                    return emitted
            workspace_path(self.workspace, data['observation_file'])
            previous = next((p for p in self.state['predictions'] if p['ref'] == data['ref']), None)
            registration = {k: data[k] for k in
                ('ref', 'activity_id', 'before_observation_ref', 'observation_file', 'check_spec')}
            if previous and any(previous[k] != value for k, value in registration.items()):
                raise ValueError('prediction_watch_identity_conflict')
            if not previous:
                if len(self.state['predictions']) >= 32:
                    raise ValueError('prediction_watch_bound')
                state = self.run_state()
                self.state['predictions'].append({**registration,
                    'execution_ref': state.execution_id if state else None,
                    **({'task_binding': self.task_binding()} if 'stage1_authority' in self.state else {}),
                    'registered_after_review': (self.state['last_reviewed'] or {}).get('activity_id')})
            emitted = (reply(event, 'prediction.receipt', {'activity_id': data['activity_id'],
                'ref': data['ref'], 'status': 'watching'}),)
        elif event.kind == 'mind.decision':
            emitted = (reply(event, 'execution.receipt', self.accept_decision(event, data)),)
        else:
            raise ValueError('unsupported_execution_event')
        self.state['handled'][event.event_id] = {'digest': digest, 'responses': [x.document() for x in emitted]}
        self.save()
        return emitted

    def accept_decision(self, event, data):
        task = data['task']
        if (self.state.get('control') or {}).get('action') in {'stop', 'revoke'}:
            state = self.run_state()
            return {'activity_id': data['activity_id'], 'status': 'control_blocked',
                    'execution_ref': state.execution_id if state else None,
                    'decision': self.actor.next_root_decision_id if self.actor else None}
        directive = data.get('directive')
        if directive is not None and (set(directive) != {'id', 'text'}
                or not isinstance(directive['text'], str) or not directive['text'].strip()
                or len(directive['text']) > 6000):
            raise ValueError('invalid_execution_guidance')
        for prediction in self.state['predictions']:
            if (prediction['ref'] in data['reviewed']['predictions']
                    and data['reviewed']['predictions'][prediction['ref']]
                    != self.observation_ref(prediction, data['snapshot']['files'])):
                raise ValueError('prediction_review_source_conflict')
        current = self.capture()
        initialization = self.state['initializing']
        recovering = initialization is not None and initialization['event_id'] == event.event_id
        contract = data.get('task_contract')
        if 'stage1_authority' in self.state:
            rejection = self.accept_task_contract(contract, task, directive, data['snapshot'], current, recovering)
            if rejection:
                if rejection == 'superseded':
                    self.request_reassessment(event.event_id, data['activity_id'], current,
                        'The reviewed Execution snapshot changed before the proposed Task could be accepted. '
                        'Reassess the original event against the current observation; no Task or direction was accepted.')
                return {'activity_id': data['activity_id'], 'status': rejection,
                        'execution_ref': current['execution_ref'], 'decision': current['decision']}
        else:
            execution_goal(task)
        if ('stage1_authority' not in self.state and self.state['task'] is not None
                and task != self.state['task']):
            raise ValueError('execution_owner_goal_conflict')
        self.state['task'] = task
        response = {'activity_id': data['activity_id'], 'status': 'accepted',
                    'execution_ref': current['execution_ref'], 'decision': current['decision']}
        if self.target(data['snapshot']) != self.target(current) and not recovering:
            response['status'] = 'superseded'
            self.request_reassessment(event.event_id, data['activity_id'], current,
                'The reviewed Execution snapshot changed before the decision could be accepted. '
                'Reassess the original event against the current observation; no direction was delivered.')
            return response
        owner = data.get('owner_input')
        if owner and not any(x['event_id'] == owner['event_id'] for x in self.state['owners']):
            if len(self.state['owners']) >= 32:
                raise ValueError('owner_input_lifetime_bound')
            record = self.evidence.put(canonical(owner), 'owner event ' + owner['event_id'], kind='owner_statement')
            self.state['owners'].append({**owner, 'sequence': len(self.state['owners']) + 1, 'source_ref': record['ref'],
                                        **({'task_binding': self.task_binding()} if 'stage1_authority' in self.state else {})})
        for d in self.state['deliveries']:
            if d['id'] in data['reviewed']['deliveries'] and d.get('call_ref'):
                d['reviewed_by'] = data['activity_id']
        for p in self.state['predictions']:
            if p['ref'] in data['reviewed']['predictions']:
                source = data['reviewed']['predictions'][p['ref']]
                if source != self.observation_ref(p, data['snapshot']['files']):
                    raise ValueError('prediction_review_source_conflict')
                p.update(reviewed_by=data['activity_id'], reviewed_source=source)
        self.state['last_reviewed'] = {'activity_id': data['activity_id'],
            **self.target(data['snapshot']),
            **({'task_binding': (fingerprint(data['snapshot']['task_contract'])
                                if data['snapshot'].get('task_contract') else None)}
               if 'stage1_authority' in self.state else {})}
        if directive is not None:
            state = self.run_state()
            task_changed = ('stage1_authority' in self.state
                            and current.get('task_contract') != self.state['task_contract'])
            if recovering or task_changed or state is None or state.status in {'completed', 'failed'}:
                if not recovering:
                    self.state['initializing'] = {'event_id': event.event_id, 'prior': self.target(current)}
                    if state:
                        prior_run = {'execution_ref': state.execution_id,
                            'status': state.status, 'run': self.state['active_run'], 'continuation_event': event.event_id,
                            **({'task_contract': current.get('task_contract')} if 'stage1_authority' in self.state else {})}
                        if task_changed and state.status == 'waiting':
                            # A completed Wait remains historical. Any received but
                            # uncommitted next plan is barred by this new Run binding.
                            pending = self.history._restore({'execution_id': state.execution_id,
                                                             'decision_count': state.decision_count})
                            prior_run['retirement'] = {'reason': 'task_revision_replaced_at_wait',
                                'replaced_by': self.task_binding(),
                                'retired_request_refs': [fingerprint(record['wire']) for record in pending]}
                        self.state['prior_runs'].append(prior_run)
                    self.state['active_run'] = 0 if self.state['active_run'] is None else self.state['active_run'] + 1
                    self.save()
                    if self.actor is not None:
                        self.actor.shutdown()
                    self.open_actor()
                if self.actor.state is None:
                    token = 'done:' + self.task_binding()[:24] if self.task_binding() else 'done'
                    self.actor.run_goal(execution_goal(task), FileContentEquals('.lumina-complete', token), defer_actions=True)
                state = self.run_state()
                if state.decision_count != 0:
                    raise ValueError('execution_initialization_already_advanced')
                for p in self.state['predictions']:
                    if (p['execution_ref'] is None and ('stage1_authority' not in self.state
                            or p['activity_id'] == data['activity_id'])):
                        p['execution_ref'] = state.execution_id
                        if 'stage1_authority' in self.state:
                            p['task_binding'] = self.task_binding()
            decision = self.actor.next_root_decision_id
            if decision is None:
                raise ValueError('no_eligible_execution_decision')
            for old in self.state['deliveries']:
                if old['status'] == 'bound' and old['execution_ref'] == state.execution_id:
                    old['status'] = 'expired_by_later_guidance'
            delivery = {**directive, 'activity_id': data['activity_id'], 'event_id': event.event_id,
                'execution_ref': state.execution_id, 'decision': decision, 'status': 'bound',
                'files': current['files'], 'owner_sequence': len(self.state['owners']),
                **({'task_binding': self.task_binding()} if 'stage1_authority' in self.state else {})}
            previous = next((x for x in self.state['deliveries'] if x['id'] == directive['id']), None)
            if previous is not None:
                if previous != delivery:
                    raise ValueError('guidance_identity_conflict')
            else:
                self.state['deliveries'].append(delivery)
            self.state['initializing'] = None
            self.state['stop_reason'] = None
            response.update(status='bound', directive_id=directive['id'],
                            execution_ref=state.execution_id, decision=decision)
        else:
            state = self.run_state()
            if state and owner and state.status == 'waiting' and state.waiting_for == owner['event_type']:
                self.actor.deliver_event(owner['event_type'], owner['text'], defer_actions=True)
            self.state['stop_reason'] = 'awaiting_user' if state is None else None
        return response

    def accept_task_contract(self, contract, task, directive, snapshot, current, recovering):
        """Check an immutable proposal against the still-current receiving boundary."""
        if contract is None:
            if task is not None or directive is not None or self.state['task_contract'] is not None:
                raise ValueError('execution_task_contract_required')
            return None
        fields = {'id', 'revision', 'intention_id', 'intention_revision', 'authority_ref', 'goal', 'acceptance'}
        if (type(contract) is not dict or set(contract) != fields
                or any(type(contract[k]) is not str or not contract[k] for k in fields - {'revision', 'intention_revision'})
                or any(type(contract[k]) is not int or contract[k] < 1 for k in ('revision', 'intention_revision'))):
            raise ValueError('invalid_execution_task_contract')
        if contract['authority_ref'] != self.state['stage1_authority']['ref']:
            raise ValueError('execution_task_authority_conflict')
        token = 'done:' + fingerprint(contract)[:24]
        expected = {'business_goal': contract['goal'] + '\n\nAcceptance:\n' + contract['acceptance'],
                    'execution_protocol': EXECUTION_PROTOCOL.replace('exact content done', 'exact content ' + token)}
        if task != expected:
            raise ValueError('execution_task_projection_conflict')
        execution_goal(task)
        accepted = self.state['task_contract']
        if directive is not None and self.preserve_unknown_action():
            return 'unknown_action'
        if accepted == contract:
            return None
        prior = [item for item in self.state['task_contracts'] if item['id'] == contract['id']]
        if any(item['revision'] == contract['revision'] and item != contract for item in prior):
            raise ValueError('execution_task_version_conflict')
        if prior and contract['revision'] <= max(item['revision'] for item in prior):
            return 'stale_task'
        if contract['revision'] != (max(item['revision'] for item in prior) + 1 if prior else 1):
            raise ValueError('execution_task_version_conflict')
        state = self.run_state()
        if self.preserve_unknown_action():
            return 'unknown_action'
        revising_wait = (state and state.status == 'waiting' and accepted
                        and accepted['id'] == contract['id'])
        if state and state.status not in {'completed', 'failed'} and not revising_wait:
            return 'task_not_settled'
        if revising_wait:
            # Validate any pending provider outcome before mutating owner state.
            self.history._restore({'execution_id': state.execution_id, 'decision_count': state.decision_count})
        if directive is None:
            return 'task_requires_direction'
        if self.target(snapshot) != self.target(current) and not recovering:
            return 'superseded'
        self.state['task_contracts'].append(dict(contract))
        self.state['task_contract'] = dict(contract)
        return None

    def request_reassessment(self, cause, activity_id, snapshot, reason):
        """Persist changed applicability as an event, without choosing new direction."""
        identity = fingerprint([cause, self.target(snapshot)])
        event = Event('execution-reassess-' + identity[:24], 'execution', 'mind',
            'execution.changed', {'snapshot': snapshot, 'reason': reason,
                                  'origin_activity_id': activity_id}, cause)
        if not any(item['event_id'] == event.event_id for item in self.state['outbox']):
            self.state['outbox'].append(event.document())
        outcome = self.outcome_identity(snapshot)
        if outcome not in self.state['announced']:
            self.state['announced'].append(outcome)

    def pending_advisory(self):
        self.reconcile_deliveries()
        state = self.run_state()
        if state is None:
            return None
        for delivery in reversed(self.state['deliveries']):
            if delivery['status'] != 'bound' or delivery['execution_ref'] != state.execution_id:
                continue
            files = self.evidence.snapshot(self.watched())['files']
            if delivery['decision'] != self.actor.next_root_decision_id or delivery['files'] != files:
                delivery['status'] = 'expired_before_request'
                # Remove an already-persisted redirect without driving its action.
                self.actor.resume(decision_advisory=(self.actor.next_root_decision_id, None))
                self.request_reassessment(delivery['event_id'], delivery['activity_id'], self.capture(),
                    'The observed workspace or receiving decision changed before guidance could be delivered. '
                    'The expired guidance was withdrawn without an action. Reassess the original event '
                    'against the current observation.')
                self.save()
                return None
            return (delivery['decision'], advisory(delivery['text']))
        return None

    def advance(self):
        if (self.state.get('control') or {}).get('action') in {'stop', 'revoke'}:
            return False
        state = self.run_state()
        if state is None or state.status in {'completed', 'failed', 'child_pending'}:
            return False
        if self.preserve_unknown_action():
            self.state['stop_reason'] = 'execution_action_outcome_requires_owner_check'
            self.save()
            return False
        guidance = self.pending_advisory()
        if self.state['outbox']:
            return True  # Nervous must deliver the changed observation before another action.
        if state.status == 'waiting' and guidance is None and not self.actor.has_unhandled_external_event():
            return False
        if self.actor.completion_review_pending() and self.feedback_required() and guidance is None:
            return False
        before = state.version
        try:
            self.actor.resume(decision_advisory=guidance)
        except BudgetPause as error:
            changed = self.state['stop_reason'] != str(error)
            self.state['stop_reason'] = str(error)
            self.save()
            if str(error) == 'user_pause_requested':
                raise
            # A newly persisted yield gives the mechanical pump one chance to
            # publish feedback; unchanged budget cannot spin another model call.
            return changed and str(error) == 'feedback_budget_reserved'
        except ValueError as error:
            if str(error) != 'interrupted IPython execution recovery is unsupported':
                raise
            self.state['stop_reason'] = 'execution_action_outcome_requires_owner_check'
            self.state['unknown_action'] = {'execution_ref': state.execution_id, 'reason': str(error)}
            self.save()
            return False
        self.reconcile_deliveries()
        self.preserve_unknown_action()
        self.state['stop_reason'] = None
        self.save()
        return self.run_state().version != before

    def poll(self, *, observation_cycle=None):
        if self.state['outbox']:
            return tuple(Event(**x) for x in self.state['outbox'])
        state = self.run_state()
        if state is None and not self.state['predictions']:
            return ()
        snapshot = self.capture()
        requests = ([item for item in self.actor.cognitive_requests()
                     if item[0] not in self.state['handled_requests']] if self.actor else [])
        review = self.state['last_reviewed']
        changed_predictions, source_changes = [], []
        for prediction in self.task_scoped(self.state['predictions']):
            # A review after registration records notification even if the body
            # stayed unread. It does not acknowledge the prediction comparison.
            notified = (review
                        and review['activity_id'] != prediction.get('registered_after_review'))
            previous = (self.observation_ref(prediction, review['files']) if notified
                        else prediction.get('reviewed_source', prediction['before_observation_ref']))
            observed = self.observation_ref(prediction, snapshot['files'])
            if observed != previous:
                changed_predictions.append(prediction)
                source_changes.append({'file': prediction['observation_file'],
                                       'before': {'ref': previous}, 'after': {'ref': observed}})
        dependencies = self.dependencies(snapshot['files'])
        same_files = review and review['execution_ref'] == snapshot['execution_ref'] and review['files'] == snapshot['files']
        if (not requests and not changed_predictions and review
                and self.target(snapshot) == self.target(review)):
            # Unread comparisons remain pending, but an accepted review already
            # handled this notification. Waiting alone is not new evidence.
            self.save()
            return ()
        from Nervous.triggers import execution_reasons
        reasons = execution_reasons({
            'requests': bool(requests), 'changed_predictions': bool(changed_predictions),
            'budget_feedback': self.state['stop_reason'] == 'feedback_budget_reserved' and any(dependencies),
            'completion_feedback': bool(self.actor and self.actor.completion_review_pending() and any(dependencies)),
            'significant_result': bool(state and state.status in {'waiting', 'completed', 'failed'}
                                       and (any(dependencies)
                                            or (not review or review.get('status') != state.status
                                                if 'stage1_authority' in self.state else not same_files)))})
        if not reasons:
            self.save()
            return ()
        # Several mechanical triggers may describe the same committed outcome.
        # Publishing its request must not enqueue a second review merely because
        # the next poll now notices its pending prediction comparison instead.
        identity = self.outcome_identity(snapshot)
        if identity in self.state['announced']:
            self.save()
            return ()
        event = Event('execution-change-' + identity[:24], 'execution', 'mind', 'execution.changed',
                      {'snapshot': snapshot, 'reason': reasons[0].reason,
                       **({'observation_cycle': observation_cycle, 'source_changes': source_changes}
                          if observation_cycle is not None and 'stage1_authority' in self.state else {})})
        self.state['announced'].append(identity)
        self.state['handled_requests'].extend(item[0] for item in requests)
        self.state['outbox'].append(event.document())
        self.save()
        return (event,)

    def published(self, event_id):
        self.state['outbox'] = [item for item in self.state['outbox'] if item['event_id'] != event_id]
        self.save()

    def status(self):
        state = self.run_state()
        deliveries, predictions = self.dependencies()
        return {'status': state.status if state else 'not_started',
            **({'control': self.state.get('control'), 'task_contract': self.state.get('task_contract')}
               if 'stage1_authority' in self.state else {}),
            'execution_ref': state.execution_id if state else None,
            'decision_count': state.decision_count if state else 0,
            'waiting_for': state.waiting_for if state else None,
            'stop_reason': self.state['stop_reason'],
            'feedback_pending': {'deliveries': [d['id'] for d in deliveries], 'predictions': [p['ref'] for p in predictions]},
            'deliveries': [{k: v for k, v in d.items() if k not in {'text', 'files'}} for d in self.state['deliveries']],
            'outbox': [x['event_id'] for x in self.state['outbox']]}
