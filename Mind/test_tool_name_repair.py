"""Wrong native names may be corrected by the model, never rewritten by the host."""
import copy
import json

import pytest

from Mind.event_loop import CognitiveModel
from Mind.organ import MindOrgan, MindResultEvent
from Mind.test_event_loop import input_value, reply, step
from Mind.test_protocol_recovery import traces
from Mind.trace import ACTIVITY_NATIVE_PROTOCOL_VERSION, TOOL_NAME_REPAIR_NATIVE_PROTOCOL_VERSION, MindTrace


def model(transport, protocol=TOOL_NAME_REPAIR_NATIVE_PROTOCOL_VERSION, *, contract='cognitive-chain-v62'):
    adapter = CognitiveModel(transport, contract=contract)
    adapter.native_protocol_version = protocol
    return adapter


def wrong_name():
    response = reply('next', {'type': 'capability_request', 'capability': 'read_evidence', 'refs': ['source']})
    response['content'][:0] = [
        {'type': 'thinking', 'thinking': 'Opaque original provider thinking.', 'signature': 'retained-signature'},
        {'type': 'text', 'text': 'I will inspect the available source.'}]
    return response


@pytest.mark.parametrize('contract', ['cognitive-chain-v62', 'cognitive-chain-v64'])
def test_wrong_name_corrects_to_read_then_commits_after_restart(tmp_path, contract):
    original = wrong_name()
    read = step({'type': 'capability_request', 'capability': 'read_evidence', 'refs': ['source']})
    final = step({'type': 'directive', 'text': 'Use the sourced scope when reconsidering the result.'}, [
        {'kind': 'belief', 'id': 'new:scope', 'claim': 'Only settled entries count.',
         'status': 'supported', 'basis': [{'ref': 'source'}]}])
    replies = iter([original, read, final]); wires = []

    def transport(wire):
        wires.append(wire)
        return copy.deepcopy(next(replies))

    options = dict(directory=tmp_path, available_capabilities=('read_evidence',))
    with MindOrgan(model=model(transport, contract=contract), **options) as mind:
        waiting = mind.activate(input_value())
        assert waiting.status == 'waiting'
        assert waiting.request.payload['capability'] == 'read_evidence'
        assert mind.inspect().revision == 0 and not mind.inspect().items
        assert len(wires) == 2
    feedback = wires[1]['messages'][-1]['content'][0]
    assert feedback['tool_use_id'] == original['content'][-1]['id'] and feedback['is_error']
    assert wires[1]['messages'][-2]['content'] == original['content']
    body = json.loads(feedback['content'])
    assert body['submission_status'] == 'rejected_before_commit'
    assert body['field_errors'][0]['validator'] == 'native_tool_name'
    assert 'cognitive_step' in body['field_errors'][0]['message']
    before = next(tmp_path.glob('*.native.jsonl')).read_bytes()
    with MindOrgan(model=model(transport, contract=contract), **options) as mind:
        assert mind.activate(input_value()).request == waiting.request
        assert len(wires) == 2
        result = mind.accept_result(MindResultEvent(waiting.request.request_ref,
            {'capability': 'read_evidence', 'origin': 'execution', 'text': json.dumps({'read_result': 'sources-v1',
                'sources': [{'ref': 'source', 'text': 'Only settled entries count.', 'origin': 'execution'}]})}))
        assert result.status == 'accepted' and mind.inspect().revision == 1
        assert result.output['type'] == 'directive'
        assert len(mind.inspect().items) == 1
    assert next(tmp_path.glob('*.native.jsonl')).read_bytes().startswith(before)
    assert [message['content'] for message in wires[2]['messages'] if message['role'] == 'assistant'] == [original['content'], read['content']]
    trace, records = traces(tmp_path)
    assert records[1]['response'] == original and not records[1]['accepted'] and records[1]['recoverable']
    assert sum(record['kind'] == 'call' for record in records) == 3
    assert not any(event.event_type == 'CAPABILITY_REQUESTED' for event in trace.events[:2])
    with MindOrgan(model=model(lambda _: pytest.fail('Committed response was resampled.'), contract=contract), **options) as mind:
        assert mind.activate(input_value()).status == 'duplicate'
        assert mind.inspect().revision == 1


def test_wrong_name_corrections_share_six_calls_across_restarts(tmp_path, monkeypatch):
    response = wrong_name(); wires = []

    class Cut(BaseException):
        pass

    def transport(wire):
        wires.append(wire)
        assert [m['content'] for m in wire['messages'] if m['role'] == 'assistant'] == [response['content']] * (len(wires) - 1)
        return copy.deepcopy(response)

    original_append = MindTrace.append_native

    def interrupted_result(trace, **payload):
        original_append(trace, **payload)
        if payload['kind'] == 'result':
            raise Cut()

    for spent in range(1, 7):
        with MindOrgan(directory=tmp_path, model=model(transport)) as mind:
            with monkeypatch.context() as patch:
                patch.setattr(MindTrace, 'append_native', interrupted_result)
                with pytest.raises(Cut):
                    mind.activate(input_value())
            assert mind.inspect().revision == 0 and len(wires) == spent
        trace, records = traces(tmp_path)
        assert len(records) == 2 * spent
        assert sum(record.get('repair', False) for record in records) == spent - 1
    assert 'last Mind call' in wires[-1]['system']
    assert all(option['properties']['type']['enum'] != ['capability_request']
               for option in wires[-1]['tools'][0]['input_schema']['properties']['next']['oneOf'])
    before = next(tmp_path.glob('*.native.jsonl')).read_bytes()
    with MindOrgan(directory=tmp_path, model=model(lambda _: pytest.fail('Restart replenished the six-call budget.'))) as mind:
        result = mind.activate(input_value())
        assert result.status == 'failed' and result.output is None
        assert mind.inspect().revision == 0 and not mind.inspect().items
        assert mind.activate(input_value()).status == 'failed'
    assert next(tmp_path.glob('*.native.jsonl')).read_bytes() == before
    assert trace.events[0].payload['native_protocol'] == TOOL_NAME_REPAIR_NATIVE_PROTOCOL_VERSION


def test_historical_protocol_keeps_wrong_name_unrecoverable(tmp_path):
    wires = []
    with MindOrgan(directory=tmp_path, model=model(lambda wire: wires.append(wire) or wrong_name(),
                   protocol=ACTIVITY_NATIVE_PROTOCOL_VERSION)) as mind:
        result = mind.activate(input_value())
        assert result.status == 'failed' and result.output is None and len(wires) == 1
    trace, records = traces(tmp_path)
    assert not records[-1]['recoverable']
    assert records[-1]['errors'][0]['validator'] == 'native_envelope'
    before = {path.name: path.read_bytes() for path in tmp_path.glob('activation-*.jsonl')}
    with MindOrgan(directory=tmp_path, model=model(lambda _: pytest.fail('Old failure was reinterpreted.'))) as mind:
        assert mind.activate(input_value()).status == 'failed'
        assert mind.inspect().revision == 0
    assert {path.name: path.read_bytes() for path in tmp_path.glob('activation-*.jsonl')} == before
    assert trace.events[0].payload['native_protocol'] == ACTIVITY_NATIVE_PROTOCOL_VERSION


@pytest.mark.parametrize('damage', ['empty_name', 'missing_name', 'empty_id', 'missing_id',
                                  'missing_input', 'non_object_input', 'multiple_tools', 'truncated', 'text_only'])
def test_incomplete_or_ambiguous_wrong_tool_response_remains_failure(tmp_path, damage):
    response = wrong_name(); block = response['content'][-1]
    if damage.startswith('empty_'):
        block[damage.removeprefix('empty_')] = ''
    elif damage.startswith('missing_'):
        del block[damage.removeprefix('missing_')]
    elif damage == 'non_object_input':
        block['input'] = []
    elif damage == 'multiple_tools':
        response['content'].append({**block, 'id': 'second'})
    elif damage == 'truncated':
        response['stop_reason'] = 'max_tokens'
    else:
        response['content'].pop()
    wires = []
    with MindOrgan(directory=tmp_path, model=model(lambda wire: wires.append(wire) or response)) as mind:
        result = mind.activate(input_value())
        assert result.status == 'failed' and result.output is None
        assert mind.inspect().revision == 0 and len(wires) == 1
    _, records = traces(tmp_path)
    assert records[-1]['response'] == response and not records[-1]['recoverable']
    with MindOrgan(directory=tmp_path, model=model(lambda _: pytest.fail('Unsafe envelope was retried.'))) as mind:
        assert mind.activate(input_value()).status == 'failed'


@pytest.mark.parametrize('interruption', ['reserved', 'unknown_transport', 'failed_transport'])
def test_tool_name_protocol_never_replays_an_unknown_or_failed_transport(tmp_path, monkeypatch, interruption):
    calls = []

    class Cut(BaseException):
        pass

    def transport(wire):
        calls.append(wire)
        if interruption == 'failed_transport':
            raise ConnectionError('Transport ended without a response.')
        raise Cut()

    original_append = MindTrace.append_native

    def interrupted_call(trace, **payload):
        original_append(trace, **payload)
        if payload['kind'] == 'call':
            raise Cut()

    with MindOrgan(directory=tmp_path, model=model(transport)) as mind:
        with monkeypatch.context() as patch:
            if interruption == 'reserved':
                patch.setattr(MindTrace, 'append_native', interrupted_call)
            if interruption == 'failed_transport':
                assert mind.activate(input_value()).status == 'failed'
            else:
                with pytest.raises(Cut):
                    mind.activate(input_value())
    assert len(calls) == (interruption != 'reserved')
    before = next(tmp_path.glob('*.native.jsonl')).read_bytes()
    with MindOrgan(directory=tmp_path, model=model(lambda _: pytest.fail('Uncertain call was resampled.'))) as mind:
        result = mind.activate(input_value())
        assert result.status == 'failed' and result.output is None
        assert mind.inspect().revision == 0
    assert next(tmp_path.glob('*.native.jsonl')).read_bytes() == before
