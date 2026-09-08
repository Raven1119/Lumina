"""User events reach Mind before any action, including without an existing run."""
import json

import pytest

from Mind.chain import Session, read_json
from Mind.test_chain import LocalTestPython, cognitive, reply


def view(wire):
    return json.loads(wire['messages'][0]['content'])


def direction():
    return cognitive({'type': 'directive', 'text':
        'Complete the authorized result with the required content, then report the observed outcome.'})


def test_start_thinks_before_first_action_and_receives_result(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    calls = []
    def transport(role, wire):
        calls.append((role, wire))
        if role == 'mind':
            if len(calls) == 1:
                assert view(wire)['activation']['execution_status'] is None
                assert view(wire)['cognition']['execution_ref'] is None
                assert 'USER_GOAL' in view(wire)['current_activity']
                return direction()
            assert (workspace / 'result.json').exists()
            return cognitive()
        assert calls[0][0] == 'mind'
        assert any('Complete the authorized result' in str(m['content']) for m in wire['messages'])
        count = json.loads(wire['messages'][0]['content'][0]['text'])['state']['decision_count']
        return reply('ipython', {'code': 'finish'}) if count == 0 else reply('claim_complete', {})

    with Session(tmp_path / 'session', workspace=workspace, goal='Deliver result.json.',
                 transport=transport, ipython=LocalTestPython(workspace)) as session:
        status = session.run()
        assert status['execution_status'] == 'completed'
        assert status['mind_revision'] >= 2
        assert status['deliveries'][0]['call_ref']
        assert status['deliveries'][0]['feedback_event']
        assert session.state['owner_inputs'][0]['status'] == 'reviewed'
        activation = next(event for event in session.nervous._load()['events'] if event['kind'] == 'mind.activate')
        assert activation['source'] == 'user'


def test_no_action_understanding_is_durable_and_later_message_can_start_work(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    directory = tmp_path / 'session'; calls = []
    def transport(role, wire):
        calls.append(role)
        if role == 'mind':
            data = view(wire)
            if data['activation']['execution_status'] is None and 'Please proceed' in data['current_activity']:
                return direction()
            return cognitive()
        count = json.loads(wire['messages'][0]['content'][0]['text'])['state']['decision_count']
        return reply('ipython', {'code': 'finish'}) if count == 0 else reply('claim_complete', {})
    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Keep this goal pending until I ask to proceed.', **options) as session:
        status = session.run()
        assert status['execution_status'] == 'not_started'
        assert session.execution.state is None and calls == ['mind']
        assert not list(workspace.iterdir())
    with Session(**options) as session:
        session.run()
        assert calls == ['mind']
        status = session.run(owner_event=('USER_MESSAGE', 'Please proceed with the authorized result now.'))
        assert status['execution_status'] == 'completed'
        assert calls[:3] == ['mind', 'mind', 'execution']


def test_user_input_does_not_depend_on_execution_wait_condition(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    calls = []; redirected = False
    def transport(role, wire):
        nonlocal redirected
        calls.append(role)
        if role == 'mind':
            data = view(wire)
            if data['activation']['execution_status'] is None:
                return direction()
            if 'Use the available material' in data['current_activity']:
                redirected = True
                return direction()
            return cognitive()
        if not redirected:
            return reply('wait', {'event_type': 'UNAVAILABLE_FEED'})
        if not (workspace / 'result.json').exists():
            return reply('ipython', {'code': 'finish'})
        return reply('claim_complete', {})
    with Session(tmp_path / 'session', workspace=workspace, goal='Deliver within the available scope.',
                 transport=transport, ipython=LocalTestPython(workspace)) as session:
        assert session.run()['execution_status'] == 'waiting'
        before = len(calls)
        session.run(owner_event=('USER_MESSAGE', 'Use the available material and deliver the authorized partial result.'))
        assert calls[before] == 'mind'
        assert session.execution.state.status == 'completed'
        events = session.execution._event_log.events
        assert any(event.event_type == 'ROOT_REDIRECTED' for event in events)
        assert not any(event.event_type == 'EXTERNAL_EVENT_RECEIVED' for event in events)


def test_initial_consultation_restores_without_creating_execution(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    text = 'Observed original source. ' * 70
    (workspace / 'source.txt').write_text(text)
    directory = tmp_path / 'session'; calls = []
    class Cut(BaseException):
        pass
    def transport(role, wire):
        assert role == 'mind'
        calls.append(wire)
        if len(calls) == 1:
            records = view(wire)['source_records']
            catalogue = json.loads(next(s['text'] for s in records if s.get('label') == 'workspace catalogue'))
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': [catalogue[0]['ref']]}, wire=wire)
        assert text in json.dumps(wire)
        return cognitive()
    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Understand the source; do not act.', **options) as session:
        with monkeypatch.context() as patch:
            patch.setattr(session, 'consult', lambda _: (_ for _ in ()).throw(Cut()))
            with pytest.raises(Cut):
                session.run()
        assert session.execution.state is None
        request_id = session.nervous.pending('mind.requests', 1)[0].event_id
    with Session(**options) as session:
        assert session.nervous.pending('mind.requests', 1)[0].event_id == request_id
        result = session.run()
        assert result['execution_status'] == 'not_started'
        assert result['mind_revision'] == 1 and len(calls) == 2
        session.run()
        assert len(calls) == 2


@pytest.mark.parametrize('after_start', [False, True])
def test_receipt_recovery_starts_only_one_real_run(tmp_path, monkeypatch, after_start):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    directory = tmp_path / 'session'; roles = []
    class Cut(BaseException):
        pass
    def transport(role, wire):
        roles.append(role)
        if role == 'mind':
            return direction() if len(roles) == 1 else cognitive()
        if not (workspace / 'result.json').exists():
            return reply('ipython', {'code': 'finish'})
        return reply('claim_complete', {})
    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Deliver the authorized result.', **options) as session:
        original = session.execution.run_goal
        def interrupted(*args, **kwargs):
            if after_start:
                original(*args, **kwargs)
            raise Cut()
        with monkeypatch.context() as patch:
            patch.setattr(session.execution, 'run_goal', interrupted)
            with pytest.raises(Cut):
                session.run()
        assert roles == ['mind']
        initial_run = session.execution.state.execution_id if session.execution.state else None
    with Session(**options) as session:
        result = session.run()
        assert result['execution_status'] == 'completed'
        assert len(result['deliveries']) == 1
        if initial_run:
            assert session.execution.state.execution_id == initial_run
        assert not session.state.get('prior_runs')


def test_failed_initial_cognition_does_not_start_or_silently_retry(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    calls = []
    def transport(role, wire):
        calls.append(role)
        assert role == 'mind'
        return cognitive(updates=[{'kind': 'belief', 'id': 'new:bad', 'status': 'supported',
            'claim': 'An ungrounded claim.', 'basis': [{'ref': 'nonexistent-source'}]}])
    options = dict(directory=tmp_path / 'session', transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Deliver only under the known conditions.', **options) as session:
        result = session.run()
        assert result['execution_status'] == 'not_started'
        assert result['obligation']['status'] == 'failed'
        count = len(calls)
    with Session(**options) as session:
        result = session.run()
        assert len(calls) == count and result['obligation']['status'] == 'failed'
        assert result['mind_revision'] == 0


def test_bound_initial_guidance_survives_before_provider_restart(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    directory = tmp_path / 'session'; roles = []
    class Cut(BaseException):
        pass
    def transport(role, wire):
        roles.append(role)
        if role == 'mind':
            return direction() if len(roles) == 1 else cognitive()
        assert any('Complete the authorized result' in str(m['content']) for m in wire['messages'])
        return reply('ipython', {'code': 'finish'}) if not (workspace/'result.json').exists() else reply('claim_complete', {})
    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Deliver the result.', **options) as session:
        with monkeypatch.context() as patch:
            patch.setattr(session.execution, 'resume', lambda **_: (_ for _ in ()).throw(Cut()))
            with pytest.raises(Cut):
                session.run()
        assert roles == ['mind'] and len(session.state['deliveries']) == 1
        run_id = session.execution.state.execution_id
        event_id = session.state['obligation']['event_id']
    with Session(**options) as session:
        result = session.run()
        assert result['execution_status'] == 'completed'
        assert session.execution.state.execution_id == run_id
        assert len(result['deliveries']) == 1 and result['deliveries'][0]['feedback_event']
        assert not session.nervous.pending('host', 1)
        assert result['deliveries'][0]['event_id'] == event_id


def test_message_queued_during_initial_consultation_has_own_mind_event(tmp_path, monkeypatch):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    (workspace/'source.txt').write_text('Original source ' * 200)
    directory = tmp_path/'session'; wires = []
    class Cut(BaseException):
        pass
    def transport(role, wire):
        assert role == 'mind'
        wires.append(wire)
        if len(wires) == 1:
            catalogue = json.loads(next(s['text'] for s in view(wire)['source_records']
                                       if s.get('label') == 'workspace catalogue'))
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': [catalogue[0]['ref']]}, wire=wire)
        return cognitive()
    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Understand the source without action.', **options) as session:
        with monkeypatch.context() as patch:
            patch.setattr(session, 'consult', lambda _: (_ for _ in ()).throw(Cut()))
            with pytest.raises(Cut):
                session.run()
    with Session(**options) as session:
        status = session.run(owner_event=('USER_MESSAGE', 'Also preserve the original scope; still do not act.'))
        assert status['execution_status'] == 'not_started'
        assert len(wires) == 3
        assert 'Also preserve' in view(wires[-1])['current_activity']
        assert [item['status'] for item in session.state['owner_inputs']] == ['superseded', 'reviewed']
        events = [e for e in session.nervous._load()['events'] if e['kind'] == 'mind.activate']
        assert len(events) == 2 and len({e['event_id'] for e in events}) == 2


def test_pre_execution_analysis_uses_isolated_builder_then_action_feedback(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    (workspace/'policy.txt').write_text('Only the declared local result is authorized.')
    directory = tmp_path/'session'; roles = []; mind_count = 0
    def transport(role, wire):
        nonlocal mind_count
        roles.append(role)
        if role == 'builder':
            assert 'prior_model_judgments' not in json.dumps(wire)
            return reply('report', {'run_ref': '', 'answer': 'The authorized scope is the declared local result.',
                                   'assumptions': 'The stated authorization remains current.', 'unknowns': ''})
        if role == 'mind':
            mind_count += 1
            if mind_count == 1:
                ref = next(s['ref'] for s in view(wire)['source_records'] if 'Only the declared' in s['text'])
                return cognitive({'type': 'capability_request', 'capability': 'analyze_world_model',
                                  'question': 'Assess the scope and consequences of proceeding.', 'refs': [ref], 'model_ref': ''}, wire=wire)
            if mind_count == 2:
                assert view(wire)['activation']['execution_status'] is None
                assert 'authorized scope is the declared' in json.dumps(wire)
                return direction()
            assert (workspace/'result.json').exists()
            return cognitive()
        assert mind_count >= 2
        assert 'assumptions' not in json.dumps(wire)
        return reply('ipython', {'code': 'finish'}) if not (workspace/'result.json').exists() else reply('claim_complete', {})
    with Session(directory, workspace=workspace, goal='Deliver the authorized local result.',
                 transport=transport, ipython=LocalTestPython(workspace)) as session:
        result = session.run()
        assert result['execution_status'] == 'completed'
        assert roles[:4] == ['mind', 'builder', 'mind', 'execution']
        assert result['deliveries'][0]['feedback_event']


def test_long_user_message_preserves_complete_readable_source_without_execution(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'; wires = []; source_ref = None
    message = 'Preserve this bounded original evidence. ' * 35 + 'Only assess; do not act.'
    assert 1000 < len(message) <= 4000

    def transport(role, wire):
        nonlocal source_ref
        assert role == 'mind'
        wires.append(wire)
        if len(wires) == 1:
            return cognitive()
        if len(wires) == 2:
            catalogue = next(source for source in view(wire)['source_records']
                             if source.get('label') == 'user message source catalogue')
            source_ref = json.loads(catalogue['text'])['user_message_ref']
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence',
                              'refs': [source_ref]}, wire=wire)
        assert message in json.dumps(wire, ensure_ascii=False)
        return cognitive(updates=[{'kind': 'belief', 'id': 'new:user-scope',
            'claim': 'The user requests assessment without action.', 'status': 'supported',
            'basis': [{'ref': source_ref}]}])

    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Await the evidence and assess it.', **options) as session:
        assert session.run()['execution_status'] == 'not_started'
        result = session.run(owner_event=('USER_MESSAGE', message))
        assert result['execution_status'] == 'not_started'
        assert result['obligation']['status'] == 'accepted'
        assert session.state['owner_inputs'][-1]['status'] == 'reviewed'
        assert json.loads(session.mind.read_source(source_ref)['text']) == {
            'event_type': 'USER_MESSAGE', 'data': message}
        assert result['cognition'][0]['basis'] == [{'ref': source_ref}]
        assert not list(workspace.iterdir())
    with Session(**options) as session:
        assert session.run()['execution_status'] == 'not_started'
        assert json.loads(session.mind.read_source(source_ref)['text'])['data'] == message
    assert len(wires) == 3


def test_failed_earlier_user_event_does_not_block_queued_later_event(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'; activities = []

    def transport(role, wire):
        assert role == 'mind'
        activity = view(wire)['current_activity']
        activities.append(activity)
        if 'USER_GOAL' in activity:
            return cognitive(updates=[{'kind': 'belief', 'id': 'new:invalid',
                'claim': 'This claim lacks an admitted source.', 'status': 'supported',
                'basis': [{'ref': 'nonexistent-source'}]}])
        assert 'Assess the later request without action.' in activity
        return cognitive()

    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Assess the first request.', **options) as session:
        result = session.run(owner_event=('USER_MESSAGE', 'Assess the later request without action.'))
        assert result['execution_status'] == 'not_started'
        assert [item['status'] for item in session.state['owner_inputs']] == ['failed', 'reviewed']
        assert result['obligation']['owner_input_sequence'] == 2
        assert result['obligation']['status'] == 'accepted'
        assert result['mind_revision'] == 1
        assert 'USER_MESSAGE' in activities[-1]
        count = len(activities)
    with Session(**options) as session:
        assert session.run()['execution_status'] == 'not_started'
        assert len(activities) == count
        assert [item['status'] for item in session.state['owner_inputs']] == ['failed', 'reviewed']


@pytest.mark.parametrize('later_directive', [False, True])
def test_queued_user_messages_are_judged_before_any_execution(tmp_path, later_directive):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    roles = []; mind_count = 0

    def transport(role, wire):
        nonlocal mind_count
        roles.append(role)
        if role == 'mind':
            mind_count += 1
            if mind_count == 1:
                assert 'USER_GOAL' in view(wire)['current_activity']
                return direction()
            if mind_count == 2:
                assert 'USER_MESSAGE' in view(wire)['current_activity']
                assert session.execution.state is None
                return direction() if later_directive else cognitive()
            return cognitive()
        assert roles[:2] == ['mind', 'mind']
        assert not session.owner_review_pending()
        return (reply('ipython', {'code': 'finish'}) if not (workspace/'result.json').exists()
                else reply('claim_complete', {}))

    message = 'Proceed with the authorized result.' if later_directive else 'Only assess; do not act yet.'
    with Session(tmp_path/'session', workspace=workspace, goal='Deliver the authorized result.',
                 transport=transport, ipython=LocalTestPython(workspace)) as session:
        result = session.run(owner_event=('USER_MESSAGE', message))
        assert roles[:2] == ['mind', 'mind']
        assert [item['status'] for item in session.state['owner_inputs']] == ['superseded', 'reviewed']
        if later_directive:
            assert result['execution_status'] == 'completed'
            assert len(result['deliveries']) == 1
        else:
            assert result['execution_status'] == 'not_started'
            assert roles == ['mind', 'mind'] and result['deliveries'] == []
            assert not list(workspace.iterdir())


def test_pre_execution_prediction_keeps_feedback_binding_after_start_interruption(tmp_path, monkeypatch):
    from collections import Counter
    from Mind import world_model as wm

    workspace = tmp_path/'workspace'; workspace.mkdir()
    (workspace/'rule.txt').write_text('Initial count is three. Add two to reach five.')
    directory = tmp_path/'session'; counts = Counter(); feedback_sources = []; source_ref = None

    class Cut(BaseException):
        pass

    def compute(request):
        return json.dumps({'initial': {'observation': request['initial_observation'], 'outcome': 'ongoing'},
                           'steps': [{'observation': {'count': 5}, 'outcome': 'complete'}]}).encode()

    monkeypatch.setattr(wm, '_compute', compute)

    def transport(role, wire):
        counts[role] += 1
        if role == 'mind':
            if counts[role] == 1:
                return cognitive({'type': 'capability_request', 'capability': 'analyze_world_model',
                    'question': 'Predict the final count.', 'refs': [source_ref],
                    'model_ref': '', 'observation_file': 'result.json'}, wire=wire)
            if counts[role] == 2:
                return direction()
            feedback_sources.extend(source for source in view(wire)['source_records']
                                    if source.get('label') == 'model feedback')
            return cognitive()
        if role == 'builder':
            if counts[role] == 1:
                return reply('compute', {'source': '# deterministic test computation',
                    'initial_observation': {'count': 3}, 'actions': [{'add': 2}],
                    'observation_file': 'result.json', 'check_spec': {
                        'action': {'add': 2}, 'conditions': {}, 'object': 'result.json',
                        'when': 'after_add', 'quantities': {'count': {'meaning': 'Number of items', 'unit': 'items'}}}})
            computed = json.loads(wire['messages'][-1]['content'][0]['content'])
            return reply('report', {'run_ref': computed['run_ref'],
                'answer': 'The conditional final count is five.',
                'assumptions': 'The sourced additive rule applies.',
                'unknowns': 'Future execution has not occurred.'})
        return (reply('ipython', {'code': 'finish'}) if not (workspace/'result.json').exists()
                else reply('claim_complete', {}))

    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace),
                   limits={'calls': 40, 'output_tokens': 300000, 'request_bytes': 2800000})
    with Session(workspace=workspace, goal='Deliver result.json with the final count.', **options) as session:
        source_ref = session.file_source('rule.txt')['ref']
        original = session.execution.run_goal

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise Cut()

        with monkeypatch.context() as patch:
            patch.setattr(session.execution, 'run_goal', interrupted)
            with pytest.raises(Cut):
                session.run()
        run_id = session.execution.state.execution_id
        initial_event = session.state['obligation']['event_id']
        prediction_ref = session.state['predictions'][0]['ref']
        assert session.state['predictions'][0]['execution_ref'] is None
        assert counts == {'mind': 2, 'builder': 2}
    with Session(**options) as session:
        result = session.run()
        assert result['execution_status'] == 'completed'
        assert session.execution.state.execution_id == run_id
        prediction, = session.current_predictions()
        assert prediction['ref'] == prediction_ref
        assert prediction['execution_ref'] == run_id
        assert prediction['execution_binding_event'] == initial_event
        assert prediction['receipt'] is None  # No pre-action Execution receipt existed for this computation.
        assert prediction['feedback_event']
        assert feedback_sources
        assert counts['builder'] == 2
        assert not session.state.get('prior_runs')
        saved_counts = dict(counts)
        session.run()
        assert counts == saved_counts


@pytest.mark.parametrize('later_fails', [False, True], ids=['nochange', 'ungrounded'])
@pytest.mark.parametrize('cut', [None, 'before_cancel', 'after_cancel'])
def test_new_user_judgment_withdraws_unconsumed_direction_before_execution(tmp_path, monkeypatch, later_fails, cut):
    from Mind.chain import BudgetPause

    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'; wires = []; paused = []
    advice = 'Use the superseded provisional interpretation to choose the next work.'
    message = 'Reassess that interpretation without advancing the work.'

    class Cut(BaseException):
        pass

    def transport(role, wire):
        wires.append((role, wire))
        if role == 'execution':
            assert sum(r == 'execution' for r, _ in wires) == 1, 'Expired guidance reached Execution.'
            assert advice not in json.dumps(wire)
            return reply('wait', {'event_type': 'UNAVAILABLE_FEED'})
        assert role == 'mind'
        activity = view(wire)['current_activity']
        if 'USER_GOAL' in activity:
            return direction()
        if message not in activity:
            assert view(wire)['activation']['execution_status'] == 'waiting'
            return cognitive({'type': 'directive', 'text': advice})
        assert session.execution.state.waiting_for == 'UNAVAILABLE_FEED'
        if later_fails:
            return cognitive(updates=[{'kind': 'belief', 'id': 'new:ungrounded',
                'claim': 'The provisional interpretation is verified.', 'status': 'supported',
                'basis': [{'ref': 'nonexistent-source'}]}])
        return cognitive()

    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Assess the available scope and await missing evidence.', **options) as session:
        original_ensure = session.calls.ensure

        def pause_before_dispatch(wire, *, role=None):
            if advice in json.dumps(wire) and any(tool['name'] == 'ipython' for tool in wire.get('tools', [])):
                paused.append(wire)
                raise BudgetPause('test_before_provider_dispatch')
            return original_ensure(wire, role=role)

        with monkeypatch.context() as patch:
            patch.setattr(session.calls, 'ensure', pause_before_dispatch)
            result = session.run()
        assert result['stop_reason'] == 'test_before_provider_dispatch'
        assert session.execution.state.status == 'running' and session.execution.state.decision_count == 1
        assert [role for role, _ in wires] == ['mind', 'execution', 'mind'] and len(paused) == 1
        old = next(delivery for delivery in session.state['deliveries'] if delivery['text'] == advice)
        assert old['status'] == 'bound' and 'call_ref' not in old
        old_id, decision = old['directive_id'], old['decision']
        revision = session.mind.inspect().revision
        original_resume = session.execution.resume

        def interrupted_cancel(*, decision_advisory=None):
            if decision_advisory == (decision, None):
                saved = read_json(directory/'session.json')['state']
                assert saved['owner_inputs'][-1]['data'] == message
                assert saved['owner_inputs'][-1]['status'] == 'pending'
                if cut == 'before_cancel':
                    raise Cut()
                original_resume(decision_advisory=decision_advisory)
                raise Cut()
            return original_resume(decision_advisory=decision_advisory)

        if cut:
            with monkeypatch.context() as patch:
                patch.setattr(session.execution, 'resume', interrupted_cancel)
                with pytest.raises(Cut):
                    session.run(owner_event=('USER_MESSAGE', message))
            assert old['status'] == 'bound'
            assert session.execution.state.status == ('running' if cut == 'before_cancel' else 'waiting')
            assert [role for role, _ in wires] == ['mind', 'execution', 'mind']
        else:
            session.run(owner_event=('USER_MESSAGE', message))

    with Session(**options) as session:
        result = session.run()
        assert result['execution_status'] == 'waiting'
        assert session.execution.state.waiting_for == 'UNAVAILABLE_FEED'
        assert session.execution.state.decision_count == 1
        assert result['obligation']['status'] == ('failed' if later_fails else 'accepted')
        assert session.state['owner_inputs'][-1]['status'] == ('failed' if later_fails else 'reviewed')
        assert session.mind.inspect().revision == revision + (not later_fails)
        old = next(delivery for delivery in session.state['deliveries'] if delivery['directive_id'] == old_id)
        assert old['status'] == 'expired_on_owner_reassessment' and 'call_ref' not in old
        cancellations = [event for event in session.execution._event_log.events
                         if event.event_type == 'ROOT_REDIRECTED' and event.payload['advisory'] == (decision, None)]
        assert len(cancellations) == 1
        assert not any(event.event_type == 'EXTERNAL_EVENT_RECEIVED' for event in session.execution._event_log.events)
        assert sum(role == 'execution' for role, _ in wires) == 1
        assert all(message in view(wire)['current_activity'] for role, wire in wires[3:])
        assert not list(workspace.iterdir())
        records = [read_json(path) for path in (directory/'calls').glob('*.json')]
        assert len(records) == len(wires) and all(record['status'] == 'received' for record in records)
        count, cost = len(wires), result['cost']
        assert session.run()['cost'] == cost and len(wires) == count
    with Session(**options) as session:
        assert session.run()['cost'] == cost and len(wires) == count
        assert session.execution.state.waiting_for == 'UNAVAILABLE_FEED'
