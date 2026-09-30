"""Four local transition layers and mass-conserving row mixing."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

import numpy as np

from .clock import logical_day


def ppmi(nij:int,ni:int,nj:int,n:int,kappa:float)->float:
    if not nij or not ni or not nj or not n:return 0.0
    return max(0.0,math.log(nij*n/(ni*nj)))*(1-math.exp(-nij/kappa))


def time_kernel(hours:float,sigma:float)->float:
    return math.exp(-hours/sigma) if hours<=3*sigma else 0.0


def _prune(rows:dict[int,dict[int,float]],cap:int):
    result={}
    for node,neighbors in rows.items():
        picked=sorted(neighbors.items(),key=lambda item:(-item[1],item[0]))[:cap]
        total=sum(weight for _,weight in picked)
        if total>0:result[node]={other:weight/total for other,weight in picked}
    return result


def build_layers(snapshot,now:datetime,cfg):
    memories=snapshot.memories; n=len(memories); entities=snapshot.entities
    ids={m['id']:i for i,m in enumerate(memories)}
    eids={e['id']:n+i for i,e in enumerate(entities)}
    layers={key:defaultdict(dict) for key in ('semantic','time','entity','event')}
    def add(layer,a,b,w):
        if w>0:layers[layer][a][b]=layers[layer][a].get(b,0)+w
    if cfg.semantic_layer and n>1:
        vectors=np.stack([m['embedding'] for m in memories])
        cos=vectors@vectors.T
        pairs={}
        for i in range(n):
            order=sorted((j for j in range(n) if j!=i and cos[i,j]>=cfg.c_sem),key=lambda j:(-float(cos[i,j]),j))[:cfg.k_sem]
            for j in order:
                pairs[tuple(sorted((i,j)))]=float(cos[i,j])
        for (i,j),weight in pairs.items():
            add('semantic',i,j,weight);add('semantic',j,i,weight)
    if cfg.time_layer and n>1:
        # A sorted occurrence sweep visits only pairs within 3 sigma.
        occ=sorted((at,i) for i,m in enumerate(memories) for at in m['occurrences'])
        pairs={}
        for p,(at,i) in enumerate(occ):
            j=p+1
            while j<len(occ) and (occ[j][0]-at).total_seconds()/3600<=3*cfg.sigma_time_hours:
                bt,other=occ[j]
                if i!=other:
                    key=tuple(sorted((i,other)))
                    gap=(bt-at).total_seconds()/3600
                    pairs[key]=min(pairs.get(key,float('inf')),gap)
                j+=1
        for (i,j),hours in pairs.items():
            w=time_kernel(hours,cfg.sigma_time_hours)
            add('time',i,j,w);add('time',j,i,w)
    if cfg.entity_layer:
        for i,m in enumerate(memories):
            for eid in m['entities']:
                if eid in eids:
                    j=eids[eid];add('entity',i,j,1);add('entity',j,i,1)
    if cfg.event_layer:
        for edge in snapshot.event_edges:
            if edge['component']=='relate' and not cfg.relate_edges:continue
            if edge['component']=='merge' and not cfg.merge_edges:continue
            if edge['component']=='corecall' and not cfg.corecall:continue
            if edge['a'] not in ids or edge['b'] not in ids:continue
            age=max(0,(now-datetime.fromisoformat(edge['last_reinforced'])).total_seconds()/86400)
            w=edge['weight']*math.exp(-age/cfg.event_decay_days)
            if w<cfg.epsilon:continue
            a,b=ids[edge['a']],ids[edge['b']]
            add('event',a,b,w)
            add('event',b,a,w*(cfg.rho if edge['directed'] else 1))
        if cfg.cooccur:
            days=[{logical_day(at) for at in m['occurrences']} for m in memories]
            all_days=set().union(*days) if days else set()
            N=len(all_days)
            for i in range(n):
                for j in range(i+1,n):
                    if (memories[i]['id'],memories[j]['id']) in snapshot.merges or (memories[j]['id'],memories[i]['id']) in snapshot.merges:continue
                    overlap=days[i]&days[j]
                    if not overlap:continue
                    w=ppmi(len(overlap),len(days[i]),len(days[j]),N,cfg.kappa)
                    last=max(overlap)
                    age=max(0,(logical_day(now)-last).days)
                    w*=math.exp(-age/cfg.event_decay_days)
                    if w>=cfg.epsilon:
                        add('event',i,j,w);add('event',j,i,w)
    enabled={'semantic':cfg.semantic_layer,'time':cfg.time_layer,'entity':cfg.entity_layer,'event':cfg.event_layer}
    pruned={key:_prune(rows,cfg.degree_cap) for key,rows in layers.items() if enabled[key]}
    return pruned


def transition(snapshot,now:datetime,cfg):
    n=len(snapshot.memories)+len(snapshot.entities)
    layers=build_layers(snapshot,now,cfg)
    weights={'semantic':cfg.w_sem,'time':cfg.w_time,'entity':cfg.w_ent,'event':cfg.w_event}
    P=np.zeros((n,n),dtype=np.float64)
    for i in range(n):
        present=[key for key,rows in layers.items() if i in rows and rows[i]]
        if not present:
            P[i,i]=1;continue
        total=sum(weights[key] for key in present)
        for key in present:
            for j,value in layers[key][i].items():P[i,j]+=weights[key]/total*value
    return P


def diffuse(start:np.ndarray,P:np.ndarray,lambda_:float,steps:int)->np.ndarray:
    if not np.isclose(start.sum(),1) and start.size:
        raise ValueError('seed mass must be one')
    state=start.copy()
    for _ in range(steps):
        state=(1-lambda_)*start+lambda_*(P.T@state)
    return state
