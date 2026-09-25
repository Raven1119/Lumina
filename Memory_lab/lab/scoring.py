"""Deterministic source-based memory-layer scoring and paired intervals."""
from __future__ import annotations

import random


def _parse_id(value):
    try:
        session,turn=value.rsplit('-t',1)
        return session,int(turn)
    except (ValueError,TypeError):return value,-1000


def intersects(sources,group,loose=False):
    if set(sources)&set(group):return True
    if not loose:return False
    return any(sa==ga and abs(si-gi)<=2 for source in sources for gold in group
               for sa,si in [_parse_id(source)] for ga,gi in [_parse_id(gold)])


def score_probe(result,probe,variant=False):
    groups=probe.get('must_surface',[])
    distractors=set(probe.get('must_not_surface',[]))
    parts=[('near',list(result.near)),('remote',list(result.remote)),
           ('core',list(result.core)),('raw',list(result.raw))]
    memory_items=[m for _,items in parts[:3] for m in items]
    source_mem={s for m in memory_items for s in m.sources}
    source_raw={m.turn_id for m in result.raw}
    detail={}
    for label,sources in [('all',source_mem|source_raw),('memory',source_mem)]:
        strict=[intersects(sources,g) for g in groups]
        loose=[intersects(sources,g,True) for g in groups]
        detail[label]={'groups_strict':strict,'groups_loose':loose,
                       'strict_rate':sum(strict)/len(strict) if strict else 1.0,
                       'loose_rate':sum(loose)/len(loose) if loose else 1.0,
                       'complete_strict':all(strict),'complete_loose':all(loose)}
    only_distractor=sum(bool(set(m.sources)&distractors) and not any(intersects(m.sources,g) for g in groups)
                        for m in memory_items)
    raw_distractor=sum(m.turn_id in distractors and not any(m.turn_id in g for g in groups) for m in result.raw)
    attribution=[];generalized=0
    for group in groups:
        via=next((label for label,items in parts if any(intersects(m.sources if label!='raw' else [m.turn_id],group)
                                                     for m in items)),None)
        attribution.append(via)
        hits=[m for m in memory_items if intersects(m.sources,group)]
        if len(hits)==1 and len({_parse_id(s)[0] for s in hits[0].sources})>3:generalized+=1
    forgetting_pass=None
    if probe['category']=='淡忘' and variant:
        forgetting_pass=only_distractor==0 and raw_distractor==0
    return {'all':detail['all'],'memory':detail['memory'],
            'only_distractor':only_distractor,'raw_distractor':raw_distractor,
            'attribution':attribution,'generalized_hits':generalized,
            'forgetting_pass':forgetting_pass,'soft':bool(probe.get('soft'))}


def bootstrap(values,seed=0,n=2000):
    if not values:return [0.0,0.0]
    rng=random.Random(seed)
    draws=[]
    for _ in range(n):draws.append(sum(values[rng.randrange(len(values))] for _ in values)/len(values))
    draws.sort()
    return [draws[int(.025*n)],draws[min(n-1,int(.975*n))]]


def paired_delta(a,b,seed=0):
    if len(a)!=len(b):raise ValueError('paired probes must align')
    return {'delta':sum(y-x for x,y in zip(a,b))/len(a) if a else 0.0,
            'ci':bootstrap([y-x for x,y in zip(a,b)],seed)}


def summarize(records):
    by={}
    for row in records:
        target=60 if row['category'] in ('淡忘','保留') else 0
        if row.get('variant_days',0)!=target:continue
        if row.get('not_scored'):continue
        by.setdefault(row['category'],[]).append(row)
    out={}
    for category,rows in sorted(by.items()):
        def avg(path):return sum(r['score'][path[0]][path[1]] for r in rows)/len(rows)
        if category=='淡忘':
            vals=[float(r['score']['forgetting_pass']) for r in rows]
            mem=[float(r['score']['only_distractor']==0) for r in rows]
        else:
            vals=[float(r['score']['all']['complete_strict']) for r in rows]
            mem=[float(r['score']['memory']['complete_strict']) for r in rows]
        out[category]={'n':len(rows),'all_strict_group':avg(('all','strict_rate')),
                       'all_loose_group':avg(('all','loose_rate')),
                       'all_complete':sum(vals)/len(vals),'all_complete_ci':bootstrap(vals),
                       'memory_strict_group':avg(('memory','strict_rate')),
                       'memory_loose_group':avg(('memory','loose_rate')),
                       'memory_complete':sum(mem)/len(mem),'memory_complete_ci':bootstrap(mem),
                       'only_distractor':sum(r['score']['only_distractor'] for r in rows),
                       'raw_distractor':sum(r['score']['raw_distractor'] for r in rows),
                       'generalized_hits':sum(r['score']['generalized_hits'] for r in rows),
                       'primary_variant_days':60 if category in ('淡忘','保留') else 0}
    return out
