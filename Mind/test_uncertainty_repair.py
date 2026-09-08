"""Mechanism checks; deliberately scripted erroneous cognition is not model evidence."""
import json
import shutil
from collections import Counter

import pytest

from Execution import ExecutionOrgan, FileContentEquals
from Execution.execution import Wait
from Mind.chain import Session, read_json
from Mind.execution_checkpoint_fixture import execution_checkpoint
from Mind.test_chain import LocalTestPython, cognitive, reply, prior_updates


def payload(wire):
    value = json.loads(wire['messages'][0]['content'].split('\n\nExact source catalogue')[0])
    # These provenance assertions span the historical catalogue and the current
    # source-record rendering; they check the same actual transmitted metadata.
    if 'source_records' in value:
        value['source_annotations'] = {r['ref']: {k:v for k,v in r.items() if k not in {'ref','text'}}
                                       for r in value['source_records']}
    return value


@pytest.mark.parametrize('contract', ['cognitive-chain-v20', 'cognitive-chain-v49', 'cognitive-chain-v51', 'cognitive-chain-v53'])
def test_complete_checkpoint_retains_reaffirms_and_replaces_with_history_unchanged(contract):
    from jsonschema import validate, ValidationError
    from Mind.organ import cognitive_step_schema, _apply_updates
    from Mind.experiment_a import _parse_output, NoChange
    source={'r':{'text':'An observation is recorded.'}}
    items={f'item-{n}':{'id':f'item-{n}','kind':'belief','claim':f'Observation {n} is recorded.',
                      'status':'supported','basis':[{'ref':'r','quote':'An observation is recorded.'}]}
           for n in range(8)}
    schema=cognitive_step_schema({'r':source['r']['text']},list(items.values()),
        ['read_evidence'],contract=contract)
    empty={'type':'cognitive_step','updates':[],'next':{'type':'no_change'}}
    with pytest.raises(ValidationError):validate(empty,schema)
    with pytest.raises(ValueError,match='incomplete_cognitive_checkpoint'):
        _apply_updates(items,[],source,'event',contract=contract)
    validate({**empty,'next':{'type':'capability_request','capability':'read_evidence','refs':['r']}},schema)
    validate(empty,cognitive_step_schema({'r':source['r']['text']},list(items.values()),contract='cognitive-chain-v16'))
    updates=[dict(item) for item in items.values()]
    updates[0]={**updates[0],'status':'archived'}
    updates.append({**items['item-0'],'id':'new:replacement','claim':'A replacement observation is recorded.'})
    final={**empty,'updates':updates}
    validate(final,schema)
    assert isinstance(_parse_output(json.dumps(final),unified=True,contract=contract),NoChange)
    current=_apply_updates(items,updates,source,'event',contract=contract)
    assert len(current)==8 and 'item-0' not in current
    assert items['item-0']['status']=='supported'
    assert current['item-1']==items['item-1']


def test_missing_checkpoint_item_has_one_native_repair_and_replays(tmp_path, monkeypatch):
    # V20 required a complete final checkpoint; the current profile permits deltas.
    monkeypatch.setattr("Mind.chain.INTEGRATION_CONTRACT_VERSION", "cognitive-chain-v20")
    from Mind.chain import ChainMind
    from Mind.organ import MindOrgan, MindInput, Evidence
    calls=[]
    def transport(wire):
        calls.append(wire)
        if len(calls)==1:
            return cognitive(updates=[{'id':'new:record','kind':'belief','claim':'The record reports five.',
                'status':'supported','basis':[{'ref':'record','quote':'Count is five.'}]}])
        if len(calls)==2:return cognitive()
        assert 'Final checkpoint is missing item item-' in json.dumps(wire['messages'][-1])
        data=payload(wire)
        prior=data['cognition']['prior_model_judgments'][0]
        return cognitive(updates=prior_updates([prior]))
    directory=tmp_path/'mind'
    first=MindInput('first','Read.','goal',1,'Retain the observed count.','run','waiting',
                    (Evidence('record','Count is five.','execution'),))
    second=MindInput('second','Reassess.','goal',1,'Retain the observed count.','run','waiting')
    with MindOrgan(directory=directory,model=ChainMind(transport)) as mind:
        assert mind.activate(first).status=='accepted'
        receipt=mind.activate(second)
        assert receipt.status=='accepted' and receipt.revision==2 and len(calls)==3
        view=mind.inspect()
    with MindOrgan(directory=directory,model=ChainMind(lambda *_:pytest.fail('replay dispatched'))) as mind:
        assert mind.activate(second).revision==2
        assert mind.inspect().items==view.items


def test_short_primary_sources_survive_owner_and_catalogue_projection(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    files={'aggregate.json':'{"total":8}', 'analysis.json':'An actor interpreted the total.',
           'measurement.txt':'No subgroup was recorded in this original sample.',
           'terms.txt':'Preserve unknown subgroup identity.'}
    for name,text in files.items():(workspace/name).write_text(text,encoding='utf-8')
    saw_owner=[]
    def transport(role,wire):
        if role=='execution':return reply('wait',{'event_type':'OWNER_EVIDENCE'})
        data=payload(wire)
        if any(a['kind']=='owner_statement' for a in data['source_annotations'].values()):
            saw_owner.append(data)
            labels={a['label'] for a in data['source_annotations'].values()}
            assert set(files)<=labels
            for text in files.values():assert any(r['text'] == text for r in data['source_records'])
        return cognitive()
    directory=tmp_path/'session'
    with Session(directory,workspace=workspace,goal='Determine subgroup identity when observed.',
                 transport=transport,ipython=LocalTestPython(workspace)) as session:
        session.run()
        final=session.run(owner_event=('OWNER_EVIDENCE','Collection for this sample remains open.'))
        assert saw_owner
    with Session(directory,transport=lambda *_:pytest.fail('quiet replay dispatched'),
                 ipython=LocalTestPython(workspace)) as session:
        assert session.run()['cost']==final['cost']


def test_analysis_target_binds_builder_and_survives_replay(tmp_path, monkeypatch):
    from Mind.chain import Builder, Calls
    from Mind.test_chain import fake_compute
    fake_compute(monkeypatch)
    turns = 0
    def transport(role, wire):
        nonlocal turns
        turns += 1
        assert wire['tools'][0]['input_schema']['properties']['observation_file']['const'] == 'result.json'
        if turns <= 2:
            if turns == 2:
                error=json.loads(wire['messages'][-1]['content'][0]['content'])
                assert error['status']=='rejected_before_commit'
                assert error['field_errors'][0]['path']==['observation_file']
                assert error['field_errors'][0]['validator']=='const'
                assert not list((tmp_path/'models').glob('*.compute.json'))
            return reply('compute', {'source':'model', 'initial_observation':{'count':3},
                'actions':[{'add':2}], 'observation_file':'alternative.json' if turns == 1 else 'result.json'})
        result=json.loads(wire['messages'][-1]['content'][0]['content'])
        return reply('report',{'run_ref':result['run_ref'],'answer':'Conditional count five.',
            'assumptions':'The given additive rule holds.','unknowns':'Execution has not acted.'})
    calls=Calls(tmp_path/'calls',{'calls':3,'output_tokens':3*16384,'request_bytes':100000},transport)
    builder=Builder(tmp_path/'models',calls,lambda ref:'Initial count three; add two.')
    request={'question':'Predict count.','refs':['rules'],'model_ref':'','observation_file':'result.json'}
    observed=builder.analyze('request',request)
    report=json.loads(observed['text'])
    assert report['observation_file']=='result.json'
    assert builder.model(report['model_ref'])['observation_file']=='result.json'
    assert len(list((tmp_path/'models').glob('*.compute.json')))==1
    assert Builder(tmp_path/'models',calls,lambda ref:pytest.fail('replay reread evidence')).analyze('request',request)==observed
    assert turns==3


def test_observation_binding_trace_shape_is_versioned():
    from Mind.trace import _validate_capability_request, TraceError
    old={'capability':'analyze_world_model','question':'Predict.','refs':['rules'],'model_ref':''}
    new={**old,'observation_file':'result.json'}
    _validate_capability_request(old,contract='cognitive-chain-v15')
    _validate_capability_request(new,contract='cognitive-chain-v16')
    with pytest.raises(TraceError):_validate_capability_request(old,contract='cognitive-chain-v16')
    with pytest.raises(TraceError):_validate_capability_request(new,contract='cognitive-chain-v15')


def test_deferred_owner_registration_has_no_action_until_explicit_resume(tmp_path):
    class Model:
        identifier = 'deferred-owner-test'
        tool_contracts = ('wait(event_type: str)',)
        def __init__(self): self.calls=0
        def decide(self, request):
            self.calls+=1
            return Wait('OWNER_EVIDENCE')
    workspace=tmp_path/'workspace';workspace.mkdir()
    model=Model()
    owner=ExecutionOrgan(workspace=workspace,event_log_path=tmp_path/'execution.jsonl',max_decisions=4,
        model=model,ipython_control=LocalTestPython(workspace))
    first=owner.run_goal('Consider supplied observations.',FileContentEquals('result.txt','done'),defer_actions=True)
    assert first.status=='running' and model.calls==0
    owner.resume()
    assert owner.state.status=='waiting' and model.calls==1
    owner.deliver_event('OWNER_EVIDENCE','Current count is five.',defer_actions=True)
    assert owner.state.status=='running' and model.calls==1
    assert owner.next_root_decision_id=='decision-000002'
    owner.shutdown()
    restored=ExecutionOrgan(workspace=workspace,event_log_path=tmp_path/'execution.jsonl',max_decisions=4,
        model=model,ipython_control=LocalTestPython(workspace))
    restored.resume()
    assert model.calls==2 and restored.state.waiting_for=='OWNER_EVIDENCE'
    restored.shutdown()


def test_completed_error_history_reassessed_in_new_run_and_correct_directive_only(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    (workspace/'evidence.txt').write_text('No count has been supplied yet.',encoding='utf-8')
    calls=Counter(); wires=[]
    bad='No future observations can arrive.'
    event='Observation received: the count is five.'
    def transport(role,wire):
        calls[role]+=1;wires.append((role,wire))
        if role=='execution':
            if calls[role]==4:return reply('wait',{'event_type':'MIND_REVIEW'})
            if calls[role]==5:return reply('claim_complete',{})
            return reply('ipython',{'code':'finish'}) if calls[role]%2 else reply('claim_complete',{})
        data=payload(wire)
        sources=data['source_annotations']
        if calls[role]==1:
            goal=data['goal']['ref']
            # Explicitly erroneous scripted seed. Only update/lineage behavior is tested.
            return cognitive({'type':'directive','text':'Stop all investigation.'},[{'id':'new:seed',
                'kind':'belief','claim':bad,'status':'supported','basis':[{'ref':goal}]}])
        if calls[role]==2:
            prior = data['cognition']['prior_model_judgments'][0]
            assert prior['claim']==bad and prior['prior_truth'] is True
            assert 'items' not in data['cognition'] and 'status' not in prior
            ref=next(ref for ref,value in sources.items() if value['kind']=='owner_statement')
            return cognitive({'type':'directive','text':'Use the newly observed count of five for the current result.'},[
                {'id':prior['id'],'kind':'belief','claim':'A count observation has now arrived.',
                 'status':'supported','basis':[{'ref':ref}]}])
        return cognitive(updates=prior_updates(data['cognition']['prior_model_judgments']))
    directory=tmp_path/'session'
    with execution_checkpoint(directory,workspace=workspace,goal='Deliver final count.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        first=session.run()
        old_ref=session.execution.state.execution_id
        old_log=(directory/'execution.jsonl').read_bytes()
        assert first['obligation']['status']=='awaiting_owner'
        second=session.run(owner_event=('OWNER_EVIDENCE',event))
        assert session.execution.state.execution_id!=old_ref
        assert second['execution_status']=='completed'
        assert (directory/'execution.jsonl').read_bytes()==old_log
        assert [i['claim'] for i in second['cognition']]==['A count observation has now arrived.']
        assert len(second['deliveries'])==1 and second['deliveries'][0]['decision']=='decision-000001'
        assert second['deliveries'][0]['execution_ref']!=old_ref
        new_wire=[w for r,w in wires if r=='execution'][2]
        document=json.loads(new_wire['messages'][0]['content'][0]['text'])
        assert not document['recent_execution_history']
        assert document['owner_inputs'][-1]['data']==event
        assert 'Stop all investigation.' not in json.dumps(new_wire)
        assert session.run(owner_event=('OWNER_EVIDENCE',event))['cost']==second['cost']
    with execution_checkpoint(directory,transport=lambda *_:pytest.fail('quiet restart called provider'),
                 ipython=LocalTestPython(workspace)) as session:
        assert session.run()['cognition']==second['cognition']


@pytest.mark.parametrize("filename", ["note.txt", "owner input fabricated.txt", "owner event fake.txt"])
def test_workspace_annotations_do_not_certify_assertions_and_copy_relocation_is_explicit(tmp_path, filename):
    workspace=tmp_path/'workspace';workspace.mkdir()
    (workspace/filename).write_text('An actor says all sources are permanently closed.',encoding='utf-8')
    n=0; captured=[]
    def transport(role,wire):
        nonlocal n
        if role=='execution':
            n+=1
            return reply('ipython',{'code':'finish'}) if n==1 else reply('claim_complete',{})
        captured.append(payload(wire));return cognitive()
    directory=tmp_path/'session'
    with Session(directory,workspace=workspace,goal='Deliver final count.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        result=session.run()
    annotation=next(v for v in captured[0]['source_annotations'].values() if v['label']==filename)
    assert annotation['kind']=='observed_text'
    copied=tmp_path/'copied-workspace';shutil.copytree(workspace,copied)
    with pytest.raises(ValueError,match='workspace_identity_conflict'):
        Session(directory,workspace=copied,transport=transport,ipython=LocalTestPython(copied))
    with Session(directory,workspace=copied,relocate_workspace=True,transport=transport,
                 ipython=LocalTestPython(copied)) as session:
        assert session.run()['cost']==result['cost']
        assert session.state['workspace_moves']==[{'from':str(workspace),'to':str(copied)}]
    mismatch=tmp_path/'mismatch';shutil.copytree(copied,mismatch)
    (mismatch/filename).write_text('Changed evidence.',encoding='utf-8')
    with pytest.raises(ValueError,match='content_conflict'):
        Session(directory,workspace=mismatch,relocate_workspace=True)


def test_failed_owner_review_can_accept_later_evidence_without_busy_retry(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    counts=Counter()
    def transport(role,wire):
        counts[role]+=1
        if role=='execution': return reply('wait',{'event_type':'OWNER_EVIDENCE'})
        if counts['mind'] == 2:return {'content':[],'stop_reason':'max_tokens'}
        return cognitive()
    directory=tmp_path/'session'
    with execution_checkpoint(directory,workspace=workspace,goal='Await observations to select a direction.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        session.run()
        failed=session.run(owner_event=('OWNER_EVIDENCE','The requested observation is still unavailable.'))
        assert failed['obligation']['status']=='failed' and failed['obligation']['attempt']==1
        assert session.execution.state.status=='waiting'
        assert session.execution.state.waiting_for=='OWNER_EVIDENCE'
        assert session.run()['cost']==failed['cost']
        recovered=session.run(owner_event=('OWNER_EVIDENCE','A new measurement reports a count of five.'))
        assert recovered['mind_revision']>failed['mind_revision']
        assert session.state['owner_inputs'][0]['status']=='failed'  # Preserve the historical failed judgment.
        assert session.state['owner_inputs'][-1]['status']=='reviewed'
        assert session.execution.state.waiting_for=='OWNER_EVIDENCE'
        assert session.run()['cost']==recovered['cost']


def test_v9_can_atomically_revise_all_active_items_without_changing_old_limits():
    from Mind.organ import _apply_updates, cognitive_step_schema
    prior={f'item-{n}':{'id':f'item-{n}','kind':'belief','claim':'Earlier uncertain claim.',
        'status':'open','basis':[]} for n in range(8)}
    updates=[{**v,'claim':'Revised scoped unknown.'} for v in prior.values()]
    assert len(_apply_updates(prior,updates,{},'event',contract='cognitive-chain-v9'))==8
    assert cognitive_step_schema({},contract='cognitive-chain-v9')['properties']['updates']['maxItems']==8
    for version in ('cognitive-chain-v7','cognitive-chain-v8'):
        assert cognitive_step_schema({},contract=version)['properties']['updates']['maxItems']==4
        with pytest.raises(ValueError,match='update_budget_exceeded'):
            _apply_updates(prior,updates,{},'event',contract=version)


@pytest.mark.parametrize('contract',['cognitive-chain-v7','cognitive-chain-v8'])
def test_old_integration_profile_retains_length_repair_and_activity_budget(tmp_path,contract):
    from Mind.event_loop import CognitiveModel
    from Mind.organ import MindOrgan, MindInput, INTEGRATION_EVENT_BUDGET_VERSION
    n=0
    def transport(wire):
        nonlocal n
        n+=1
        return cognitive({'type':'directive','text':'x'*1001}) if n==1 else cognitive()
    with MindOrgan(directory=tmp_path/'mind',model=CognitiveModel(transport,contract=contract,thinking=True)) as mind:
        receipt=mind.activate(MindInput('event','Review.','goal',1,'Deliver.','run','waiting'))
        assert receipt.status=='accepted' and n==2
        starts=[r for r in mind._load() if r['kind']=='started']
        assert starts[0]['budget_version']==INTEGRATION_EVENT_BUDGET_VERSION


@pytest.mark.parametrize('terminal',[False,True])
@pytest.mark.parametrize('with_files',[False,True])
def test_legacy_projection_migration_is_quiet_but_changed_evidence_wakes(tmp_path,monkeypatch,terminal,with_files):
    from Mind.chain import fingerprint, write_json
    workspace=tmp_path/'workspace';workspace.mkdir()
    if with_files:
        (workspace/'note.txt').write_text('Current count unknown.',encoding='utf-8')
        write_json(workspace/'.mind-request.json',{'question':'Is there enough evidence?'})
    counts=Counter()
    def transport(role,wire):
        counts[role]+=1
        if role=='mind':return cognitive()
        if not terminal:return reply('wait',{'event_type':'OWNER_EVIDENCE'})
        return reply('ipython',{'code':'finish'}) if counts[role]==1 else reply('claim_complete',{})
    capture=Session.capture
    def legacy_source(self,text,label,origin='execution',**_):
        ref='source:'+fingerprint([label,text,origin])[:24]
        filename=ref.replace(':','-')+'.json'
        write_json(self.directory/'sources'/filename,{'ref':ref,'label':label,'text':text,'origin':origin})
        self.state['sources'][ref]=filename
        return ref
    def legacy_capture(self):
        _,snapshot=capture(self);snapshot.pop('request_origin',None)
        return fingerprint(snapshot),snapshot
    directory=tmp_path/'session'
    with monkeypatch.context() as patch:
        patch.setattr(Session,'source',legacy_source)
        patch.setattr(Session,'capture',legacy_capture)
        with execution_checkpoint(directory,workspace=workspace,goal='Report available evidence.',transport=transport,
                     ipython=LocalTestPython(workspace)) as session:
            before=session.run()
            session.state['version']='cognitive-chain-v7';session.save()
    with execution_checkpoint(directory,transport=transport,ipython=LocalTestPython(workspace)) as session:
        after=session.run()
        assert after['cost']==before['cost']
        assert after['cognition']==before['cognition']
        (workspace/'new.txt').write_text('A later observed count is five.',encoding='utf-8')
        if terminal:
            changed=session.run()
            assert changed['cost']['calls']==before['cost']['calls']+1
        else:
            assert session.run()['cost']==before['cost']
            changed=session.run(owner_event=('OWNER_EVIDENCE','A later measurement is available in new.txt.'))
            assert changed['mind_revision']==before['mind_revision']+1
            assert counts['execution']==2
            assert changed['execution_status']=='waiting'
            assert session.run()['cost']==changed['cost']


def test_owner_reassessment_expires_paused_same_run_guidance(tmp_path,monkeypatch):
    from Mind.chain import Calls, BudgetPause
    workspace=tmp_path/'workspace';workspace.mkdir()
    counts=Counter();pauses=0;original=Calls.call
    def transport(role,wire):
        counts[role]+=1
        if role=='mind':
            return cognitive({'type':'directive','text':'Prioritize the currently supported option.'}) if counts[role]==1 else cognitive()
        return reply('wait',{'event_type':'MIND_REVIEW' if counts[role]==1 else 'OWNER_EVIDENCE'})
    def call(self,role,wire,**kwargs):
        nonlocal pauses
        if role=='execution' and counts['execution']==1 and pauses<2:
            pauses+=1;raise BudgetPause('chain_budget_exhausted')
        return original(self,role,wire,**kwargs)
    monkeypatch.setattr(Calls,'call',call)
    with execution_checkpoint(tmp_path/'session',workspace=workspace,goal='Select only with adequate evidence.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        first=session.run()
        assert first['deliveries'][0]['status']=='bound'
        assert session.execution.state.status=='running'
        revised=session.run(owner_event=('OWNER_EVIDENCE','The earlier measurement was withdrawn; no replacement is available yet.'))
        assert revised['stop_reason'] is None
        assert counts=={'execution':1,'mind':2}
        assert session.execution.state.waiting_for=='MIND_REVIEW'
        assert revised['deliveries'][0]['status']=='expired_on_owner_reassessment'
        assert not session.completion_review_required()  # Undelivered expired advice creates no result duty.
        assert session.run()['cost']==revised['cost']
        paused=session.run(owner_event=('MIND_REVIEW','Reassessment complete; resume without additional direction.'))
        assert paused['stop_reason']=='chain_budget_exhausted'
        recovered=session.run()
        assert recovered['execution_status']=='waiting'
        assert session.execution.state.waiting_for=='OWNER_EVIDENCE'
        assert session.run()['cost']==recovered['cost']


def test_current_profile_accepts_eight_updates_through_actual_native_trace(tmp_path):
    from Mind.organ import MindOrgan, MindInput
    from Mind.chain import ChainMind
    def transport(wire):
        return cognitive(updates=[{'id':f'new:q{n}','kind':'question','text':f'Is condition {n} known?',
            'status':'open','basis':[]} for n in range(8)])
    with MindOrgan(directory=tmp_path/'mind',model=ChainMind(transport)) as mind:
        receipt=mind.activate(MindInput('event','Review scope.','goal',1,'Deliver.','run','waiting'))
        assert receipt.status=='accepted',receipt
        assert len(mind.inspect().items)==8
    with MindOrgan(directory=tmp_path/'mind',model=ChainMind(lambda *_:pytest.fail('replay must be local'))) as mind:
        assert len(mind.inspect().items)==8


def test_read_result_is_citable_in_plain_native_continuation(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    text='Measured count is five.\n'+('Context line.\n'*85)
    (workspace/'reading.txt').write_bytes(text.encode('utf-8'))
    counts=Counter()
    def transport(role,wire):
        counts[role]+=1
        if role=='execution':return reply('wait',{'event_type':'MIND_REVIEW' if counts[role]==1 else 'OWNER_EVIDENCE'})
        if counts[role]==1:
            ref=next(ref for ref,name in session.state['sources'].items()
                if read_json(session.directory/'sources'/name)['label']=='reading.txt')
            return reply('read_evidence',{'refs':[ref]})
        content=wire['messages'][-1]['content'][0]['content']
        result=json.loads(content.split('\n',1)[1])
        assert result['observation']['source_ref']=='activation:observation'
        assert any(item['text']==text for item in result['source_records'])
        return cognitive(updates=[{'id':'new:observed','kind':'belief','claim':'The reading records a count of five.',
            'status':'supported','basis':[{'ref':result['source_records'][0]['ref']}]}])
    with Session(tmp_path/'session',workspace=workspace,goal='Assess the available reading.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        result=session.run()
        assert result['cognition'][0]['claim']=='The reading records a count of five.'
        ref=result['cognition'][0]['basis'][0]['ref']
        assert ref!='activation:observation'
        assert text in session.read_source(ref)
        assert session.source_info(ref)['workspace_version']=='current'
        assert text==session.mind.read_source(ref)['text']
        (workspace/'reading.txt').write_text('Later count is six.',encoding='utf-8')
        _,snapshot=session.capture();session.state['obligation']['snapshot']=snapshot
        old=session.source_info(ref)
        assert old['workspace_version']=='superseded'
        assert session.read_source(old['current_ref'])=='Later count is six.'


@pytest.mark.parametrize('consult',[False,True])
def test_eight_update_output_recovers_before_request_or_terminal_without_resampling(tmp_path,monkeypatch,consult):
    # The historical combined consultation/updates output uses the V20 contract.
    monkeypatch.setattr('Mind.chain.INTEGRATION_CONTRACT_VERSION','cognitive-chain-v20')
    from Mind.organ import MindOrgan, MindInput
    from Mind.trace import MindTrace
    from Mind.chain import ChainMind
    updates=[{'id':f'new:q{n}','kind':'question','text':f'Is condition {n} known?',
        'status':'open','basis':[]} for n in range(8)]
    response=cognitive({'type':'capability_request','capability':'inspect_execution'} if consult else None,updates)
    append=MindTrace.append
    class Crash(BaseException):pass
    def crash(trace,event_type,*args,**kwargs):
        result=append(trace,event_type,*args,**kwargs)
        if event_type=='MODEL_OUTPUT_RECORDED':raise Crash()
        return result
    from Mind.experiment_a import ExecutionObservation
    event=MindInput('event','Review.','goal',1,'Deliver.','run','waiting',
        execution_observation=ExecutionObservation('Deliver.','waiting','Review current scope.',None))
    with monkeypatch.context() as patch:
        patch.setattr(MindTrace,'append',crash)
        with MindOrgan(directory=tmp_path/'mind',model=ChainMind(lambda _:response),available_capabilities=('inspect_execution',)) as mind:
            with pytest.raises(Crash):mind.activate(event)
    with MindOrgan(directory=tmp_path/'mind',model=ChainMind(lambda *_:pytest.fail('persisted output was resampled')),available_capabilities=('inspect_execution',)) as mind:
        receipt=mind.activate(event)
        assert receipt.status==('waiting' if consult else 'accepted'),receipt
        assert len(mind.inspect().items)==(0 if consult else 8)


def test_source_annotations_distinguish_current_and_historical_file_contents(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    with Session(tmp_path/'session',workspace=workspace,goal='Assess the record.',transport=lambda *_:None,
                 ipython=LocalTestPython(workspace)) as session:
        old=session.source('Count unknown.','record.txt')
        current=session.source('Count measured as five.','record.txt')
        session.state['obligation']={'snapshot':{'files':[{'file':'record.txt','ref':current,'chars':23}]}}
        assert session.source_info(old)['workspace_version']=='superseded'
        assert session.source_info(old)['current_ref']==current
        assert session.source_info(current)['workspace_version']=='current'
        assert session.read_source(old)=='Count unknown.'


def test_owner_input_order_is_retained_inside_read_lineage(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    with Session(tmp_path/'session',workspace=workspace,goal='Assess available records.',transport=lambda *_:None,
                 ipython=LocalTestPython(workspace)) as session:
        old=session.source('No records available now.','owner input 1',kind='owner_statement')
        new=session.source('A later record arrived.','owner input 2',kind='owner_statement')
        session.state['obligation']={'snapshot':{'files':[],'owner_events':[
            {'ref':old,'call_ref':'owner-input:1'},{'ref':new,'call_ref':'owner-input:2'}]}}
        assert session.source_info(old)['owner_input_order']=='earlier_in_view'
        assert session.source_info(new)['owner_input_order']=='latest_in_view'


@pytest.mark.parametrize('contract',['cognitive-chain-v13','cognitive-chain-v14'])
def test_model_report_string_quotes_have_one_versioned_grounding_projection(tmp_path,contract):
    from Mind.organ import MindOrgan, MindInput, MindResultEvent, Evidence
    from Mind.event_loop import CognitiveModel
    calls=0;sentence='The model satisfies the "at most 4" condition.'
    def transport(wire):
        nonlocal calls
        calls+=1
        if calls==1:return cognitive({'type':'capability_request','capability':'analyze_world_model',
            'question':'Evaluate the stated bound.','refs':['rules'],'model_ref':''})
        return cognitive(updates=[{'id':'new:forecast','kind':'belief','claim':'The conditional model satisfies the stated bound.',
            'status':'supported','basis':[{'ref':'activation:observation','quote':sentence}]}])
    with MindOrgan(directory=tmp_path/'mind',model=CognitiveModel(transport,contract=contract),
                   available_capabilities=('analyze_world_model',)) as mind:
        receipt=mind.activate(MindInput('event','Review.','goal',1,'Deliver.','run','waiting',
            evidence=(Evidence('rules','The upper bound is four.','execution'),)))
        assert receipt.status=='waiting'
        result=mind.accept_result(MindResultEvent(receipt.request.request_ref,observation={
            'capability':'analyze_world_model','origin':'computation','text':json.dumps({'kind':'COMPUTED_CONDITIONAL','answer':sentence})}))
        assert result.status==('accepted' if contract=='cognitive-chain-v14' else 'failed')
        if contract=='cognitive-chain-v14':
            source=mind.read_source(mind.inspect().items[0]['basis'][0]['ref'])
            assert source['origin']=='computation' and sentence in source['text']
        else:assert result.error=='ungrounded_basis'


def test_explicit_failed_review_retry_keeps_failure_and_does_not_invent_owner_evidence(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    calls=Counter()
    def transport(role,wire):
        calls[role]+=1
        if role=='execution':return reply('wait',{'event_type':'OWNER_EVIDENCE'})
        return {'content':[],'stop_reason':'max_tokens'} if calls[role]==1 else cognitive()
    with Session(tmp_path/'session',workspace=workspace,goal='Assess the available observations.',transport=transport,
                 ipython=LocalTestPython(workspace)) as session:
        failed=session.run();old=failed['obligation']['event_id']
        initial_owner_inputs=json.loads(json.dumps(session.state['owner_inputs']))
        assert failed['obligation']['status']=='failed'
        assert session.run()['cost']==failed['cost']
        recovered=session.run(retry_review=True)
        assert recovered['obligation']['status']=='accepted'
        assert recovered['obligation']['event_id']!=old
        assert session.state['review_retries'][0]['previous_event_id']==old
        assert session.state['owner_inputs']==[{**initial_owner_inputs[0], 'status':'reviewed'}]
        assert initial_owner_inputs[0]['event_type']=='USER_GOAL'
        assert session.run()['cost']==recovered['cost']
        with pytest.raises(ValueError,match='retry_requires_failed'):
            session.run(retry_review=True)


@pytest.mark.parametrize('recovery', ['owner_evidence', 'explicit_retry'])
def test_running_failed_feedback_stays_failed_on_quiet_resume_and_can_recover(tmp_path, monkeypatch, recovery):
    from Mind.chain import BudgetPause
    from dataclasses import replace
    workspace = tmp_path/'workspace'; workspace.mkdir()
    directory = tmp_path/'session'
    counts = Counter()
    new_evidence = 'A follow-up observation confirms the recorded count of five.'
    def transport(role, wire):
        counts[role] += 1
        if role == 'execution':
            if counts[role] == 1:
                return reply('wait', {'event_type': 'MIND_REVIEW'})
            if counts[role] == 2:
                return reply('ipython', {'code': 'finish'})
            pytest.fail('Execution bypassed the retained feedback allocation.')
        if counts[role] == 1:
            return cognitive({'type': 'directive', 'text': 'Retain the specified final count.'})
        if counts[role] == 2:
            return {'content': [], 'stop_reason': 'max_tokens'}
        if recovery == 'owner_evidence':
            assert new_evidence in wire['messages'][0]['content']
        return cognitive()
    class ReportingPython(LocalTestPython):
        def execute(self, code):
            result=super().execute(code)
            return replace(result,cognitive_request=json.dumps({
                'question':'Assess the completed count result.',
                'evidence_files':['result.json'], 'model_ref':''}))
    with execution_checkpoint(directory, workspace=workspace, goal='Deliver the final count.', transport=transport,
                 limits={'calls': 5, 'output_tokens': 100000, 'request_bytes': 2000000},
                 ipython=ReportingPython(workspace)) as session:
        failed = session.run()
        assert failed['execution_status'] == 'running'
        assert failed['obligation']['status'] == 'failed'
        assert failed['obligation']['attempt'] == 1
        assert counts == {'execution': 2, 'mind': 2}
        original = dict(failed['obligation'])
        cost = failed['cost']
    with execution_checkpoint(directory, transport=transport, ipython=ReportingPython(workspace)) as session:
        for _ in range(2):
            quiet = session.run()
            assert quiet['cost'] == cost
            assert quiet['obligation'] == original
            assert not quiet['obligation']['understanding_updated']
            assert not any(session.nervous.pending(t, 1)
                           for t in ('mind', 'mind.requests', 'mind.results', 'host'))
        # A reservation failure must not republish an already consumed activation
        # and replace its failed receipt with an unprocessable pending obligation.
        with monkeypatch.context() as patch:
            def reserved(*, owner_event=None):
                raise BudgetPause('feedback_budget_reserved')
            patch.setattr(session, '_run', reserved)
            paused = session.run()
            assert paused['obligation'] == original
            assert paused['cost'] == cost
            assert not session.nervous.pending('mind', 1)
        if recovery == 'owner_evidence':
            recovered = session.run(owner_event=('OWNER_EVIDENCE', new_evidence))
            assert session.state['owner_inputs'][-1]['status'] == 'reviewed'
        else:
            recovered = session.run(retry_review=True)
            assert not session.state.get('owner_inputs')
            assert session.state['review_retries'][-1]['previous_event_id'] == original['event_id']
        assert recovered['obligation']['status'] == 'accepted'
        assert recovered['obligation']['event_id'] != original['event_id']
        assert recovered['mind_revision'] == failed['mind_revision'] + 1
        assert counts == {'execution': 2, 'mind': 3}
        assert session.run()['cost'] == recovered['cost']


def test_repeated_legacy_owner_content_uses_latest_occurrence(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    with Session(tmp_path/'session',workspace=workspace,goal='Assess observations.',transport=lambda *_:None,
                 ipython=LocalTestPython(workspace)) as session:
        a=session.source('Available now.','owner event OWNER_EVIDENCE',kind='owner_statement')
        b=session.source('Unavailable now.','owner event OWNER_EVIDENCE',kind='owner_statement')
        session.state['obligation']={'snapshot':{'files':[],'owner_events':[
            {'ref':a,'call_ref':'execution-call:0001'},{'ref':b,'call_ref':'execution-call:0002'},
            {'ref':a,'call_ref':'execution-call:0003'}]}}
        info=session.source_info(a)
        assert info['owner_input_order']=='latest_in_view'
        assert info['event_ref']=='execution-call:0003'


def test_accepted_review_releases_old_reserve_but_new_guidance_retains_feedback_budget(tmp_path):
    from Mind.chain import BudgetPause
    workspace=tmp_path/'workspace';workspace.mkdir()
    counts=Counter()
    execution_wires=[]
    def transport(role,wire):
        counts[role]+=1
        if role=='mind':
            return cognitive({'type':'directive','text':('Retain the specified final count.' if counts[role]==1
                else 'Include the newly established measurement scope in the delivery.')})
        execution_wires.append(wire)
        if counts[role] in {1,3}:return reply('wait',{'event_type':'MIND_REVIEW'})
        if counts[role]==2:return reply('ipython',{'code':'finish'})
        pytest.fail('A new execution decision consumed the reserved feedback allocation.')
    with execution_checkpoint(tmp_path/'session',workspace=workspace,goal='Deliver the final count.',transport=transport,
                 limits={'calls':7,'output_tokens':140000,'request_bytes':2000000},
                 ipython=LocalTestPython(workspace)) as session:
        result=session.run()
        assert result['stop_reason']=='feedback_budget_reserved'
        assert result['obligation']['status']=='accepted'
        assert result['obligation']['understanding_updated']
        assert result['mind_revision']==2
        assert session.execution.state.status=='running'
        assert session.completion_review_required()
        assert result['deliveries'][0]['feedback_event']==result['obligation']['event_id']
        assert result['deliveries'][1]['status']=='bound'
        assert counts=={'execution':3,'mind':2}
        assert result['deliveries'][0]['call_ref']
        # The review is accepted through the normal chain. No new observation or
        # accepted-state fabrication is needed to reproduce the admission edge.
        before=json.dumps(session.state,sort_keys=True)
        pending={target:session.nervous.pending(target)
                 for target in ('mind','mind.requests','mind.results','host')}
        events=session.execution.result.events
        cost=session.calls.summary()
        for _ in range(2):
            with pytest.raises(BudgetPause,match='feedback_budget_reserved'):
                session.call_execution(execution_wires[-1])
        assert counts=={'execution':3,'mind':2}
        assert session.calls.summary()==cost
        assert json.dumps(session.state,sort_keys=True)==before
        assert session.execution.result.events==events
        assert {target:session.nervous.pending(target) for target in pending}==pending
        assert session.run()['cost']==cost


@pytest.mark.parametrize('new_owner_evidence',[False,True])
def test_no_change_wake_before_receipt_ack_recovers_without_swallowing_owner_input(
        tmp_path,monkeypatch,new_owner_evidence):
    from Nervous.organ import NervousOrgan
    workspace=tmp_path/'workspace';workspace.mkdir()
    directory=tmp_path/'session'
    counts=Counter()
    literal='A newly measured count of six is now available.'
    def transport(role,wire):
        counts[role]+=1
        if role=='execution':
            return reply('wait',{'event_type':'MIND_REVIEW' if counts[role]==1 else 'OWNER_EVIDENCE'})
        if counts[role]>1:
            assert new_owner_evidence, 'Recovery repeated the already accepted Mind judgment.'
            assert literal in json.dumps(wire,ensure_ascii=False)
        return cognitive()
    class Crash(BaseException):pass
    complete=NervousOrgan.complete
    def crash_before_ack(nervous,event_id,target,*,emitted=()):
        if target=='host':raise Crash()
        return complete(nervous,event_id,target,emitted=emitted)
    with monkeypatch.context() as patch:
        patch.setattr(NervousOrgan,'complete',crash_before_ack)
        with execution_checkpoint(directory,workspace=workspace,goal='Assess the available count.',transport=transport,
                     limits={'calls':2,'output_tokens':100000,'request_bytes':2000000},
                     ipython=LocalTestPython(workspace)) as session:
            with pytest.raises(Crash):session.run()
            assert session.execution.result.events[-1].event_type=='ROOT_WOKEN'
            assert session.execution.state.status=='running'
            assert session.state['obligation']['status']=='accepted'
            assert session.nervous.pending('host')
            event=session.state['obligation']['event_id']
            cost=session.calls.summary()
    assert counts=={'execution':1,'mind':1}
    with execution_checkpoint(directory,transport=transport,ipython=LocalTestPython(workspace)) as session:
        if new_owner_evidence:session.extend_budget(4)
        result=session.run(owner_event=('OWNER_EVIDENCE',literal) if new_owner_evidence else None)
        assert not session.nervous.pending('host')
        assert result['obligation']['status']=='accepted'
        assert result['obligation']['understanding_updated']
        assert result['mind_revision']==1+int(new_owner_evidence)
        if new_owner_evidence:
            assert result['obligation']['event_id']!=event
            assert session.state['owner_inputs'][-1]['data']==literal
            assert session.state['owner_inputs'][-1]['status']=='reviewed'
            assert counts=={'execution':2,'mind':2}
        else:
            assert result['obligation']['event_id']==event
            assert result['cost']==cost
            assert not session.state.get('owner_inputs')
        assert session.run()['cost']==result['cost']


def test_update_batch_capacity_and_text_bound_share_one_native_repair(tmp_path):
    # Current commits bound the update batch and Directive, not all retained knowledge.
    from Mind.chain import ChainMind
    from Mind.organ import MindOrgan, MindInput
    calls=[]
    def transport(wire):
        calls.append(wire)
        updates=[{'id':f'new:h{i}','kind':'belief','claim':f'Unresolved condition {i}.',
                  'status':'open','basis':[]} for i in range(17 if len(calls)==1 else 16)]
        if len(calls)==2:
            feedback=json.loads(wire['messages'][-1]['content'][0]['content'])
            assert {e['validator'] for e in feedback['field_errors']}=={'maxItems','maxLength'}
        return cognitive({'type':'directive','text':'x'*6001 if len(calls)==1 else 'Keep these conditions unresolved.'},updates)
    with MindOrgan(directory=tmp_path/'mind',model=ChainMind(transport)) as mind:
        receipt=mind.activate(MindInput('event','Review.','goal',1,'Assess unknown conditions.','run','waiting'))
        assert receipt.status=='accepted'
        assert len(mind.inspect().items)==16
        assert len(calls)==2
