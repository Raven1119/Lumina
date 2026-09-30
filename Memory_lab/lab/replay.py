"""Deterministic simulated-time event stream for a development set."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict,replace
from datetime import datetime,timedelta
from pathlib import Path

from eval_set.build import build_one,simulate_hot
from Conversation_Memory.engine.clock import SimClock
from Conversation_Memory.engine.config import preset
from Conversation_Memory.engine.integrate import run_dream
from Conversation_Memory.engine.integrate import consume_traces
from Conversation_Memory.engine.pattern_v2 import run_pattern as run_pattern_v2
from Conversation_Memory.engine.pattern_v3 import run_pattern as run_pattern_v3
from Conversation_Memory.engine.recall import recall
from Conversation_Memory.engine.render import render_memory_block
from Conversation_Memory.engine.store import Store
from Conversation_Memory.engine.types import Cue,Turn
from Conversation_Memory.engine.usage import used_memories
from Conversation_Memory.engine.strength import strengths

from .hotcold import HotCold
from .answer import update_summary,generate_answer
from Conversation_Memory.engine.usage_quote import parse_integrated_used_v4
from .report import write_run,timing_summary,write_jsonl
from .scoring import score_probe,summarize
from .measure import measure_probe,summarize_measure
from .diagnose import remote_diagnostic

ROOT=Path(__file__).resolve().parents[1]


def load_set(name):
    built=ROOT/'eval_set'/'built'
    long_v2=re.fullmatch(r'(dev_[ab])_long(8|12|16)w_v2',name)
    if long_v2:
        filler=ROOT/'eval_set'/'fillers'/f'{long_v2[1]}_{long_v2[2]}w_v2.txt'
        a=built/f'{name}.dialogue.json';b=built/f'{name}.gold.json'
        if not a.is_file() or not b.is_file() or min(a.stat().st_mtime,b.stat().st_mtime)<filler.stat().st_mtime:
            from eval_set.build_long import build_long
            build_long(long_v2[1],int(long_v2[2]),'v2')
        return json.loads(a.read_text(encoding='utf-8')),json.loads(b.read_text(encoding='utf-8'))
    if name in ('dev_a_long8w','dev_b_long8w'):
        a=built/f'{name}.dialogue.json';b=built/f'{name}.gold.json'
        filler=ROOT/'eval_set'/'fillers'/f'{name.removesuffix("_long8w")}_8w.txt'
        if not a.is_file() or not b.is_file() or min(a.stat().st_mtime,b.stat().st_mtime)<filler.stat().st_mtime:
            from eval_set.build_long import build_long
            build_long(name.removesuffix('_long8w'))
        return (json.loads(a.read_text(encoding='utf-8')),
                json.loads(b.read_text(encoding='utf-8')))
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
            dream_retry_failed=0,allow_holdout=False,summary_enabled=None,
            answer_prompt=None,answer_scope='all',answer_ids=None,usage_source=None,usage_min_overlap=3,
            integrate_prompt=None,render_version=None,write_check='none',
            usage_mode=None,usage_reply_source=None,probe_observer=None):
    if name=='holdout_c' and not allow_holdout:raise ValueError('holdout requires --allow-holdout')
    dialogue,gold=load_set(name)
    if dialogue.get('split')=='holdout' and not allow_holdout:raise ValueError('holdout requires --allow-holdout')
    cfg=preset(preset_name,'hash' if embedder.identity['model'].startswith('hash') else
               'minilm' if 'MiniLM' in embedder.identity['model'] else 'bge-m3')
    cfg=replace(cfg,llm_model=llm.model)
    answer_prompt=answer_prompt or cfg.answer_prompt
    usage_source=usage_source or cfg.usage_source
    integrate_prompt=integrate_prompt or cfg.integrate_prompt
    render_version=render_version or cfg.render_version
    usage_mode=usage_mode or cfg.usage_mode
    usage_reply_source=usage_reply_source or cfg.usage_reply_source
    if usage_source != cfg.usage_source:raise ValueError('memory v1 usage source is fixed by preset')
    if usage_min_overlap not in (1,2,3):raise ValueError('unsupported overlap threshold')
    if integrate_prompt not in ('v1','v2','v3','v4'):raise ValueError('unknown integrate prompt')
    if render_version not in ('v1','v2','v3','v4'):raise ValueError('unknown render version')
    if write_check != 'none':raise ValueError('old repair path removed')
    if usage_mode != cfg.usage_mode:raise ValueError('memory v1 usage mode is fixed by preset')
    if usage_reply_source != cfg.usage_reply_source:raise ValueError('memory v1 reply source is fixed by preset')
    if answer_prompt != cfg.answer_prompt or integrate_prompt != cfg.integrate_prompt or render_version != cfg.render_version:
        raise ValueError('memory v1 prompt and render versions are fixed by preset')
    cfg=replace(cfg,usage_source=usage_source,usage_min_overlap=usage_min_overlap,
                integrate_prompt=integrate_prompt,render_version=render_version,
                usage_mode=usage_mode,usage_reply_source=usage_reply_source,
                answer_prompt=answer_prompt)
    if answer and preset_name not in ('B1','P8','P9'):
        raise ValueError('answer comparison is limited to B1, P8 and P9')
    raw_turns=[Turn(t['id'],session['id'],t['role'],datetime.fromisoformat(t['time']),t['text'])
               for session in dialogue['sessions'] for t in session['turns']]
    t0=raw_turns[0].time
    def shift(at):return t0+(at-t0)*time_scale+timedelta(days=time_shift_days)
    turns=[Turn(t.id,t.session_id,t.role,shift(t.time),t.text) for t in raw_turns]
    turn_by_id={t.id:t for t in turns}
    turn_positions={t.id:i for i,t in enumerate(turns)}
    scripted_next={t.id:turns[i+1].text for i,t in enumerate(turns[:-1])
                   if t.role=='user' and turns[i+1].role=='assistant' and turns[i+1].session_id==t.session_id}
    scripted_reply_id={t.id:turns[i+1].id for i,t in enumerate(turns[:-1])
                       if t.role=='user' and turns[i+1].role=='assistant' and turns[i+1].session_id==t.session_id}
    probe_events=[]
    for p in gold['probes']:
        probe_events.append((shift(datetime.fromisoformat(p['time'])),0,p))
    events=[(t.time,1,t) for t in turns]+probe_events
    events.sort(key=lambda row:(row[0],row[1]))
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    store=Store(out/'memory.sqlite');store.set_embedder_identity(embedder.identity)
    clock=SimClock(turns[0].time);hot=HotCold();dream_log=[];records=[];curve=[];times=[];shadow_log=[];usage_log=[];pending_usage=[];repair_log=[];pattern_log=[]
    first_dream=False;probe_version_checks=0
    changed_time=time_shift_days!=0 or time_scale!=1
    if changed_time:
        expected=(len(turns)//40)
        print(f'预计 Dream 新调用上限约 {expected} 次（时间变换改变提示词）')
    if usage_source in ('shadow','dream') and cfg.usage_mode!='integrate':
        print(f'预计 shadow 新调用上限 {sum(t.role=="user" for t in turns)} 次（缓存命中可降低实际值）')
    for at,kind,obj in events:
        clock.set(at)
        if kind==1:
            t=obj
            if t.role=='user' and (first_dream or usage_source=='dream') and cfg.recall_strengthening:
                snap=store.snapshot(embedder)
                cue=Cue(t.text,tuple(hot.hot[-cfg.cue_recent_turns:]),hot.ids())
                recalled=recall(snap,cue,at,cfg)
                used=[]
                if usage_source!='surfaced':
                    memories={m.id:m for m in (*recalled.near,*recalled.remote,*recalled.core)}
                    entity_names={entity['id']:entity['name'] for entity in snap.entities}
                    by_id={m['id']:m for m in snap.memories}
                    usage_memories=[{'id':mid,'text':m.text,
                                     'entity_names':[entity_names[e] for e in by_id[mid]['entities'] if e in entity_names]}
                                    for mid,m in memories.items()]
                    context_texts=[t.text,*(turn.text for turn in hot.hot),hot.summary]
                    if cfg.usage_mode=='integrate' and cfg.usage_reply_source=='scripted':
                        response_text=scripted_next.get(t.id,'')
                    elif usage_source=='scripted':
                        response_text=scripted_next.get(t.id,'')
                    else:
                        response,_=generate_answer(llm,{'message':t.text},hot.hot,hot.summary,
                                                   hot.summary_until,at,render_memory_block(recalled,at,replace(cfg,render_version='v1')),
                                                   'v2',purpose='shadow')
                        response_text=response.text
                    if usage_source!='dream':
                        used=used_memories(response_text,context_texts,usage_memories,cfg.usage_min_overlap)
                    if usage_source in ('shadow','dream') and cfg.usage_mode!='integrate':
                        usage_row={'turn_id':t.id,'time':at,'message':t.text,
                                   'answer':response_text,'context_texts':context_texts,
                                   'memories':usage_memories,'context_ids':list(memories), 'used':used,
                                   'cache_key':response.cache_key,'cache_hit':response.cache_hit}
                        shadow_log.append(usage_row)
                    elif usage_source=='dream' and cfg.usage_mode=='integrate':
                        reply_id=scripted_reply_id.get(t.id)
                        previous=turns[max(0,turn_positions[reply_id]-cfg.usage_context_turns):turn_positions[reply_id]] if reply_id else []
                        usage_row={'turn_id':t.id,'reply_turn_id':scripted_reply_id.get(t.id),
                                   'time':at,'message':t.text,'answer':response_text,
                                   'memories':usage_memories,'context_ids':list(memories),'used':used,
                                   **({'previous_messages':[x.text for x in previous]}
                                      if cfg.usage_quote_check else {}),
                                   **({'known_entity_names':sorted({name for entity in snap.entities
                                              for name in (entity['name'],*entity['aliases'])})}
                                      if cfg.usage_short_match=='entity' else {})}
                trace_id=store.add_trace(at,[m.id for m in recalled.near],[m.id for m in recalled.remote],
                                         [m.id for m in recalled.core],used)
                if usage_source=='dream':
                    usage_row['trace_id']=trace_id
                    pending_usage.append(usage_row)
            moved=hot.add(t)
            if moved:store.add_cold(moved)
            if moved and (answer or usage_source!='surfaced'):
                hot.summary=update_summary(llm,hot.summary,moved)
            if cfg.dream_enabled and t.role=='assistant':
                while store.pending_count()>=cfg.dream_trigger_turns:
                    window=store.pending(cfg.dream_window_max_turns)
                    if len(window)%2:window=window[:-1]
                    if not window:raise RuntimeError('unaligned Cold window')
                    for attempt in range(dream_retry_failed+1):
                        window_ids={turn.id for turn in window if turn.role=='assistant'}
                        active_usage=[row for row in pending_usage if row['reply_turn_id'] in window_ids] if cfg.usage_mode=='integrate' else pending_usage
                        outcome=run_dream(store,window,llm,embedder,clock,cfg,attempt=attempt,
                                          recalled_turns=active_usage if cfg.usage_mode=='integrate' else None)
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
                    if cfg.pattern_enabled:
                        pattern_log.append({'dream_id':outcome.dream_id,
                                            **(run_pattern_v3 if cfg.pattern_prompt=='v3' else run_pattern_v2)(
                                                store,outcome.dream_id,llm,embedder,at,cfg)})
                    if usage_source=='dream':
                        if cfg.usage_mode=='integrate':
                            response_text=store.conn.execute('SELECT response FROM dream_runs WHERE id=?',
                                                             (outcome.dream_id,)).fetchone()[0]
                            if cfg.usage_quote_check:
                                judged,invalid,accepted=parse_integrated_used_v4(
                                    response_text,active_usage,cfg.usage_short_match)
                            else:
                                raise AssertionError('P8/P9 require quote validation')
                            judged={row['turn_id']:judged[row['reply_turn_id']] for row in active_usage}
                            usage_response=None
                        else:
                            raise AssertionError('P8/P9 require integrated use judgment')
                        for item in active_usage:
                            item['used']=judged[item['turn_id']]
                            store.conn.execute('UPDATE recall_traces SET used_json=? WHERE id=? AND consumed_by IS NULL',
                                               (json.dumps(item['used']),item['trace_id']))
                        trace_ids={item['trace_id'] for item in active_usage} if cfg.usage_mode=='integrate' else None
                        consume_traces(store,cfg,at,outcome.dream_id,trace_ids)
                        store.conn.commit()
                        usage_log.append({'dream_id':outcome.dream_id,'turns':len(active_usage),
                                          'context_count':sum(len(t['context_ids']) for t in active_usage),
                                          **({'raw_used_count':len(__import__('Conversation_Memory.engine.integrate',fromlist=['_json_first'])._json_first(response_text).get('used') or []),
                                              'accepted':accepted} if cfg.usage_quote_check else {}),
                                          'used_count':sum(len(t['used']) for t in active_usage),
                                          'invalid':invalid,'judgments':[
                                              {'turn_id':t['turn_id'],'reply_turn_id':t.get('reply_turn_id'),
                                               'reply':t['answer'],'memories':t['memories'],'used':t['used']}
                                              for t in active_usage],
                                          'cache_key':outcome.cache_key if cfg.usage_mode=='integrate' else usage_response.cache_key,
                                          'cache_hit':outcome.cache_hit if cfg.usage_mode=='integrate' else usage_response.cache_hit})
                        pending_usage=[row for row in pending_usage if row not in active_usage]
                    first_dream=True
                    snap=store.snapshot(embedder)
                    merged={x for pair in snap.merges for x in pair[:1]}
                    curve.append({'turns':sum(x.time<=at for x in turns),'total':len(snap.memories),
                                  'awake':sum(not __import__('Conversation_Memory.engine.strength',fromlist=['strength']).strength(list(m['events']),m['salience'],at,cfg)[2] for m in snap.memories),
                                  'merge_parents':len(merged),'entities':len(snap.entities)})
            continue
        probe=obj
        original=datetime.fromisoformat(probe['time'])
        expected=simulate_hot([{'id':t.id,'role':t.role,'time':t.time} for t in raw_turns],original)
        if hot.ids()!=expected:raise AssertionError(f'Hot mismatch at {probe["id"]}')
        scenarios=[(0,at,probe,False)]
        primary_offsets={0,*(v['offset_days'] for v in probe.get('gap_variants',[]))}
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
            strength_rows=strengths(snap.memories,when,cfg) if snap.memories else []
            awake=[row[1] for row in strength_rows if not row[2]]
            pi_profile={'awake':len(awake),
                        'gt_0_9':sum(pi>.9 for pi in awake)/len(awake) if awake else None,
                        'lt_0_1':sum(pi<.1 for pi in awake)/len(awake) if awake else None}
            not_scored=bool(probe.get('not_scored')) or (changed_time and (probe['category']=='时间' or bool(result.diagnostics['time_intervals'])))
            answer_data=None
            if answer and not not_scored and (answer_ids is None or probe['id'] in answer_ids) and (answer_scope=='all' or offset in primary_offsets):
                answer_result,context=generate_answer(llm,probe,hot.hot,hot.summary,
                                              hot.summary_until,when,render_memory_block(result,when,cfg),
                                              answer_prompt,parse_mode=cfg.answer_parse)
                answer_data={'text':answer_result.text,'noticed':context.get('noticed'),
                             'usage':answer_result.usage,
                             'cache_hit':answer_result.cache_hit,'cache_key':answer_result.cache_key,
                             'context':context}
            remote_detail=remote_diagnostic(p,snap,result,when,cfg,
                                            answer_data['text'] if answer_data else '',
                                            [probe['message'],*(turn.text for turn in hot.hot),hot.summary]) if offset==0 else None
            formation_v51=None
            if preset_name in ('P8','P9') and offset==0:
                from analysis.metrics_v51 import PLANTS,formation
                if probe['id'] in PLANTS:
                    formation_v51=formation(snap,gold['tags'],
                                            {tid:turn.time for tid,turn in turn_by_id.items()},probe['id'])
            records.append({'probe_id':probe['id'],'category':probe['category'],'time':when.isoformat(),
                            'message':probe['message'],'variant_days':offset,'soft':probe.get('soft',False),
                            'rolling_summary':hot.summary,
                            'pending_cold':store.pending_count(),'store_version':before,
                            'not_scored':not_scored,'recall':_serialize_result(result),
                            'rendered':render_memory_block(result,when,cfg),'score':mark,'measure':measure,
                            'pi_profile':pi_profile,
                            'answer':answer_data,'remote_diagnostic':remote_detail})
            if probe_observer is not None:
                probe_observer(store,embedder,cfg,hot,probe,when,records[-1])
            if formation_v51 is not None:
                records[-1]['formation_v51']=formation_v51
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
             'recall_strengthening_events':store.conn.execute("SELECT COUNT(*) FROM strength_events WHERE kind='recall'").fetchone()[0],
             'measure':summarize_measure(records)}
    timing=timing_summary(times,sum(not x['cache_hit'] for x in dream_log),sum(x['cache_hit'] for x in dream_log),
                          llm.new_calls,llm.new_input_tokens,llm.new_output_tokens,
                          llm.new_calls_by_purpose,llm.cache_hits_by_purpose)
    curves={'dream':curve,'gap_sweep':{r['probe_id']+'+'+str(r['variant_days']):(
                                         r['score']['forgetting_pass'] if r['category']=='淡忘' and r['variant_days']>0
                                         else r['score']['all']['complete_strict'])
                                     for r in records if r['category'] in ('淡忘','保留')}}
    import subprocess
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    config={'set':name,'preset':preset_name,'parameters':asdict(cfg),'embedder':embedder.identity,
            'llm':llm.model,'prompt_versions':{'integrate':f'integrate_{integrate_prompt}','answer':f'answer_{answer_prompt}','render':f'render_{render_version}'},
            'git_head':head,'answer':answer,'summary_enabled':bool(answer or usage_source!='surfaced') if summary_enabled is None else summary_enabled,
            'time_shift_days':time_shift_days,'time_scale':time_scale,'dream_retry_failed':dream_retry_failed,
            'answer_scope':answer_scope,'usage_source':usage_source,'write_check':write_check,
            **({'answer_ids':sorted(answer_ids)} if answer_ids is not None else {})}
    write_run(out,config,store,turns[-1].time,cfg,dream_log,records,curves,summary,timing)
    if usage_source in ('shadow','dream') and cfg.usage_mode!='integrate':write_jsonl(out/'shadow_log.jsonl',shadow_log)
    if usage_source=='dream':write_jsonl(out/'usage_log.jsonl',usage_log)
    if write_check=='repair':write_jsonl(out/'repair_log.jsonl',repair_log)
    if cfg.pattern_enabled:write_jsonl(out/'pattern_log.jsonl',pattern_log)
    if cfg.trace_enabled:
        from Conversation_Memory.engine.trace import read_rows
        write_jsonl(out/'trace_clauses.jsonl',[
            {key:value for key,value in row.items() if key!='embedding'}
            for row in read_rows(store.conn)])
    store.close()
    return {'out':str(out),'summary':summary,'timing':timing}
