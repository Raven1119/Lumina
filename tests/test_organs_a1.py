"""A1 frozen v1 oracle, durable replay, routing and lifecycle checks (no network)."""
import json
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import pytest

from config.lumina import load_config
from Conversation_Memory.facade import MemoryRead
from Conversation_Memory.engine.types import RecallResult
from core.contracts import ChatRequest
from core.cold_draft_store import ColdDraftStore
from core.draft_store import JsonlDraftStore
from core.hot_draft_compactor import HotDraftCompactor
from core.message_runtime import MessageRuntime
from core.dialogue_io import DialogueIO
from core.model_client import DeepSeekAnthropicModelClient
from Mind.runner import DialogueRunner
from Language.channel import LanguageChannel
from Nervous.bus import EventBus
from Nervous.scheduler import DialogueScheduler
from Nervous.lumina_state import LuminaState

AT=datetime(2026,1,1,tzinfo=timezone.utc)

class Clock:
    def __init__(self): self.n=0
    def now(self):
        self.n+=1
        return AT+timedelta(seconds=self.n)
class IDs:
    def __init__(self): self.n=0
    def new_id(self):
        self.n+=1
        return f'{self.n:032x}'
class Memory:
    def __init__(self): self.traces={}
    def recall_and_render(self,*_): return MemoryRead('【记忆】\nm1｜茶',('m1',),RecallResult())
    def record_trace(self,*args): self.traces.setdefault(args[0],args)


def rig(path,model,*,config=None,checkpoint=None):
    # Freeze the A1 oracle even when production's default protocol is A2.
    choices=dict(config or {})
    choices['mind']={'protocol':'a1', **choices.get('mind', {})}
    cfg=load_config(overrides=choices)
    memory=Memory()
    hot,cold=JsonlDraftStore(path/'hot'),ColdDraftStore(path/'cold')
    compact=HotDraftCompactor(hot,cold,path/'compact',summarizer=model.summarize_hot_draft,
        retain_recent_raw_turns=2,max_raw_turns_before_compression=4)
    runtime=MessageRuntime(hot_store=hot,model_client=model,chat_background='PRIVATE_PERSONA_SENTINEL',
        memory=memory,compactor=compact,clock=Clock(),turn_id_factory=IDs())
    io=DialogueIO(runtime,threading.Lock())
    bus=EventBus(path/'bus.sqlite')
    live=LuminaState(threading.Lock())
    channel=LanguageChannel(bus,io,cfg)
    dreams=[]
    scheduler=DialogueScheduler(bus,None,channel,lambda response:dreams.append((response,len(cold.list_all_turns()))))
    runner=DialogueRunner(bus,io,cfg,live,scheduler.emit,checkpoint)
    scheduler.runner=runner
    return runtime,scheduler,dreams


def model():
    requests=[]
    answers=0
    def transport(request):
        nonlocal answers
        requests.append(request.content)
        body=json.loads(request.content)
        if body['system'].startswith('You maintain'):
            return httpx.Response(200,json={'content':[{'type':'text','text':'固定滚动摘要'}]})
        answers+=1
        if answers==3:
            return httpx.Response(503,json={'error':'private provider body'})
        return httpx.Response(200,json={'content':[{'type':'text','text':'私有理解", "借鉴":"", "顺带":"", "回复":"公开回复"}'}]})
    client=DeepSeekAnthropicModelClient('test','https://example.invalid','deepseek-flash',
        http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    client.answer_prefill_enabled=True
    return client,requests


def test_a1_v1_request_and_all_owner_bytes(tmp_path,monkeypatch):
    monkeypatch.setattr(HotDraftCompactor,'_utc_now',staticmethod(lambda:'2026-01-01T00:00:00.000000Z'))
    monkeypatch.setattr(ColdDraftStore,'_utc_now',staticmethod(lambda:'2026-01-01T00:00:00.000000Z'))
    old_model,old_requests=model();new_model,new_requests=model()
    old,_,_=rig(tmp_path/'v1',old_model)
    new,scheduler,dreams=rig(tmp_path/'a1',new_model)
    for i in range(23):
        request=ChatRequest(message=f'固定消息{i}',client_timezone='Asia/Shanghai')
        expected=old.handle_chat(request).response.model_dump()
        scheduler.bus.publish(str(i),'user.message',request.model_dump())
        scheduler.drain_once()
        assert scheduler.bus.get(str(i)+':reply')==expected
    assert old_requests==new_requests # exact HTTP body: system/messages/prefill/parameters
    for name in ('hot','cold','compact'):
        assert (tmp_path/'v1'/name).read_bytes()==(tmp_path/'a1'/name).read_bytes()
    assert old._memory.traces==new._memory.traces
    assert len(dreams)==23 and dreams[-1][1]>=40
    assert any(json.loads(r)['max_tokens']==2000 for r in new_requests)


class Crash(BaseException): pass

@pytest.mark.parametrize('point',['response_saved','speech_saved'])
def test_restart_speaks_once_and_never_recalls_known_response(tmp_path,point):
    client,requests=model()
    def crash(at):
        if at==point:raise Crash()
    runtime,scheduler,_=rig(tmp_path,client,checkpoint=crash)
    scheduler.bus.publish('thought','user.message',{'message':'你好'})
    with pytest.raises(Crash):scheduler.drain_once()
    scheduler.bus.close()
    # Fresh bus, runner and ID factory simulate a process restart.
    restored,new_scheduler,_=rig(tmp_path,client)
    restored._turn_factory._id_factory.n=10 # a fresh UUID source never repeats old IDs
    new_scheduler.drain_once()
    assert [t.role for t in restored._hot_store.list_all_raw()]==['user','assistant']
    assert len(requests)==1
    assert not new_scheduler.bus.pending('mind')


def test_event_identity_and_integrity(tmp_path):
    bus=EventBus(tmp_path/'bus.sqlite')
    bus.publish('one','user.message',{'message':'a'})
    bus.publish('one','user.message',{'message':'a'})
    assert len(bus.pending('mind'))==1
    with pytest.raises(ValueError):bus.publish('one','user.message',{'message':'b'})
    bus.conn.execute("UPDATE events SET body='{}'");bus.conn.commit()
    with pytest.raises(ValueError,match='integrity'):bus.pending('mind')


def test_live_state_dream_lock():
    lock=threading.Lock();state=LuminaState(lock)
    assert state.snapshot()['states']==['空闲']
    with lock:
        state.thinking(True)
        assert state.snapshot()=={'states':['空闲','做梦'],'focus':'在回你的消息','thinking':True,'executing':False}
    assert state.snapshot()['states']==['空闲']
