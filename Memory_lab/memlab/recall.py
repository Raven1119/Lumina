"""Read-only local recall: three seed channels, four layers, four outputs."""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np

from .clock import WEEKDAYS,period,logical_start
from .entities import matches
from .layers import transition,diffuse
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
    if cfg.diffusion and len(a0):
        P=transition(snapshot,now,cfg)
        act=diffuse(a0,P,cfg.lambda_,cfg.steps) if a0.sum()>0 else a0.copy()
    else:act=a0.copy()
    scores=act[:n]*pi
    selected=[]
    def similar(i):
        return any(float(memories[i]['embedding']@memories[j]['embedding'])>=cfg.dup_cos for j in selected)
    def item(i,part):
        return RecalledMemory(memories[i]['id'],memories[i]['text'],float(scores[i]),float(pi[i]),
                              float(B[i]),(part,),memories[i]['sources'],memory_time_label(memories[i],now))
    near=[]
    for i in sorted(range(n),key=lambda i:(-float(scores[i]),memories[i]['id'])):
        if len(near)>=cfg.near_cap:break
        if scores[i]<=0 or similar(i):continue
        near.append(item(i,'near'));selected.append(i)
    remote=[]
    if cfg.remote_slot and near:
        options=[i for i in range(n) if a0[i]==0 and not dormant[i] and scores[i]>0 and i not in selected and not similar(i)]
        if options:
            i=min(options,key=lambda i:(-float(scores[i]),memories[i]['id']))
            if scores[i]>=cfg.remote_ratio*near[-1].score:remote=[item(i,'remote')];selected.append(i)
    core=[]
    if cfg.salience_core:
        for i in sorted(range(n),key=lambda i:(-float(B[i]),memories[i]['id'])):
            if len(core)>=cfg.core_cap:break
            if dormant[i] or i in selected or similar(i):continue
            core.append(item(i,'core'));selected.append(i)
    raw=search_raw(snapshot,cue,now,q,intervals,cfg) if cfg.raw_enabled else ()
    seeds={key:[{'id':(memories[i]['id'] if i<n else entities[i-n]['id']),'weight':round(float(vec[i]),6)}
                for i in sorted(np.flatnonzero(vec),key=lambda i:-float(vec[i]))[:5]] for key,vec in channels.items()}
    diagnostics={'version':snapshot.version,'time_intervals':[(span.start.isoformat(),span.end.isoformat()) for span in intervals],
                 'seeds':seeds,'activation_total':round(float(act.sum()),12),
                 'top_scores':[{'id':memories[i]['id'],'score':round(float(scores[i]),6),'activation':round(float(act[i]),6)}
                               for i in sorted(range(n),key=lambda i:(-float(scores[i]),memories[i]['id']))[:20]]}
    return RecallResult(tuple(near),tuple(remote),tuple(core),raw,diagnostics)
