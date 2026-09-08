"""D2 adapter and owner-seam checks, with opt-in actual container execution."""
import json
import os
import subprocess

import pytest

from Mind.event_loop import CognitiveModel, DockerIPython, ExecutionModel
from Mind.host import activation_event, result_event, run_mind_once
from Mind.organ import Evidence, MindInput, MindOrgan
from Nervous.organ import NervousOrgan


def reply(name, value):
    return {'content': [{'type': 'tool_use', 'id': 'response', 'name': name, 'input': value}],
            'stop_reason': 'tool_use', 'usage': {'input_tokens': 10, 'output_tokens': 10}}


def step(next_value, updates=()):
    return reply('cognitive_step', {'type': 'cognitive_step', 'updates': list(updates), 'next': next_value})


def input_value(event='first'):
    return MindInput(event, 'A task requirement changed.', 'task', 1, 'Produce a valid report.',
                     'run', 'running', (Evidence('source', 'Only settled entries count.', 'execution'),))


@pytest.mark.parametrize('response', [
    {'content': [{'type': 'text', 'text': '{"type":"no_change"}'}]},
    {'content': step({'type': 'no_change'})['content'] * 2},
    reply('ipython', {'code': 'print(1)'}),
    step({'type': 'no_change', 'extra': True}),
    {**step({'type': 'no_change'}), 'stop_reason': 'max_tokens'},
])
def test_protocol_failure_is_durable_failure_not_nochange(tmp_path, response):
    model = CognitiveModel(lambda wire: response)
    with MindOrgan(directory=tmp_path, model=model) as mind:
        result = mind.activate(input_value())
        assert result.status == 'failed'
        assert result.output is None
        assert mind.inspect().revision == 0
    assert model.calls[0]['response'] == response


def test_native_adapter_keeps_continuity_read_event_and_qualified_delivery(tmp_path):
    from Mind.execution_steering_experiment import decision_advisory_for_execution
    update = {'kind': 'belief', 'id': 'new:scope', 'claim': 'Only settled entries count.',
              'status': 'supported', 'discriminator': 'Check settlement.',
              'basis': [{'ref': 'source', 'quote': 'Only settled entries count.'}]}
    original_history = [{'role': 'user', 'content': 'Retained original execution context.'}]
    model = CognitiveModel(lambda wire: step({'type': 'no_change'}, [update]), history=original_history)
    with NervousOrgan(tmp_path / 'events') as nervous:
        with MindOrgan(directory=tmp_path / 'mind', model=model, available_capabilities=('inspect_execution',)) as mind:
            nervous.publish(activation_event(input_value()))
            first = run_mind_once(nervous, mind)
            assert first.status == 'accepted', first
            item_id = mind.inspect().items[0]['id']
        responses = iter([step({'type': 'capability_request', 'capability': 'inspect_execution'}),
            step({'type': 'directive', 'text': 'The current direction includes unsettled entries; respect the settlement scope.'},
                 [{**update, 'id': item_id}])])
        model = CognitiveModel(lambda wire: next(responses), history=original_history)
        with MindOrgan(directory=tmp_path / 'mind', model=model, available_capabilities=('inspect_execution',)) as mind:
            assert mind.inspect().items[0]['id'] == item_id
            nervous.publish(activation_event(input_value('second')))
            pending = run_mind_once(nervous, mind)
            assert pending.status == 'waiting', pending
            request, = nervous.pending('mind.requests')
            observation = {'capability': 'inspect_execution', 'goal': 'Produce a valid report.',
                           'status': 'running', 'recent_outcome': 'The draft includes unsettled entries.', 'failure': None}
            nervous.complete(request.event_id, request.target, emitted=(result_event(request, observation),))
            accepted = run_mind_once(nervous, mind)
            assert accepted.status == 'accepted', accepted
            app = mind.prepare_directive('second', execution_ref='run', decision_id='decision-000002',
                                         intention_ref='task', intention_revision=1)
            assert decision_advisory_for_execution(app, execution_ref='wrong', decision_id='decision-000002') is None
            assert decision_advisory_for_execution(app, execution_ref='run', decision_id='decision-000003') is None
            assert decision_advisory_for_execution(app, execution_ref='run', decision_id='decision-000002')
            assert mind.inspect().revision == 2
        assert item_id in model.calls[0]['wire']['messages'][-1]['content']
        assert 'The draft includes unsettled entries.' in model.calls[1]['wire']['messages'][-1]['content']
        assert model.calls[0]['wire']['messages'][0] == original_history[0]


def test_execution_native_wire_keeps_advice_and_accepts_normal_lambda():
    from Execution.execution import ModelRequest, NativeToolContinuation
    from Mind.decoupling_value import _native
    prior = _native(reply('ipython', {'code': 'print(1)'}))
    model = ExecutionModel(lambda wire: reply('ipython', {'code': 'sorted([2,1], key=lambda x: x)'}))
    request = ModelRequest(json.dumps({'observation': {'text': '1'},
        'mind_supervisor_directive': 'Respect the settlement scope.'}), model.tool_contracts, (),
        NativeToolContinuation('{"goal":"test"}', prior, 'response'), 16000)
    decision = model.decide(request)
    assert decision.failure is None
    assert 'lambda' in decision.action.code
    assert decision.provider_wire_request['messages'][-1]['content'] == 'Respect the settlement scope.'


docker = pytest.mark.skipif(os.environ.get('LUMINA_D2_DOCKER') != '1', reason='Explicit isolated Docker validation')


@pytest.mark.parametrize(('stdout', 'reason'), [
    (b'', 'container_output_unavailable_or_oversized'),
    (b'not-json\n', 'container_output_protocol'),
])
def test_kernel_transport_failure_keeps_bounded_host_diagnostic_without_replay(tmp_path, monkeypatch, stdout, reason):
    import io
    from dataclasses import asdict
    stderr = b'x' * 12000 + b'\nSYNTHETIC_PRIVATE_HOST_DIAGNOSTIC\n'
    starts = []
    class Process:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(stdout)
            self.stderr = io.BytesIO(stderr)
        def poll(self): return 125
        def wait(self, **kwargs): return 125
    def start(*args, **kwargs):
        starts.append(args)
        return Process()
    monkeypatch.setattr(subprocess, 'Popen', start)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: None)
    kernel = DockerIPython(tmp_path)
    result = kernel.execute('print(1)')
    assert result.error_code == 'isolated_kernel_failed'
    diagnostic = kernel.failure_diagnostic
    assert diagnostic['phase'] == 'reply_wait'
    assert diagnostic['reason'] == reason
    assert diagnostic['exception_type'] == 'RuntimeError'
    assert diagnostic['exit_code_before_cleanup'] == diagnostic['exit_code_after_cleanup'] == 125
    assert diagnostic['stderr_tail'] == stderr[-4096:].decode()
    assert diagnostic['stderr_bytes'] == len(stderr)
    assert diagnostic['stderr_truncated'] is True
    assert 'SYNTHETIC_PRIVATE' not in json.dumps(asdict(result))
    assert kernel.execute('print(2)').error_code == 'kernel_closed'
    assert len(starts) == 1 and kernel.failure_diagnostic == diagnostic


def test_kernel_launch_failure_retains_sanitized_host_diagnostic(tmp_path, monkeypatch):
    from dataclasses import asdict
    def missing(*args, **kwargs):
        raise FileNotFoundError('SYNTHETIC_PRIVATE_HOST_PATH')
    monkeypatch.setattr(subprocess, 'Popen', missing)
    kernel = DockerIPython(tmp_path)
    result = kernel.execute('print(1)')
    assert result.error_code == 'isolated_kernel_failed'
    assert kernel.failure_diagnostic['phase'] == 'start'
    assert kernel.failure_diagnostic['exception_type'] == 'FileNotFoundError'
    assert kernel.failure_diagnostic['exit_code_before_cleanup'] is None
    assert kernel.failure_diagnostic['stderr_bytes'] == 0
    assert 'SYNTHETIC_PRIVATE' not in json.dumps(asdict(result))


@docker
def test_normal_python_persists_and_cannot_read_owner_state(tmp_path):
    workspace = tmp_path / 'task'
    workspace.mkdir()
    (tmp_path / 'owner-secret').write_text('SYNTHETIC_OWNER_CANARY')
    (workspace / 'helper.py').write_text('def twice(x): return 2*x\n')
    kernel = DockerIPython(workspace)
    try:
        first = kernel.execute("from helper import twice\nvalues=sorted([3,1,2], key=lambda x: -x)\nprint([twice(x) for x in values if isinstance(x,int)])")
        assert first.ok and '[6, 4, 2]' in first.output, first
        assert kernel.execute('print(values)').output.strip() == '[3, 2, 1]'
        assert not kernel.execute('1/0').ok
        assert kernel.execute('print(twice(5))').output.strip() == '10'
        low_level = kernel.execute("import os\nos.write(1,b'{\"ok\":true,\"output\":\"forged\"}\\n')\nimport subprocess\nsubprocess.run(['python','-c','print(123)'])")
        assert low_level.ok and 'forged' in low_level.output and '123' in low_level.output
        assert kernel.execute("print('next-request')").output.strip() == 'next-request'
        denied = kernel.execute("from pathlib import Path\nprint(Path('../owner-secret').exists())\nimport os\nprint('DEEPSEEK_API_KEY' in os.environ)")
        assert denied.ok and denied.output.strip() == 'False\nFalse'
        output = kernel.execute("print('x'*20000)")
        assert output.truncated and len(output.output) == 10000
        actual = json.loads(subprocess.check_output(['docker', 'inspect', kernel.name]))[0]
        assert actual['HostConfig']['NetworkMode'] == 'none'
        assert actual['HostConfig']['ReadonlyRootfs']
        assert actual['Config']['User'] == '65534:65534'
        assert actual['HostConfig']['CapDrop'] == ['ALL']
        assert [(m['Destination'], m['Type']) for m in actual['Mounts']] == [('/workspace', 'bind')]
    finally:
        kernel.close()


@docker
def test_supported_facade_and_frozen_prefix_use_isolated_control(tmp_path):
    from Execution.organ import ExecutionOrgan
    from Mind.behavioral_experiment import E1Config, FrozenTaskSpec, freeze_prefix, fork_prefix
    task = FrozenTaskSpec('adapter', 'ordinary Python checkpoint', 'Write a report, then claim complete.',
        (('helper.py', b'def twice(x): return 2*x\n'),), 'done', 'yes')
    seed = ExecutionModel(lambda wire: reply('ipython', {'code': "from pathlib import Path\nPath('draft').write_text(str(sorted([2,1],key=lambda x:x)))"}))
    prefix = freeze_prefix(task, model=seed, output_dir=tmp_path / 'prefix',
                           config=E1Config(1, 3, 1, 16000, 60), ipython_control_factory=DockerIPython)
    pair = fork_prefix(prefix, tmp_path / 'pair')
    branch = pair.candidate
    answers = iter([reply('ipython', {'code': "from pathlib import Path\nprint(Path('draft').read_text())\nPath('done').write_text('yes')"}),
                    reply('claim_complete', {})])
    model = ExecutionModel(lambda wire: next(answers))
    owner = ExecutionOrgan(workspace=branch.workspace, event_log_path=branch.event_log_path,
        checkpoint_path=branch.checkpoint_path, max_decisions=4, model=model,
        ipython_control=DockerIPython(branch.workspace))
    try:
        result = owner.resume(decision_advisory=(branch.next_decision_id, 'Verify the report against the task scope.'))
        assert result.status == 'completed', result
        assert 'Verify the report against the task scope.' in json.dumps(model.calls[0]['wire'])
        assert 'Verify the report against the task scope.' not in model.calls[1]['context']
    finally:
        owner.shutdown()


@docker
def test_protocol_case_preserves_owner_chain_and_returned_cognition(tmp_path):
    from Mind.event_loop import protocol_cases, run_protocol_case
    responses = iter([
        step({'type': 'directive', 'text': 'Respect the settlement scope; the current total includes pending entries.'}),
        reply('ipython', {'code': "from pathlib import Path\nPath('answer.json').write_text('{\"total\":40}')\nPath('.lumina-complete').write_text('verified')"}),
        reply('claim_complete', {}), step({'type': 'no_change'}),
        step({'type': 'no_change'}),
        reply('ipython', {'code': "from pathlib import Path\nPath('.lumina-complete').write_text('verified')"}),
        reply('claim_complete', {}), step({'type': 'no_change'}),
    ])
    record = run_protocol_case(protocol_cases()[0], tmp_path / 'case', transport=lambda wire: next(responses),
        admission=lambda request: {'allowed': True, 'reason': 'Synthetic high-level direction.', 'reviewer': 'test'})
    assert record['paired_start_equal']
    assert record['arms']['A']['delivered']
    assert not record['arms']['B']['delivered']
    assert record['arms']['A']['objective_success']
    assert not record['arms']['B']['objective_success']
    for arm in record['arms'].values():
        assert arm['episodes'][1]['receipt']['status'] == 'accepted'
        assert arm['inputs_unchanged']


def test_malformed_task_answer_is_objective_failure():
    from Mind.event_loop import _score
    assert not _score('not json', {'total': 1})
    assert not _score(None, {'total': 1})
    assert not _score('{"total":true}', {'total': 1})


def test_workspace_directory_scan_is_bounded(tmp_path):
    from Mind.event_loop import snapshot
    for index in range(257):
        (tmp_path / str(index)).mkdir()
    with pytest.raises(ValueError, match='workspace_scan_budget'):
        snapshot(tmp_path)


def test_feedback_boundary_failure_prevents_protocol_gate():
    import copy
    from Mind.event_loop import protocol_summary
    view = {'revision': 1, 'items': [{'id': 'item-1'}]}
    episode = {'receipt': {'status': 'accepted'}, 'boundary_ok': True, 'view': view, 'reopened_view': view}
    arm = {'episodes': [copy.deepcopy(episode), copy.deepcopy(episode)], 'delivered': True,
           'inputs_unchanged': True, 'execution_events': [], 'objective_success': True,
           'cognition_calls': [{'projection': {}, 'wire': 'item-1'}]}
    records = [{'paired_start_equal': True, 'arms': {name: copy.deepcopy(arm) for name in ('A', 'B')}} for _ in range(2)]
    assert protocol_summary(records)['protocol_gate'] == 'PASS'
    records[1]['arms']['B']['episodes'][1]['boundary_ok'] = False
    assert protocol_summary(records)['protocol_gate'] == 'FAIL'


def test_calibrated_limits_and_citations_match_existing_mind_contract(tmp_path):
    from Mind.event_loop import COGNITIVE_CONTRACT_VERSION, calibrated_schema, citation_sources
    direction = ('The selected asset no longer meets rights.status == active in catalog.json. '
        'Reassess eligibility and prioritize a compliant release. ') * 4
    assert 320 < len(direction) <= 1000
    model = CognitiveModel(lambda wire: step({'type': 'directive', 'text': direction.strip()}),
                           contract=COGNITIVE_CONTRACT_VERSION)
    with MindOrgan(directory=tmp_path, model=model) as mind:
        receipt = mind.activate(input_value())
        assert receipt.status == 'accepted', receipt
        assert receipt.output['text'] == direction.strip()
    wire = model.calls[0]['wire']
    assert 'SOURCE REF: source\nLITERAL SOURCE TEXT:\nOnly settled entries count.' in wire['messages'][-1]['content']
    schema = wire['tools'][0]['input_schema']
    assert schema['properties']['updates']['maxItems'] == 4
    assert schema['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum'] == ['source']
    assert schema['properties']['next']['oneOf'][1]['properties']['text']['maxLength'] == 1000
    assert wire['max_tokens'] == 2000


def test_calibrated_unknown_source_is_not_guessed_or_replaced(tmp_path):
    from Mind.event_loop import COGNITIVE_CONTRACT_VERSION
    update = {'kind': 'belief', 'id': 'new:scope', 'claim': 'Only settled entries count.',
        'status': 'supported', 'discriminator': 'Which entries are counted.',
        'basis': [{'ref': 'source:invented', 'quote': 'Only settled entries count.'}]}
    model = CognitiveModel(lambda wire: step({'type': 'no_change'}, [update]), contract=COGNITIVE_CONTRACT_VERSION)
    with MindOrgan(directory=tmp_path, model=model) as mind:
        receipt = mind.activate(input_value())
        assert receipt.status == 'failed' and mind.inspect().revision == 0
    assert model.calls[0]['response']['content'][0]['input']['updates'][0]['basis'][0]['ref'] == 'source:invented'


@docker
def test_guidance_on_natural_event_wake_is_once_only_and_does_not_recreate_kernel(tmp_path):
    from Execution import ExecutionOrgan, FileContentEquals
    responses = iter([reply('ipython', {'code': "remembered=7"}),
        reply('wait', {'event_type': 'new_batch'}),
        reply('ipython', {'code': "from pathlib import Path\nPath('done').write_text(str(remembered))"}),
        reply('claim_complete', {})])
    model = ExecutionModel(lambda wire: next(responses))
    workspace = tmp_path / 'task'
    workspace.mkdir()
    owner = ExecutionOrgan(workspace=workspace, event_log_path=tmp_path / 'execution.jsonl',
        checkpoint_path=tmp_path / 'checkpoint.json', model=model, max_decisions=6,
        ipython_control=DockerIPython(workspace))
    try:
        assert owner.run_goal('Wait for the external batch then finish.', FileContentEquals('done', '7')).status == 'waiting'
        decision = owner.next_root_decision_id
        assert owner.deliver_event('unrelated', decision_advisory=(decision, 'Not for this event.')).status == 'waiting'
        result = owner.deliver_event('new_batch', decision_advisory=(decision, 'Preserve the accepted eligibility constraint.'))
        assert result.status == 'completed'
        assert (workspace / 'done').read_text() == '7'
        assert 'Preserve the accepted eligibility constraint.' in json.dumps(model.calls[2]['wire'])
        assert 'Not for this event.' not in json.dumps(model.calls[2]['wire'])
        assert 'Preserve the accepted eligibility constraint.' not in model.calls[3]['context']
    finally:
        owner.shutdown()


@docker
@pytest.mark.parametrize('contract', ['cognitive-submit-d3-v1', 'cognitive-submit-d4-v1'])
def test_d3_three_event_loop_reuses_persistent_mind_and_real_wake(tmp_path, contract):
    from Mind.cognitive_contract import run_loop_case
    from Mind.event_loop import IMAGE_TAG
    first_code = "from pathlib import Path\nPath('release.json').write_text('{\"catalog_revision\":1,\"policy_revision\":1,\"selected_ids\":[\"alpha\",\"beta\"]}')"
    last_code = "from pathlib import Path\nPath('release.json').write_text('{\"catalog_revision\":2,\"policy_revision\":2,\"selected_ids\":[\"beta\"]}')\nPath('.lumina-complete').write_text('verified')"
    execution = iter([reply('ipython', {'code': first_code}), reply('wait', {'event_type': 'catalog_update'}),
                      reply('ipython', {'code': last_code}), reply('claim_complete', {})])
    cognitive_count = 0
    injected = False
    def transport(wire):
        nonlocal cognitive_count, injected
        if wire['tools'][0]['name'] != 'cognitive_step':
            return next(execution)
        if contract == 'cognitive-submit-d4-v1' and not injected:
            injected = True
            return reply('cognitive_step', {})
        cognitive_count += 1
        text = wire['messages'][0]['content'].split('\n\nExact citation catalogue')[0]
        payload = json.loads(text)
        sources = payload['cognition']['evidence']
        source = sources[-1]
        items = payload['cognition']['items']
        update = {'kind': 'belief', 'id': items[0]['id'] if items else 'new:policy',
            'claim': 'Release eligibility follows the current policy.', 'status': 'supported',
            'discriminator': 'Which policy basis the current release satisfies.',
            'basis': [{'ref': source['ref'], 'quote': 'Eligibility basis:'}]}
        output = {'type': 'directive', 'text': 'Approval alone no longer establishes eligibility; prioritize current release rights.'} if cognitive_count == 2 else {'type': 'no_change'}
        return step(output, [update])
    record = run_loop_case({'id': 'license_change', 'license_v2': 'revoked', 'basis_v2': 'release_time'},
        tmp_path / 'loop', transport=transport, image=IMAGE_TAG, cognitive_contract=contract,
        review=lambda request: {'allowed': True, 'claims_supported': True, 'classification': 'high_level',
                                'direction_relevant': True, 'uncertainty_preserved': True, 'behavior_consistent': True,
                                'operation_tendency': 'domain', 'reason': 'Scripted grounded direction.', 'reviewer': 'test'})
    assert record['passed'], record.get('blocker')
    assert record['episodes'][1]['delivered'] and record['episodes'][1]['delivered_text_unchanged']
    assert record['episodes'][2]['receipt']['revision'] == 3
    assert record['continuity']['carried_item_ids']
    assert record['continuity']['final_owner_source_in_cognition']
    state = json.loads((tmp_path / 'loop/nervous/events.json').read_text())['state']
    outcome = next(e for e in state['events'] if e['event_id'] == 'owner-event-3')
    assert outcome['causation_id'] == 'wake'
    feedback = next(e for e in state['events'] if e['event_id'] == 'cognition-3')
    assert feedback['causation_id'] == outcome['event_id']


@pytest.mark.parametrize('prior_thinking',[False,True])
def test_execution_thinking_native_content_survives_restart_without_replaying_actions(tmp_path,prior_thinking):
    from Execution.organ import ExecutionOrgan
    from Execution.execution import FileContentEquals
    from Execution.ipython_control import IPythonResult
    seen=[]; actions=[]
    class Python:
        def execute(self,code):
            actions.append(code); return IPythonResult(True,output='Real test observation '+code)
        def close(self): pass
    envelopes=[]
    def transport(wire):
        seen.append(wire)
        answer=reply('ipython',{'code':str(len(seen))}) if len(seen)<3 else reply('wait',{'event_type':'DONE'})
        if wire['thinking']['type']=='enabled':
            assert wire['output_config']=={'effort':'low'} and 'temperature' not in wire
            answer['content'].insert(0,{'type':'thinking','thinking':'opaque local analysis','signature':'signature-'+str(len(seen))})
        envelopes.append(answer)
        return answer
    workspace=tmp_path/'workspace';workspace.mkdir()
    opts=dict(workspace=workspace,event_log_path=tmp_path/'execution.jsonl',max_decisions=5,
              max_context_chars=12000,max_decisions_per_advance=1,ipython_control=Python())
    first=ExecutionOrgan(model=ExecutionModel(transport,thinking=prior_thinking),**opts)
    try:
        first.run_goal('Preserve evidence and finish the scoped result.',FileContentEquals('result.txt','done'))
        run=first.state.execution_id
    finally:first.shutdown()
    second=ExecutionOrgan(model=ExecutionModel(transport,thinking=True),**opts)
    try:
        second.resume();assert second.state.execution_id==run
        messages=seen[-1]['messages']
        assistants=[m for m in messages if m['role']=='assistant']
        if prior_thinking: assert assistants[0]['content']==envelopes[0]['content']
        else:
            assert assistants==[]
            assert all(b.get('type')!='tool_result' for m in messages for b in m['content'])
        assert 'Real test observation 1' in json.dumps(messages)
    finally:second.shutdown()
    third=ExecutionOrgan(model=ExecutionModel(transport,thinking=True),**opts)
    try:
        third.resume()
        assistant=next(m for m in seen[-1]['messages'] if m['role']=='assistant')
        assert assistant['content']==envelopes[1]['content']
        assert third.state.execution_id==run and third.state.decision_count==3
        assert actions==['1','2']
    finally:third.shutdown()


def test_execution_thinking_rejects_an_envelope_with_changed_tool_arguments():
    from Execution.execution import ModelRequest,NativeToolContinuation
    from Mind.decoupling_value import _native
    original=reply('ipython',{'code':'print(1)'})
    raw=_native(original)
    raw['anthropic_response']={'content':[{'type':'thinking','thinking':'opaque','signature':'sig'},
        {**original['content'][0],'input':{'code':'different()'}}]}
    model=ExecutionModel(lambda _:pytest.fail('invalid continuation dispatched'),thinking=True)
    request=ModelRequest('{"goal":"Preserve facts","observation":{"text":"1"}}',model.tool_contracts,(),
        NativeToolContinuation('{"goal":"Preserve facts"}',raw,'response'),16000)
    with pytest.raises(ValueError,match='execution_native_envelope_mismatch'): model.decide(request)


@pytest.mark.parametrize('thinking',[False,True])
@pytest.mark.parametrize('native_batches',[False,True])
@pytest.mark.parametrize('tool_shape',['complete','empty','partial'])
def test_truncated_execution_response_never_dispatches_its_parseable_tool(tmp_path,thinking,native_batches,tool_shape):
    from Execution.organ import ExecutionOrgan
    from Execution.execution import FileContentEquals
    class Python:
        def execute(self,code): pytest.fail('incomplete provider response executed')
        def close(self): pass
    value=reply('ipython',{'code':'write_business_result()'});value['stop_reason']='max_tokens'
    if tool_shape=='empty':value['content']=[]
    if tool_shape=='partial':value['content']=[{'type':'tool_use','id':'unfinished'}]
    if thinking:value['content'].insert(0,{'type':'thinking','thinking':'unfinished','signature':'sig'})
    model=ExecutionModel(lambda _:value,thinking=thinking,native_batches=native_batches)
    workspace=tmp_path/'workspace';workspace.mkdir()
    organ=ExecutionOrgan(workspace=workspace,event_log_path=tmp_path/'execution.jsonl',
        max_decisions=2,model=model,ipython_control=Python())
    try:
        result=organ.run_goal('Deliver the result.',FileContentEquals('result.txt','done'))
        assert result.status=='failed'
        assert 'incomplete_response' in str(result.failure)
        assert model.calls[0]['response']==value
        assert not list(workspace.iterdir())
    finally:organ.shutdown()
    reopened=ExecutionOrgan(workspace=workspace,event_log_path=tmp_path/'execution.jsonl',max_decisions=2,
        model=ExecutionModel(lambda _:pytest.fail('failed native response resampled'),thinking=thinking),
        ipython_control=Python())
    try: assert reopened.resume().status=='failed'
    finally: reopened.shutdown()


@pytest.mark.parametrize('thinking', [False, True])
def test_latest_native_round_over_context_limit_stops_before_another_call(tmp_path, thinking):
    from Execution.organ import ExecutionOrgan
    from Execution.execution import FileContentEquals
    from Execution.ipython_control import IPythonResult
    from Mind.task_view import execution_goal
    task = {'business_goal':'Retain the observed source.', 'execution_protocol':'Use the scoped workspace.'}
    seen, actions = [], []
    def transport(wire):
        seen.append(wire)
        answer = reply('ipython', {'code':'read_source'})
        block = {'type':'thinking','thinking':'x'*60001,'signature':'opaque'} if thinking else {'type':'text','text':'x'*60001}
        answer['content'].insert(0, block)
        return answer
    class Python:
        def execute(self, code): actions.append(code); return IPythonResult(True, output='owner observation')
        def close(self): pass
    workspace=tmp_path/'workspace'; workspace.mkdir()
    model=ExecutionModel(transport, owner_task=task, execution_context=lambda _: {
        'version': 'test', 'rounds': [], 'owner_inputs': [], 'received_guidance': [], 'guidance_scope': ''}, thinking=thinking)
    organ=ExecutionOrgan(workspace=workspace, event_log_path=tmp_path/'execution.jsonl',
                         max_decisions=3,max_decisions_per_advance=1,model=model,ipython_control=Python())
    try:
        organ.run_goal(execution_goal(task), FileContentEquals('result','done'))
        with pytest.raises(ValueError,match='execution_native_context_bound'):
            organ.resume()
        assert len(seen)==1 and actions==['read_source']
    finally: organ.shutdown()
