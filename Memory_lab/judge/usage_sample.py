"""Create a deterministic balanced review sheet from dev_a shadow replies."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Conversation_Memory.engine.usage import used_memories


def build(shadow_log:Path,out:Path):
    rows=[]
    for line in shadow_log.read_text(encoding='utf-8').splitlines():
        entry=json.loads(line)
        for memory in entry['memories']:
            rows.append({'id':f"{entry['turn_id']}:{memory['id']}",
                         'message':entry['message'],'context_texts':entry['context_texts'],
                         'answer':entry['answer'],'memory':memory,
                         'detected':memory['id'] in entry['used'],'label':None})
    rng=random.Random(20260926)
    positives=[row for row in rows if row['detected']]
    negatives=[row for row in rows if not row['detected']]
    if not positives or len(negatives)<75:raise ValueError('insufficient pairs')
    chosen=rng.sample(positives,min(75,len(positives)))
    # Include the threshold-1 boundary so the three-way comparison is informative.
    boundary=[row for row in negatives if row['memory']['id'] in
              used_memories(row['answer'],row['context_texts'],[row['memory']],1)]
    hard=rng.sample(boundary,min(75,len(boundary)))
    rest=[row for row in negatives if row not in hard]
    chosen.extend(hard+rng.sample(rest,75-len(hard)))
    rng.shuffle(chosen)
    out.write_text(json.dumps(chosen,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    return chosen


def score(rows):
    if not rows or any(row.get('label') not in (True,False) for row in rows):
        raise ValueError('reviewed yes/no labels required')
    out={}
    for threshold in (1,2,3):
        tp=fp=fn=0
        for row in rows:
            predicted=row['memory']['id'] in used_memories(row['answer'],row['context_texts'],
                                                           [row['memory']],threshold)
            actual=row['label']
            tp+=predicted and actual;fp+=predicted and not actual;fn+=not predicted and actual
        precision=tp/(tp+fp) if tp+fp else 0
        recall=tp/(tp+fn) if tp+fn else 0
        f1=2*precision*recall/(precision+recall) if precision+recall else 0
        out[threshold]={'precision':precision,'recall':recall,'f1':f1,'tp':tp,'fp':fp,'fn':fn}
    return out


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--shadow-log',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    print(f'{len(build(args.shadow_log,args.out))} pairs written to {args.out}')
