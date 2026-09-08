"""D4 synthetic protocol failures, durable credit and atomic cognitive commits."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from Mind.event_loop import CognitiveModel, RECOVERY_CONTRACT_VERSION, recovery_schema
from Mind.organ import MindOrgan, MindResultEvent
from Mind.test_event_loop import input_value, reply, step
from Mind.trace import MindTrace, TraceError, replay_activation


def model(transport):
    return CognitiveModel(transport, contract=RECOVERY_CONTRACT_VERSION)


def grounded():
    return {'kind': 'belief', 'id': 'new:scope', 'claim': 'Only settled entries count.',
            'status': 'supported', 'discriminator': 'The declared scope.',
            'basis': [{'ref': 'source', 'quote': 'Only settled entries count.'}]}


def traces(directory):
    path, = [p for p in Path(directory).glob('activation-*.jsonl') if '.native.' not in p.name]
    trace = MindTrace.reopen(path)
    return trace, trace.native_records()


@pytest.mark.parametrize('corrected', [True, False])
def test_directive_length_feedback_counts_characters_without_echoing_text(tmp_path, corrected):
    direction = '界' * 1008  # JSON Schema length counts characters, not UTF-8 bytes.
    wires = []
    def transport(wire):
        wires.append(wire)
        if len(wires) == 2 and corrected:
            return step({'type': 'no_change'})  # The model retains its substantive choice.
        return step({'type': 'directive', 'text': direction})
    adapter = CognitiveModel(transport, contract='cognitive-chain-v20', thinking=False)
    with MindOrgan(directory=tmp_path, model=adapter) as mind:
        result = mind.activate(input_value())
        assert result.status == ('accepted' if corrected else 'failed')
        assert mind.inspect().revision == int(corrected)
    trace, records = traces(tmp_path)
    assert len(wires) == 2 and len(records) == 4
    feedback = json.loads(wires[1]['messages'][-1]['content'][0]['content'])
    assert feedback['field_errors'] == [{'path': ['next', 'text'], 'validator': 'maxLength',
        'message': 'String has 1008 characters; maximum allowed is 1000.'}]
    assert direction not in wires[1]['messages'][-1]['content'][0]['content']
    assert wires[1]['messages'][-2]['content'] == step({'type': 'directive', 'text': direction})['content']
    assert sum(bool(record.get('repair')) for record in records) == 1
    assert records[1]['response'] == step({'type': 'directive', 'text': direction})
    before = {path.name: path.read_bytes() for path in tmp_path.glob('activation-*.jsonl')}
    with MindOrgan(directory=tmp_path, model=CognitiveModel(lambda _: pytest.fail('terminal replay called provider'),
                   contract='cognitive-chain-v20', thinking=False)) as mind:
        replay = mind.activate(input_value())
        assert replay.status == ('duplicate' if corrected else 'failed')
    assert {path.name: path.read_bytes() for path in tmp_path.glob('activation-*.jsonl')} == before


@pytest.mark.parametrize('continued', [False, True])
@pytest.mark.parametrize('completed', [False, True])
def test_activity_budget_label_must_match_persisted_native_protocol(tmp_path, continued, completed):
    from Mind.organ import CONTINUED_EVENT_BUDGET_VERSION, INTEGRATION_EVENT_BUDGET_VERSION, _digest
    from Mind.trace import CONTINUED_NATIVE_PROTOCOL_VERSION
    adapter = CognitiveModel(lambda wire: step({'type': 'no_change'} if completed else
        {'type': 'capability_request', 'capability': 'inspect_execution'}), contract='cognitive-chain-v20')
    if continued:
        adapter.native_protocol_version = CONTINUED_NATIVE_PROTOCOL_VERSION
    with MindOrgan(directory=tmp_path, model=adapter, available_capabilities=('inspect_execution',)) as mind:
        assert mind.activate(input_value()).status == ('accepted' if completed else 'waiting')
    path = tmp_path / 'cognition.json'
    document = json.loads(path.read_text(encoding='utf-8'))
    document['records'][0]['budget_version'] = (INTEGRATION_EVENT_BUDGET_VERSION if continued
                                               else CONTINUED_EVENT_BUDGET_VERSION)
    document['sha256'] = _digest(document['records'])
    path.write_text(json.dumps(document), encoding='utf-8')
    with MindOrgan(directory=tmp_path, model=adapter) as mind:
        with pytest.raises(ValueError, match='budget_protocol_conflict'):
            mind.inspect()


@pytest.mark.parametrize('continued', [False, True])
def test_persisted_activity_budget_survives_consultation_restart(tmp_path, continued):
    from Mind.trace import CONTINUED_NATIVE_PROTOCOL_VERSION, NATIVE_PROTOCOL_VERSION
    consultations = 2 if continued else 1
    request = step({'type': 'capability_request', 'capability': 'inspect_execution'})
    answers = iter([*[request] * consultations, step({'type': 'no_change'})])
    calls = []
    def transport(wire):
        calls.append(wire)
        return next(answers)
    def adapter(new_protocol):
        result = CognitiveModel(transport, contract='cognitive-chain-v20')
        if new_protocol:
            result.native_protocol_version = CONTINUED_NATIVE_PROTOCOL_VERSION
        return result
    options = dict(directory=tmp_path, available_capabilities=('inspect_execution',))
    with MindOrgan(model=adapter(continued), **options) as mind:
        receipt = mind.activate(input_value())
    observation = {'capability': 'inspect_execution', 'goal': 'Produce a valid report.',
                   'status': 'running', 'recent_outcome': 'Owner checked scope.', 'failure': None}
    for index in range(consultations):
        with MindOrgan(model=adapter(True), **options) as mind:
            assert mind.activate(input_value()).request == receipt.request
            assert mind.inspect().revision == 0
            receipt = mind.accept_result(MindResultEvent(receipt.request.request_ref, observation))
            assert receipt.status == ('accepted' if index == consultations - 1 else 'waiting')
    trace, records = traces(tmp_path)
    assert trace.events[0].payload['native_protocol'] == (CONTINUED_NATIVE_PROTOCOL_VERSION
                                                          if continued else NATIVE_PROTOCOL_VERSION)
    assert len(replay_activation(trace.events).model_requests) == consultations + 1
    assert len(calls) == consultations + 1
    assert all(record['version'] == trace.events[0].payload['native_protocol'] for record in records)
    with MindOrgan(model=adapter(True), **options) as mind:
        assert mind.activate(input_value()).status == 'duplicate'
        assert mind.inspect().revision == 1
    assert len(calls) == consultations + 1


def test_budget_binding_preserves_start_before_trace_creation_checkpoint(tmp_path, monkeypatch):
    from Mind.trace import CONTINUED_NATIVE_PROTOCOL_VERSION
    adapter = CognitiveModel(lambda wire: pytest.fail('No call before Trace creation'),
                             contract='cognitive-chain-v20')
    adapter.native_protocol_version = CONTINUED_NATIVE_PROTOCOL_VERSION
    def interrupted(*args, **kwargs):
        raise SystemExit('process exit before Trace creation')
    with MindOrgan(directory=tmp_path, model=adapter) as mind:
        with monkeypatch.context() as patch:
            patch.setattr(MindTrace, 'create', interrupted)
            with pytest.raises(SystemExit):
                mind.activate(input_value())
    with MindOrgan(directory=tmp_path, model=adapter) as mind:
        assert mind.inspect().revision == 0
    assert not adapter.calls


def test_synthetic_empty_return_repairs_with_paired_body_and_no_partial_commit(tmp_path):
    old = reply('cognitive_step', {})  # Explicit malformed fixture, not a provider recording.
    direction = 'The current direction omits the settlement condition; reassess eligibility.'
    answers = iter([old, step({'type': 'directive', 'text': direction}, [grounded()])])
    wires = []
    def transport(wire):
        wires.append(wire)
        journal = json.loads((tmp_path / 'cognition.json').read_text())
        assert all(r['kind'] == 'started' for r in journal['records'])
        return next(answers)
    with MindOrgan(directory=tmp_path, model=model(transport)) as mind:
        result = mind.activate(input_value())
        assert result.status == 'accepted' and mind.inspect().revision == 1
        app = mind.prepare_directive('first', execution_ref='run', decision_id='decision-000001',
                                     intention_ref='task', intention_revision=1)
        assert app.text == direction
        assert mind.prepare_directive('first', execution_ref='other', decision_id='decision-000001',
                                       intention_ref='task', intention_revision=1) is None
    trace, records = traces(tmp_path)
    assert len(records) == 4 and records[1]['response'] == old
    assert records[2]['repair']
    feedback = wires[1]['messages'][-1]['content'][0]
    assert wires[1]['messages'][-2]['content'] == old['content']
    assert feedback['tool_use_id'] == old['content'][0]['id']
    body = json.loads(feedback['content'])
    assert body['submission_status'] == 'rejected_before_commit'
    assert {e['message'] for e in body['field_errors']} == {
        "'type' is a required property", "'updates' is a required property", "'next' is a required property"}
    assert 'directive' not in feedback['content'].lower() and 'no_change' not in feedback['content'].lower()
    assert replay_activation(trace.events).final_result['text'] == direction


@pytest.mark.parametrize('point,accepted,new_calls', [
    ('rejected', True, 1), ('reserved', False, 0), ('accepted', True, 0), ('exhausted', False, 0)])
def test_process_restart_cannot_refresh_credit_or_resample_unknown(point, accepted, new_calls, tmp_path):
    code = r'''
import os,sys
from Mind.event_loop import CognitiveModel,RECOVERY_CONTRACT_VERSION
from Mind.organ import MindOrgan
from Mind.trace import MindTrace
from Mind.test_event_loop import reply,step,input_value
point=sys.argv[2]
original=MindTrace.append_native
def append(self,**payload):
    original(self,**payload)
    n=len(self.native_records())
    if ((point=='rejected' and n==2) or (point=='reserved' and n==3)
        or (point in {'accepted','exhausted'} and n==4)):
        os._exit(71)
MindTrace.append_native=append
answers=iter([reply('cognitive_step',{}), reply('cognitive_step',{}) if point=='exhausted' else step({'type':'no_change'})])
with MindOrgan(directory=sys.argv[1],model=CognitiveModel(lambda wire:next(answers),contract=RECOVERY_CONTRACT_VERSION)) as mind:
    mind.activate(input_value())
'''
    child = subprocess.run([sys.executable, '-c', code, str(tmp_path), point], cwd=Path.cwd(), capture_output=True)
    assert child.returncode == 71, child.stderr.decode(errors='replace')
    resumed = model(lambda wire: step({'type': 'no_change'}, [grounded()]))
    with MindOrgan(directory=tmp_path, model=resumed) as mind:
        assert mind.inspect().revision == 0
        result = mind.activate(input_value())
        assert (result.status == 'accepted') == accepted, result
        assert mind.inspect().revision == int(accepted)
        before = len(resumed.calls)
        again = mind.activate(input_value())
        assert again.status == ('duplicate' if accepted else 'failed')
        assert len(resumed.calls) == before
    _, records = traces(tmp_path)
    assert sum(r.get('repair', False) for r in records) == 1
    assert sum(not c.get('replayed') for c in resumed.calls) == new_calls


@pytest.mark.parametrize('repair_before_read', [True, False])
def test_one_credit_for_whole_activity_preserves_read_and_three_call_ceiling(tmp_path, repair_before_read):
    request = step({'type': 'capability_request', 'capability': 'inspect_execution'}, [grounded()])
    empty = reply('cognitive_step', {})
    answers = iter([empty, request] if repair_before_read else [request])
    first = model(lambda wire: next(answers))
    with MindOrgan(directory=tmp_path, model=first, available_capabilities=('inspect_execution',)) as mind:
        receipt = mind.activate(input_value())
        assert receipt.status == 'waiting' and mind.inspect().revision == 0
        ref = receipt.request.request_ref
    answers = iter([empty] if repair_before_read else [empty, step({'type': 'no_change'}, [grounded()])])
    second = model(lambda wire: next(answers))
    observation = {'capability': 'inspect_execution', 'goal': 'Produce a valid report.',
                   'status': 'running', 'recent_outcome': 'Owner checked scope.', 'failure': None}
    with MindOrgan(directory=tmp_path, model=second, available_capabilities=('inspect_execution',)) as mind:
        result = mind.accept_result(MindResultEvent(ref, observation))
        assert (result.status == 'accepted') == (not repair_before_read)
        assert mind.inspect().revision == int(not repair_before_read)
    _, records = traces(tmp_path)
    assert len(records) == 6 and sum(r.get('repair', False) for r in records) == 1


@pytest.mark.parametrize('bad', ['reference', 'quote', 'semantic_shape', 'cardinality', 'truncated', 'network', 'empty_id'])
def test_recovery_does_not_absorb_other_failures(tmp_path, bad):
    update = grounded()
    if bad == 'reference': update['basis'][0]['ref'] = 'invented'
    if bad == 'quote': update['basis'][0]['quote'] = 'Invented source text.'
    response = step({'type': 'no_change'}, [update])
    if bad == 'semantic_shape': response = step({'type': 'shell', 'command': 'do something'})
    if bad == 'cardinality': response['content'] *= 2
    if bad == 'truncated': response['stop_reason'] = 'max_tokens'
    if bad == 'empty_id':
        response = reply('cognitive_step', {})
        response['content'][0]['id'] = '  '
    calls = []
    def transport(wire):
        calls.append(wire)
        if bad == 'network': raise TimeoutError('Unknown provider outcome')
        return response
    with MindOrgan(directory=tmp_path, model=model(transport)) as mind:
        result = mind.activate(input_value())
        assert result.status == 'failed' and result.output is None and mind.inspect().revision == 0
    _, records = traces(tmp_path)
    assert len(calls) == 1 and not any(r.get('repair') for r in records)


def test_scenario_schema_and_existing_reducer_agree(tmp_path):
    scenario = {'kind': 'scenario', 'id': 'new:consequence', 'status': 'active',
        'assumptions': ['new:scope'], 'steps': [{'state': 'Unsettled entries included', 'actors': 'Execution',
        'action': 'Reassess scope', 'external': 'Declared settlement rule', 'outcome': 'A scoped report'}],
        'unknowns': ['Whether current entries are settled']}
    with MindOrgan(directory=tmp_path, model=model(lambda wire: step({'type': 'no_change'}, [grounded(), scenario]))) as mind:
        receipt = mind.activate(input_value())
        assert receipt.status == 'accepted', receipt
        assert mind.inspect().items[1]['analysis_status'] == 'QUALITATIVE'


@pytest.mark.parametrize('on_result', [False, True])
def test_storage_failure_never_becomes_protocol_correction(tmp_path, monkeypatch, on_result):
    original = MindTrace.append_native
    def failing(self, **payload):
        if (payload['kind'] == 'result') == on_result:
            raise TraceError('native_persistence_failed')
        return original(self, **payload)
    monkeypatch.setattr(MindTrace, 'append_native', failing)
    calls = []
    def transport(wire):
        calls.append(wire)
        return reply('cognitive_step', {})
    with MindOrgan(directory=tmp_path, model=model(transport)) as mind:
        result = mind.activate(input_value())
        assert result.status == 'failed' and mind.inspect().revision == 0
        assert result.error == 'trace_failed'
    assert len(calls) == int(on_result)


def test_whole_record_annex_tail_loss_cannot_refresh_repair_credit(tmp_path, monkeypatch):
    class Crash(BaseException): pass
    original = MindTrace.append_native
    def crash_after_repair(self, **payload):
        original(self, **payload)
        if len(self.native_records()) == 4:
            raise Crash()
    monkeypatch.setattr(MindTrace, 'append_native', crash_after_repair)
    with MindOrgan(directory=tmp_path, model=model(lambda wire: reply('cognitive_step', {}))) as mind:
        with pytest.raises(Crash): mind.activate(input_value())
    path, = tmp_path.glob('*.native.jsonl')
    lines = path.read_text().splitlines(keepends=True)
    path.write_text(''.join(lines[:2]))  # Structurally valid old prefix, repair tail lost.
    monkeypatch.setattr(MindTrace, 'append_native', original)
    resumed = model(lambda wire: pytest.fail('Repair credit must not refresh'))
    with MindOrgan(directory=tmp_path, model=resumed) as mind:
        receipt = mind.activate(input_value())
        assert receipt.status == 'failed' and mind.inspect().revision == 0
    assert not resumed.calls


def test_campaign_stops_on_trace_failure_before_independent_case(tmp_path, monkeypatch):
    from Mind.cognitive_contract import run_interface, recovery_cases
    from Mind.event_loop import digest
    registration = {'version': RECOVERY_CONTRACT_VERSION, 'development_max_calls': 6,
        'development': recovery_cases(), 'model': 'deepseek-v4-pro', 'thinking': 'disabled', 'temperature': 0}
    registration['sha256'] = digest(registration)
    (tmp_path / 'registration.json').write_text(json.dumps(registration))
    def broken(self, **payload): raise TraceError('native_persistence_failed')
    monkeypatch.setattr(MindTrace, 'append_native', broken)
    with pytest.raises(ValueError, match='hard_gate_persistence'):
        run_interface(tmp_path, 'dev-1', transport=lambda wire: pytest.fail('No dispatch after storage failure'))
    assert not (tmp_path / 'dev-1/case-2').exists()


def test_lost_second_phase_tail_cannot_create_fourth_physical_call(tmp_path, monkeypatch):
    class Crash(BaseException): pass
    original = MindTrace.append_native
    def crash(self, **payload):
        original(self, **payload)
        if len(self.native_records()) == 6: raise Crash()
    monkeypatch.setattr(MindTrace, 'append_native', crash)
    responses = iter([reply('cognitive_step', {}),
        step({'type': 'capability_request', 'capability': 'inspect_execution'}),
        step({'type': 'no_change'}, [grounded()])])
    with MindOrgan(directory=tmp_path, model=model(lambda wire: next(responses)),
                   available_capabilities=('inspect_execution',)) as mind:
        pending = mind.activate(input_value())
        observation = {'capability': 'inspect_execution', 'goal': 'Produce a valid report.',
                       'status': 'running', 'recent_outcome': 'Owner checked scope.', 'failure': None}
        with pytest.raises(Crash): mind.accept_result(MindResultEvent(pending.request.request_ref, observation))
    path, = tmp_path.glob('*.native.jsonl')
    path.write_text(''.join(path.read_text().splitlines(keepends=True)[:4]))
    monkeypatch.setattr(MindTrace, 'append_native', original)
    resumed = model(lambda wire: pytest.fail('Lost phase-2 tail must not permit a fourth call'))
    with MindOrgan(directory=tmp_path, model=resumed, available_capabilities=('inspect_execution',)) as mind:
        assert mind.activate(input_value()).status == 'failed'
        assert mind.inspect().revision == 0
    assert not resumed.calls
