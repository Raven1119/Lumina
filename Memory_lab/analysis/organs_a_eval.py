"""Organ A answer arm and v2 evidence judging on the existing 60 dev_a rows.

Every HTTP attempt is reserved durably before dispatch. No holdout, graph write,
provider retry, or model call through the historical provider implementation.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys
import threading

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'Memory_lab')]
from config.lumina import load_config
from core.env_loader import load_env_file
from core.contracts import DraftTurn
from core.draft_store import JsonlDraftStore,HotDraftSummary
from core.message_runtime import MessageRuntime
from core.dialogue_io import DialogueIO
from Mind.runner import DialogueRunner
from Language.channel import LanguageChannel
from Nervous.bus import EventBus
from Nervous.scheduler import DialogueScheduler
from Conversation_Memory.answer import parse_answer_v5_tolerant,fallback_answer_v5
from Conversation_Memory.facade import MemoryV1
from Conversation_Memory.engine.embed import resolve_embedder
from lab.llm import CachedLLM,RealLLM
from lab.replay import run_set
from lab.answer import answer_system,answer_messages
from judge.build_packets import evidence_turns
from judge.run_judge import validate
from judge.aggregate import block

class BudgetExhausted(BaseException):pass
class ProviderFailure(BaseException):pass
class EvaluationMismatch(BaseException):pass

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

class Calls:
    def __init__(self,path,model,transport=None,limit=300):
        self.model,self.transport,self.limit=model,transport,limit
        path.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(path)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=FULL')
        self.conn.execute('CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, operation TEXT UNIQUE, purpose TEXT, model TEXT, request TEXT, response TEXT, status TEXT)')
        self.conn.commit()

    def complete(self,operation,purpose,system,messages,max_tokens=1000):
        body={'model':self.model,'max_tokens':max_tokens,'temperature':0,
              'thinking':{'type':'disabled'},'system':system,'messages':messages}
        raw=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        old=self.conn.execute('SELECT * FROM calls WHERE operation=?',(operation,)).fetchone()
        if old:
            if old['request']!=raw:raise ValueError('evaluation_request_changed')
            if old['status']!='received':raise ProviderFailure('prior_call_outcome_'+old['status'])
            data=json.loads(old['response'])
            return ''.join(p.get('text','') for p in data.get('content',[]) if p.get('type')=='text')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            if self.conn.execute('SELECT count(*) FROM calls').fetchone()[0]>=self.limit:
                raise BudgetExhausted('300_attempt_limit')
            self.conn.execute('INSERT INTO calls(operation,purpose,model,request,status) VALUES(?,?,?,?,?)',
                              (operation,purpose,self.model,raw,'reserved'))
        try:
            if self.transport:
                data=self.transport(body)
            else:
                import os,httpx
                load_env_file(ROOT/'.env.local',override=False)
                with httpx.Client(timeout=180) as client:
                    result=client.post('https://api.deepseek.com/anthropic/v1/messages',
                        headers={'X-Api-Key':os.environ['DEEPSEEK_API_KEY'],'anthropic-version':'2023-06-01'},json=body)
                    if result.status_code!=200:raise ProviderFailure('HTTP_'+str(result.status_code))
                    data=result.json()
            with self.conn:
                self.conn.execute('UPDATE calls SET response=?,status=? WHERE operation=?',
                                  (json.dumps(data,ensure_ascii=False),'received',operation))
        except BaseException as exc:
            with self.conn:
                self.conn.execute('UPDATE calls SET status=? WHERE operation=?',('failed',operation))
            if isinstance(exc,(KeyboardInterrupt,SystemExit,ProviderFailure)):raise
            raise ProviderFailure(type(exc).__name__) from None
        return ''.join(p.get('text','') for p in data.get('content',[]) if p.get('type')=='text')

    def summary(self):
        result={}
        for row in self.conn.execute('SELECT * FROM calls'):
            key=row['model']+'/'+row['purpose']
            entry=result.setdefault(key,{'attempts':0,'received':0,'input_tokens':0,'output_tokens':0,'cache_read_input_tokens':0,'cache_creation_input_tokens':0})
            entry['attempts']+=1
            if row['response']:
                entry['received']+=1
                usage=json.loads(row['response']).get('usage',{})
                for name in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens'):
                    entry[name]+=int(usage.get(name,0))
        return result

class AnswerModel:
    client_kind='model'
    def __init__(self,calls,prefix):self.calls,self.prefix=calls,prefix;self.n=0;self.l=0
    def complete_answer(self,system,messages):
        self.n+=1
        key=hashlib.sha256(json.dumps([system,messages],ensure_ascii=False).encode()).hexdigest()
        return self.calls.complete(f'{self.prefix}:mind:{key}','mind_answer',system,messages)
    def complete_text(self,system,messages):
        self.l+=1
        key=hashlib.sha256(json.dumps([system,messages],ensure_ascii=False).encode()).hexdigest()
        return self.calls.complete(f'{self.prefix}:language:{key}','language',system,messages)

class EmptyState:
    def thinking(self,_):pass
    def snapshot(self):return {}


def answer_arm(out,calls):
    config=json.loads((ROOT/'Memory_lab/runs/v51/dev_a_P8/config.json').read_text())
    archived={ (r['probe_id'],r['variant_days']):r['answer'] for r in
        map(json.loads,(ROOT/'Memory_lab/runs/v51/dev_a_P8/probes.jsonl').read_text().splitlines()) if r.get('answer')}
    embed=resolve_embedder('bge-m3',False,ROOT/'Memory_lab/cache/embed',hf_home=ROOT/'Memory_lab/cache/hf')
    llm=CachedLLM(RealLLM(config['llm']),ROOT/'Memory_lab/cache/llm',True)
    persona=(ROOT/'prompts/chat_background.md').read_text(encoding='utf-8')
    completed=[]
    def observe(store,embedding,cfg,hot,probe,now,row):
        label=row['probe_id']+'+'+str(row['variant_days'])
        dest=out/'answers'/f'{label}.json'
        if dest.exists():
            completed.append(json.loads(dest.read_text()));return
        key=(row['probe_id'],row['variant_days'])
        if key in archived:
            baseline=archived[key]['text'];origin='archived_P8'
        else:
            system=answer_system(now,row['rendered'])
            messages=answer_messages(probe,hot.hot,hot.summary,hot.summary_until,now)
            raw=calls.complete(label+':p8','p8_missing_answer',system,messages)
            baseline,_,found,*_=parse_answer_v5_tolerant(raw)
            if not found:baseline=fallback_answer_v5(raw)
            origin='new_P8_same_parameters'
        # Isolated Hot/bus per probe; Memory's committed replay snapshot is read-only.
        folder=out/'thoughts'/label
        facade=MemoryV1(folder/'unused-memory',embedder_factory=lambda:embedding,config=cfg)
        facade._local.store=store;facade._local.embedder=embedding
        facade.record_trace=lambda *_:None
        original_recall=facade.recall_and_render
        initial=[True]
        def checked_recall(*args):
            recalled=original_recall(*args)
            if initial[0]:
                initial[0]=False
                if recalled.block!=row['rendered']:
                    raise EvaluationMismatch('P8_memory_changed_before_model_call')
            return recalled
        facade.recall_and_render=checked_recall
        drafts=JsonlDraftStore(folder/'hot.jsonl')
        if not (folder/'bus.sqlite').exists():
            summary=HotDraftSummary(hot.summary,1,1,'2026-01-01T00:00:00.000000Z',hot.summary_until.isoformat() if hot.summary_until else None) if hot.summary else None
            drafts.replace_contents_atomically(summary,[DraftTurn(role=t.role,text=t.text,turn_id=t.id,created_at=t.time,
                source_timezone='Asia/Shanghai',timezone_source='configured_default') for t in hot.hot])
        class Clock:
            def now(self):return now
        model=AnswerModel(calls,label)
        runtime=MessageRuntime(hot_store=drafts,model_client=model,chat_background=persona,memory=facade,clock=Clock(),default_timezone='Asia/Shanghai')
        io=DialogueIO(runtime,threading.Lock());bus=EventBus(folder/'bus.sqlite')
        settings=load_config(overrides={'mind':{'protocol':'a2'},'workspace':{'path':str(folder/'empty-workspace')}})
        channel=LanguageChannel(bus,io,settings)
        scheduler=DialogueScheduler(bus,None,channel,lambda _:None)
        runner=DialogueRunner(bus,io,settings,EmptyState(),scheduler.emit)
        scheduler.runner=runner
        bus.publish(label,'user.message',{'message':probe['message']})
        scheduler.drain_once()
        prepared=bus.get(label+':prepared')
        assert prepared['read']['block']==row['rendered'], 'P8_memory_changed'
        assert prepared['state_block']=='', 'blind_probe_state_not_empty'
        speeches=[];choices=[]
        for step in range(1,settings['mind']['dialogue_max_steps']+1):
            speech=bus.get(f'{label}:{step}:0:done')
            if speech:speeches.append(speech)
            response=bus.get(f'{label}:step:{step}:response')
            if response:
                from Conversation_Memory.answer import parse_dialogue
                choices.append(parse_dialogue(response['raw'])['rephrase'])
        result={'probe':probe,'id':row['probe_id'],'variant':row['variant_days'],'time':row['time'],
                'category':row['category'],'soft':row['soft'],'p8':baseline,'p8_origin':origin,
                'a2':'\n'.join(r['final_text'] for r in speeches),
                'rephrase':any(choices),'choices':choices,'speeches':speeches,'hot_ids':[t.id for t in hot.hot],
                'memory_sha256':hashlib.sha256(row['rendered'].encode()).hexdigest()}
        write(dest,result);completed.append(result);bus.close()
        print('answer',label,len(completed),'attempts',sum(v['attempts'] for v in calls.summary().values()),flush=True)
    replay=out/'replay'
    # run_set creates a fresh store. On resume only this task's derived replay
    # directory is rotated; individual saved answers and receipts stay intact.
    index=1
    while replay.exists():replay=out/f'replay_{index}';index+=1
    run_set('dev_a','P8',llm,embed,replay,probe_observer=observe)
    assert len(completed)==60
    write(out/'answers.json',completed)
    write(out/'evaluation_config.json',{'baseline_config':config,'baseline_archived':len(archived),
        'answer_max_tokens':1000,'temperature':0,'thinking':'disabled','prefill':False,
        'state':'empty','set':'dev_a','probes':60})
    return completed


def packets(rows):
    dialogue=json.loads((ROOT/'Memory_lab/eval_set/built/dev_a.dialogue.json').read_text())
    gold=json.loads((ROOT/'Memory_lab/eval_set/built/dev_a.gold.json').read_text())
    turns=[dict(t,time=datetime.fromisoformat(t['time'])) for s in dialogue['sessions'] for t in s['turns']]
    rng=random.Random('organs-a-dev-a');items=[];key={}
    for row in rows:
        p=row['probe'];variant=row['variant']
        spec=next((v for v in p.get('gap_variants',[]) if v['offset_days']==variant),None) if variant else p
        if spec is None:spec=next(iter(p.get('gap_variants',[])),p)
        tags=set(sum(spec.get('must_surface_tags',p.get('must_surface_tags',[])),[]))|set(spec.get('must_not_surface_tags',p.get('must_not_surface_tags',[])))
        iid='dev_a-'+str(len(items)+1).zfill(2)
        x,y=('A2','P8') if rng.random()<0.5 else ('P8','A2')
        answers={'A2':row['a2'],'P8':row['p8']}
        key[iid]={'X':x,'Y':y,'probe':row['id'],'variant':variant,'category':row['category'],'origin':row['p8_origin']}
        items.append({'id':iid,'时间':row['time'],'说明':f'这是 {variant} 天后的变体，期间没有新对话。' if variant else '',
            '他发来':p['message'],'相关事实（出题人标注）':[plant['summary'] for plant in gold['plants'] if set(plant['tags'])&tags],
            '评分要点':p['rubric'],'出题人备注':p.get('note',''),
            '证据轮次':evidence_turns(turns,p,row['hot_ids'],(row['p8'],row['a2'])),
            'X':answers[x],'Y':answers[y]})
    return items,key


def judge_arm(out,calls,rows):
    items,key=packets(rows)
    write(out/'blind_key.json',key)
    prompt=(ROOT/'Memory_lab/judge/judge_prompt_v2.md').read_text()
    outcomes={};all_results=[]
    for order in (1,2):
        pending=[];results=[]
        for item in items:
            if order==2:item={**item,'X':item['Y'],'Y':item['X']}
            # Follow evidence-v2: identical answers / first-pass tie skip reversal.
            if (order==1 and item['X']==item['Y']) or (order==2 and outcomes[item['id']]=='tie'):
                results.append({'id':item['id'],'X':None,'Y':None,'prefer':'tie','reason':'identical_answers' if order==1 else 'first_pass_tie'})
            else:pending.append(item)
        write(out/f'blind_packet_{order}.json',pending)
        for start in range(0,len(pending),5):
            batch=pending[start:start+5]
            shapes={it['id']:{f:len(it['评分要点'][f]) for f in ('should','should_not')} for it in batch}
            skeleton={'items':[{'id':it['id'],**{side:{'should':[None]*shapes[it['id']]['should'],'should_not':[None]*shapes[it['id']]['should_not'],'wrong':None,'note':'','alive':None} for side in ('X','Y')},'prefer':None,'reason':''} for it in batch]}
            payload={'items':batch,'required_score_lengths_by_id':shapes,
                     'schema_reminder':'逐项填写下面骨架的 null，should 和 should_not 数组长度必须保留。',
                     'output_skeleton_fill_nulls':skeleton}
            content=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            for attempt in range(3):
                raw=calls.complete(f'judge_numbered:{order}:{start}:{attempt}','judge',prompt,[{'role':'user','content':content}],6000)
                try:
                    results.extend(validate(raw,batch));break
                except (ValueError,TypeError,KeyError,AttributeError):
                    pass
            else:
                # No invented judgments or zero-filled scores on invalid output.
                raise ProviderFailure('judge_invalid_after_three_attempts')
            print('judge',order,start+len(batch),'/',len(pending),flush=True)
        if order==1:outcomes={r['id']:r['prefer'] for r in results}
        all_results.append({r['id']:r for r in results})
        write(out/f'judge_{order}.json',results)
    merged=[]
    for iid,k in key.items():
        row={**k,'judgments':{'P8':[],'A2':[]},'prefer':[],'reason':[]}
        for order,results in enumerate(all_results,1):
            result=results[iid];mapping={'X':k['X'],'Y':k['Y']} if order==1 else {'X':k['Y'],'Y':k['X']}
            if order==1:
                for side in ('X','Y'):
                    if isinstance(result[side],dict):row['judgments'][mapping[side]].append(result[side])
            row['prefer'].append('tie' if result['prefer']=='tie' else mapping[result['prefer']]);row['reason'].append(result['reason'])
        merged.append(row)
    summary={'overall':block(['P8','A2'],merged,True),
        'by_category':{c:block(['P8','A2'],[r for r in merged if r['category']==c],True) for c in sorted({r['category'] for r in merged})},
        'by_baseline_origin':{o:block(['P8','A2'],[r for r in merged if r['origin']==o],True) for o in sorted({r['origin'] for r in merged})},
        'rephrase_probes':sum(r['rephrase'] for r in rows),'rephrase_steps':sum(sum(r['choices']) for r in rows),
        'total_steps':sum(len(r['choices']) for r in rows),'rows':merged}
    write(out/'summary.json',summary)
    return summary



def language_examples(out,calls,rows):
    """Explicit component smoke examples, never substituted into the blind arm."""
    from Language.rephrase import language_request
    examples=[]
    for pid in ('A01','A12','A13'):
        row=next(r for r in rows if r['id']==pid and r['variant']==0)
        label=pid+'+0'
        bus=EventBus(out/'thoughts'/label/'bus.sqlite')
        prepared=bus.get(label+':prepared')
        meaning=row['speeches'][0]['mind_text']
        system,messages=language_request(meaning,prepared['memory_block'],'',
            prepared['recent']+[prepared['user']],recent_turns=6)
        final=calls.complete('language_example:'+label,'language_smoke',system,messages)
        examples.append({'id':pid,'source':'explicit_component_smoke_not_mind_choice',
                         'before':meaning,'after':final})
        bus.close()
    write(out/'language_examples.json',examples)
    return examples

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--stage',choices=('answer','judge','examples','all'),default='all')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    config=json.loads((ROOT/'Memory_lab/runs/v51/dev_a_P8/config.json').read_text())
    calls=Calls(args.out/'budget.sqlite',config['llm'])
    try:
        rows=answer_arm(args.out,calls) if args.stage in ('answer','all') else json.loads((args.out/'answers.json').read_text())
        if args.stage in ('judge','all'):judge_arm(args.out,calls,rows)
        if args.stage=='examples':language_examples(args.out,calls,rows)
    finally:
        write(args.out/'cost.json',calls.summary())

if __name__=='__main__':main()
