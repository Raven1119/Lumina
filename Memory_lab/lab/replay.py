"""Deterministic simulated-time event stream for a development set."""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime,timedelta
from pathlib import Path

from eval_set.build import build_one,simulate_hot
from memlab.clock import SimClock
from memlab.config import preset
from memlab.integrate import run_dream
from memlab.recall import recall
from memlab.render import render_memory_block
from memlab.store import Store
from memlab.types import Cue,Turn

from .hotcold import HotCold
from .answer import update_summary,generate_answer
from .report import write_run,timing_summary
from .scoring import score_probe,summarize
from .measure import measure_probe,summarize_measure

ROOT=Path(__file__).resolve().parents[1]


def load_set(name):
    built=ROOT/'eval_set'/'built'
    script=ROOT/'eval_set'/'scripts'/f'{name}.txt'
    gold_source=ROOT/'eval_set'/'gold'/f'{name}.json'
    a=built/f'{name}.dialogue.json';b=built/f'{name}.gold.json'
    if not a.is_file() or not b.is_file() or min(a.stat().st_mtime,b.stat().st_mtime)<max(script.stat().st_mtime,gold_source.stat().st_mtime):
        errors,_,_=build_one(name)
        if errors:raise ValueError(f'eval build failed for {name}: {errors}')
    dialogue=json.loads(a.read_text(encoding='utf-8'))
    gold=json.loads(b.read_text(encoding='utf-8'))
    return dialogue,gold


def _serialize_result(result):
    return {'near':[asdict(m) for m in result.near],
            'remote':[asdict(m) for m in result.remote],
            'core':[asdict(m) for m in result.core],
            'raw':[asdict(m) for m in result.raw],
            'diagnostics':result.diagnostics}


def run_set(name,preset_name,llm,embedder,out:Path,*,answer=False,
            gap_sweep=(0,1,7,14,30,60,120),time_shift_days=0,time_scale=1.0,
            dream_retry_failed=0,allow_holdout=False,summary_enabled=None):
    if name=='holdout_c' and not allow_holdout:raise ValueError('holdout requires --allow-holdout')
    dialogue,gold=load_set(name)
    if dialogue.get('split')=='holdout' and not allow_holdout:raise ValueError('holdout requires --allow-holdout')
    cfg=preset(preset_name,'hash' if embedder.identity['model'].startswith('hash') else
               'minilm' if 'MiniLM' in embedder.identity['model'] else 'bge-m3')
    if answer and preset_name not in ('B1','P6'):
        raise ValueError('answer comparison is limited to B1 and P6')
    raw_turns=[Turn(t['id'],session['id'],t['role'],datetime.fromisoformat(t['time']),t['text'])
               for session in dialogue['sessions'] for t in session['turns']]
    t0=raw_turns[0].time
    def shift(at):return t0+(at-t0)*time_scale+timedelta(days=time_shift_days)
    turns=[Turn(t.id,t.session_id,t.role,shift(t.time),t.text) for t in raw_turns]
    turn_by_id={t.id:t for t in turns}
    probe_events=[]
    for p in gold['probes']:
        probe_events.append((shift(datetime.fromisoformat(p['time'])),0,p))
    events=[(t.time,1,t) for t in turns]+probe_events
    events.sort(key=lambda row:(row[0],row[1]))
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    store=Store(out/'memory.sqlite');store.set_embedder_identity(embedder.identity)
    clock=SimClock(turns[0].time);hot=HotCold();dream_log=[];records=[];curve=[];times=[]
    first_dream=False;probe_version_checks=0
    changed_time=time_shift_days!=0 or time_scale!=1
    if changed_time:
        expected=(len(turns)//40)
        print(f'预计 Dream 新调用上限约 {expected} 次（时间变换改变提示词）')
    for at,kind,obj in events:
        clock.set(at)
        if kind==1:
            t=obj
            if t.role=='user' and first_dream and cfg.recall_strengthening:
                snap=store.snapshot(embedder)
                cue=Cue(t.text,tuple(hot.hot[-cfg.cue_recent_turns:]),hot.ids())
                recalled=recall(snap,cue,at,cfg)
                store.add_trace(at,[m.id for m in recalled.near],[m.id for m in recalled.remote],
                                [m.id for m in recalled.core])
            moved=hot.add(t)
            if moved:store.add_cold(moved)
            if moved and answer:
                hot.summary=update_summary(llm,hot.summary,moved)
            if cfg.dream_enabled and t.role=='assistant':
                while store.pending_count()>=cfg.dream_trigger_turns:
                    window=store.pending(cfg.dream_window_max_turns)
                    if len(window)%2:window=window[:-1]
                    if not window:raise RuntimeError('unaligned Cold window')
                    for attempt in range(dream_retry_failed+1):
                        outcome=run_dream(store,window,llm,embedder,clock,cfg,attempt=attempt)
                        row=asdict(outcome)
                        op_rows=store.conn.execute('SELECT op_json,status FROM dream_ops WHERE dream_id=?',(outcome.dream_id,)).fetchall()
                        counts={}
                        for op_row in op_rows:
                            kind=json.loads(op_row['op_json']).get('op','unknown')
                            counts[kind]=counts.get(kind,0)+1
                        row.update({'first_turn':window[0].id,'last_turn':window[-1].id,
                                    'attempt':attempt,'operation_counts':counts,
                                    'rejections':[dict(r) for r in store.conn.execute('SELECT op_index,reason FROM dream_ops WHERE dream_id=? AND status="rejected"',(outcome.dream_id,))]})
                        dream_log.append(row)
                        if outcome.status=='applied':break
                    if outcome.status!='applied':raise RuntimeError(f'Dream JSON failed: {outcome.dream_id}')
                    first_dream=True
                    snap=store.snapshot(embedder)
                    merged={x for pair in snap.merges for x in pair[:1]}
                    curve.append({'turns':sum(x.time<=at for x in turns),'total':len(snap.memories),
                                  'awake':sum(not __import__('memlab.strength',fromlist=['strength']).strength(list(m['events']),m['salience'],at,cfg)[2] for m in snap.memories),
                                  'merge_parents':len(merged),'entities':len(snap.entities)})
            continue
        probe=obj
        original=datetime.fromisoformat(probe['time'])
        expected=simulate_hot([{'id':t.id,'role':t.role,'time':t.time} for t in raw_turns],original)
        if hot.ids()!=expected:raise AssertionError(f'Hot mismatch at {probe["id"]}')
        scenarios=[(0,at,probe,False)]
        for variant in probe.get('gap_variants',[]):
            scenarios.append((variant['offset_days'],at+timedelta(days=variant['offset_days']),
                              {**probe,**variant},True))
        if probe['category'] in ('淡忘','保留'):
            used={days for days,_,_,_ in scenarios}
            template=next((s for s in scenarios if s[3]),scenarios[0])
            for days in gap_sweep:
                if days not in used:
                    scenarios.append((days,at+timedelta(days=days),template[2] if days>0 else probe,days>0))
        for offset,when,p,variant in scenarios:
            clock.set(when)
            before=store.version
            snap=store.snapshot(embedder)
            cue=Cue(probe['message'],tuple(hot.hot[-cfg.cue_recent_turns:]),hot.ids())
            start=time.perf_counter()
            result=recall(snap,cue,when,cfg)
            times.append(time.perf_counter()-start)
            if store.version!=before:raise AssertionError('probe changed graph version')
            probe_version_checks+=1
            for item in (*result.near,*result.remote,*result.core):
                for source in item.sources:
                    if source in turn_by_id and turn_by_id[source].time>=at:raise AssertionError('future memory source')
            for item in result.raw:
                if item.time>=at:raise AssertionError('future raw source')
            mark=score_probe(result,p,variant)
            measure=measure_probe(result,p,snap,when,cfg)
            not_scored=changed_time and (probe['category']=='时间' or bool(result.diagnostics['time_intervals']))
            answer_data=None
            if answer:
                answer_result=generate_answer(llm,probe,hot.hot,hot.summary,when,
                                              render_memory_block(result,when,cfg))
                answer_data={'text':answer_result.text,'usage':answer_result.usage,
                             'cache_hit':answer_result.cache_hit,'cache_key':answer_result.cache_key}
            records.append({'probe_id':probe['id'],'category':probe['category'],'time':when.isoformat(),
                            'message':probe['message'],'variant_days':offset,'soft':probe.get('soft',False),
                            'pending_cold':store.pending_count(),'store_version':before,
                            'not_scored':not_scored,'recall':_serialize_result(result),
                            'rendered':render_memory_block(result,when,cfg),'score':mark,'measure':measure,
                            'answer':answer_data})
        clock.set(at)
    summary={'set':name,'preset':preset_name,'by_category':summarize(records),
             'total_probes':sum(r['variant_days']==0 for r in records),
             'pending_cold_at_probes':{r['probe_id']:r['pending_cold'] for r in records if r['variant_days']==0},
             'dream_usage':{'input_tokens':sum(x['usage'].get('input_tokens',0) for x in dream_log),
                            'output_tokens':sum(x['usage'].get('output_tokens',0) for x in dream_log)},
             'soft':[{'probe_id':r['probe_id'],
                      'all_complete':r['score']['all']['complete_strict'],
                      'memory_complete':r['score']['memory']['complete_strict'],
                      'attribution':r['score']['attribution']}
                     for r in records if r['soft'] and r['variant_days']==0],
             'attribution':{r['probe_id']:r['score']['attribution'] for r in records if r['variant_days']==0},
             'probe_version_checks':probe_version_checks,
             'measure':summarize_measure(records)}
    timing=timing_summary(times,sum(not x['cache_hit'] for x in dream_log),sum(x['cache_hit'] for x in dream_log),
                          llm.new_calls,llm.new_input_tokens,llm.new_output_tokens)
    curves={'dream':curve,'gap_sweep':{r['probe_id']+'+'+str(r['variant_days']):(
                                         r['score']['forgetting_pass'] if r['category']=='淡忘' and r['variant_days']>0
                                         else r['score']['all']['complete_strict'])
                                     for r in records if r['category'] in ('淡忘','保留')}}
    import subprocess
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    config={'set':name,'preset':preset_name,'parameters':asdict(cfg),'embedder':embedder.identity,
            'llm':llm.model,'prompt_versions':{'integrate':'integrate_v1','answer':'answer_v1'},
            'git_head':head,'answer':answer,'summary_enabled':bool(answer) if summary_enabled is None else summary_enabled,
            'time_shift_days':time_shift_days,'time_scale':time_scale,'dream_retry_failed':dream_retry_failed}
    write_run(out,config,store,turns[-1].time,cfg,dream_log,records,curves,summary,timing)
    store.close()
    return {'out':str(out),'summary':summary,'timing':timing}
