"""Stage1 owner integration. Scripted responses verify mechanics, not judgment quality."""
from collections import Counter
from contextlib import ExitStack
import json

import pytest

from Execution.model import EXECUTION_PROTOCOL
from Execution.runtime import Execution
from Execution.test_runtime import Python, native
from Mind.organ import MindOrgan
from Mind.test_core_loop import source_records
from Nervous.organ import NervousOrgan
from Nervous.storage import canonical, fingerprint


AUTHORITY = {'mind_id': 'mind-persistent-research', 'ref': 'scope:research',
             'text': 'Research the supplied sources in this workspace. Choose bounded follow-up work from evidence; preserve uncertainty.'}


def cognition(next=None, *, intentions=(), task=None, watches=(), effects=True):
    value = {'type': 'cognitive_step', 'updates': [], 'next': next or {'type': 'no_change'}}
    if effects:
        value['effects'] = {'intentions': list(intentions), 'task': task, 'watches': list(watches)}
    return native('cognitive_step', value)


def pursuit(identity='new:research', revision=0, commitment='committed'):
    return {'id': identity, 'base_revision': revision,
            'aim': 'Keep source assessments independently checkable.', 'why': 'The owner authorized sourced follow-up research.',
            'commitment': commitment, 'origin_refs': [AUTHORITY['ref']],
            'understanding_refs': [], 'influence_refs': []}


def task(identity='new:a', intention_id='new:research', goal='Inventory source coverage.',
         acceptance='Write coverage.txt identifying measured and unobserved subsets.'):
    return {'id': identity, 'base_revision': 0, 'intention_id': intention_id,
            'authority_ref': AUTHORITY['ref'], 'goal': goal, 'acceptance': acceptance}


def watch(identity='new:observations', intention_id='new:research', revision=0, status='active'):
    return {'id': identity, 'base_revision': revision, 'spec_id': 'source.changed', 'spec_version': 1,
            'params': {'file': 'observation.txt'}, 'intention_id': intention_id, 'status': status,
            'reason': 'Changed observations can affect the scoped conclusion.', 'origin_refs': [AUTHORITY['ref']]}


def organs(path, workspace, transport, *, first=False, clock=None, context_mode=None):
    """Same supported composition as test_core_loop, opting into pursuit authority."""
    stack = ExitStack()
    try:
        nervous = stack.enter_context(NervousOrgan(path / 'nervous', transport=transport,
            limits={'calls': 32, 'output_tokens': 500000, 'request_bytes': 4000000},
            **({'clock': clock} if clock else {})))
        mind = MindOrgan(path / 'mind', nervous.calls, execution_protocol=EXECUTION_PROTOCOL,
                         stage1_authority=AUTHORITY if first else None, context_mode=context_mode)
        stack.callback(mind.close)
        kernel = Python(workspace)
        execution = Execution(path / 'execution', nervous.calls,
            workspace=workspace if first else None, ipython=kernel,
            stage1_authority=AUTHORITY if first else None)
        stack.callback(execution.close)
        return stack, nervous, mind, execution, kernel
    except BaseException:
        stack.close()
        raise


@pytest.mark.parametrize('context_mode', ['baseline', 'mask', 'summary'])
def test_same_mind_completes_a_then_chooses_distinct_b_from_read_result(tmp_path, context_mode):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'input.txt').write_text('Source A was measured; source B has no observations.', encoding='utf-8')
    counts, wires = Counter(), []
    tasks, actor_calls = {}, Counter()
    body = None
    def transport(role, wire):
        counts[role] += 1
        wires.append((role, wire))
        if role == 'execution':
            contract = body.state['task_contract']
            tasks[contract['id']] = dict(contract)
            actor_calls[contract['id']] += 1
            assert contract['goal'] in canonical(wire)
            if actor_calls[contract['id']] == 1:
                name, content = (('coverage.txt', 'Measured A; B remains unknown.') if len(tasks) == 1
                                 else ('interpretation.txt', 'Conclusion covers A only. B is still unknown.'))
                token = 'done:' + fingerprint(contract)[:24]
                return native('ipython', {'code': f"(workspace / {name!r}).write_text({content!r}); "
                    f"(workspace / '.lumina-complete').write_text({token!r})"})
            assert actor_calls[contract['id']] <= 3  # Claim after the required result review settles completion.
            return native('claim_complete', {})
        assert role == 'mind'
        payload = json.loads(wire['messages'][0]['content'])
        state = payload['cognition']
        proposed = state['pursuit']['task']
        if proposed is None:
            return cognition({'type': 'directive', 'text': 'Inventory the observed coverage and identify what is unknown.'},
                             intentions=[pursuit()], task=task())
        if proposed['goal'] == 'Inventory source coverage.' and state['task_status'] == 'completed':
            records = source_records(wire)
            if not any(record['text'] == 'Measured A; B remains unknown.' for record in records):
                source = next(file for file in state['attention']['views']['execution']['files']
                              if file['file'] == 'coverage.txt')
                return native('read_evidence', {'refs': [source['ref']]})
            return cognition({'type': 'directive', 'text': 'Explain the supported subset and preserve the observed gap.'},
                task=task('new:b', proposed['intention_id'], 'Resolve the interpretation gap.',
                          'Write interpretation.txt limiting conclusions to observed A and retaining B as unknown.'))
        return cognition(effects=False)
    state_path = tmp_path / 'state'
    stack, nervous, mind, body, kernel = organs(state_path, workspace, transport, first=True,
                                              context_mode=context_mode)
    with stack:
        initial = nervous.submit(AUTHORITY['text'], 'USER_SCOPE')
        result = nervous.run(mind, body)
        assert result['execution']['status'] == 'completed'
        assert not any(result['pending'].values())
        assert not result['mind']['unresolved']
        pursuit_state = mind.cognition.pursuit_state()
        assert pursuit_state['mind_id'] == AUTHORITY['mind_id']
        assert len(pursuit_state['intentions']) == 1 and len(pursuit_state['tasks']) == 2
        contracts = body.state['task_contracts']
        assert contracts[0]['id'] != contracts[1]['id']
        assert contracts[0]['intention_id'] == contracts[1]['intention_id']
        assert contracts[0]['acceptance'] != contracts[1]['acceptance']
        assert len(kernel.actions) == 2
        assert initial.event_id in canonical(nervous.attention_view()) or initial.event_id in canonical(mind.state['owners'])
        before_cost = result['cost']
        prior_refs = [record['execution_ref'] for record in body.state['prior_runs']]
        assert len(prior_refs) == 1 and prior_refs[0] != result['execution']['execution_ref']
    assert (workspace / 'coverage.txt').read_text(encoding='utf-8') == 'Measured A; B remains unknown.'
    assert (workspace / 'interpretation.txt').read_text(encoding='utf-8') == 'Conclusion covers A only. B is still unknown.'
    stack, nervous, mind, body, kernel = organs(state_path, workspace, transport)
    with stack:
        quiet = nervous.run(mind, body)
        assert quiet['cost']['calls'] == before_cost['calls']
        assert mind.cognition.pursuit_state() == pursuit_state
        assert not kernel.actions
    b_wires = [wire for role, wire in wires if role == 'execution' and 'Resolve the interpretation gap.' in canonical(wire)]
    assert b_wires and all('Inventory source coverage.' not in canonical(wire) for wire in b_wires)
    if context_mode != 'baseline':
        b_id = contracts[1]['id']
        for role, wire in wires:
            if role != 'mind':
                continue
            payload = json.loads(wire['messages'][0]['content'])
            current = payload['cognition']['pursuit'].get('execution_task')
            if current and current['id'] == b_id:
                background = payload['derived_history_background']
                assert 'Inventory source coverage.' not in canonical(background)
                assert all(segment['content']['task_ref'] == {'id': b_id, 'revision': 1}
                           for segment in background['recent_activities'])


def test_query_continuation_keeps_frame_and_deferred_a_b_a_occurrences(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    observation = workspace / 'observation.txt'
    observation.write_text('initial', encoding='utf-8')
    wires = []
    def transport(role, wire):
        assert role == 'mind'
        wires.append(wire)
        if len(wires) == 1:
            return cognition(intentions=[pursuit()], watches=[watch()])
        if len(wires) == 2:
            return native('read_evidence', {'refs': ['view:execution.state']})
        return cognition(effects=False)
    state_path = tmp_path / 'state'
    stack, nervous, mind, execution, kernel = organs(state_path, workspace, transport, first=True)
    with stack:
        nervous.submit(AUTHORITY['text'], 'USER_SCOPE')
        initial = nervous.run(mind, execution)
        assert initial['mind']['revision'] == 1 and len(wires) == 1
        inquiry = nervous.submit('Inspect the current saved execution status.', submission_id='inquiry')
        waiting = nervous.run(mind, execution, max_steps=3)
        assert len(wires) == 2 and waiting['pending']['nervous']
        activity_id = mind.state['active']
        work = mind.state['activities'][activity_id]
        original_frame = canonical(work['attention'])
        original_request = work['request']['request_ref']
        unrelated = nervous.submit('A separate contextual note that does not change the authorized pursuit.',
                                    submission_id='unrelated')
        observation.write_text('A', encoding='utf-8')
        routed = nervous.run(mind, execution, max_steps=1)
        assert mind.state['active'] == activity_id and len(wires) == 2
        assert routed['pending']['mind.results']
        first_signal = next(event for event in nervous.pending('mind') if event.kind == 'attention.signal')
        assert first_signal.data['after']['ref'] != first_signal.data['before']['ref']
        assert unrelated.event_id in routed['pending']['mind']
    # New organ instances claim the known routed view result, not a fresh query.
    observation.write_text('B', encoding='utf-8')
    stack, nervous, mind, execution, kernel = organs(state_path, workspace, transport)
    with stack:
        resumed = nervous.run(mind, execution, max_steps=1)
        assert len(wires) == 3 and mind.state['active'] is None
        assert canonical(mind.state['activities'][activity_id]['attention']) == original_frame
        assert mind.state['activities'][activity_id]['request']['request_ref'] == original_request
        assert wires[2]['messages'][0] == wires[1]['messages'][0]
        assert wires[2]['messages'][-1]['content'][0]['type'] == 'tool_result'
        assert 'view-result:' in canonical(wires[2]['messages'][-1])
        signals = [event for event in nervous.pending('mind') if event.kind == 'attention.signal']
        assert len(signals) == 2 and signals[0].event_id == first_signal.event_id
        assert all(event.event_id in resumed['pending']['mind'] for event in signals)
        finished = nervous.run(mind, execution)
        assert not any(finished['pending'].values())
        # The frozen query review predates A/B. Its NoChange is not rebound to a
        # newer snapshot: one explicit applicability review is retained as well.
        assert any(receipt['status'] == 'superseded'
                   for receipt in mind.state['activities'][activity_id]['receipts'])
        assert finished['mind']['revision'] == 6 and len(wires) == 7
        assert mind.state['activities'][activity_id]['origin_event'] == inquiry.event_id
        assert len(mind.cognition.pursuit_state()['intentions']) == 1
        observation.write_text('A', encoding='utf-8')
        repeated = nervous.run(mind, execution)
        assert repeated['mind']['revision'] == 7 and len(wires) == 8
        events = nervous._load()['events']
        changes = [event for event in events if event['kind'] == 'attention.signal']
        assert len(changes) == 3 and len({event['event_id'] for event in changes}) == 3
        assert changes[0]['data']['after']['ref'] == changes[2]['data']['after']['ref']
        assert [event['data']['watch_matches'][0]['sequence'] for event in changes] == [1, 2, 3]
        assert not kernel.actions
    stack, nervous, mind, execution, kernel = organs(state_path, workspace, transport)
    with stack:
        quiet = nervous.run(mind, execution)
        assert quiet['mind']['revision'] == 7 and len(wires) == 8
        assert not any(quiet['pending'].values()) and not kernel.actions


@pytest.mark.parametrize('cut', ['before_effect_application', 'after_effect_application'])
def test_accepted_pursuit_and_watch_effect_survive_transport_cut(tmp_path, monkeypatch, cut):
    class Crash(BaseException):
        pass
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'observation.txt').write_text('initial', encoding='utf-8')
    wires = []
    def transport(role, wire):
        assert role == 'mind'
        wires.append(wire)
        assert len(wires) == 1
        return cognition(intentions=[pursuit()], watches=[watch()])
    original = NervousOrgan.handle
    hit = False
    def handle(self, event, mind, execution):
        nonlocal hit
        if not hit and event.kind == 'mind.effects':
            assert mind.cognition.inspect().revision == 1
            assert len(mind.cognition.pursuit_state()['intentions']) == 1
            hit = True
            if cut == 'after_effect_application':
                original(self, event, mind, execution)
            raise Crash()
        return original(self, event, mind, execution)
    monkeypatch.setattr(NervousOrgan, 'handle', handle)
    state_path = tmp_path / 'state'
    stack, nervous, mind, execution, kernel = organs(state_path, workspace, transport, first=True)
    with stack:
        nervous.submit(AUTHORITY['text'], 'USER_SCOPE')
        with pytest.raises(Crash):
            nervous.run(mind, execution)
        assert len(wires) == 1 and not kernel.actions
        retained = mind.cognition.pursuit_state()
        effect_event = next(event for event in nervous.pending('nervous') if event.kind == 'mind.effects')
    stack, nervous, mind, execution, kernel = organs(state_path, workspace, transport)
    with stack:
        result = nervous.run(mind, execution)
        assert hit and len(wires) == 1 and not kernel.actions
        assert mind.cognition.pursuit_state() == retained
        assert not any(result['pending'].values())
        stored = nervous._load()
        assert effect_event.event_id in stored['completed']
        watch_record = next(iter(stored['attention']['watches'].values()))
        assert watch_record['watch']['revision'] == 1 and watch_record['sequence'] == 0
        assert watch_record['history'] == []
        assert len([event for event in stored['events'] if event['kind'] == 'mind.decision']) == 1


def test_closing_watch_keeps_queued_old_signal_but_does_not_wake_closed_pursuit(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    observation = workspace / 'observation.txt'
    observation.write_text('initial', encoding='utf-8')
    wires = []
    def transport(role, wire):
        assert role == 'mind'
        wires.append(wire)
        if len(wires) == 1:
            return cognition(intentions=[pursuit()], watches=[watch()])
        assert len(wires) == 2, 'A cancelled subscription must not reactivate a closed pursuit.'
        state = json.loads(wire['messages'][0]['content'])['cognition']['pursuit']
        intent = next(iter(state['intentions'].values()))
        subscription = next(iter(state['watches'].values()))
        return cognition(intentions=[pursuit(intent['id'], intent['revision'], 'closed')],
                         watches=[watch(subscription['id'], intent['id'], subscription['revision'], 'cancelled')])
    state_path = tmp_path / 'state'
    stack, nervous, mind, execution, kernel = organs(state_path, workspace, transport, first=True)
    with stack:
        nervous.submit(AUTHORITY['text'], 'USER_SCOPE')
        nervous.run(mind, execution)
        nervous.submit('End the current investigation and its subscription; no task is outstanding.')
        observation.write_text('A', encoding='utf-8')
        # Commit the close while the first observed change remains independently pending.
        paused = nervous.run(mind, execution, max_steps=3)
        assert len(wires) == 2 and paused['pending']['nervous']
        old_signal = next(event for event in nervous.pending('mind') if event.kind == 'attention.signal')
        assert old_signal.data['watch_matches'][0]['watch_revision'] == 1
    stack, nervous, mind, execution, kernel = organs(state_path, workspace, transport)
    with stack:
        closed = nervous.run(mind, execution)
        assert not any(closed['pending'].values()) and len(wires) == 2
        state = nervous._load()
        assert old_signal.event_id in state['completed']
        assert state['completed'][old_signal.event_id] == []
        assert any(event['event_id'] == old_signal.event_id for event in state['events'])
        assert state['attention']['focus'] is None
        record = next(iter(state['attention']['watches'].values()))
        assert record['watch']['revision'] == 2 and record['lifecycle'] == 'cancelled'
        assert record['history'][0]['watch']['revision'] == 1
        observation.write_text('B', encoding='utf-8')
        nervous.run(mind, execution)
        assert len(wires) == 2 and not kernel.actions


def test_full_authorization_can_be_read_without_changing_its_source_identity(tmp_path, monkeypatch):
    text = 'The owner authorizes bounded investigation of these supplied records. ' * 20
    assert 1000 < len(text) < 4000
    monkeypatch.setitem(AUTHORITY, 'text', text)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    wires = []
    def transport(role, wire):
        assert role == 'mind'
        wires.append(wire)
        if len(wires) == 1:
            return native('read_evidence', {'refs': [AUTHORITY['ref']]})
        assert len(wires) == 2
        actual = next(source for source in source_records(wire) if source['ref'] == AUTHORITY['ref'])
        assert actual['text'] == text
        return cognition(effects=False)
    stack, nervous, mind, execution, kernel = organs(tmp_path / 'state', workspace, transport, first=True)
    with stack:
        nervous.submit(text, 'USER_SCOPE')
        result = nervous.run(mind, execution)
        assert result['mind']['revision'] == 1 and not result['mind']['unresolved']
        assert mind.source_record(AUTHORITY['ref'])['text'] == text
        assert mind.cognition.read_source(AUTHORITY['ref'])['text'] == text
        assert len(wires) == 2 and not kernel.actions
