"""Isolated helper-event integration: no Docker launch and no provider request."""
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
import time
from pathlib import Path
from types import SimpleNamespace

from Execution.pool import HelperPool
from Nervous.bus import EventBus
from config.lumina import load_config
from Mind.helper_actions import actions_from_object, parse_event
from test_organs_a2 import a2, send


CONTRACT = {'目标':'写一份表', '理由':'方便核对', '验收':'表存在', '背景':'合成样例'}


class CompletedOrgan:
    def __init__(self, **kwargs):
        self.directory = Path(kwargs['workspace'])
        self.state = None
    def run_goal(self, goal, spec):
        (self.directory/'result.csv').write_text('date,value\n2026-01-01,1\n')
        (self.directory/'.lumina-complete').write_text('done')
        self.state = SimpleNamespace(decision_count=1)
        return SimpleNamespace(status='completed', output='已生成合成表。', failure=None,
                               state=self.state)
    def repetition_tail(self):return []
    def cognitive_requests(self):return ()
    def shutdown(self):pass


class QuestionOrgan(CompletedOrgan):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.answered = None
    def run_goal(self, goal, spec):
        self.state = SimpleNamespace(decision_count=1, waiting_for=None)
        return SimpleNamespace(status='running', output=None, failure=None, state=self.state)
    def cognitive_requests(self):
        return [('question-1', json.dumps({'question':'按哪种日期？',
                 'evidence_files':[], 'model_ref':''}))]
    def resume(self):
        if self.answered is None:
            self.state = SimpleNamespace(decision_count=2, waiting_for='MIND_REPLY')
            return SimpleNamespace(status='waiting', output=None, failure=None, state=self.state)
        (self.directory/'answer.txt').write_text(self.answered)
        self.state = SimpleNamespace(decision_count=3, waiting_for=None)
        return SimpleNamespace(status='completed', output='按答复完成。', failure=None, state=self.state)
    def deliver_event(self, kind, text, *, defer_actions=False):
        assert kind == 'MIND_REPLY' and defer_actions
        self.answered = text
        self.state = SimpleNamespace(decision_count=2, waiting_for=None)
        return SimpleNamespace(status='running', output=None, failure=None, state=self.state)


def test_action_parser_keeps_valid_helper_contracts_and_event_json():
    actions = actions_from_object({'行动': [{'派活': CONTRACT},
        {'答复': {'帮手':'H123','内容':'按日期'}}, {'取消':'H123'}, {'搁置':'H234'},
        {'派活': {'目标':'缺字段'}}, {'读':'note.txt'}]})
    assert len(actions) == 5
    assert parse_event('说明 {"行动": [], "说": "知道了", "思绪": "", "带着": []}')['speech'] == '知道了'
    assert parse_event('不是 JSON') is None


def test_complete_json_after_provider_prefill_preserves_nested_spawn():
    from Conversation_Memory.answer import parse_dialogue
    from Mind.helper_actions import object_from_text
    complete = json.dumps({'理解':'需要整理','行动':[{'派活':CONTRACT}],
                           '回复':''}, ensure_ascii=False)
    raw = '{"理解": "' + complete
    assert object_from_text(raw)['行动'] == [{'派活':CONTRACT}]
    assert actions_from_object(object_from_text(raw)) == [{'派活':CONTRACT}]
    assert parse_dialogue(raw)['noticed']['理解'] == '需要整理'
    assert parse_event('{"行动":["读","tasks/H1/result.csv"],"说":""}')['actions'] == [
        {'读':'tasks/H1/result.csv'}]
    assert parse_event('{"行动":["搁置 H1"],"说":""}')['actions'] == [{'搁置':'H1'}]
    assert parse_event('{"行动":"读 tasks/H1/result.csv","说":""}')['actions'] == [
        {'读':'tasks/H1/result.csv'}]


def test_helper_ask_mind_native_tool_runs_as_sandbox_code():
    from Execution.deepseek_model import DeepSeekModel
    from Execution.execution import IPythonCode,ModelRequest
    wire=[]
    def transport(payload):
        wire.append(payload)
        return {'choices':[{'message':{'tool_calls':[{'id':'ask1','type':'function',
            'function':{'name':'ask_mind','arguments':json.dumps({'question':'哪列日期？'})}}]}}]}
    model=DeepSeekModel(transport=transport,helper_prompt='帮手')
    decision=model.decide(ModelRequest('{}',model.tool_contracts,()))
    assert isinstance(decision.action,IPythonCode)
    assert decision.action.code=="ask_mind('哪列日期？')"
    assert 'ask_mind' in [tool['function']['name'] for tool in wire[0]['tools']]


def test_helper_wrapper_exposes_ask_mind_only_to_root(tmp_path):
    from Execution.execution import ModelRequest
    from Execution.pool import _HelperModel
    captured=[]
    class Base:
        identifier='script';tool_contracts=()
        def decide(self,request):
            captured.append(request.available_tools)
            return None
    bus=EventBus(tmp_path/'bus.sqlite')
    pool=HelperPool(bus,load_config(),workspace=tmp_path/'workspace')
    model=_HelperModel(pool,'H1','帮手',base=Base())
    model.decide(ModelRequest('{}',('ipython(code: str)','claim_complete()'),()))
    model.decide(ModelRequest('{}',('ipython(code: str)','return(local_result: str)'),()))
    assert any(tool.startswith('ask_mind(') for tool in captured[0])
    assert not any(tool.startswith('ask_mind(') for tool in captured[1])


def test_chat_spawns_helper_report_wakes_mind_and_proactive_speech_is_once(tmp_path):
    runtime, scheduler, model, _ = a2(tmp_path, [
        {'回复':'我交给帮手处理。','行动':[{'派活':CONTRACT}]},
        {'行动':[], '说':'已经整理好，文件在任务目录。','思绪':'', '带着':[]}],
        language={'render':'proactive_only'})
    pool = HelperPool(scheduler.bus, scheduler.runner.config,
                      workspace=tmp_path/'workspace', organ_factory=CompletedOrgan)
    scheduler.pool = pool
    scheduler.runner.pool = pool
    scheduler.runner.state.attach_pool(pool)
    try:
        assert send(scheduler)['response']['text'] == '我交给帮手处理。'
        helper = 'H'+hashlib.sha256('t1:1:1'.encode()).hexdigest()[:8]
        until = time.monotonic()+3
        while not scheduler.bus.pending('mind') and time.monotonic()<until:
            time.sleep(.01)
        assert pool.get(helper)['status'] == '已完成'
        assert (tmp_path/'workspace/tasks'/helper/'result.csv').exists()
        assert scheduler.bus.pending('mind')[0]['kind'] == 'agent.report'
        scheduler.drain_once()
        assert not scheduler.bus.pending('mind')
        assert [turn.text for turn in runtime._hot_store.list_all_raw()] == [
            '你好','我交给帮手处理。','这是她最终说的话。']
        assert len(model.rephrases) == 1
        assert scheduler.bus.get(helper+':report:finished')
        assert scheduler.runner.state.snapshot()['helpers'][0]['id'] == helper
    finally:
        pool.stop()


def test_question_reply_and_missing_action_auto_reply(tmp_path):
    for decision, expected in [({'行动':[{'答复':{'帮手':'PLACEHOLDER','内容':'按记录日期'}}],
                                '说':'', '思绪':'', '带着':[]}, '按记录日期'),
                               ({'行动':[], '说':'', '思绪':'', '带着':[]},
                                '她暂时没有答复，请按你的最佳判断继续，并在回报里说明。')]:
        folder = tmp_path / ('explicit' if expected == '按记录日期' else 'automatic')
        helper = 'H'+hashlib.sha256('t1:1:1'.encode()).hexdigest()[:8]
        configured = json.loads(json.dumps(decision).replace('PLACEHOLDER',helper))
        runtime, scheduler, model, _ = a2(folder, [
            {'回复':'我会让帮手做。','行动':[{'派活':CONTRACT}]}, configured,
            {'行动':[], '说':'', '思绪':'', '带着':[]}], language={'render':'proactive_only'})
        pool = HelperPool(scheduler.bus, scheduler.runner.config,
                          workspace=folder/'workspace', organ_factory=QuestionOrgan)
        scheduler.pool = pool; scheduler.runner.pool = pool
        scheduler.runner.state.attach_pool(pool)
        try:
            send(scheduler)
            until = time.monotonic()+3
            while not scheduler.bus.pending('mind') and time.monotonic()<until:
                time.sleep(.01)
            assert scheduler.bus.pending('mind')[0]['kind'] == 'agent.question'
            scheduler.drain_once()
            until = time.monotonic()+3
            while pool.get(helper)['status'] != '已完成' and time.monotonic()<until:
                time.sleep(.01)
            assert pool.get(helper)['status'] == '已完成'
            assert (folder/'workspace/tasks'/helper/'answer.txt').read_text() == expected
            assert not pool.get(helper)['question']
            scheduler.drain_once()
        finally:
            pool.stop()


def test_hold_expiry_schedules_auto_reply_without_model(tmp_path):
    runtime, scheduler, model, _ = a2(tmp_path, [
        {'回复':'让帮手先看。','行动':[{'派活':CONTRACT}]},
        {'行动':[{'搁置':'PLACEHOLDER'}], '说':'', '思绪':'', '带着':[]}],
        language={'render':'proactive_only'})
    helper = 'H'+hashlib.sha256('t1:1:1'.encode()).hexdigest()[:8]
    model.rows[1]['行动'][0]['搁置'] = helper
    pool = HelperPool(scheduler.bus, scheduler.runner.config,
                      workspace=tmp_path/'workspace', organ_factory=QuestionOrgan)
    scheduler.pool = pool; scheduler.runner.pool = pool
    scheduler.runner.state.attach_pool(pool)
    try:
        send(scheduler)
        until = time.monotonic()+3
        while not scheduler.bus.pending('mind') and time.monotonic()<until:
            time.sleep(.01)
        scheduler.drain_once()
        row = dict(pool.get(helper))
        assert row['status'] == '在等答复' and row['question']
        row['question']['at'] = (datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
        with pool._changed:pool._save(row)
        pool.expire_questions()
        assert any(event['kind']=='mind.reply' for event in scheduler.bus.pending('execution'))
        scheduler.drain_once()
        until = time.monotonic()+3
        while pool.get(helper)['status'] != '已完成' and time.monotonic()<until:
            time.sleep(.01)
        assert pool.get(helper)['status'] == '已完成'
    finally:
        pool.stop()


def test_held_question_can_be_answered_during_next_chat(tmp_path):
    helper='H'+hashlib.sha256('t1:1:1'.encode()).hexdigest()[:8]
    runtime,scheduler,model,_=a2(tmp_path,[
        {'回复':'先请帮手看看。','行动':[{'派活':CONTRACT}]},
        {'行动':[{'搁置':helper}],'说':'','思绪':'','带着':[]},
        {'回复':'明白，我告诉它按事件日期。','行动':[{'答复':{'帮手':helper,'内容':'按 event_date 排序'}}]},
        {'行动':[],'说':'','思绪':'','带着':[]}],language={'render':'proactive_only'})
    pool=HelperPool(scheduler.bus,scheduler.runner.config,workspace=tmp_path/'workspace',organ_factory=QuestionOrgan)
    scheduler.pool=pool;scheduler.runner.pool=pool;scheduler.runner.state.attach_pool(pool)
    try:
        send(scheduler)
        deadline=time.monotonic()+3
        while not scheduler.bus.pending('mind') and time.monotonic()<deadline:time.sleep(.01)
        scheduler.drain_once()
        assert pool.get(helper)['status']=='在等答复'
        assert send(scheduler,'t2','请按 event_date 排序。')['response']['text']=='明白，我告诉它按事件日期。'
        deadline=time.monotonic()+3
        while pool.get(helper)['status']!='已完成' and time.monotonic()<deadline:time.sleep(.01)
        assert pool.get(helper)['status']=='已完成'
        assert (tmp_path/'workspace/tasks'/helper/'answer.txt').read_text()=='按 event_date 排序'
    finally:pool.stop()


def test_event_parse_failure_retries_without_thinking_and_with_prefill(tmp_path):
    runtime, scheduler, model, _ = a2(tmp_path, [
        {'回复':'已派出。','行动':[{'派活':CONTRACT}]}], language={'render':'proactive_only'})
    attempts = []
    def event_request(system, messages, *, thinking, prefill):
        return {'system':system, 'messages':messages, 'thinking':thinking, 'prefill':prefill}
    def complete_event(system, messages, *, thinking, prefill):
        attempts.append((thinking, prefill))
        return 'not json' if len(attempts) == 1 else '{"行动":[],"说":"结果已到。","思绪":"","带着":[]}'
    model.event_request = event_request
    model.complete_event = complete_event
    pool = HelperPool(scheduler.bus, scheduler.runner.config,
                      workspace=tmp_path/'workspace', organ_factory=CompletedOrgan)
    scheduler.pool = pool; scheduler.runner.pool = pool
    scheduler.runner.state.attach_pool(pool)
    try:
        send(scheduler)
        deadline = time.monotonic()+3
        while not scheduler.bus.pending('mind') and time.monotonic()<deadline:time.sleep(.01)
        scheduler.drain_once()
        assert attempts == [('low',False),('disabled',True)]
        assert runtime._hot_store.list_all_raw()[-1].text == '这是她最终说的话。'
    finally:
        pool.stop()


class LoopOrgan(CompletedOrgan):
    def run_goal(self, goal, spec):
        self.state = SimpleNamespace(decision_count=1)
        return SimpleNamespace(status='running', output=None, failure=None, state=self.state)
    def resume(self):
        self.state = SimpleNamespace(decision_count=self.state.decision_count+1)
        return SimpleNamespace(status='running', output=None, failure=None, state=self.state)
    def interrupt(self):
        return SimpleNamespace(status='suspended')


def test_call_cap_requests_return_then_pulls_plug(tmp_path):
    bus = EventBus(tmp_path/'bus.sqlite')
    config = load_config(overrides={'execution':{'helper_call_cap':2,'return_grace_steps':2}})
    pool = HelperPool(bus, config, workspace=tmp_path/'workspace', organ_factory=LoopOrgan)
    bus.publish('spawn','mind.spawn',{'helper':'Hcap','contract':CONTRACT})
    pool.handle(bus.pending('execution')[0])
    try:
        deadline=time.monotonic()+3
        while pool.get('Hcap')['status'] not in ('被拉闸','失败') and time.monotonic()<deadline:
            time.sleep(.01)
        row=pool.get('Hcap')
        assert row['status']=='被拉闸' and row['return_requested']=='调用次数达到上限'
        assert row['grace_steps']==2
        while not bus.pending('mind') and time.monotonic()<deadline:time.sleep(.01)
        assert [event['kind'] for event in bus.pending('mind')] == ['agent.report']
    finally:pool.stop()


def test_repeated_action_and_unchanged_files_request_return(tmp_path):
    class RepeatOrgan(LoopOrgan):
        def repetition_tail(self):
            return [{'code':'print(1)','result':{'stdout':'1'}}]*self.state.decision_count
    bus=EventBus(tmp_path/'bus.sqlite')
    config=load_config(overrides={'execution':{'repeat_threshold':3,'helper_call_cap':60,'return_grace_steps':2}})
    pool=HelperPool(bus,config,workspace=tmp_path/'workspace',organ_factory=RepeatOrgan)
    bus.publish('spawn','mind.spawn',{'helper':'Hrepeat','contract':CONTRACT})
    pool.handle(bus.pending('execution')[0])
    try:
        deadline=time.monotonic()+3
        while pool.get('Hrepeat')['status']!='被拉闸' and time.monotonic()<deadline:time.sleep(.01)
        row=pool.get('Hrepeat')
        assert row['status']=='被拉闸' and row['return_requested']=='原地打转'
        assert row['calls']<60
    finally:pool.stop()


def test_helper_reports_within_guard_grace_instead_of_being_pulled(tmp_path):
    owner=[]
    class ReturnOrgan(LoopOrgan):
        def resume(self):
            self.state=SimpleNamespace(decision_count=self.state.decision_count+1)
            if owner[0].get('Hreturn')['return_requested']:
                return SimpleNamespace(status='completed',output='按请回报信号停下并说明当前情况',
                                       failure=None,state=self.state)
            return SimpleNamespace(status='running',output=None,failure=None,state=self.state)
    bus=EventBus(tmp_path/'bus.sqlite')
    config=load_config(overrides={'execution':{'helper_call_cap':2,'return_grace_steps':2}})
    pool=HelperPool(bus,config,workspace=tmp_path/'workspace',organ_factory=ReturnOrgan)
    owner.append(pool)
    bus.publish('spawn','mind.spawn',{'helper':'Hreturn','contract':CONTRACT})
    pool.handle(bus.pending('execution')[0])
    try:
        deadline=time.monotonic()+3
        while pool.get('Hreturn')['status']!='已完成' and time.monotonic()<deadline:time.sleep(.01)
        row=pool.get('Hreturn')
        assert row['status']=='已完成' and row['return_requested']=='调用次数达到上限'
        assert '请回报' in row['report']
    finally:pool.stop()


def test_cancel_waiting_helper(tmp_path):
    class WaitingOrgan(CompletedOrgan):
        def run_goal(self,goal,spec):
            self.state=SimpleNamespace(decision_count=1,waiting_for='MIND_REPLY')
            return SimpleNamespace(status='waiting',output=None,failure=None,state=self.state)
        def interrupt(self):pass
    bus=EventBus(tmp_path/'bus.sqlite')
    pool=HelperPool(bus,load_config(),workspace=tmp_path/'workspace',organ_factory=WaitingOrgan)
    bus.publish('spawn','mind.spawn',{'helper':'Hcancel','contract':CONTRACT})
    pool.handle(bus.pending('execution')[0])
    try:
        bus.publish('cancel','mind.cancel',{'helper':'Hcancel'})
        pool.handle(bus.pending('execution')[0])
        deadline=time.monotonic()+3
        while pool.get('Hcancel')['status']!='已取消' and time.monotonic()<deadline:time.sleep(.01)
        assert pool.get('Hcancel')['status']=='已取消'
        assert bus.pending('mind')[0]['kind']=='agent.report'
    finally:pool.stop()


def test_service_restart_requeues_waiting_helper_and_keeps_task_id(tmp_path):
    class WaitingOrgan(CompletedOrgan):
        def run_goal(self,goal,spec):
            self.state=SimpleNamespace(decision_count=1,waiting_for='MIND_REPLY')
            return SimpleNamespace(status='waiting',output=None,failure=None,state=self.state)
    path=tmp_path/'bus.sqlite';workspace=tmp_path/'workspace'
    bus=EventBus(path)
    first=HelperPool(bus,load_config(),workspace=workspace,organ_factory=WaitingOrgan)
    bus.publish('spawn','mind.spawn',{'helper':'Hrestart','contract':CONTRACT})
    first.handle(bus.pending('execution')[0])
    deadline=time.monotonic()+3
    while first.get('Hrestart')['status']!='进行中' and time.monotonic()<deadline:time.sleep(.01)
    first.stop()
    second_bus=EventBus(path)
    second=HelperPool(second_bus,load_config(),workspace=workspace,organ_factory=CompletedOrgan)
    try:
        second.start()
        deadline=time.monotonic()+3
        while second.get('Hrestart')['status']!='已完成' and time.monotonic()<deadline:time.sleep(.01)
        assert second.get('Hrestart')['status']=='已完成'
        assert (workspace/'tasks/Hrestart/result.csv').exists()
    finally:second.stop()


def test_user_event_has_priority_over_helper_report(tmp_path):
    runtime,scheduler,model,_=a2(tmp_path,[{'回复':'先回应你的消息。'}],
                                  language={'render':'proactive_only'})
    scheduler.bus.publish('report','agent.report',{'helper':'H1','status':'已完成',
        'summary':'完成','outputs':[],'contract':CONTRACT})
    scheduler.bus.publish('user','user.message',{'message':'现在请先回我'})
    scheduler.drain_once()
    assert scheduler.bus.get('user:reply')['response']['text']=='先回应你的消息。'
    assert scheduler.bus.pending('mind')[0]['kind']=='agent.report'


def test_child_completion_does_not_publish_to_mind(tmp_path):
    class ChildOrgan(CompletedOrgan):
        def run_goal(self,goal,spec):
            self._model=object()
            self.state=SimpleNamespace(decision_count=1,pending_child_refs=('C1',))
            return SimpleNamespace(status='child_pending',output=None,failure=None,state=self.state)
        def open_child(self,*args,**kwargs):
            class Child:
                def run_child(self):return SimpleNamespace(status='completed')
                def shutdown(self):pass
            return Child()
        def accept_child(self,outcome):
            self.state=SimpleNamespace(decision_count=2,pending_child_refs=())
            return SimpleNamespace(status='completed',output='子帮手结果已纳入',failure=None,state=self.state)
    bus=EventBus(tmp_path/'bus.sqlite')
    pool=HelperPool(bus,load_config(),workspace=tmp_path/'workspace',organ_factory=ChildOrgan)
    bus.publish('spawn','mind.spawn',{'helper':'Hparent','contract':CONTRACT})
    pool.handle(bus.pending('execution')[0])
    try:
        deadline=time.monotonic()+3
        while pool.get('Hparent')['status']!='已完成' and time.monotonic()<deadline:time.sleep(.01)
        assert pool.get('Hparent')['status']=='已完成'
        while not bus.pending('mind') and time.monotonic()<deadline:time.sleep(.01)
        assert [event['kind'] for event in bus.pending('mind')]==['agent.report']
    finally:pool.stop()


def test_completed_task_expires_after_keep_window(tmp_path):
    now=datetime(2026,1,2,tzinfo=timezone.utc)
    bus=EventBus(tmp_path/'bus.sqlite')
    pool=HelperPool(bus,load_config(),workspace=tmp_path/'workspace',clock=lambda:now)
    with pool._changed:
        pool._register({'body':{'helper':'Hdone','contract':CONTRACT}})
        row=pool.get('Hdone');row['status']='已完成';row['finished_at']=(now-timedelta(hours=25)).isoformat()
        pool._save(row)
    assert pool.visible()==[]


def test_running_helper_appears_in_lumina_status_and_focus(tmp_path):
    from Nervous.lumina_state import LuminaState
    bus=EventBus(tmp_path/'bus.sqlite')
    pool=HelperPool(bus,load_config(),workspace=tmp_path/'workspace')
    with pool._changed:pool._register({'body':{'helper':'Hstatus','contract':CONTRACT}})
    state=LuminaState(threading.Lock(),bus);state.attach_pool(pool)
    with pool._changed:
        row=pool.get('Hstatus');row['status']='进行中';pool._save(row)
    live=state.snapshot()
    assert live['executing'] and '执行中' in live['states']
    assert live['focus']=='帮手 Hstatus：写一份表'
    assert live['helpers'][0]['status']=='进行中'
    state.thinking(True,'在看帮手 Hstatus 的回报')
    assert state.snapshot()['focus']=='在看帮手 Hstatus 的回报'
    state.thinking(False)
    with pool._changed:
        row=pool.get('Hstatus');row['status']='已完成';row['finished_at']=datetime.now(timezone.utc).isoformat();pool._save(row)
    assert not state.snapshot()['executing'] and state.snapshot()['focus']==''


def test_three_helpers_limit_concurrency_and_keep_separate_task_dirs(tmp_path):
    gate=threading.Event()
    lock=threading.Lock()
    active=0; peak=0
    class WaitingOrgan(CompletedOrgan):
        def run_goal(self,goal,spec):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
            assert gate.wait(3)
            with lock:active-=1
            self.directory.joinpath('only-here.txt').write_text(self.directory.name)
            self.state=SimpleNamespace(decision_count=1)
            return SimpleNamespace(status='completed',output='完成',failure=None,state=self.state)
    bus=EventBus(tmp_path/'bus.sqlite')
    pool=HelperPool(bus,load_config(),workspace=tmp_path/'workspace',organ_factory=WaitingOrgan)
    try:
        for n in range(3):
            bus.publish(f'spawn{n}','mind.spawn',{'helper':f'H{n}', 'contract':CONTRACT})
            pool.handle(bus.pending('execution')[0])
        deadline=time.monotonic()+3
        while peak<2 and time.monotonic()<deadline:time.sleep(.01)
        assert peak==2 and pool.get('H2')['status']=='排队中'
        gate.set()
        deadline=time.monotonic()+3
        while any(pool.get(f'H{n}')['status']!='已完成' for n in range(3)) and time.monotonic()<deadline:
            time.sleep(.01)
        assert all(pool.get(f'H{n}')['status']=='已完成' for n in range(3))
        assert all((tmp_path/'workspace/tasks'/f'H{n}'/'only-here.txt').read_text()==f'H{n}' for n in range(3))
        assert peak==2
    finally:
        gate.set();pool.stop()


def test_unknown_action_recovery_reports_need_for_manual_check(tmp_path):
    class UnknownOrgan(CompletedOrgan):
        def run_goal(self,goal,spec):
            raise ValueError('interrupted IPython execution recovery is unsupported')
    bus=EventBus(tmp_path/'bus.sqlite')
    pool=HelperPool(bus,load_config(),workspace=tmp_path/'workspace',organ_factory=UnknownOrgan)
    bus.publish('spawn','mind.spawn',{'helper':'Hunknown','contract':CONTRACT})
    pool.handle(bus.pending('execution')[0])
    try:
        deadline=time.monotonic()+3
        while pool.get('Hunknown')['status']!='失败' and time.monotonic()<deadline:time.sleep(.01)
        assert pool.get('Hunknown')['report']=='失败：需要人工确认'
    finally:pool.stop()


def test_old_manual_execution_route_is_removed_and_status_lists_helpers(tmp_path):
    from core.main import create_app
    from core.model_client import MockModelClient
    app=create_app(env_file_path=None, model_client=MockModelClient(),
                   draft_store_path=tmp_path/'hot', memory_dir=tmp_path/'memory',
                   enable_compaction=False, recall_enabled=False,
                   lumina_config={'workspace':{'path':str(tmp_path/'workspace')}})
    paths={route.path for route in app.routes}
    assert '/api/chat' in paths and '/api/status' in paths
    assert '/api/execution' not in paths
    snapshot=app.state.lumina_state.snapshot()
    assert snapshot['helpers']==[] and not snapshot['executing']
