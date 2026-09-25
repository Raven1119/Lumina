"""Memory Lab command line; holdout stays locked unless explicitly enabled."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from memlab.embed import resolve_embedder
from memlab.store import Store

from .llm import CachedLLM,FakeLLM,RealLLM
from .replay import ROOT,load_set,run_set
from .report import write_json
from .scoring import paired_delta
from .measure import write_comparison_v2


def _options(parser):
    parser.add_argument('--llm',choices=('fake','real'),default='fake')
    parser.add_argument('--cache-only',action='store_true')
    parser.add_argument('--embedder',choices=('auto','bge-m3','minilm','hash'),default='auto')
    parser.add_argument('--allow-download',action='store_true')
    parser.add_argument('--answer',action='store_true')
    parser.add_argument('--judge',choices=('none','deepseek'),default='none')
    parser.add_argument('--gap-sweep',default='0,1,7,14,30,60,120')
    parser.add_argument('--time-shift-days',type=float,default=0)
    parser.add_argument('--time-scale',type=float,default=1)
    parser.add_argument('--dream-retry-failed',type=int,default=0)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--allow-holdout',action='store_true')


def _out(name,kind):
    dialogue,_=load_set(name)
    first=datetime.fromisoformat(dialogue['sessions'][0]['turns'][0]['time'])
    base=ROOT/'runs'/f"{first:%Y%m%dT%H%M}_{kind}"
    if not base.exists():return base
    i=2
    while (ROOT/'runs'/f'{base.name}_{i}').exists():i+=1
    return ROOT/'runs'/f'{base.name}_{i}'


def _execute(args,name,preset_name,out,embedder=None):
    if name=='holdout_c' and not args.allow_holdout:raise ValueError('holdout requires --allow-holdout')
    dialogue,_=load_set(name)
    if dialogue['split']=='holdout' and not args.allow_holdout:raise ValueError('holdout requires --allow-holdout')
    if args.judge!='none':raise ValueError('judge model is undecided; use --judge none')
    embedder=embedder or resolve_embedder(args.embedder,args.allow_download,ROOT/'cache'/'embed')
    base=FakeLLM(embedder) if args.llm=='fake' else RealLLM()
    llm=CachedLLM(base,ROOT/'cache'/'llm',args.cache_only)
    return run_set(name,preset_name,llm,embedder,out,answer=args.answer,
                   gap_sweep=tuple(int(x) for x in args.gap_sweep.split(',') if x),
                   time_shift_days=args.time_shift_days,time_scale=args.time_scale,
                   dream_retry_failed=args.dream_retry_failed,allow_holdout=args.allow_holdout)


def _suite(args):
    names=[x.strip() for x in args.sets.split(',') if x.strip()]
    stages=[x.strip() for x in args.presets.split(',') if x.strip()]
    for name in names:
        if name=='holdout_c' and not args.allow_holdout:raise ValueError('holdout requires --allow-holdout')
        dialogue,_=load_set(name)
        if dialogue['split']=='holdout' and not args.allow_holdout:raise ValueError('holdout requires --allow-holdout')
    root=args.out or _out(names[0],'suite')
    root.mkdir(parents=True,exist_ok=False)
    embedder=resolve_embedder(args.embedder,args.allow_download,ROOT/'cache'/'embed')
    results={};rows=[]
    for name in names:
        for stage in stages:
            run_out=root/f'{name}_{stage}'
            value=_execute(args,name,stage,run_out,embedder)
            results[(name,stage)]=value
            rows.append({'set':name,'stage':stage,'categories':{cat:entry['all_complete'] for cat,entry in value['summary']['by_category'].items()},
                         'new_model_calls':value['timing']['new_model_calls']})
    deltas=[]
    for name in names:
        for a,b in zip(stages,stages[1:]):
            def records(stage):
                path=root/f'{name}_{stage}'/'probes.jsonl'
                return {r['probe_id']:r for r in map(json.loads,path.read_text(encoding='utf-8').splitlines())
                        if r['variant_days']==(60 if r['category'] in ('淡忘','保留') else 0)}
            left,right=records(a),records(b)
            for category in sorted({r['category'] for r in left.values()}):
                ids=sorted(pid for pid,r in left.items() if r['category']==category and pid in right)
                def value(row):
                    return float(row['score']['forgetting_pass'] if category=='淡忘'
                                 else row['score']['all']['complete_strict'])
                vals_a=[value(left[pid]) for pid in ids]
                vals_b=[value(right[pid]) for pid in ids]
                deltas.append({'set':name,'from':a,'to':b,'category':category,**paired_delta(vals_a,vals_b)})
    table={'rows':rows,'paired_deltas':deltas}
    write_json(root/'comparison.json',table)
    categories=sorted({cat for row in rows for cat in row['categories']})
    lines=['# 阶段对照','',
           '| 集合 | 阶段 | '+' | '.join(categories)+' | 新模型调用 |',
           '| --- | --- | '+' | '.join('---:' for _ in categories)+' | ---: |']
    for row in rows:
        values=' | '.join(f"{row['categories'].get(cat,0):.6f}" for cat in categories)
        lines.append(f"| {row['set']} | {row['stage']} | {values} | {row['new_model_calls']} |")
    lines.extend(['','## 相邻阶段配对差值（95% bootstrap CI）','',
                  '| 集合 | 对照 | 类别 | 差值 | 95% CI |','| --- | --- | --- | ---: | --- |'])
    for d in deltas:
        ci=f"[{d['ci'][0]:.6f}, {d['ci'][1]:.6f}]"
        lines.append(f"| {d['set']} | {d['from']}→{d['to']} | {d['category']} | {d['delta']:.6f} | {ci} |")
    (root/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write_comparison_v2(root,names,stages)
    print(root)


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m lab')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('build-eval')
    run=commands.add_parser('run');run.add_argument('--set',required=True);run.add_argument('--preset',required=True);_options(run)
    suite=commands.add_parser('suite');suite.add_argument('--sets',default='dev_a,dev_b');suite.add_argument('--presets',default='B0,B1,P1,P2,P2g,P3,P4,P5,P6');_options(suite)
    compare=commands.add_parser('compare');compare.add_argument('left',type=Path);compare.add_argument('right',type=Path)
    inspect=commands.add_parser('inspect');inspect.add_argument('run',type=Path);inspect.add_argument('--probe',required=True);inspect.add_argument('--variant',type=int,default=0)
    v2=commands.add_parser('comparison-v2');v2.add_argument('suite',type=Path);v2.add_argument('--sets',default='dev_a,dev_b');v2.add_argument('--presets',default='B0,B1,P1,P2,P2g,P3,P4,P5,P6')
    memories=commands.add_parser('memories');memories.add_argument('run',type=Path);memories.add_argument('--at')
    args=parser.parse_args(argv)
    if args.command=='build-eval':
        import subprocess,sys
        return subprocess.call([sys.executable,str(ROOT/'eval_set'/'build.py')],cwd=ROOT)
    if args.command=='run':
        out=args.out or _out(args.set,args.set+'_'+args.preset)
        print(_execute(args,args.set,args.preset,out)['out']);return 0
    if args.command=='suite':_suite(args);return 0
    if args.command=='comparison-v2':
        write_comparison_v2(args.suite,[x.strip() for x in args.sets.split(',') if x.strip()],
                            [x.strip() for x in args.presets.split(',') if x.strip()])
        print(args.suite/'comparison_v2.md');return 0
    if args.command=='compare':
        left=json.loads((args.left/'summary.json').read_text());right=json.loads((args.right/'summary.json').read_text())
        print(json.dumps({'left':left['by_category'],'right':right['by_category']},ensure_ascii=False,indent=2));return 0
    if args.command=='inspect':
        for line in (args.run/'probes.jsonl').read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            if row['probe_id']==args.probe and row['variant_days']==args.variant:
                print(json.dumps(row,ensure_ascii=False,indent=2));return 0
        raise ValueError('probe not found')
    if args.command=='memories':
        if not args.at:print((args.run/'memories.md').read_text(encoding='utf-8'));return 0
        at=datetime.fromisoformat(args.at)
        store=Store(args.run/'memory.sqlite')
        for m in store.snapshot().memories:
            if m['created_at']<=at:
                text=m['text']
                future=[x for x in m['lineage'] if x['kind']=='revise' and datetime.fromisoformat(x['at'])>at]
                for row in sorted(future,key=lambda x:x['at'],reverse=True):text=row['text']
                print(m['id'],text)
        store.close();return 0
    return 1
