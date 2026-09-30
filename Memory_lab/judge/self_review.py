"""Summarize direct Codex pairwise reviews without pretending to be a model judge."""
from __future__ import annotations

import argparse
import json
from collections import Counter,defaultdict
from pathlib import Path


def run(round_dir:Path,decisions_path:Path):
    meta=json.loads((round_dir/'meta.json').read_text(encoding='utf-8'))
    decisions=json.loads(decisions_path.read_text(encoding='utf-8'))
    labels=meta['labels']
    out_dir=round_dir/'judge_self';out_dir.mkdir(exist_ok=True)
    overall=Counter();by_set=defaultdict(Counter);by_cat=defaultdict(Counter)
    wrong=Counter();total=0;notes=[]
    for set_name in meta['sets']:
        key=json.loads((round_dir/f'key_{set_name}.json').read_text(encoding='utf-8'))
        packets=[json.loads((round_dir/f'packet_{set_name}_{order}.json').read_text(encoding='utf-8'))
                 for order in (1,2)]
        outputs=[[],[]]
        for i,item in enumerate(packets[0]['items']):
            item_key=key[item['id']]
            tag=item_key['probe']+(f"+{item_key['variant']}" if item_key['variant'] else '')
            lookup=f'{set_name}:{tag}'
            decision=decisions.get(lookup)
            if decision is None:
                if item['X']!=item['Y']:raise ValueError(f'missing direct review: {lookup}')
                decision={'winner':'tie','wrong':[],'reason':'两份回答文字相同'}
            winner=decision['winner']
            if winner not in (*labels,'tie'):raise ValueError(f'bad winner: {lookup}')
            flags=set(decision.get('wrong',[]))
            if not flags<=set(labels):raise ValueError(f'bad wrong flags: {lookup}')
            reason=decision.get('reason','')
            if not reason:raise ValueError(f'missing reason: {lookup}')
            for order in (1,2):
                pair=packets[order-1]['items'][i]
                x_label=item_key['X'] if order==1 else item_key['Y']
                y_label=item_key['Y'] if order==1 else item_key['X']
                outputs[order-1].append({'id':item['id'],
                                         'X':{'wrong':x_label in flags},'Y':{'wrong':y_label in flags},
                                         'prefer':'tie' if winner=='tie' else 'X' if winner==x_label else 'Y',
                                         'reason':reason})
                assert pair['X']==(item['X'] if order==1 else item['Y'])
            overall[winner]+=1;by_set[set_name][winner]+=1;by_cat[item_key['category']][winner]+=1
            for label in labels:wrong[label]+=label in flags
            total+=1
            if winner!='tie' or flags:notes.append(f'- {set_name} {tag}［{item_key["category"]}］：{winner}；{reason}'+
                                                   (f'；说错：{",".join(sorted(flags))}' if flags else ''))
        for order in (1,2):
            (out_dir/f'out_{set_name}_{order}.json').write_text(
                json.dumps({'items':outputs[order-1],'schema':'self_review_preference_wrong_v1'},ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    summary={'labels':labels,'n':total,'pairs':dict(overall),'wrong':{l:wrong[l]/total for l in labels},
             'by_set':{s:dict(c) for s,c in by_set.items()},
             'by_category':{s:dict(c) for s,c in by_cat.items()},
             'method':'single Codex direct review; swapped-order outputs mirror the same decision',
             'missing_rubric_scores':True}
    (out_dir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    lines=[f'# Codex 直接评审：{labels[0]} vs {labels[1]}','',
           '单一评审者直接审阅不同回答；两份 X/Y 交换顺序的输出使用同一判断，不构成独立重复。'
           '仅记录成对偏好和说错往事标记；未伪造逐要点分数与活人感分数。结论待独立复核。','',
           f'| 范围 | n | {labels[0]} 胜 | {labels[1]} 胜 | 平 | 说错 {labels[0]} | 说错 {labels[1]} |',
           '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    def line(name,c,n,w):
        return f'| {name} | {n} | {c[labels[0]]} | {c[labels[1]]} | {c["tie"]} | {w[labels[0]]/n:.3f} | {w[labels[1]]/n:.3f} |'
    lines.append(line('合计',overall,total,wrong))
    for s in meta['sets']:
        c=by_set[s];n=sum(c.values());w=Counter()
        for lookup,d in decisions.items():
            if lookup.startswith(s+':'):
                for label in d.get('wrong',[]):w[label]+=1
        lines.append(line(s,c,n,w))
    lines+=['','## 不同回答的直接判断','',*notes,'']
    (out_dir/'summary.md').write_text('\n'.join(lines),encoding='utf-8')
    print(out_dir/'summary.md')
    return summary


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('round_dir',type=Path);ap.add_argument('decisions',type=Path)
    args=ap.parse_args();run(args.round_dir,args.decisions)
