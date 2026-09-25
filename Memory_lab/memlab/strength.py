"""ACT-R base strength, accessibility, and derived dormancy."""
from __future__ import annotations

import math
from datetime import datetime,timedelta

import numpy as np

from .config import Config


def strength(events:list[tuple[datetime,float]],salience:float,now:datetime,cfg:Config)->tuple[float,float,bool]:
    valid=[(at,w) for at,w in events if at<=now]
    if not valid:return -math.inf,0.0,True
    def base(at):
        total=sum(w*max((at-time).total_seconds()/86400,cfg.min_interval_hours/24)**(-cfg.d)
                  for time,w in valid if time<=at)
        return math.log(total)+math.log(max(salience,1e-12)) if total>0 else -math.inf
    B=base(now)
    pi=1/(1+math.exp(-max(-700,min(700,(B-cfg.tau)/cfg.theta))))
    cutoff=now-timedelta(days=cfg.dormant_days)
    low_at_cutoff=base(cutoff)
    pi_old=1/(1+math.exp(-max(-700,min(700,(low_at_cutoff-cfg.tau)/cfg.theta)))) if math.isfinite(low_at_cutoff) else 0
    dormant=pi_old<cfg.pi_min and not any(at>cutoff for at,_ in valid)
    return B,pi,dormant


def strengths(memories:tuple[dict,...],now:datetime,cfg:Config):
    """Vectorized event sums for the entire immutable snapshot."""
    n=len(memories)
    if n==0:return np.empty((0,3),dtype=object)
    width=max(1,max(len(m['events']) for m in memories))
    seconds=np.full((n,width),np.inf,dtype=np.float64)
    weights=np.zeros((n,width),dtype=np.float64)
    salience=np.asarray([max(m['salience'],1e-12) for m in memories],dtype=np.float64)
    for i,m in enumerate(memories):
        for j,(at,weight) in enumerate(m['events']):
            seconds[i,j]=at.timestamp()
            weights[i,j]=weight
    now_sec=now.timestamp()
    cutoff_sec=(now-timedelta(days=cfg.dormant_days)).timestamp()
    min_days=cfg.min_interval_hours/24
    def activation(at_sec):
        valid=(seconds<=at_sec)&(weights>0)
        ages=np.maximum((at_sec-seconds)/86400,min_days)
        terms=np.where(valid,weights*np.power(ages,-cfg.d),0.0)
        total=terms.sum(axis=1)
        return np.where(total>0,np.log(np.maximum(total,1e-300))+np.log(salience),-np.inf)
    B=activation(now_sec)
    old=activation(cutoff_sec)
    pi=1/(1+np.exp(-np.clip((B-cfg.tau)/cfg.theta,-700,700)))
    old_pi=1/(1+np.exp(-np.clip((old-cfg.tau)/cfg.theta,-700,700)))
    recent=((seconds>cutoff_sec)&(seconds<=now_sec)&(weights>0)).any(axis=1)
    dormant=(old_pi<cfg.pi_min)&~recent
    return np.column_stack((B,pi,dormant)).astype(object)
