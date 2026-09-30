"""P9 induction: memory analogy plus implicit utterance repetition/cooccurrence."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

from .clock import format_now,logical_day
from .pattern_common import _source_union,changed_ids,pattern_ids
from .pattern_v2 import own_days,select_groups as select_memory_groups
from .store import next_id
from .trace import read_rows
from .trace_candidates import select_trace_groups

PROMPT=Path(__file__).resolve().parents[1]/'prompts'/'pattern_v3.md'
ORDER=('sameday','similar','recur')


def _number(mid):return int(mid[1:])


def select_groups(store,dream_id,embedder,cfg,*,limit=True):
    snap=store.snapshot(embedder)
    turn_times={r['turn_id']:datetime.fromisoformat(r['time'])
                for r in store.conn.execute('SELECT turn_id,time FROM cold_turns')}
    own=own_days(store.conn,turn_times)
    current={r['turn_id'] for r in store.conn.execute(
        "SELECT turn_id FROM cold_turns WHERE integrated_by=? AND role='user'",(dream_id,))}
    anchor={logical_day(turn_times[tid]) for tid in current}
    memory_groups,_=select_memory_groups(snap,changed_ids(store,dream_id),anchor,own,cfg,
                                          limit=False)
    similar=[{**g,'origin':'memory'} for g in memory_groups if g['kind']=='similar']
    patterns=pattern_ids(snap)
    sources=[m['sources'] for m in snap.memories if m['id'] in patterns]
    trace,trace_counts=select_trace_groups(read_rows(store.conn),current,cfg,sources,limit=False)
    candidate={kind:[g for g in trace if g['kind']==kind] for kind in ('sameday','recur')}
    candidate['similar']=similar
    counts={'sameday':trace_counts['sameday'],'similar':len(similar),
            'recur':trace_counts['recur']}
    groups=[]
    for kind in ORDER:
        selected=candidate[kind][:cfg.pattern_max_per_type] if limit else candidate[kind]
        groups.extend(selected)
        if limit and len(groups)>=cfg.pattern_max_groups:
            groups=groups[:cfg.pattern_max_groups];break
    return groups,counts


def render_prompt(snapshot,groups,now):
    raw=re.sub(r'<!--.*?-->','',PROMPT.read_text(encoding='utf-8'),flags=re.S)
    system,user=raw.split('=== user ===',1)
    system=system.split('=== system ===',1)[1].strip()
    memories={m['id']:m for m in snapshot.memories}
    lines=[]
    for number,group in enumerate(groups):
        lines.append(f'组 {number}（线索：{group["cue"]}）')
        if group['origin']=='trace':
            for row in group['evidence']:
                day=__import__('datetime').date.fromisoformat(row['day'])
                lines.append(f'- {row["turn_id"]}｜{day.month}月{day.day}日｜他说：{row["original"]}')
        else:
            for mid in sorted(group['ids'],key=lambda m:(group['member_days'][m],_number(m))):
                day=__import__('datetime').date.fromisoformat(group['member_days'][mid])
                lines.append(f'- {mid}｜{day.month}月{day.day}日｜{memories[mid]["text"]}')
        lines.append('')
    represented={mid for g in groups if g['origin']=='memory' for mid in g['ids']}
    trace_turns={tid for g in groups if g['origin']=='trace' for tid in g['ids']}
    direct={edge['a'] for edge in snapshot.event_edges if edge['component']=='merge'
            and edge['b'] in represented}
    direct|={child for child,parent in snapshot.merges if parent in represented}
    patterns=pattern_ids(snapshot)
    trace_texts=[r['text'] for g in groups if g['origin']=='trace' for r in g['evidence']]
    trace_vectors=snapshot.embedder.encode(trace_texts) if trace_texts else ()
    existing=[]
    for mid in patterns:
        m=memories[mid]
        linked=mid in direct or bool(set(m['sources'])&trace_turns)
        scores=[float(m['embedding']@memories[other]['embedding']) for other in represented]
        scores.extend(float(m['embedding']@v) for v in trace_vectors)
        existing.append((linked,max(scores) if scores else 0.,mid,m['text']))
    existing.sort(key=lambda x:(-x[0],-x[1],_number(x[2])))
    listed='\n'.join(f'{mid}｜{body}' for _,_,mid,body in existing[:10]) or '（无）'
    user=(user.strip().replace('{{now}}',format_now(now)).replace('{{existing}}',listed)
          .replace('{{groups}}','\n'.join(lines).strip()))
    return system,user


def _trace_links(conn,members,current):
    selected=set(members);links=[]
    for mid in (r[0] for r in conn.execute('SELECT id FROM memories')):
        if mid in current:continue
        overlap=sum(r[0] in selected for r in conn.execute(
            'SELECT turn_id FROM memory_sources WHERE memory_id=?',(mid,)))
        if overlap:links.append((overlap,mid))
    links.sort(key=lambda x:(-x[0],_number(x[1])))
    return [mid for _,mid in links[:6]]


def apply_patterns(store,dream_id,groups,obj,embedder,now,cfg):
    entries=obj.get('patterns')
    if not isinstance(entries,list):raise ValueError('patterns array missing')
    selected={};log=[]
    for entry in entries:
        if not isinstance(entry,dict) or type(entry.get('group')) is not int or not 0<=entry['group']<len(groups):
            log.append({'status':'invalid_group','row':entry});continue
        number=entry['group']
        if number in selected:
            log.append({'group':number,'status':'duplicate_group'});continue
        selected[number]=entry
    snap=store.snapshot(embedder)
    current={m['id']:m for m in snap.memories if m['id'] in pattern_ids(snap)}
    turn_times={r['turn_id']:datetime.fromisoformat(r['time'])
                for r in store.conn.execute('SELECT turn_id,time FROM cold_turns')}
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
            members=tuple(dict.fromkeys(x for x in supplied
                                        if isinstance(x,str) and x in group['ids']))
            kind='existing' if isinstance(row.get('existing'),str) else 'text'
            if kind=='existing' and row['existing'] not in current:
                log.append({'group':number,'status':'invalid_existing'});continue
            if len(members)<(1 if kind=='existing' else 2):
                log.append({'group':number,'status':'insufficient_instances'});continue
            if kind=='text':
                days=({__import__('datetime').date.fromisoformat(group['member_days'][tid]) for tid in members}
                      if group['origin']=='trace' else set().union(*(own.get(mid,set()) for mid in members)))
                if len(days)<2:
                    log.append({'group':number,'status':'insufficient_instance_days'});continue
                body=row.get('text')
                if not isinstance(body,str) or not body.strip() or len(body)>300:
                    log.append({'group':number,'status':'invalid_text'});continue
                body=body.strip();vector=embedder.encode([body])[0]
                duplicate=next((mid for mid,m in sorted(current.items(),key=lambda x:_number(x[0]))
                                if float(vector@m['embedding'])>=cfg.pattern_dup_cos),None)
                if duplicate:
                    kind='existing';target=duplicate
                    log.append({'group':number,'status':'text_dup_to_existing','id':target})
                else:
                    target=next_id(conn,'memories','m')
                    try:salience=min(3.,max(1.,float(row.get('salience',1))))
                    except (ValueError,TypeError):salience=1.
            else:target=row['existing']
            if group['origin']=='trace':
                evidence={r['turn_id']:r for r in group['evidence']}
                times=sorted(evidence[tid]['at'] for tid in members)
                sources=list(members)
                links=_trace_links(conn,members,current)
                day_latest={}
                for tid in members:
                    event=evidence[tid]
                    day_latest[event['day']]=max(day_latest.get(event['day'],''),event['at'])
            else:
                sources,times,events=_source_union(conn,members)
                links=list(members)
            if not times:
                log.append({'group':number,'status':'no_instance_occurrence'});continue
            at=max(times)
            if kind=='text':
                conn.execute('INSERT INTO memories VALUES(?,?,?,?,?,?)',
                             (target,body,salience,at,dream_id,np.asarray(vector,dtype=np.float32).tobytes()))
                conn.execute('INSERT INTO memory_versions VALUES(?,?,?,?,?,?,?)',
                             (target,1,body,at,dream_id,number,'pattern'))
                if group['origin']=='trace':
                    conn.execute('INSERT INTO memory_pattern_origins VALUES(?,?)',(target,'trace'))
                    for day,when in sorted(day_latest.items()):
                        conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',
                                     (target,when,1.,'pattern','pattern:trace:'+day))
                else:
                    for parent,when,weight,event_kind in events:
                        conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',
                                     (target,when,weight,event_kind,f'pattern:{parent}'))
                    for parent in members:
                        latest=conn.execute('SELECT MAX(at) FROM occurrences WHERE memory_id=?',(parent,)).fetchone()[0]
                        conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',
                                     (target,latest,1.,'pattern',f'pattern:{parent}'))
                current[target]={'id':target,'embedding':vector,'text':body}
            else:
                conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',
                             (target,at,1.,'touch',f'pattern:{dream_id}:{number}'))
            occurrence_times=times if kind=='text' or group['origin']=='trace' else [at]
            for when in occurrence_times:
                conn.execute('INSERT OR IGNORE INTO occurrences VALUES(?,?,?)',(target,when,dream_id))
            for sid in sources:
                conn.execute('INSERT OR IGNORE INTO memory_sources VALUES(?,?,?)',(target,sid,dream_id))
            for member in links:
                if member==target:
                    log.append({'group':number,'status':'self_loop_skipped','id':target});continue
                conn.execute("INSERT OR REPLACE INTO event_edges VALUES(?,?,'merge',1,0,?,'')",
                             (target,member,now.isoformat()))
            log.append({'group':number,'status':kind,'id':target,'kind':group['kind'],
                        'origin':group['origin'],'members':list(members),
                        'linked_memories':links,'days':group['days']})
    return log


def run_pattern(store,dream_id,llm,embedder,now,cfg):
    from .integrate import _json_first
    groups,counts=select_groups(store,dream_id,embedder,cfg)
    if not groups:
        return {'status':'no_candidates','groups':[],'candidate_counts':counts,'results':[]}
    system,user=render_prompt(store.snapshot(embedder),groups,now)
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
