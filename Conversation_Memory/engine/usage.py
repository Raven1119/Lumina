"""Local lexical evidence that a generated answer used a surfaced memory."""
from __future__ import annotations

import re

STOP=set('的了是在着过又也都就还不没个这那吗呢吧啊')
CJK=re.compile(r'[\u3400-\u9fff]')
LATIN=re.compile(r'(?<![A-Za-z])[A-Za-z]{3,}(?![A-Za-z])')


def content_grams(text:str)->set[str]:
    chars=[(match.start(),match.group()) for match in CJK.finditer(text)]
    grams={a+b for (i,a),(j,b) in zip(chars,chars[1:]) if j==i+1 and a not in STOP and b not in STOP}
    grams.update(x.group().casefold() for x in LATIN.finditer(text))
    return grams


def used_memories(answer,context_texts,memories,min_overlap=2)->list[str]:
    if min_overlap<1:raise ValueError('min_overlap must be positive')
    context='\n'.join(context_texts)
    novel=content_grams(answer)-content_grams(context)
    used=[]
    for memory in memories:
        overlap=len(novel & content_grams(memory['text']))
        names=memory.get('entity_names',())
        named=any(len(name)>=2 and name in answer and name not in context for name in names)
        if overlap>=min_overlap or named:used.append(memory['id'])
    return used
