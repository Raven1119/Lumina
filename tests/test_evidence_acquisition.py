"""The removed source-tool path is replaced by a bounded local memory read."""
from datetime import datetime, timezone

from Conversation_Memory.engine.types import RecallResult
from Conversation_Memory.facade import MemoryRead
from core.contracts import ChatRequest
from core.draft_store import JsonlDraftStore
from core.message_runtime import MessageRuntime


class Clock:
    def now(self): return datetime(2026,8,1,tzinfo=timezone.utc)


class Memory:
    def __init__(self): self.calls=0
    def recall_and_render(self, message, hot, now):
        self.calls += 1
        return MemoryRead('有界本地记忆',(),RecallResult())
    def record_trace(self,*args): pass


class Model:
    client_kind='model'
    def __init__(self): self.messages=None; self.system=None; self.calls=0
    def complete_answer(self, system, messages):
        self.calls += 1; self.messages=messages; self.system=system
        return '{"回复":"回答"}'


def test_one_local_read_and_one_answer_without_tool_rounds(tmp_path):
    memory,model=Memory(),Model()
    runtime=MessageRuntime(hot_store=JsonlDraftStore(tmp_path/'hot.jsonl'),
        model_client=model,chat_background='人设',memory=memory,clock=Clock())
    reply=runtime.handle_chat(ChatRequest(message='问题')).response.response.text
    assert reply=='回答'
    assert memory.calls==1 and model.calls==1
    assert '有界本地记忆' in model.system
    assert len(model.messages)==1 and model.messages[0]['role']=='user'
