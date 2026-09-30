"""Export grounded memory evidence and summarize manually reviewed error labels.

The model is never used to grade. A reviewer reads the evidence JSON and supplies
one boolean per error category for every exported memory.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

CATEGORIES=('fabricated','speaker_reversed','overgeneralized','date_error','person_confusion')


def export(run:Path,out:Path,cap:int=60):
    conn=sqlite3.connect(run/'memory.sqlite');conn.row_factory=sqlite3.Row
    memories=conn.execute('SELECT id,text FROM memories ORDER BY CAST(SUBSTR(id,2) AS INTEGER) LIMIT ?',(cap,)).fetchall()
    rows=[]
    for m in memories:
        sources=conn.execute('''SELECT c.turn_id,c.role,c.time,c.text FROM memory_sources s
            LEFT JOIN cold_turns c ON c.turn_id=s.turn_id WHERE s.memory_id=? ORDER BY c.time''',(m['id'],)).fetchall()
        rows.append({'id':m['id'],'text':m['text'],'sources':[dict(x) for x in sources]})
    conn.close();out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(rows,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    return rows


def summarize(evidence:Path,labels:Path,out:Path):
    rows=json.loads(evidence.read_text(encoding='utf-8'))
    judged=json.loads(labels.read_text(encoding='utf-8'))
    if set(judged)!={x['id'] for x in rows}:raise ValueError('every exported memory needs a direct label')
    for mid,flags in judged.items():
        if set(flags)!=set(CATEGORIES) or not all(isinstance(v,bool) for v in flags.values()):
            raise ValueError(f'incomplete categories: {mid}')
    n=len(rows);counts={cat:sum(judged[x['id']][cat] for x in rows) for cat in CATEGORIES}
    result={'n':n,'counts':counts,'rates':{cat:counts[cat]/n if n else None for cat in CATEGORIES},
            'method':'Codex direct evidence review; no grading model calls'}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path);ap.add_argument('--evidence',required=True,type=Path)
    ap.add_argument('--labels',type=Path);ap.add_argument('--summary',type=Path)
    args=ap.parse_args()
    if args.run:export(args.run,args.evidence)
    if args.labels:
        if not args.summary:ap.error('--summary required with --labels')
        print(json.dumps(summarize(args.evidence,args.labels,args.summary),ensure_ascii=False))
