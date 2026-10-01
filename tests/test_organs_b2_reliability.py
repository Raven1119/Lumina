"""B2 terminal, event, context, and response behavior with synthetic state."""
import sqlite3

from Execution.pool import HelperPool
from Nervous.bus import EventBus
from config.lumina import load_config
from test_organs_a2 import Script, a2, send


CONTRACT = {'目标': '整理表', '理由': '供核对', '验收': '行数正确', '背景': '合成数据'}


def test_outcome_and_report_are_atomic_and_startup_backfills_once(tmp_path):
    bus=EventBus(tmp_path/'bus.sqlite')
    pool=HelperPool(bus,load_config(overrides={'workspace':{'path':str(tmp_path/'workspace')}}))
    pool._pump=lambda:None
    bus.publish('spawn','mind.spawn',{'helper':'H1','contract':CONTRACT})
    pool.handle(bus.pending('execution')[0])
    row=pool.get('H1')
    directory=tmp_path/'workspace/tasks/H1'
    (directory/'.lumina-outcome').write_text('做不到\n网络不可用',encoding='utf-8')
    (directory/'.lumina-complete').write_text('done')
    (directory/'result.txt').write_text('partial')
    bus.conn.execute("CREATE TRIGGER reject_report BEFORE INSERT ON events WHEN NEW.id='H1:report' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    bus.conn.commit()
    try:
        with pool._changed:
            try:pool._finish(dict(row),'已交回','旧说明')
            except sqlite3.IntegrityError:pass
            else:assert False, 'terminal transaction should roll back'
        assert pool.get('H1')['status']=='排队中'
        assert not bus.pending('mind')
    finally:
        bus.conn.execute('DROP TRIGGER reject_report');bus.conn.commit()
    with pool._changed:pool._finish(dict(row),'已交回','旧说明')
    finished=pool.get('H1')
    assert finished['outcome']=='做不到' and finished['report']=='网络不可用'
    assert finished['outputs']==['tasks/H1/result.txt']
    assert bus.pending('mind')[0]['body']['outcome']=='做不到'
    bus.conn.execute("DELETE FROM events WHERE id='H1:report'");bus.conn.commit()
    pool.start();pool.start()
    assert len(bus.pending('mind'))==1


def test_recent_dialogue_and_helper_qa_are_in_one_event_context(tmp_path):
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'收到'}, {'说':''}])
    pool=HelperPool(scheduler.bus,scheduler.runner.config,workspace=tmp_path/'workspace')
    pool._pump=lambda:None
    scheduler.pool=pool;scheduler.runner.pool=pool;scheduler.runner.state.attach_pool(pool)
    send(scheduler,'chat1','请按 event_date 排序')
    scheduler.bus.publish('spawn','mind.spawn',{'helper':'H1','contract':CONTRACT})
    pool.handle(scheduler.bus.pending('execution')[0])
    qa=[{'question':'按哪列？','asked_at':'2026-10-01T06:02:00+00:00',
         'answer':'按 event_date','answer_source':'她答的',
         'answered_at':'2026-10-01T06:03:00+00:00'}]
    scheduler.bus.publish('report','agent.report',{'helper':'H1','status':'已交回',
        'outcome':'完成','summary':'排好了','outputs':['tasks/H1/sorted.csv'],
        'contract':CONTRACT,'qa':qa})
    scheduler.drain_once()
    messages=model.calls[-1][1]
    assert messages[0]['content'].startswith('最近的对话：')
    assert '请按 event_date 排序' in messages[0]['content']
    notice=messages[-1]['content']
    assert '帮手 H1｜交回（自报：完成）' in notice
    assert '你答：按 event_date' in notice
    assert '产出：tasks/H1/sorted.csv' in notice


def test_event_dead_letter_does_not_stop_next_message(tmp_path):
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'下一条正常'}])
    original=scheduler.runner.run
    def run(event):
        if event['id']=='bad':raise RuntimeError('private conversation must not leak')
        return original(event)
    scheduler.runner.run=run
    scheduler.bus.publish('bad','user.message',{'message':'private body'})
    scheduler.bus.publish('good','user.message',{'message':'新消息'})
    for _ in range(3):scheduler.drain_once()
    assert scheduler.bus.get('bad:reply')['response']=={
        'type':'error','text':'这次没能回应：事件处理失败'}
    assert scheduler.bus.dead_letter_count()==1
    dead=scheduler.bus.conn.execute('SELECT * FROM dead_letters').fetchone()
    assert dead['exception_type']=='RuntimeError'
    assert 'private' not in str(dict(dead))
    scheduler.drain_once()
    assert scheduler.bus.get('good:reply')['response']['text']=='下一条正常'


def test_a2_no_speech_and_provider_error_never_use_placeholder(tmp_path):
    _,scheduler,_,_=a2(tmp_path/'none',[{'回复':''}])
    assert send(scheduler)['response']=={'type':'none','text':''}
    runtime,scheduler,model,_=a2(tmp_path/'error',[])
    def fail(*args,**kwargs):raise RuntimeError('synthetic')
    model.complete_tools=fail
    reply=send(scheduler)['response']
    assert reply['type']=='error' and reply['text']=='这次没能回应：模型调用失败'
    assert len(runtime._hot_store.list_all_raw())==1


def test_api_status_reports_dead_letter_count(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from core.main import create_app
    from test_memory_v1_chat import FakeMemory
    monkeypatch.setattr('core.main.build_memory_model_from_env',lambda *_:None)
    app=create_app(draft_store_path=tmp_path/'hot',memory_dir=tmp_path/'memory',
        model_client=Script([]),memory=FakeMemory(),env_file_path=None,
        enable_compaction=False,recall_enabled=False)
    app.state.nervous_bus.publish('bad','user.message',{'message':'synthetic'})
    for _ in range(3):
        app.state.nervous_bus.event_failed({'id':'bad','kind':'user.message'},
                                           RuntimeError('hidden'),3)
    with TestClient(app) as client:
        assert client.get('/api/status').json()['dead_letters']==1
