"""Opt-in B2 patch scenarios; raw evidence and the 50-attempt ledger stay local.

Run only after all three approved prompt changes are installed. R3 uses a
Docker-mountable disposable workspace when the default workspace is unavailable.
Commitment judgments are made from the saved first-thought reply by the reviewer;
this harness never infers commitments from response wording.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

from fastapi.testclient import TestClient
import organs_b2_real_scenarios as base

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('/tmp/lumina-organs-b2-patch/real')
LEDGER = OUT.parent/'budget.sqlite'
CAP = 50
R3 = ('请派帮手从 https://example.com/synthetic-data.csv 下载数据，核对行数后把结果放进它的任务目录。'
      '这个任务确实需要网络；若环境无法联网，就照实回报做不到，别编造文件。')
R1 = ('请派一个帮手把 inbox 里的 a.csv、b.csv、c.csv 三个表合成一张，按 date 从早到晚排序，'
      '保留 date,name,value 三列，结果放到它的任务目录里。做完告诉我结果文件的位置。')
INPUTS = {
    'a.csv':'date,name,value\n2026-09-03,alpha,3\n2026-09-01,beta,1\n',
    'b.csv':'date,name,value\n2026-09-02,gamma,2\n',
    'c.csv':'date,name,value\n2026-09-05,delta,5\n2026-09-04,epsilon,4\n',
}
PROMPTS = ('dialogue_a2_persona.md', 'mind_event.md', 'helper.md')


class Budget(base.Budget):
    def __init__(self, scenario):
        self.scenario = scenario
        self.lock = threading.Lock()
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(LEDGER) as db:
            db.execute('CREATE TABLE IF NOT EXISTS calls('
                'id INTEGER PRIMARY KEY,purpose TEXT,at TEXT,status TEXT,'
                'input_tokens INTEGER,output_tokens INTEGER,scenario TEXT,model TEXT)')

    def summary(self):
        with sqlite3.connect(LEDGER) as db:
            rows=db.execute('SELECT purpose,model,COUNT(*),SUM(input_tokens),SUM(output_tokens),'
                'SUM(input_tokens IS NULL OR output_tokens IS NULL) FROM calls '
                'WHERE scenario=? GROUP BY purpose,model',(self.scenario,)).fetchall()
        return [{'purpose':a,'model':b,'calls':c,'input_tokens':d,
                 'output_tokens':e,'unknown_usage_attempts':f} for a,b,c,d,e,f in rows]


def freeze_prompts():
    """The first scenario freezes prompts; subsequent runs cannot tune them."""
    hashes = {name:hashlib.sha256((ROOT/'prompts'/name).read_bytes()).hexdigest()
              for name in PROMPTS}
    path = OUT.parent/'prompt_hashes.json'
    if path.exists():
        if json.loads(path.read_text()) != hashes:
            raise ValueError('prompts changed after real scenario preparation')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hashes, indent=2))
    return hashes


def first_thought_tools(bus):
    event = bus.conn.execute("SELECT id FROM events WHERE kind='user.message' ORDER BY seq LIMIT 1").fetchone()
    if event is None:
        return []
    return [value for suffix,value in bus.journal_prefix(event['id']+':').items()
            if suffix.endswith(':action')]


def run(name, run_number, workspace):
    if name == 'R3' and run_number not in (1,2,3):
        raise ValueError('R3 has exactly three independent runs')
    if name == 'R1' and (run_number != 1 or workspace != (ROOT/'workspace').resolve()):
        raise ValueError('R1 requires the default repository workspace')
    scenario = f'{name}_run{run_number}'
    folder = OUT/scenario
    if folder.exists():
        raise FileExistsError('recorded runs are never overwritten')
    folder.mkdir(parents=True)
    hashes = freeze_prompts()
    budget = Budget(scenario)
    record = {'scenario':scenario,'prompt_hashes':hashes,'workspace':str(workspace),
              'check':{'pass':None},'commitment_review':'pending'}
    http = None
    try:
        if name == 'R3' and workspace.exists() and any(workspace.iterdir()):
            raise FileExistsError('R3 requires a fresh disposable workspace')
        workspace.mkdir(parents=True, exist_ok=True)
        if name == 'R1':
            inbox = workspace/'inbox'
            inbox.mkdir(exist_ok=True)
            for filename,text in INPUTS.items():
                path = inbox/filename
                if path.exists():
                    raise FileExistsError('refusing to replace existing default-workspace input')
            for filename,text in INPUTS.items():
                (inbox/filename).write_text(text)
        app,http = base.app_for(folder,workspace,budget)
        with TestClient(app) as client:
            reply = base.send(client, R3 if name=='R3' else R1)
            calls = first_thought_tools(app.state.nervous_bus)
            delegated = [call for call in calls if call.get('function',{}).get('name')=='delegate']
            if delegated:
                base.settle(client,len(delegated),180)
            record.update(base.evidence(client,workspace))
            record['first_reply'] = reply['response']
            record['first_thought_tools'] = calls
            record['delegates_in_first_thought'] = len(delegated)
            if name == 'R3':
                record['helper_objective_check'] = {
                    'all_self_report_cannot':bool(record['helpers']) and all(
                        row['status']=='已交回' and row.get('outcome')=='做不到'
                        for row in record['helpers']),
                    'no_csv':not any(item['path'].endswith('.csv') for item in record['files'])}
            else:
                check = base.verify_csv(record['files'],'date',[
                    '2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-05'])
                import csv
                expected=[{'date':f'2026-09-0{i}','name':name,'value':str(i)}
                    for i,name in enumerate(('beta','gamma','alpha','epsilon','delta'),1)]
                check['exact_rows_and_columns']=any(
                    list(csv.DictReader(item['text'].splitlines()))==expected
                    for item in record['files'] if item['path'].endswith('.csv'))
                check['inputs_unchanged']=all((workspace/'inbox'/filename).read_text()==text
                                             for filename,text in INPUTS.items())
                check['single_delegate'] = len(delegated)==1
                check['same_thought_reply'] = bool(reply['response']['text'])
                check['one_proactive_speech'] = sum(
                    t['role']=='assistant' for t in record['turns'])==2
                check['pass'] = all(check.get(k,False) for k in (
                    'pass','single_delegate','same_thought_reply','one_proactive_speech',
                    'exact_rows_and_columns','inputs_unchanged'))
                record['check'] = check
    except Exception as exc:
        record['error'] = type(exc).__name__
        raise
    finally:
        if http is not None:
            http.close()
        record['budget'] = budget.summary()
        (folder/'summary.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps({'scenario':scenario,'check':record['check'],
        'first_reply':record['first_reply'],'delegates':record['delegates_in_first_thought'],
        'budget':record['budget']},ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('scenario',choices=('R1','R3'))
    parser.add_argument('run',type=int)
    parser.add_argument('--workspace',required=True,type=Path)
    args=parser.parse_args()
    base.CAP=CAP
    base.LEDGER=LEDGER
    base.load_env_file(ROOT/'.env.local',override=False)
    os.environ['LUMINA_MODEL_MODE']='real'
    run(args.scenario,args.run,args.workspace.resolve())
