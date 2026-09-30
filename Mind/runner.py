"""The Chat dialogue thought. Independent of the old Mind cognitive chain."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PureWindowsPath
import hashlib

from core.dialogue_io import response
from Mind.dialogue_state import DialogueState
from Mind.helper_actions import object_from_text, actions_from_object, parse_event
from Execution.pool import AUTO_REPLY
from core.model_client import MOCK_ASSISTANT_TEXT
from Conversation_Memory.answer import parse_answer_v5_tolerant, fallback_answer_v5, parse_dialogue, answer_messages


class DialogueRunner:
    def __init__(self, bus, io, config, state, emit, checkpoint=None, pool=None):
        self.bus, self.io, self.config, self.state, self.emit = bus, io, config, state, emit
        self.checkpoint = checkpoint or (lambda _: None)
        self.pool = pool

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

    def _action(self, tid, step, ordinal, action, user):
        key=f'{tid}:{step}:{ordinal}'
        result=self.bus.get(key+':result')
        if result is not None:return result
        self.bus.put(key+':action',action)
        name,value=next(iter(action.items()))
        if name not in ('读','回忆'):
            return self._helper_action(key, name, value)
        if name=='读':
            ref='文件:'+value
            try:
                relative=Path(value)
                root=Path(self.config['workspace']['path']).resolve()
                target=(root/relative).resolve()
                if relative.is_absolute() or PureWindowsPath(value).drive or '..' in relative.parts or not target.is_relative_to(root):
                    raise ValueError('outside_workspace')
                if not target.is_file():
                    raise ValueError('not_regular_file')
                limit=self.config['mind']['read_max_chars']
                with target.open('r',encoding='utf-8') as source:
                    content=source.read(limit+1)
                truncated=len(content)>limit
                text=content[:limit]+('\n（已截断，后续内容未读取）' if truncated else '')
                result={'id':ref,'text':text,'truncated':truncated,'status':'ok'}
            except (OSError,ValueError,UnicodeError):
                result={'id':ref,'text':'读取失败：仅可读取工作区内可用的文本文件。','status':'unavailable'}
        else:
            ref=f'回忆:{step}.{ordinal}'
            try:
                recalled=self.io.recall(value,user)
                # Active recall is deliberately absent from Memory's trace.
                lines=[line for line in recalled.block.splitlines() if line.strip()]
                children={f'{ref}.{i}':{'id':f'{ref}.{i}','text':line} for i,line in enumerate(lines,1)}
                text='\n'.join(f"[{item['id']}] {item['text']}" for item in children.values()) or '（没有召回结果）'
                result={'id':ref,'text':text,'status':'ok','references':children}
            except Exception:
                result={'id':ref,'text':'（回忆暂不可用）','status':'unavailable'}
        return self.bus.put(key+':result',result)

    def _helper_action(self, key, name, value):
        if name == '派活':
            helper = 'H' + hashlib.sha256(key.encode()).hexdigest()[:8]
            kind, body = 'mind.spawn', {'helper': helper, 'contract': value}
            result = {'id': helper, 'text': '已派出帮手 '+helper, 'status': 'ok'}
        else:
            helper = value['帮手'] if name == '答复' else value
            if self.pool is not None and self.pool.get(helper) is None:
                return self.bus.put(key+':result', {'id': helper, 'text': '帮手不存在', 'status': 'unavailable'})
            kind = {'答复':'mind.reply','取消':'mind.cancel','搁置':'mind.hold'}[name]
            body = {'helper': helper}
            if name == '答复': body['content'] = value['内容']
            result = {'id': helper, 'text': {'答复':'已答复','取消':'已取消','搁置':'已搁置'}[name], 'status':'ok'}
        self.bus.publish(key+':event', kind, body)
        return self.bus.put(key+':result', result)

    def _run_a2(self,event):
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
            input_events=[tid];unanswered=[tid];speeches=[]
            refs={**state['references'],**prepared['memory_references']}
            history=[];recent=list(prepared['recent'])+[user]
            max_steps=self.config['mind']['dialogue_max_steps']
            phase='mock_chat' if getattr(self.io.model,'client_kind','model')=='mock' else 'model_chat'
            parsed={'thought':'','carry':[]}
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
                request=deepcopy(prepared)
                request['messages']+=history
                result=self._call(key,request)
                if result['failed']:
                    parsed={'reply':MOCK_ASSISTANT_TEXT,'noticed':{},'rephrase':False,'actions':[],'thought':'','carry':[]}
                    kind='fallback'
                elif phase=='mock_chat':
                    parsed={'reply':result['raw'],'noticed':{},'rephrase':False,'actions':[],'thought':'','carry':[]}
                    kind='mock'
                else:
                    parsed=parse_dialogue(result['raw']);kind='model'
                    obj=object_from_text(result['raw'])
                    if obj is not None:
                        parsed['actions']=actions_from_object(obj)
                if parsed['reply']:
                    said=self.emit(f'{tid}:{step}:0','mind.say',{'text':parsed['reply'],'user':user,
                        'kind':kind,'phase':phase,'rephrase':parsed['rephrase'],
                        'memory_block':prepared['memory_block'],'status_line':state['status_line'],
                        'recent':recent,'protocol':'a2'})
                    speeches.append({**said,'noticed':parsed['noticed']})
                    recent.append(said['turn'])
                    self.checkpoint('speech_saved')
                    for input_id in unanswered:
                        if self.bus.get(input_id+':reply') is None:
                            self.bus.put(input_id+':reply',said['response'])
                    unanswered=[]
                if not parsed['actions']:break
                returns=[action for action in parsed['actions'] if next(iter(action)) in ('读','回忆')]
                if step==max_steps and returns:
                    interrupted=True
                    self.bus.put(key+':skipped_actions',returns)
                results=[]
                for ordinal,action in enumerate(parsed['actions'],1):
                    if step==max_steps and action in returns:continue
                    value=self._action(tid,step,ordinal,action,user)
                    results.append(value);refs[value['id']]=value
                    refs.update(value.get('references',{}))
                if not returns or step==max_steps:break
                history.append({'role':'assistant','content':result['raw']})
                history.append({'role':'user','content':'本次思考的行动结果：\n'+
                    '\n\n'.join('['+r['id']+'] '+r['text'] for r in results)})
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
                    self.bus.put(input_id+':reply',response(phase=phase,compaction=compact))
            if speeches:
                self.bus.publish(tid+':spoke','mind.spoke',{'response':speeches[0]['response']})
            self.bus.put(tid+':inputs',input_events)
            self.bus.put(tid+':finished',True)
            self.bus.ack_many(input_events)
            return self.bus.get(tid+':reply')
        finally:
            self.state.thinking(False)

    def _event_call(self, key, system, messages, *, retry=False):
        saved = self.bus.get(key+':response')
        if saved is not None:return saved
        if self.bus.get(key+':request') is not None:
            return self.bus.put(key+':response', {'raw':'','failed':True,'reason':'outcome_unknown'})
        model=self.io.model
        thinking='disabled' if retry else self.config['mind']['nondialogue_thinking']
        wire=(model.event_request(system,messages,thinking=thinking,prefill=retry)
              if hasattr(model,'event_request') else {'system':system,'messages':messages,'thinking':thinking,'prefill':retry})
        self.bus.put(key+':request',wire)
        try:
            if hasattr(model,'complete_event'):
                raw=model.complete_event(system,messages,thinking=thinking,prefill=retry)
            elif hasattr(model,'complete_answer'):
                raw=model.complete_answer(system,messages)
            else:
                raw=model.generate([],messages[-1]['content'],system_prompt=system)
            saved={'raw':raw if isinstance(raw,str) else '', 'failed':not isinstance(raw,str)}
        except Exception:
            saved={'raw':'','failed':True,'reason':'model_call_failed'}
        return self.bus.put(key+':response',saved)

    def run_events(self, events):
        """One serial thought for currently pending helper events of the same kind."""
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
                messages.extend({'role':turn.role,'content':turn.text} for turn in recent[-self.config['mind']['nondialogue_recent_turns']:])
            if state['block']:
                messages.append({'role':'user','content':state['block']})
            notices=[]
            for event in events:
                body=event['body'];contract=body.get('contract',{})
                notices.append('帮手 '+body['helper']+'｜'+event['kind']+'\n'+
                    '\n'.join(k+'：'+v for k,v in contract.items())+'\n消息：'+
                    (body.get('question') or body.get('summary') or '')+'\n产出：'+
                    '、'.join(body.get('outputs',[])))
            messages.append({'role':'user','content':'\n\n'.join(notices)})
            refs=dict(state['references']);parsed={'thought':'','carry':[]}
            spoken=[];user={'source_timezone':self.io.factory.default_timezone,
                            'timezone_source':'configured_default','created_at':at.isoformat()}
            for step in range(1,self.config['mind']['nondialogue_max_steps']+1):
                key=f'{tid}:event:{step}'
                outcome=self._event_call(key,system,messages)
                parsed=parse_event(outcome['raw']) if not outcome['failed'] else None
                if parsed is None:
                    outcome=self._event_call(key+':retry',system,messages,retry=True)
                    parsed=parse_event(outcome['raw']) if not outcome['failed'] else None
                if parsed is None:
                    parsed={'actions':[],'speech':'','thought':'','carry':[]}
                if parsed['speech']:
                    with self.io.lock:
                        recent=[turn.model_dump(mode='json') for turn in self.io.runtime._hot_store.read_context().raw_turns]
                    said=self.emit(f'{tid}:event:{step}:say','mind.say',{
                        'text':parsed['speech'],'user':user,'kind':'model','phase':'model_chat',
                        'protocol':'a2','proactive':True,'memory_block':'',
                        'status_line':state['status_line'],'recent':recent})
                    spoken.append(said)
                returned=[]
                for ordinal,action in enumerate(parsed['actions'],1):
                    if step==self.config['mind']['nondialogue_max_steps'] and next(iter(action)) in ('读','回忆'):
                        continue
                    item=self._action(tid,step,ordinal,action,user)
                    refs[item['id']]=item
                    if next(iter(action)) in ('读','回忆'):returned.append(item)
                if not returned:break
                messages.append({'role':'assistant','content':outcome['raw']})
                messages.append({'role':'user','content':'本次思考的行动结果：\n'+
                    '\n'.join('['+item['id']+'] '+item['text'] for item in returned)})
            for event in events:
                if event['kind'] != 'agent.question':continue
                helper=event['body']['helper']
                if not any(e['kind'] in ('mind.reply','mind.cancel','mind.hold') and e['body'].get('helper')==helper
                           for e in self.bus.pending('execution')):
                    self.bus.publish(tid+':auto:'+helper,'mind.reply',
                                     {'helper':helper,'content':AUTO_REPLY})
            handoff.finish(tid,self.io.factory._clock.now(),parsed['thought'],parsed['carry'],refs,
                           reason='处理帮手消息时')
            if spoken:
                self.io.finish(self.bus,tid)
            self.bus.put(tid+':finished',True)
            self.bus.ack_many([event['id'] for event in events])
        finally:
            self.state.thinking(False)
