"""Scripted dialogue protocol checks; all state and workspace files are temporary."""
import json
from copy import deepcopy
from datetime import timedelta
import threading

import pytest

from Conversation_Memory.answer import answer_system, answer_messages, parse_dialogue
from Conversation_Memory.facade import MemoryRead
from Conversation_Memory.engine.types import RecallResult
from Mind.dialogue_state import DialogueState
from Nervous.bus import EventBus
from config.lumina import load_config
from test_organs_a1 import rig, AT, Crash

class Script:
    client_kind='model'
    def __init__(self,rows):self.rows=list(rows);self.calls=[];self.rephrases=[]
    def complete_answer(self,system,messages):
        self.calls.append((system,messages))
        row=self.rows.pop(0)
        if callable(row):row=row()
        return json.dumps(row,ensure_ascii=False) if isinstance(row,dict) else row
    def complete_tools(self,system,messages,tools,*,thinking='disabled',tool_choice='auto'):
        self.calls.append((system,messages))
        row=self.rows.pop(0)
        if callable(row):row=row()
        row=deepcopy(row)
        if isinstance(row,dict):
            actions=row.pop('行动',[]) if '行动' in row else []
            if any(next(iter(action)) in ('派活','答复','取消','搁置') for action in actions):
                # Old scenario fixtures ended a thought after helper actions.
                # Native tools always require one tool-result continuation.
                self.rows.insert(0, {'说':''} if '说' in row else {'回复':''})
            mapping={'读':('read_file','path'),'回忆':('recall','clue'),
                     '派活':('delegate',None),'答复':('answer_helper',None),
                     '取消':('cancel_helper','helper'),'搁置':('hold_question','helper')}
            calls=[]
            for ordinal,action in enumerate(actions,1):
                name,value=next(iter(action.items()))
                tool,field=mapping[name]
                if field:
                    args={field:value}
                elif name=='派活':
                    args={english:value[chinese] for english,chinese in
                          [('goal','目标'),('reason','理由'),('acceptance','验收'),('context','背景')]
                          if chinese in value}
                else:
                    args={'helper':value['帮手'],'content':value['内容']}
                calls.append({'id':f'call{ordinal}','type':'function',
                              'function':{'name':tool,'arguments':json.dumps(args,ensure_ascii=False)}})
            content=json.dumps(row,ensure_ascii=False)
        else:
            content=row;calls=[]
        return {'choices':[{'message':{'role':'assistant','content':content,
                 'tool_calls':calls or None},'finish_reason':'tool_calls' if calls else 'stop'}]}
    def complete_text(self,system,messages):
        self.rephrases.append((system,messages))
        return '这是她最终说的话。'
    def summarize_hot_draft(self,*_):return '摘要'


def a2(tmp_path,rows,**extra):
    script=Script(rows)
    config={'mind':{'protocol':'a2'},'language':{'render':'mind_choice'},
            'workspace':{'path':str(tmp_path/'workspace')}}
    config.update(extra)
    runtime,scheduler,dreams=rig(tmp_path,script,config=config)
    runtime._compactor._allow_multi_speech=True
    return runtime,scheduler,script,dreams


def send(scheduler,tid='t1',text='你好'):
    scheduler.bus.publish(tid,'user.message',{'message':text})
    scheduler.drain_once()
    return scheduler.bus.get(tid+':reply')


def test_read_then_speech_and_private_handoff(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir();(workspace/'note.txt').write_text('确认过的文件内容')
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'','行动':[{'读':'note.txt'}]},
        {'回复':'读到了。','思绪':'我还想核对时间。','带着':['文件:note.txt']}])
    assert send(scheduler)['response']['text']=='读到了。'
    system,messages=model.calls[-1]
    assert 'PRIVATE_PERSONA_SENTINEL' in system and '思考中枢' in system
    assert '确认过的文件内容' in messages[-1]['content']
    assert '你的状态' in messages[0]['content']
    assert [t.text for t in runtime._hot_store.list_all_raw()]==['你好','读到了。']
    handoff=scheduler.bus.get('handoff',state=True)
    assert handoff['thoughts'][0]['text']=='我还想核对时间。'
    assert handoff['carry'][0]['id']=='文件:note.txt'


def test_active_recall_excluded_from_trace(tmp_path):
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'','行动':[{'回忆':'线索'}]},{'回复':'回答'}])
    count=0
    def recall(*_):
        nonlocal count
        count+=1
        return MemoryRead('自动 m1' if count==1 else '主动 m99',('m1',) if count==1 else ('m99',),RecallResult())
    runtime._memory.recall_and_render=recall
    send(scheduler)
    trace=next(iter(runtime._memory.traces.values()))
    assert trace[2].context_ids==('m1',) and count==2
    assert '主动 m99' in str(model.calls[-1][1])


def test_rephrase_final_hot_and_trace_failure_fallback(tmp_path):
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'要表达的原文','重组':True},
                                         {'回复':'失败时的原文','重组':True}])
    assert send(scheduler)['response']['text']=='这是她最终说的话。'
    record=scheduler.bus.get('t1:1:0:done')
    assert record['mind_text']=='要表达的原文' and record['final_text']=='这是她最终说的话。'
    assert next(iter(runtime._memory.traces.values()))[-1]=='这是她最终说的话。'
    assert '你的语言准确、简洁、从容' in model.rephrases[0][0]
    assert 'PRIVATE_PERSONA_SENTINEL' not in model.rephrases[0][0]
    def fail(*_):raise RuntimeError('private key')
    model.complete_text=fail
    assert send(scheduler,'t2')['response']['text']=='失败时的原文'
    assert scheduler.bus.get('t2:1:0:done')['rephrase_status']=='fallback'
    assert len(model.calls)==2


def test_inserted_message_waits_for_subsequent_speech(tmp_path):
    runtime,scheduler,model,_=a2(tmp_path,[])
    def first():
        scheduler.bus.publish('t2','user.message',{'message':'补充的消息'})
        return {'回复':'第一句','行动':[{'回忆':'线索'}]}
    model.rows=[first,{'回复':'我也收到你的补充了。'}]
    send(scheduler)
    assert scheduler.bus.get('t2:reply')['response']['text']=='我也收到你的补充了。'
    assert [t.text for t in runtime._hot_store.list_all_raw()]==['你好','第一句','补充的消息','我也收到你的补充了。']
    assert '补充的消息' in str(model.calls[-1][1])
    assert len(runtime._memory.traces)==1 and not scheduler.bus.pending('mind')


def test_fourth_step_skips_actions_and_leaves_interrupted_thought(tmp_path):
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'','行动':[{'回忆':'x'}]}]*4)
    send(scheduler)
    assert len(model.calls)==4
    assert scheduler.bus.get('t1:4:1:result') is None
    assert scheduler.bus.get('handoff',state=True)['thoughts'][-1]['text']=='（这次想到一半被打断了）'
    assert len(runtime._hot_store.list_all_raw())==1


def test_thought_window_carry_expires_and_empty_omitted(tmp_path):
    cfg=load_config(overrides={'mind':{'thought_window':2,'thought_max_chars':10}})
    state=DialogueState(EventBus(tmp_path/'bus'),cfg)
    def run(i,text,carry):
        tid=str(i);now=AT+timedelta(hours=i)
        before=state.begin(tid,now,{})
        state.finish(tid,now,text,carry,before['references'])
        return before
    assert run(1,'第一条',[])['block']==''
    run(2,'第二条',['思绪:1'])
    run(3,'第三条',['思绪:1'])
    fourth=run(4,'',[])
    assert '第一条' in fourth['block'] and '3小时前' in fourth['block'] # carry is shown once with its original time
    fifth=run(5,'123456789012345',[])
    assert '第一条' not in fifth['block']
    saved=state.bus.get('handoff',state=True)
    assert len(saved['thoughts'])==2 and saved['thoughts'][-1]['text']=='1234567890'
    assert all(t['text'] for t in saved['thoughts'])


def test_empty_state_omitted_and_context_order():
    messages=answer_messages({'message':'当前'},[],None,None,AT,protocol='a2',memory_block='记忆块')
    assert len(messages)==1 and '你的状态' not in str(messages)
    messages=answer_messages({'message':'当前'},[],None,None,AT,protocol='a2',state_block='你的状态：空闲',memory_block='记忆块')
    assert messages[0]['content']=='你的状态：空闲'
    assert messages[1]['content'].startswith('记忆块')
    assert 'PRIVATE_PERSONA_SENTINEL' in answer_system(AT,'记忆块','PRIVATE_PERSONA_SENTINEL',protocol='a2')


@pytest.mark.parametrize('bad',[{'重组':'true','行动':'读','思绪':{},'带着':'x'},
    {'重组':1,'行动':[{'读':'a','回忆':'b'},None,{'读':2}],'思绪':False,'带着':[None,2]}])
def test_bad_new_fields_ignored_without_retry(bad):
    parsed=parse_dialogue(json.dumps({'回复':'公开',**bad},ensure_ascii=False))
    assert parsed['reply']=='公开'
    assert not parsed['rephrase'] and not parsed['thought'] and not parsed['carry']


def test_malformed_json_does_not_leak_new_private_fields():
    parsed=parse_dialogue('"回复":"公开", "思绪":"私有", "带着":["私有引用"]')
    assert parsed['reply']=='公开'
    assert parsed['thought']=='私有'


@pytest.mark.parametrize('point',['response_saved','speech_saved'])
def test_a2_restart_uses_saved_reply_and_final_text_once(tmp_path,point):
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'原文','重组':True}])
    def crash(at):
        if at==point:raise Crash()
    scheduler.runner.checkpoint=crash
    scheduler.bus.publish('t1','user.message',{'message':'你好'})
    with pytest.raises(Crash):scheduler.drain_once()
    scheduler.runner.checkpoint=lambda _:None
    scheduler.drain_once()
    assert len(model.calls)==1 and len(model.rephrases)==1
    assert [t.role for t in runtime._hot_store.list_all_raw()]==['user','assistant']


def test_file_sandbox_truncation_and_symlink(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    (workspace/'long').write_text('abcdef')
    (tmp_path/'outside').write_text('secret')
    (workspace/'link').symlink_to(tmp_path/'outside')
    import os
    os.mkfifo(workspace/'pipe')
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'','行动':[{'读':'long'},{'读':'../outside'},{'读':'link'},{'读':'/etc/passwd'},{'读':'pipe'}]}, {'回复':'好了'}],mind={'protocol':'a2','read_max_chars':3})
    send(scheduler)
    result=scheduler.bus.get('t1:1:1:result')
    assert 'abc' in result['text'] and '已截断' in result['text']
    for i in (2,3,4,5):assert scheduler.bus.get(f't1:1:{i}:result')['status']=='error'
    assert 'secret' not in str(model.calls)


def test_multi_speech_compacts_and_traces_final_text(tmp_path):
    runtime,scheduler,model,dreams=a2(tmp_path,[{'回复':'一句','行动':[{'回忆':'x'}]},{'回复':'两句'}]*3)
    for i in range(3):send(scheduler,str(i))
    assert runtime._compactor._cold_store.list_all_turns()
    assert runtime._hot_store.read_summary().content=='摘要'
    assert all(trace[-1]=='一句\n两句' for trace in runtime._memory.traces.values())
    assert len(dreams)==3


def test_a2_recall_uses_p8_timezone_even_though_draft_stores_utc(tmp_path):
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'好'}])
    times=[]
    def recall(message,hot,now):
        times.append(now)
        return MemoryRead('',(),RecallResult())
    runtime._memory.recall_and_render=recall
    send(scheduler)
    assert times[0].utcoffset()==timedelta(hours=8)


def test_api_first_reply_precedes_completion_and_status_is_live(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from core.main import create_app
    from test_memory_v1_chat import FakeMemory
    started=threading.Event();release=threading.Event()
    def second():
        started.set()
        assert release.wait(10)
        return {'回复':'后一句'}
    model=Script([{'回复':'第一句','行动':[{'回忆':'测试'}]},second])
    monkeypatch.setattr('core.main.build_memory_model_from_env',lambda *_:None)
    app=create_app(draft_store_path=tmp_path/'hot',memory_dir=tmp_path/'memory',
        model_client=model,memory=FakeMemory(),env_file_path=None,enable_compaction=False,
        recall_enabled=False,lumina_config={'mind':{'protocol':'a2'},
                                            'language':{'render':'mind_choice'}})
    with TestClient(app) as client:
        try:
            result=client.post('/api/chat',json={'message':'你好'}).json()
            assert result['response']['text']=='第一句'
            assert started.wait(5)
            assert client.get('/api/status').json()['lumina']['focus']=='在回你的消息'
            assert client.get('/api/history').json()['turns'][-1]['content']=='第一句'
        finally:release.set()
    assert not app.state.dialogue_scheduler._thread.is_alive()
    assert app.state.hot_draft_store.list_all_raw()[-1].text=='后一句'


def test_api_timeout_keeps_input_event_and_future_speech(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from core.main import create_app
    from test_memory_v1_chat import FakeMemory
    release=threading.Event()
    def slow():
        assert release.wait(10)
        return {'回复':'迟到的回复'}
    monkeypatch.setattr('core.main.build_memory_model_from_env',lambda *_:None)
    app=create_app(draft_store_path=tmp_path/'hot',memory_dir=tmp_path/'memory',
        model_client=Script([slow]),memory=FakeMemory(),env_file_path=None,enable_compaction=False,
        recall_enabled=False,lumina_config={'mind':{'protocol':'a2'},
                                            'language':{'render':'mind_choice'},
                                            'chat':{'first_reply_timeout_s':0.05}})
    with TestClient(app) as client:
        try:
            result=client.post('/api/chat',json={'message':'你好'}).json()
            assert result['response']['type']=='fallback'
            assert len(app.state.nervous_bus.pending('mind'))==1
        finally:release.set()
    assert app.state.hot_draft_store.list_all_raw()[-1].text=='迟到的回复'


def test_automatic_dream_runs_after_compaction_and_only_once(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from core.main import create_app
    from test_memory_v1_chat import FakeMemory
    from Conversation_Memory.facade import DreamOutcome
    dream_called=threading.Event();observed=[]
    memory=FakeMemory();memory.has_cold_cursor=lambda:True
    memory._embedder=lambda:object()
    memory.unintegrated_turn_count=lambda cold:len(cold)
    def dream(cold,**kwargs):
        observed.append(len(cold));dream_called.set()
        return DreamOutcome('no_window')
    memory.dream_once=dream
    monkeypatch.setenv('LUMINA_DREAM_TRIGGER_TURNS','2')
    monkeypatch.setattr('core.main.build_memory_model_from_env',lambda *_:object())
    model=Script([{'回复':'第一句','行动':[{'回忆':'x'}]},{'回复':'第二句'}, {'回复':'结束'}])
    app=create_app(draft_store_path=tmp_path/'hot',memory_dir=tmp_path/'memory',
        model_client=model,memory=memory,env_file_path=None,
        retain_recent_raw_turns=1,max_raw_turns_before_compression=3,
        lumina_config={'mind':{'protocol':'a2'}})
    with TestClient(app) as client:
        client.post('/api/chat',json={'message':'一'})
        first_id=app.state.nervous_bus.conn.execute(
            "SELECT id FROM events WHERE kind='user.message' ORDER BY seq LIMIT 1").fetchone()[0]
        assert app.state.nervous_bus.wait(first_id+':finished',5)
        client.post('/api/chat',json={'message':'二'})
        assert dream_called.wait(5)
    assert observed==[3] # one complete user / two-assistant segment, once after compaction


def test_a2_memory_references_are_visible_and_carryable_without_changing_p8(tmp_path):
    from Conversation_Memory.engine.types import RecalledMemory
    memory=RecalledMemory('m1','固定的记忆正文',1,1,1,(),(),'昨天')
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'好','带着':['记忆:m1']}])
    runtime._memory.recall_and_render=lambda *_:MemoryRead('【此刻想起的】\n- 固定的记忆正文',('m1',),RecallResult(near=(memory,)))
    send(scheduler)
    prepared=scheduler.bus.get('t1:prepared')
    assert prepared['read']['block']=='【此刻想起的】\n- 固定的记忆正文'
    assert '[记忆:m1]' in model.calls[0][1][-1]['content']
    assert scheduler.bus.get('handoff',state=True)['carry'][0]['text']=='固定的记忆正文'


def test_active_recall_child_reference_is_carryable_but_not_reinforced(tmp_path):
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'','行动':[{'回忆':'线索'}]},
        {'回复':'回答','带着':['回忆:t1:1:1.2']}])
    runtime._memory.recall_and_render=lambda *_:MemoryRead('标题\n回忆正文',(),RecallResult())
    send(scheduler)
    carry=scheduler.bus.get('handoff',state=True)['carry']
    assert carry[0]['id']=='回忆:t1:1:1.2' and carry[0]['text']=='回忆正文'
    assert next(iter(runtime._memory.traces.values()))[2].context_ids==()


def test_actual_state_is_projected_into_sqlite(tmp_path):
    from Nervous.lumina_state import LuminaState
    bus=EventBus(tmp_path/'bus.sqlite');lock=threading.Lock()
    state=LuminaState(lock,bus)
    state.thinking(True)
    assert bus.get('lumina',state=True)['focus']=='在回你的消息'
    with lock:
        state.snapshot()
        assert bus.get('lumina',state=True)['states']==['空闲','做梦']
    state.thinking(False)
    assert bus.get('lumina',state=True)=={'states':['空闲'],'focus':'','thinking':False,'executing':False}
