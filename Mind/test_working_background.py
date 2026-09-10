"""Derived cross-activity background preserves the current cognitive owner."""
from contextlib import ExitStack
import json

import pytest

from Execution.model import EXECUTION_PROTOCOL
from Execution.runtime import Execution
from Mind.organ import MindOrgan
from Mind.test_core_loop import native, source_records
from Nervous.organ import NervousOrgan
from Nervous.storage import canonical, read_json


def open_organs(directory, workspace, transport, *, mode=None):
    stack = ExitStack()
    try:
        nervous = NervousOrgan(directory / 'nervous', transport=transport,
            limits={'calls': 100, 'output_tokens': 2000000, 'request_bytes': 20000000})
        stack.callback(nervous.close)
        mind = MindOrgan(directory / 'mind', nervous.calls,
            goal='Review the supplied observations without workspace actions.',
            execution_protocol=EXECUTION_PROTOCOL, context_mode=mode)
        stack.callback(mind.close)
        execution = Execution(directory / 'execution', nervous.calls, workspace=workspace)
        stack.callback(execution.close)
        return stack, nervous, mind, execution
    except BaseException:
        stack.close()
        raise


def summary_response(wire):
    payload = json.loads(wire['messages'][0]['content'])
    return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': canonical({
        'summary': 'Earlier judgments are historical. The measurement remains conditional.',
        'source_refs': [*payload['previous_source_refs'], *[s['ref'] for s in payload['segments']]]})}]}


def test_three_compactions_and_restart_keep_updated_cognition_and_quiet_nochange(tmp_path):
    directory, workspace = tmp_path / 'state', tmp_path / 'work'
    workspace.mkdir()
    decisions, summaries, backgrounds = [], [], []

    def transport(role, wire):
        assert role == 'mind'
        if not wire.get('tools'):
            summaries.append(wire)
            body = json.loads(wire['messages'][0]['content'])
            assert 'cognitive_context' not in canonical(body['segments'])
            assert 'derived_history_background' not in canonical(body['segments'])
            assert '"wire"' not in canonical(body['segments'])
            return summary_response(wire)
        decisions.append(wire)
        payload = json.loads(wire['messages'][0]['content'])
        background = payload['derived_history_background']
        backgrounds.append(background)
        assert 'derived_history_background' not in payload['cognition']
        items = payload['cognition']['prior_model_judgments']
        updates = []
        if len(decisions) == 1:
            ref = next(s['ref'] for s in source_records(wire) if 'Observation 1:' in s['text'])
            updates = [{'kind': 'belief', 'id': 'new:measurement', 'claim': 'The initial measurement is recorded.',
                        'status': 'supported', 'basis': [{'ref': ref}]}]
        elif len(decisions) == 14:
            assert items[0]['prior_truth'] is True
            updates = [{'kind': 'belief', 'id': items[0]['id'], 'claim': 'The later measurement is unknown.',
                        'status': 'open', 'basis': []}]
        elif len(decisions) == 22:
            assert items[0]['prior_truth'] is None
            ref = next(s['ref'] for s in source_records(wire) if 'Observation 22:' in s['text'])
            updates = [{'kind': 'belief', 'id': items[0]['id'], 'claim': 'The later measurement is now recorded.',
                        'status': 'supported', 'basis': [{'ref': ref}]}]
        elif len(decisions) > 22:
            assert items[0]['claim'] == 'The later measurement is now recorded.'
            assert items[0]['prior_truth'] is True
        basis = wire['tools'][0]['input_schema']['properties']['updates']['items']['oneOf'][0]['properties']['basis']
        assert not set(background['source_refs']) & set(basis['items']['properties']['ref'].get('enum', []))
        return native('cognitive_step', {'type': 'cognitive_step', 'updates': updates,
                                         'next': {'type': 'no_change'}})

    for start, end in ((1, 14), (14, 21), (21, 26)):
        stack, nervous, mind, execution = open_organs(directory, workspace, transport,
                                                    mode='summary' if start == 1 else None)
        with stack:
            assert mind.state['context_mode'] == 'summary'
            before = nervous.calls.summary()
            nervous.run(mind, execution)
            assert nervous.calls.summary() == before
            for number in range(start, end):
                nervous.submit(f'Observation {number}: record this event and retain uncertainty when warranted.')
                result = nervous.run(mind, execution)
                assert result['mind']['revision'] == number and not result['mind']['unresolved']
            before = nervous.calls.summary()
            nervous.run(mind, execution)
            assert nervous.calls.summary() == before
    assert len(decisions) == 25 and len(summaries) == 3
    saved = read_json(directory / 'mind' / 'background.json')['accepted']
    assert saved['revision'] >= 3
    tail = backgrounds[-1]['recent_activities']
    assert 6 <= len(tail) < 12
    assert len(saved['source_refs']) + len(tail) == 24
    assert not set(saved['source_refs']) & {segment['ref'] for segment in tail}
    assert result['mind']['items'][0]['claim'] == 'The later measurement is now recorded.'


def test_mask_keeps_old_activity_structure_and_reads_original_trace_ranges(tmp_path):
    directory, workspace = tmp_path / 'state', tmp_path / 'work'
    workspace.mkdir()
    wires, selected = [], {}

    def transport(role, wire):
        assert role == 'mind' and wire.get('tools'), 'Mask mode must not call a summarizer.'
        wires.append(wire)
        if len(wires) == 9:
            payload = json.loads(wire['messages'][0]['content'])
            background = payload['derived_history_background']
            assert len(background['masked_activities']) == 2
            assert len(background['recent_activities']) == 6
            first = background['masked_activities'][0]['content']['pieces']
            assert first[0]['content']['payload']['activation']['trigger']
            assert first[1]['content']['masked'] is True
            assert first[-1]['event_type'] == 'ACTIVATION_FINISHED'
            return native('read_evidence', {'refs': [selected['ref']]})
        if len(wires) == 10:
            record = next(s for s in source_records(wire) if s['ref'] == selected['ref'])
            assert record['text'] == selected['text']
            assert record['origin'] == 'computation' and record['kind'] == 'historical_model_judgment'
        return native('cognitive_step', {'type': 'cognitive_step', 'updates': [],
                                         'next': {'type': 'no_change'}})

    stack, nervous, mind, execution = open_organs(directory, workspace, transport, mode='mask')
    with stack:
        for number in range(8):
            nervous.submit(f'Review event {number}.')
            assert nervous.run(mind, execution)['mind']['revision'] == number + 1
        segments = mind.cognition.history_segments()
        piece = segments[0]['content']['pieces'][1]
        original = canonical(piece['content'])
        selected.update(ref=piece['ref'] + ':3:19', text=original[3:22], total_chars=len(original))
        before = {p: p.read_bytes() for p in directory.rglob('*.jsonl')}
        read = mind.source_record(selected['ref'])
        assert read['text'] == selected['text'] and read['truncated']
        assert read['origin'] == 'computation'
        assert before == {p: p.read_bytes() for p in directory.rglob('*.jsonl')}
        for suffix in (':0:6001', ':-1:4', ':1000000:1'):
            with pytest.raises(ValueError, match='invalid_history'):
                mind.source_record(piece['ref'] + suffix)
        nervous.submit('Read the original historical judgment fragment.')
        result = nervous.run(mind, execution)
        assert result['mind']['revision'] == 9
        assert result['mind']['context_mode'] == 'mask'
        assert result['mind']['background']['revision'] == 0
        assert not (directory / 'mind' / 'background.json').exists()
        sent = next(s for s in source_records(wires[-1]) if s['ref'] == selected['ref'])
        assert sent['offset'] == 3 and sent['limit'] == 19
        assert sent['total_chars'] == selected['total_chars'] and sent['truncated'] is True
        receipts = [record['result']['observation'] for record in
                    read_json(directory / 'mind' / 'cognition' / 'cognition.json')['records']
                    if record['kind'] == 'result_received']
        body = json.loads(receipts[-1]['text'])
        assert set(body['sources'][0]) == {'ref', 'text', 'origin'}
        assert body['source_metadata'][selected['ref']] == {
            'source_kind': 'historical_model_judgment', 'label': 'MODEL_OUTPUT_RECORDED',
            'offset': 3, 'limit': 19, 'total_chars': len(original), 'truncated': True}


@pytest.mark.parametrize('mode', [None, 'baseline', 'mask', 'summary'])
def test_mode_is_fixed_and_readonly_status_does_not_create_background(tmp_path, mode):
    from Mind.test_readonly_status import files_under
    directory, workspace = tmp_path / 'state', tmp_path / 'work'
    workspace.mkdir()
    transport = lambda *_: pytest.fail('No event admits no model call.')
    stack, nervous, mind, execution = open_organs(directory, workspace, transport, mode=mode)
    with stack:
        nervous.run(mind, execution)
        before = files_under(directory)
        inspected = MindOrgan.inspect_directory(directory / 'mind')
        assert inspected['context_mode'] == (mode or 'baseline')
        assert inspected['background'] == {'revision': 0, 'covered_until': None,
                                            'covered_segments': 0, 'pending_call': None}
        assert files_under(directory) == before
    stack, nervous, mind, execution = open_organs(directory, workspace, transport)
    with stack:
        assert mind.status()['context_mode'] == (mode or 'baseline')
        for other in {'baseline', 'mask', 'summary'} - {mode or 'baseline'}:
            with pytest.raises(ValueError, match='context_mode_is_fixed_at_start'):
                MindOrgan(directory / 'mind', nervous.calls, context_mode=other)


@pytest.mark.parametrize('cut', ['compaction_received', 'native_received'])
def test_pending_new_activity_reuses_known_summary_and_frozen_native_wire(tmp_path, monkeypatch, cut):
    import working_context
    from Mind import model as mind_model
    from Mind.trace import MindTrace
    directory, workspace = tmp_path / 'state', tmp_path / 'work'
    workspace.mkdir()
    summaries, decisions = [], []

    def transport(role, wire):
        assert role == 'mind'
        if not wire.get('tools'):
            summaries.append(wire)
            return summary_response(wire)
        decisions.append(wire)
        return native('cognitive_step', {'type': 'cognitive_step', 'updates': [],
                                         'next': {'type': 'no_change'}})

    class Crash(BaseException):
        pass

    stack, nervous, mind, execution = open_organs(directory, workspace, transport, mode='summary')
    with stack:
        for number in range(12):
            nervous.submit(f'Original event {number}.')
            assert nervous.run(mind, execution)['mind']['revision'] == number + 1
        assert not summaries
        nervous.submit('The next event must keep its original pending request.')
        with monkeypatch.context() as patch:
            if cut == 'compaction_received':
                write = working_context.write_json
                def fail_commit(path, value):
                    if path.name == 'background.json' and value.get('accepted') and value['pending'] is None:
                        raise Crash()
                    return write(path, value)
                patch.setattr(working_context, 'write_json', fail_commit)
            else:
                append = MindTrace.append_native
                def fail_native_result(trace, **value):
                    if value['kind'] == 'result':
                        raise Crash()
                    return append(trace, **value)
                patch.setattr(MindTrace, 'append_native', fail_native_result)
            with pytest.raises(Crash):
                nervous.run(mind, execution)
        cost = nervous.calls.summary()
        background_bytes = (directory / 'mind' / 'background.json').read_bytes()
        starts = [r for r in read_json(directory / 'mind' / 'cognition' / 'cognition.json')['records']
                  if r['kind'] == 'started']
        assert len(starts) == (12 if cut == 'compaction_received' else 13)
        pending = mind.status()['background']['pending_call']
        assert bool(pending) == (cut == 'compaction_received')
        if cut == 'native_received':
            native_path = directory / 'mind' / 'cognition' / (starts[-1]['activation_id'] + '.native.jsonl')
            original = json.loads(native_path.read_text())['wire']
            assert original == decisions[-1]
    monkeypatch.setattr(working_context, 'SYSTEM', 'Changed only after the original summary was received.')
    monkeypatch.setattr(mind_model, 'MIND_PROMPT', mind_model.MIND_PROMPT + '\nA later prompt revision.')
    stack, nervous, mind, execution = open_organs(directory, workspace, transport)
    with stack:
        result = nervous.run(mind, execution)
        assert result['mind']['revision'] == 13 and not result['mind']['unresolved']
        assert len(summaries) == 1 and len(decisions) == 13
        assert result['mind']['background']['pending_call'] is None
        if cut == 'native_received':
            assert nervous.calls.summary() == cost
            assert (directory / 'mind' / 'background.json').read_bytes() == background_bytes
            assert json.loads(native_path.read_text().splitlines()[0])['wire'] == original
        else:
            assert nervous.calls.summary()['calls'] == cost['calls'] + 1
        before = nervous.calls.summary()
        nervous.run(mind, execution)
        assert nervous.calls.summary() == before


def test_summary_and_unread_history_are_not_basis_sources(tmp_path):
    from Mind.activity import start_activity
    from Mind.contracts import ActivationInput, NoChange
    from Mind.model import MindModel, citation_sources
    from Mind.test_model import context, response
    from Mind.trace import MindTrace, project_model_request
    background = {'mode': 'summary', 'summary': 'The old model guessed that the outcome was certain.',
                  'source_refs': ['activity:old'], 'recent_activities': [],
                  'catalogue': [{'ref': 'history:old:1', 'chars': 30}]}
    calls = []
    def transport(wire):
        calls.append(wire)
        if len(calls) == 1:
            return response('cognitive_step', {'type': 'cognitive_step', 'updates': [
                {'kind': 'belief', 'id': 'new:bad', 'claim': 'The outcome is certain.',
                 'status': 'supported', 'basis': [{'ref': 'activity:old'}]}], 'next': {'type': 'no_change'}})
        feedback = json.loads(wire['messages'][-1]['content'][0]['content'])
        assert feedback['field_errors']
        return response('cognitive_step', {'type': 'cognitive_step', 'updates': [],
                                           'next': {'type': 'no_change'}})
    trace = MindTrace.create(tmp_path / 'activity.jsonl', activation_id='activity-1')
    result = start_activity(ActivationInput('Review new evidence.', 'Keep uncertainty.', None),
        model=MindModel(transport, readable_sources=lambda: ()), trace=trace,
        cognitive_context={**context(), 'derived_history_background': background})
    assert isinstance(result, NoChange) and len(calls) == 2
    assert citation_sources(project_model_request(trace.events[:1]).user_message) == {
        'rule': 'The current source is incomplete.'}
    assert [r['accepted'] for r in trace.native_records() if r['kind'] == 'result'] == [False, True]


def test_whole_wire_pressure_compacts_once_then_pauses_without_new_cognition(tmp_path, monkeypatch):
    from Nervous.provider import REQUEST_LIMITS
    directory, workspace = tmp_path / 'state', tmp_path / 'work'
    workspace.mkdir()
    decisions, summaries = [], []
    def transport(role, wire):
        assert role == 'mind'
        if not wire.get('tools'):
            summaries.append(wire)
            return summary_response(wire)
        decisions.append(wire)
        return native('cognitive_step', {'type': 'cognitive_step', 'updates': [],
                                         'next': {'type': 'no_change'}})
    stack, nervous, mind, execution = open_organs(directory, workspace, transport, mode='summary')
    with stack:
        for number in range(7):
            nervous.submit(f'Observation {number}: preserve the original event.')
            assert nervous.run(mind, execution)['mind']['revision'] == number + 1
        assert not summaries
        # Admit the known six-activity wire plus a small new event, but not an
        # additional whole raw activity. The real provider byte check decides.
        previous_bytes = len(json.dumps(decisions[-1], ensure_ascii=False).encode('utf-8'))
        monkeypatch.setitem(REQUEST_LIMITS, 'mind', previous_bytes + 2000)
        nervous.submit('The eighth observation has arrived.')
        result = nervous.run(mind, execution)
        assert result['mind']['revision'] == 8 and len(summaries) == 1
        assert result['mind']['background']['covered_segments'] == 6
        assert len(json.loads(decisions[-1]['messages'][0]['content'])[
            'derived_history_background']['recent_activities']) == 1
        before = nervous.calls.summary()
        cognition = (directory / 'mind' / 'cognition' / 'cognition.json').read_bytes()
        monkeypatch.setitem(REQUEST_LIMITS, 'mind', 200)
        nervous.submit('The next activity must remain pending at the capacity boundary.')
        result = nervous.run(mind, execution)
        assert result['stop_reason'] == 'working_context_capacity_exhausted'
        assert result['mind']['revision'] == 8 and result['mind']['active']
        assert nervous.calls.summary() == before and len(summaries) == 1
        assert (directory / 'mind' / 'cognition' / 'cognition.json').read_bytes() == cognition


def test_historical_role_projection_keeps_business_goal_without_execution_protocol(tmp_path):
    from Mind.cognition import MindInput
    from Mind.contracts import ExecutionObservation
    from Mind.task_view import execution_goal
    from Nervous.provider import ProviderCalls
    goal = 'Create report.txt containing the requested business value READY.'
    protocol = 'Internal protocol: use ClaimComplete and emit marker .internal-complete-flag.'
    task = {'business_goal': goal, 'execution_protocol': protocol}
    wires = []
    def transport(role, wire):
        wires.append(wire)
        return native('cognitive_step', {'type': 'cognitive_step', 'updates': [],
                                         'next': {'type': 'no_change'}})
    calls = ProviderCalls(tmp_path / 'calls',
        {'calls': 4, 'output_tokens': 100000, 'request_bytes': 1000000}, transport)
    mind = MindOrgan(tmp_path / 'mind', calls, goal=goal, execution_protocol=protocol, context_mode='summary')
    try:
        for number in (1, 2):
            assert mind.cognition.activate(MindInput(f'event-{number}', 'Review the actual observation.',
                'owner-goal', 1, execution_goal(task), 'execution-1', 'running',
                execution_observation=ExecutionObservation(execution_goal(task), 'running',
                                                           'The report has not been observed.', None),
                owner_task=task)).status == 'accepted'
        for wire in wires:
            assert 'ClaimComplete' not in canonical(wire)
            assert '.internal-complete-flag' not in canonical(wire)
            assert json.loads(wire['messages'][0]['content'])['goal']['text'] == goal
        raw = {path: path.read_bytes() for path in (tmp_path / 'mind' / 'cognition').glob('*.jsonl')}
        assert any(protocol in body.decode('utf-8') for body in raw.values())
        segment = mind.cognition.history_segments()[0]
        for piece in segment['content']['pieces'][:2]:
            read = mind.source_record(piece['ref'])
            assert goal in read['text'] and 'ClaimComplete' not in read['text']
            assert '.internal-complete-flag' not in read['text']
        assert raw == {path: path.read_bytes() for path in raw}
    finally:
        mind.close()


def test_native_continuation_uses_only_exact_receipt_metadata_without_source_callback(tmp_path):
    from Mind.activity import start_activity, continue_activity
    from Mind.contracts import ActivationInput, CapabilityRequest, NoChange
    from Mind.model import MindModel
    from Mind.test_model import context, response
    from Mind.trace import MindTrace
    ref = 'history:activation-' + 'a' * 24 + ':1:3:19'
    original = 'This saved text records a prior model judgment, not a new observation.'
    fragment = original[3:22]
    wires = []
    def transport(wire):
        wires.append(wire)
        return response('read_evidence', {'refs': [ref]}) if len(wires) == 1 else response(
            'cognitive_step', {'type': 'cognitive_step', 'updates': [], 'next': {'type': 'no_change'}})
    trace = MindTrace.create(tmp_path / 'activity.jsonl', activation_id='activity-1')
    model = MindModel(transport, readable_sources=lambda: [ref])
    assert isinstance(start_activity(ActivationInput('Read the relevant history.', 'Keep its provenance.', None),
        model=model, trace=trace, cognitive_context=context(), capabilities=('read_evidence',)), CapabilityRequest)
    trace = MindTrace.reopen_for_result(tmp_path / 'activity.jsonl')
    result = continue_activity(trace=trace, model=model, observation={
        'capability': 'read_evidence', 'origin': 'computation', 'text': canonical({
            'read_result': 'sources-v1', 'sources': [{'ref': ref, 'text': fragment, 'origin': 'computation'}],
            'source_metadata': {
                ref: {'offset': 3, 'limit': 19, 'total_chars': len(original), 'truncated': True,
                      'source_kind': 'historical_model_judgment', 'label': 'MODEL_OUTPUT_RECORDED',
                      'origin': 'execution', 'text': 'Must not replace the actual text.', 'private_path': 'not-exposed'},
                'rule': {'source_kind': 'historical_model_judgment', 'label': 'Must not relabel another source.'}}})})
    assert isinstance(result, NoChange) and len(wires) == 2
    record = next(source for source in source_records(wires[-1]) if source['ref'] == ref)
    assert record['text'] == fragment and record['origin'] == 'computation'
    assert record['offset'] == 3 and record['limit'] == 19
    assert record['total_chars'] == len(original) and record['truncated'] is True
    assert record['kind'] == 'historical_model_judgment' and 'private_path' not in record

    assert 'not a complete input' in record['scope']
    assert 'Must not relabel another source.' not in canonical(wires[-1])
    assert wires[-1]['messages'][-2]['content'][0]['type'] == 'thinking'


def test_capacity_can_compact_two_ended_activities_without_changing_history(tmp_path, monkeypatch):
    from Nervous.provider import REQUEST_LIMITS
    directory, workspace = tmp_path / 'state', tmp_path / 'work'
    workspace.mkdir()
    decisions, summaries = [], []
    def transport(role, wire):
        assert role == 'mind'
        if not wire.get('tools'):
            summaries.append(wire)
            return summary_response(wire)
        decisions.append(wire)
        return native('cognitive_step', {'type': 'cognitive_step', 'updates': [],
                                         'next': {'type': 'no_change'}})
    stack, nervous, mind, execution = open_organs(directory, workspace, transport, mode='summary')
    with stack:
        for number in range(2):
            nervous.submit(f'Observation {number}: ' + ('scoped observation; ' * 100))
            assert nervous.run(mind, execution)['mind']['revision'] == number + 1
        assert not summaries
        original = mind.cognition.history_segments()
        assert len(original) == 2
        prior_traces = {path: path.read_bytes() for path in (directory / 'mind' / 'cognition').glob('*.jsonl')}
        previous_bytes = len(json.dumps(decisions[-1], ensure_ascii=False).encode('utf-8'))
        monkeypatch.setitem(REQUEST_LIMITS, 'mind', previous_bytes + 2000)
        nervous.submit('The third event arrived; retain the earlier observation scope.')
        result = nervous.run(mind, execution)
        assert result['mind']['revision'] == 3 and not result['mind']['unresolved']
        assert len(summaries) == 1 and len(decisions) == 3
        background = json.loads(decisions[-1]['messages'][0]['content'])['derived_history_background']
        assert background['source_refs'] == [original[0]['ref']]
        assert background['recent_activities'] == original[1:]
        assert mind.cognition.history_segments()[:2] == original
        assert all(path.read_bytes() == before for path, before in prior_traces.items())
