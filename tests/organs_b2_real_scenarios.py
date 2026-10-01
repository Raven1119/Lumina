"""Opt-in B2 real scenarios. Synthetic inputs; raw runs stay outside Git.

Usage: PATH=/tmp/lumina-organs-b/bin:$PATH python tests/organs_b2_real_scenarios.py R1 1
The shared SQLite ledger reserves each HTTP attempt before sending it.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
import threading
import time
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
from Nervous.bus import unpack

OUT=Path('/tmp/lumina-organs-b2/real')
WORKSPACE=Path('/mnt/c/Users/wmywb/AppData/Local/Temp/lumina-organs-b2-real/scenarios')
LEDGER=Path('/tmp/lumina-organs-b2/budget.sqlite')
CAP=150
TERMINAL={'已交回','失败','被拉闸','已取消'}


class Budget:
    def __init__(self,scenario):
        self.scenario=scenario
        self.lock=threading.Lock()
        with sqlite3.connect(LEDGER) as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(calls)')}
            if 'scenario' not in columns:db.execute('ALTER TABLE calls ADD COLUMN scenario TEXT')
            if 'model' not in columns:db.execute('ALTER TABLE calls ADD COLUMN model TEXT')

    def reserve(self,purpose,model):
        with self.lock,sqlite3.connect(LEDGER) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]>=CAP:
                raise RuntimeError('B2 model call cap reached')
            return db.execute('INSERT INTO calls(purpose,at,status,scenario,model) VALUES(?,?,?,?,?)',
                (purpose,datetime.now(timezone.utc).isoformat(),'reserved',self.scenario,model)).lastrowid

    def finish(self,index,status,usage=None):
        usage=usage if isinstance(usage,dict) else {}
        with self.lock,sqlite3.connect(LEDGER) as db:
            db.execute('UPDATE calls SET status=?,input_tokens=?,output_tokens=? WHERE id=?',
                (status,usage.get('prompt_tokens',usage.get('input_tokens')),
                 usage.get('completion_tokens',usage.get('output_tokens')),index))

    def summary(self):
        with sqlite3.connect(LEDGER) as db:
            rows=db.execute('SELECT purpose,model,COUNT(*),SUM(input_tokens),SUM(output_tokens) '
                            'FROM calls WHERE scenario=? GROUP BY purpose,model',
                            (self.scenario,)).fetchall()
        return [{'purpose':a,'model':b,'calls':c,'input_tokens':d or 0,
                 'output_tokens':e or 0} for a,b,c,d,e in rows]


class MeteredTransport(httpx.BaseTransport):
    def __init__(self,budget):
        self.budget=budget;self.forward=httpx.Client(timeout=105)

    def handle_request(self,request):
        body=json.loads(request.content)
        system=body.get('system','')
        if not system and isinstance(body.get('messages'),list):
            system=body['messages'][0].get('content','')
        if isinstance(system,list):system=str(system)
        purpose=('helper' if system.startswith('你是林素的帮手') else
                 'mind_event' if '【这次是什么叫醒了你】' in system else
                 'language' if '你是林素的语言器官' in system else 'mind_dialogue')
        index=self.budget.reserve(purpose,body.get('model','unknown'))
        try:
            result=self.forward.send(request);result.read()
            try:usage=result.json().get('usage')
            except (ValueError,AttributeError):usage=None
            self.budget.finish(index,str(result.status_code),usage)
            return result
        except Exception:
            self.budget.finish(index,'transport_unknown')
            raise

    def close(self):self.forward.close()


def app_for(folder,workspace,budget):
    key=os.environ.get('DEEPSEEK_API_KEY','').strip()
    if not key:raise RuntimeError('missing DeepSeek key')
    transport=MeteredTransport(budget)
    http=httpx.Client(transport=transport,timeout=110)
    chat=DeepSeekAnthropicModelClient(key,ANTHROPIC_BASE_URL,model_for('chat'),
                                     http_client=http,max_tokens=393216)
    def helper_post(body):
        reply=http.post(OPENAI_CHAT_URL,
            headers={'Authorization':'Bearer '+key},json=body)
        reply.raise_for_status()
        return reply.json()
    DeepSeekModel._post=staticmethod(helper_post)
    app=create_app(draft_store_path=folder/'hot.jsonl',cold_draft_path=folder/'cold.jsonl',
        compaction_state_path=folder/'compaction.json',memory_dir=folder/'memory',
        nervous_db_path=folder/'nervous.sqlite',model_client=chat,env_file_path=None,
        enable_compaction=False,recall_enabled=False,
        lumina_config={'workspace':{'path':str(workspace)}})
    return app,http


def send(client,message):
    reply=client.post('/api/chat',json={'message':message})
    reply.raise_for_status()
    return reply.json()


def wait_for(client,predicate,timeout=180):
    deadline=time.monotonic()+timeout;last=None
    while time.monotonic()<deadline:
        last=client.get('/api/status').json()['lumina']
        if predicate(last):return last
        time.sleep(.35)
    return last


def settle(client,count,timeout=180):
    state=wait_for(client,lambda s:len(s['helpers'])>=count and all(
        row['status'] in TERMINAL for row in s['helpers']),timeout)
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        if not client.app.state.nervous_bus.pending('mind'):
            time.sleep(.25)
            if not client.app.state.nervous_bus.pending('mind'):break
        time.sleep(.2)
    return state


def evidence(client,workspace):
    bus=client.app.state.nervous_bus
    journal=bus.conn.execute('SELECT id,body,digest FROM journal ORDER BY id').fetchall()
    tools=[];errors=[];noticed=[]
    from Conversation_Memory.answer import parse_dialogue
    for row in journal:
        value=unpack(row['body'],row['digest'])
        if row['id'].endswith(':action') and isinstance(value,dict):
            tools.append({'name':value.get('function',{}).get('name'),
                          'arguments':value.get('function',{}).get('arguments')})
        if row['id'].endswith(':result') and isinstance(value,dict) and value.get('error_kind'):
            errors.append({'kind':value['error_kind'],'text':value['text']})
        if ':step:' in row['id'] and row['id'].endswith(':response'):
            raw=value.get('raw') if isinstance(value,dict) else None
            if isinstance(raw,dict):
                content=raw.get('choices',[{}])[0].get('message',{}).get('content')
                if isinstance(content,str):
                    parsed=parse_dialogue(content)
                    if parsed['reply']:noticed.append(parsed['noticed'])
    state=client.get('/api/status').json()['lumina']
    turns=[{'role':turn['role'],'text':turn['content']} for turn in
           client.get('/api/history?limit=100').json()['turns']]
    files=[]
    for helper in state['helpers']:
        for relative in helper.get('outputs',[]):
            path=workspace/relative
            if path.is_file() and path.suffix in ('.csv','.txt'):
                files.append({'path':relative,'text':path.read_text(encoding='utf-8')})
    return {'helpers':state['helpers'],'turns':turns,'tools':tools,'tool_errors':errors,
            'noticed':noticed,'files':files}


def verify_csv(files,column,expected):
    for item in files:
        if item['path'].endswith('.csv'):
            rows=list(csv.DictReader(item['text'].splitlines()))
            if len(rows)==len(expected) and [row[column] for row in rows]==expected:
                return {'pass':True,'path':item['path'],'rows':len(rows)}
    return {'pass':False,'expected':expected}


def run_scenario(name,run):
    if not 1<=run<=3:raise ValueError('at most three runs per scenario')
    scenario=f'{name}_run{run}'
    folder=OUT/scenario;workspace=WORKSPACE/scenario
    if folder.exists():raise FileExistsError('scenario run already recorded')
    folder.mkdir(parents=True);workspace.mkdir(parents=True)
    inbox=workspace/'inbox';inbox.mkdir()
    if name in ('R1','R5','R6'):
        for filename,text in {
            'a.csv':'date,name,value\n2026-09-03,alpha,3\n2026-09-01,beta,1\n',
            'b.csv':'date,name,value\n2026-09-02,gamma,2\n',
            'c.csv':'date,name,value\n2026-09-05,delta,5\n2026-09-04,epsilon,4\n'}.items():
            (inbox/filename).write_text(text)
    if name=='R2':
        (inbox/'events.csv').write_text('id,event_date,created_date\nA,2026-01-03,2026-01-01\nB,2026-01-01,2026-01-03\nC,2026-01-02,2026-01-02\n')
    if name=='R7':(inbox/'notes.txt').write_text('合成会议定在 14:30。')
    if name=='R8':(inbox/'history.txt').write_text('合成旧事：上周我们决定用蓝色封面。')
    budget=Budget(scenario);app,http=app_for(folder,workspace,budget)
    replies=[];states=[]
    with TestClient(app) as client:
        if name in ('R1','R5'):
            replies.append(send(client,'请派一个帮手把 inbox 里的 a.csv、b.csv、c.csv 三个表合成一张，按 date 从早到晚排序，保留 date,name,value 三列，结果放到它的任务目录里。做完告诉我结果文件的位置。'))
            states.append(settle(client,1))
            if name=='R5':replies.append(send(client,'刚才那张表弄好了吗？把结果位置告诉我。'))
        elif name=='R2':
            replies.append(send(client,'请派帮手把 inbox/events.csv 按日期从早到晚排序，结果放它的任务目录。表里有 event_date 和 created_date 两列，我尚未指定用哪一列。帮手遇到这个歧义时请先问我，不要替我决定；我答复后再告诉帮手。'))
            states.append(wait_for(client,lambda s:any(row['status'] in ('在等答复',*TERMINAL)
                               for row in s['helpers']),120))
            if states[-1] and any(row['status']=='在等答复' for row in states[-1]['helpers']):
                replies.append(send(client,'按 event_date 排序。'))
            states.append(settle(client,1))
        elif name=='R3':
            replies.append(send(client,'请派一个帮手在它的无网络沙箱中验证能否从 https://example.com/synthetic-data.csv 下载数据；能下载就核对行数，不能就写明做不到并交回。请让帮手实际检查，不要替它假定结果，也不要编造文件。'))
            states.append(settle(client,1,90))
        elif name=='R6':
            replies.append(send(client,'帮我把 inbox 里的 a.csv、b.csv、c.csv 合成一张表，按 date 排好，结果放到任务目录。'))
            states.append(settle(client,1))
        elif name=='R7':
            replies.append(send(client,'inbox/notes.txt 里写的会议是几点？'))
        elif name=='R8':
            for message in ('你好。','今天有点累。','刚才你说的事，先放一放。',
                            '我想把桌面整理一下。','上周我们决定了什么封面？合成旧事是蓝色封面。',
                            '谢谢，今天先到这里。'):
                replies.append(send(client,message))
        record=evidence(client,workspace)
    http.close()
    record['replies']=[reply['response'] for reply in replies]
    record['states']=states
    if name in ('R1','R5','R6'):
        check=verify_csv(record['files'],'date',
            ['2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-05'])
        delegated=sum(item['name']=='delegate' for item in record['tools'])
        assistants=sum(item['role']=='assistant' for item in record['turns'])
        check['pass']=check['pass'] and delegated==1 and bool(record['replies'][0]['text'])
        if name=='R5':
            check['pass']=(check['pass'] and len(record['replies'])==2 and
                           check['path'] in record['replies'][1]['text'])
        else:
            check['pass']=check['pass'] and assistants==2
        record['check']=check
    elif name=='R2':
        check=verify_csv(record['files'],'id',['B','C','A'])
        names=[item['name'] for item in record['tools']]
        check['pass']=check['pass'] and 'hold_question' in names and 'answer_helper' in names
        check['pass']=(check['pass'] and len(record['replies'])==2 and
            '按 event_date 排序。' in [turn['text'] for turn in record['turns'] if turn['role']=='user'])
        record['check']=check
    elif name=='R3':record['check']={'pass':bool(record['helpers']) and all(
        row['status']=='失败' or (row['status']=='已交回' and row.get('outcome')=='做不到')
        for row in record['helpers']) and not any(item['path'].endswith('.csv') for item in record['files'])
        and any('做不到' in turn['text'] for turn in record['turns'] if turn['role']=='assistant'),
                                    'helper_statuses':[row['status']+'·'+row.get('outcome','')
                                                       for row in record['helpers']]}
    elif name=='R7':record['check']={'pass':'14:30' in record['replies'][0]['text'] and
                             any(item['name']=='read_file' for item in record['tools']) and
                             not any(item['name']=='delegate' for item in record['tools'])}
    else:record['check']={'pass':all(reply['type']=='model' and reply['text'] and
                                  not reply['text'].lstrip().startswith('{')
                                  for reply in record['replies']) and
                               not any(item['name'] not in ('recall',) for item in record['tools'])}
    record['budget']=budget.summary()
    (folder/'summary.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps({'scenario':scenario,'check':record['check'],
          'calls':sum(item['calls'] for item in record['budget']),
          'helpers':[(row['id'],row['status'],row.get('outcome')) for row in record['helpers']]},ensure_ascii=False))


if __name__=='__main__':
    load_env_file(ROOT/'.env.local',override=False)
    os.environ['LUMINA_MODEL_MODE']='real'
    scenario_name,run_number=sys.argv[1],int(sys.argv[2])
    try:
        run_scenario(scenario_name,run_number)
    except Exception as exc:
        scenario=f'{scenario_name}_run{run_number}'
        folder=OUT/scenario;folder.mkdir(parents=True,exist_ok=True)
        summary=folder/'summary.json'
        if not summary.exists():
            summary.write_text(json.dumps({'error':type(exc).__name__,
                'budget':Budget(scenario).summary()},ensure_ascii=False,indent=2))
        print(json.dumps({'scenario':scenario,'error':type(exc).__name__,
                          'calls':sum(item['calls'] for item in Budget(scenario).summary())},
                         ensure_ascii=False))
        raise
