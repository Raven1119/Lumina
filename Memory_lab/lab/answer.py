"""Optional real Answer calls for B1 and P6, with no judge by default."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def answer_system(now,memory_block):
    sys.path.insert(0,str(ROOT.parent))
    from memlab.clock import format_now
    persona_path=ROOT.parent/'prompts'/'chat_background.md'
    persona=persona_path.read_text(encoding='utf-8') if persona_path.exists() else ''
    raw=re.sub(r'<!--.*?-->','',(ROOT/'prompts'/'answer_v1.md').read_text(encoding='utf-8'),flags=re.S).strip()
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


def generate_answer(llm,probe,hot,summary,now,memory_block):
    messages=[]
    if summary:messages.append({'role':'user','content':'近期对话摘要：'+summary})
    messages.extend({'role':t.role,'content':t.text} for t in hot)
    messages.append({'role':'user','content':probe['message']})
    return llm.complete(system=answer_system(now,memory_block),messages=messages,
                        max_tokens=1000,purpose='answer')
