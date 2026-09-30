"""P9 induction candidates from implicit utterance clauses."""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np


ORDER=('sameday','recur')


def select_trace_groups(rows, current_turns, cfg, pattern_sources=(), *, limit=True,
                        skip_patterns=True):
    """Use clauses to find classes, but use whole utterances as group members."""
    empty={kind:0 for kind in ORDER}
    if not rows or not current_turns:
        return [],empty
    vectors=np.stack([r['embedding'] for r in rows])
    cosine=vectors @ vectors.T
    days={r['day'] for r in rows}
    anchors=[i for i,r in enumerate(rows) if r['turn_id'] in current_turns]
    sources=[set(x) for x in pattern_sources]
    candidates=[]
    kd={i:{rows[j]['day'] for j in range(len(rows))
           if float(cosine[i,j])>=cfg.pattern_trace_kind_cos} for i in anchors}

    def best(anchor,day,used):
        choices=(j for j,r in enumerate(rows)
                 if r['day']==day and r['turn_id'] not in used and
                 float(cosine[anchor,j])>=cfg.pattern_trace_kind_cos)
        return min(choices,key=lambda j:(-float(cosine[anchor,j]),rows[j]['at'],
                                         rows[j]['turn_id'],rows[j]['index']),default=None)

    def add(kind,indices,strength,cue,cue_days,rank):
        chosen={}
        for i in indices:
            chosen.setdefault(rows[i]['turn_id'],rows[i])
        ids=tuple(sorted(chosen))
        if len(ids)<2 or len({r['day'] for r in chosen.values()})<2:
            return
        members=set(ids)
        if skip_patterns and any(members<=known for known in sources):
            return
        reinforced=bool(skip_patterns and any((members-set(current_turns))<=known for known in sources))
        evidence=[{'turn_id':r['turn_id'],'text':r['text'],'original':r['original'],
                   'at':r['at'].isoformat(),'day':r['day'].isoformat()}
                  for r in sorted(chosen.values(),key=lambda r:(r['at'],r['turn_id']))]
        candidates.append({'kind':kind,'origin':'trace','ids':ids,
                           'strength':float(strength),'cue':cue,
                           'days':sorted(d.isoformat() for d in cue_days),
                           'member_days':{r['turn_id']:r['day'].isoformat() for r in chosen.values()},
                           'evidence':evidence,'rank':tuple(rank),'reinforced':reinforced})

    for i in anchors:
        found=kd[i]
        if len(found)<cfg.pattern_trace_min_days:
            continue
        chosen=[];used=set()
        for day in sorted(found,reverse=True):
            j=best(i,day,used)
            if j is None:continue
            chosen.append(j);used.add(rows[j]['turn_id'])
            if len(chosen)>=cfg.pattern_recur_max_members:break
        score=sum(float(cosine[i,j]) for j in chosen)/len(chosen) if chosen else 0.
        add('recur',chosen,score,
            f'「{rows[i]["text"][:24]}」这类事反复出现，共 {len(found)} 天',
            found,(len(found),score))

    for pos,i in enumerate(anchors):
        for j in anchors[pos+1:]:
            if rows[i]['day']!=rows[j]['day'] or float(cosine[i,j])>=cfg.pattern_trace_kind_cos:
                continue
            common=kd[i]&kd[j]
            if len(common)<cfg.pattern_trace_min_days:
                continue
            weight=(max(0.,math.log(len(common)*len(days)/(len(kd[i])*len(kd[j])))) *
                    (1-math.exp(-len(common)/cfg.kappa)))
            if weight<cfg.pattern_cooccur_min:
                continue
            chosen=[];used=set()
            for day in sorted(common,reverse=True)[:cfg.pattern_sameday_max_days]:
                for anchor in (i,j):
                    k=best(anchor,day,used)
                    if k is None:continue
                    if len(chosen)>=cfg.pattern_sameday_max_members:break
                    chosen.append(k);used.add(rows[k]['turn_id'])
                if len(chosen)>=cfg.pattern_sameday_max_members:break
            cue=(f'「{rows[i]["text"][:24]}」和「{rows[j]["text"][:24]}」'
                 f'这两类事常在同一天出现，共 {len(common)} 天')
            add('sameday',chosen,weight,cue,common,(len(common),weight))

    priority={kind:n for n,kind in enumerate(ORDER)}
    candidates.sort(key=lambda r:(priority[r['kind']],-len(r['ids']),
                                  -r['strength'],r['ids'][0]))
    exact={}
    for row in candidates:
        exact.setdefault(row['ids'],row)
    kept=[]
    for kind in ORDER:
        within=[]
        for row in sorted((r for r in exact.values() if r['kind']==kind),
                          key=lambda r:(-len(r['ids']),-r['strength'],r['ids'][0])):
            members=set(row['ids'])
            if any(members<=set(other['ids']) or
                   len(members&set(other['ids']))/len(members|set(other['ids']))>=.5
                   for other in within):
                continue
            within.append(row)
        within.sort(key=lambda r:(r['reinforced'],*[-x for x in r['rank']],r['ids'][0]))
        kept.extend(within)
    counts={kind:sum(row['kind']==kind for row in kept) for kind in ORDER}
    if limit:
        selected=[];by_kind=defaultdict(int)
        for row in kept:
            if by_kind[row['kind']]>=cfg.pattern_max_per_type or len(selected)>=cfg.pattern_max_groups:
                continue
            selected.append(row);by_kind[row['kind']]+=1
        kept=selected
    return kept,counts
