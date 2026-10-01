"""Count durable thought receipts without copying conversation content into statistics."""
from __future__ import annotations

import re


_REQUEST = re.compile(r'(?:step|event):\d+(?::retry)?:request$')
_RESPONSE = re.compile(r'(?:step|event):\d+(?::retry)?:response$')
_STEP = re.compile(r'(?:step|event):(\d+)(?::retry)?:request$')
_TOOL_ACTION = re.compile(r'(?:step:)?\d+:\d+:action$|event:\d+:\d+:action$')
_TOOL_RESULT = re.compile(r'\d+:\d+:result$|event:\d+:\d+:result$')


def record_thought(bus, thought_id, kind):
    """One idempotent aggregate from already committed request/action receipts."""
    journal=bus.journal_prefix(thought_id+':')
    role='dialogue_mind' if kind=='dialogue' else 'nondialogue_mind'
    calls={role:0,'language':0}
    tokens={role:{'input':0,'output':0},'language':{'input':0,'output':0}}
    steps=set();tools={};errors={};error_texts=[]
    parse_failures=retries=residue=speech=duplicate=ignored=model_failures=0
    for suffix,value in journal.items():
        if _REQUEST.fullmatch(suffix):
            calls[role]+=1
            match=_STEP.fullmatch(suffix)
            if match:steps.add(int(match.group(1)))
            if ':retry:' in suffix:retries+=1
        elif _RESPONSE.fullmatch(suffix):
            if value.get('failed'):model_failures+=1
            raw=value.get('raw')
            usage=raw.get('usage',{}) if isinstance(raw,dict) else {}
            tokens[role]['input']+=int(usage.get('prompt_tokens') or 0)
            tokens[role]['output']+=int(usage.get('completion_tokens') or 0)
        elif _TOOL_ACTION.fullmatch(suffix):
            name=value.get('function',{}).get('name','unknown')
            tools[name]=tools.get(name,0)+1
        elif _TOOL_RESULT.fullmatch(suffix):
            if isinstance(value,dict) and value.get('error_kind'):
                name=value['error_kind'];errors[name]=errors.get(name,0)+1
                error_texts.append(value['text'])
        elif suffix.endswith(':parse_failed'):
            parse_failures+=1
        elif suffix.endswith(':protocol_residue'):
            residue+=1
        elif suffix.endswith(':duplicate_speech'):
            duplicate+=1
        elif suffix.endswith(':skipped_tool_calls'):
            ignored+=len(value)
        elif suffix.endswith(':language_request'):
            calls['language']+=1
        elif suffix.endswith(':rephrase'):
            used=value.get('usage',{}) if isinstance(value,dict) else {}
            tokens['language']['input']+=int(used.get('input_tokens') or 0)
            tokens['language']['output']+=int(used.get('output_tokens') or 0)
        elif (re.fullmatch(r'\d+:0:done',suffix) or
              re.fullmatch(r'event:\d+:say:done',suffix)):
            speech+=1
    record={'type':kind,'steps':len(steps),'tools':tools,'tool_errors':errors,
            'tool_error_texts':error_texts,'parse_failures':parse_failures,
            'retries':retries,'protocol_residue':residue,'speech_count':speech,
            'duplicate_speech':duplicate,'no_speech':int(speech==0),
            'ignored_last_tool_calls':ignored,'model_failures':model_failures,
            'calls':calls,'tokens':tokens}
    bus.record_usage('thought:'+thought_id,record)
    return record
