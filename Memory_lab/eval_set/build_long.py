#!/usr/bin/env python3
"""Insert eight weeks of independent filler sessions before the first probe."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime,timedelta
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    from .build import ROOT,RAW_WINDOW_DAYS,assign_ids_and_times,parse_script,simulate_hot
except ImportError:
    from build import ROOT,RAW_WINDOW_DAYS,assign_ids_and_times,parse_script,simulate_hot

SHIFT=timedelta(days=56)


def _at(value):return datetime.fromisoformat(value)


def build_long(name,weeks=8,version='v1'):
    if name not in ('dev_a','dev_b'):raise ValueError('only development sets are permitted')
    if weeks not in (8,12,16) or version not in ('v1','v2'):raise ValueError('unsupported long build')
    if version=='v1' and weeks!=8:raise ValueError('v1 supports eight weeks only')
    stem=f'{name}_long{weeks}w'+('_v2' if version=='v2' else '')
    shift=timedelta(days=weeks*7)
    source=ROOT/'built'
    dialogue=json.loads((source/f'{name}.dialogue.json').read_text(encoding='utf-8'))
    gold=json.loads((source/f'{name}.gold.json').read_text(encoding='utf-8'))
    earliest=min(_at(p['time']) for p in gold['probes'])
    before=[s for s in dialogue['sessions'] if _at(s['turns'][-1]['time'])<earliest]
    after=[s for s in dialogue['sessions'] if _at(s['turns'][-1]['time'])>=earliest]
    if not before or not after:raise ValueError('cannot locate insertion boundary')
    if _at(before[-1]['turns'][-1]['time'])>=earliest:raise AssertionError('bad boundary')
    _,filler=parse_script(ROOT/'fillers'/(f'{name}_{weeks}w_v2.txt' if version=='v2' else f'{name}_8w.txt'))
    errors=assign_ids_and_times(stem,filler)
    if errors:raise ValueError(errors)
    if not all(s['id'].startswith('f') for s in filler):raise ValueError('filler session ids must start with f')
    first_day=(_at(before[-1]['turns'][-1]['time'])+timedelta(days=1)).date()
    week_counts={i:0 for i in range(weeks)}
    for s in filler:
        days=(s['start'].date()-first_day).days
        if not 0<=days<weeks*7:raise ValueError(f'filler outside {weeks*7} days: {s["id"]}')
        week_counts[days//7]+=1
        if not 10<=len(s['turns'])<=24 or len(s['turns'])%2:raise ValueError(f'bad turn count: {s["id"]}')
    if any(not 3<=count<=4 for count in week_counts.values()):raise ValueError(f'weekly session count: {week_counts}')
    filler_sessions=[{'id':s['id'],'start':s['start'].isoformat(),
                      'turns':[{'id':t['id'],'role':t['role'],'time':t['time'].isoformat(),'text':t['text']}
                               for t in s['turns']]} for s in filler]
    shifted=copy.deepcopy(after)
    for s in shifted:
        s['start']=(_at(s['start'])+shift).isoformat()
        for t in s['turns']:t['time']=(_at(t['time'])+shift).isoformat()
    sessions=copy.deepcopy(before)+filler_sessions+shifted
    all_turns=[t for s in sessions for t in s['turns']]
    ids=[t['id'] for t in all_turns]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate turn ids')
    by_id={t['id']:t for t in all_turns}
    dated_turns=[{**t,'time':_at(t['time'])} for t in all_turns]
    if any(_at(a['time'])>=_at(b['time']) for a,b in zip(all_turns,all_turns[1:])):
        raise ValueError('turn chronology invalid')
    out_gold=copy.deepcopy(gold)
    excluded=[]
    for p in out_gold['probes']:
        p['time']=(_at(p['time'])+shift).isoformat()
        when=_at(p['time'])
        for variant in p.get('gap_variants',[]):variant['time']=(_at(variant['time'])+shift).isoformat()
        hot=simulate_hot(dated_turns,when)
        groups=p['must_surface']
        reasons=[]
        if groups and all(set(group)&hot for group in groups):reasons.append('gold_all_in_hot')
        if p['category']=='时间':
            from Conversation_Memory.engine.timeparse import parse
            spans=parse(p['message'],when)
            if not spans or any(not any(s in by_id and any(span.contains(_at(by_id[s]['time'])) for span in spans)
                                    for s in group) for group in groups):
                reasons.append('time_interval_misses_gold')
        p['not_scored']=bool(reasons)
        p['not_scored_reasons']=reasons
        p['diagnostics']={'hot_visible_gold':sorted({i for group in groups for i in group}&hot),
                          'raw_window_gold':sorted({i for group in groups for i in group
                                                   if i in by_id and i not in hot and
                                                   _at(by_id[i]['time'])>=when-timedelta(days=RAW_WINDOW_DAYS)})}
        if reasons:excluded.append({'id':p['id'],'reasons':reasons})
    out_dialogue={**dialogue,'id':stem,'sessions':sessions}
    out_gold['id']=stem
    source.mkdir(exist_ok=True)
    (source/f'{stem}.dialogue.json').write_text(json.dumps(out_dialogue,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    (source/f'{stem}.gold.json').write_text(json.dumps(out_gold,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    return {'set':stem,'sessions':len(sessions),'turns':len(all_turns),'excluded':excluded,'weeks':week_counts}


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('names',nargs='*',default=['dev_a','dev_b'])
    for name in ap.parse_args().names:print(json.dumps(build_long(name),ensure_ascii=False))
