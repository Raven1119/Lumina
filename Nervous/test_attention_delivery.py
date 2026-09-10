"""Foreground attention delivery through persistent owners; scripted inference only."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from Execution.model import EXECUTION_PROTOCOL
from Execution.runtime import Execution
from Execution.test_runtime import Python, native
from Mind.organ import MindOrgan
from Mind.test_cognition_core import Script, response, step
from Nervous.organ import NervousOrgan
from Nervous.storage import fingerprint


NOW = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
SCOPE = 'Investigate supplied records; choose bounded follow-up work within the authorized workspace.'


@contextmanager
def chain(root, script, *, now=NOW, execution_responses=()):
    workspace = root / 'workspace'
    workspace.mkdir(exist_ok=True)
    actions = iter(execution_responses)

    def transport(role, wire):
        assert role in {'mind', 'execution'}
        return script(wire) if role == 'mind' else next(actions)

    with NervousOrgan(root / 'private' / 'nervous', transport=transport,
            limits={'calls': 40, 'output_tokens': 1000000, 'request_bytes': 10000000},
            clock=lambda: now) as nervous:
        initial = nervous.initialize(workspace=workspace, pursuit=SCOPE)
        authority = {'mind_id': initial['mind_id'], 'text': initial['pursuit'],
                     'ref': 'owner-scope:' + fingerprint([initial['mind_id'], initial['pursuit']])[:24]}
        execution = Execution(root / 'private' / 'execution', nervous.calls, workspace,
                              Python(workspace), stage1_authority=authority)
        mind = MindOrgan(root / 'private' / 'mind', nervous.calls,
                         execution_protocol=EXECUTION_PROTOCOL, stage1_authority=authority)
        try:
            yield nervous, mind, execution
        finally:
            mind.close()
            execution.close()


def register_source(wire, *, with_task=False):
    value = json.loads(wire['messages'][0]['content'])
    authority = value['cognition']['pursuit']['authorization']['ref']
    intention = {'id': 'new:research', 'base_revision': 0, 'aim': 'Resolve the supplied evidence gap.',
                 'why': 'The authorized material may acquire a relevant observation.',
                 'commitment': 'committed', 'origin_refs': [authority],
                 'understanding_refs': [], 'influence_refs': []}
    watch = {'id': 'new:source', 'base_revision': 0, 'spec_id': 'source.changed', 'spec_version': 1,
             'params': {'file': 'observations.json'}, 'intention_id': 'new:research',
             'status': 'active', 'reason': 'A new observation may resolve the current gap.',
             'origin_refs': [authority]}
    task = {'id': 'new:task-a', 'base_revision': 0, 'intention_id': 'new:research',
            'authority_ref': authority, 'goal': 'Assess the observation when available.',
            'acceptance': 'Retain the uncertainty until a relevant observation is available.'} if with_task else None
    direction = {'type': 'directive', 'text': 'Await the relevant observation without inventing a conclusion.'} if with_task else None
    return response(step(next=direction, effects={'intentions': [intention], 'task': task, 'watches': [watch]}))


def attention_events(root):
    # Public integrity-checking read API; never edit owner state to manufacture history.
    state = NervousOrgan.read_state(root / 'private' / 'nervous' / 'events.json')
    return [event for event in state['events'] if event['kind'] == 'attention.signal']


def test_future_owner_review_is_quiet_and_overdue_restart_uses_one_call(tmp_path):
    script = Script(response(step()), response(step()))
    with chain(tmp_path, script) as (nervous, mind, execution):
        nervous.run(mind, execution)
        identity = mind.cognition.pursuit_state()['mind_id']
        scheduled = nervous.schedule_review('2026-09-10T11:00:00Z', 'One bounded reassessment of the unresolved scope.',
                                            submission_id='review-opportunity')
        nervous.run(mind, execution)
        assert len(script.wires) == 1
        assert attention_events(tmp_path) == []
        assert mind.cognition.pursuit_state()['task'] is None
    with chain(tmp_path, script, now=NOW + timedelta(hours=2)) as (nervous, mind, execution):
        nervous.run(mind, execution)
        event, = attention_events(tmp_path)
        assert event['data']['kind'] == 'review.due'
        assert event['data']['due_at'] == '2026-09-10T11:00:00Z'
        assert event['data']['observed_at'] == '2026-09-10T12:00:00+00:00'
        assert len(script.wires) == 2
        assert mind.cognition.pursuit_state()['mind_id'] == identity
        assert mind.cognition.inspect().revision == 2
        assert not nervous.pending('mind')
        assert nervous.schedule_review('2026-09-10T11:00:00Z', 'One bounded reassessment of the unresolved scope.',
                                        submission_id='review-opportunity') == scheduled
        nervous.run(mind, execution)
        assert len(script.wires) == 2


def test_busy_mind_keeps_source_occurrence_pending_until_original_query_resumes(tmp_path, monkeypatch):
    script = Script(register_source, response(step()), response(step()))
    with chain(tmp_path, script) as (nervous, mind, execution):
        nervous.run(mind, execution)
        original = execution.handle

        def delayed(event):
            return None if event.kind == 'execution.inspect' else original(event)

        monkeypatch.setattr(execution, 'handle', delayed)
        nervous.submit('Consider whether the present evidence is sufficient.')
        nervous.run(mind, execution)
        active = mind.status()['active']
        assert active is not None
        (tmp_path / 'workspace' / 'observations.json').write_text('A', encoding='utf-8')
        nervous.run(mind, execution)
        event, = attention_events(tmp_path)
        assert event['event_id'] in [item.event_id for item in nervous.pending('mind')]
        assert len(script.wires) == 1
        assert mind.status()['active'] == active
    with chain(tmp_path, script) as (nervous, mind, execution):
        nervous.run(mind, execution)
        assert len(script.wires) == 3
        assert [item['event_id'] for item in attention_events(tmp_path)] == [event['event_id']]
        assert not nervous.pending('mind')
        assert mind.cognition.inspect().revision == 3


def test_source_a_b_a_at_same_waiting_run_reaches_three_nochange_reviews(tmp_path):
    script = Script(lambda wire: register_source(wire, with_task=True),
                    *(response(step()) for _ in range(4)))
    with chain(tmp_path, script, execution_responses=[native('wait', {'event_type': 'INPUT'})]) as (nervous, mind, execution):
        nervous.run(mind, execution)
        assert execution.status()['status'] == 'waiting'
        checkpoint = execution.view('execution.state')
        assert len(script.wires) == 2  # initial judgment and actual Execution wait feedback
        for value in ['A', 'B']:
            (tmp_path / 'workspace' / 'observations.json').write_text(value, encoding='utf-8')
            nervous.run(mind, execution)
        assert len(script.wires) == 4
        identity = mind.cognition.pursuit_state()['mind_id']
    with chain(tmp_path, script) as (nervous, mind, execution):
        (tmp_path / 'workspace' / 'observations.json').write_text('A', encoding='utf-8')
        nervous.run(mind, execution)
        events = attention_events(tmp_path)
        assert len(events) == len({event['event_id'] for event in events}) == 3
        assert events[0]['data']['after']['ref'] == events[-1]['data']['after']['ref']
        assert events[1]['data']['after']['ref'] != events[-1]['data']['after']['ref']
        assert len(script.wires) == 5
        assert nervous.calls.summary()['calls'] == 6
        assert mind.cognition.pursuit_state()['mind_id'] == identity
        current = execution.view('execution.state')
        assert current['content']['execution_ref'] == checkpoint['content']['execution_ref']
        assert current['content']['decision_count'] == checkpoint['content']['decision_count']
        assert current['content']['status'] == 'waiting'
        assert not nervous.pending('mind')
        nervous.run(mind, execution)
        assert len(script.wires) == 5


def test_crash_after_watch_save_before_ack_keeps_baseline_and_exactly_one_signal(tmp_path, monkeypatch):
    class Crash(BaseException):
        pass

    # The changed observation creates both a Watch occurrence and a distinct
    # applicability review of the original, now-stale NoChange decision.
    script = Script(register_source, response(step()), response(step()))
    original = NervousOrgan.complete
    interrupted = False

    def cut(self, event_id, target, *, emitted=()):
        nonlocal interrupted
        if not interrupted and target == 'nervous' and any(
                event.event_id == event_id and event.kind == 'mind.effects' for event in self.pending('nervous')):
            interrupted = True
            raise Crash()
        return original(self, event_id, target, emitted=emitted)

    monkeypatch.setattr(NervousOrgan, 'complete', cut)
    with chain(tmp_path, script) as (nervous, mind, execution):
        with pytest.raises(Crash):
            nervous.run(mind, execution)
        effect, = nervous.pending('nervous')
        assert effect.kind == 'mind.effects'
        assert len(script.wires) == 1
    (tmp_path / 'workspace' / 'observations.json').write_text('A', encoding='utf-8')
    with chain(tmp_path, script) as (nervous, mind, execution):
        nervous.run(mind, execution)
        event, = attention_events(tmp_path)
        assert event['data']['before']['missing'] is True
        assert event['data']['after']['missing'] is False
        state = NervousOrgan.read_state(tmp_path / 'private' / 'nervous' / 'events.json')
        receipts = [item for item in state['events'] if item['kind'] == 'execution.receipt']
        superseded, = [item for item in receipts if item['data']['status'] == 'superseded']
        reassessment, = [item for item in state['events'] if item['kind'] == 'execution.changed']
        assert reassessment['event_id'].startswith('execution-reassess-')
        assert reassessment['data']['origin_activity_id'] == superseded['data']['activity_id']
        assert reassessment['causation_id'] == superseded['causation_id']
        assert 'snapshot changed before the decision could be accepted' in reassessment['data']['reason']
        assert [item['data']['status'] for item in receipts].count('accepted') == 2
        assert len(script.wires) == nervous.calls.summary()['calls'] == 3
        assert mind.cognition.inspect().revision == 3
        assert mind.status()['unresolved'] == []
        assert not nervous.pending('nervous')
        nervous.run(mind, execution)
        assert len(script.wires) == 3


@pytest.mark.parametrize('event_type, action', [('STOP', 'stop'), ('REVOKE', 'revoke')])
def test_explicit_control_closes_execution_admission_before_mind_review(tmp_path, event_type, action):
    owner = {}

    def reviewed(wire):
        assert owner['execution'].status()['control']['action'] == action
        return response(step())

    script = Script(lambda wire: register_source(wire, with_task=True), response(step()), reviewed)
    with chain(tmp_path, script, execution_responses=[native('wait', {'event_type': 'INPUT'})]) as (nervous, mind, execution):
        owner['execution'] = execution
        nervous.run(mind, execution)
        before = nervous.calls.summary()['calls']
        control = nervous.submit('Stop the currently authorized action scope.', event_type=event_type)
        assert control.kind == 'user.control' and control.target == 'nervous'
        nervous.run(mind, execution)
        assert len(script.wires) == 3
        assert execution.status()['control'] == {'action': action, 'control_ref': control.event_id}
        assert nervous.calls.summary()['calls'] == before + 1
        assert not execution.advance()
