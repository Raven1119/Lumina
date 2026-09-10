"""Current analysis contract, calculation provenance and crash recovery.

Responses and isolated-compute output are scripted here; these tests establish
mechanics, not the semantic ability of the runtime model.
"""
import json
from collections import Counter

import pytest

from Mind import analysis, world_model as wm
from Mind.analysis import Analysis
from Nervous.provider import ProviderCalls
from Nervous.storage import fingerprint, read_json, write_json


def reply(name, value):
    return {'content': [{'type': 'tool_use', 'id': 'call-1', 'name': name, 'input': value}],
            'stop_reason': 'tool_use', 'usage': {'input_tokens': 1, 'output_tokens': 1}}


def request():
    return {'question': 'Compare the specified candidate using the supplied rule.',
            'refs': ['rule'], 'model_ref': ''}


def report(ref=''):
    return {'run_ref': ref, 'answer': 'Conditional result from supplied inputs.',
            'assumptions': 'The supplied rule applies.', 'unknowns': 'No later observation supplied.'}


def ledger(tmp_path, transport, calls=6):
    return ProviderCalls(tmp_path / 'calls',
        {'calls': calls, 'output_tokens': calls * 16384, 'request_bytes': calls * 150000}, transport)


def test_analysis_claims_global_response_when_local_turn_write_was_interrupted(tmp_path, monkeypatch):
    class Crash(BaseException):
        pass
    calls = ledger(tmp_path, lambda *_: reply('report', report()), calls=1)
    original_write = analysis.write_json
    def interrupted(path, value):
        if '.turn-' in path.name and 'response' in value:
            raise Crash()
        original_write(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(analysis, 'write_json', interrupted)
        owner = Analysis(tmp_path / 'models', calls, lambda _: 'Original rule.')
        with pytest.raises(Crash):
            owner.analyze('request-1', request())
    before = calls.summary()
    calls = ledger(tmp_path, lambda *_: pytest.fail('Known analysis sampled twice'), calls=1)
    owner = Analysis(tmp_path / 'models', calls, lambda _: pytest.fail('Recovery reread an already frozen source'))
    result = json.loads(owner.analyze('request-1', request())['text'])
    assert result['answer'] == report()['answer']
    assert calls.summary() == before


def static_value(**extra):
    return {'source': 'def predict(inputs, action): return {"bytes": inputs["bytes"] + action["extra"]}',
            'inputs': {'bytes': 12}, 'action': {'extra': 3}, **extra}


def check_spec():
    return {'conditions': {'source_bytes': 12}, 'object': 'selected artifact', 'when': 'after generation',
            'quantities': {'bytes': {'meaning': 'complete artifact size', 'unit': 'byte'}}}


@pytest.mark.parametrize('prospective', [False, True])
def test_computation_report_preserves_scope_and_declared_observation(tmp_path, monkeypatch, prospective):
    monkeypatch.setattr(wm, '_compute', lambda *args, **kwargs: json.dumps({'quantities': {'bytes': 15}}))
    counts = Counter()
    def transport(role, wire):
        counts[role] += 1
        assert role == 'builder'
        context = json.loads(wire['messages'][0]['content'])
        assert set(context) == {'question', 'evidence', 'prior_model', 'owner_task'}
        assert context['evidence'] == [{'ref': 'rule', 'text': 'The supplied size is twelve bytes.'}]
        assert {t['name'] for t in wire['tools']} == {'compute', 'report'}
        if counts[role] == 1:
            extra = {'observation_file': 'result.json', 'check_spec': check_spec()} if prospective else {}
            return reply('compute', static_value(**extra))
        result = json.loads(wire['messages'][-1]['content'][0]['content'])
        return reply('report', report(result['run_ref']))
    calls = ledger(tmp_path, transport)
    owner = Analysis(tmp_path / 'models', calls, lambda ref: 'The supplied size is twelve bytes.')
    observation = owner.analyze('request-1', request())
    value = json.loads(observation['text'])
    artifact = owner.model(value['model_ref'])
    assert observation['origin'] == 'computation'
    assert value['kind'] == ('COMPUTED_CONDITIONAL' if prospective else 'CALCULATION')
    assert artifact['run']['request']['action'] == {'extra': 3}
    assert artifact['evidence_refs'] == ['rule']
    assert 'def predict' not in observation['text']
    if prospective:
        assert value['check_spec'] == check_spec()
        comparison = wm.compare_observation_contract(artifact['run'], value['check_spec'], None)
        assert comparison['status'] == 'unverified'
    else:
        assert value['result'] == {'quantities': {'bytes': 15}}
        assert 'check_spec' not in artifact and 'observation_file' not in artifact
    assert owner.analyze('request-1', request()) == observation
    assert counts == {'builder': 2}
    with pytest.raises(ValueError, match='analysis_request_identity_conflict'):
        owner.analyze('request-1', {**request(), 'question': 'Different question'})


def test_analysis_can_report_missing_evidence_without_computation(tmp_path):
    calls = ledger(tmp_path, lambda role, wire: reply('report', {
        'run_ref': '', 'answer': 'The available source gives no measurement.',
        'assumptions': '', 'unknowns': 'A measured size is needed to compare candidates.'}))
    owner = Analysis(tmp_path / 'models', calls, lambda ref: 'Measurement is pending.')
    value = json.loads(owner.analyze('request-1', request())['text'])
    assert value['kind'] == 'MODEL_ANALYSIS' and value['model_ref'] == ''
    assert value['evidence_refs'] == ['rule']
    assert not list((tmp_path / 'models').glob('model-*.json'))


def test_stateful_computation_retains_its_own_observation_contract(tmp_path, monkeypatch):
    prediction = {'initial': {'observation': {'bytes': 12}, 'outcome': 'ongoing'},
                  'steps': [{'observation': {'bytes': 15}, 'outcome': 'complete'}]}
    monkeypatch.setattr(wm, '_compute', lambda *args, **kwargs: json.dumps(prediction))
    count = 0
    def transport(role, wire):
        nonlocal count
        count += 1
        if count == 1:
            return reply('compute', {'source': 'state transition model', 'initial_observation': {'bytes': 12},
                'actions': [{'extra': 3}], 'observation_file': 'result.json',
                'check_spec': {**check_spec(), 'action': {'extra': 3}}})
        result = json.loads(wire['messages'][-1]['content'][0]['content'])
        assert result['initialization_check']['status'] == 'matched'
        return reply('report', report(result['run_ref']))
    owner = Analysis(tmp_path / 'models', ledger(tmp_path, transport), lambda ref: 'Twelve bytes initially.')
    value = json.loads(owner.analyze('request-1', request())['text'])
    assert value['kind'] == 'COMPUTED_CONDITIONAL'
    artifact = owner.model(value['model_ref'])
    assert artifact['run']['protocol'] == wm.PROTOCOL
    assert artifact['check_spec']['action'] == {'extra': 3}
    assert value['prediction'] == prediction['steps'][-1]
    assert count == 2


def test_resume_reuses_response_computation_and_thinking_blocks(tmp_path, monkeypatch):
    counts = Counter()
    def compute(*args, **kwargs):
        counts['compute'] += 1
        return json.dumps({'quantities': {'bytes': 15}})
    monkeypatch.setattr(wm, '_compute', compute)
    def transport(role, wire):
        counts['provider'] += 1
        if counts['provider'] == 1:
            result = reply('compute', static_value())
            result['content'].insert(0, {'type': 'thinking', 'thinking': 'private', 'signature': 'signature'})
            return result
        assert wire['messages'][-2]['content'][0]['type'] == 'thinking'
        result = json.loads(wire['messages'][-1]['content'][0]['content'])
        return reply('report', report(result['run_ref']))
    calls = ledger(tmp_path, transport)
    destination = tmp_path / 'models' / (fingerprint('request-1') + '.json')
    class Crash(BaseException):
        pass
    original = analysis.write_json
    def crash_on_commit(path, value):
        if path == destination:
            raise Crash()
        original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(analysis, 'write_json', crash_on_commit)
        with pytest.raises(Crash):
            Analysis(tmp_path / 'models', calls, lambda ref: 'Twelve bytes.').analyze('request-1', request())
    owner = Analysis(tmp_path / 'models', calls, lambda ref: 'Twelve bytes.')
    observation = owner.analyze('request-1', request())
    assert json.loads(observation['text'])['kind'] == 'CALCULATION'
    assert counts == {'provider': 2, 'compute': 1}
    assert 'private' not in observation['text']


@pytest.mark.parametrize('error', ['container_cleanup_failed', 'container_output_incomplete'])
def test_isolation_failure_stays_stopped_after_restart(tmp_path, error):
    counts = Counter()
    def transport(role, wire):
        counts['provider'] += 1
        return reply('compute', static_value())
    def broken(*args):
        counts['compute'] += 1
        raise wm.ModelComputationError(error)
    calls = ledger(tmp_path, transport)
    for _ in range(2):
        with pytest.raises(RuntimeError, match=error):
            Analysis(tmp_path / 'models', calls, lambda ref: 'Twelve bytes.', static_compute=broken).analyze(
                'request-1', request())
    assert counts == {'provider': 1, 'compute': 1}


def test_unconfirmed_provider_call_is_not_repeated(tmp_path):
    counts = Counter()
    def transport(role, wire):
        counts['provider'] += 1
        raise ConnectionError('unknown outcome')
    calls = ledger(tmp_path, transport)
    owner = Analysis(tmp_path / 'models', calls, lambda ref: 'Twelve bytes.')
    with pytest.raises(ConnectionError):
        owner.analyze('request-1', request())
    with pytest.raises(RuntimeError, match='builder_call_outcome_unknown'):
        Analysis(tmp_path / 'models', calls, lambda ref: 'Twelve bytes.').analyze('request-1', request())
    assert counts == {'provider': 1}


def test_invalid_reports_exhaust_one_durable_activity(tmp_path):
    calls = ledger(tmp_path, lambda role, wire: reply('report', report('invented-model-ref')))
    owner = Analysis(tmp_path / 'models', calls, lambda ref: 'Twelve bytes.')
    observation = owner.analyze('request-1', request())
    assert json.loads(observation['text'])['kind'] == 'ANALYSIS_INCOMPLETE'
    assert calls.summary()['calls'] == 6
    assert Analysis(tmp_path / 'models', calls, lambda ref: 'Twelve bytes.').analyze('request-1', request()) == observation
    assert calls.summary()['calls'] == 6


@pytest.mark.parametrize('field', ['answer', 'assumptions', 'unknowns', 'total'])
def test_report_field_and_combined_bounds_return_explicit_feedback(tmp_path, field):
    oversized = report()
    if field == 'total':
        oversized.update(answer='a' * 2000, assumptions='b' * 800)
    else:
        oversized[field] = 'x' * (2001 if field == 'answer' else 901)
    count = 0
    def transport(role, wire):
        nonlocal count
        count += 1
        if count == 1:
            return reply('report', oversized)
        feedback = wire['messages'][-1]['content'][0]
        assert feedback['is_error']
        if field == 'total':
            assert 'Report exceeds 2700 characters' in feedback['content']
        else:
            error = json.loads(feedback['content'])['field_errors'][0]
            assert error['path'] == [field] and error['validator'] == 'maxLength'
        # A legal report need not obey historical, shorter 900/1000 character caps.
        return reply('report', {**report(), 'answer': 'c' * 1100})
    value = json.loads(Analysis(tmp_path / 'models', ledger(tmp_path, transport),
        lambda ref: 'Supplied source.').analyze('request-1', request())['text'])
    assert value['answer'] == 'c' * 1100
    assert count == 2


def test_compute_batch_validates_every_member_before_execution_and_shares_budget(tmp_path, monkeypatch):
    counts = Counter()
    def compute(*args, **kwargs):
        counts['compute'] += 1
        return json.dumps({'quantities': {'bytes': 15}})
    monkeypatch.setattr(wm, '_compute', compute)
    def batch(size, invalid=False):
        blocks = []
        for index in range(size):
            block = reply('compute', static_value())['content'][0]
            block['id'] = f'compute-{index}'
            if invalid and index == size - 1:
                block['input']['source'] = 'x' * 16001
            blocks.append(block)
        return {'content': blocks, 'stop_reason': 'tool_use'}
    def transport(role, wire):
        counts['provider'] += 1
        turn = counts['provider']
        if turn == 1:
            return batch(2, invalid=True)
        if turn == 2:
            assert counts['compute'] == 0
            assert all(result['is_error'] for result in wire['messages'][-1]['content'])
            return batch(3)
        if turn == 3:
            assert counts['compute'] == 3
            return batch(3)
        if turn == 4:
            assert counts['compute'] == 6
            return batch(1)
        assert counts['compute'] == 6
        assert 'Computation budget: 0 remain' in wire['messages'][-1]['content'][0]['content']
        selected = wire['tools'][1]['input_schema']['properties']['run_ref']['enum'][-1]
        return reply('report', report(selected))
    calls = ledger(tmp_path, transport)
    observation = Analysis(tmp_path / 'models', calls, lambda ref: 'Twelve bytes.').analyze('request-1', request())
    assert json.loads(observation['text'])['kind'] == 'CALCULATION'
    assert counts == {'provider': 5, 'compute': 6}
    assert len(list((tmp_path / 'models').glob('model-*.json'))) == 6


def test_model_artifact_requires_current_integrity_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(wm, '_compute', lambda *args, **kwargs: json.dumps({'quantities': {'bytes': 15}}))
    owner = Analysis(tmp_path / 'models', None, None)
    ref = 'model:' + 'a' * 32
    path = tmp_path / 'models' / (ref.replace(':', '-') + '.json')
    value = static_value()
    artifact = {'ref': ref, 'run': wm.run_static_model(value['source'], value['inputs'], value['action'])}
    write_json(path, artifact)
    with pytest.raises(ValueError, match='model_artifact_integrity_failure'):
        owner.model(ref)
    artifact['artifact_sha256'] = fingerprint(artifact)
    write_json(path, artifact)
    assert owner.model(ref) == artifact
    tampered = read_json(path)
    tampered['run']['prediction']['quantities']['bytes'] = 16
    write_json(path, tampered)
    with pytest.raises(ValueError, match='model_artifact_integrity_failure'):
        owner.model(ref)
