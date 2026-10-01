"""B2 stale questions, content-free receipts and terminal question context."""
import json
from types import SimpleNamespace
from pathlib import Path

from Execution.pool import HelperPool
from Nervous.bus import unpack
from test_organs_a2 import a2
from test_organs_b2_reliability import CONTRACT


def setup(tmp_path, rows):
    _, scheduler, model, _ = a2(tmp_path, rows)
    pool = HelperPool(scheduler.bus, scheduler.runner.config, workspace=tmp_path/'workspace')
    pool._pump = lambda: None
    scheduler.pool = pool
    scheduler.runner.pool = pool
    scheduler.runner.state.attach_pool(pool)
    return scheduler, pool, model


def question(scheduler, pool, helper, eid):
    bus = scheduler.bus
    bus.publish('spawn:'+helper, 'mind.spawn', {'helper':helper,'contract':CONTRACT})
    pool.handle(next(e for e in bus.pending('execution') if e['id']=='spawn:'+helper))
    with pool._changed:
        pool._questions(dict(pool.get(helper)), SimpleNamespace(cognitive_requests=lambda:[
            (eid,json.dumps({'question':'私有问题文字'}))]))
    return next(e for e in bus.pending('mind') if e['body']['helper']==helper)


def test_terminal_question_is_acked_counted_without_thought(tmp_path):
    scheduler, pool, model = setup(tmp_path, [])
    event = question(scheduler,pool,'H1','q1')
    with pool._changed: pool._finish(dict(pool.get('H1')),'已交回','finished')
    scheduler.runner.run_events([event])
    assert model.calls == []
    assert not scheduler.bus.pending('execution')
    assert event['id'] not in [e['id'] for e in scheduler.bus.pending('mind')]
    receipt = scheduler.bus.get(event['id']+':stale_question')
    assert receipt == {'event':event['id'],'helper':'H1','reason':'helper_terminal'}
    scheduler.runner.run_events([event])
    usage=scheduler.bus.conn.execute('SELECT body,digest FROM usage_records WHERE id=?',
                                    (event['id']+':stale_question',)).fetchall()
    assert len(usage)==1 and unpack(*usage[0])['stale_questions']==1
    assert '私有问题文字' not in usage[0]['body']


def test_mixed_batch_only_valid_question_is_seen_and_auto_answered(tmp_path):
    scheduler,pool,model=setup(tmp_path,[{'说':''}])
    stale=question(scheduler,pool,'H1','q1')
    valid=question(scheduler,pool,'H2','q2')
    with pool._changed:pool._finish(dict(pool.get('H1')),'已交回','finished')
    scheduler.runner.run_events([stale,valid])
    assert len(model.calls)==1
    notice=model.calls[0][1][-1]['content']
    assert '帮手 H2｜提问' in notice and '帮手 H1｜提问' not in notice
    replies=[e for e in scheduler.bus.pending('execution') if e['kind']=='mind.reply']
    assert len(replies)==1 and replies[0]['body']['helper']=='H2'


def test_answered_or_queued_answer_question_is_stale(tmp_path):
    scheduler,pool,model=setup(tmp_path,[])
    event=question(scheduler,pool,'H1','q1')
    scheduler.bus.publish('reply','mind.reply',{'helper':'H1','content':'answer'})
    assert pool.question_stale_reason(event)=='answer_queued'
    pool.handle(scheduler.bus.pending('execution')[0])
    assert pool.question_stale_reason(event)=='question_not_pending'
    scheduler.runner.run_events([event])
    assert model.calls==[]


def test_terminal_unanswered_qa_is_in_report_and_notice(tmp_path):
    scheduler,pool,_=setup(tmp_path,[])
    question(scheduler,pool,'H1','q1')
    with pool._changed:pool._finish(dict(pool.get('H1')),'已交回','assumed')
    event=next(e for e in scheduler.bus.pending('mind') if e['kind']=='agent.report')
    assert event['body']['qa'][0]['ended_without_answer'] is True
    notice=scheduler.runner._helper_notice(event)
    assert '期间的问答：' in notice and '（它没等答复就结束了）' in notice
    assert pool.get('H1')['question'] is None


def test_same_cognitive_request_is_published_once(tmp_path):
    scheduler,pool,_=setup(tmp_path,[])
    event=question(scheduler,pool,'H1','q1')
    with pool._changed:
        pool._questions(dict(pool.get('H1')),SimpleNamespace(cognitive_requests=lambda:[
            ('q1',json.dumps({'question':'私有问题文字'}))]))
    assert [e['id'] for e in scheduler.bus.pending('mind')]==[event['id']]
    assert len(pool.get('H1')['qa'])==1


def test_exact_supplied_prompt_additions():
    root=Path(__file__).resolve().parents[1]
    assert '要派活，就先调用 delegate，看到"已派出"的结果后' in (root/'prompts/dialogue_a2_persona.md').read_text()
    assert '没有调用 delegate，就不要说"我去安排""我让帮手试一下"' in (root/'prompts/dialogue_a2_persona.md').read_text()
    assert '必须是这次思考里真的调用了对应工具、看到了成功结果的' in (root/'prompts/mind_event.md').read_text()


def test_helper_ends_during_thought_no_automatic_reply(tmp_path):
    scheduler,pool,model=setup(tmp_path,[])
    event=question(scheduler,pool,'H1','q1')
    def finish():
        with pool._changed:pool._finish(dict(pool.get('H1')),'已交回','finished')
        return {'说':''}
    model.rows.append(finish)
    scheduler.runner.run_events([event])
    assert len(model.calls)==1
    assert not scheduler.bus.pending('execution')


def test_superseded_and_cancelled_questions_do_not_wake_mind(tmp_path):
    scheduler,pool,model=setup(tmp_path,[])
    old=question(scheduler,pool,'H1','q1')
    with pool._changed:
        pool._questions(dict(pool.get('H1')),SimpleNamespace(cognitive_requests=lambda:[
            ('q2',json.dumps({'question':'第二个不同问题'}))]))
    assert pool.question_stale_reason(old)=='question_superseded'
    new=next(e for e in scheduler.bus.pending('mind') if e['id']!=old['id'])
    scheduler.bus.publish('cancel','mind.cancel',{'helper':'H1'})
    assert pool.question_stale_reason(new)=='cancel_queued'
    scheduler.runner.run_events([old,new])
    assert not model.calls and not scheduler.bus.pending('mind')


def test_exact_supplied_helper_prompt():
    root=Path(__file__).resolve().parents[1]
    expected='- 遇到需要她来决定的事，比如任务的意思不清楚、要在几种做法之间取舍、发现任务本身可能有问题，就调用 ask_mind("问题")，紧接着用 Wait("MIND_REPLY") 等她答复。问了就一定要等：收到答复之前，不要继续做，也不要结束。答复如果是让你先等着，就继续等。同一个问题已经得到答复，就按答复做，不要再问。'
    assert expected in (root/"prompts/helper.md").read_text()
