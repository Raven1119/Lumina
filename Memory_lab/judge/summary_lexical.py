"""Check whether an answer's rolling summary still contains marked facts' words."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Conversation_Memory.engine.usage import content_grams


BOILERPLATE=('对话里从未把两件事联系起来','只靠共现相连',
             '表面用词不同，结构相同','多次零散出现')


def fact_grams(summary:str):
    for phrase in BOILERPLATE:summary=summary.replace(phrase,'')
    return content_grams(re.sub(r'（[^）]*作为噪声[^）]*）','',summary))


def inspect(run:Path,gold:Path):
    spec=json.loads(gold.read_text(encoding='utf-8'))
    plants=spec['plants'];probes={p['id']:p for p in spec['probes']}
    rows=[]
    for line in (run/'probes.jsonl').read_text(encoding='utf-8').splitlines():
        row=json.loads(line)
        if row['variant_days']!=0 or row.get('not_scored'):continue
        probe=probes[row['probe_id']]
        tags=set(sum(probe.get('must_surface_tags',[]),[]))
        summaries=[p['summary'] for p in plants if tags.intersection(p['tags'])]
        if not summaries:continue
        keywords=set().union(*(fact_grams(s) for s in summaries))
        summary=(row['answer']['context']['summary'] if row.get('answer') else row.get('rolling_summary')) or ''
        hits=sorted(keywords & content_grams(summary))
        rows.append({'probe_id':row['probe_id'],'gold_summaries':summaries,
                     'summary_grams':hits,'has_main_words':len(hits)>=2})
    return {'n':len(rows),'missing':sum(not r['has_main_words'] for r in rows),
            'criterion':'at least two content grams shared with planted facts after removing rubric boilerplate',
            'rows':rows}


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);ap.add_argument('gold',type=Path)
    ap.add_argument('out',type=Path);args=ap.parse_args()
    value=inspect(args.run,args.gold)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(value,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    print(args.out,value['missing'],'/',value['n'])
