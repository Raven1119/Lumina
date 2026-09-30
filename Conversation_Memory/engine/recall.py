"""Read-only local recall: three seed channels, four layers, four outputs."""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np

from .clock import WEEKDAYS,period,logical_start,logical_day
from .entities import matches
from .layers import transition,diffuse,build_layers,ppmi
from .rawwindow import search_raw
from .render import memory_time_label
from .strength import strengths
from .timeparse import parse,memory_intervals
from .types import RecallResult,RecalledMemory


def _unit(vector):
    size=float(np.linalg.norm(vector))
    return vector/max(size,1e-12)


def _distance_hours(at,interval):
    if interval.start<=at<interval.end:return 0.0
    if at<interval.start:return (interval.start-at).total_seconds()/3600
    return (at-interval.end).total_seconds()/3600


def event_components(snapshot,now,cfg,i,j):
    a,b=snapshot.memories[i],snapshot.memories[j]
    scores={}
    for edge in snapshot.event_edges:
        if {edge['a'],edge['b']}!={a['id'],b['id']}:continue
        component=edge['component']
        if component in ('relate','merge','corecall'):
            if component=='relate' and not cfg.relate_edges:continue
            if component=='merge' and not cfg.merge_edges:continue
            if component=='corecall' and not cfg.corecall:continue
            from datetime import datetime
            age=max(0,(now-datetime.fromisoformat(edge['last_reinforced'])).total_seconds()/86400)
            scores[component]=max(scores.get(component,0),edge['weight']*math.exp(-age/cfg.event_decay_days))
    if cfg.cooccur and (a['id'],b['id']) not in snapshot.merges and (b['id'],a['id']) not in snapshot.merges:
        from .clock import logical_day
        days=[{logical_day(t) for t in m['occurrences']} for m in snapshot.memories]
        overlap=days[i]&days[j]
        if overlap:
            union=set().union(*days)
            weight=ppmi(len(overlap),len(days[i]),len(days[j]),len(union),cfg.kappa)
            weight*=math.exp(-max(0,(logical_day(now)-max(overlap)).days)/cfg.event_decay_days)
            scores['cooccur']=weight
    return scores


def _event_reason(snapshot,now,cfg,i,j):
    scores=event_components(snapshot,now,cfg,i,j)
    if not scores:return ''
    a=snapshot.memories[i]
    component=max(scores,key=lambda key:(scores[key],key))
    snippet=a['text'][:12]
    return {'cooccur':f'常和「{snippet}」在同一天',
            'relate':f'和「{snippet}」有关',
            'merge':f'和「{snippet}」是同一类事',
            'corecall':f'和「{snippet}」有关'}[component]


def _reason(snapshot,now,cfg,a0,j,layers):
    if a0[j]>0:return ''
    weights={'semantic':cfg.w_sem,'time':cfg.w_time,'entity':cfg.w_ent,'event':cfg.w_event}
    choices=[]
    for layer,rows in layers.items():
        for i in np.flatnonzero(a0):
            p=rows.get(int(i),{}).get(j,0)
            if p:choices.append((float(a0[i])*weights[layer]*p,layer,int(i)))
    if not choices:return ''
    _,layer,i=max(choices,key=lambda row:(row[0],row[1],-row[2]))
    if layer=='semantic':return ''
    if layer=='entity':
        n=len(snapshot.memories)
        return f'都和{snapshot.entities[i-n]["name"]}有关' if i>=n else ''
    snippet=snapshot.memories[i]['text'][:12]
    if layer=='time':return f'和「{snippet}」在同一段时间'
    return _event_reason(snapshot,now,cfg,i,j)


def background_activation(snapshot,now,cfg,P,dormant):
    """Structural activation from a uniform start over awake memories."""
    key=(snapshot.version,logical_day(now))
    cached=snapshot.background_cache.get(key)
    if cached is not None:return cached
    n=len(snapshot.memories)
    start=np.zeros(P.shape[0],dtype=np.float64)
    awake=np.flatnonzero(~dormant)
    if len(awake):start[awake]=1/len(awake)
    value=diffuse(start,P,cfg.lambda_,cfg.steps) if len(awake) else start
    snapshot.background_cache[key]=value
    return value


def recall(snapshot,cue,now,cfg)->RecallResult:
    memories=snapshot.memories;n=len(memories);entities=snapshot.entities
    intervals=parse(cue.message,now)
    if n==0 and not cfg.raw_enabled:
        return RecallResult(diagnostics={'version':snapshot.version,'time_intervals':[(i.start.isoformat(),i.end.isoformat()) for i in intervals]})
    embedder=snapshot.embedder
    if embedder is None:raise ValueError('snapshot needs embedder for recall')
    recent=cue.recent[-cfg.cue_recent_turns:]
    time_phrase=f"周{WEEKDAYS[now.weekday()]}{period(now)}"
    pieces=[cue.message]
    if recent:pieces.append('\n'.join(t.text for t in recent))
    pieces.append(time_phrase)
    encoded=embedder.encode(pieces)
    q=encoded[0].copy()
    idx=1
    if recent:q+=cfg.cue_recent_weight*encoded[idx];idx+=1
    q+=cfg.cue_time_weight*encoded[idx]
    q=_unit(q)
    a0=np.zeros(n+len(entities),dtype=np.float64)
    channels={};cos=np.zeros(n,dtype=np.float32)
    if n:
        matrix=np.stack([m['embedding'] for m in memories])
        cos=matrix@q
        state=strengths(memories,now,cfg)
        B=np.asarray([float(x[0]) for x in state]);pi=np.asarray([float(x[1]) for x in state]);dormant=np.asarray([bool(x[2]) for x in state])
    else:
        B=pi=dormant=np.zeros(0)
    if cfg.semantic_channel and n:
        order=sorted((i for i in range(n) if not dormant[i]),key=lambda i:(-float(cos[i]),memories[i]['id']))[:cfg.semantic_seeds]
        if order:
            values=np.exp((cos[order]-np.max(cos[order]))/cfg.semantic_temperature)
            vec=np.zeros_like(a0);vec[order]=values/values.sum();channels['semantic']=vec
    if cfg.time_channel and n:
        valid=memory_intervals(intervals,now)
        vec=np.zeros_like(a0)
        for i,m in enumerate(memories):
            if dormant[i] or not m['occurrences']:continue
            dist=min((_distance_hours(at,span) for at in m['occurrences'] for span in valid),default=float('inf'))
            value=math.exp(-dist/cfg.time_seed_decay_hours) if math.isfinite(dist) else 0
            if value>=.05:vec[i]=value
        if vec.sum()>0:channels['time']=vec/vec.sum()
    if cfg.entity_channel and entities:
        current=matches(cue.message,list(entities))
        old=matches('\n'.join(t.text for t in recent),list(entities)) if recent else []
        eids={e['id']:n+i for i,e in enumerate(entities)}
        vec=np.zeros_like(a0)
        for eid in current:vec[eids[eid]]=1
        for eid in old:vec[eids[eid]]=max(vec[eids[eid]],cfg.entity_recent_weight)
        if vec.sum()>0:channels['entity']=vec/vec.sum()
    weights={'semantic':cfg.alpha_S,'time':cfg.alpha_T,'entity':cfg.alpha_E}
    if channels:
        total=sum(weights[key] for key in channels)
        for key,vec in channels.items():a0+=weights[key]/total*vec
    P=None
    if cfg.diffusion and len(a0):
        P=transition(snapshot,now,cfg)
        act=diffuse(a0,P,cfg.lambda_,cfg.steps) if a0.sum()>0 else a0.copy()
    else:act=a0.copy()
    scores=act[:n]*pi
    selected=[]
    reason_layers=build_layers(snapshot,now,cfg) if cfg.render_version in ('v2','v3','v4') and cfg.diffusion else {}
    prelast=(diffuse(a0,P,cfg.lambda_,cfg.steps-1) if cfg.render_version=='v4' and P is not None
             and a0.sum()>0 and cfg.steps>0 else a0)
    pattern_set=({m['id'] for m in memories if any(v['kind']=='pattern' for v in m['lineage'])}
                 if cfg.render_version=='v4' else set())
    def similar(i):
        return any(float(memories[i]['embedding']@memories[j]['embedding'])>=cfg.dup_cos for j in selected)
    def item(i,part):
        reason=_reason(snapshot,now,cfg,a0,i,reason_layers) if reason_layers else ''
        if cfg.render_version=='v4' and part=='remote' and memories[i]['id'] not in pattern_set and P is not None:
            contribution=prelast*P[:,i]
            if contribution.size and contribution.max()>0:
                source=min((j for j in range(len(contribution)) if contribution[j]==contribution.max()),
                           key=lambda j:(memories[j]['id'][0],int(memories[j]['id'][1:])) if j<n
                           else (entities[j-n]['id'][0],int(entities[j-n]['id'][1:])))
                if source<n and memories[source]['id'] in pattern_set:
                    reason='因为'+memories[source]['text'][:40]
        return RecalledMemory(memories[i]['id'],memories[i]['text'],float(scores[i]),float(pi[i]),
                              float(B[i]),(part,),memories[i]['sources'],memory_time_label(memories[i],now),
                              reason)
    near=[]
    excluded_near = ({m['id'] for m in memories if any(v['kind']=='pattern' for v in m['lineage'])}
                     if cfg.near_pattern_exclude else set())
    for i in sorted(range(n),key=lambda i:(-float(scores[i]),memories[i]['id'])):
        if len(near)>=cfg.near_cap:break
        if memories[i]['id'] in excluded_near or scores[i]<=0 or pi[i]<cfg.pi_recall or similar(i):continue
        near.append(item(i,'near'));selected.append(i)
    remote=[]
    if cfg.remote_slot and near:
        if cfg.assoc_rule=='lift':
            baseline=background_activation(snapshot,now,cfg,P,dormant)[:n]
            epsilon=max(float(np.median(baseline[~dormant])),1e-12) if np.any(~dormant) else 1e-12
            options=[i for i in range(n) if a0[i]==0 and not dormant[i] and
                     pi[i]>=cfg.assoc_pi_min and scores[i]>=cfg.assoc_abs_ratio*near[-1].score
                     and i not in selected and not similar(i)]
            if options:
                i=min(options,key=lambda i:(-float((act[i]+epsilon)/(baseline[i]+epsilon)),
                                            -float(scores[i]),memories[i]['id']))
                remote=[item(i,'remote')];selected.append(i)
        elif cfg.assoc_rule=='score':
            options=[i for i in range(n) if a0[i]==0 and not dormant[i] and scores[i]>0
                     and pi[i]>=cfg.pi_recall and i not in selected and not similar(i)]
            if options:
                i=min(options,key=lambda i:(-float(scores[i]),memories[i]['id']))
                if scores[i]>=cfg.remote_ratio*near[-1].score:remote=[item(i,'remote')];selected.append(i)
        else:raise ValueError('unknown association rule')
    core=[]
    if cfg.salience_core:
        for i in sorted(range(n),key=lambda i:(-float(B[i]),memories[i]['id'])):
            if len(core)>=cfg.core_cap:break
            if dormant[i] or pi[i]<cfg.pi_recall or i in selected or similar(i):continue
            core.append(item(i,'core'));selected.append(i)
    raw=search_raw(snapshot,cue,now,q,intervals,cfg) if cfg.raw_enabled else ()
    seeds={key:[{'id':(memories[i]['id'] if i<n else entities[i-n]['id']),'weight':round(float(vec[i]),6)}
                for i in sorted(np.flatnonzero(vec),key=lambda i:-float(vec[i]))[:5]] for key,vec in channels.items()}
    diagnostics={'version':snapshot.version,'time_intervals':[(span.start.isoformat(),span.end.isoformat()) for span in intervals],
                 'seeds':seeds,'activation_total':round(float(act.sum()),12),
                 'top_scores':[{'id':memories[i]['id'],'score':round(float(scores[i]),6),'activation':round(float(act[i]),6)}
                               for i in sorted(range(n),key=lambda i:(-float(scores[i]),memories[i]['id']))[:20]]}
    return RecallResult(tuple(near),tuple(remote),tuple(core),raw,diagnostics)
