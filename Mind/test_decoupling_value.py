"""D1 behavioral checks through the experiment's public seams."""
import json
from pathlib import Path
import pytest

from Mind.decoupling_value import score_result


def test_marker_cannot_certify_an_incorrect_deliverable(tmp_path):
    (tmp_path / '.lumina-complete').write_text('verified', encoding='utf-8')
    (tmp_path / 'answer.json').write_text('{"total":true}', encoding='utf-8')
    assert score_result(tmp_path, {"total": 1}) is False
    (tmp_path / 'answer.json').write_text('{"total":1}', encoding='utf-8')
    assert score_result(tmp_path, {"total": 1}) is True


def test_real_wire_carries_advice_and_blocks_external_actions():
    from Execution.execution import ModelRequest, NativeToolContinuation
    from Mind.decoupling_value import ExecutionAdapter
    seen = []
    replies = iter([
        {"content": [{"type": "tool_use", "id": "c2", "name": "ipython",
          "input": {"code": "open('answer.json', 'w').write('{}')"}}], "usage": {}},
        {"content": [{"type": "tool_use", "id": "c3", "name": "ipython",
          "input": {"code": "open('../oracle.json').read()"}}], "usage": {}},
    ])
    def transport(wire):
        seen.append(wire)
        return next(replies)
    model = ExecutionAdapter(transport)
    old = {"choices": [{"message": {"content": None, "tool_calls": [{"id": "c1",
           "type": "function", "function": {"name": "ipython", "arguments": '{"code":"print(1)"}'}}]}}]}
    request = ModelRequest(json.dumps({"observation": {"text": "1"},
        "mind_supervisor_directive": "CHECK THE CAPACITY REQUIREMENT"}),
        ("ipython(code: str)", "wait(event_type: str)", "claim_complete()"), (),
        NativeToolContinuation('{"goal":"test"}', old, 'c1'), 16000)
    decision = model.decide(request)
    assert decision.failure is None
    wire = seen[0]
    assert wire['temperature'] == 0 and wire['thinking'] == {"type": "disabled"}
    assert 'CHECK THE CAPACITY REQUIREMENT' in json.dumps(wire)
    assert any(block.get('tool_use_id') == 'c1' for message in wire['messages']
               if isinstance(message['content'], list) for block in message['content'])
    denied = model.decide(request)
    assert denied.action is None and denied.failure == 'experiment_authority_rejected'


def test_admission_rejects_path_and_import_alias_escapes():
    from Execution.execution import ModelRequest
    from Mind.decoupling_value import ExecutionAdapter
    payloads = [
        "from pathlib import os as json; print(json.environ)",
        "from pathlib import Path; p=Path('task.json').parent/'../oracle.json'; print(p.read_text())",
        "from pathlib import Path; p=Path('task.json'); p.write_text('{}')",
        "from pathlib import Path; Path('answer.json').replace('../outside.json')",
        "from pathlib import Path; (Path('task.json')/'../oracle.json').read_text()",
        "print = eval; print(\"open('../oracle.json').read()\")",
        "print = exec; print(\"import os; print(os.environ)\")",
        "print = globals; print()",
        "sorted(['../oracle.json'], key=open)",
        "match open:\n case print:\n  print('../oracle.json').read()",
        "f=eval; eval=1; sorted([\"open('../oracle.json').read()\"], key=f)",
        "if False:\n get_ipython=1\nf=get_ipython; sorted(['x'], key=f)",
        "for x in [1]:\n break\nelse:\n eval=1\nf=eval\nsorted(['x'],key=f)",
    ]
    request = ModelRequest('{"goal":"test"}', ('ipython(code: str)',), ())
    for code in payloads:
        model = ExecutionAdapter(lambda wire: {'content': [{'type': 'tool_use', 'id': 'attempt',
                                  'name': 'ipython', 'input': {'code': code}}]})
        result = model.decide(request)
        assert result.action is None and result.failure == 'experiment_authority_rejected'


@pytest.mark.parametrize('allow_directive', [True, False])
def test_matched_checkpoint_review_delivery_and_objective_continuation(tmp_path, allow_directive):
    from Mind.decoupling_value import run_case
    case = {'id': 'synthetic-check', 'family': 'constraint', 'needs_correction': True,
            'task': {'requirement': 'Choose the certified route.', 'data': [
                {'id': 'fast', 'certified': False}, {'id': 'safe', 'certified': True}],
                'output': {'route': 'chosen route ID'}},
            'draft': {'route': 'fast'}, 'assumption': 'The fastest route is sufficient.',
            'expected': {'route': 'safe'}}
    def review(output):
        return lambda wire: {'content': [{'type': 'text', 'text': json.dumps({
            'type': 'cognitive_step', 'updates': [], 'next': output})}],
            'usage': {'input_tokens': 100, 'output_tokens': 30}}
    def execution(answer):
        replies = iter([
            {'content': [{'type': 'tool_use', 'id': 'write', 'name': 'ipython',
              'input': {'code': "from pathlib import Path\nPath('answer.json').write_text("
                  + repr(json.dumps(answer)) + ")\nPath('.lumina-complete').write_text('verified')"}}]},
            {'content': [{'type': 'tool_use', 'id': 'done', 'name': 'claim_complete', 'input': {}}]},
        ])
        return lambda wire: {**next(replies), 'usage': {'input_tokens': 100, 'output_tokens': 30}}
    result = run_case(case, tmp_path / 'case', review_transports={
        'A': review({'type': 'no_change'}),
        'B': review({'type': 'directive', 'text': 'The current direction overlooks the certification requirement.'})},
        execution_transports={'A': execution(case['draft']), 'B': execution(case['expected'])},
        admit_review=lambda request: {'allowed': allow_directive if request['output']['type'] == 'directive' else True,
                                     'reason': 'Scripted admission decision.', 'reviewer': 'test'})
    assert result['paired_start_equal']
    a, b = result['arms']['A'], result['arms']['B']
    assert a['review_projection'] == b['review_projection']
    assert len(a['review_wire']['messages']) > len(b['review_wire']['messages'])
    assert a['review_wire']['tool_choice'] == b['review_wire']['tool_choice'] == {'type': 'none'}
    assert a['review_wire']['tools'] == b['review_wire']['tools']
    assert a['objective_success'] is False and b['objective_success'] is True
    assert a['marker_completed'] is True and b['marker_completed'] is True
    assert a['delivered'] is False and b['delivered'] is allow_directive
    assert all('certification requirement' not in json.dumps(wire)
               for wire in b['execution_wires'][1:])
    assert b['owner_outcome'][-1]['completion_verified'] is True
    assert b['admission']['allowed'] is allow_directive


def test_campaign_freeze_is_single_use_and_cannot_hide_a_ceiling(tmp_path):
    import pytest
    from Mind.decoupling_value import preregister, run_campaign, summarize
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{"schema":"mind-decoupling-d1-v1","cases":[]}', encoding='utf-8')
    with pytest.raises(ValueError, match='twelve_cases_required'):
        preregister(manifest, tmp_path / 'invalid')
    frozen = preregister(Path(__file__).parent / 'fixtures/decoupling_d1/manifest.json', tmp_path / 'registered')
    assert frozen['limits']['review_calls_per_arm'] == 1
    started = tmp_path / 'registered' / 'campaign-started.json'
    started.write_text('{}', encoding='utf-8')
    calls = []
    with pytest.raises(FileExistsError):
        run_campaign(tmp_path / 'registered', transport=lambda request: calls.append(request))
    assert calls == []
    arms = {arm: {'objective_success': True, 'directive_issued': False, 'delivered': False,
                  'review_status': 'accepted', 'boundary_ok': True, 'task_unchanged': True,
                  'authority_rejections': [], 'failure': None,
                  'admission': {'allowed': True},
                  'review_response': {'usage': {'input_tokens': 100, 'output_tokens': 20}},
                  'execution_responses': [{'usage': {'input_tokens': 100, 'output_tokens': 20}}],
                  'repeated_exact_actions': 0, 'execution_calls': 1}
            for arm in ('A', 'B')}
    records = [{'family': 'test', 'needs_correction': i < 8, 'paired_start_equal': True,
                'arms': arms} for i in range(12)]
    result = summarize(records)
    assert result['verdict'] == 'MIND_DECOUPLING_VALUE_NOT_SUPPORTED'
    assert result['successes'] == {'A': 12, 'B': 12}
    # Actual savings are outcomes. A control preserved by NoChange is a win.
    import copy
    mixed = [copy.deepcopy(record) for record in records]
    trace_audit = {}
    for i, record in enumerate(mixed):
        record['case_id'] = str(i)
        record['family'] = 'first' if i % 2 else 'second'
        b = record['arms']['B']
        b['review_response']['usage'] = {'input_tokens': 1, 'output_tokens': 1}
        b['execution_responses'][0]['usage'] = {'input_tokens': 1, 'output_tokens': 1}
        if i < 5 or i == 8:
            record['arms']['A']['objective_success'] = False
            if i < 5:
                b['directive_issued'] = b['delivered'] = True
                trace_audit[str(i)] = {'B_rescue_adopted_relevant': True}
            else:
                trace_audit[str(i)] = {'B_control_preserved': True}
    supported = summarize(mixed, trace_audit)
    assert supported['verdict'] == 'MIND_DECOUPLING_VALUE_SUPPORTED'
    assert supported['total_token_ratio_B_over_A'] < .1
    arms['B']['authority_rejections'] = ['external_path']
    assert summarize(records)['verdict'] == 'INCONCLUSIVE'


def test_scripted_campaign_retains_controls_and_never_resamples(tmp_path):
    import pytest
    from Mind.decoupling_value import preregister, run_campaign
    directory = tmp_path / 'campaign'
    preregister(Path(__file__).parent / 'fixtures/decoupling_d1/manifest.json', directory)
    calls = []
    execution_calls = 0
    def transport(wire):
        nonlocal execution_calls
        calls.append(wire)
        if wire.get('tool_choice') == {'type': 'none'}:
            content = [{'type': 'text', 'text': '{"type":"cognitive_step","updates":[],"next":{"type":"no_change"}}'}]
        else:
            execution_calls += 1
            if execution_calls % 2:
                content = [{'type': 'tool_use', 'id': str(execution_calls), 'name': 'ipython',
                            'input': {'code': "open('.lumina-complete', 'w').write('verified')"}}]
            else:
                content = [{'type': 'tool_use', 'id': str(execution_calls), 'name': 'claim_complete', 'input': {}}]
        return {'content': content, 'usage': {'input_tokens': 100, 'output_tokens': 20}}
    result = run_campaign(directory, transport=transport,
        admit_review=lambda request: {'allowed': True, 'reason': 'Known scripted NoChange.', 'reviewer': 'test'})
    assert result['provider_calls'] == len(calls) == 72
    assert result['summary']['successes'] == {'A': 4, 'B': 4}
    assert result['summary']['verdict'] == 'MIND_DECOUPLING_VALUE_NOT_SUPPORTED'
    assert all(arm['marker_completed'] for record in result['records'] for arm in record['arms'].values())
    with pytest.raises(FileExistsError):
        run_campaign(directory, transport=transport)
    assert len(calls) == 72
