"""Bounded Cold original-turn search inside the 14-day window."""
from __future__ import annotations

from datetime import timedelta

import numpy as np

from .embed import lexical_features
from .timeparse import clipped_intervals
from .types import RawHit

STOP={"我们","今天","什么","一下","这个","那个","就是","还是","没有","可以","然后","感觉"}


def search_raw(snapshot,cue,now,q,intervals,cfg):
    cutoff=now-timedelta(days=cfg.raw_window_days)
    turns=[t for t in snapshot.cold if cutoff<=t.time<now and t.id not in cue.hot_ids]
    if not turns or snapshot.embedder is None:return ()
    vectors=snapshot.embedder.encode([t.text for t in turns])
    cos=vectors@q
    terms=lexical_features(cue.message)-STOP
    spans=[s for s in clipped_intervals(intervals,now) if s.end>cutoff]
    scores=[]
    for i,t in enumerate(turns):
        overlap=len(terms&(lexical_features(t.text)-STOP))/len(terms) if terms else 0
        scores.append(0.5*float(cos[i])+0.5*overlap)
    interval_ids=set()
    for span in spans:
        choices=[i for i,t in enumerate(turns) if span.contains(t.time)]
        choices.sort(key=lambda i:(-scores[i],turns[i].id))
        interval_ids.update(choices[:cfg.raw_interval_cap])
    content=sorted(range(len(turns)),key=lambda i:(-scores[i],turns[i].id))[:cfg.raw_topk]
    chosen=sorted(interval_ids|set(content),key=lambda i:(turns[i].time,turns[i].id))
    return tuple(RawHit(turns[i].id,turns[i].time,turns[i].role,turns[i].text,
                        float(scores[i]),'interval' if i in interval_ids else 'content') for i in chosen)
