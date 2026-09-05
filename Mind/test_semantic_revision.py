"""D5 mechanics; scripted responses are never evidence of model semantics."""
import json
import os
from pathlib import Path

import pytest

from Mind.cognitive_contract import SEMANTIC_FIELDS, run_semantic_case
from Mind.event_loop import CognitiveModel, RECOVERY_CONTRACT_VERSION, SEMANTIC_CONTRACT_VERSION, IMAGE_TAG
from Mind.organ import MindOrgan
from Mind.test_event_loop import reply, step


def stripped(value):
    if isinstance(value, dict):
        return {k: stripped(v) for k, v in value.items() if k != 'description'}
    return [stripped(v) for v in value] if isinstance(value, list) else value


def test_old_d4_wire_is_unchanged_and_d5_only_changes_semantic_instructions():
    old = json.loads(Path('Mind/fixtures/protocol_recovery_d4/loop/license_change/result.json').read_text(encoding='utf-8'))
    for call in old['cognition_calls']:
        d4 = CognitiveModel(None, contract=RECOVERY_CONTRACT_VERSION)._prepare_call(**call['projection'])['wire']
        assert d4 == call['wire']
        d5 = CognitiveModel(None, contract=SEMANTIC_CONTRACT_VERSION)._prepare_call(**call['projection'])['wire']
        assert d5['messages'] == d4['messages']
        assert stripped(d5['tools']) == stripped(d4['tools'])
        assert {k: v for k, v in d5.items() if k not in {'tools', 'system'}} == {
            k: v for k, v in d4.items() if k not in {'tools', 'system'}}
        assert d5['system'] != d4['system']


def review(request):
    return {**dict.fromkeys(('allowed', 'claims_supported', 'direction_relevant', 'uncertainty_preserved',
                            'behavior_consistent', *SEMANTIC_FIELDS), True),
            'reason': 'SCRIPTED mechanics only; not semantic evidence.'}


def test_seed_correction_reopens_full_content_and_keeps_history_with_d4_repair(tmp_path):
    case = {'id': 'scoped-rule', 'goal': 'Keep the current rule scoped.',
        'seed': {'items': [{'kind': 'belief', 'claim': 'Every mode requires a supervisor.', 'status': 'supported',
            'basis': [{'ref': 'prior', 'quote': 'Review mode requires a supervisor.'}],
            'discriminator': 'No supervisor means every mode is invalid.'}],
            'evidence': [{'ref': 'prior', 'text': 'Review mode requires a supervisor.', 'origin': 'execution'}]},
        'events': [{'trigger': 'A scoped rule arrived.', 'kind': 'execution.input_changed', 'status': 'waiting',
            'facts': 'Only review mode requires a supervisor. Independent mode does not.'},
            {'trigger': 'Owner result arrived.', 'kind': 'execution.outcome', 'status': 'completed',
             'facts': 'Independent mode completed without a supervisor.'}]}
    wires = []
    def transport(wire):
        wires.append(wire)
        if len(wires) == 1:
            return reply('cognitive_step', {})
        payload = json.loads(wire['messages'][0]['content'].split('\n\nExact citation catalogue')[0])
        item = payload['cognition']['items'][0]
        source = payload['cognition']['evidence'][-1]
        corrected = {**item, 'claim': 'Only review mode requires a supervisor; independent mode does not.',
            'status': 'supported', 'discriminator': 'An authoritative rule changing the requirement within either mode.',
            'basis': [{'ref': source['ref'], 'quote': source['text']}]}
        return step({'type': 'no_change'}, [corrected])
    audit = []
    def recording_review(request):
        audit.append(request)
        return review(request)
    record = run_semantic_case(case, tmp_path, transport=transport, review=recording_review, image=IMAGE_TAG)
    assert record['passed'] and len(wires) == 3
    assert record['seed']['provenance'].startswith('SCRIPTED')
    assert record['episodes'][0]['reopened_view'] == record['seed']['view']
    assert record['episodes'][1]['reopened_view'] == record['episodes'][0]['view']
    with MindOrgan(directory=tmp_path / 'mind', model=None) as mind:
        item, = mind.inspect().items
        assert item['status'] == 'supported' and item['claim'].startswith('Only review mode')
        assert item['discriminator'] == 'An authoritative rule changing the requirement within either mode.'
    journal = (tmp_path / 'mind/cognition.json').read_text(encoding='utf-8')
    assert 'Every mode requires a supervisor.' in journal and 'Only review mode requires' in journal
    assert audit[-1]['items'] == record['final_view']['items']
    assert audit[0]['cognitive_revision']['prior_items'] == record['seed']['view']['items']
    assert all('expected_output' not in str(w['messages']) and 'condition_correct' not in str(w['messages']) for w in wires)


def test_semantic_rejection_does_not_erase_structurally_accepted_error(tmp_path):
    case = {'id': 'retained-error', 'goal': 'Assess scoped rules.', 'events': [
        {'facts': 'Only local mode accepts unsigned input.', 'trigger': 'Rule arrived.',
         'kind': 'execution.outcome', 'status': 'completed'}]}
    def transport(wire):
        payload = json.loads(wire['messages'][0]['content'].split('\n\nExact citation catalogue')[0])
        ref = payload['cognition']['evidence'][0]['ref']
        return step({'type': 'no_change'}, [{'kind': 'belief', 'id': 'new:bad', 'claim': 'All modes accept unsigned input.',
            'status': 'supported', 'basis': [{'ref': ref, 'quote': 'Only local mode accepts unsigned input.'}],
            'discriminator': 'Unsigned input accepted.'}])
    record = run_semantic_case(case, tmp_path, transport=transport,
        review=lambda r: {**review(r), 'condition_correct': False}, image=IMAGE_TAG)
    assert not record['passed']
    assert record['episodes'][0]['receipt']['status'] == 'accepted'
    assert record['final_view']['items'][0]['claim'] == 'All modes accept unsigned input.'


def test_literal_task_projection_preserves_archived_quotes_without_rewriting_them():
    from Mind.cognitive_contract import _telemetry_facts
    from Mind.event_loop import canonical
    record = json.loads(Path('Mind/fixtures/semantic_revision_d5/acceptance/net_summary_revision/result.json')
                        .read_text(encoding='utf-8'))
    staged = {**record['initial_workspace'], **{name: canonical(value)
        for name, value in record['case']['workspace']['changed_files'].items()}}
    repaired_quotes = 0
    for index, files in ((1, staged), (2, record['final_workspace'])):
        old = record['episodes'][index]
        assert old['receipt']['error'] == 'ungrounded_basis'
        facts = _telemetry_facts({**files, 'helper.py': 'PRIVATE_CODE', 'debug.log': 'PRIVATE_LOG'}, 'waiting')
        assert 'PRIVATE_CODE' not in facts and 'PRIVATE_LOG' not in facts
        raw = record['cognition_calls'][index]['response']['content'][0]['input']
        for item in raw['updates']:
            for basis in item.get('basis', []):
                if basis['ref'] == old['source_ref']:
                    assert basis['quote'] in facts
                    repaired_quotes += basis['quote'] not in old['source_facts']
    assert repaired_quotes == 3
    bounded = _telemetry_facts({'policy.json': 'x' * 801}, 'waiting')
    assert 'x' * 801 not in bounded and '[truncated;' in bounded


@pytest.mark.skipif(os.environ.get('LUMINA_D2_DOCKER') != '1', reason='Explicit Docker validation only')
@pytest.mark.parametrize('rejected_field', [None, 'direction_relevant', 'uncertainty_preserved', 'tampered_input'])
def test_d5_real_execution_bridge_and_whole_state_feedback(tmp_path, rejected_field):
    case = {'id': 'telemetry-mechanics', 'goal': 'Prepare the telemetry summary; await source_update.',
        'workspace': {'initial_files': {'policy.json': {'revision': 1, 'mode': 'positive_only'},
            'samples.json': {'revision': 1, 'samples': [{'value': 5, 'approved': True}, {'value': -2, 'approved': True}]}},
            'changed_files': {'policy.json': {'revision': 2, 'mode': 'net'}}},
        'events': [{'kind': 'execution.outcome', 'trigger': 'Initial result.'},
                   {'kind': 'execution.input_changed', 'trigger': 'New rule.'},
                   {'kind': 'execution.outcome', 'trigger': 'Final result.'}]}
    first = "from pathlib import Path\nPath('summary.json').write_text('{\"policy_revision\":1,\"sample_revision\":1,\"count\":1,\"total\":5}')"
    first += "\nPath('helper.py').write_text('PRIVATE_EXECUTION_TRAJECTORY')\nPath('debug.log').write_text('PRIVATE_EXECUTION_TRAJECTORY')"
    if rejected_field == 'tampered_input':
        first += "\nPath('policy.json').write_text('{\"revision\":1,\"mode\":\"net\"}')"
    last = "from pathlib import Path\nPath('summary.json').write_text('{\"policy_revision\":2,\"sample_revision\":1,\"count\":2,\"total\":3}')\nPath('.lumina-complete').write_text('verified')"
    actions = iter([reply('ipython', {'code': first}), reply('wait', {'event_type': 'source_update'}),
                    reply('ipython', {'code': last}), reply('claim_complete', {})])
    def transport(wire):
        if wire['tools'][0]['name'] != 'cognitive_step':
            return next(actions)
        assert 'PRIVATE_EXECUTION_TRAJECTORY' not in str(wire['messages'])
        data = json.loads(wire['messages'][0]['content'].split('\n\nExact citation catalogue')[0])['cognition']
        source = data['evidence'][-1]
        update = {'kind': 'belief', 'id': data['items'][0]['id'] if data['items'] else 'new:rule',
            'claim': 'The summary follows the currently observed aggregation mode.', 'status': 'supported',
            'basis': [{'ref': source['ref'], 'quote': 'Observed task files:'}],
            'discriminator': 'A summary that differs from the current mode.'}
        output = {'type': 'directive', 'text': 'Reassess the summary under the current aggregation mode.'} if data['event_id'] == 'cognition-2' else {'type': 'no_change'}
        return step(output, [update])
    if rejected_field == 'tampered_input':
        with pytest.raises(ValueError, match='hard_gate_execution_input_authority'):
            run_semantic_case(case, tmp_path, transport=transport, review=review, image=IMAGE_TAG)
        assert not json.loads((tmp_path / 'result.json').read_text())['episodes']
        return
    def gate(request):
        return {**review(request), **({rejected_field: False} if rejected_field else {})}
    record = run_semantic_case(case, tmp_path, transport=transport, review=gate, image=IMAGE_TAG)
    assert record['passed'] == (rejected_field is None)
    assert record['episodes'][1]['delivered'] == (rejected_field is None)
    assert record['objective_success'] and record['final_owner_source_used']
    assert record['reopened_equal'] and record['final_view']['revision'] == 3
    events = json.loads((tmp_path / 'nervous/events.json').read_text(encoding='utf-8'))['state']['events']
    assert next(e for e in events if e['event_id'] == 'owner-event-3')['causation_id'] == 'wake'
