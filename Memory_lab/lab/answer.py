"""Optional Answer calls with versioned prompts and explicit time context."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


from Conversation_Memory.answer import (
    parse_answer_v5,parse_answer_v5_tolerant,fallback_answer_v5,
    answer_system as _shared_answer_system,answer_messages as _shared_answer_messages,
)


def answer_system(now,memory_block,prompt_version='v5'):
    persona_path=ROOT.parent/'prompts'/'chat_background.md'
    persona=persona_path.read_text(encoding='utf-8') if persona_path.exists() else ''
    if prompt_version not in ('v2', 'v5'):
        raise ValueError('only B1 answer_v2 and P8/P9 answer_v5 are maintained')
    prompt=(ROOT/'prompts'/'answer_v2.md' if prompt_version=='v2' else
            ROOT.parent/'Conversation_Memory'/'prompts'/'answer_v5.md')
    return _shared_answer_system(now,memory_block,persona,prompt_version=prompt_version,prompt_path=prompt)


def update_summary(llm,old_summary,moved):
    from .summary_prompt import _HOT_DRAFT_SUMMARY_PROMPT
    payload={'existing_summary':old_summary or None,
             'archived_turns':[{'role':t.role,'text':t.text} for t in moved]}
    result=llm.complete(system=_HOT_DRAFT_SUMMARY_PROMPT,
                        messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False,separators=(',',':'))}],
                        max_tokens=1000,purpose='summary')
    return result.text


def answer_messages(probe,hot,summary,summary_until,now,prompt_version='v5'):
    return _shared_answer_messages(probe,hot,summary,summary_until,now,prompt_version=prompt_version)


def generate_answer(llm,probe,hot,summary,summary_until,now,memory_block,prompt_version='v5',purpose='answer',
                    parse_mode='strict'):
    system=answer_system(now,memory_block,prompt_version)
    messages=answer_messages(probe,hot,summary,summary_until,now,prompt_version)
    result=llm.complete(system=system,messages=messages,max_tokens=1000,purpose=purpose)
    attempt=0;noticed='';fallback=False;markers_removed=False;tolerant=False
    if prompt_version=='v5':
        if parse_mode=='tolerant':
            reply,noticed,found,markers_removed,tolerant=parse_answer_v5_tolerant(result.text)
        else:
            reply,noticed,found,markers_removed=parse_answer_v5(result.text)
        if not found:
            attempt=1
            result=llm.complete(system=system,messages=messages,max_tokens=1000,
                                purpose=purpose,attempt=attempt)
            if parse_mode=='tolerant':
                reply,noticed,found,markers_removed,tolerant=parse_answer_v5_tolerant(result.text)
            else:
                reply,noticed,found,markers_removed=parse_answer_v5(result.text)
            fallback=not found
            if fallback:
                reply=fallback_answer_v5(result.text)
        from .llm import LLMResult
        result=LLMResult(reply,result.usage,result.cache_hit,result.cache_key)
    context={'prompt_version':prompt_version,'system_sha256':hashlib.sha256(system.encode('utf-8')).hexdigest(),
             'summary':summary,'summary_until':summary_until.isoformat() if summary_until else None,
             'messages':messages}
    if prompt_version=='v5':context.update({'noticed':noticed,'answer_fallback':fallback,
                                            'markers_removed':markers_removed,'attempt':attempt})
    if prompt_version=='v5':
        context['parse']='fallback' if fallback else 'tolerant' if tolerant else 'retry' if attempt else 'ok'
    return result,context
