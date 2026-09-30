"""Core's Hot/Cold and recall seam for the event-driven dialogue runner.

Draft and Memory owners remain unchanged; serialized snapshots permit recovery
without recalling again or rebuilding a model request against newer history.
"""
from dataclasses import asdict
from datetime import datetime

from Conversation_Memory.engine.clock import TZ
from Conversation_Memory.answer import answer_system, answer_messages, number_dialogue_memories
from Conversation_Memory.facade import MemoryRead
from Conversation_Memory.engine.types import RecallResult, RecalledMemory, RawHit
from core.contracts import DraftTurn, ChatResponse, AssistantResponse, ChatCompactionResponse
from core.memory_adapter import draft_turns_to_memory
from core.model_client import MOCK_ASSISTANT_TEXT, TruncatedSummaryError
from core.turn_provenance import resolve_source_timezone


def dump_read(read):
    result = asdict(read.result)
    for hit in result['raw']:
        hit['time'] = hit['time'].isoformat()
    return {'block':read.block,'context_ids':list(read.context_ids),'result':result}


def load_read(data):
    result = data['result']
    groups = {key:tuple(RecalledMemory(**{**r,'parts':tuple(r['parts']),'sources':tuple(r['sources'])})
                        for r in result[key]) for key in ('near','remote','core')}
    return MemoryRead(data['block'],tuple(data['context_ids']),RecallResult(**groups,
        raw=tuple(RawHit(**{**r,'time':datetime.fromisoformat(r['time'])}) for r in result['raw']),
        diagnostics=result['diagnostics']))


def response(text=MOCK_ASSISTANT_TEXT, kind='fallback', phase='model_chat', compaction=None):
    return ChatResponse(app='lumina',status='ok',phase=phase,message_consumed=True,
        response=AssistantResponse(type=kind,text=text),
        compaction=ChatCompactionResponse(**(compaction or {
            'status':'not_needed','archived_turns':0,'summary_updated':False}))).model_dump()


class DialogueIO:
    def __init__(self, runtime, writer_lock):
        self.runtime = runtime
        self.lock = writer_lock

    @property
    def model(self):
        return self.runtime._model_client

    @property
    def factory(self):
        return self.runtime._turn_factory

    def new_user(self, request):
        tz, source = resolve_source_timezone(request.get('client_timezone'), self.factory.default_timezone)
        return self.factory.create(role='user',text=request.get('message') or request.get('text') or '',
                                   source_timezone=tz,timezone_source=source).model_dump(mode='json')

    def new_assistant(self, text, user):
        return self.factory.create(role='assistant',text=text,source_timezone=user['source_timezone'],
                                   timezone_source=user['timezone_source']).model_dump(mode='json')

    def append(self, turn):
        with self.lock:
            self.runtime._hot_store.append_turn(DraftTurn.model_validate(turn))

    def prepare(self, user, *, protocol='a1', state_block=''):
        at = datetime.fromisoformat(user['created_at'])
        if protocol == 'a2':
            at = at.astimezone(TZ)
        with self.lock:
            context = self.runtime._hot_store.read_context()
        hot = draft_turns_to_memory(context.raw_turns, at)
        summary = context.summary
        until = datetime.fromisoformat(summary.summary_until) if summary and summary.summary_until else None
        read = MemoryRead('',(),RecallResult())
        if self.runtime._memory and self.runtime._recall_enabled:
            try:
                read = self.runtime._memory.recall_and_render(user['text'],hot,at)
            except Exception:
                pass
        block, references = number_dialogue_memories(read) if protocol=='a2' else (read.block,{})
        system = answer_system(at,block,self.runtime._background,protocol=protocol)
        messages = answer_messages({'message':user['text']},hot,summary.content if summary else None,
                                   until,at,protocol=protocol,state_block=state_block,memory_block=block)
        return {'system':system,'messages':messages,'read':dump_read(read),
                'recent':[t.model_dump(mode='json') for t in context.raw_turns],
                'protocol':protocol,'state_block':state_block,'user':user,
                'memory_block':block,'memory_references':references}

    def recall(self, clue, user):
        if not self.runtime._memory or not self.runtime._recall_enabled:
            return MemoryRead('',(),RecallResult())
        at = datetime.fromisoformat(user['created_at']).astimezone(TZ)
        with self.lock:
            hot = self.runtime._hot_store.read_context().raw_turns
        return self.runtime._memory.recall_and_render(clue,draft_turns_to_memory(hot,at),at)

    def trace(self, turn, read, noticed, text):
        if self.runtime._memory and self.runtime._recall_enabled:
            self.runtime._memory.record_trace(turn['turn_id'],datetime.fromisoformat(turn['created_at']),
                                             load_read(read),noticed,text)

    def finish(self, bus=None, thought_id=None):
        compact = self.runtime._compactor
        if compact is None:
            return {'status':'not_needed','archived_turns':0,'summary_updated':False}
        original = compact._summarizer
        def durable_summary(old, moved):
            key = thought_id+':summary'
            saved = bus.get(key+':response')
            if saved is not None:
                if saved['status']=='ok':return saved['text']
                if saved['status']=='truncated':raise TruncatedSummaryError('summary_truncated')
                raise RuntimeError('summary_failed')
            if bus.get(key+':request') is not None:
                raise RuntimeError('summary_outcome_unknown')
            bus.put(key+':request',{'old':old,'moved':[t.storage_turn() for t in moved]})
            try:
                text = original(old,moved)
                bus.put(key+':response',{'status':'ok','text':text})
                return text
            except Exception as error:
                bus.put(key+':response',{'status':'truncated' if isinstance(error,TruncatedSummaryError) else 'failed'})
                raise
        with self.lock:
            try:
                if bus is not None and original is not None:
                    compact._summarizer=durable_summary
                return asdict(compact.maybe_compact())
            except Exception:
                return {'status':'failed','archived_turns':0,'summary_updated':False}
            finally:
                compact._summarizer=original
