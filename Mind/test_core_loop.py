"""Fresh end-to-end transport/action smoke, not a model-ability experiment.

Scripted native responses exercise the supported CLI composition. With
LUMINA_TEST_CORE_DOCKER=1, both Python action and model computation run in the
real isolated backends; no live provider calls or historical fixtures are used.
"""
from collections import Counter
from contextlib import ExitStack
import csv
import hashlib
import io
import json
import os

import pytest

from Execution.model import EXECUTION_PROTOCOL
from Execution.runtime import Execution
from Mind import world_model
from Mind.organ import MindOrgan
from Nervous.organ import NervousOrgan
from Nervous.storage import canonical


def native(name, value):
    return {'stop_reason': 'tool_use', 'content': [
        {'type': 'tool_use', 'id': 'call', 'name': name, 'input': value}],
        'usage': {'input_tokens': 10, 'output_tokens': 10}}


def source_records(wire):
    records = {}
    for message in wire['messages']:
        bodies = [message['content']] if isinstance(message['content'], str) else [
            b.get('content', b.get('text', '')) for b in message['content']]
        for body in bodies:
            if not isinstance(body, str):
                continue
            try:
                value = json.loads(body[body.index('{'):])
            except (ValueError, TypeError):
                continue
            for item in value.get('source_records', []):
                records[item['ref']] = item
    return list(records.values())


def start_organs(directory, workspace, transport, goal=None):
    stack = ExitStack()
    try:
        nervous = NervousOrgan(directory / 'nervous', transport=transport,
            limits={'calls': 20, 'output_tokens': 250000, 'request_bytes': 2800000})
        stack.callback(nervous.close)
        mind = MindOrgan(directory / 'mind', nervous.calls, goal=goal,
                         execution_protocol=EXECUTION_PROTOCOL)
        stack.callback(mind.close)
        execution = Execution(directory / 'execution', nervous.calls, workspace=workspace)
        stack.callback(execution.close)
        return stack, nervous, mind, execution
    except BaseException:
        stack.close()
        raise


def test_fresh_nochange_has_no_action_and_quiet_restart(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'decision.txt').write_text('No action is authorized; record understanding only.', encoding='utf-8')
    goal = 'Read the current instruction; this request authorizes no workspace action.'
    calls = []
    def transport(role, wire):
        calls.append(role)
        assert role == 'mind'
        source = next(x for x in source_records(wire) if 'No action is authorized' in x['text'])
        return native('cognitive_step', {'type': 'cognitive_step', 'updates': [
            {'kind': 'belief', 'id': 'new:scope', 'claim': 'This request authorizes understanding only.',
             'status': 'supported', 'basis': [{'ref': source['ref']}]}], 'next': {'type': 'no_change'}})
    directory = tmp_path / 'state'
    stack, nervous, mind, execution = start_organs(directory, workspace, transport, goal)
    with stack:
        nervous.submit(goal, 'USER_GOAL')
        result = nervous.run(mind, execution)
        assert result['mind']['revision'] == 1
        assert not result['mind']['unresolved']
        assert result['execution']['execution_ref'] is None
        prior = result['mind']['items']
    stack, nervous, mind, execution = start_organs(directory, None, transport)
    with stack:
        result = nervous.run(mind, execution)
        assert result['mind']['items'] == prior
        assert result['execution']['execution_ref'] is None
        assert not any(result['pending'].values())
    assert calls == ['mind']


@pytest.mark.parametrize('observation_exists', [False, True])
@pytest.mark.parametrize('unread_observation', [False, True])
@pytest.mark.parametrize('prior_review', [False, True])
def test_prediction_without_execution_run_reassesses_external_change_after_restart(
        tmp_path, monkeypatch, observation_exists, unread_observation, prior_review):
    workspace, directory = tmp_path / 'workspace', tmp_path / 'state'
    workspace.mkdir()
    rule = 'The external producer adds three bytes to the supplied twelve-byte input.'
    (workspace / 'rule.txt').write_text(rule, encoding='utf-8')
    observation_path = workspace / 'observation.json'
    observation = {'extra': 3, 'input_bytes': 12, 'observation_object': 'external artifact',
                   'observation_time': 'after generation', 'bytes': 15}
    if unread_observation:
        observation['detail'] = 'x' * 26000
    if prior_review:
        observation_path.write_text(json.dumps({**observation, 'bytes': 6}), encoding='utf-8')
    elif observation_exists:
        observation_path.write_text(json.dumps({**observation, 'bytes': 9}), encoding='utf-8')
    goal = 'Predict the external artifact size and review later observations; no workspace action is authorized.'
    counts, computations, observed_refs = Counter(), [], []

    def compute(*args, **kwargs):
        computations.append(args)
        return json.dumps({'quantities': {'bytes': 15}})

    monkeypatch.setattr(world_model, '_compute', compute)

    def transport(role, wire):
        counts[role] += 1
        if role == 'mind':
            records = source_records(wire)
            if prior_review and counts[role] == 1:
                return native('cognitive_step', {'type': 'cognitive_step', 'updates': [],
                                                'next': {'type': 'no_change'}})
            if counts[role] == 1 + prior_review:
                source = next(x for x in records if x['text'] == rule)
                return native('analyze_world_model', {'refs': [source['ref']], 'model_ref': '',
                    'observation_file': 'observation.json',
                    'question': 'Predict the external artifact size under the supplied rule and declare a later observation check.'})
            if counts[role] >= 3 + prior_review:
                assert counts[role] == 2 + prior_review + len(observed_refs), 'An unchanged observation must not repeat the review.'
                if unread_observation:
                    assert 'observation_source_not_read' in canonical(wire)
                    assert all(x['text'] != json.dumps(observation) for x in records)
                else:
                    actual = next(x for x in records if x['text'] == json.dumps(observation))
                    assert actual['ref'] == observed_refs[-1]
            else:
                assert counts[role] == 2 + prior_review
            return native('cognitive_step', {'type': 'cognitive_step', 'updates': [],
                                            'next': {'type': 'no_change'}})
        assert role == 'builder', 'No execution model call is authorized by NoChange.'
        if counts[role] == 1:
            return native('compute', {
                'source': 'def predict(inputs, action): return {"bytes": inputs["bytes"] + action["extra"]}',
                'inputs': {'bytes': 12}, 'action': {'extra': 3}, 'observation_file': 'observation.json',
                'check_spec': {'conditions': {'input_bytes': 12}, 'object': 'external artifact',
                    'when': 'after generation',
                    'quantities': {'bytes': {'meaning': 'complete artifact size', 'unit': 'byte'}}}})
        assert counts[role] == 2
        result = json.loads(wire['messages'][-1]['content'][0]['content'])
        return native('report', {'run_ref': result['run_ref'], 'answer': 'The conditional artifact size is fifteen bytes.',
            'assumptions': 'The external producer uses the supplied input and rule.',
            'unknowns': 'A later observation is needed.'})

    stack, nervous, mind, execution = start_organs(directory, workspace, transport, goal)
    with stack:
        nervous.submit(goal, 'USER_GOAL')
        if prior_review:
            earlier = nervous.run(mind, execution)
            assert earlier['mind']['revision'] == 1 and counts == {'mind': 1}
            assert not execution.state['predictions'] and execution.actor is None
            if observation_exists:
                observation_path.write_text(json.dumps({**observation, 'bytes': 9}), encoding='utf-8')
            else:
                observation_path.unlink()
            nervous.submit('The observation baseline has changed. Predict the later external artifact size.')
        first = nervous.run(mind, execution)
        assert counts == {'mind': 2 + prior_review, 'builder': 2}, 'Registering a watch must not announce an unchanged baseline.'
        assert first['mind']['revision'] == 1 + prior_review and not first['mind']['unresolved']
        assert first['execution']['execution_ref'] is None and execution.actor is None
        assert not any(first['pending'].values())
        assert len(computations) == 1
        prediction = execution.state['predictions'][0]
        prediction_ref = prediction['ref']
        assert prediction_ref in mind.state['predictions'] and prediction['execution_ref'] is None
        assert bool(prediction['before_observation_ref']) == observation_exists
        assert not execution.state['deliveries']

    resumed, event_ids, review_ids = first, set(), set()
    for index, quantity in enumerate((15, 12, 15, 12)):
        stack, nervous, mind, execution = start_organs(directory, None, transport)
        with stack:
            unchanged = nervous.run(mind, execution)
            assert unchanged['cost'] == resumed['cost']
            assert counts == {'mind': 2 + prior_review + index, 'builder': 2}
            observation['bytes'] = quantity
            observation_path.write_text(json.dumps(observation), encoding='utf-8')
            events = execution.poll()
            assert len(events) == 1, 'Every later observation change needs a new review, even when old content returns.'
            assert execution.poll() == events
            event_ids.add(events[0].event_id)
            observed_refs.append(next(x['ref'] for x in events[0].data['snapshot']['files']
                                      if x['file'] == 'observation.json'))

        stack, nervous, mind, execution = start_organs(directory, None, transport)
        with stack:
            assert execution.poll() == events  # The unpublished outbox survives restart.
            nervous.publish(events[0])
            execution.published(events[0].event_id)
            assert execution.poll() == ()  # Pending transport must not duplicate its review.
            resumed = nervous.run(mind, execution)
            assert counts == {'mind': 3 + prior_review + index, 'builder': 2}, resumed
            assert resumed['mind']['revision'] == 2 + prior_review + index and not resumed['mind']['unresolved']
            assert resumed['execution']['execution_ref'] is None and execution.actor is None
            assert not any(resumed['pending'].values())
            if unread_observation:
                assert 'reviewed_source' not in mind.state['predictions'][prediction_ref]
                assert 'reviewed_source' not in execution.state['predictions'][0]
                assert observed_refs[-1] not in mind.state['sources']
                assert resumed['execution']['feedback_pending']['predictions'] == [prediction_ref]
            else:
                assert mind.state['predictions'][prediction_ref]['reviewed_source'] == observed_refs[-1]
                assert execution.state['predictions'][0]['reviewed_source'] == observed_refs[-1]
            review_ids.add(execution.state['last_reviewed']['activity_id'])
            comparisons = [json.loads(mind.source_record(ref)['text']) for ref in mind.state['sources']
                           if mind.source_info(ref).get('label') == 'prediction comparison']
            comparison = next(x for x in comparisons if x['reality_source'] == observed_refs[-1])
            if unread_observation:
                assert comparison['alignment']['reason'] == 'observation_source_not_read'
                assert comparison['alignment']['comparison'] is None
            else:
                assert comparison['alignment']['comparison']['status'] == ('matched' if quantity == 15 else 'mismatch')
            assert len(computations) == 1 and not execution.state['deliveries']
            settled = canonical(mind.status())
            assert nervous.run(mind, execution)['cost'] == resumed['cost']

    stack, nervous, mind, execution = start_organs(directory, None, transport)
    with stack:
        quiet = nervous.run(mind, execution)
        assert quiet['cost'] == resumed['cost'] and canonical(mind.status()) == settled
        assert not any(quiet['pending'].values()) and not execution.state['outbox']
    assert counts == {'mind': 6 + prior_review, 'builder': 2} and len(computations) == 1
    assert len(event_ids) == len(review_ids) == 4
    assert observed_refs[0] == observed_refs[2] != observed_refs[1] == observed_refs[3]


@pytest.mark.skipif(os.environ.get('LUMINA_TEST_CORE_DOCKER') != '1',
                    reason='explicit isolated whole-loop Docker smoke')
def test_unicode_csv_prediction_action_feedback_and_restart(tmp_path):
    workspace, directory = tmp_path / 'workspace', tmp_path / 'state'
    workspace.mkdir()
    rows = [['name', 'note'], ['Ada', 'commas, remain quoted'],
            ['Lin', ('release caf\u00e9 / \u6570\u636e; ' * 2) + '\nsecond line']]
    raw = json.dumps(rows, ensure_ascii=False, indent=64)
    (workspace / 'data.json').write_bytes(raw.encode('utf-8'))
    goal = ('Export data.json as release.csv using UTF-8 without BOM and LF record endings, '
            'preserving commas and embedded line breaks. First predict the encoded byte length '
            'with the independent analysis capability, then measure the actual export and report '
            'its SHA256 and input identity in observation.json. Review those actual results.')
    counts, wires = Counter(), []
    data_ref = None
    guidance = ('Export the supplied records as CSV with UTF-8 and LF record endings. '
                'Preserve embedded delimiters and newlines. Compare the actual output byte length '
                'with the conditional estimate for this same input and report the observed identity.')
    def transport(role, wire):
        nonlocal data_ref
        counts[role] += 1
        wires.append((role, wire))
        if role == 'mind':
            records = source_records(wire)
            if counts[role] == 1:
                catalogue = next(json.loads(x['text']) for x in records
                    if x['text'].startswith('[{') and '"file"' in x['text'])
                data_ref = next(x['ref'] for x in catalogue if x['file'] == 'data.json')
                return native('read_evidence', {'refs': [data_ref]})
            if counts[role] == 2:
                assert any(x['ref'] == data_ref and x['text'] == raw for x in records)
                return native('analyze_world_model', {'refs': [data_ref], 'model_ref': '',
                    'observation_file': 'observation.json',
                    'question': 'Predict encoded CSV byte length for UTF-8 and LF while preserving quoted values. Declare how to compare the later actual export.'})
            if counts[role] == 3:
                return native('cognitive_step', {'type': 'cognitive_step', 'updates': [
                    {'kind': 'belief', 'id': 'new:input', 'claim': 'The source includes delimiters, Unicode and an embedded line break.',
                     'status': 'supported', 'basis': [{'ref': data_ref}]}],
                    'next': {'type': 'directive', 'text': guidance}})
            actual = next((x for x in records if '"observation_object":"release.csv"' in x['text']
                           or '"observation_object": "release.csv"' in x['text']), None)
            assert actual, 'Feedback must include actual observation, not only a forecast or directory.'
            return native('cognitive_step', {'type': 'cognitive_step', 'updates': [
                {'kind': 'belief', 'id': 'new:measured', 'claim': 'The exported CSV was measured with the declared encoding and input identity.',
                 'status': 'supported', 'basis': [{'ref': actual['ref']}]}], 'next': {'type': 'no_change'}})
        if role == 'builder':
            if counts[role] == 1:
                context = json.loads(wire['messages'][0]['content'])
                selected = context['evidence'][0]['text']
                assert selected == raw and 'received_guidance' not in context
                return native('compute', {
                    'source': 'import csv,io,json\ndef predict(inputs, action):\n    out=io.StringIO(newline="")\n    csv.writer(out,lineterminator="\\n").writerows(json.loads(inputs["rows_json"]))\n    return {"bytes":len(out.getvalue().encode("utf-8"))}',
                    'inputs': {'rows_json': json.dumps(json.loads(selected), ensure_ascii=False)},
                    'action': {'export_format': 'csv-utf8-lf'},
                    'observation_file': 'observation.json',
                    'check_spec': {'conditions': {'input_sha256': hashlib.sha256(selected.encode()).hexdigest()},
                        'object': 'release.csv', 'when': 'after export',
                        'quantities': {'bytes': {'meaning': 'complete CSV file size', 'unit': 'byte'}}}})
            result = json.loads(wire['messages'][-1]['content'][0]['content'])
            return native('report', {'run_ref': result['run_ref'],
                'answer': 'Computed byte length for the selected records using UTF-8 CSV with LF endings.',
                'assumptions': 'The export uses these same records and encoding.', 'unknowns': 'Actual output remains to be measured.'})
        assert role == 'execution'
        document = json.loads(wire['messages'][0]['content'][0]['text'])
        assert 'source' not in document or 'def predict' not in canonical(document)
        if counts[role] == 1:
            assert guidance in canonical(wire)
            return native('ipython', {'code': """import csv,hashlib,json
from pathlib import Path
source=Path('data.json').read_bytes()
rows=json.loads(source.decode('utf-8'))
with Path('release.csv').open('w',encoding='utf-8',newline='') as output:
    csv.writer(output,lineterminator='\\n').writerows(rows)
actual=Path('release.csv').read_bytes()
observation={'export_format':'csv-utf8-lf','input_sha256':hashlib.sha256(source).hexdigest(),
 'observation_object':'release.csv','observation_time':'after export',
 'bytes':len(actual),'sha256':hashlib.sha256(actual).hexdigest()}
Path('observation.json').write_text(json.dumps(observation),encoding='utf-8')
Path('.lumina-complete').write_text('done',encoding='utf-8')
request_mind('Export completed and measured. Review the actual observation against the declared forecast.',
 evidence_files=('observation.json',))
"""})
        return native('claim_complete', {})
    stack, nervous, mind, execution = start_organs(directory, workspace, transport, goal)
    with stack:
        nervous.submit(goal, 'USER_GOAL')
        first = nervous.run(mind, execution, max_steps=3)
        assert counts == {'mind': 1}
        assert first['mind']['active'] and first['pending']['execution']
        assert not (workspace / 'release.csv').exists()
    stack, nervous, mind, execution = start_organs(directory, None, transport)
    with stack:
        result = nervous.run(mind, execution)
        assert result['execution']['status'] == 'completed', result
        assert result['mind']['revision'] == 2 and not result['mind']['unresolved'], result
        assert not any(result['pending'].values())
        assert len(mind.state['predictions']) == 1
        prediction = next(iter(mind.state['predictions'].values()))
        assert prediction['reviewed_source']
        reports = [mind.source_record(ref) for ref in mind.state['sources']
                   if mind.source_info(ref).get('label') == 'prediction comparison']
        assert json.loads(reports[-1]['text'])['alignment']['comparison']['status'] == 'matched'
        delivery = execution.state['deliveries']
        assert len(delivery) == 1 and delivery[0]['text'] == guidance
        before = canonical(mind.status())
        cost = result['cost']
    with (workspace / 'release.csv').open(encoding='utf-8', newline='') as stream:
        assert list(csv.reader(stream)) == rows
    observation = json.loads((workspace / 'observation.json').read_text())
    output = (workspace / 'release.csv').read_bytes()
    assert observation['bytes'] == len(output)
    assert observation['sha256'] == hashlib.sha256(output).hexdigest()
    assert b'\r\n' not in output and not output.startswith(b'\xef\xbb\xbf')
    stack, nervous, mind, execution = start_organs(directory, None, transport)
    with stack:
        quiet = nervous.run(mind, execution)
        assert quiet['cost'] == cost and canonical(mind.status()) == before
        assert quiet['execution']['status'] == 'completed'
    assert counts == {'mind': 4, 'builder': 2, 'execution': 2}
