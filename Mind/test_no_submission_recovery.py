"""Received no-submission responses get one bounded, versioned native correction."""
import json

import pytest

from Mind.activity import start_activity, continue_activity, resume_native_activity
from Mind.contracts import ActivationInput, ActivationFailure, CapabilityRequest, NoChange
from Mind.model import MindModel
from Mind.task_view import COGNITIVE_INTERFACE_VERSION
from Mind.test_cognition_core import Script
from Mind.test_model import commit, context, response
from Mind.trace import MindTrace, MODEL_OUTPUT_RECORDED
from Nervous.provider import BudgetPause, ProviderCalls


def no_submission(kind='thinking', stop='max_tokens'):
    block = ({'type': 'thinking', 'thinking': 'Unfinished private process.', 'signature': 'saved-signature'}
             if kind == 'thinking' else {'type': 'text', 'text': 'A conclusion without a native submission.'})
    return {'content': [block], 'stop_reason': stop}


def begin(directory, model, *, versioned=True, capabilities=()):
    cognitive_context = context()
    if versioned:
        cognitive_context['cognitive_interface'] = COGNITIVE_INTERFACE_VERSION
    trace = MindTrace.create(directory / 'activity.jsonl', activation_id='activity-1')
    result = start_activity(ActivationInput('Assess the new evidence.', 'Deliver a sourced judgment.', None),
        model=model, trace=trace, cognitive_context=cognitive_context, capabilities=capabilities)
    return trace, result


@pytest.mark.parametrize('kind,stop', [('thinking', 'max_tokens'), ('text', 'end_turn')])
def test_received_no_submission_is_corrected_by_user_message_without_fake_tool_result(tmp_path, kind, stop):
    incomplete = no_submission(kind, stop)
    belief = {'kind': 'belief', 'id': 'new:literal', 'claim': 'The current source is incomplete.',
              'status': 'contradicted', 'basis': [{'ref': 'rule'}]}
    script = Script(incomplete, commit([belief]))
    trace, result = begin(tmp_path, MindModel(script), capabilities=('read_evidence', 'analyze_world_model'))
    assert isinstance(result, NoChange)
    assert len(script.wires) == 2
    first, corrected = script.wires
    assert corrected['messages'][:-2] == first['messages']
    assert corrected['messages'][-2] == {'role': 'assistant', 'content': incomplete['content']}
    feedback = corrected['messages'][-1]
    assert feedback['role'] == 'user' and isinstance(feedback['content'], str)
    assert 'no_change' not in feedback['content'] and 'contradicted' not in feedback['content']
    assert 'tool_result' not in json.dumps(feedback)
    assert corrected['tools'] == first['tools']
    assert {tool['name'] for tool in corrected['tools']} == {'cognitive_step', 'read_evidence', 'analyze_world_model'}
    assert corrected['thinking'] == first['thinking'] and corrected['max_tokens'] == first['max_tokens']
    accepted = next(event for event in trace.events if event.event_type == MODEL_OUTPUT_RECORDED)
    assert json.loads(accepted.payload['text'])['updates'] == [belief]
    assert [r['accepted'] for r in trace.native_records() if r['kind'] == 'result'] == [False, True]


def test_versioned_schema_uses_updates_and_archiving_without_current_selection(tmp_path):
    supplied_current = response('cognitive_step', {'type': 'cognitive_step', 'updates': [],
        'next': {'type': 'no_change'}, 'current': []})
    script = Script(supplied_current, commit())
    trace, result = begin(tmp_path, MindModel(script))
    assert isinstance(result, NoChange) and len(script.wires) == 2
    wire = script.wires[0]
    schema = wire['tools'][0]['input_schema']
    assert 'current' not in schema['properties']
    assert 'use current to retire' not in wire['system']
    assert 'current can explicitly select' not in wire['tools'][0]['description']
    feedback = script.wires[1]['messages'][-1]['content'][0]
    assert feedback['type'] == 'tool_result' and feedback['tool_use_id'] == 'call-1'
    assert 'current' in feedback['content']
    assert [r['accepted'] for r in trace.native_records() if r['kind'] == 'result'] == [False, True]


def test_unmarked_old_schema_keeps_current_selection(tmp_path):
    script = Script(response('cognitive_step', {'type': 'cognitive_step', 'updates': [],
        'next': {'type': 'no_change'}, 'current': []}))
    trace, result = begin(tmp_path, MindModel(script), versioned=False)
    assert isinstance(result, NoChange) and len(script.wires) == 1
    assert 'current' in script.wires[0]['tools'][0]['input_schema']['properties']
    before = {path: path.read_bytes() for path in tmp_path.glob('*.jsonl')}
    MindTrace.reopen(tmp_path / 'activity.jsonl')
    assert all(path.read_bytes() == body for path, body in before.items())


@pytest.mark.parametrize('first,second', [
    (no_submission(), no_submission('text', 'end_turn')),
    (response('wrong_tool', {}), no_submission()),
    (no_submission(), response('wrong_tool', {})),
])
def test_all_protocol_errors_share_one_activity_correction(tmp_path, first, second):
    script = Script(first, second, commit())
    trace, result = begin(tmp_path, MindModel(script))
    assert isinstance(result, ActivationFailure)
    assert len(script.wires) == 2 and len(script.responses) == 1
    assert not any(event.event_type == MODEL_OUTPUT_RECORDED for event in trace.events)
    assert [r['recoverable'] for r in trace.native_records() if r['kind'] == 'result'] == [True, False]


def test_correction_limit_survives_consultation_restart(tmp_path):
    script = Script(no_submission(), response('read_evidence', {'refs': ['rule']}), no_submission(), commit())
    trace, result = begin(tmp_path, MindModel(script), capabilities=('read_evidence',))
    assert isinstance(result, CapabilityRequest) and len(script.wires) == 2
    frozen = (tmp_path / 'activity.native.jsonl').read_bytes()
    trace = MindTrace.reopen_for_result(tmp_path / 'activity.jsonl')
    result = continue_activity(trace=trace, model=MindModel(script), observation={
        'capability': 'read_evidence', 'origin': 'execution', 'text': json.dumps({
            'read_result': 'sources-v1', 'sources': [
                {'ref': 'rule', 'origin': 'execution', 'text': 'The current source is incomplete.'}]})})
    assert isinstance(result, ActivationFailure) and len(script.wires) == 3
    assert len(script.responses) == 1
    assert (tmp_path / 'activity.native.jsonl').read_bytes().startswith(frozen)


@pytest.mark.parametrize('block', [
    {'type': 'tool_use', 'id': 'partial', 'name': 'read_evidence', 'input': None},
    {'type': 'server_tool_use', 'id': 'external', 'name': 'unowned_tool', 'input': {}},
])
def test_partial_or_unowned_tool_outcome_is_not_replayed_as_no_submission(tmp_path, block):
    script = Script({'content': [block], 'stop_reason': 'max_tokens'}, commit())
    trace, result = begin(tmp_path, MindModel(script), capabilities=('read_evidence',))
    assert isinstance(result, ActivationFailure) and len(script.wires) == 1
    assert trace.native_records()[-1]['recoverable'] is False


def test_recovery_respects_shared_provider_budget_and_resumes_the_same_request(tmp_path):
    script = Script(no_submission(), commit())
    limits = {'calls': 1, 'output_tokens': 32768, 'request_bytes': 480000}
    calls = ProviderCalls(tmp_path / 'calls', limits, lambda _, wire: script(wire))
    model = MindModel(calls, preflight=lambda wire: calls.ensure(wire, role='mind'))
    with pytest.raises(BudgetPause, match='provider_budget_exhausted'):
        begin(tmp_path, model)
    assert calls.summary()['calls'] == 1
    original = (tmp_path / 'calls/0001.json').read_bytes()
    trace = MindTrace.reopen_for_native(tmp_path / 'activity.jsonl')
    assert len(trace.native_records()) == 2 and trace.native_records()[-1]['recoverable']
    # An explicit test-owner extension adds one slot; recovery does not reset costs.
    calls = ProviderCalls(calls.directory, {**limits, 'calls': 2}, lambda _, wire: script(wire))
    restored = MindModel(calls, preflight=lambda wire: calls.ensure(wire, role='mind'))
    assert isinstance(resume_native_activity(trace, restored), NoChange)
    assert calls.summary()['calls'] == 2 and len(script.wires) == 2
    assert (tmp_path / 'calls/0001.json').read_bytes() == original


@pytest.mark.parametrize('crash_on_result', [1, 2])
def test_received_provider_results_recover_without_repeating_dispatch_or_cost(tmp_path, monkeypatch, crash_on_result):
    class Crash(BaseException):
        pass
    script = Script(no_submission(), commit())
    limits = {'calls': 2, 'output_tokens': 32768, 'request_bytes': 480000}
    calls = ProviderCalls(tmp_path / 'calls', limits, lambda _, wire: script(wire))
    original_append = MindTrace.append_native
    results = 0
    def interrupted(trace, **value):
        nonlocal results
        if value['kind'] == 'result':
            results += 1
            if results == crash_on_result:
                raise Crash()
        return original_append(trace, **value)
    with monkeypatch.context() as patch:
        patch.setattr(MindTrace, 'append_native', interrupted)
        with pytest.raises(Crash):
            begin(tmp_path, MindModel(calls))
    assert calls.summary()['calls'] == crash_on_result
    saved = {path: path.read_bytes() for path in calls.directory.glob('*.json')}
    frozen_native = (tmp_path / 'activity.native.jsonl').read_bytes()
    calls = ProviderCalls(calls.directory, limits, lambda _, wire: script(wire))
    trace = MindTrace.reopen_for_native(tmp_path / 'activity.jsonl', allow_pending=True)
    assert isinstance(resume_native_activity(trace, MindModel(calls)), NoChange)
    assert calls.summary()['calls'] == 2 and len(script.wires) == 2
    assert all(path.read_bytes() == body for path, body in saved.items())
    assert (tmp_path / 'activity.native.jsonl').read_bytes().startswith(frozen_native)


def test_frozen_legacy_no_submission_keeps_its_original_failure_rule(tmp_path, monkeypatch):
    class Crash(BaseException):
        pass
    script = Script(no_submission(), commit())
    calls = ProviderCalls(tmp_path / 'calls', {'calls': 2, 'output_tokens': 32768, 'request_bytes': 480000},
                          lambda _, wire: script(wire))
    original_append = MindTrace.append_native
    def interrupted(trace, **value):
        if value['kind'] == 'result':
            raise Crash()
        return original_append(trace, **value)
    with monkeypatch.context() as patch:
        patch.setattr(MindTrace, 'append_native', interrupted)
        with pytest.raises(Crash):
            begin(tmp_path, MindModel(calls), versioned=False)
    original_call = (tmp_path / 'calls/0001.json').read_bytes()
    trace = MindTrace.reopen_for_native(tmp_path / 'activity.jsonl', allow_pending=True)
    frozen_wire = trace.native_records()[0]['wire']
    assert isinstance(resume_native_activity(trace, MindModel(calls)), ActivationFailure)
    assert calls.summary()['calls'] == 1 and len(script.wires) == 1
    assert trace.native_records()[0]['wire'] == frozen_wire
    assert trace.native_records()[-1]['recoverable'] is False
    assert (tmp_path / 'calls/0001.json').read_bytes() == original_call


def test_unknown_provider_dispatch_remains_blocked_without_retry(tmp_path):
    class LostOutcome(BaseException):
        pass
    def lost(*_):
        raise LostOutcome()
    calls = ProviderCalls(tmp_path / 'calls', {'calls': 2, 'output_tokens': 32768, 'request_bytes': 480000}, lost)
    with pytest.raises(LostOutcome):
        begin(tmp_path, MindModel(calls))
    before = calls.summary()
    calls = ProviderCalls(calls.directory, calls.limits, lambda *_: pytest.fail('Unknown dispatch repeated'))
    trace = MindTrace.reopen_for_native(tmp_path / 'activity.jsonl', allow_pending=True)
    with pytest.raises(BudgetPause, match='provider_call_outcome_unknown'):
        resume_native_activity(trace, MindModel(calls))
    assert calls.summary() == before and len(trace.native_records()) == 1
