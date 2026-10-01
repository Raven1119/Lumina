"""Shared answer_v5 parsing and request construction for Chat and lab."""
from __future__ import annotations
import json
import re
from datetime import datetime
from pathlib import Path
from .engine.clock import TZ,WEEKDAYS,format_now

PROMPT_DIR=Path(__file__).resolve().parent/"prompts"

def _clean_v5_reply(value):
    lines=[];removed=False
    for line in value.splitlines():
        if re.match(r'^\s*["“]?(?:理解|借鉴|顺带)["”]?\s*[：:]',line):
            removed=True;continue
        line=re.sub(r'["“]?(?:理解|借鉴|顺带)["”]?\s*[：:][^\n]*','',line)
        line=line.replace('回复：','').replace('回复:','')
        lines.append(line)
    answer='\n'.join(lines).strip().strip('"”}\n ')
    return answer,removed or answer!=value.strip()


def parse_answer_v5(text):
    """Reply, three private columns, success and cleanup flag."""
    data=None
    for source in (text, text[text.find('{'):text.rfind('}')+1] if '{' in text and '}' in text else ''):
        try:
            item=json.loads(source,strict=False)
            if isinstance(item,dict):data=item;break
        except (ValueError,TypeError):pass
    noticed={key:'' for key in ('理解','借鉴','顺带')}
    if data is not None:
        noticed={key: str(data.get(key,'') or '') for key in noticed}
        reply=data.get('回复')
        if isinstance(reply,str) and reply.strip():
            answer,cleaned=_clean_v5_reply(reply)
            return answer,noticed,bool(answer),cleaned
    return '',noticed,False,False


def parse_answer_v5_tolerant(text):
    """Recover labeled fields from malformed JSON without treating private fields as a reply."""
    reply,noticed,found,cleaned=parse_answer_v5(text)
    if found:
        return reply,noticed,True,cleaned,False
    keys=list(re.finditer(r'["“]?(理解|借鉴|顺带|回复)["”]?\s*[：:]\s*',text))
    if not keys:
        return '',noticed,False,False,False
    fields={}
    for index,match in enumerate(keys):
        end=keys[index+1].start() if index+1<len(keys) else len(text)
        value=text[match.end():end].strip().strip(" \t\r\n\"“”,}")
        fields[match.group(1)]=value
    candidate=fields.get('回复','')
    if not candidate:
        return '',noticed,False,False,False
    answer,cleaned=_clean_v5_reply(candidate)
    return answer,{key:fields.get(key,'') for key in noticed},bool(answer),cleaned,True


def fallback_answer_v5(text):
    marker=list(re.finditer(r'["“]?回复["”]?\s*[：:]',text))
    if marker:
        tail=text[marker[-1].end():].lstrip()
        if tail.startswith('"'):
            try: tail=json.JSONDecoder(strict=False).raw_decode(tail)[0]
            except ValueError: tail=tail[1:].split('"}',1)[0].rstrip('"')
        reply,_=_clean_v5_reply(str(tail))
        return reply
    # With no reply field, only text outside all private labeled fields is safe.
    lines=[line for line in text.splitlines()
           if not re.search(r'["“]?(?:理解|借鉴|顺带)["”]?\s*[：:]',line)]
    return _clean_v5_reply('\n'.join(lines))[0]



def answer_system(now: datetime, memory_block: str, persona: str, *, prompt_version: str='v5', prompt_path: Path|None=None, protocol: str='a1') -> str:
    path=prompt_path or PROMPT_DIR/f'answer_{prompt_version}.md'
    raw=re.sub(r'<!--.*?-->','',path.read_text(encoding='utf-8'),flags=re.S).strip()
    if protocol not in ('a1','a2'):
        raise ValueError('invalid_dialogue_protocol')
    if protocol == 'a2':
        memory_block = ''
    result = raw.replace('{{persona}}',persona).replace('{{now}}',format_now(now)).replace('{{memory_block}}',memory_block)
    if protocol == 'a2':
        result = result.strip()+'\n\n'+(PROMPT_DIR.parents[1]/'prompts/dialogue_a2_persona.md').read_text(encoding='utf-8').strip()
    return result

def answer_messages(probe: dict, hot, summary: str|None, summary_until: datetime|None, now: datetime, *, prompt_version: str='v5', protocol: str='a1', state_block: str='', memory_block: str='') -> list[dict[str,str]]:
    messages=[]
    if prompt_version=='v1':
        if summary:messages.append({'role':'user','content':'近期对话摘要：'+summary})
        messages.extend({'role':t.role,'content':t.text} for t in hot)
        messages.append({'role':'user','content':probe['message']})
        return messages
    if summary:
        if summary_until is None:
            messages.append({'role':'user','content':f'近期对话摘要：{summary}'})
        else:
            at=summary_until.astimezone(TZ)
            messages.append({'role':'user','content':f'近期对话摘要（截至 {at.month}月{at.day}日）：{summary}'})
    for t in hot:
        content=t.text
        if t.role=='user':
            at=t.time.astimezone(TZ)
            content=f'（{at.month}月{at.day}日 周{WEEKDAYS[at.weekday()]} {at:%H:%M}）{content}'
        messages.append({'role':t.role,'content':content})
    at=now.astimezone(TZ)
    gap=''
    if hot:
        seconds=max(0,(now-hot[-1].time).total_seconds())
        if seconds>=48*3600:gap=f'，距上一句{int(seconds//86400)}天'
        elif seconds>=3600:gap=f'，距上一句{int(seconds//3600)}小时'
    messages.append({'role':'user','content':f'（{at.month}月{at.day}日 周{WEEKDAYS[at.weekday()]} {at:%H:%M}{gap}）{probe["message"]}'})
    if protocol == 'a2':
        if state_block:
            messages.insert(len(messages)-1,{'role':'user','content':state_block})
        if memory_block:
            messages[-1]['content'] = memory_block+'\n\n'+messages[-1]['content']
    return messages


def parse_dialogue(text, *, allow_unlabeled=True):
    """Tolerant v5 reply plus private A2 handoff fields, without text actions.

    Malformed new fields never become public prose. Only a labeled reply (or
    unlabeled natural language) may be sent out of the dialogue protocol.
    """
    data = None
    decoder = json.JSONDecoder(strict=False)
    for offset, character in enumerate(text):
        if character != '{':
            continue
        try:
            candidate, _ = decoder.raw_decode(text, offset)
            if isinstance(candidate,dict) and any(k in candidate for k in ('理解','行动','回复')):
                data = candidate
                break
        except (ValueError,TypeError):
            pass
    keys = ('理解','借鉴','顺带','回复','重组','行动','思绪','带着')
    if data is None:
        matches = list(re.finditer(r'["“]?('+'|'.join(keys)+r')["”]?\s*[：:]\s*',text))
        data = {}
        for index,match in enumerate(matches):
            end=matches[index+1].start() if index+1<len(matches) else len(text)
            value=text[match.end():end].strip().rstrip(',} \n')
            # New fields require valid JSON values. Private strings may be
            # recovered by the original forgiving labeled-field convention.
            try:
                parsed=json.JSONDecoder(strict=False).raw_decode(value)[0]
            except ValueError:
                parsed=value.strip('"“”') if match[1] in keys[:4] else None
            data[match[1]]=parsed
        if not matches and allow_unlabeled:
            data['回复']=fallback_answer_v5(text)
    noticed={key:data.get(key,'') if isinstance(data.get(key,''),str) else '' for key in keys[:3]}
    reply=data.get('回复','')
    reply=_clean_v5_reply(reply)[0] if isinstance(reply,str) else ''
    return {'reply':reply,'noticed':noticed,'rephrase':data.get('重组') is True,
            'protocol_residue':'行动' in data,
            'thought':data.get('思绪','') if isinstance(data.get('思绪',''),str) else '',
            'carry':[ref for ref in data.get('带着',[]) if isinstance(ref,str)] if isinstance(data.get('带着'),list) else []}


def number_dialogue_memories(read):
    """Add visible A2 references at the answer seam; leave P8's block untouched."""
    block=read.block
    references={}
    for memory in (*read.result.near,*read.result.remote,*read.result.core):
        ref='记忆:'+memory.id
        if ref in references:
            continue
        if memory.text in block:
            block=block.replace(memory.text,'['+ref+'] '+memory.text,1)
            references[ref]={'id':ref,'text':memory.text}
    for index,hit in enumerate(read.result.raw,1):
        ref='原话:'+str(index)
        if hit.text in block:
            block=block.replace(hit.text,'['+ref+'] '+hit.text,1)
            references[ref]={'id':ref,'text':hit.text}
    return block,references
