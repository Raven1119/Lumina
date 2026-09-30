"""Organ B synthetic /api/chat scenarios with a persisted HTTP-attempt budget.

Run manually. No runtime body or credential is written to this ledger.
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import httpx
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from core.env_loader import load_env_file
from core.main import create_app
from core.model_client import DeepSeekAnthropicModelClient
from Execution.deepseek_model import DeepSeekModel
from model_policy import model_for, OPENAI_CHAT_URL, ANTHROPIC_BASE_URL

OUT=Path(os.environ.get('LUMINA_ORGANS_B_RESULTS','/tmp/lumina-organs-b/real'))
WORKSPACE=Path(os.environ.get('LUMINA_ORGANS_B_DOCKER_WORKSPACE','/tmp/lumina-organs-b-workspace'))
CAP=250

class Ledger:
    def __init__(self,path):
        self.path=path;self.lock=threading.Lock();self.scenario='setup'
        path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, at TEXT,
                scenario TEXT, purpose TEXT, model TEXT, status TEXT, http_code INTEGER,
                input_tokens INTEGER, output_tokens INTEGER)''')
    def reserve(self,purpose,model):
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            n=db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            if n>=CAP:raise RuntimeError('organ_b_model_call_cap_reached')
            cur=db.execute('INSERT INTO attempts(at,scenario,purpose,model,status) VALUES(?,?,?,?,?)',
                (datetime.now(timezone.utc).isoformat(),self.scenario,purpose,model,'reserved'))
            return cur.lastrowid
    def finish(self,index,code=None,usage=None):
        usage=usage if isinstance(usage,dict) else {}
        input_tokens=usage.get('input_tokens',usage.get('prompt_tokens'))
        output_tokens=usage.get('output_tokens',usage.get('completion_tokens'))
        with self.lock,sqlite3.connect(self.path) as db:
            db.execute('UPDATE attempts SET status=?,http_code=?,input_tokens=?,output_tokens=? WHERE id=?',
                ('http_response' if code is not None else 'transport_unknown',code,
                 input_tokens if type(input_tokens) is int else None,
                 output_tokens if type(output_tokens) is int else None,index))
    def summary(self):
        with sqlite3.connect(self.path) as db:
            rows=db.execute('SELECT scenario,purpose,model,COUNT(*),SUM(input_tokens),SUM(output_tokens) FROM attempts GROUP BY scenario,purpose,model').fetchall()
            total=db.execute('SELECT COUNT(*),SUM(input_tokens),SUM(output_tokens) FROM attempts').fetchone()
        return {'rows':[{'scenario':a,'purpose':b,'model':c,'calls':d,'input_tokens':e,'output_tokens':f} for a,b,c,d,e,f in rows],
                'total':{'calls':total[0],'input_tokens':total[1],'output_tokens':total[2]}}

class BudgetTransport(httpx.BaseTransport):
    def __init__(self,ledger):
        self.ledger=ledger;self.forward=httpx.Client(timeout=100)
    def handle_request(self,request):
        body=json.loads(request.content)
        system=body.get('system','')
        if isinstance(system,list):system=str(system)
        purpose=('helper' if 'chat/completions' in str(request.url) else
                 'mind_event' if '【这次是什么叫醒了你】' in system else
                 'language' if '你是林素的语言器官' in system else 'mind_dialogue')
        index=self.ledger.reserve(purpose,body.get('model','unknown'))
        try:
            response=self.forward.send(request)
            response.read()
            try:usage=response.json().get('usage')
            except (ValueError,AttributeError):usage=None
            self.ledger.finish(index,response.status_code,usage)
            return response
        except Exception:
            self.ledger.finish(index)
            raise
    def close(self):self.forward.close()

def make_app(folder,workspace,ledger):
    key=os.environ.get('DEEPSEEK_API_KEY','').strip()
    if not key:raise RuntimeError('missing_deepseek_key')
    transport=BudgetTransport(ledger)
    http=httpx.Client(transport=transport,timeout=105)
    chat=DeepSeekAnthropicModelClient(key,ANTHROPIC_BASE_URL,
        model_for('chat'),http_client=http)
    def helper_post(payload):
        response=http.post(OPENAI_CHAT_URL,headers={'Authorization':'Bearer '+key},json=payload)
        response.raise_for_status()
        return response.json()
    DeepSeekModel._post=staticmethod(helper_post)
    app=create_app(draft_store_path=folder/'hot.jsonl',cold_draft_path=folder/'cold.jsonl',
        compaction_state_path=folder/'compaction.json',memory_dir=folder/'memory',
        model_client=chat,env_file_path=None,enable_compaction=False,recall_enabled=False,
        nervous_db_path=folder/'nervous.sqlite',lumina_config={'workspace':{'path':str(workspace)}})
    return app,http

def send(client,text):
    response=client.post('/api/chat',json={'message':text})
    response.raise_for_status()
    return response.json()

def wait_for(client,predicate,timeout=240):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic()<deadline:
        last=client.get('/api/status').json()['lumina']
        if predicate(last):return last
        time.sleep(.4)
    return last

def history(client):
    return [(turn['role'],turn['content']) for turn in client.get('/api/history?limit=100').json()['turns']]

def settle(client, count, timeout=180):
    live=wait_for(client,lambda s:len(s['helpers'])>=count and all(
        h['status'] in ('已完成','失败','被拉闸','已取消') for h in s['helpers']),timeout)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        if not client.app.state.nervous_bus.pending('mind'):
            time.sleep(.3)
            if not client.app.state.nervous_bus.pending('mind'):break
        time.sleep(.2)
    return live

def outputs(workspace, helpers):
    found=[]
    for helper in helpers:
        for rel in helper.get('outputs',[]):
            path=workspace/rel
            if path.is_file() and path.suffix in ('.csv','.txt'):
                found.append({'path':rel,'text':path.read_text(encoding='utf-8')})
    return found

def r1(ledger):
    folder=OUT/('R1_'+os.environ.get('LUMINA_SCENARIO_RUN','run3'))
    folder.mkdir(parents=True,exist_ok=True)
    workspace=WORKSPACE/'R1';(workspace/'inbox').mkdir(parents=True,exist_ok=True)
    samples={'a.csv':'date,name,value\n2026-09-03,alpha,3\n2026-09-01,beta,1\n',
             'b.csv':'date,name,value\n2026-09-02,gamma,2\n',
             'c.csv':'date,name,value\n2026-09-05,delta,5\n2026-09-04,epsilon,4\n'}
    for name,body in samples.items():(workspace/'inbox'/name).write_text(body,encoding='utf-8')
    app,http=make_app(folder,workspace,ledger)
    ledger.scenario='R1'
    with TestClient(app) as client:
        first=send(client,'请派一个帮手把 inbox 里的 a.csv、b.csv、c.csv 三个表合成一张，按 date 从早到晚排序，保留 date,name,value 三列，结果放到它的任务目录里。做完告诉我结果文件的位置。')
        live=settle(client,1)
        turns=history(client)
        return {'first_response':first['response']['text'],'helpers':live['helpers'] if live else [],
                'history':turns,'outputs':outputs(workspace,live['helpers'] if live else [])}

def r2(ledger):
    folder=OUT/('R2_'+os.environ.get('LUMINA_SCENARIO_RUN','run1'))
    folder.mkdir(parents=True,exist_ok=True)
    workspace=WORKSPACE/'R2';(workspace/'inbox').mkdir(parents=True,exist_ok=True)
    (workspace/'inbox/events.csv').write_text('id,event_date,created_date\nA,2026-01-03,2026-01-01\nB,2026-01-01,2026-01-03\nC,2026-01-02,2026-01-02\n',encoding='utf-8')
    app,http=make_app(folder,workspace,ledger);ledger.scenario='R2'
    with TestClient(app) as client:
        first=send(client,'请派帮手把 inbox/events.csv 按日期从早到晚排序，结果放它的任务目录。表里有 event_date 和 created_date 两列，我尚未指定用哪一列；请让帮手遇到这个歧义时问你，然后再决定。')
        before=wait_for(client,lambda s:any(h['status'] in ('在等答复','已完成','失败') for h in s['helpers']),timeout=150)
        follow=None
        if before and any(h['status']=='在等答复' for h in before['helpers']):
            follow=send(client,'刚才那张表按 event_date 排序；请把这个决定答复给帮手。')
        live=settle(client,1)
        return {'first_response':first['response']['text'],'follow_response':follow['response']['text'] if follow else None,
                'before':before['helpers'] if before else [],'helpers':live['helpers'] if live else [],
                'history':history(client),'outputs':outputs(workspace,live['helpers'] if live else [])}

def r3(ledger):
    folder=OUT/'R3';folder.mkdir(parents=True,exist_ok=True)
    workspace=WORKSPACE/'R3';workspace.mkdir(parents=True,exist_ok=True)
    app,http=make_app(folder,workspace,ledger);ledger.scenario='R3'
    with TestClient(app) as client:
        first=send(client,'请派帮手从 https://example.com/synthetic-data.csv 下载数据，核对行数后把结果放进它的任务目录。这个任务确实需要网络；若环境无法联网，就照实回报做不到，别编造文件。')
        live=settle(client,1)
        return {'first_response':first['response']['text'],'helpers':live['helpers'] if live else [],
                'history':history(client),'outputs':outputs(workspace,live['helpers'] if live else [])}

def r4(ledger):
    folder=OUT/('R4_'+os.environ.get('LUMINA_SCENARIO_RUN','run1'))
    folder.mkdir(parents=True,exist_ok=True)
    workspace=WORKSPACE/'R4';(workspace/'inbox').mkdir(parents=True,exist_ok=True)
    for n in range(1,4):(workspace/'inbox'/f'part{n}.txt').write_text(f'part-{n}\n',encoding='utf-8')
    app,http=make_app(folder,workspace,ledger);ledger.scenario='R4'
    with TestClient(app) as client:
        messages=[f'请派一个帮手把 inbox/part{n}.txt 的内容转成大写，写到它自己的任务目录里，做完回报文件路径。这是独立的第 {n} 件事。' for n in range(1,4)]
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures=[executor.submit(send,client,message) for message in messages]
            peak=0;queued=False;deadline=time.monotonic()+220
            while time.monotonic()<deadline:
                state=client.get('/api/status').json()['lumina']
                peak=max(peak,sum(h['status'] in ('进行中','在等答复') for h in state['helpers']))
                queued=queued or any(h['status']=='排队中' for h in state['helpers'])
                if len(state['helpers'])>=3 and all(h['status'] in ('已完成','失败','被拉闸','已取消') for h in state['helpers']):break
                time.sleep(.2)
            replies=[f.result(timeout=100)['response']['text'] for f in futures]
        live=settle(client,3)
        return {'first_responses':replies,'helpers':live['helpers'] if live else [],
                'peak':peak,'queued_seen':queued,'history':history(client),
                'outputs':outputs(workspace,live['helpers'] if live else [])}

def r5(ledger):
    folder=OUT/os.environ.get('LUMINA_R1_BASE','R1_run4');workspace=WORKSPACE/'R1'
    app,http=make_app(folder,workspace,ledger);ledger.scenario='R5'
    with TestClient(app) as client:
        answer=send(client,'刚才那张表弄好了吗？把结果位置告诉我。')
        return {'response':answer['response']['text'],'helpers':client.get('/api/status').json()['lumina']['helpers'],
                'history':history(client)}

def main():
    load_env_file(ROOT/'.env.local',override=False)
    os.environ['LUMINA_MODEL_MODE']='real'
    OUT.mkdir(parents=True,exist_ok=True)
    ledger=Ledger(OUT/'budget.sqlite')
    task=sys.argv[1] if len(sys.argv)>1 else 'R1'
    scenarios={'R1':r1,'R2':r2,'R3':r3,'R4':r4,'R5':r5}
    if task not in scenarios:raise ValueError('unknown_scenario')
    result=scenarios[task](ledger)
    folder_name=(task+'_'+os.environ.get('LUMINA_SCENARIO_RUN',
                 'run3' if task=='R1' else 'run1')) if task in ('R1','R2','R4') else task
    (OUT/(folder_name+'_summary.json')).write_text(json.dumps({'result':result,'budget':ledger.summary()},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'scenario':task,'helpers':[(h['id'],h['status']) for h in result['helpers']],
                      'turns':len(result['history']),'outputs':len(result.get('outputs',[])),
                      'budget':ledger.summary()['total']},ensure_ascii=False))
if __name__=='__main__':main()
