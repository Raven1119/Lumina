"""Stable run artifacts; elapsed time stays out of deterministic summaries."""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict,is_dataclass
from pathlib import Path

from memlab.clock import rough_age
from memlab.strength import strength

REQUIRED=('config.json','memory.sqlite','dream_log.jsonl','probes.jsonl','memories.md',
          'curves.json','summary.json','timing.json','report.md')


def _ready(obj):
    if is_dataclass(obj):return _ready(asdict(obj))
    if isinstance(obj,dict):return {k:_ready(v) for k,v in obj.items()}
    if isinstance(obj,(list,tuple)):return [_ready(v) for v in obj]
    if isinstance(obj,float):return round(obj,6)
    if hasattr(obj,'isoformat'):return obj.isoformat()
    return obj


def dumps(obj):return json.dumps(_ready(obj),ensure_ascii=False,sort_keys=True,indent=2)+'\n'


def write_json(path,obj):Path(path).write_text(dumps(obj),encoding='utf-8')


def write_jsonl(path,rows):
    Path(path).write_text(''.join(json.dumps(_ready(r),ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for r in rows),encoding='utf-8')


def memories_markdown(snapshot,now,cfg):
    current=[];dormant=[]
    for m in sorted(snapshot.memories,key=lambda x:(x['created_at'],x['id'])):
        B,pi,sleep=strength(list(m['events']),m['salience'],now,cfg)
        line=[f"## {m['id']}",m['text'],
              f"时间：{rough_age((now-m['created_at']).total_seconds()/86400)}；强度 B={B:.6f}；π={pi:.6f}；salience={m['salience']:.6f}",
              '实体：'+('、'.join(m['entities']) or '无'),
              '来源：'+('、'.join(m['sources']) or '无'),
              'lineage：'+json.dumps(m['lineage'],ensure_ascii=False,sort_keys=True,default=str),'']
        (dormant if sleep else current).extend(line)
    return '# 记忆清单\n\n'+'\n'.join(current)+'\n## 沉睡\n\n'+'\n'.join(dormant)+'\n'


def write_run(out,config,store,now,cfg,dream_log,records,curves,summary,timing):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    write_json(out/'config.json',config)
    write_jsonl(out/'dream_log.jsonl',dream_log)
    write_jsonl(out/'probes.jsonl',records)
    snapshot=store.snapshot(None)
    (out/'memories.md').write_text(memories_markdown(snapshot,now,cfg),encoding='utf-8')
    write_json(out/'curves.json',curves)
    write_json(out/'summary.json',summary)
    write_json(out/'timing.json',timing)
    lines=['# Memory Lab run',f"集合：{config['set']}；预设：{config['preset']}；模型：{config['llm']}",
           f"Dream 新调用：{timing['dream_new_calls']}；缓存命中：{timing['dream_cache_hits']}",
           '', '| 类别 | n | 完全命中 | 95% CI | 只命中干扰 |','| --- | ---: | ---: | --- | ---: |']
    for category,row in summary['by_category'].items():
        lines.append(f"| {category} | {row['n']} | {row['all_complete']:.6f} | {row['all_complete_ci']} | {row['only_distractor']} |")
    if 'measure' in summary:
        from .measure import LEGEND,measure_table,time_table
        blocks=[(cat,block) for cat,block in summary['measure']['by_category'].items()]
        blocks.append(('合计',summary['measure']['overall']))
        lines.extend(['','## 测量 v2','',*LEGEND,'',*measure_table(blocks,'category','类别'),
                      '','### 时间区间','',*time_table([('合计',summary['measure']['overall'])])])
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    missing=[name for name in REQUIRED if not (out/name).exists()]
    if missing:raise AssertionError(f'missing artifacts: {missing}')


def timing_summary(samples,dream_new,dream_hits,model_new,input_tokens,output_tokens):
    ordered=sorted(samples)
    def pct(p):
        return ordered[min(len(ordered)-1,round((len(ordered)-1)*p))] if ordered else 0.0
    return {'probe_seconds':samples,'probe_p50':statistics.median(samples) if samples else 0.0,
            'probe_p95':pct(.95),'dream_new_calls':dream_new,'dream_cache_hits':dream_hits,
            'new_model_calls':model_new,'new_input_tokens':input_tokens,'new_output_tokens':output_tokens}
