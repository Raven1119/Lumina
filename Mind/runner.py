"""The Chat dialogue thought. Independent of the old Mind cognitive chain."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from core.dialogue_io import response
from Mind.dialogue_state import DialogueState
from Mind.helper_actions import parse_event
from Mind.tools import MindTools, MIND_TOOLS
from Mind.usage import record_thought
from Execution.deepseek_model import chat_assistant_message, chat_tool_calls
from Execution.pool import AUTO_REPLY
from core.model_client import MOCK_ASSISTANT_TEXT
from Conversation_Memory.answer import parse_answer_v5_tolerant, fallback_answer_v5, parse_dialogue, answer_messages
from Conversation_Memory.engine.clock import TZ


class DialogueRunner:
    def __init__(self, bus, io, config, state, emit, checkpoint=None, pool=None):
        self.bus, self.io, self.config, self.state, self.emit = bus, io, config, state, emit
        self.checkpoint = checkpoint or (lambda _: None)
        self.pool = pool
        self.tools = MindTools(bus, io, config, pool)

    def _call(self, key, request):
        result = self.bus.get(key+':response')
        if result is not None:
            return result
        # A reserved request with no durable response has an unknown outcome.
        # Do not silently charge another provider request after process death.
        if self.bus.get(key+':request') is not None:
            return self.bus.put(key+':response',{'raw':'','failed':True,'reason':'outcome_unknown'})
        model = self.io.model
        durable = dict(request)
        if hasattr(model, 'answer_request'):
            durable['wire'] = model.answer_request(request['system'], request['messages'])
        self.bus.put(key+':request',durable)
        try:
            if getattr(model,'client_kind','model')=='mock':
                raw=model.generate([],request['user']['text'],system_prompt=request['system'])
            elif hasattr(model,'complete_answer'):
                raw=model.complete_answer(request['system'],request['messages'])
            else:
                raw=model.generate([{'role':m['role'],'text':m['content']} for m in request['messages'][:-1]],
                                   request['messages'][-1]['content'],system_prompt=request['system'])
            if not isinstance(raw,str) or not raw.strip():
                raise ValueError('empty_answer')
            result={'raw':raw,'failed':False}
        except Exception:
            result={'raw':'','failed':True,'reason':'model_call_failed'}
        self.bus.put(key+':response',result)
        self.checkpoint('response_saved')
        return result

    def _native_call(self, key, system, messages, *, thinking='disabled',
                     tool_choice='auto'):
        """Journal the exact native request/response before any tool can run."""
        saved = self.bus.get(key+':response')
        if saved is not None:
            return saved
        if self.bus.get(key+':request') is not None:
            return self.bus.put(key+':response',
                                {'raw': None, 'failed': True, 'reason': 'outcome_unknown'})
        model = self.io.model
        if hasattr(model, 'tool_request'):
            wire = model.tool_request(system, messages, MIND_TOOLS,
                                      thinking=thinking, tool_choice=tool_choice)
        else:
            wire = {'system': system, 'messages': messages, 'tools': MIND_TOOLS,
                    'thinking': thinking, 'tool_choice': tool_choice}
        self.bus.put(key+':request', wire)
        try:
            if getattr(model, 'client_kind', 'model') == 'mock':
                text = model.generate([], messages[-1]['content'], system_prompt=system)
                raw = {'choices': [{'message': {'role': 'assistant',
                    'content': text, 'tool_calls': None}, 'finish_reason': 'stop'}]}
            elif hasattr(model, 'complete_tools'):
                raw = model.complete_tools(system, messages, MIND_TOOLS,
                                           thinking=thinking, tool_choice=tool_choice)
            else:
                text = model.complete_answer(system, messages)
                raw = {'choices': [{'message': {'role': 'assistant',
                    'content': text, 'tool_calls': None}, 'finish_reason': 'stop'}]}
            chat_tool_calls(chat_assistant_message(raw))
            result = {'raw': raw, 'failed': False}
        except Exception:
            result = {'raw': None, 'failed': True, 'reason': 'model_call_failed'}
        self.bus.put(key+':response', result)
        self.checkpoint('response_saved')
        return result

    @staticmethod
    def _continue_with_tools(message, calls, results):
        assistant = {'role': 'assistant', 'content': message.get('content'),
                     'tool_calls': calls}
        if 'reasoning_content' in message:
            assistant['reasoning_content'] = message['reasoning_content']
        return [assistant, *({'role': 'tool', 'tool_call_id': call['id'],
                              'content': result['text']}
                             for call, result in zip(calls, results, strict=True))]

    def run(self, event):
        if event['kind'] != 'user.message':
            return self.run_events([event])
        if self.bus.get(event['id']+':finished'):
            self.bus.ack_many(self.bus.get(event['id']+':inputs') or [event['id']])
            return self.bus.get(event['id']+':reply')
        protocol=self.bus.get(event['id']+':protocol')
        if protocol is None:
            protocol=self.bus.put(event['id']+':protocol',self.config['mind']['protocol'])
        if protocol=='a2':
            return self._run_a2(event)
        return self._run_a1(event)

    def _run_a1(self, event):
        tid=event['id']
        handoff=self.bus.get('handoff',state=True)
        if handoff and handoff.get('carry'):
            self.bus.put('handoff',{**handoff,'carry':[]},state=True)
        self.state.thinking(True)
        try:
            user=self.bus.get(tid+':user')
            if user is None:
                user=self.bus.put(tid+':user',self.io.new_user(event['body']))
            prepared=self.bus.get(tid+':prepared')
            if prepared is None:
                prepared=self.bus.put(tid+':prepared',self.io.prepare(user))
            if self.bus.get(tid+':user:written') is None:
                self.io.append(user)
                self.bus.put(tid+':user:written',True)
            model=self.io.model
            mock=getattr(model,'client_kind','model')=='mock'
            phase='mock_chat' if mock else 'model_chat'
            result=self._call(tid+':step:1',prepared)
            noticed={k:'' for k in ('理解','借鉴','顺带')}
            kind='mock' if mock else 'model'
            if result['failed']:
                text,kind=MOCK_ASSISTANT_TEXT,'fallback'
            elif mock:
                text=result['raw']
            else:
                text,noticed,found,*_=parse_answer_v5_tolerant(result['raw'])
                if not found:
                    text=fallback_answer_v5(result['raw'])
                if not text:
                    text,kind=MOCK_ASSISTANT_TEXT,'fallback'
            said=self.emit(tid+':1:0','mind.say',{'text':text,'user':user,'kind':kind,'phase':phase})
            self.checkpoint('speech_saved')
            self.emit(tid+':trace','mind.trace',{'turn':said['turn'],'read':prepared['read'],
                                                'noticed':noticed,'text':said['final_text']})
            compact=self.bus.get(tid+':compaction')
            if compact is None:
                compact=self.bus.put(tid+':compaction',self.io.finish(self.bus,tid))
            final=response(said['final_text'],kind,phase,compact)
            self.bus.put(tid+':reply',final)
            self.bus.publish(tid+':spoke','mind.spoke',{'response':final})
            record_thought(self.bus,tid,'dialogue')
            self.bus.put(tid+':finished',True)
            self.bus.ack(tid)
            return final
        finally:
            self.state.thinking(False)


    def _input(self, event):
        key=event['id']+':user'
        user=self.bus.get(key)
        if user is None:
            user=self.bus.put(key,self.io.new_user(event['body']))
        if self.bus.get(key+':written') is None:
            self.io.append(user)
            self.bus.put(key+':written',True)
        return user

    def _run_a2(self,event):
        self.tools.pool=self.pool
        tid=event['id'];self.state.thinking(True)
        try:
            user=self.bus.get(tid+':user')
            if user is None:user=self.bus.put(tid+':user',self.io.new_user(event['body']))
            at=datetime.fromisoformat(user['created_at'])
            handoff=DialogueState(self.bus,self.config)
            state=handoff.begin(tid,at,self.state.snapshot())
            prepared=self.bus.get(tid+':prepared')
            if prepared is None:
                prepared=self.bus.put(tid+':prepared',self.io.prepare(user,protocol='a2',state_block=state['block']))
            self._input(event)
            input_events=[tid];unanswered=[tid];speeches=[];spoken_texts=set()
            refs={**state['references'],**prepared['memory_references']}
            history=[];recent=list(prepared['recent'])+[user]
            max_steps=self.config['mind']['dialogue_max_steps']
            phase='mock_chat' if getattr(self.io.model,'client_kind','model')=='mock' else 'model_chat'
            parsed={'thought':'','carry':[]}
            failed_reason=None
            interrupted=False
            for step in range(1,max_steps+1):
                key=f'{tid}:step:{step}'
                additions=self.bus.get(key+':inbox')
                if additions is None:
                    additions=[]
                    if step>1:
                        additions=[e for e in self.bus.pending('mind')
                                   if e['kind']=='user.message' and e['id'] not in input_events]
                    self.bus.put(key+':inbox',additions)
                for incoming in additions:
                    added=self._input(incoming)
                    input_events.append(incoming['id']);unanswered.append(incoming['id']);recent.append(added)
                    history.extend(answer_messages({'message':added['text']},[],None,None,datetime.fromisoformat(added['created_at'])))
                messages=[*deepcopy(prepared['messages']),*deepcopy(history)]
                result=self._native_call(key,prepared['system'],messages,
                    tool_choice='none' if step==max_steps else 'auto')
                if result['failed']:
                    parsed={'reply':'','noticed':{},'rephrase':False,'thought':'','carry':[],
                            'protocol_residue':False}
                    calls=[];message={'content':''};kind='fallback'
                    failed_reason=result.get('reason','model_call_failed')
                else:
                    message=chat_assistant_message(result['raw'])
                    calls=chat_tool_calls(message)
                    content=message.get('content')
                    if phase=='mock_chat':
                        parsed={'reply':content if isinstance(content,str) else '',
                                'noticed':{},'rephrase':False,'thought':'','carry':[],
                                'protocol_residue':False}
                    else:
                        parsed=parse_dialogue(content if isinstance(content,str) else '',
                                              allow_unlabeled=not calls)
                    kind='mock' if phase=='mock_chat' else 'model'
                if parsed.get('protocol_residue'):
                    self.bus.put(key+':protocol_residue',True)
                reply=parsed['reply']
                if reply and reply in spoken_texts:
                    self.bus.put(key+':duplicate_speech',reply)
                elif reply:
                    said=self.emit(f'{tid}:{step}:0','mind.say',{'text':reply,'user':user,
                        'kind':kind,'phase':phase,'rephrase':parsed['rephrase'],
                        'memory_block':prepared['memory_block'],'status_line':state['status_line'],
                        'recent':recent,'protocol':'a2'})
                    spoken_texts.add(reply)
                    speeches.append({**said,'noticed':parsed['noticed']})
                    recent.append(said['turn'])
                    self.checkpoint('speech_saved')
                    for input_id in unanswered:
                        if self.bus.get(input_id+':reply') is None:
                            self.bus.put(input_id+':reply',said['response'])
                    unanswered=[]
                if step==max_steps and calls:
                    interrupted=True
                    self.bus.put(key+':skipped_tool_calls',calls)
                    break
                if not calls:break
                results=[]
                limit=self.config['mind']['tool_max_calls_per_step']
                for ordinal,call in enumerate(calls,1):
                    value=self.tools.execute(f'{tid}:{step}:{ordinal}',call,user,
                                             too_many=ordinal>limit)
                    results.append(value);refs[value['id']]=value
                    refs.update(value.get('references',{}))
                history.extend(self._continue_with_tools(message,calls,results))
            thought=parsed['thought']
            if interrupted:
                note='（这次想到一半被打断了）'
                thought=thought[:max(0,self.config['mind']['thought_max_chars']-len(note))]+note
            handoff.finish(tid,self.io.factory._clock.now(),thought,parsed['carry'],refs)
            if speeches:
                self.emit(tid+':trace','mind.trace',{'turn':speeches[0]['turn'],'read':prepared['read'],
                    'noticed':speeches[0]['noticed'],'text':'\n'.join(s['final_text'] for s in speeches)})
            compact=self.bus.get(tid+':compaction')
            if compact is None:compact=self.bus.put(tid+':compaction',self.io.finish(self.bus,tid))
            for input_id in input_events:
                if self.bus.get(input_id+':reply') is None:
                    if failed_reason:
                        reason='结果未知' if failed_reason=='outcome_unknown' else '模型调用失败'
                        self.bus.put(input_id+':reply',response('这次没能回应：'+reason,
                            kind='error',phase=phase,compaction=compact))
                    else:
                        self.bus.put(input_id+':reply',response('',kind='none',
                            phase=phase,compaction=compact))
            if speeches:
                self.bus.publish(tid+':spoke','mind.spoke',{'response':speeches[0]['response']})
            self.bus.put(tid+':inputs',input_events)
            record_thought(self.bus,tid,'dialogue')
            self.bus.put(tid+':finished',True)
            self.bus.ack_many(input_events)
            return self.bus.get(tid+':reply')
        finally:
            self.state.thinking(False)

    def _helper_notice(self, event):
        body=event['body'];helper=body['helper']
        row=self.pool.get(helper) if self.pool is not None else None
        contract=body.get('contract') or (row or {}).get('contract',{})
        if event['kind']=='agent.question':
            label='提问'
        else:
            label={'已交回':'交回（自报：'+body.get('outcome','未说明')+'）',
                   '失败':'失败','被拉闸':'被拉闸','已取消':'已取消'}.get(body.get('status'),body.get('status','回报'))
        lines=[f'帮手 {helper}｜{label}']
        for key,title in (('目标','目标'),('理由','理由'),('验收','怎样算做完'),('背景','背景')):
            lines.append(title+'：'+contract.get(key,''))
        qa=body.get('qa') or (row or {}).get('qa',[])
        answered=[item for item in qa if item.get('answer') is not None
                  or item.get('ended_without_answer')]
        if answered:
            lines.append('期间的问答：')
            for item in answered:
                at=datetime.fromisoformat(item['asked_at']).astimezone(TZ)
                lines.append(f'· 它问（{at.month}月{at.day}日 {at:%H:%M}）：{item["question"]}')
                if item.get('answer') is None:
                    lines.append('  （它没等答复就结束了）')
                    continue
                source=item.get('answer_source','她答的')
                lines.append(('  自动答复：' if source=='自动答复' else '  你答：')+item['answer'])
        lines.append(('它现在问：'+body.get('question','')) if event['kind']=='agent.question'
                     else '它的说明：'+body.get('summary',''))
        outputs=body.get('outputs',[])
        lines.append('产出：'+('、'.join(outputs) if outputs else '无'))
        return '\n'.join(lines)

    def run_events(self, events):
        """One serial thought for currently pending helper events of the same kind."""
        self.tools.pool=self.pool
        valid=[]
        for event in events:
            reason = (self.pool.question_stale_reason(event)
                      if event['kind']=='agent.question' and self.pool is not None else None)
            if reason:
                key=event['id']+':stale_question'
                record=self.bus.get(key)
                if record is None:
                    record=self.bus.put(key,{'event':event['id'],
                        'helper':event['body']['helper'],'reason':reason})
                self.bus.record_usage(key,{'type':'stale_question',
                    'tools':{'过时提问':1},'stale_questions':1,**record})
                self.bus.ack(event['id'])
            else:
                valid.append(event)
        events=valid
        if not events:
            return None
        tid=events[0]['id']
        if self.bus.get(tid+':finished'):
            self.bus.ack_many([event['id'] for event in events]);return None
        self.state.thinking(True, '在看帮手 '+events[0]['body']['helper']+
                            (' 的提问' if events[0]['kind']=='agent.question' else ' 的回报'))
        try:
            handoff=DialogueState(self.bus,self.config)
            at=self.io.factory._clock.now()
            live=self.state.snapshot()
            state=handoff.begin(tid,at,live)
            root=Path(__file__).resolve().parents[1]
            system=(root/'prompts/chat_background.md').read_text(encoding='utf-8').strip()+ '\n\n'+ \
                   (root/'prompts/mind_event.md').read_text(encoding='utf-8').strip()
            messages=[]
            if self.config['mind']['nondialogue_recent_turns']:
                with self.io.lock:
                    recent=self.io.runtime._hot_store.read_context().raw_turns
                lines=[]
                for turn in recent[-2*self.config['mind']['nondialogue_recent_turns']:]:
                    who='他' if turn.role=='user' else '你'
                    timestamp=turn.created_at
                    when=(datetime.fromisoformat(timestamp) if isinstance(timestamp,str)
                          else timestamp)
                    when=when.astimezone(TZ) if when else None
                    label=f'（{when.month}月{when.day}日 {when:%H:%M}）' if when else ''
                    lines.append(f'{who}{label}：{turn.text}')
                if lines:messages.append({'role':'user','content':'最近的对话：\n'+'\n'.join(lines)})
            if state['block']:
                messages.append({'role':'user','content':state['block']})
            notices=[self._helper_notice(event) for event in events]
            messages.append({'role':'user','content':'\n\n'.join(notices)})
            refs=dict(state['references']);parsed={'thought':'','carry':[]}
            spoken=[];spoken_texts=set();handled=set()
            user={'source_timezone':self.io.factory.default_timezone,
                            'timezone_source':'configured_default','created_at':at.isoformat()}
            for step in range(1,self.config['mind']['nondialogue_max_steps']+1):
                key=f'{tid}:event:{step}'
                final_step=step==self.config['mind']['nondialogue_max_steps']
                outcome=self._native_call(key,system,messages,
                    thinking=self.config['mind']['nondialogue_thinking'],
                    tool_choice='none' if final_step else 'auto')
                message=chat_assistant_message(outcome['raw']) if not outcome['failed'] else {'content':''}
                calls=chat_tool_calls(message) if not outcome['failed'] else []
                parsed=parse_event(message.get('content') or '')
                if parsed is None and calls:
                    parsed={'speech':'','thought':'','carry':[],'protocol_residue':False}
                if parsed is None or (not outcome['failed'] and
                    outcome['raw']['choices'][0].get('finish_reason')=='length'):
                    retry=self._native_call(key+':retry',system,messages,
                        thinking='disabled',tool_choice='none' if final_step else 'auto')
                    if not retry['failed']:
                        message=chat_assistant_message(retry['raw'])
                        calls=chat_tool_calls(message)
                        parsed=parse_event(message.get('content') or '')
                    else:
                        calls=[];parsed=None
                if parsed is None:
                    parsed={'speech':'','thought':'','carry':[],'protocol_residue':False}
                    calls=[]
                    self.bus.put(key+':parse_failed',True)
                if parsed.get('protocol_residue'):
                    self.bus.put(key+':protocol_residue',True)
                if parsed['speech'] and parsed['speech'] not in spoken_texts:
                    with self.io.lock:
                        recent=[turn.model_dump(mode='json') for turn in self.io.runtime._hot_store.read_context().raw_turns]
                    said=self.emit(f'{tid}:event:{step}:say','mind.say',{
                        'text':parsed['speech'],'user':user,'kind':'model','phase':'model_chat',
                        'protocol':'a2','proactive':True,'memory_block':'',
                        'status_line':state['status_line'],'recent':recent})
                    spoken.append(said)
                    spoken_texts.add(parsed['speech'])
                elif parsed['speech']:
                    self.bus.put(key+':duplicate_speech',parsed['speech'])
                if final_step and calls:
                    self.bus.put(key+':skipped_tool_calls',calls)
                    note='（这次想到一半被打断了）'
                    parsed['thought']=parsed['thought'][:max(0,self.config['mind']['thought_max_chars']-len(note))]+note
                    break
                if not calls:break
                results=[]
                limit=self.config['mind']['tool_max_calls_per_step']
                for ordinal,call in enumerate(calls,1):
                    item=self.tools.execute(f'{tid}:event:{step}:{ordinal}',call,user,
                                            too_many=ordinal>limit)
                    results.append(item);refs[item['id']]=item
                    refs.update(item.get('references',{}))
                    if item['status']=='ok' and call['function']['name'] in ('answer_helper','cancel_helper','hold_question'):
                        handled.add(item['id'])
                messages.extend(self._continue_with_tools(message,calls,results))
            for event in events:
                if event['kind'] != 'agent.question':continue
                helper=event['body']['helper']
                if helper not in handled and (self.pool is None or
                        self.pool.question_stale_reason(event) is None):
                    self.bus.publish(tid+':auto:'+helper,'mind.reply',
                                     {'helper':helper,'content':AUTO_REPLY,'source':'自动答复'})
            handoff.finish(tid,self.io.factory._clock.now(),parsed['thought'],parsed['carry'],refs,
                           reason='处理帮手消息时')
            if spoken:
                self.io.finish(self.bus,tid)
            record_thought(self.bus,tid,'nondialogue')
            self.bus.put(tid+':finished',True)
            self.bus.ack_many([event['id'] for event in events])
        finally:
            self.state.thinking(False)
