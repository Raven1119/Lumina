"""D7 input, representation and native continuation; no semantic-model claims."""
import copy
import json
from dataclasses import replace

import pytest

from Mind.event_loop import CognitiveModel, _document, _episode, citation_sources
from Mind.experiment_a import ExecutionObservation
from Mind.organ import Evidence, MindInput, MindOrgan, MindResultEvent, _apply_updates
from Mind.task_view import THINKING_CONTRACT_VERSION as D7, execution_goal
from Mind.test_cognitive_chain import belief, response
from Nervous.organ import NervousOrgan


TASK = {'business_goal': 'Deliver a complete receipt with valid custody evidence.',
        'execution_protocol': 'Write verified to .lumina-complete, then ClaimComplete.'}


def input_value():
    return MindInput('event-1', 'Owner condition changed.', 'task', 1, execution_goal(TASK),
        'run', 'waiting', (Evidence('owner', 'Custody evidence is missing.', 'execution'),),
        execution_observation=ExecutionObservation(execution_goal(TASK), 'waiting', 'Custody evidence is missing.', None),
        owner_task=TASK)


def payload(wire):
    text = wire['messages'][-1]['content']
    if isinstance(text, list):
        text = json.loads(text[0]['content'])['continuation']
    return json.loads(text.split('\n\nExact citation catalogue')[0])


def test_task_views_are_owner_bound_through_read_citation_and_restart(tmp_path):
    wires = []
    def transport(wire):
        wires.append(copy.deepcopy(wire))
        view = payload(wire)
        assert view['activation']['execution_goal_snapshot'] == TASK['business_goal']
        assert view['initial_execution_observation']['goal'] == TASK['business_goal']
        assert '.lumina-complete' not in json.dumps(wire)
        sources = citation_sources(json.dumps(view))
        assert TASK['business_goal'] in sources.values()
        if len(wires) == 1:
            result = response(next_value={'type': 'capability_request', 'capability': 'inspect_execution'})
            result['content'].insert(0, {'type': 'thinking', 'thinking': 'opaque provider state', 'signature': 'opaque'})
            return result
        if len(wires) == 2:
            assert wire['messages'][-2]['content'][0]['type'] == 'thinking'
            assert TASK['business_goal'] in sources['activation:observation']
            item = belief('activation:observation', 'Custody evidence is missing.')
            item.pop('discriminator')
            return response([item], {'type': 'directive', 'text': 'Prioritize the missing custody evidence.'})
        assert view['cognition']['items'][0]['basis'][0]['ref'].startswith('activation-')
        return response()
    model = CognitiveModel(transport, contract=D7)
    value = input_value()
    with MindOrgan(directory=tmp_path/'mind', model=model, available_capabilities=('inspect_execution',)) as mind:
        waiting = mind.activate(value)
        assert waiting.status == 'waiting'
    observation = {'capability': 'inspect_execution', 'goal': value.goal, 'status': 'waiting',
        'recent_outcome': 'Custody evidence is missing.', 'failure': None}
    with MindOrgan(directory=tmp_path/'mind', model=model, available_capabilities=('inspect_execution',)) as mind:
        before = _document(mind.inspect())
        with pytest.raises(ValueError, match='execution_task_view_conflict'):
            mind.accept_result(MindResultEvent(waiting.request.request_ref, {**observation, 'goal': 'different task'}))
        assert _document(mind.inspect()) == before
        assert mind.accept_result(MindResultEvent(waiting.request.request_ref, observation)).status == 'accepted'
        view = _document(mind.inspect())
    with MindOrgan(directory=tmp_path/'mind', model=model) as mind:
        assert _document(mind.inspect()) == view
        assert 'discriminator' not in view['items'][0]
        assert mind.activate(replace(value, event_id='event-2')).status == 'accepted'
        with pytest.raises(ValueError, match='owner_task_identity_conflict'):
            mind.activate(replace(value, event_id='event-3', owner_task=None))
    assert len(wires) == 3
    # Raw owner goal and provider continuation remain durable; only projection differs.
    journal = json.loads((tmp_path/'mind/cognition.json').read_text(encoding='utf-8'))
    assert journal['records'][0]['input']['goal'] == value.goal
    assert 'ClaimComplete' in journal['records'][0]['input']['owner_task']['execution_protocol']
    assert journal['records'][0]['budget_version'] == 'cognition-events-d7:2-steps:3-calls:1-result:6000-output-chars'


def test_owner_view_never_fakes_the_execution_goal(tmp_path):
    with MindOrgan(directory=tmp_path, model=None) as mind:
        with pytest.raises(ValueError, match='owner_task_goal_conflict'):
            mind.activate(replace(input_value(), owner_task={**TASK, 'business_goal': 'Another goal.'}))


def test_optional_test_is_versioned_and_old_errors_remain_until_explicit_update():
    sources = {'owner': {'text': 'Observed condition.', 'origin': 'execution', 'ref': 'owner'}}
    old = belief('owner', 'Observed condition.', discriminator='An old erroneous condition.')
    items = _apply_updates({}, [old], sources, 'seed')
    assert _apply_updates(items, [], sources, 'next', contract=D7) == items
    update = {**next(iter(items.values()))}
    update.pop('discriminator')
    with pytest.raises(ValueError, match='invalid_belief'):
        _apply_updates(items, [update], sources, 'legacy')
    revised = _apply_updates(items, [update], sources, 'new', contract=D7)
    assert 'discriminator' not in next(iter(revised.values()))
    assert next(iter(items.values()))['discriminator'] == old['discriminator']


@pytest.mark.parametrize('thinking', [False, True])
def test_native_repair_preserves_provider_content_and_bounded_profile(tmp_path, thinking):
    wires = []
    rejected = response(); rejected['content'][0]['input'].pop('updates')
    rejected['content'].insert(0, {'type': 'thinking', 'thinking': 'opaque'})
    def transport(wire):
        wires.append(copy.deepcopy(wire))
        if len(wires) == 1:
            return rejected
        assert wire['messages'][-2]['content'] == rejected['content']
        assert 'field_errors' in wire['messages'][-1]['content'][0]['content']
        return response()
    with MindOrgan(directory=tmp_path, model=CognitiveModel(transport, contract=D7, thinking=thinking)) as mind:
        assert mind.activate(input_value()).status == 'accepted'
    assert len(wires) == 2
    assert wires[0]['max_tokens'] == 8192
    assert wires[0]['thinking']['type'] == ('enabled' if thinking else 'disabled')
    assert ('temperature' not in wires[0]) == thinking
    assert wires[0]['tool_choice'] == ({'type': 'auto'} if thinking else {'type': 'tool', 'name': 'cognitive_step'})


def test_larger_cognitive_update_commits_atomically_and_replays(tmp_path):
    updates = [belief('owner', 'Custody evidence is missing.', id='new:b'+str(i),
        claim='C'+str(i)+'x'*398, discriminator='t'*300) for i in range(4)]
    assert len(json.dumps(updates)) > 2000
    with MindOrgan(directory=tmp_path, model=CognitiveModel(lambda w: response(updates), contract=D7)) as mind:
        assert mind.activate(input_value()).status == 'accepted'
        before = _document(mind.inspect())
    with MindOrgan(directory=tmp_path, model=None) as reopened:
        assert _document(reopened.inspect()) == before
        assert len(before['items']) == 4


def test_negative_control_output_is_measured_even_when_external_review_allows_it(tmp_path):
    from Mind.cognitive_contract import run_semantic_case
    from Mind.test_semantic_revision import review
    case = {'id': 'complete', 'goal': 'Deliver the complete receipt.', 'events': [{
        'facts': 'Receipt is complete.', 'kind': 'execution.outcome', 'status': 'completed',
        'trigger': 'Routine owner recheck.', 'expected_output': 'no_change'}]}
    item = belief('synthetic-owner:owner-event-1', 'Receipt is complete.')
    result = run_semantic_case(case, tmp_path, transport=lambda w: response([item],
        {'type': 'directive', 'text': 'Repeat the completed handoff review.'}),
        review=review, image='unused', contract=D7)
    assert result['episodes'][0]['receipt']['status'] == 'accepted'
    assert result['actual_outputs'] == ['directive']
    assert result['expected_output_matches'] is False
    assert result['passed'] is False


def test_d7_recorder_retains_thinking_and_enforces_shared_budget(tmp_path):
    from Mind.cognitive_contract import _start_stage, digest
    registration = {'version': 'cognitive-input-d7-v1', 'model': 'deepseek-v4-pro',
        'stage_limits': {'protocol_probe': 1}, 'total_call_limit': 1}
    registration['sha256'] = digest(registration)
    (tmp_path/'registration.json').write_text(json.dumps(registration), encoding='utf-8')
    returned = {**response(), 'model': 'deepseek-v4-pro'}
    returned['content'].insert(0, {'type': 'thinking', 'thinking': 'opaque'})
    _, _, recorded, _ = _start_stage(tmp_path, 'protocol_probe', 3, lambda w: returned,
        source_files=('Mind/task_view.py',))
    wire = {'model': 'deepseek-v4-pro', 'thinking': {'type': 'enabled'},
        'output_config': {'effort': 'high'}, 'max_tokens': 8192}
    assert recorded(wire) == returned
    with pytest.raises(ValueError, match='campaign_budget_exhausted'):
        recorded(wire)


def test_d7_recorder_retains_bounded_http_error_evidence(tmp_path):
    import httpx
    from Mind.cognitive_contract import _start_stage, digest
    registration = {'version': 'cognitive-input-d7-v1', 'model': 'deepseek-v4-pro',
        'stage_limits': {'protocol_probe': 2}, 'total_call_limit': 2}
    registration['sha256'] = digest(registration)
    (tmp_path/'registration.json').write_text(json.dumps(registration), encoding='utf-8')
    def reject(wire):
        httpx.Response(400, text='Invalid tool configuration.', request=httpx.Request('POST',
            'https://api.deepseek.com/anthropic/v1/messages')).raise_for_status()
    _, _, recorded, calls = _start_stage(tmp_path, 'protocol_probe', 2, reject,
        source_files=('Mind/task_view.py',))
    wire = {'model': 'deepseek-v4-pro', 'thinking': {'type': 'enabled'},
        'output_config': {'effort': 'high'}, 'max_tokens': 8192}
    with pytest.raises(httpx.HTTPStatusError):
        recorded(wire)
    assert calls[0]['http_error'] == {'status_code': 400, 'body': 'Invalid tool configuration.'}
    assert 'response' not in calls[0]


@pytest.mark.parametrize('contract,accepted', [(D7, False), ('cognitive-submit-d7-v3', True)])
def test_context_capacity_is_versioned_atomic_and_survives_unicode_read_repair(tmp_path, contract, accepted):
    from Mind.task_view import context_limit
    from Mind.trace import MindTrace
    evidence = tuple(Evidence('source-'+str(i), '\U0001f30d'*1000, 'execution') for i in range(3))
    value = replace(input_value(), evidence=evidence, execution_observation=None)
    def updates(count, prefix):
        return [belief(evidence[i % 3].ref, '\U0001f30d'*100, id='new:'+prefix+str(i),
            claim='C'+'\U0001f30d'*399, discriminator='D'+'\U0001f30d'*299) for i in range(count)]
    answers = [response(updates(4, 'first')), response(updates(2, 'second'))]
    with MindOrgan(directory=tmp_path, model=CognitiveModel(lambda w: answers.pop(0), contract=contract)) as mind:
        assert mind.activate(value).status == 'accepted'
        before = _document(mind.inspect())
        receipt = mind.activate(replace(value, event_id='event-2'))
        assert (receipt.status == 'accepted') == accepted
        if not accepted:
            assert receipt.error == 'context_budget_exceeded'
            assert _document(mind.inspect()) == before
            return
        committed = _document(mind.inspect())
    wire_calls = []
    def transport(wire):
        wire_calls.append(wire)
        if len(wire_calls) == 1:
            result = response(next_value={'type': 'capability_request', 'capability': 'inspect_execution'})
        elif len(wire_calls) == 2:
            result = response(); result['content'][0]['input'].pop('updates')
        else:
            result = response()
        result['content'].insert(0, {'type': 'thinking', 'thinking': '\U0001f30d'*8192})
        return result
    model = CognitiveModel(transport, contract=contract)
    with MindOrgan(directory=tmp_path, model=model, available_capabilities=('inspect_execution',)) as mind:
        assert _document(mind.inspect()) == committed
        waiting = mind.activate(replace(value, event_id='event-3'))
        assert waiting.status == 'waiting'
    with MindOrgan(directory=tmp_path, model=model, available_capabilities=('inspect_execution',)) as mind:
        receipt = mind.accept_result(MindResultEvent(waiting.request.request_ref, {
            'capability': 'inspect_execution', 'goal': value.goal, 'status': 'completed',
            'recent_outcome': 'The owner evidence is unchanged.', 'failure': None}))
        assert receipt.status == 'accepted'
        final = _document(mind.inspect())
        assert {i['id']: i for i in final['items']} == {i['id']: i for i in committed['items']}
    with MindOrgan(directory=tmp_path, model=None) as mind:
        assert _document(mind.inspect()) == final
    assert len(wire_calls) == 3
    journal = json.loads((tmp_path/'cognition.json').read_text(encoding='utf-8'))
    start = next(r for r in journal['records'] if r.get('kind') == 'started' and r['event_id'] == 'event-3')
    assert 8000 < len(json.dumps(start['context'], ensure_ascii=False)) < context_limit(start['context'])
    trace_path = tmp_path/(start['activation_id']+'.jsonl')
    assert max(map(len, trace_path.read_bytes().splitlines())) > 32768
    trace = MindTrace.reopen(trace_path)
    assert len(trace.native_records()) == 6
    from Mind.trace import _validate_started, TraceError
    too_large = _document(trace.events[0].payload)
    too_large['cognitive_context']['padding'] = 'x'*16000
    with pytest.raises(TraceError, match='invalid_cognitive_context'):
        _validate_started(too_large)


@pytest.mark.parametrize('wrong_run', [False, True])
def test_engineering_continuation_restores_history_without_replacing_acceptance(tmp_path, monkeypatch, wrong_run):
    from pathlib import Path
    from types import SimpleNamespace
    from Mind.cognitive_contract import run_semantic_case, run_input_continuation, digest
    from Mind.test_semantic_revision import review
    import Execution
    root = tmp_path/'campaign'
    original = root/'acceptance/signed_summary_revision'
    case = {'id': 'scripted-capacity-fixture', 'owner_task': TASK, 'goal': execution_goal(TASK),
        'events': [{'facts': 'X'*1000, 'status': 'completed', 'kind': 'execution.outcome',
            'trigger': 'Owner report.'} for _ in range(2)]}
    responses = [response([belief('synthetic-owner:owner-event-'+str(event), 'X'*200,
        id='new:b'+str(i), claim='C'*400, discriminator='D'*300) for i in range(4)]) for event in (1, 2)]
    prior = run_semantic_case(case, original, transport=lambda w: responses.pop(0), review=review,
        image='unused', contract=D7)
    assert prior['episodes'][-1]['receipt']['error'] == 'context_budget_exceeded'
    from Nervous.organ import Event
    with NervousOrgan(original/'nervous') as nervous:
        nervous.publish(Event('owner-event-3', 'execution', 'host', 'execution.outcome', {'status': 'completed'}))
        nervous.complete('owner-event-3', 'host')
    for name in ('execution.jsonl', 'checkpoint.json'):
        (original/name).write_text('scripted owner restoration fixture', encoding='utf-8')
    workspace = tmp_path/'workspace'; workspace.mkdir()
    files = {name: '{}' for name in ('policy.json', 'samples.json', 'summary.json')}
    for name, content in files.items():
        (workspace/name).write_text(content, encoding='utf-8')
    prior.update(workspace=str(workspace), final_workspace=files, final_status='completed')
    (original/'result.json').write_text(json.dumps(prior), encoding='utf-8')
    (root/'acceptance/result.json').write_text(json.dumps({'sha256': digest(prior)}), encoding='utf-8')
    (root/'registration.json').write_text(json.dumps({'sha256': 'scripted-registration',
        'total_call_limit': 80, 'model': 'deepseek-v4-pro', 'endpoint': 'unused', 'candidate_limits': {}}), encoding='utf-8')
    class ReadOnlyOwner:
        def __init__(self, **kwargs):
            self.state = SimpleNamespace(goal=case['goal'], status='completed',
                execution_id='wrong-run' if wrong_run else 'synthetic-owner')
        def reality_evidence(self): return ()
        def shutdown(self): pass
    monkeypatch.setattr(Execution, 'ExecutionOrgan', ReadOnlyOwner)
    frozen = {str(p): p.read_bytes() for p in (root/'acceptance').rglob('*') if p.is_file()}
    wires = []
    def transport(wire):
        wires.append(wire)
        assert 'cognition-4' in json.dumps(wire)
        return {**response(), 'model': 'deepseek-v4-pro'}
    if wrong_run:
        with pytest.raises(ValueError, match='hard_gate_execution_run_identity'):
            run_input_continuation(root, transport=transport, review=review)
        assert not wires
        return
    result = run_input_continuation(root, transport=transport, review=review)
    assert result['receipt']['status'] == 'accepted' and result['provider_calls'] == 1
    assert result['history_prefix_unchanged'] and result['after'] == result['restarted']
    assert all(Path(name).read_bytes() == content for name, content in frozen.items())
    with pytest.raises(FileExistsError):
        run_input_continuation(root, transport=transport, review=review)
    assert len(wires) == 1
