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
