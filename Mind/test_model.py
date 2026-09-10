"""The supported native activity protocol, without historical campaign replay."""
import json

import pytest

from Mind.activity import start_activity, continue_activity, resume_native_activity
from Mind.contracts import ActivationInput, CapabilityRequest, NoChange, ActivationFailure
from Mind.model import MindModel, citation_sources
from Mind.task_view import COGNITIVE_CONTRACT_VERSION, execution_goal, mind_task_view, fingerprint
from Mind.trace import MindTrace, ACTIVATION_STARTED, MODEL_OUTPUT_RECORDED, project_model_request
from Nervous.provider import ProviderCalls


def response(name, value):
    return {'content': [{'type': 'thinking', 'thinking': 'private process', 'signature': 'signature'},
                        {'type': 'tool_use', 'id': 'call-1', 'name': name, 'input': value}],
            'stop_reason': 'tool_use'}


def commit(updates=None):
    return response('cognitive_step', {'type': 'cognitive_step', 'updates': updates or [],
                                     'next': {'type': 'no_change'}})


def context(items=()):
    return {'contract_version': COGNITIVE_CONTRACT_VERSION, 'items': list(items), 'execution_ref': None,
            'evidence': [{'ref': 'rule', 'text': 'The current source is incomplete.', 'origin': 'execution'}]}


def begin(tmp_path, model, *, items=(), capabilities=()):
    trace = MindTrace.create(tmp_path / 'activity.jsonl', activation_id='activity-1')
    result = start_activity(ActivationInput('Evaluate the new evidence.', 'Deliver a sourced conclusion.', None),
        model=model, trace=trace, cognitive_context=context(items), capabilities=capabilities)
    return trace, result


def test_native_claims_global_response_without_repeating_call_or_charge(tmp_path, monkeypatch):
    class Crash(BaseException):
        pass
    calls = ProviderCalls(tmp_path / 'calls',
        {'calls': 1, 'output_tokens': 16384, 'request_bytes': 240000}, lambda *_: commit())
    append = MindTrace.append_native
    def interrupted(trace, **value):
        if value['kind'] == 'result':
            raise Crash()
        return append(trace, **value)
    with monkeypatch.context() as patch:
        patch.setattr(MindTrace, 'append_native', interrupted)
        with pytest.raises(Crash):
            begin(tmp_path, MindModel(calls))
    assert calls.summary()['calls'] == 1
    before = calls.summary()
    calls = ProviderCalls(calls.directory, calls.limits, lambda *_: pytest.fail('Known Mind call repeated'))
    trace = MindTrace.reopen_for_native(tmp_path / 'activity.jsonl', allow_pending=True)
    assert isinstance(resume_native_activity(trace, MindModel(calls)), NoChange)
    assert calls.summary() == before
    assert len(trace.native_records()) == 2


def test_unknown_tool_feedback_does_not_supply_a_semantic_answer(tmp_path):
    wires = []
    wrong_belief = {'kind': 'belief', 'id': 'new:literal', 'claim': 'The current source is incomplete.',
                    'status': 'contradicted', 'basis': [{'ref': 'rule'}]}
    def transport(wire):
        wires.append(wire)
        if len(wires) == 1:
            return response('finish_review', {})
        feedback = json.loads(wire['messages'][-1]['content'][0]['content'])
        assert feedback['field_errors'][0]['validator'] == 'native_tool_name'
        assert 'contradicted' not in feedback['contract']
        return commit([wrong_belief])
    trace, result = begin(tmp_path, MindModel(transport))
    assert isinstance(result, NoChange)
    assert len(wires) == 2
    assert [r['accepted'] for r in trace.native_records() if r['kind'] == 'result'] == [False, True]
    # A deliberately inconsistent scripted judgment is not rewritten by the host.
    output = next(e for e in trace.events if e.event_type == MODEL_OUTPUT_RECORDED)
    assert json.loads(output.payload['text'])['updates'] == [wrong_belief]


def test_read_analyze_submit_survives_consultation_restarts(tmp_path):
    wires = []
    def transport(wire):
        wires.append(wire)
        if len(wires) == 1:
            return response('read_evidence', {'refs': ['measurement']})
        assert wire['messages'][-2]['content'][0]['type'] == 'thinking'
        if len(wires) == 2:
            return response('analyze_world_model', {'refs': ['measurement'],
                'question': 'What remains conditional?', 'model_ref': ''})
        return commit([{'kind': 'belief', 'id': 'new:measured', 'claim': 'The measured size is fifteen bytes.',
                        'status': 'supported', 'basis': [{'ref': 'measurement'}]}])
    trace, result = begin(tmp_path, MindModel(transport), capabilities=('read_evidence', 'analyze_world_model'))
    assert isinstance(result, CapabilityRequest) and result.capability == 'read_evidence'
    trace = MindTrace.reopen_for_result(tmp_path / 'activity.jsonl')
    result = continue_activity(trace=trace, model=MindModel(transport), observation={
        'capability': 'read_evidence', 'origin': 'execution', 'text': json.dumps({
            'read_result': 'sources-v1', 'sources': [{'ref': 'measurement', 'origin': 'execution',
                                                   'text': 'The measured size is fifteen bytes.'}]})})
    assert isinstance(result, CapabilityRequest) and result.capability == 'analyze_world_model'
    trace = MindTrace.reopen_for_result(tmp_path / 'activity.jsonl')
    result = continue_activity(trace=trace, model=MindModel(transport), observation={
        'capability': 'analyze_world_model', 'origin': 'computation', 'text': json.dumps({
            'kind': 'MODEL_ANALYSIS', 'answer': 'The present measurement is available.',
            'assumptions': '', 'unknowns': 'No future observation.'})})
    assert isinstance(result, NoChange)
    assert len(wires) == 3
    # Continuations append new source material, rather than reinsert all prior context.
    assert len(wires[-1]['messages']) == 5
    assert wires[-1]['messages'][0] == wires[0]['messages'][0]
    assert json.dumps(wires[-1]['messages']).count('The current source is incomplete.') == 1
    assert [r['phase'] for r in trace.native_records() if r['kind'] == 'call'] == [1, 2, 3]


def test_repair_budget_is_shared_and_final_call_cannot_consult(tmp_path):
    wires = []
    def transport(wire):
        wires.append(wire)
        return response('unavailable_tool', {})
    trace, result = begin(tmp_path, MindModel(transport), capabilities=('read_evidence', 'analyze_world_model'))
    assert isinstance(result, ActivationFailure)
    assert len(wires) == 6
    assert {t['name'] for t in wires[0]['tools']} == {'cognitive_step', 'read_evidence', 'analyze_world_model'}
    assert [t['name'] for t in wires[-1]['tools']] == ['cognitive_step']
    assert len(trace.native_records()) == 12


def test_accepted_native_result_replays_without_another_call(tmp_path):
    count = 0
    def transport(wire):
        nonlocal count
        count += 1
        return commit()
    model = MindModel(transport)
    trace = MindTrace.create(tmp_path / 'activity.jsonl', activation_id='activity-1')
    trace.append(ACTIVATION_STARTED, {'activation': {'trigger': 'Review.',
        'execution_goal_snapshot': 'Deliver a conclusion.', 'execution_status': None},
        'cognitive_context': context(), 'available_capabilities': [], 'native_protocol': model.native_protocol_version},
        source_event_seqs=())
    model.generate_from_trace(trace, project_model_request(trace.events))
    # Crash window: native result persisted, logical MODEL_OUTPUT not yet appended.
    frozen = trace.native_records()[0]["wire"]
    assert "capacity" not in json.loads(frozen["messages"][0]["content"])["cognition"]
    trace = MindTrace.reopen_for_native(tmp_path / 'activity.jsonl')
    assert isinstance(resume_native_activity(trace, MindModel(transport)), NoChange)
    assert count == 1
    assert trace.native_records()[0]["wire"] == frozen


def test_projection_preserves_business_goal_sources_and_prior_truth(tmp_path):
    owner = {'business_goal': 'Deliver the report with all required measurements.',
             'execution_protocol': 'Use runtime-marker and ClaimComplete after local checks.'}
    actual_goal = execution_goal(owner)
    goal_ref = 'owner-task:' + fingerprint(owner)
    belief = {'kind': 'belief', 'id': 'item-prior', 'claim': 'The source is incomplete.',
              'status': 'contradicted', 'basis': [{'ref': 'rule'}]}
    cognition = context([belief])
    cognition['task_view'] = mind_task_view(owner, actual_goal)
    cognition['evidence'].append({'ref': goal_ref, 'text': owner['business_goal'], 'origin': 'execution'})
    model = MindModel(lambda wire: commit(), readable_sources=lambda: ['future-readable'])
    trace = MindTrace.create(tmp_path / 'activity.jsonl', activation_id='activity-1')
    start_activity(ActivationInput('Review before starting.', actual_goal, None), model=model, trace=trace,
                   cognitive_context=cognition, capabilities=('read_evidence', 'analyze_world_model'))
    wire = model.calls[0]['wire']
    content = json.loads(wire['messages'][0]['content'])
    assert content['goal'] == {'ref': goal_ref, 'text': owner['business_goal']}
    assert 'runtime-marker' not in json.dumps(wire) and 'ClaimComplete' not in json.dumps(wire)
    assert content['cognition']['prior_model_judgments'][0]['prior_truth'] is False
    assert belief['status'] == 'contradicted'  # Input record was not edited.
    read = next(t for t in wire['tools'] if t['name'] == 'read_evidence')
    assert 'future-readable' in read['input_schema']['properties']['refs']['items']['examples']
    assert 'future-readable' not in citation_sources(model.calls[0]['projection']['user_message'])
