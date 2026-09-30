"""P8-only clause-level induction candidates. P7 keeps pattern.py unchanged."""
from __future__ import annotations

import math
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime

import numpy as np

from .clock import logical_day
from .store import next_id

PROMPT = __import__('pathlib').Path(__file__).resolve().parents[1] / 'prompts' / 'pattern_v2.md'

ORDER = ('sameday', 'similar', 'recur')
DATES = (
    re.compile(r'\d+\s*月\s*\d+\s*[日号](?:凌晨|早上|上午|中午|下午|傍晚|晚上|夜里|晚)?'),
    re.compile(r'\d+\s*月\s*(?:初|中旬|上旬|下旬|底|中)'),
    re.compile(r'(?:上|下|这|本)?(?:周|星期)[一二三四五六日天]'),
    re.compile(r'\d{1,2}/\d{1,2}'),
)


def char_count(text: str) -> int:
    return sum(not (c.isspace() or unicodedata.category(c)[0] in 'PS') for c in text)


def clauses(text: str) -> list[str]:
    parts = []
    for piece in re.split(r'[。；！？\n，]', text):
        for pattern in DATES:
            piece = pattern.sub('', piece)
        piece = piece.strip()
        if piece:
            parts.append(piece)
    index = 0
    while index < len(parts):
        if char_count(parts[index]) >= 6 or len(parts) == 1:
            index += 1
            continue
        if index:
            parts[index - 1] += parts.pop(index)
            index -= 1
        else:
            parts[:2] = [parts[0] + parts[1]]
    return [part for part in parts if char_count(part) >= 4]


def own_days(conn, turn_times: dict[str, datetime], through_dream: int | None = None) -> dict[str, set]:
    """Only the memory's own integration operations establish its days."""
    result = defaultdict(set)
    sql = "SELECT d.id,dream_id,op_json,result_id FROM dream_ops o JOIN dream_runs d ON d.id=o.dream_id WHERE o.status='applied'"
    for row in conn.execute(sql):
        if through_dream is not None and int(row['id'][1:]) > through_dream:
            continue
        import json
        op = json.loads(row['op_json'])
        if op.get('op') not in ('new', 'revise', 'touch', 'merge'):
            continue
        mid = row['result_id']
        if not mid:
            continue
        for source in op.get('sources', []):
            if source in turn_times:
                result[mid].add(logical_day(turn_times[source]))
    return result


def _number(mid):
    return int(mid[1:])


def _own_clause_rows(snapshot, days, embedder):
    from .pattern_common import pattern_ids
    pattern = pattern_ids(snapshot)
    memories = {m['id']: m for m in snapshot.memories if m['id'] not in pattern}
    rows = [(mid, text, frozenset(days.get(mid, ())))
            for mid, m in memories.items() for text in clauses(m['text']) if days.get(mid)]
    vectors = embedder.encode([row[1] for row in rows]) if rows else np.zeros((0, 0))
    return memories, pattern, rows, vectors


def select_groups(snapshot, changed: set[str], anchor_days: set, own: dict, cfg,
                  *, limit: bool = True, skip_patterns: bool = True):
    """Return deterministic groups and pre-cap counts for the three P8 cues."""
    memories, pattern, rows, vectors = _own_clause_rows(snapshot, own, snapshot.embedder)
    changed = changed & set(memories)
    if not changed or not rows:
        return [], {kind: 0 for kind in ORDER}
    cos = vectors @ vectors.T
    all_days = set().union(*(own.get(mid,set()) for mid in memories)) if memories else set()
    anchors = [i for i, (mid, _, days) in enumerate(rows)
               if mid in changed and days & anchor_days]
    parents = snapshot.merges | frozenset((edge['a'],edge['b']) for edge in snapshot.event_edges
                                           if edge['component']=='merge')
    covered_by = defaultdict(set)
    if skip_patterns:
        for child, parent in parents:
            if child in pattern:
                covered_by[child].add(parent)
    candidates = []

    def add(kind, indices, score, cue, cue_days, order_score):
        member_days = {}
        for i, day in indices:
            member_days.setdefault(rows[i][0], day)
        mids = tuple(sorted(member_days, key=_number))
        if len(mids) < 2 or len(set().union(*(own[mid] for mid in mids))) < 2:
            return
        if skip_patterns and any(set(mids) <= members for members in covered_by.values()):
            return
        reinforced = bool(skip_patterns and any((set(mids) - changed) <= members
                                                  for members in covered_by.values()))
        candidates.append({'kind': kind, 'ids': mids, 'strength': float(score),
                           'days': sorted(day.isoformat() for day in cue_days), 'cue': cue,
                           'member_days': {mid: member_days[mid].isoformat() for mid in mids},
                           'rank': tuple(order_score), 'reinforced': reinforced})

    def kind_days(i):
        return set().union(*(rows[j][2] for j in range(len(rows))
                             if float(cos[i, j]) >= cfg.pattern_kind_cos))

    kd = {i: kind_days(i) for i in anchors}
    relevant = {i: kd[i] for i in anchors}

    def members_for(i, days, used, cap):
        out = []
        for day in sorted(days, reverse=True):
            choices = [j for j, (mid, _, owned) in enumerate(rows)
                       if day in owned and mid not in used and float(cos[i, j]) >= cfg.pattern_kind_cos]
            if choices:
                best = min(choices, key=lambda j: (-float(cos[i, j]), _number(rows[j][0]), j))
                out.append((best, day)); used.add(rows[best][0])
            if len(used) >= cap:
                break
        return out

    for i in anchors:
        mid, text, _ = rows[i]
        days = relevant[i]
        if len(days) >= 2:
            selected = members_for(i, days, set(), cfg.pattern_recur_max_members)
            mean_cos = sum(float(cos[i, j]) for j, _ in selected) / len(selected) if selected else 0
            add('recur', selected, mean_cos, f'「{text[:24]}」这类事反复出现，共 {len(days)} 天',
                days, (len(days), mean_cos))

    for pos, i in enumerate(anchors):
        amid, atext, adays = rows[i]
        for j in anchors[pos + 1:]:
            bmid, btext, bdays = rows[j]
            if not (adays & bdays & anchor_days) or float(cos[i, j]) >= cfg.pattern_kind_cos:
                continue
            bdays_kind = relevant[j]
            common = relevant[i] & bdays_kind
            if len(common) < 2:
                continue
            weight = (max(0.0, math.log(len(common) * len(all_days) /
                                        (len(relevant[i]) * len(bdays_kind)))) *
                      (1 - math.exp(-len(common) / cfg.kappa)))
            if weight < cfg.pattern_cooccur_min:
                continue
            selected = []
            used = set()
            for day in sorted(common, reverse=True)[:cfg.pattern_sameday_max_days]:
                selected_today=set()
                for anchor in (i, j):
                    choices=[index for index,(candidate_mid,_,owned) in enumerate(rows)
                             if day in owned and (candidate_mid not in used or candidate_mid in selected_today)
                             and float(cos[anchor,index]) >= cfg.pattern_kind_cos]
                    if not choices:continue
                    best=min(choices,key=lambda index:(-float(cos[anchor,index]),
                                                       _number(rows[index][0]),index))
                    chosen_mid=rows[best][0]
                    if chosen_mid not in used:
                        if len(used)>=cfg.pattern_sameday_max_members:continue
                        selected.append((best,day));used.add(chosen_mid)
                    selected_today.add(chosen_mid)
                if len(used) >= cfg.pattern_sameday_max_members:
                    break
            cue = (f'「{atext[:24]}」和「{btext[:24]}」这两类事常在同一天出现，'
                   f'共 {len(common)} 天')
            add('sameday', selected, weight, cue, common, (len(common), weight))

    ids = sorted(memories, key=_number)
    for n, a in enumerate(ids):
        for b in ids[n + 1:]:
            if not changed.intersection((a, b)) or (a, b) in parents or (b, a) in parents:
                continue
            if set(memories[a]['entities']) & set(memories[b]['entities']):
                continue
            if not own.get(a) or not own.get(b) or len(own[a] | own[b]) < 2:
                continue
            sim = float(memories[a]['embedding'] @ memories[b]['embedding'])
            if cfg.pattern_cos_low <= sim <= cfg.pattern_cos_high:
                da = max(own[a]); db = max(own[b])
                add('similar', [(next(i for i, row in enumerate(rows) if row[0] == a), da),
                                (next(i for i, row in enumerate(rows) if row[0] == b), db)],
                    sim, '话题不同、处境相似', {da, db}, (sim,))

    priority = {kind: n for n, kind in enumerate(ORDER)}
    candidates.sort(key=lambda row: (priority[row['kind']], -len(row['ids']),
                                     -row['strength'], _number(row['ids'][0])))
    exact = {}
    for row in candidates:
        exact.setdefault(row['ids'], row)
    kept = []
    for kind in ORDER:
        subset = [row for row in exact.values() if row['kind'] == kind]
        subset.sort(key=lambda row: (-len(row['ids']), -row['strength'], _number(row['ids'][0])))
        within = []
        for row in subset:
            members = set(row['ids'])
            if any(members <= set(other['ids']) or
                   len(members & set(other['ids'])) / len(members | set(other['ids'])) >= .5
                   for other in within):
                continue
            within.append(row)
        within.sort(key=lambda row: (row['reinforced'], *(-x for x in row['rank']),
                                     _number(row['ids'][0])))
        kept.extend(within)
    counts = {kind: sum(row['kind'] == kind for row in kept) for kind in ORDER}
    if limit:
        chosen = []
        by_kind = defaultdict(int)
        for row in kept:
            if by_kind[row['kind']] >= cfg.pattern_max_per_type or len(chosen) >= cfg.pattern_max_groups:
                continue
            chosen.append(row); by_kind[row['kind']] += 1
        kept = chosen
    return kept, counts


def render_prompt(snapshot, groups, now, prompt_path=None):
    from .clock import format_now
    from .pattern_common import pattern_ids
    raw = re.sub(r'<!--.*?-->', '', (prompt_path or PROMPT).read_text(encoding='utf-8'), flags=re.S)
    system, user = raw.split('=== user ===', 1)
    system = system.split('=== system ===', 1)[1].strip()
    memories = {m['id']:m for m in snapshot.memories}
    lines = []
    for number, group in enumerate(groups):
        lines.append(f'组 {number}（线索：{group["cue"]}）')
        for mid in sorted(group['ids'], key=lambda mid:(group['member_days'][mid], _number(mid))):
            day = __import__('datetime').date.fromisoformat(group['member_days'][mid])
            lines.append(f'- {mid}｜{day.month}月{day.day}日｜{memories[mid]["text"]}')
        lines.append('')
    direct = {child for child, parent in (snapshot.merges | frozenset(
              (edge['a'],edge['b']) for edge in snapshot.event_edges if edge['component']=='merge'))
              if parent in {mid for group in groups for mid in group['ids']}}
    existing = []
    for mid in pattern_ids(snapshot):
        memory = memories[mid]
        score = max(float(memory['embedding'] @ memories[member]['embedding'])
                    for group in groups for member in group['ids'])
        existing.append((mid in direct, score, memory))
    existing.sort(key=lambda row:(-row[0], -row[1], _number(row[2]['id'])))
    listed = '\n'.join(f'{m["id"]}｜{m["text"]}' for _,_,m in existing[:10]) or '（无）'
    user = (user.strip().replace('{{now}}',format_now(now))
            .replace('{{existing}}',listed).replace('{{groups}}','\n'.join(lines).strip()))
    return system, user


def apply_patterns(store, dream_id, groups, obj, embedder, now, cfg):
    from .pattern_common import _source_union, pattern_ids, WRITE_KINDS
    entries = obj.get('patterns')
    if not isinstance(entries,list):
        raise ValueError('patterns array missing')
    selected={}; log=[]
    for entry in entries:
        if not isinstance(entry,dict) or type(entry.get('group')) is not int or not 0<=entry['group']<len(groups):
            log.append({'status':'invalid_group','row':entry});continue
        number=entry['group']
        if number in selected:
            log.append({'group':number,'status':'duplicate_group'});continue
        selected[number]=entry
    current={m['id']:m for m in store.snapshot(embedder).memories
             if m['id'] in pattern_ids(store.snapshot(embedder))}
    turn_times={row['turn_id']:datetime.fromisoformat(row['time'])
                for row in store.conn.execute('SELECT turn_id,time FROM cold_turns')}
    own=own_days(store.conn,turn_times)
    with store.dream_transaction() as conn:
        for number,group in enumerate(groups):
            row=selected.get(number)
            if row is None:
                log.append({'group':number,'status':'missing'});continue
            if row.get('none') is True:
                log.append({'group':number,'status':'none'});continue
            if 'instances' not in row:
                log.append({'group':number,'status':'missing_instances'});continue
            supplied=row['instances']
            if not isinstance(supplied,list):
                log.append({'group':number,'status':'invalid_instances_type'});continue
            members=tuple(dict.fromkeys(x for x in supplied if isinstance(x,str) and x in group['ids']))
            kind='existing' if isinstance(row.get('existing'),str) else 'text'
            if kind=='existing' and row['existing'] not in current:
                log.append({'group':number,'status':'invalid_existing'});continue
            if len(members)<(1 if kind=='existing' else 2):
                log.append({'group':number,'status':'insufficient_instances'});continue
            if kind=='text' and len(set().union(*(own.get(mid,set()) for mid in members)))<2:
                log.append({'group':number,'status':'insufficient_instance_days'});continue
            if kind=='text':
                body=row.get('text')
                if not isinstance(body,str) or not body.strip() or len(body)>300:
                    log.append({'group':number,'status':'invalid_text'});continue
                body=body.strip()
                vector=embedder.encode([body])[0]
                duplicate=next((mid for mid,m in sorted(current.items(),key=lambda item:_number(item[0]))
                                if float(vector @ m['embedding'])>=cfg.pattern_dup_cos),None)
                if duplicate:
                    kind='existing';target=duplicate
                    log.append({'group':number,'status':'text_dup_to_existing','id':duplicate})
                else:
                    target=next_id(conn,'memories','m')
                    try: salience=min(3.,max(1.,float(row.get('salience',1))))
                    except (ValueError,TypeError): salience=1.
            else:
                target=row['existing']
            sources,occurrences,events=_source_union(conn,members)
            if not occurrences:
                log.append({'group':number,'status':'no_instance_occurrence'});continue
            at=max(occurrences)
            if kind=='text':
                conn.execute('INSERT INTO memories VALUES(?,?,?,?,?,?)',
                             (target,body,salience,at,dream_id,vector.astype(np.float32).tobytes()))
                conn.execute('INSERT INTO memory_versions VALUES(?,?,?,?,?,?,?)',
                             (target,1,body,at,dream_id,number,'pattern'))
                for parent,when,weight,event_kind in events:
                    conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',
                                 (target,when,weight,event_kind,f'pattern:{parent}'))
                for parent in members:
                    latest=conn.execute('SELECT MAX(at) FROM occurrences WHERE memory_id=?',(parent,)).fetchone()[0]
                    conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',
                                 (target,latest,1.,'pattern',f'pattern:{parent}'))
                for when in occurrences:
                    conn.execute('INSERT OR IGNORE INTO occurrences VALUES(?,?,?)',(target,when,dream_id))
                current[target]={'id':target,'embedding':vector,'text':body}
            else:
                conn.execute('INSERT OR IGNORE INTO occurrences VALUES(?,?,?)',(target,at,dream_id))
                conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',
                             (target,at,1.,'touch',f'pattern:{dream_id}:{number}'))
            for sid in sources:
                conn.execute('INSERT OR IGNORE INTO memory_sources VALUES(?,?,?)',(target,sid,dream_id))
            for member in members:
                if target==member:
                    log.append({'group':number,'status':'self_loop_skipped','id':target});continue
                conn.execute("INSERT OR REPLACE INTO event_edges VALUES(?,?,'merge',1,0,?,'')",
                             (target,member,now.isoformat()))
            log.append({'group':number,'status':kind,'id':target,'kind':group['kind'],
                        'members':list(members),'days':group['days']})
    return log


def run_pattern(store,dream_id,llm,embedder,now,cfg,prompt_dir=None):
    from .integrate import _json_first
    from .pattern_common import changed_ids
    snapshot=store.snapshot(embedder)
    turn_times={row['turn_id']:datetime.fromisoformat(row['time'])
                for row in store.conn.execute('SELECT turn_id,time FROM cold_turns')}
    own=own_days(store.conn,turn_times)
    anchor={logical_day(datetime.fromisoformat(row['time'])) for row in store.conn.execute(
        'SELECT time FROM cold_turns WHERE integrated_by=?',(dream_id,))}
    groups,counts=select_groups(snapshot,changed_ids(store,dream_id),anchor,own,cfg)
    if not groups:
        return {'status':'no_candidates','groups':[],'candidate_counts':counts,'results':[]}
    system,user=render_prompt(snapshot,groups,now,
                              __import__('pathlib').Path(prompt_dir)/'pattern_v2.md' if prompt_dir else None)
    result=llm.complete(system=system,messages=[{'role':'user','content':user}],
                        max_tokens=2500,purpose='pattern')
    try:
        actions=apply_patterns(store,dream_id,groups,_json_first(result.text),embedder,now,cfg)
        status='applied'
    except (ValueError,TypeError,KeyError) as exc:
        actions=[];status='parse_failed:'+type(exc).__name__
    return {'status':status,'groups':groups,'candidate_counts':counts,'results':actions,
            'cache_key':result.cache_key,'cache_hit':result.cache_hit,
            'usage':result.usage,'response':result.text}
