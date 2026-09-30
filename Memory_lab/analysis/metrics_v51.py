"""Read-only, as-of local checks shared by P7 archives and P8 replay."""
from __future__ import annotations

import fnmatch
from collections import defaultdict

from Conversation_Memory.engine.clock import logical_day
from Conversation_Memory.engine.pattern_common import pattern_ids

PLANTS = {
    'A11': ('analogy', ('incubator',), ('platereader',), ('培养箱',), ('酶标仪',), ('读数','仪器','设备','显示')),
    'A12': ('sameday', ('imaging-*',), ('malatang-*',), ('成像','机时','实验'), ('麻辣烫',), ()),
    'A13': ('sameday', ('meeting-*',), ('mom-*',), ('陈老师','开会','组会','汇报','开完会'), ('妈',), ()),
    'B11': ('analogy', ('sketch-1',), ('sketch-2',), ('老王',), ('妹妹','请柬'), ('草图','参考','几组','方向')),
    'B12': ('sameday', ('deliver-*',), ('milktea-*',), ('交稿','交了','交付','一批'), ('奶茶','杨枝甘露','芋泥','喝的'), ()),
    'B13': ('sameday', ('call-*',), ('bike-*',), ('老王','电话'), ('骑',), ()),
}

ATTENTION = {
    'A01': ('深夜','熬夜','实验室','机时','knockdown','这么晚'),
    'A02': ('深夜','很晚','熬夜','难得','平时'),
    'A03': ('压力','解压','缓解','脑子','睡得'),
    'A11': ('仪器','读数','温度计','标准品','培养箱','酶标仪','校准','设备'),
    'A12': ('麻辣烫',), 'A13': ('妈',),
    'B01': ('画','五点','清晨','早起','稿'),
    'B02': ('五点','清晨','早起','画','难得','少见'),
    'B03': ('焦虑','解压','心里顺','习惯','稿'),
    'B11': ('草图','参考图','请柬','妹妹','老王','几组','三组'),
    'B12': ('奶茶','杨枝甘露','芋泥','喝的','喝点'),
    'B13': ('骑','河边'),
}

TRIVIA = {'A24': ('桂花拿铁','拿铁'), 'A25': ('柯基',),
          'A26': ('红烧肉',), 'B24': ('烤红薯','红薯'),
          'B25': ('风筝','章鱼'), 'B26': ('酸菜鱼',)}


def tag_sources(tags, patterns):
    return {source for pattern in patterns for tag, ids in tags.items()
            if fnmatch.fnmatchcase(tag, pattern) for source in ids}


def formation(snapshot, tags, turn_times, probe_id):
    kind, a_tags, b_tags, a_words, b_words, abstract = PLANTS[probe_id]
    a, b = tag_sources(tags, a_tags), tag_sources(tags, b_tags)
    pattern = pattern_ids(snapshot)
    records = []
    for m in snapshot.memories:
        sources = set(m['sources'])
        da = {logical_day(turn_times[s]) for s in sources & a if s in turn_times}
        db = {logical_day(turn_times[s]) for s in sources & b if s in turn_times}
        source_only = len(da & db) >= 2 if kind == 'sameday' else bool(da and db)
        if kind == 'sameday':
            text_source = source_only and any(w in m['text'] for w in a_words) and any(w in m['text'] for w in b_words)
        else:
            text_source = (any(w in m['text'] for w in abstract) and
                           (bool(da) or any(w in m['text'] for w in a_words)) and
                           (bool(db) or any(w in m['text'] for w in b_words)))
        if source_only or text_source:
            records.append({'id':m['id'], 'text':m['text'],
                            'kind':'pattern' if m['id'] in pattern else 'ordinary',
                            'source_only':source_only,'text_source':text_source,
                            'a_days':sorted(x.isoformat() for x in da),
                            'b_days':sorted(x.isoformat() for x in db)})
    return records


def attention(row):
    key = row['probe_id']
    if key not in ATTENTION or not row.get('answer'):
        return None
    answer = row['answer']
    noticed = answer.get('noticed') or answer.get('context',{}).get('noticed') or ''
    thought = ' '.join(str(v) for v in noticed.values()) if isinstance(noticed,dict) else str(noticed)
    reply = answer['text']
    terms = [word for word in ATTENTION[key] if word not in row['message']]
    return {'id':key, 'noticed':noticed,'reply':reply,
            'thought_hits':[w for w in terms if w in thought],
            'reply_hits':[w for w in terms if w in reply]}


def floating(row):
    targets = (row.get('remote_diagnostic') or {}).get('target_memories',())
    ranks = [m['rank'] for m in targets if isinstance(m.get('rank'),int)]
    return {'id':row['probe_id'], 'rank':min(ranks) if ranks else None,
            'remote':any(m['section']=='remote' for m in targets),
            'top6':any(rank<=6 for rank in ranks),
            'reasons':[m.get('reason','') for m in row['recall']['remote']]}


def forgetting_answer(rows):
    return {row['probe_id']: {'mentioned':any(w in row['answer']['text'] for w in TRIVIA[row['probe_id']]),
                               'text':row['answer']['text']}
            for row in rows if row['probe_id'] in TRIVIA and row['variant_days']==60 and row.get('answer')}
