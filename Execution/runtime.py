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
    def __init__(self, directory, calls, workspace=None, ipython=None):
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

    def open_actor(self):
        directory = self.directory / 'runs' / str(self.state['active_run'])
        self.control = self._ipython or DockerIPython(self.workspace)
        self.actor = ExecutionOrgan(workspace=self.workspace,
            event_log_path=directory / 'events.jsonl', checkpoint_path=directory / 'checkpoint.json',
            max_decisions=120, max_depth=1, max_context_chars=16000, max_decisions_per_advance=1,
            completion_review_required=self.feedback_required,
            model=ExecutionModel(self.call_execution, owner_task=self.state['task'],
                execution_context=self.execution_context, role_prompt=EXECUTION_ROLE,
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

    def watched(self):
        return tuple(p['observation_file'] for p in self.state['predictions'])

    def dependencies(self, files=None):
        state = self.run_state()
        run = state.execution_id if state else None
        deliveries = [d for d in self.state['deliveries'] if d['execution_ref'] == run
                      and not d['status'].startswith('expired') and not d.get('reviewed_by')]
        predictions = []
        for p in self.state['predictions']:
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
                                                        if x['sequence'] > d['owner_sequence']]
        return result

    def execution_context(self, count):
        state = self.run_state()
        owners = []
        for item in self.state['owners']:
            value = {k: v for k, v in item.items() if k != 'text'}
            if len(item['text']) <= 2000:
                value['text'] = item['text']
            else:
                value['text_ref'] = item['source_ref']
                value['text_chars'] = len(item['text'])
                value['scope'] = 'Exact owner statement retained externally; request Mind if its full text matters.'
            owners.append(value)
        return {'version': 'execution-context-1',
            'rounds': self.history.history(count, state.execution_id, self.actor.committed_tool_calls()),
            'received_guidance': self.received_guidance(count), 'owner_inputs': owners,
            'cognitive_feedback': {'last_reviewed': self.state['last_reviewed'],
                                  'completion_review_required': self.feedback_required()},
            'guidance_scope': 'Exact prior advice in receiving-decision order, not a new delivery or verified fact. '
                'NoChange does not revoke advice. Consider later guidance and owner inputs for current applicability.'}

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
        for delivery in self.state['deliveries']:
            if delivery['status'] != 'bound':
                continue
            for path, record in self.calls.records(role='execution'):
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
        return content

    @staticmethod
    def target(snapshot):
        return {key: snapshot.get(key) for key in
                ('execution_ref', 'decision', 'state_version', 'status', 'files')}

    def outcome_identity(self, snapshot):
        position = [self.target(snapshot), snapshot['request_event']]
        if snapshot['execution_ref'] is None:
            # Without an Actor checkpoint, accepted reviews distinguish later
            # changes back to old content from retries of the pending review.
            review = self.state['last_reviewed']
            position.append(review['activity_id'] if review else None)
        return fingerprint(position)

    def handle(self, event):
        if event.target != 'execution' or event.source not in {'mind', 'mind.results'}:
            raise ValueError('execution_event_authority_conflict')
        digest = fingerprint(event.document())
        previous = self.state['handled'].get(event.event_id)
        if previous:
            if previous['digest'] != digest:
                raise ValueError('execution_event_identity_conflict')
            return tuple(Event(**x) for x in previous['responses'])
        data = plain(event.data)
        if event.kind == 'execution.inspect':
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
        execution_goal(task)
        if self.state['task'] is not None and task != self.state['task']:
            raise ValueError('execution_owner_goal_conflict')
        self.state['task'] = task
        current = self.capture()
        initialization = self.state['initializing']
        recovering = initialization is not None and initialization['event_id'] == event.event_id
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
            self.state['owners'].append({**owner, 'sequence': len(self.state['owners']) + 1, 'source_ref': record['ref']})
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
            **self.target(data['snapshot'])}
        if directive is not None:
            state = self.run_state()
            if recovering or state is None or state.status in {'completed', 'failed'}:
                if not recovering:
                    self.state['initializing'] = {'event_id': event.event_id, 'prior': self.target(current)}
                    if state:
                        self.state['prior_runs'].append({'execution_ref': state.execution_id,
                            'status': state.status, 'run': self.state['active_run'], 'continuation_event': event.event_id})
                    self.state['active_run'] = 0 if self.state['active_run'] is None else self.state['active_run'] + 1
                    self.save()
                    if self.actor is not None:
                        self.actor.shutdown()
                    self.open_actor()
                if self.actor.state is None:
                    self.actor.run_goal(execution_goal(task), FileContentEquals('.lumina-complete', 'done'), defer_actions=True)
                state = self.run_state()
                if state.decision_count != 0:
                    raise ValueError('execution_initialization_already_advanced')
                for p in self.state['predictions']:
                    if p['execution_ref'] is None:
                        p['execution_ref'] = state.execution_id
            decision = self.actor.next_root_decision_id
            if decision is None:
                raise ValueError('no_eligible_execution_decision')
            for old in self.state['deliveries']:
                if old['status'] == 'bound' and old['execution_ref'] == state.execution_id:
                    old['status'] = 'expired_by_later_guidance'
            delivery = {**directive, 'activity_id': data['activity_id'], 'event_id': event.event_id,
                'execution_ref': state.execution_id, 'decision': decision, 'status': 'bound',
                'files': current['files'], 'owner_sequence': len(self.state['owners'])}
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
        if self.actor.completion_review_pending() and self.feedback_required():
            return False
        before = state.version
        try:
            self.actor.resume(decision_advisory=guidance)
        except BudgetPause as error:
            changed = self.state['stop_reason'] != str(error)
            self.state['stop_reason'] = str(error)
            self.save()
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

    def poll(self):
        if self.state['outbox']:
            return tuple(Event(**x) for x in self.state['outbox'])
        state = self.run_state()
        if state is None and not self.state['predictions']:
            return ()
        snapshot = self.capture()
        requests = ([item for item in self.actor.cognitive_requests()
                     if item[0] not in self.state['handled_requests']] if self.actor else [])
        review = self.state['last_reviewed']
        changed_predictions = []
        for prediction in self.state['predictions']:
            # A review after registration records notification even if the body
            # stayed unread. It does not acknowledge the prediction comparison.
            notified = (state is None and review
                        and review['activity_id'] != prediction.get('registered_after_review'))
            previous = (self.observation_ref(prediction, review['files']) if notified
                        else prediction.get('reviewed_source', prediction['before_observation_ref']))
            if self.observation_ref(prediction, snapshot['files']) != previous:
                changed_predictions.append(prediction)
        dependencies = self.dependencies(snapshot['files'])
        same_files = review and review['execution_ref'] == snapshot['execution_ref'] and review['files'] == snapshot['files']
        reason = None
        if requests:
            reason = 'Execution explicitly requests high-level judgment; its question is an attributed actor judgment.'
        elif changed_predictions:
            reason = 'A declared prediction observation changed. Check its source, action and conditions before comparing.'
        elif self.state['stop_reason'] == 'feedback_budget_reserved' and any(dependencies):
            reason = 'Execution yielded its remaining allocation for pending feedback; budget exhaustion proves no business conclusion.'
        elif self.actor and self.actor.completion_review_pending() and any(dependencies):
            reason = 'Execution proposed completion; outstanding guidance or computation results require business-result feedback.'
        elif state and state.status in {'waiting', 'completed', 'failed'} and (any(dependencies) or not same_files):
            reason = 'Execution reached a significant result or outside wait; assess current business evidence and remaining conditions.'
        if reason is None:
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
                      {'snapshot': snapshot, 'reason': reason})
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
            'execution_ref': state.execution_id if state else None,
            'decision_count': state.decision_count if state else 0,
            'waiting_for': state.waiting_for if state else None,
            'stop_reason': self.state['stop_reason'],
            'feedback_pending': {'deliveries': [d['id'] for d in deliveries], 'predictions': [p['ref'] for p in predictions]},
            'deliveries': [{k: v for k, v in d.items() if k not in {'text', 'files'}} for d in self.state['deliveries']],
            'outbox': [x['event_id'] for x in self.state['outbox']]}
