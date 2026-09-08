"""Synthetic Builder report boundaries and historical-cache compatibility."""
import json

import pytest

from Mind.chain import Builder, Calls, BUILDER_MAX_TURNS, BUILDER_OUTPUT_TOKENS, fingerprint, write_json
from Mind.test_chain import reply
from Mind.trace import _validate_capability_observation


def calls_at(path, transport):
    return Calls(path, {'calls': BUILDER_MAX_TURNS, 'output_tokens': BUILDER_MAX_TURNS * BUILDER_OUTPUT_TOKENS,
                        'request_bytes': 1000000}, transport)


@pytest.mark.parametrize('length', [1018, 964, 917])
def test_synthetic_reports_fit_compact_budget_and_reuse_computation(tmp_path, monkeypatch, length):
    from Mind import world_model
    # Deterministic computation transport; no Docker or provider call is made.
    monkeypatch.setattr(world_model, '_compute', lambda request, *, static=False:
        json.dumps({'quantities': {'count': request['inputs']['count'] + request['action']['add']}}).encode())
    models = tmp_path / 'models'
    wires = []
    report = {'run_ref': '', 'answer': 'x' * length, 'assumptions': '', 'unknowns': ''}

    def transport(role, wire):
        assert role == 'builder'
        wires.append(wire)
        properties = next(tool['input_schema']['properties'] for tool in wire['tools'] if tool['name'] == 'report')
        assert properties['answer']['maxLength'] == 2000
        assert all(properties[field]['maxLength'] == 900 for field in ('assumptions', 'unknowns'))
        if len(wires) == 1:
            return reply('compute', {'source': 'def predict(inputs, action):\n return {"count": inputs["count"] + action["add"]}',
                'inputs': {'count': 3}, 'action': {'add': 2}})
        if len(wires) == 2:
            computed = json.loads(wire['messages'][-1]['content'][0]['content'])
            return reply('report', {**report, 'run_ref': computed['run_ref'], 'answer': 'Conditional count five.'})
        assert len(wires) == 3, 'A report below the compact-result bound consumed another Builder turn.'
        return reply('report', report)

    builder = Builder(models, calls_at(tmp_path / 'calls', transport), lambda _: 'Count three; add two.', structured=True)
    request = {'question': 'Calculate the conditional count.', 'refs': ['source'], 'model_ref': ''}
    seed = json.loads(builder.analyze('synthetic-computation', request)['text'])
    report['run_ref'] = seed['model_ref']
    model_path = models / (report['run_ref'].replace(':', '-') + '.json')
    model_bytes = model_path.read_bytes()
    observation = builder.analyze('synthetic-report', {**request, 'model_ref': report['run_ref']})
    accepted = json.loads(observation['text'])
    assert accepted['answer'] == report['answer'] and accepted['kind'] == 'CALCULATION'
    assert len(observation['text']) < 2700
    _validate_capability_observation({'capability': 'analyze_world_model', 'observation': observation},
                                     requested_capability='analyze_world_model', contract='cognitive-chain-v65')
    assert len(wires) == 3
    saved = models / (fingerprint('synthetic-report') + '.json')
    before = saved.read_bytes()
    reopened = Builder(models, calls_at(tmp_path / 'unused', lambda *_: pytest.fail('replay called the model')),
                       lambda _: pytest.fail('replay reread evidence'), structured=True)
    assert reopened.analyze('synthetic-report', {}) == observation
    assert saved.read_bytes() == before
    assert model_path.read_bytes() == model_bytes


@pytest.mark.parametrize('field,length', [('answer', 2001), ('assumptions', 901), ('unknowns', 901), ('total', 2701)])
def test_oversized_fields_and_total_still_receive_bounded_repair(tmp_path, field, length):
    value = {'run_ref': '', 'answer': 'A sourced conclusion.', 'assumptions': '', 'unknowns': ''}
    if field == 'total':
        value.update(answer='a' * 2000, assumptions='b' * 800)
    else:
        value[field] = 'x' * length
    wires = []

    def transport(_, wire):
        wires.append(wire)
        if len(wires) == 1:
            return reply('report', value)
        assert len(wires) == 2
        feedback = wire['messages'][-1]['content'][0]
        assert feedback['is_error']
        if field == 'total':
            assert 'Report exceeds 2700 characters' in feedback['content']
        else:
            error = json.loads(feedback['content'])['field_errors'][0]
            assert error['path'] == [field] and error['validator'] == 'maxLength'
        return reply('report', {'run_ref': '', 'answer': 'A concise sourced conclusion.', 'assumptions': '', 'unknowns': ''})

    observation = Builder(tmp_path / 'models', calls_at(tmp_path / 'calls', transport), lambda _: 'A source.',
                          structured=True).analyze('bounded-report', {'question': 'Assess the source.', 'refs': ['source'], 'model_ref': ''})
    assert json.loads(observation['text'])['answer'] == 'A concise sourced conclusion.'
    assert len(wires) == 2


def test_synthetic_old_version_incomplete_cache_is_returned_without_new_calls(tmp_path):
    history = {'analysis_version': 'structured-analysis-v3', 'request_ref': 'synthetic-old-analysis',
        'request': {'question': 'Assess missing evidence.', 'refs': ['source'], 'model_ref': ''},
        'observation': {'capability': 'analyze_world_model', 'origin': 'computation',
            'text': json.dumps({'kind': 'ANALYSIS_INCOMPLETE', 'answer': 'Unresolved.'})}}
    models = tmp_path / 'models'
    models.mkdir()
    saved = models / (fingerprint(history['request_ref']) + '.json')
    write_json(saved, history)
    original = saved.read_bytes()
    builder = Builder(models, calls_at(tmp_path / 'calls', lambda *_: pytest.fail('historical analysis dispatched')),
                      lambda _: pytest.fail('historical analysis reread evidence'), structured=True)
    assert builder.analyze(history['request_ref'], history['request']) == history['observation']
    assert saved.read_bytes() == original
    assert not list((tmp_path / 'calls').glob('*.json'))


def test_report_rejection_still_stops_at_six_turns_with_current_analysis_version(tmp_path):
    wires = []
    def transport(_, wire):
        wires.append(wire)
        return reply('report', {'run_ref': '', 'answer': 'x' * 2001, 'assumptions': '', 'unknowns': ''})
    observation = Builder(tmp_path / 'models', calls_at(tmp_path / 'calls', transport), lambda _: 'A source.',
                          structured=True).analyze('exhausted-report', {'question': 'Assess the source.', 'refs': ['source'], 'model_ref': ''})
    report = json.loads(observation['text'])
    assert report['kind'] == 'ANALYSIS_INCOMPLETE' and report['analysis_version'] == 'structured-analysis-v4'
    assert len(wires) == 6
