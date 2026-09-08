"""Declared native consultations retain raw history and use the existing Mind owners."""
import copy
import json

import pytest

from Mind.chain import ChainMind
from Mind.event_loop import CognitiveModel
from Mind.organ import MindOrgan, MindResultEvent
from Mind.test_event_loop import input_value, reply, step
from Mind.test_protocol_recovery import traces
from Mind.trace import MindTrace, CONSULTATION_NATIVE_PROTOCOL_VERSION, TOOL_NAME_REPAIR_NATIVE_PROTOCOL_VERSION


def native(name, arguments):
    response = reply(name, arguments)
    response['content'].insert(0, {'type': 'thinking', 'thinking': 'Opaque provider content for ' + name,
                                  'signature': 'native-signature'})
    return response


def read_result(request):
    return MindResultEvent(request.request_ref, {'capability': 'read_evidence', 'origin': 'execution',
        'text': json.dumps({'read_result': 'sources-v1', 'sources': [
            {'ref': 'source', 'text': 'Only settled entries count.', 'origin': 'execution'}]})})


def belief():
    return {'kind': 'belief', 'id': 'new:scope', 'claim': 'Only settled entries count.',
            'status': 'supported', 'basis': [{'ref': 'source'}]}


@pytest.mark.parametrize('received_cut', [False, True])
@pytest.mark.parametrize('reverse_tools', [False, True])
def test_direct_read_analysis_and_final_commit_keep_native_history(tmp_path, monkeypatch, received_cut, reverse_tools):
    answers = [native('read_evidence', {'refs': ['source']}),
        native('analyze_world_model', {'refs': ['source'], 'question': 'Assess the sourced scope.', 'model_ref': ''}),
        step({'type': 'directive', 'text': 'Use the sourced scope when reconsidering the result.'}, [belief()])]
    wires = []

    def transport(wire):
        wires.append(wire)
        return copy.deepcopy(answers[len(wires) - 1])

    def model():
        adapter = ChainMind(transport)
        original = adapter._prepare_call
        def prepare(*args, **kwargs):
            record = original(*args, **kwargs)
            if reverse_tools:
                record['wire']['tools'].reverse()
            return record
        adapter._prepare_call = prepare
        return adapter

    class Cut(BaseException):
        pass

    original_append = MindTrace.append_native
    def interrupted(trace, **payload):
        original_append(trace, **payload)
        if payload['kind'] == 'result' and payload['accepted']:
            raise Cut()

    options = dict(directory=tmp_path, available_capabilities=('read_evidence', 'analyze_world_model'))
    with MindOrgan(model=model(), **options) as mind:
        if received_cut:
            with monkeypatch.context() as patch:
                patch.setattr(MindTrace, 'append_native', interrupted)
                with pytest.raises(Cut):
                    mind.activate(input_value())
        else:
            assert mind.activate(input_value()).status == 'waiting'
        assert mind.inspect().revision == 0 and not mind.inspect().items
    prefix = next(tmp_path.glob('*.native.jsonl')).read_bytes()
    with MindOrgan(model=model(), **options) as mind:
        waiting = mind.activate(input_value())
        assert waiting.status == 'waiting' and len(wires) == 1
        assert waiting.request.payload['capability'] == 'read_evidence'
        analysis = mind.accept_result(read_result(waiting.request))
        assert analysis.status == 'waiting' and len(wires) == 2
        assert analysis.request.payload['capability'] == 'analyze_world_model'
        assert analysis.request.payload['question'] == 'Assess the sourced scope.'
        assert mind.inspect().revision == 0 and not mind.inspect().items
    with MindOrgan(model=model(), **options) as mind:
        assert mind.activate(input_value()).request == analysis.request
        result = mind.accept_result(MindResultEvent(analysis.request.request_ref,
            {'capability': 'analyze_world_model', 'origin': 'computation',
             'text': '{"answer":"The sourced condition applies."}'}))
        assert result.status == 'accepted' and mind.inspect().revision == 1
        assert result.output['type'] == 'directive' and len(mind.inspect().items) == 1
        assert mind.activate(input_value()).status == 'duplicate' and len(wires) == 3
    assert next(tmp_path.glob('*.native.jsonl')).read_bytes().startswith(prefix)
    assert [message['content'] for message in wires[-1]['messages'] if message['role'] == 'assistant'] == [answer['content'] for answer in answers[:2]]
    assert [wire['messages'][-1]['content'][0]['tool_use_id'] for wire in wires[1:]] == ['response', 'response']
    assert 'The sourced condition applies.' in json.dumps(wires[-1])
    trace, records = traces(tmp_path)
    assert trace.events[0].payload['native_protocol'] == CONSULTATION_NATIVE_PROTOCOL_VERSION
    assert [record['response'] for record in records if record['kind'] == 'result'] == answers
    logical = [json.loads(event.payload['text']) for event in trace.events if event.event_type == 'MODEL_OUTPUT_RECORDED']
    assert [value['next']['capability'] for value in logical[:2]] == ['read_evidence', 'analyze_world_model']
    assert all(value['updates'] == [] for value in logical[:2])
    assert all('updates' not in answer['content'][-1]['input'] for answer in answers[:2])


@pytest.mark.parametrize('bad', ['unknown_name', 'invalid_refs', 'legacy_draft', 'read_with_updates', 'undeclared_analysis'])
def test_only_declared_consultation_arguments_are_accepted(tmp_path, bad):
    rejected = {
        'unknown_name': native('next', {'type': 'capability_request', 'capability': 'read_evidence', 'refs': ['source']}),
        'invalid_refs': native('read_evidence', {'refs': []}),
        'legacy_draft': step({'type': 'capability_request', 'capability': 'read_evidence', 'refs': ['source']}, [belief()]),
        'read_with_updates': native('read_evidence', {'refs': ['source'], 'updates': [belief()]}),
        'undeclared_analysis': native('analyze_world_model', {'refs': ['source'], 'question': 'Assess scope.', 'model_ref': ''}),
    }[bad]
    wires = []
    def transport(wire):
        wires.append(wire)
        return copy.deepcopy(rejected) if len(wires) == 1 else step({'type': 'no_change'})
    with MindOrgan(directory=tmp_path, model=ChainMind(transport), available_capabilities=('read_evidence',)) as mind:
        result = mind.activate(input_value())
        assert result.status == 'accepted' and result.output['type'] == 'no_change'
        assert mind.inspect().revision == 1 and not mind.inspect().items and len(wires) == 2
    trace, records = traces(tmp_path)
    assert records[1]['response'] == rejected and not records[1]['accepted'] and records[1]['recoverable']
    assert not any(event.event_type == 'CAPABILITY_REQUESTED' for event in trace.events)
    assert wires[1]['messages'][-2]['content'] == rejected['content']
    feedback = wires[1]['messages'][-1]['content'][0]
    assert feedback['is_error'] and feedback['tool_use_id'] == 'response'
    assert json.loads(feedback['content'])['submission_status'] == 'rejected_before_commit'


@pytest.mark.parametrize('last_consults', [False, True])
def test_six_shared_calls_leave_only_final_commit_after_five_reads(tmp_path, last_consults):
    wires = []
    def transport(wire):
        wires.append(wire)
        if len(wires) == 6:
            assert [tool['name'] for tool in wire['tools']] == ['cognitive_step']
            assert 'last Mind call' in wire['system']
            if not last_consults:
                return step({'type': 'no_change'})
        return native('read_evidence', {'refs': ['source']})
    options = dict(directory=tmp_path, available_capabilities=('read_evidence',))
    with MindOrgan(model=ChainMind(transport), **options) as mind:
        receipt = mind.activate(input_value())
    for spent in range(1, 6):
        with MindOrgan(model=ChainMind(transport), **options) as mind:
            assert mind.activate(input_value()).request == receipt.request
            assert len(wires) == spent and mind.inspect().revision == 0
            receipt = mind.accept_result(read_result(receipt.request))
            assert receipt.status == ('waiting' if spent < 5 else 'failed' if last_consults else 'accepted')
    assert len(wires) == 6
    trace, records = traces(tmp_path)
    assert sum(event.event_type == 'CAPABILITY_REQUESTED' for event in trace.events) == 5
    assert len(records) == 12
    before = next(tmp_path.glob('*.native.jsonl')).read_bytes()
    with MindOrgan(model=ChainMind(lambda _: pytest.fail('A seventh call was dispatched.')), **options) as mind:
        assert mind.activate(input_value()).status == ('failed' if last_consults else 'duplicate')
        assert mind.inspect().revision == int(not last_consults)
    assert next(tmp_path.glob('*.native.jsonl')).read_bytes() == before


def test_final_grounding_preview_still_rejects_duplicate_state_before_commit(tmp_path):
    wires = []
    rejected = step({'type': 'no_change'}, [belief(), belief()])
    def transport(wire):
        wires.append(wire)
        if len(wires) == 1:
            return native('read_evidence', {'refs': ['source']})
        return rejected if len(wires) == 2 else step({'type': 'no_change'}, [belief()])
    with MindOrgan(directory=tmp_path, model=ChainMind(transport), available_capabilities=('read_evidence',)) as mind:
        waiting = mind.activate(input_value())
        assert mind.inspect().revision == 0 and not mind.inspect().items
        receipt = mind.accept_result(read_result(waiting.request))
        assert receipt.status == 'accepted' and mind.inspect().revision == 1
        assert len(mind.inspect().items) == 1 and len(wires) == 3
    feedback = json.loads(wires[-1]['messages'][-1]['content'][0]['content'])
    assert feedback['field_errors'][0]['validator'] == 'effective_state'
    assert wires[-1]['messages'][-2]['content'] == rejected['content']


def test_explicitly_declared_inspection_uses_existing_read_only_capability(tmp_path):
    wires = []
    def transport(wire):
        wires.append(wire)
        assert {tool['name'] for tool in wire['tools']} == {'cognitive_step', 'inspect_execution'}
        return native('inspect_execution', {}) if len(wires) == 1 else step({'type': 'no_change'})
    with MindOrgan(directory=tmp_path, model=ChainMind(transport), available_capabilities=('inspect_execution',)) as mind:
        receipt = mind.activate(input_value())
        assert receipt.status == 'waiting' and dict(receipt.request.payload) == {'capability': 'inspect_execution'}
        receipt = mind.accept_result(MindResultEvent(receipt.request.request_ref,
            {'capability': 'inspect_execution', 'goal': 'Produce a valid report.', 'status': 'running',
             'recent_outcome': 'The scoped run is still pending.', 'failure': None}))
        assert receipt.status == 'accepted' and mind.inspect().revision == 1 and len(wires) == 2


@pytest.mark.parametrize('damage', ['multiple', 'truncated', 'missing_id', 'invalid_name'])
def test_unsafe_consultation_envelopes_remain_failed_without_requests(tmp_path, damage):
    response = native('read_evidence', {'refs': ['source']})
    if damage == 'multiple':
        response['content'].append({**response['content'][-1], 'id': 'another'})
    elif damage == 'truncated':
        response['stop_reason'] = 'max_tokens'
    elif damage == 'missing_id':
        del response['content'][-1]['id']
    else:
        response['content'][-1]['name'] = []
    calls = []
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda wire: calls.append(wire) or response),
                   available_capabilities=('read_evidence',)) as mind:
        receipt = mind.activate(input_value())
        assert receipt.status == 'failed' and receipt.output is None and mind.inspect().revision == 0
    trace, records = traces(tmp_path)
    assert len(calls) == 1 and records[-1]['response'] == response and not records[-1]['recoverable']
    assert not any(event.event_type == 'CAPABILITY_REQUESTED' for event in trace.events)


def test_v64_does_not_reinterpret_direct_read_as_an_accepted_consultation(tmp_path):
    calls = []
    def transport(wire):
        calls.append(wire)
        return native('read_evidence', {'refs': ['source']}) if len(calls) == 1 else step({'type': 'no_change'})
    adapter = CognitiveModel(transport, contract='cognitive-chain-v64')
    adapter.native_protocol_version = TOOL_NAME_REPAIR_NATIVE_PROTOCOL_VERSION
    with MindOrgan(directory=tmp_path, model=adapter, available_capabilities=('read_evidence',)) as mind:
        assert mind.activate(input_value()).status == 'accepted' and len(calls) == 2
    trace, records = traces(tmp_path)
    assert records[1]['errors'][0]['validator'] == 'native_tool_name' and not records[1]['accepted']
    assert not any(event.event_type == 'CAPABILITY_REQUESTED' for event in trace.events)
    before = next(tmp_path.glob('*.native.jsonl')).read_bytes()
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: pytest.fail('Historical result was replayed as a new tool.'))) as mind:
        assert mind.activate(input_value()).status == 'duplicate'
    assert next(tmp_path.glob('*.native.jsonl')).read_bytes() == before
