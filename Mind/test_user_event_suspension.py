"""A suspended, unconsumed direction expires before a later user judgment."""
import json

import Execution.execution as execution_runtime
from Mind.chain import Session
from Mind.test_chain import LocalTestPython, cognitive, reply


def test_new_user_message_withdraws_guidance_suspended_before_provider(tmp_path, monkeypatch):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'; calls = []; execution_wires = []
    old_direction = 'Proceed under direction A and its original conditions.'

    def transport(role, wire):
        calls.append(role)
        if role == 'mind':
            payload = json.loads(wire['messages'][0]['content'])
            if payload['activation']['execution_status'] is None:
                return cognitive({'type': 'directive', 'text': 'Begin the bounded authorized work.'})
            activity = payload['current_activity']
            if 'User input' in activity and 'Apply direction A.' in activity:
                return cognitive({'type': 'directive', 'text': old_direction})
            return cognitive()
        assert role == 'execution'
        execution_wires.append(wire)
        return reply('wait', {'event_type': 'OUTSIDE'})

    options = dict(directory=directory, transport=transport, ipython=LocalTestPython(workspace))
    with Session(workspace=workspace, goal='Work only within current authorization.', **options) as session:
        assert session.run()['execution_status'] == 'waiting'
        run_id = session.execution.state.execution_id
        original = execution_runtime._build_model_request

        def interrupt_before_dispatch(*args, **kwargs):
            request = original(*args, **kwargs)
            # The real interruption occurs after durable redirection, before
            # dispatch. resume() returns normally with no model decision.
            session.execution.interrupt()
            return request

        with monkeypatch.context() as patch:
            patch.setattr(execution_runtime, '_build_model_request', interrupt_before_dispatch)
            result = session.run(owner_event=('USER_MESSAGE', 'Apply direction A.'))
        assert result['execution_status'] == 'suspended'
        assert len(execution_wires) == 1
        delivery = next(item for item in result['deliveries'] if item['text'] == old_direction)
        assert delivery['status'] == 'bound' and not delivery.get('call_ref')
        old_decision = delivery['decision']
        old_advisory = next(event.payload['advisory'] for event in session.execution._event_log.events
                            if event.event_type == 'ROOT_REDIRECTED')
        assert old_advisory[0] == old_decision and old_advisory[1].endswith(old_direction)
        assert any(event.event_type == 'ACTOR_SUSPENDED' for event in session.execution._event_log.events)

    with Session(**options) as session:
        result = session.run(owner_event=('USER_MESSAGE', 'Only reassess direction B; do not act.'))
        assert result['obligation']['status'] == 'accepted'
        assert result['execution_status'] == 'suspended'
        assert session.execution.state.waiting_for == 'OUTSIDE'
        assert session.execution.state.execution_id == run_id
        assert len(execution_wires) == 1
        delivery = next(item for item in result['deliveries'] if item['text'] == old_direction)
        assert delivery['status'] == 'expired_on_owner_reassessment'
        assert not delivery.get('call_ref')
        redirects = [event for event in session.execution._event_log.events
                     if event.event_type == 'ROOT_REDIRECTED']
        assert [event.payload['advisory'] for event in redirects] == [
            old_advisory, (old_decision, None)]
        calls_after_judgment = list(calls)

    with Session(**options) as session:
        assert session.run()['execution_status'] == 'waiting'
        assert session.execution.state.waiting_for == 'OUTSIDE'
        assert calls == calls_after_judgment
        assert not any(old_direction in json.dumps(wire) for wire in execution_wires)
