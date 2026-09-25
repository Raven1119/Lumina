"""Dream-only integration: persist cached response, validate, apply atomically."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime,timedelta
from pathlib import Path

import numpy as np

from .clock import format_now,rough_age
from .entities import align,matches
from .store import Store,next_id
from .types import DreamResult,Turn

PROMPT=Path(__file__).resolve().parents[1]/'prompts'/'integrate_v1.md'


def _json_first(text:str):
    decoder=json.JSONDecoder()
    for match in re.finditer(r'\{',text):
        try:
            obj,_=decoder.raw_decode(text[match.start():])
            if isinstance(obj,dict):return obj
        except json.JSONDecodeError:pass
    raise ValueError('invalid_json')


def _old_memories(store:Store):
    from .snapshot import build_snapshot
    return build_snapshot(store,None).memories


def remind(store:Store,window:list[Turn],embedder,cfg)->list[dict]:
    """Frozen write-side retrieval, independent of stage and recall traces."""
    memories=list(_old_memories(store))
    if not memories:return []
    chunks=[window[i:i+cfg.remind_chunk_turns] for i in range(0,len(window),cfg.remind_chunk_turns)]
    cues=['\n'.join(('他' if t.role=='user' else '我')+'：'+t.text for t in chunk) for chunk in chunks]
    vectors=embedder.encode(cues)
    matrix=np.stack([m['embedding'] for m in memories])
    cos=vectors@matrix.T
    chosen=set()
    near_dup=set()
    for row in cos:
        order=sorted(range(len(memories)),key=lambda i:(-float(row[i]),memories[i]['id']))
        chosen.update(order[:cfg.remind_per_chunk])
        near_dup.update(i for i,value in enumerate(row) if value>=cfg.remind_dup_cos)
    ents=list(_old_entities(store))
    window_text='\n'.join(t.text for t in window)
    entity_ids=matches(window_text,ents)
    for eid in entity_ids[:cfg.remind_entity_cap]:
        related=[i for i,m in enumerate(memories) if eid in m['entities']]
        related.sort(key=lambda i:(-max(men['occurrences']).timestamp() if (men:=memories[i])['occurrences'] else 0,men['id']))
        chosen.update(related[:3])
    chosen.update(near_dup)
    ranked=sorted(chosen,key=lambda i:(i not in near_dup,
                                        not bool(set(memories[i]['entities'])&set(entity_ids)),
                                        -float(cos[:,i].max()),memories[i]['id']))
    return [memories[i] for i in ranked[:cfg.remind_cap]]


def _old_entities(store:Store):
    c=store.conn
    for row in c.execute('SELECT * FROM entities ORDER BY CAST(SUBSTR(id,2) AS INTEGER)'):
        yield {'id':row['id'],'name':row['name'],
               'aliases':[r[0] for r in c.execute('SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias',(row['id'],))]}


def render_prompt(store:Store,window:list[Turn],reminded:list[dict],now:datetime,cfg,prompt_path:Path=PROMPT):
    raw=re.sub(r'<!--.*?-->','',prompt_path.read_text(encoding='utf-8'),flags=re.S)
    system,user=raw.split('=== user ===',1)
    system=system.split('=== system ===',1)[1].strip()
    user=user.strip()
    memlines=[]
    for m in sorted(reminded,key=lambda m:(m['created_at'],m['id'])):
        occurrences=m['occurrences']
        first=rough_age((now-min(occurrences)).total_seconds()/86400)
        last=rough_age((now-max(occurrences)).total_seconds()/86400)
        writes=sum(kind in ('new','revise','merge','touch') for _,_,kind,_ in m['event_rows'])
        count='一次' if writes<=1 else '几次' if writes<=3 else '常提'
        memlines.append(f"{m['id']}｜首次{first}，最近一次{last}｜{count}｜{m['text']}")
    known=list(_old_entities(store))
    by_id={e['id']:e for e in known}
    ids=matches('\n'.join(t.text for t in window),known)
    for m in reminded:
        ids.extend(eid for eid in m['entities'] if eid not in ids)
    entlines=[]
    for eid in ids[:cfg.entities_in_prompt_cap]:
        ent=by_id[eid]
        aliases='（别名：'+'、'.join(ent['aliases'])+'）' if ent['aliases'] else ''
        entlines.append(f"{eid}｜{ent['name']}{aliases}")
    lines=[f"[{t.id}] {t.time.month}月{t.time.day}日 周{'一二三四五六日'[t.time.weekday()]} {t.time:%H:%M} {'他' if t.role=='user' else '我'}：{t.text}" for t in window]
    data={'{{now}}':format_now(now),'{{memories}}':'\n'.join(memlines) or '（无）',
          '{{entities}}':'\n'.join(entlines) or '（无）','{{window}}':'\n'.join(lines)}
    for key,value in data.items():
        system=system.replace(key,value); user=user.replace(key,value)
    return system,user


def _source_times(op,window_by_id):
    supplied=op.get('sources',[])
    ids=[x for x in supplied if isinstance(x,str) and x in window_by_id] if isinstance(supplied,list) else []
    return list(dict.fromkeys(ids)),[window_by_id[x].time for x in ids]


def _add_sources(conn,mid,ids,times,dream_id):
    for sid in ids:conn.execute('INSERT OR IGNORE INTO memory_sources VALUES(?,?,?)',(mid,sid,dream_id))
    for at in times:conn.execute('INSERT OR IGNORE INTO occurrences VALUES(?,?,?)',(mid,at.isoformat(),dream_id))


def consume_traces(store:Store,cfg,now:datetime,dream_id:str):
    conn=store.conn
    for trace in conn.execute('SELECT * FROM recall_traces WHERE consumed_by IS NULL ORDER BY id').fetchall():
        at=datetime.fromisoformat(trace['at'])
        ids=list(dict.fromkeys(json.loads(trace['near_json'])+json.loads(trace['remote_json'])))
        if cfg.recall_strengthening:
            for mid in ids:
                row=conn.execute("SELECT MAX(at) FROM strength_events WHERE memory_id=? AND kind='recall'",(mid,)).fetchone()
                if row[0] is None or at-datetime.fromisoformat(row[0])>=timedelta(hours=cfg.recall_dedup_hours):
                    conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',
                                 (mid,at.isoformat(),cfg.w_recall,'recall',f'trace:{trace["id"]}'))
        if cfg.corecall:
            for i,first in enumerate(ids):
                for second in ids[i+1:]:
                    a,b=sorted((first,second))
                    row=conn.execute("SELECT weight,last_reinforced FROM event_edges WHERE a=? AND b=? AND component='corecall'",(a,b)).fetchone()
                    if row and at-datetime.fromisoformat(row['last_reinforced'])<timedelta(hours=cfg.recall_dedup_hours):continue
                    weight=(row['weight']*math.exp(-(at-datetime.fromisoformat(row['last_reinforced'])).total_seconds()/86400/cfg.event_decay_days) if row else 0)+cfg.beta
                    conn.execute("INSERT OR REPLACE INTO event_edges VALUES(?,?,'corecall',?,0,?,'')",(a,b,weight,at.isoformat()))
        conn.execute('UPDATE recall_traces SET consumed_by=? WHERE id=?',(dream_id,trace['id']))


def run_dream(store:Store,cold_window:list[Turn],llm,embedder,clock,cfg,attempt:int=0)->DreamResult:
    if not cold_window:raise ValueError('empty dream window')
    now=clock.now()
    reminded=remind(store,cold_window,embedder,cfg)
    system,user=render_prompt(store,cold_window,reminded,now,cfg)
    result=llm.complete(system=system,messages=[{'role':'user','content':user}],
                        max_tokens=cfg.dream_max_tokens,purpose='dream',attempt=attempt)
    dream_id=next_id(store.conn,'dream_runs','d')
    try:
        obj=_json_first(result.text)
        ops=obj.get('ops')
        if not isinstance(ops,list):raise ValueError('missing_ops')
    except ValueError as exc:
        store.conn.execute('INSERT INTO dream_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (dream_id,now.isoformat(),cold_window[0].id,cold_window[-1].id,'failed',
                            result.cache_key,'integrate_v1',cfg.llm_model,0,0,json.dumps(result.usage),str(exc),result.text))
        store.conn.commit()
        return DreamResult(dream_id,'failed',result.cache_key,result.cache_hit,result.usage,0,0)
    window_by_id={t.id:t for t in cold_window}
    allowed={m['id'] for m in reminded}
    rejected=0;created=[];resolutions={}
    with store.dream_transaction() as conn:
        conn.execute('INSERT INTO dream_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (dream_id,now.isoformat(),cold_window[0].id,cold_window[-1].id,'applied',
                      result.cache_key,'integrate_v1',cfg.llm_model,len(ops),0,json.dumps(result.usage),None,result.text))
        for index,op in enumerate(ops):
            if not isinstance(op,dict):op={}
            kind=op.get('op'); reason=None;warning=None;mid=None
            sources,times=_source_times(op,window_by_id)
            if kind not in ('new','revise','merge','touch','relate'):reason='unknown_op'
            elif kind=='relate':continue
            elif not sources:reason='no_window_sources'
            elif kind in ('revise','touch') and (not isinstance(op.get('id'),str) or op['id'] not in allowed):reason='id_not_reminded'
            elif kind=='merge' and (not isinstance(op.get('ids'),list) or
                                    not all(isinstance(x,str) for x in op['ids']) or
                                    len(set(op['ids']))<2 or any(x not in allowed for x in op['ids'])):reason='invalid_merge_ids'
            elif kind in ('new','revise','merge') and (not isinstance(op.get('text'),str) or not op['text'].strip() or len(op['text'])>300):reason='invalid_text'
            if reason is None:
                at=max(times).isoformat()
                if kind in ('new','merge'):
                    mid=next_id(conn,'memories','m')
                    parents=list(dict.fromkeys(op['ids'])) if kind=='merge' else []
                    if kind=='merge' and 'salience' not in op:
                        salience=max(conn.execute('SELECT salience FROM memories WHERE id=?',(p,)).fetchone()[0] for p in parents)
                    else:
                        try:salience=float(op.get('salience',1))
                        except (TypeError,ValueError):
                            salience=1;warning='invalid_salience_defaulted'
                        if not 1<=salience<=3:
                            salience=1;warning='invalid_salience_defaulted'
                    vector=embedder.encode([op['text'].strip()])[0].astype(np.float32).tobytes()
                    conn.execute('INSERT INTO memories VALUES(?,?,?,?,?,?)',(mid,op['text'].strip(),salience,at,dream_id,vector))
                    conn.execute('INSERT INTO memory_versions VALUES(?,?,?,?,?,?,?)',(mid,1,op['text'].strip(),at,dream_id,index,'new' if kind=='new' else 'merge_child'))
                    if kind=='merge':
                        for parent in parents:
                            conn.execute('INSERT INTO memory_merges VALUES(?,?,?)',(mid,parent,dream_id))
                            conn.execute('UPDATE memories SET salience=salience*? WHERE id=?',(cfg.merge_parent_salience_factor,parent))
                            for row in conn.execute('SELECT turn_id FROM memory_sources WHERE memory_id=?',(parent,)).fetchall():
                                conn.execute('INSERT OR IGNORE INTO memory_sources VALUES(?,?,?)',(mid,row[0],dream_id))
                            for row in conn.execute('SELECT at FROM occurrences WHERE memory_id=?',(parent,)).fetchall():
                                conn.execute('INSERT OR IGNORE INTO occurrences VALUES(?,?,?)',(mid,row[0],dream_id))
                            for row in conn.execute('SELECT at,weight,kind FROM strength_events WHERE memory_id=?',(parent,)).fetchall():
                                conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',(mid,row[0],row[1],row[2],'inherit:'+parent))
                            for row in conn.execute('SELECT entity_id FROM memory_entities WHERE memory_id=?',(parent,)).fetchall():
                                conn.execute('INSERT OR IGNORE INTO memory_entities VALUES(?,?)',(mid,row[0]))
                            conn.execute("INSERT OR REPLACE INTO event_edges VALUES(?,?,'merge',1,0,?,'')",(mid,parent,at))
                    resolutions[index]=mid
                else:
                    mid=op['id']
                    if kind=='revise':
                        old=conn.execute('SELECT text,salience FROM memories WHERE id=?',(mid,)).fetchone()
                        version=conn.execute('SELECT COUNT(*) FROM memory_versions WHERE memory_id=?',(mid,)).fetchone()[0]+1
                        conn.execute('INSERT INTO memory_versions VALUES(?,?,?,?,?,?,?)',(mid,version,old['text'],at,dream_id,index,'revise'))
                        try:salience=float(op.get('salience',old['salience']))
                        except (TypeError,ValueError):
                            salience=1;warning='invalid_salience_defaulted'
                        if not 1<=salience<=3:
                            salience=1;warning='invalid_salience_defaulted'
                        vector=embedder.encode([op['text'].strip()])[0].astype(np.float32).tobytes()
                        conn.execute('UPDATE memories SET text=?,salience=?,embedding=? WHERE id=?',
                                     (op['text'].strip(),max(old['salience'],salience),vector,mid))
                _add_sources(conn,mid,sources,times,dream_id)
                conn.execute('INSERT OR IGNORE INTO strength_events VALUES(?,?,?,?,?)',(mid,at,cfg.w_write,kind,f'dream:{dream_id}:{index}'))
                if kind in ('new','revise','merge'):
                    eids,new,warnings=align(conn,op.get('entities',[]),at)
                    created.extend(new)
                    if warnings:warning=';'.join(filter(None,[warning,*warnings]))
                    for eid in eids:conn.execute('INSERT OR IGNORE INTO memory_entities VALUES(?,?)',(mid,eid))
            else:rejected+=1
            conn.execute('INSERT INTO dream_ops VALUES(?,?,?,?,?,?)',
                         (dream_id,index,json.dumps(op,ensure_ascii=False,sort_keys=True),'rejected' if reason else 'applied',reason or warning,mid))
        for index,op in enumerate(ops):
            if not isinstance(op,dict) or op.get('op')!='relate':continue
            def resolve(value):
                if isinstance(value,str) and value.startswith('new:'):
                    try:return resolutions.get(int(value[4:]))
                    except ValueError:return None
                return value if isinstance(value,str) and value in allowed else None
            a,b=resolve(op.get('a')),resolve(op.get('b'))
            reason=None if a and b and a!=b else 'invalid_relate_reference'
            if reason:rejected+=1
            else:
                at=cold_window[-1].time.isoformat()
                row=conn.execute("SELECT weight,last_reinforced FROM event_edges WHERE a=? AND b=? AND component='relate'",(a,b)).fetchone()
                weight=max(row['weight']*math.exp(-(cold_window[-1].time-datetime.fromisoformat(row['last_reinforced'])).total_seconds()/86400/cfg.event_decay_days),1) if row else 1
                conn.execute("INSERT OR REPLACE INTO event_edges VALUES(?,?,'relate',?,?,?,?)",(a,b,weight,int(bool(op.get('directed'))),at,str(op.get('note',''))))
            conn.execute('INSERT INTO dream_ops VALUES(?,?,?,?,?,?)',
                         (dream_id,index,json.dumps(op,ensure_ascii=False,sort_keys=True),'rejected' if reason else 'applied',reason,None))
        consume_traces(store,cfg,now,dream_id)
        for t in cold_window:conn.execute('UPDATE cold_turns SET integrated_by=? WHERE turn_id=?',(dream_id,t.id))
        conn.execute('UPDATE dream_runs SET n_rejected=? WHERE id=?',(rejected,dream_id))
        for edge in conn.execute('SELECT a,b,component,weight,last_reinforced FROM event_edges').fetchall():
            age=max(0,(now-datetime.fromisoformat(edge['last_reinforced'])).total_seconds()/86400)
            if edge['weight']*math.exp(-age/cfg.event_decay_days)<cfg.epsilon:
                conn.execute('DELETE FROM event_edges WHERE a=? AND b=? AND component=?',(edge['a'],edge['b'],edge['component']))
    return DreamResult(dream_id,'applied',result.cache_key,result.cache_hit,result.usage,len(ops),rejected,tuple(created))
