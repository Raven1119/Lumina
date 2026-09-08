"""D6 structural regressions cross the real event/request/commit/replay seam.

Scripted responses test transport and persistence, never semantic model ability.
"""
import json

import pytest

from Mind.cognitive_contract import _telemetry_evidence, run_semantic_case
from Mind.event_loop import CognitiveModel, RECOVERY_CONTRACT_VERSION, SEMANTIC_CONTRACT_VERSION, _episode, _document, citation_sources, parameter_errors, recovery_schema
from Mind.organ import (CHAIN_CONTRACT_VERSION, CHAIN_CONTRACT_VERSIONS, Evidence, MindInput, MindOrgan,
    _apply_updates, cognitive_step_schema)
from Mind.test_event_loop import reply
from Nervous.organ import NervousOrgan


def response(updates=(), next_value=None):
    # Simulate actual JSON request/response transport, including escaped Unicode.
    return json.loads(json.dumps(reply('cognitive_step', {'type': 'cognitive_step',
        'updates': list(updates), 'next': next_value or {'type': 'no_change'}}), ensure_ascii=True))


def belief(ref, quote, **changes):
    return {'kind': 'belief', 'id': 'new:literal', 'claim': 'The owner reported this literal text.',
        'status': 'supported', 'basis': [{'ref': ref, 'quote': quote}],
        'discriminator': 'A correction to that exact owner report.', **changes}


@pytest.mark.parametrize('changes,valid', [({}, True), ({'basis': []}, False),
    ({'basis': [], 'status': 'open'}, True), ({'claim': 'x'*400}, True),
    ({'claim': 'x'*401}, False), ({'discriminator': 'x'*301}, False),
    ({'claim': '  \n'}, False), ({'status': 'corrected'}, False),
    ({'id': 'new:bad label'}, False)])
def test_native_and_owner_share_active_field_contract(changes, valid):
    sources = {'owner': {'ref': 'owner', 'text': 'literal', 'origin': 'execution'}}
    update = belief('owner', 'literal', **changes)
    payload = {'type': 'cognitive_step', 'updates': [update], 'next': {'type': 'no_change'}}
    native_valid = not parameter_errors(payload, cognitive_step_schema(sources))
    try:
        _apply_updates({}, [update], sources, 'event')
        owner_valid = True
    except ValueError:
        owner_valid = False
    assert native_valid == owner_valid == valid
    if changes == {'basis': []}:
        assert not parameter_errors(payload, recovery_schema(sources))  # Frozen D4 mismatch.


@pytest.mark.parametrize('corrupt', [False, True])
@pytest.mark.parametrize('contract', CHAIN_CONTRACT_VERSIONS)
def test_literal_evidence_roundtrip_commit_reopen_and_strict_rejection(tmp_path, corrupt, contract):
    literal = '他说："你好"\nC:\\资料\\run.json'
    source_text = literal + 'x'*(798-len(literal)) + '边界截断'
    files = {name: source_text for name in ('policy.json', 'samples.json', 'summary.json')}
    evidence = _telemetry_evidence(files, 'owner:event-1')
    assert len(evidence) == 3 and all(len(e.text) <= 1000 for e in evidence)
    assert all('边界' in e.text and '截断' not in e.text for e in evidence)
    calls = []
    def transport(wire):
        wire = json.loads(json.dumps(wire, ensure_ascii=True))
        calls.append(wire)
        content = wire['messages'][-1]['content']
        if isinstance(content, list):
            content = json.loads(content[0]['content'])['continuation']
        payload = json.loads(content.split('\n\nExact citation catalogue')[0])
        sources = citation_sources(json.dumps(payload, ensure_ascii=True))
        assert sources == {e.ref: e.text for e in evidence}
        assert literal in wire['messages'][0]['content']  # Literal catalogue, not extra body encoding.
        quote = literal.replace('\\', '/') if corrupt else literal
        update = belief(evidence[0].ref, quote)
        if contract in {'cognitive-chain-v58', 'cognitive-chain-v59', 'cognitive-chain-v60', 'cognitive-chain-v61', 'cognitive-chain-v62', 'cognitive-chain-v64', 'cognitive-chain-v65', 'cognitive-chain-v66', 'cognitive-chain-v67'}:
            # These native contracts emit refs only; a missing ref is the
            # corresponding grounding defect. Historical quote checks stay below.
            update['basis'] = [{'ref': 'missing' if corrupt else evidence[0].ref}]
        return response([update])
    value = MindInput('event-1', 'Owner evidence arrived.', 'task', 1, 'Preserve literal owner observations.',
                      'run', 'completed', evidence)
    with NervousOrgan(tmp_path/'nervous') as nervous, MindOrgan(directory=tmp_path/'mind',
            model=CognitiveModel(transport, contract=contract)) as mind:
        receipt = _episode(nervous, mind, value, None)
        assert receipt.status == ('failed' if corrupt else 'accepted')
        if not (corrupt and contract in {'cognitive-chain-v58', 'cognitive-chain-v59', 'cognitive-chain-v60', 'cognitive-chain-v61', 'cognitive-chain-v62', 'cognitive-chain-v64', 'cognitive-chain-v65', 'cognitive-chain-v66', 'cognitive-chain-v67'}):
            assert receipt.error == ('ungrounded_basis' if corrupt else None)
        else:
            assert receipt.error is not None
        assert (receipt.output is None) == corrupt
        state = _document(mind.inspect())
    with MindOrgan(directory=tmp_path/'mind', model=None) as reopened:
        assert _document(reopened.inspect()) == state
        duplicate = reopened.activate(value)
        assert duplicate.status == ('failed' if corrupt else 'duplicate')
    assert len(calls) == 1
    if not corrupt and contract not in {'cognitive-chain-v58', 'cognitive-chain-v59', 'cognitive-chain-v60', 'cognitive-chain-v61', 'cognitive-chain-v62', 'cognitive-chain-v64', 'cognitive-chain-v65', 'cognitive-chain-v66', 'cognitive-chain-v67'}:
        assert state['items'][0]['basis'][0]['quote'] == literal


@pytest.mark.parametrize('contract', CHAIN_CONTRACT_VERSIONS)
def test_read_result_uses_same_logical_text_through_native_commit_and_next_event(tmp_path, contract):
    literal = '观察："已完成"\n路径 C:\\数据\\样本.json'
    observation = {'capability': 'inspect_execution', 'goal': 'Understand the owner report.',
        'status': 'completed', 'recent_outcome': literal, 'failure': None}
    calls = []
    def transport(wire):
        wire = json.loads(json.dumps(wire, ensure_ascii=True))
        calls.append(wire)
        content = wire['messages'][-1]['content']
        if isinstance(content, list):
            content = content[0]['content']
            if content.startswith('Earlier updates remain provisional'):
                content = content.split('\n', 1)[1]
            else:
                content = json.loads(content)['continuation']
        payload = json.loads(content.split('\n\nExact citation catalogue')[0])
        if len(calls) == 1:
            return response(next_value={'type': 'capability_request', 'capability': 'inspect_execution'})
        if len(calls) == 2:
            sources = citation_sources(json.dumps(payload))
            assert literal in sources['activation:observation']
            assert not any(v['properties']['type']['enum'] == ['capability_request']
                for v in wire['tools'][0]['input_schema']['properties']['next']['oneOf'])
            update = belief('activation:observation', literal)
            if contract in {'cognitive-chain-v58', 'cognitive-chain-v59', 'cognitive-chain-v60', 'cognitive-chain-v61', 'cognitive-chain-v62', 'cognitive-chain-v64', 'cognitive-chain-v65', 'cognitive-chain-v66', 'cognitive-chain-v67'}:
                update['basis'] = [{'ref': 'activation:observation'}]
            return response([update])
        assert payload['cognition']['items'][0]['basis'][0]['ref'].startswith('activation-')
        # Full-checkpoint contracts reaffirm current knowledge without new evidence.
        return response(payload['cognition']['items'] if contract in {'cognitive-chain-v20', 'cognitive-chain-v49', 'cognitive-chain-v51', 'cognitive-chain-v53', 'cognitive-chain-v55', 'cognitive-chain-v61', 'cognitive-chain-v62', 'cognitive-chain-v64', 'cognitive-chain-v65'} else ())
    model = CognitiveModel(transport, contract=contract)
    first = MindInput('e1', 'Owner report is available for inspection.', 'task', 1, observation['goal'], 'run', 'completed')
    with NervousOrgan(tmp_path/'nervous') as nervous, MindOrgan(directory=tmp_path/'mind', model=model,
            available_capabilities=('inspect_execution',)) as mind:
        assert _episode(nervous, mind, first, observation).status == 'accepted'
        before = _document(mind.inspect())
        receipt_event, = nervous.pending('host'); nervous.complete(receipt_event.event_id, 'host')
    second = MindInput('e2', 'The owner confirms nothing changed.', 'task', 1, observation['goal'], 'run', 'completed',
                       (Evidence('owner:e2', 'No new condition or observation.', 'execution'),))
    with NervousOrgan(tmp_path/'nervous') as nervous, MindOrgan(directory=tmp_path/'mind', model=model,
            available_capabilities=('inspect_execution',)) as mind:
        assert _document(mind.inspect()) == before
        assert _episode(nervous, mind, second, observation).status == 'accepted'
        assert _document(mind.inspect())['items'] == before['items']
    assert len(calls) == 3


def test_latest_evaluation_accepts_correct_state_without_a_fresh_citation(tmp_path):
    case = {'id': 'unchanged', 'goal': 'Maintain the complete handoff.', 'seed': {
        'items': [belief('prior', 'Delivery is complete.')],
        'evidence': [{'ref': 'prior', 'text': 'Delivery is complete.', 'origin': 'execution'}]},
        'events': [{'facts': 'No changed condition.', 'kind': 'execution.outcome', 'status': 'completed',
            'trigger': 'Owner recheck.'}]}
    from Mind.test_semantic_revision import review
    result = run_semantic_case(case, tmp_path, transport=lambda w: response(), review=review,
        image='unused', contract=CHAIN_CONTRACT_VERSION)
    assert result['passed'] and not result['final_owner_source_used']
    assert result['final_view']['items'] == result['seed']['view']['items']


def synthetic_contract_calls(directory, contract):
    """Exercise an old contract with generated input, never an archived provider log."""
    from Mind.organ import MindResultEvent
    from Mind.test_event_loop import input_value
    answers = iter([response(next_value={'type': 'capability_request', 'capability': 'inspect_execution'}),
                    response([belief('source', 'Only settled entries count.')])])
    model = CognitiveModel(lambda wire: next(answers), contract=contract)
    with MindOrgan(directory=directory, model=model, available_capabilities=('inspect_execution',)) as mind:
        waiting = mind.activate(input_value())
        assert waiting.status == 'waiting' and mind.inspect().revision == 0
        observed = {'capability': 'inspect_execution', 'goal': input_value().goal,
                    'status': 'running', 'recent_outcome': 'A synthetic owner scope check.', 'failure': None}
        assert mind.accept_result(MindResultEvent(waiting.request.request_ref, observed)).status == 'accepted'
        assert mind.inspect().revision == 1
    before = {p.name: p.read_bytes() for p in directory.glob('*.jsonl')}
    with MindOrgan(directory=directory, model=None) as mind:
        assert mind.inspect().revision == 1
    assert {p.name: p.read_bytes() for p in directory.glob('*.jsonl')} == before
    assert len(model.calls) == 2
    return model.calls


@pytest.mark.parametrize('contract', [RECOVERY_CONTRACT_VERSION, SEMANTIC_CONTRACT_VERSION])
def test_synthetic_d4_and_d5_requests_are_deterministically_reconstructible(tmp_path, contract):
    for call in synthetic_contract_calls(tmp_path, contract):
        assert CognitiveModel(None, contract=contract)._prepare_call(**call['projection'])['wire'] == call['wire']


def test_synthetic_d6_v1_requests_are_deterministically_reconstructible(tmp_path):
    for call in synthetic_contract_calls(tmp_path, CHAIN_CONTRACT_VERSION):
        assert CognitiveModel(None, contract=CHAIN_CONTRACT_VERSION)._prepare_call(**call['projection'])['wire'] == call['wire']


def test_d6_stops_after_unknown_provider_outcome_before_next_event(tmp_path):
    case = {'id': 'unknown', 'goal': 'Preserve the active goal.', 'events': [
        {'facts': 'Owner observation.', 'kind': 'execution.outcome', 'status': 'waiting', 'trigger': 'First event.'},
        {'facts': 'Later observation.', 'kind': 'execution.outcome', 'status': 'completed', 'trigger': 'Second event.'}]}
    calls = []
    def timeout(wire):
        calls.append(wire)
        raise TimeoutError('dispatched response unknown')
    with pytest.raises(ValueError, match='hard_gate_unknown_provider_outcome'):
        run_semantic_case(case, tmp_path, transport=timeout, review=lambda r: pytest.fail('No semantic result exists'),
            image='unused', contract=CHAIN_CONTRACT_VERSION)
    assert len(calls) == 1
    journal = json.loads((tmp_path/'mind/cognition.json').read_text(encoding='utf-8'))
    assert not any(r['event_id'] == 'cognition-2' for r in journal['records'])


def test_d6_stage_stops_physical_calls_after_unknown_outcome(tmp_path):
    from Mind.cognitive_contract import _start_stage, digest
    registration = {'version': 'cognitive-chain-d6-registration-v1', 'model': 'deepseek-v4-pro'}
    registration['sha256'] = digest(registration)
    (tmp_path/'registration.json').write_text(json.dumps(registration), encoding='utf-8')
    attempts = []
    def timeout(wire):
        attempts.append(wire)
        raise TimeoutError('response unknown')
    _, _, recorded, calls = _start_stage(tmp_path, 'test', 3, timeout,
                                        source_files=('Mind/test_cognitive_chain.py',))
    wire = {'model': 'deepseek-v4-pro', 'temperature': 0,
            'thinking': {'type': 'disabled'}, 'max_tokens': 2000}
    with pytest.raises(TimeoutError):
        recorded(wire)
    with pytest.raises(ValueError, match='hard_gate_unknown_provider_outcome'):
        recorded(wire)
    assert len(attempts) == len(calls) == 1
    assert calls[0]['error_type'] == 'TimeoutError'
