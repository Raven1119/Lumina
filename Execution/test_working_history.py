"""Working projections come from completed owner facts, not nested provider prompts."""
import json

import pytest

from Execution.test_runtime import decision, native, runtime


def test_owner_history_keeps_early_actions_without_embedded_requests(tmp_path):
    answers = iter([native('ipython', {'code': 'pass # step ' + str(i)}, str(i)) for i in range(14)])
    owner, calls, control = runtime(tmp_path, answers)
    try:
        owner.handle(decision(owner, 'start', 'Preserve the observed scope.'))
        for _ in range(14):
            owner.advance()
        segments = owner.actor.history_segments()
        assert len(segments) == 14
        assert 'pass # step 0' in json.dumps(segments[0])
        assert 'Synthetic observed action result.' in json.dumps(segments[0])
        assert 'actual_request' not in json.dumps(segments)
        assert 'provider_wire_request' not in json.dumps(segments)
        assert len(owner.actor.committed_tool_calls(limit=120)) == 14
        assert len(owner.actor.committed_tool_calls()) == 6
        assert len(owner.history.history(14, owner.run_state().execution_id,
            owner.actor.committed_tool_calls(limit=120), limit=120, max_chars=150000)) == 14
    finally:
        owner.close()


def test_compaction_calls_are_not_parsed_as_execution_decisions(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([native('ipython', {'code': 'pass'}, 'body')]))
    try:
        owner.handle(decision(owner, 'start', 'Use the source conditions.'))
        owner.advance()
        from Nervous.provider import MODEL
        calls.transport = lambda *_: {'content': [{'type': 'text', 'text': 'Background.'}], 'stop_reason': 'end_turn'}
        calls.call('execution', {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Summarize.'}],
            'max_tokens': 100}, operation='summary-test', purpose='compaction')
        assert len(owner.execution_context(1)['rounds']) == 1
    finally:
        owner.close()


@pytest.mark.parametrize('mode', ['summary', 'mask'])
def test_working_history_survives_three_compactions_and_restart(tmp_path, mode):
    from Execution.runtime import Execution
    owner, calls, control = runtime(tmp_path, iter([]))
    owner.state['context_mode'] = mode  # Isolated pre-start configuration.
    owner.save()
    actions = 0
    def transport(role, wire):
        nonlocal actions
        if not wire.get('tools'):
            payload = json.loads(wire['messages'][0]['content'])
            # Scripted summary validates transport only, not semantic fidelity.
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text',
                'text': json.dumps({'summary': 'Earlier cells completed; retain the observed scope.',
                                    'source_refs': [payload['segments'][0]['ref']]})}]}
        actions += 1
        return native('ipython', {'code': 'pass # action ' + str(actions)}, str(actions))
    calls.transport = transport
    try:
        owner.handle(decision(owner, 'start', 'Retain the observed scope; no permanent conclusion.'))
        for i in range(25):
            owner.advance()
            if i == 12:
                owner.close()
                owner = Execution(owner.directory, calls, ipython=control)
        assert len(control.actions) == 25
        summaries = [r for _, r in calls.records() if r.get('purpose') == 'compaction']
        assert len(summaries) == (3 if mode == 'summary' else 0)
        projection = owner.execution_context(25)
        if mode == 'summary':
            assert projection['derived_history_handoff']['revision'] == 3
            assert len(projection['derived_history_handoff']['source_refs']) == 18
        else:
            assert len(projection['masked_execution_history']) == 19
            assert 'omitted_chars' in json.dumps(projection['masked_execution_history'])
        used = calls.summary()['calls']
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.execution_context(25) == projection
        assert calls.summary()['calls'] == used
        wire = [r['wire'] for _, r in calls.records(role='execution') if r.get('purpose', 'decision') == 'decision'][-1]
        text = json.dumps(wire)
        assert 'Retain the observed scope; no permanent conclusion.' in text
        assert len(wire['messages']) > 1
        for assistant, user in zip(wire['messages'], wire['messages'][1:]):
            if assistant['role'] == 'assistant':
                assert user['role'] == 'user'
                assert {b['id'] for b in assistant['content'] if b['type'] == 'tool_use'} == {
                    b['tool_use_id'] for b in user['content'] if b['type'] == 'tool_result'}
    finally:
        owner.close()


def test_pressure_compacts_short_history_and_keeps_latest_native_round(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([
        native('ipython', {'code': 'pass # first'}, 'first'),
        native('ipython', {'code': 'pass # second'}, 'second')]))
    owner.state['context_mode'] = 'summary'
    owner.save()
    try:
        owner.handle(decision(owner, 'start', 'Retain the exact observation scope.'))
        owner.advance()
        owner.advance()
        original = owner.actor.history_segments()
        normal = owner.execution_context(2)
        assert len(normal['rounds']) == 2
        assert not normal.get('derived_history_handoff')
        wires = []
        def summarize(role, wire):
            wires.append(wire)
            payload = json.loads(wire['messages'][0]['content'])
            assert payload['segments'] == original[:1]
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text',
                'text': json.dumps({'summary': 'First cell completed; keep its source.',
                                    'source_refs': [original[0]['ref']]})}]}
        calls.transport = summarize
        forced = owner.execution_context(2, force=True)
        assert len(wires) == 1
        assert forced['derived_history_handoff']['source_refs'] == [original[0]['ref']]
        assert forced['rounds'] == normal['rounds'][-1:]
        assert owner.actor.history_segments() == original
        assert len(control.actions) == 2
        assert owner.execution_context(2) == forced
        assert len(wires) == 1
    finally:
        owner.close()


def test_read_history_exports_only_bounded_owner_records(tmp_path, monkeypatch):
    from Execution.sandbox import _HISTORY_HELPER, DockerIPython
    from Nervous.storage import fingerprint, write_json
    workspace, history = tmp_path / 'workspace', tmp_path / 'history'
    workspace.mkdir(); history.mkdir()
    ref = 'execution-history:run:decision-000001'
    content = {'output': 'x' * 12000, 'truncated': True, 'original_output_chars': 18000}
    write_json(history / (fingerprint(ref) + '.json'), {'ref': ref, 'content': content})
    monkeypatch.setenv('LUMINA_HISTORY_DIRECTORY', str(history))
    ns = {}
    exec(_HISTORY_HELPER, ns)
    first = ns['read_history'](ref, limit=8000)
    second = ns['read_history'](ref, offset=first['next_offset'])
    assert json.loads(first['text'] + second['text']) == content
    assert second['next_offset'] is None
    with pytest.raises(ValueError):
        ns['read_history']('../calls/0001')
    with pytest.raises(ValueError):
        ns['read_history'](ref, limit=8001)
    command = DockerIPython(workspace, history_directory=history).command()
    assert any(str(history) in item and item.endswith('target=/lumina-history,readonly') for item in command)


@pytest.mark.parametrize('mode', ['summary', 'mask'])
def test_cold_partial_batch_does_not_erase_uncovered_recent_history(tmp_path, mode):
    from Execution.runtime import Execution
    from Execution.test_runtime import Python
    from Nervous.provider import BudgetPause, ProviderCalls
    answers = iter([
        native('ipython', {'code': "(workspace / 'early.txt').write_text('93847') # EARLY_EXACT_PARAMETER_93847"}, 'early'),
        native('ipython', {'code': 'pass # second'}, 'second'),
        native('ipython', {'code': 'pass # third'}, 'third')])
    owner, calls, control = runtime(tmp_path, answers)
    owner.state['context_mode'] = mode
    owner.save()
    owner.handle(decision(owner, 'start', 'Preserve the observed scope and exact parameters.'))
    try:
        for _ in range(3):
            owner.advance()
        calls.transport = lambda *_: {'stop_reason': 'tool_use', 'content': [
            {'type': 'tool_use', 'id': 'known', 'name': 'ipython', 'input': {'code': 'pass # known partial'}},
            {'type': 'tool_use', 'id': 'unstarted', 'name': 'ipython', 'input': {'code': 'pass # unstarted'}}]}
        execute = control.execute
        def pause_after_result(code):
            result = execute(code)
            calls.request_pause()
            return result
        control.execute = pause_after_result
        with pytest.raises(BudgetPause, match='user_pause_requested'):
            owner.advance()
    finally:
        owner.close()

    resumed_calls = ProviderCalls(calls.directory, calls.limits,
        lambda *_: native('wait', {'event_type': 'NEXT'}, 'new-wait'))
    owner = Execution(owner.directory, resumed_calls, ipython=Python(owner.workspace))
    try:
        owner.advance()  # Retire only the unstarted suffix after the kernel changes.
        assert resumed_calls.summary()['calls'] == 4
        owner.advance()  # A fresh legal decision follows the retirement.
        owner.handle(decision(owner, 'later', None, owner_input={
            'event_id': 'owner-next', 'event_type': 'NEXT',
            'text': 'Continue using the original exact parameter.'}))
        owner.advance()
        assert owner.run_state().decision_count == 6
        assert (owner.workspace / 'early.txt').read_text() == '93847'
        assert len(control.actions) == 4
        wire = resumed_calls.records(role='execution')[-1][1]['wire']
        assert 'EARLY_EXACT_PARAMETER_93847' in json.dumps(wire)
        document = json.loads(wire['messages'][0]['content'][0]['text'])
        assert not document.get('derived_history_handoff')
        unpaired = document['unpaired_execution_history']
        assert unpaired[0]['content']['decision'] == 'decision-000001'
        retired = next(part for part in unpaired if part['content']['decision'] == 'decision-000004')
        assert [result['kind'] for result in retired['content']['results']] == [
            'IPYTHON_EXECUTION_RESULT', 'DECISION_RETIRED']
        # The fallback is an attributed owner record, not a forged native tool reply.
        assert all(block.get('tool_use_id') != 'unstarted'
                   for message in wire['messages'] if isinstance(message['content'], list)
                   for block in message['content'])
    finally:
        owner.close()


def test_correction_capacity_pause_preserves_known_response_on_restart(tmp_path):
    from Execution.execution import ModelRequest
    from Execution.model import ExecutionHistory, ExecutionModel, correction_wire, execution_goal
    from Nervous.provider import BudgetPause, ProviderCalls
    from Nervous.storage import canonical
    task = {'business_goal': 'Retain the actual evidence.', 'execution_protocol': 'Use the supplied tools.'}
    goal = execution_goal(task)
    request = ModelRequest(canonical({'state': {'execution_id': 'run-capacity', 'decision_count': 0},
        'goal': {'text': goal, 'original_chars': len(goal), 'truncated': False}}),
        ExecutionModel.tool_contracts, ())
    projection = {'mode': 'mask', 'version': 'bounded-history', 'rounds': [],
        'owner_inputs': [{'source_ref': f'owner-{i}', 'text': 'x' * 2000} for i in range(64)],
        'received_guidance': [], 'guidance_scope': 'Current owner statements.'}
    calls = ProviderCalls(tmp_path / 'calls', {'calls': 2, 'output_tokens': 16384, 'request_bytes': 400000},
        lambda *_: {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'a' * 22000}]})
    history = ExecutionHistory(calls)
    attempts = []
    def send(wire, **kwargs):
        attempts.append(wire)
        previous, metadata = history.attempt(wire, **kwargs)
        return previous['response'] if previous else calls.call('execution', wire, metadata=metadata)
    model = ExecutionModel(send, history=history, owner_task=task,
                           execution_context=lambda *_: projection)
    with pytest.raises(BudgetPause, match='execution_working_context_capacity'):
        model.decide(request)
    assert len(attempts) == 1
    saved = calls.records()[0][1]
    assert len(json.dumps(saved['wire'], ensure_ascii=False).encode('utf-8')) + 12000 <= 150000
    assert len(json.dumps(correction_wire(saved['wire'], saved['response']),
                          ensure_ascii=False).encode('utf-8')) > 150000
    assert calls.summary()['calls'] == 1
    before = (tmp_path / 'calls' / '0001.json').read_bytes()
    reopened = ProviderCalls(calls.directory, calls.limits, lambda *_: pytest.fail('known response resampled'))
    def no_transport(*_, **__):
        pytest.fail('Over-capacity frozen correction reached dispatch')
    restored = ExecutionModel(no_transport, history=ExecutionHistory(reopened), owner_task=task,
        execution_context=lambda *_: pytest.fail('Recovery replaced the original working context'))
    with pytest.raises(BudgetPause, match='execution_working_context_capacity'):
        restored.decide(request)
    assert reopened.summary()['calls'] == 1
    assert (tmp_path / 'calls' / '0001.json').read_bytes() == before
