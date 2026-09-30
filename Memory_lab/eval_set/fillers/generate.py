#!/usr/bin/env python3
"""Generate cached, banned-word-checked filler sessions for the long sets."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from lab.llm import CachedLLM,RealLLM  # noqa: E402
from eval_set.build import assign_ids_and_times,parse_script  # noqa: E402

TOPICS=('一部新剧','做一道新菜','天气变化','一个新游戏','办理日常事务','朋友来访','整理书架','去逛菜市场')
SCHEDULE=((0,'09:00'),(2,'18:30'),(4,'12:00'),(6,'20:00'))


def _unfence(text):
    value=text.strip()
    m=re.fullmatch(r'```(?:text)?\s*\n(.*?)\n```',value,re.S|re.I)
    return m.group(1).strip() if m else value


def _validate(text,sid,day,hhmm,banned):
    text=_unfence(text)
    hits=[word for word in banned if word.casefold() in text.casefold()]
    if hits:raise ValueError('forbidden words: '+','.join(hits))
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0]!=f'## {sid} {day} {hhmm}':raise ValueError('session header')
    turns=len(lines)-1
    if not 10<=turns<=24 or turns%2:raise ValueError(f'expected 10-24 even turns, got {turns}')
    for i,line in enumerate(lines[1:]):
        if not line.startswith('U: ' if i%2==0 else 'L: '):raise ValueError(f'role at {i}')
        if '{#' in line:raise ValueError('unexpected gold tag')
    return '\n'.join(lines)


def generate(name,out=None):
    if name not in ('dev_a','dev_b'):raise ValueError('only dev sets')
    gold=json.loads((ROOT/'eval_set'/'built'/f'{name}.gold.json').read_text(encoding='utf-8'))
    dialogue=json.loads((ROOT/'eval_set'/'built'/f'{name}.dialogue.json').read_text(encoding='utf-8'))
    earliest=min(datetime.fromisoformat(p['time']) for p in gold['probes'])
    before=[s for s in dialogue['sessions'] if datetime.fromisoformat(s['turns'][-1]['time'])<earliest]
    first_day=(datetime.fromisoformat(before[-1]['turns'][-1]['time'])+timedelta(days=1)).date()
    banned=json.loads((Path(__file__).parent/'forbidden_words.json').read_text(encoding='utf-8'))[name]
    system=(Path(__file__).parent/'filler_prompt_v1.md').read_text(encoding='utf-8')
    llm=CachedLLM(RealLLM(),ROOT/'cache'/'llm',allow_new={'filler'})
    target=out or Path(__file__).parent/f'{name}_8w.txt'
    print(f'{name}: 预计新 filler 调用 32 次（无重试）',flush=True)
    sessions=[]
    for week in range(8):
        for slot,(offset,hhmm) in enumerate(SCHEDULE):
            sid=f'f{week*4+slot+1:02d}'
            day=(first_day+timedelta(days=week*7+offset)).isoformat()
            user=json.dumps({'set':name,'session_id':sid,'date':day,'start_time':hhmm,
                             'topic':TOPICS[(week*4+slot)%len(TOPICS)],
                             'forbidden_words':banned},ensure_ascii=False)
            error=''
            for attempt in range(5):
                response=llm.complete(system=system,messages=[{'role':'user','content':user}],
                                      max_tokens=3000,purpose='filler',attempt=attempt)
                try:segment=_validate(response.text,sid,day,hhmm,banned)
                except ValueError as exc:error=str(exc)
                else:break
            else:raise ValueError(f'{sid}: generation failed: {error}')
            sessions.append(segment)
            print(f'{sid}: valid',flush=True)
    text=f'@id: {name}_long8w\n@split: dev\n\n'+'\n\n'.join(sessions)+'\n'
    target.write_text(text,encoding='utf-8')
    _,parsed=parse_script(target)
    errors=assign_ids_and_times(f'{name}_long8w',parsed)
    if errors:raise ValueError(errors)
    print(json.dumps({'sessions':len(parsed),'new_calls':llm.new_calls,
                      'input_tokens':llm.new_input_tokens,'output_tokens':llm.new_output_tokens,
                      'cache_hits':llm.hits},ensure_ascii=False),flush=True)
    return target


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('names',nargs='*',default=['dev_a','dev_b'])
    for name in ap.parse_args().names:generate(name)
