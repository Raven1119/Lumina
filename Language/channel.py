"""Durable speech outlet: only core owners append Hot and Memory traces."""
from core.dialogue_io import response
from Language.rephrase import language_request

class LanguageChannel:
    def __init__(self, bus, io, config, model=None):
        self.bus, self.io, self.config, self.model = bus, io, config, model

    def _rephrase(self, key, body):
        prior=self.bus.get(key+':rephrase')
        if prior is not None:return prior
        model=self.model or self.io.model
        system,messages=language_request(body['text'],body['memory_block'],
            body['status_line'],body['recent'],
            recent_turns=self.config['language']['recent_turns'])
        request={'system':system,'messages':messages}
        if hasattr(model,'text_request'):
            request['wire']=model.text_request(system,messages)
        if self.bus.get(key+':language_request') is not None:
            return self.bus.put(key+':rephrase',{'text':body['text'],'status':'outcome_unknown'})
        self.bus.put(key+':language_request',request)
        try:
            if hasattr(model,'complete_text'):
                text=model.complete_text(system,messages)
            else:
                text=model.generate([],messages[-1]['content'],system_prompt=system)
            if not isinstance(text,str) or not text.strip():raise ValueError('empty_language_response')
            final=text.strip()
            result={'text':final,'status':'unchanged' if final==body['text'] else 'rephrased'}
        except Exception:
            result={'text':body['text'],'status':'fallback'}
        return self.bus.put(key+':rephrase',result)

    def handle(self, event):
        key, body = event['id'], event['body']
        previous = self.bus.get(key+':done')
        if previous is not None:
            self.bus.ack(key)
            return previous
        if event['kind']=='mind.trace':
            try:
                self.io.trace(body['turn'],body['read'],body['noticed'],body['text'])
                result = {'trace':'recorded'}
            except Exception:
                result = {'trace':'failed'}
        else:
            policy = self.config['language']['render']
            should_render = body.get('protocol') == 'a2' and (
                policy == 'always' or
                (policy == 'proactive_only' and body.get('proactive') is True) or
                (policy == 'mind_choice' and body.get('rephrase') is True))
            rephrased = (self._rephrase(key,body) if should_render else
                         {'text':body['text'],'status':'verbatim'})
            turn = self.bus.get(key+':turn')
            if turn is None:
                turn = self.bus.put(key+':turn', self.io.new_assistant(rephrased['text'],body['user']))
            self.io.append(turn)
            result = {'turn':turn,'mind_text':body['text'],'final_text':turn['text'],'rephrase_status':rephrased['status'],
                      'response':response(turn['text'],body['kind'],body['phase'])}
            self.bus.publish(key+':said','language.said',result)
        self.bus.put(key+':done',result)
        self.bus.ack(key)
        return result
