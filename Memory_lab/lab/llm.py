"""Cached model calls; an API response is durable before Dream applies it."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from Conversation_Memory.engine.embed import HashEmbedder

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_REAL_MODEL='deepseek-flash'
DEEPSEEK_ANTHROPIC_BASE_URL='https://api.deepseek.com/anthropic'


class CacheMiss(RuntimeError):pass


@dataclass(frozen=True)
class LLMResult:
    text:str
    usage:dict
    cache_hit:bool=False
    cache_key:str=''


class FakeLLM:
    model='fake-v1'
    def __init__(self,embedder=None):
        self.embedder=embedder or HashEmbedder()
        if self.embedder.identity['model'].startswith('hash'):
            self.model='fake-v1-hash2'
    def complete(self,*,system,messages,max_tokens,purpose,attempt=0):
        if purpose=='summary':
            data=json.loads(messages[-1]['content'])
            previous=data.get('existing_summary') or ''
            texts=[x['text'] for x in data.get('archived_turns',[]) if x.get('role')=='user']
            return LLMResult((previous+'\n'+'；'.join(texts))[-1000:],{'input_tokens':0,'output_tokens':0})
        if purpose in ('answer','shadow','answer_noise'):
            response='想起：无\n回复：（离线模拟回答）' if '想起：' in system else '（离线模拟回答）'
            return LLMResult(response,{'input_tokens':0,'output_tokens':0})
        if purpose=='pattern':
            groups=re.findall(r'(?m)^组 (\d+)（线索：',messages[-1]['content'])
            return LLMResult(json.dumps({'patterns':[{'group':int(number),'none':True} for number in groups]}),
                             {'input_tokens':0,'output_tokens':0})
        if purpose=='usage':
            turns=json.loads(messages[-1]['content'])['turns']
            return LLMResult(json.dumps({'used':[{'turn':t['id'],'ids':[]} for t in turns]},ensure_ascii=False),
                             {'input_tokens':0,'output_tokens':0})
        if purpose=='repair':return LLMResult('[]',{'input_tokens':0,'output_tokens':0})
        user=messages[-1]['content']
        window=user.split('【这段对话】',1)[-1]
        old=user.split('【被唤起的旧记忆】',1)[-1].split('【已知实体】',1)[0]
        ops=[]
        for line in window.splitlines():
            match=re.match(r'^\[([^]]+)\].*? 他：(.*)$',line)
            if match and len(match[2])>=8:
                ops.append({'op':'new','text':'他说：'+match[2][:60],'salience':1,
                            'sources':[match[1]],'entities':[]})
        if old.strip()!='（无）':
            memories=[]
            for line in old.splitlines():
                parts=line.split('｜')
                if len(parts)==4 and parts[0].startswith('m'):
                    memories.append((parts[0],parts[3]))
            if memories:
                texts=[x[1] for x in memories]
                window_text='\n'.join(re.findall(r'他：(.*)',window))
                if window_text:
                    q=self.embedder.encode([window_text])[0]
                    vectors=self.embedder.encode(texts)
                    source=re.search(r'\[([^]]+)\]',window)
                    for (mid,_),cos in zip(memories,vectors@q):
                        if cos>=.8 and source:
                            ops.append({'op':'touch','id':mid,'sources':[source[1]]})
        return LLMResult(json.dumps({'ops':ops},ensure_ascii=False,sort_keys=True),
                         {'input_tokens':0,'output_tokens':0})


class RealLLM:
    model=DEFAULT_REAL_MODEL
    def __init__(self,model:str|None=None):
        sys.path.insert(0,str(ROOT.parent))
        from core.env_loader import load_env_file
        self.base=DEEPSEEK_ANTHROPIC_BASE_URL
        key=os.environ.get('DEEPSEEK_API_KEY')
        if not key:
            load_env_file(ROOT.parent/'.env.local')
            key=os.environ.get('DEEPSEEK_API_KEY')
        if not key:raise RuntimeError('DeepSeek key unavailable')
        self.model=model or os.environ.get('LUMINA_MEMLAB_MODEL') or DEFAULT_REAL_MODEL
        self.key=key
        self.temperature_omitted=False

    def complete(self,*,system,messages,max_tokens,purpose,attempt=0):
        import httpx  # optional, never imported by offline tests
        body={'model':self.model,'max_tokens':max_tokens,'temperature':0,
              'thinking':{'type':'disabled'},'system':system,'messages':messages}
        headers={'X-Api-Key':self.key,'anthropic-version':'2023-06-01','content-type':'application/json'}
        with httpx.Client(timeout=180) as client:
            for retry in range(3):
                try:
                    response=client.post(self.base+'/v1/messages',headers=headers,json=body)
                    if response.status_code==400 and 'temperature' in body:
                        body.pop('temperature');self.temperature_omitted=True
                        continue
                    response.raise_for_status()
                    data=response.json()
                    content=''.join(part.get('text','') for part in data.get('content',[]) if part.get('type')=='text')
                    usage=data.get('usage') or {}
                    return LLMResult(content,{'input_tokens':int(usage.get('input_tokens',0)),
                                              'output_tokens':int(usage.get('output_tokens',0))})
                except Exception as exc:
                    if retry==2:
                        detail=':'+str(exc.response.status_code) if isinstance(exc,httpx.HTTPStatusError) else ''
                        raise RuntimeError('model_call_failed:'+type(exc).__name__+detail) from None
                    import time
                    time.sleep(2**retry)
        raise RuntimeError('model_call_failed')


class CachedLLM:
    def __init__(self,base,root:Path,cache_only:bool=False,temperature:float=0,
                 allow_new:set[str]|None=None):
        self.base=base;self.model=base.model;self.root=root;self.root.mkdir(parents=True,exist_ok=True)
        self.cache_only=cache_only;self.temperature=temperature;self.allow_new=allow_new
        self.new_calls=0;self.hits=0;self.new_input_tokens=0;self.new_output_tokens=0
        self.new_calls_by_purpose={};self.cache_hits_by_purpose={}

    def complete(self,*,system,messages,max_tokens,purpose,attempt=0):
        payload={'model':self.model,'max_tokens':max_tokens,'temperature':self.temperature,
                 'prompt_version':purpose+'_v1','system':system,'messages':messages,'attempt':attempt}
        key=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path=self.root/f'{key}.json'
        with (self.root/f'{key}.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if path.is_file():
                data=json.loads(path.read_text(encoding='utf-8'))
                self.hits+=1
                self.cache_hits_by_purpose[purpose]=self.cache_hits_by_purpose.get(purpose,0)+1
                return LLMResult(data['text'],data['usage'],True,key)
            if self.cache_only or (self.allow_new is not None and purpose not in self.allow_new):
                raise CacheMiss(key)
            result=self.base.complete(system=system,messages=messages,max_tokens=max_tokens,purpose=purpose,attempt=attempt)
            data={'text':result.text,'usage':result.usage,'model':self.model,'purpose':purpose,'attempt':attempt}
            tmp=path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data,ensure_ascii=False,sort_keys=True),encoding='utf-8')
            os.replace(tmp,path)
            self.new_calls+=1
            self.new_calls_by_purpose[purpose]=self.new_calls_by_purpose.get(purpose,0)+1
            self.new_input_tokens+=int(result.usage.get('input_tokens',0))
            self.new_output_tokens+=int(result.usage.get('output_tokens',0))
            return LLMResult(result.text,result.usage,False,key)
