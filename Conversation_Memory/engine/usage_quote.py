"""P8 quote-grounded judgment of integrated memory use."""
from __future__ import annotations
import unicodedata
from .integrate import _json_first

COMMON = frozenset('一下 时候 还是 晚上 今天 明天 昨天 现在 这个 那个 什么 怎么 就是 没有 可以 不是 已经 一点 有点 早点 我们 你们 他们 自己 知道 觉得 然后 因为 所以 如果 这样 一样 还有 真的 应该 不要 下次 上次 一个 这次 最近 早上 下午 中午 回来 出去 一起 开始 比较 其实'.split())


def normalize_quote(value):
    return ''.join(c for c in value if not(c.isspace() or unicodedata.category(c)[0] in 'PS'))


def _match(quote, memory, context, short_matches=None):
    """Longest novel shared span, then the first stable tie by quote position."""
    for size in range(min(len(quote),len(memory)),1,-1):
        for i in range(len(quote)-size+1):
            part=quote[i:i+size]
            valid_short=(part not in COMMON if short_matches is None else part in short_matches)
            if part in memory and (size>=3 or valid_short) and part not in context:
                return part
    return None


def parse_integrated_used_v4(text: str, turns: list[dict], short_match='stoplist'):
    """Return accepted IDs, reasoned rejections, and quote evidence for P8."""
    data=_json_first(text)
    entries=data.get('used')
    result={row['reply_turn_id']:[] for row in turns}
    invalid=[];accepted=[]
    if not isinstance(entries,list):
        return result,[{'reason':'missing_used' if entries is None else 'invalid_used_type'}],accepted
    by_turn={row['reply_turn_id']:row for row in turns}
    for entry in entries:
        if not isinstance(entry,dict):
            invalid.append({'reason':'invalid_turn','entry':entry});continue
        tid=entry.get('turn'); mid=entry.get('id')
        if tid not in by_turn:
            invalid.append({'reason':'invalid_turn','entry':entry});continue
        row=by_turn[tid]
        memories={m['id']:m['text'] for m in row['memories']}
        if not isinstance(mid,str) or mid not in row['context_ids'] or mid not in memories:
            invalid.append({'reason':'invalid_id','entry':entry});continue
        quote=entry.get('quote')
        if not isinstance(quote,str) or len(normalize_quote(quote))<2:
            invalid.append({'reason':'missing_quote','entry':entry});continue
        q=normalize_quote(quote);reply=normalize_quote(row['answer'])
        if q not in reply:
            invalid.append({'reason':'quote_not_in_reply','entry':entry});continue
        memory=normalize_quote(memories[mid])
        context=normalize_quote(' '.join(row.get('previous_messages',[])[-6:]))
        names={normalize_quote(name) for name in row.get('known_entity_names',())}
        short_names={name for name in names if len(name)==2} if short_match=='entity' else None
        matched=_match(q,memory,context,short_names)
        if matched is None:
            reason='from_context' if _match(q,memory,'',short_names) else 'unrelated_memory'
            invalid.append({'reason':reason,'entry':entry});continue
        if mid not in result[tid]:
            result[tid].append(mid)
            accepted.append({'turn':tid,'id':mid,'quote':quote,'match':matched,
                             'memory':memories[mid],'reply':row['answer']})
    return result,invalid,accepted
