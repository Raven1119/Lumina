"""Optional Answer calls with versioned prompts and explicit time context."""
from __future__ import annotations

import json
import hashlib
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def answer_system(now,memory_block,prompt_version='v1'):
    sys.path.insert(0,str(ROOT.parent))
    from memlab.clock import format_now
    persona_path=ROOT.parent/'prompts'/'chat_background.md'
    persona=persona_path.read_text(encoding='utf-8') if persona_path.exists() else ''
    raw=re.sub(r'<!--.*?-->','',(ROOT/'prompts'/f'answer_{prompt_version}.md').read_text(encoding='utf-8'),flags=re.S).strip()
    return raw.replace('{{persona}}',persona).replace('{{now}}',format_now(now)).replace('{{memory_block}}',memory_block)


def update_summary(llm,old_summary,moved):
    sys.path.insert(0,str(ROOT.parent))
    from core.model_client import _HOT_DRAFT_SUMMARY_PROMPT
    payload={'existing_summary':old_summary or None,
             'archived_turns':[{'role':t.role,'text':t.text} for t in moved]}
    result=llm.complete(system=_HOT_DRAFT_SUMMARY_PROMPT,
                        messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False,separators=(',',':'))}],
                        max_tokens=1000,purpose='summary')
    return result.text


def answer_messages(probe,hot,summary,summary_until,now,prompt_version='v1'):
    from memlab.clock import TZ,WEEKDAYS
    messages=[]
    if prompt_version=='v1':
        if summary:messages.append({'role':'user','content':'近期对话摘要：'+summary})
        messages.extend({'role':t.role,'content':t.text} for t in hot)
        messages.append({'role':'user','content':probe['message']})
        return messages
    if summary:
        if summary_until is None:raise ValueError('summary timestamp missing')
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
    return messages


def generate_answer(llm,probe,hot,summary,summary_until,now,memory_block,prompt_version='v1'):
    system=answer_system(now,memory_block,prompt_version)
    messages=answer_messages(probe,hot,summary,summary_until,now,prompt_version)
    result=llm.complete(system=system,messages=messages,max_tokens=1000,purpose='answer')
    context={'prompt_version':prompt_version,'system_sha256':hashlib.sha256(system.encode('utf-8')).hexdigest(),
             'summary':summary,'summary_until':summary_until.isoformat() if summary_until else None,
             'messages':messages}
    return result,context
