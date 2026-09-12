"""Current organ boundaries with a real Execution facade and synthetic provider replies."""
import json
from pathlib import Path

import pytest

from Execution.evidence import EvidenceStore
from Execution.ipython_control import IPythonResult
from Execution.model import EXECUTION_PROTOCOL
from Execution.runtime import Execution, advisory
from Nervous.organ import Event
from Nervous.provider import ProviderCalls
from Nervous.storage import canonical, fingerprint, plain


class Python:
    def __init__(self, workspace):
        self.workspace, self.actions = workspace, []
    def execute(self, code):
        self.actions.append(code)
        question = None
        def request_mind(question_text, evidence_files=(), model_ref=''):
            nonlocal question
            question = canonical({'question': question_text, 'evidence_files': list(evidence_files), 'model_ref': model_ref})
        exec(code, {'workspace': self.workspace, 'request_mind': request_mind, 'Path': Path})
        return IPythonResult(True, output='Synthetic observed action result.', cognitive_request=question)
    def close(self):
        pass


def native(name, data, identity='t'):
    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': identity, 'name': name, 'input': data}],
            'usage': {'input_tokens': 7, 'output_tokens': 4}}


def runtime(tmp_path, answers, max_calls=40):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    calls = ProviderCalls(tmp_path / 'private' / 'calls',
        {'calls': max_calls, 'output_tokens': 1000000, 'request_bytes': 10000000},
        lambda role, wire: next(answers))
    control = Python(workspace)
    return Execution(tmp_path / 'private' / 'execution', calls, workspace, control), calls, control


def decision(owner, identity, text, *, snapshot=None, owner_input=None):
    return Event(identity, 'mind.results', 'execution', 'mind.decision', {
        'activity_id': 'activity-' + identity,
        'task': {'business_goal': 'Create a checked report from the authorized input.', 'execution_protocol': EXECUTION_PROTOCOL},
        'snapshot': snapshot or owner.capture(),
        'directive': {'id': 'directive-' + identity, 'text': text} if text else None,
        'reviewed': (snapshot or owner.capture())['reviewed'], 'owner_input': owner_input})


def test_handler_creates_deferred_run_and_restart_replays_receipt_without_action(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([native('wait', {'event_type': 'INPUT'})]))
    event = decision(owner, 'start', 'Await the missing observation without inventing its value.')
    try:
        first = owner.handle(event)
        assert first[0].data['status'] == 'bound'
        assert control.actions == [] and calls.summary()['calls'] == 0
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.handle(event) == first
        assert owner.advance()
        assert owner.status()['status'] == 'waiting'
        assert calls.summary()['calls'] == 1
        assert owner.state['deliveries'][0]['status'] == 'received'
        assert not owner.advance()
    finally:
        owner.close()


def test_guidance_survives_many_actions_restart_and_nochange_without_redelivery(tmp_path):
    answers = iter([native('ipython', {'code': 'pass'}, str(i)) for i in range(10)])
    owner, calls, control = runtime(tmp_path, answers)
    text = 'Report the observed scope only; a missing observation does not establish permanent absence.'
    try:
        owner.handle(decision(owner, 'start', text))
        for _ in range(7):
            assert owner.advance()
            assert owner.poll() == ()  # Ordinary actions never become cognitive approvals.
        original = dict(owner.state['deliveries'][0])
        owner.handle(decision(owner, 'review', None))
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.advance()
        latest = calls.records(role='execution')[-1][1]['wire']
        owner_context = json.loads(latest['messages'][0]['content'][0]['text'])
        assert owner_context['received_guidance'][0]['text'] == text
        assert json.loads(latest['messages'][-1]['content'])['state'] == owner_context['state']
        assert all(m['content'] != advisory(text) for m in latest['messages'])
        assert len(owner.state['deliveries']) == 1
        assert owner.state['deliveries'][0]['call_ref'] == original['call_ref']
        assert calls.summary()['calls'] == 8
        owner.handle(decision(owner, 'new-direction', 'Prioritize the now observed constraint.',
            owner_input={'event_id': 'owner2', 'event_type': 'OWNER_EVIDENCE', 'text': 'A new observed condition.'}))
        assert owner.advance()
        latest = calls.records(role='execution')[-1][1]['wire']
        assert sum(m['content'] == advisory('Prioritize the now observed constraint.') for m in latest['messages']) == 1
        assert owner.state['deliveries'][0]['call_ref'] == original['call_ref']
    finally:
        owner.close()


@pytest.mark.parametrize('new_directive', [False, True])
@pytest.mark.parametrize('retirement_crash', [None, 'before', 'after'])
def test_saved_response_does_not_acquire_new_guidance_before_owner_commit(
        tmp_path, monkeypatch, new_directive, retirement_crash):
    from Execution.execution import EventLog

    old_text, new_text = 'Use the initial condition.', 'Use the newly observed condition.'
    answers = iter([
        native('ipython', {'code': "(workspace / 'old.txt').write_text('old')"}, 'old-call'),
        native('ipython', {'code': "(workspace / 'new.txt').write_text('new')"}, 'new-call'),
    ])
    owner, calls, control = runtime(tmp_path, answers)
    original_append = EventLog.append

    def interrupt_before_decision(log, event_type, *args, **kwargs):
        if event_type == 'MODEL_DECISION':
            raise SystemExit('after durable provider response, before owner decision')
        return original_append(log, event_type, *args, **kwargs)

    try:
        owner.handle(decision(owner, 'start', old_text))
        monkeypatch.setattr(EventLog, 'append', interrupt_before_decision)
        with pytest.raises(SystemExit):
            owner.advance()
        assert calls.summary()['calls'] == 1 and control.actions == []
        monkeypatch.setattr(EventLog, 'append', original_append)
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        owner.handle(decision(owner, 'new-direction', new_text if new_directive else None,
            owner_input={'event_id': 'new-owner', 'event_type': 'OWNER_EVIDENCE',
                         'text': 'A new condition is now observed.'}))
        if retirement_crash:
            def interrupt_retirement(log, event_type, *args, **kwargs):
                if event_type == 'DECISION_RETIRED' and retirement_crash == 'before':
                    raise SystemExit('before retirement settlement')
                result = original_append(log, event_type, *args, **kwargs)
                if event_type == 'DECISION_RETIRED':
                    raise SystemExit('after retirement settlement')
                return result
            monkeypatch.setattr(EventLog, 'append', interrupt_retirement)
            with pytest.raises(SystemExit):
                owner.advance()
            monkeypatch.setattr(EventLog, 'append', original_append)
            owner.close()
            owner = Execution(owner.directory, calls, ipython=control)
            owner.actor.interrupt()
        owner.advance()
        assert not (owner.workspace / 'old.txt').exists()
        # Recovery may yield at a settled owner boundary before a fresh decision.
        if not (owner.workspace / 'new.txt').exists():
            owner.advance()
        assert (owner.workspace / 'new.txt').read_text() == 'new'
        assert calls.summary()['calls'] == 2
        log_path = owner.directory / 'runs' / str(owner.state['active_run']) / 'events.jsonl'
        events = EventLog.load(log_path).events
        frames = [event.payload['frame'] for event in events if event.event_type == 'MODEL_DECISION']
        assert sum(event.event_type == 'DECISION_RETIRED' for event in events) == 1
        assert old_text in frames[0].actual_request.context
        assert new_text not in frames[0].actual_request.context
        assert (new_text in frames[-1].actual_request.context) == new_directive
        assert 'A new condition is now observed.' in canonical(plain(frames[-1].provider_wire_request))
        assert frames[0].raw_provider_response != frames[-1].raw_provider_response
        assert sum(d['status'] == 'received' for d in owner.state['deliveries']) == 1
    finally:
        owner.close()


def test_actor_question_and_observed_result_have_distinct_sources_and_durable_outbox(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([native('ipython', {
        'code': "request_mind('Does this observation change direction?', evidence_files=('input.txt',))"})]))
    (owner.workspace / 'input.txt').write_text('The source is currently unavailable.', encoding='utf-8')
    try:
        owner.handle(decision(owner, 'start', 'Retain the unknown scope.'))
        assert owner.advance()
        events = owner.poll()
        snapshot = events[0].data['snapshot']
        question = owner.evidence.read(snapshot['request_ref'])
        assert question['source_kind'] == 'execution_judgment'
        observed = json.loads(snapshot['observation']['recent_outcome'])
        assert observed['last_tool_result']['output'] == 'Synthetic observed action result.'
        assert observed['historical_execution_request']['ref'] == question['ref']
        assert snapshot['reviewed']['deliveries'] == ('directive-start',)
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == events
        owner.published(events[0].event_id)
        assert owner.poll() == ()
        owner.handle(decision(owner, 'review', None))
        assert not owner.feedback_required()
    finally:
        owner.close()


def test_stale_guidance_is_not_delivered_and_original_snapshot_remains_visible(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([]))
    try:
        (owner.workspace / 'input.txt').write_text('before', encoding='utf-8')
        snapshot = owner.capture()
        (owner.workspace / 'input.txt').write_text('after', encoding='utf-8')
        event = decision(owner, 'stale', 'Use the earlier condition.', snapshot=snapshot)
        result = owner.handle(event)
        assert result[0].data['status'] == 'superseded'
        reassessment = owner.poll()[0]
        assert reassessment.causation_id == event.event_id
        assert reassessment.data['origin_activity_id'] == 'activity-stale'
        assert reassessment.data['snapshot']['files'][0]['ref'] != snapshot['files'][0]['ref']
        assert owner.actor is None and owner.state['deliveries'] == []
        assert owner.evidence.read(snapshot['files'][0]['ref'])['text'] == 'before'
        assert calls.summary()['calls'] == 0
    finally:
        owner.close()


def test_result_feedback_allows_completion_and_later_direction_creates_linked_run(tmp_path):
    answers = iter([native('ipython', {'code': "(workspace / 'report.txt').write_text('observed')\n(workspace / '.lumina-complete').write_text('done')"}),
        native('claim_complete', {}), native('wait', {'event_type': 'INPUT'})])
    owner, calls, control = runtime(tmp_path, answers)
    try:
        owner.handle(decision(owner, 'start', 'Deliver the observed report.'))
        assert owner.advance() and owner.advance()
        assert owner.actor.completion_review_pending()
        assert not owner.advance()
        event = owner.poll()[0]
        owner.published(event.event_id)
        owner.handle(decision(owner, 'review', None))
        assert owner.advance() and owner.status()['status'] == 'completed'
        assert owner.poll() == ()
        first_run = owner.status()['execution_ref']
        owner.handle(decision(owner, 'later', 'Apply the newly supplied condition.',
            owner_input={'event_id': 'new', 'event_type': 'OWNER_EVIDENCE', 'text': 'A newly measured condition.'}))
        assert owner.status()['execution_ref'] != first_run
        assert owner.state['prior_runs'][0]['execution_ref'] == first_run
        assert calls.summary()['calls'] == 2  # Neither settling review nor binding successor calls Execution.
        assert owner.advance()
    finally:
        owner.close()


def test_completion_review_direction_reaches_execution_instead_of_blocking_its_own_feedback(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([
        native('claim_complete', {}), native('wait', {'event_type': 'UPDATED_INPUT'}),
    ]))
    try:
        (owner.workspace / '.lumina-complete').write_text('done', encoding='utf-8')
        owner.handle(decision(owner, 'start', 'Deliver the current result.'))
        assert owner.advance() and owner.actor.completion_review_pending()
        event = owner.poll()[0]
        owner.published(event.event_id)
        text = 'The delivery lacks the new observation; wait for its actual arrival.'
        owner.handle(decision(owner, 'review', text))
        assert owner.advance()
        assert owner.run_state().waiting_for == 'UPDATED_INPUT'
        assert calls.summary()['calls'] == 2 and control.actions == []
        wire = calls.records(role='execution')[-1][1]['wire']
        assert any(message['content'] == advisory(text) for message in wire['messages'])
        feedback = json.loads(wire['messages'][0]['content'][0]['text'])['cognitive_feedback']
        assert feedback['completion']['run_status'] == 'running'
        assert feedback['completion']['deferred_claim_at_current_boundary'] is True
        assert owner.run_state().status != 'completed'
    finally:
        owner.close()


def test_waiting_nochange_wakes_only_for_the_actual_matching_owner_event(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([native('wait', {'event_type': 'OBSERVATION'})]))
    try:
        owner.handle(decision(owner, 'start', 'Wait for the missing observation.'))
        assert owner.advance()
        owner.handle(decision(owner, 'unrelated', None,
            owner_input={'event_id': 'a', 'event_type': 'COMMENT', 'text': 'No new observation.'}))
        assert not owner.advance()
        owner.handle(decision(owner, 'arrival', None,
            owner_input={'event_id': 'b', 'event_type': 'OBSERVATION', 'text': 'A direct measurement arrived.'}))
        assert owner.status()['status'] == 'running'
        assert calls.summary()['calls'] == 1
    finally:
        owner.close()


def test_prediction_observation_body_and_exact_source_survive_restart(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([native('ipython', {'code': 'pass'})]))
    try:
        owner.handle(decision(owner, 'start', 'Use the declared conditional prediction.'))
        registration = Event('watch', 'mind.results', 'execution', 'prediction.watch', {
            'activity_id': 'a', 'ref': 'model:' + '1' * 32, 'observation_file': 'observation.json',
            'before_observation_ref': None, 'check_spec': {'scope': 'Declared final value.'}})
        owner.handle(registration)
        text = canonical({'actual': 3, 'provenance': 'direct observation ' * 120})
        (owner.workspace / 'observation.json').write_text(text, encoding='utf-8')
        event = owner.poll()[0]
        snapshot = event.data['snapshot']
        ref = next(x['ref'] for x in snapshot['files'] if x['file'] == 'observation.json')
        assert next(x for x in snapshot['sources'] if x['ref'] == ref)['text'] == text
        assert snapshot['reviewed']['predictions']['model:' + '1' * 32] == ref
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == (event,)
    finally:
        owner.close()


@pytest.mark.parametrize('text', ['', '   \n', 'ordinary exact content'])
def test_empty_and_normal_source_reads_preserve_exact_content(tmp_path, text):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'source.txt').write_bytes(text.encode('utf-8'))
    evidence = EvidenceStore(tmp_path / 'sources', workspace)
    item = evidence.snapshot()['files'][0]
    result = evidence.read_result([item['ref']])
    assert json.loads(result['observation']['text'])['sources'][0]['text'] == text
    assert 'text' not in result['records'][0]


def test_read_rejection_is_durable_but_source_integrity_failure_stops(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([]))
    def request(identity, ref):
        return Event(identity, 'mind.results', 'execution', 'evidence.read',
                     {'activity_id': 'review', 'request_ref': 'request-' + identity, 'refs': [ref]})
    try:
        bad = request('missing', 'unknown-source')
        failure = owner.handle(bad)
        assert failure[0].data['error'] == 'model_failed'
        assert owner.handle(bad) == failure
        assert len(list((owner.directory / 'failures').glob('*.json'))) == 1
        record = owner.evidence.put('Original observation.', 'observation')
        source_path = owner.evidence.directory / (record['ref'].replace(':', '-') + '.json')
        source_path.write_text(canonical({**record, 'text': 'Tampered source.'}), encoding='utf-8')
        with pytest.raises(ValueError, match='source_identity_conflict'):
            owner.handle(request('corrupt', record['ref']))
        assert calls.summary()['calls'] == 0 and control.actions == []
    finally:
        owner.close()


@pytest.mark.parametrize('tool, arguments', [('wait', {'event_type': 'OLD'}), ('claim_complete', {})])
def test_saved_control_rechecks_new_owner_input_before_dispatch(tmp_path, monkeypatch, tool, arguments):
    from Execution.execution import EventLog
    owner, calls, control = runtime(tmp_path, iter([
        native(tool, arguments, 'old-control'), native('wait', {'event_type': 'NEW'}, 'new-control')]))
    original = EventLog._persist
    def crash(log, event):
        original(log, event)
        if event.event_type == 'MODEL_DECISION':
            raise SystemExit('control decision saved, suffix unstarted')
    try:
        (owner.workspace / '.lumina-complete').write_text('done')
        owner.handle(decision(owner, 'start', 'Use the current condition.'))
        with monkeypatch.context() as patch:
            patch.setattr(EventLog, '_persist', crash)
            with pytest.raises(SystemExit):
                owner.advance()
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        owner.handle(decision(owner, 'new-condition', None,
            owner_input={'event_id': 'changed', 'event_type': 'OWNER_EVIDENCE',
                         'text': 'The current condition has changed.'}))
        owner.advance()
        assert owner.status()['status'] == 'running'
        owner.advance()
        assert owner.run_state().waiting_for == 'NEW'
        assert calls.summary()['calls'] == 2 and not control.actions
        assert owner.actor.retired_decisions() == ('decision-000001',)
    finally:
        owner.close()


def test_large_observation_is_unread_not_absent_and_source_capacity_is_truthful(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    text = canonical({'observed': 'x' * 25000})
    (workspace / 'observation.json').write_text(text, encoding='utf-8')
    evidence = EvidenceStore(tmp_path / 'sources', workspace)
    snapshot = evidence.snapshot(['observation.json'])
    ref = snapshot['files'][0]['ref']
    assert snapshot['unread_observation_refs'] == [ref]
    result = evidence.read_result([ref], analysis=True)
    assert json.loads(result['observation']['text'])['read_result'] == 'capacity-v1'
    assert result['records'] == [] and evidence.read(ref)['text'] == text

def test_feedback_budget_yield_publishes_once_and_does_not_busy_wait(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([native('ipython', {'code': 'pass'})]), max_calls=3)
    try:
        owner.handle(decision(owner, 'start', 'Return the result for judgment.'))
        assert owner.advance()
        assert owner.advance()  # Only the persisted control handoff is new progress.
        assert calls.summary()['calls'] == 1
        event = owner.poll()[0]
        assert 'allocation' in event.data['reason']
        owner.published(event.event_id)
        assert not owner.advance()
        assert owner.poll() == ()
        assert calls.summary()['calls'] == 1
        assert owner.status()['stop_reason'] == 'feedback_budget_reserved'
    finally:
        owner.close()


def test_one_result_with_request_and_prediction_change_publishes_one_review(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([native('ipython', {'code':
        "(workspace / 'observation.json').write_text('{\"actual\":3}'); request_mind('Review this measured result.')"})]))
    try:
        owner.handle(decision(owner, 'start', 'Produce the observed result.'))
        owner.handle(Event('watch', 'mind.results', 'execution', 'prediction.watch', {
            'activity_id': 'a', 'ref': 'model:one', 'observation_file': 'observation.json',
            'before_observation_ref': None, 'check_spec': {'scope': 'final quantity'}}))
        assert owner.advance()
        first, = owner.poll()
        assert first.data['snapshot']['request_event']
        assert first.data['snapshot']['reviewed']['predictions']['model:one']
        owner.published(first.event_id)
        assert owner.poll() == ()
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == ()
    finally:
        owner.close()


def test_ambiguous_kernel_result_is_durable_and_cannot_dispatch_again(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([native('ipython', {'code': 'pass'})]))
    control.execute = lambda _: IPythonResult(False, error_code='isolated_kernel_failed')
    try:
        owner.handle(decision(owner, 'start', 'Perform the authorized local action.'))
        assert owner.advance()
        assert not owner.advance()
        assert owner.status()['stop_reason'] == 'execution_action_outcome_requires_owner_check'
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert not owner.advance()
        assert calls.summary()['calls'] == 1
        assert owner.state['unknown_action']['event_ref']
    finally:
        owner.close()


def test_invalid_native_sibling_batch_executes_no_partial_action(tmp_path):
    response = {'stop_reason': 'tool_use', 'content': [
        {'type': 'tool_use', 'id': 'a', 'name': 'ipython', 'input': {'code': 'pass'}},
        {'type': 'tool_use', 'id': 'b', 'name': 'ipython', 'input': {'code': 42}}]}
    owner, calls, control = runtime(tmp_path, iter([response]))
    try:
        owner.handle(decision(owner, 'start', 'Perform the authorized local action.'))
        assert owner.advance()
        assert control.actions == []
        assert owner.status()['status'] == 'failed'
    finally:
        owner.close()


def test_expired_bound_guidance_withdraws_a_persisted_redirect_without_action(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([native('wait', {'event_type': 'INPUT'})]), max_calls=2)
    try:
        owner.handle(decision(owner, 'start', 'Keep the earlier observed condition.'))
        assert owner.advance()  # Budget yields before a model request or action.
        assert calls.summary()['calls'] == 0
        (owner.workspace / 'new.txt').write_text('Changed after the reviewed checkpoint.', encoding='utf-8')
        assert owner.pending_advisory() is None
        assert owner.state['deliveries'][0]['status'] == 'expired_before_request'
        assert calls.summary()['calls'] == 0 and control.actions == []
        assert owner.advance()
        assert calls.summary()['calls'] == 0 and control.actions == []
        event = owner.poll()[0]
        assert event.data['origin_activity_id'] == 'activity-start'
        owner.published(event.event_id)
        owner.handle(decision(owner, 'review-current-observation', None))
        assert owner.advance()
        wire = calls.records(role='execution')[0][1]['wire']
        assert all(m['content'] != advisory('Keep the earlier observed condition.') for m in wire['messages'])
    finally:
        owner.close()
