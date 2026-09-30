"""MessageRuntime reads the whole Hot window and records only after Answer."""
from datetime import datetime, timedelta, timezone

from Conversation_Memory.engine.types import RecallResult
from Conversation_Memory.facade import MemoryRead
from core.contracts import ChatRequest, DraftTurn
from core.draft_store import JsonlDraftStore
from core.message_runtime import MessageRuntime

NOW=datetime(2026,8,1,12,tzinfo=timezone(timedelta(hours=8)))


class Clock:
    def now(self): return NOW


class RecordingMemory:
    def __init__(self): self.reads=[]; self.traces=[]
    def recall_and_render(self,message,hot,now):
        self.reads.append((message,list(hot),now))
        return MemoryRead('记忆正文',(),RecallResult())
    def record_trace(self,*args): self.traces.append(args)


class RecordingModel:
    client_kind='model'
    def __init__(self): self.requests=[]
    def complete_answer(self,system,messages):
        self.requests.append((system,messages))
        return '{"理解":"内部","回复":"公开回答"}'


def test_whole_raw_hot_and_trace_after_assistant(tmp_path):
    hot=JsonlDraftStore(tmp_path/'hot.jsonl')
    for i in range(14):
        hot.append_turn(DraftTurn(role='user' if i%2==0 else 'assistant',
            text=f'old-{i}', turn_id=f'{i:032x}',
            created_at=NOW-timedelta(minutes=14-i),
            source_timezone='Asia/Shanghai',timezone_source='configured_default'))
    memory,model=RecordingMemory(),RecordingModel()
    runtime=MessageRuntime(hot_store=hot,model_client=model,
                           chat_background='人设',memory=memory,clock=Clock())
    result=runtime.handle_chat(ChatRequest(message='问题'))
    assert result.response.response.text=='公开回答'
    assert len(memory.reads[0][1])==14
    assert len(model.requests[0][1])==15
    assert model.requests[0][1][-1]['role']=='user'
    assert memory.traces[0][0]==hot.list_all_raw()[-1].turn_id
    assert memory.traces[0][-1]=='公开回答'


def test_recall_error_does_not_block_answer(tmp_path):
    memory,model=RecordingMemory(),RecordingModel()
    def broken(*args): raise RuntimeError('private')
    memory.recall_and_render=broken
    runtime=MessageRuntime(hot_store=JsonlDraftStore(tmp_path/'hot.jsonl'),
        model_client=model,chat_background='人设',memory=memory,clock=Clock())
    result=runtime.handle_chat(ChatRequest(message='问题'))
    assert result.response.response.text=='公开回答'
    assert '记忆正文' not in model.requests[0][0]
    assert 'memory_recall_failed' in result.events


def test_natural_language_reply_is_not_retried(tmp_path):
    model=RecordingModel()
    model.complete_answer=lambda system,messages: '自然语言完整回答'
    runtime=MessageRuntime(hot_store=JsonlDraftStore(tmp_path/'hot.jsonl'),
        model_client=model,chat_background='人设',clock=Clock())
    assert runtime.handle_chat(ChatRequest(message='问题')).response.response.text=='自然语言完整回答'
