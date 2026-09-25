import json
from datetime import datetime,timedelta

import pytest

from lab.llm import CachedLLM,CacheMiss,FakeLLM,LLMResult
from memlab.clock import SimClock
from memlab.config import preset
from memlab.embed import HashEmbedder
from memlab.entities import align
from memlab.integrate import render_prompt,run_dream
from memlab.store import Store
from memlab.types import Turn

AT=datetime.fromisoformat('2026-03-04T23:50:00+08:00')


class StaticLLM:
    model='static-v1'
    def __init__(self,text):self.text=text;self.calls=0
    def complete(self,**kwargs):
        self.calls+=1
        return LLMResult(self.text,{'input_tokens':3,'output_tokens':4},False,'static-key')


def window(prefix='s01',offset=0):
    return [Turn(f'{prefix}-t01',prefix,'user',AT+timedelta(days=offset),'今晚在实验室做实验'),
            Turn(f'{prefix}-t02',prefix,'assistant',AT+timedelta(days=offset,seconds=30),'我在听')]


def test_cache_key_attempt_full_prompt_and_no_key(tmp_path):
    base=StaticLLM('{"ops":[]}')
    cache=CachedLLM(base,tmp_path/'llm')
    a=cache.complete(system='s',messages=[{'role':'user','content':'u'}],max_tokens=5,purpose='dream')
    b=cache.complete(system='s',messages=[{'role':'user','content':'u'}],max_tokens=5,purpose='dream')
    assert base.calls==1 and b.cache_hit and a.cache_key==b.cache_key
    c=cache.complete(system='s',messages=[{'role':'user','content':'u'}],max_tokens=5,purpose='dream',attempt=1)
    assert c.cache_key!=a.cache_key
    d=cache.complete(system='s!',messages=[{'role':'user','content':'u'}],max_tokens=5,purpose='dream')
    assert d.cache_key!=a.cache_key
    assert 'secret' not in ''.join(p.read_text() for p in (tmp_path/'llm').glob('*.json'))
    with pytest.raises(CacheMiss):
        CachedLLM(base,tmp_path/'llm',cache_only=True).complete(system='miss',messages=[],max_tokens=5,purpose='dream')


def test_apply_all_ops_lineage_merge_inheritance_and_relate():
    store=Store();embed=HashEmbedder();cfg=preset('P6','hash');clock=SimClock(AT)
    first=window();store.add_cold(first)
    llm=StaticLLM(json.dumps({'ops':[
        {'op':'new','text':'他常在实验室待到深夜。','salience':2,'sources':['s01-t01'],'entities':[{'name':'实验室'}]},
        {'op':'new','text':'他在做成像实验。','salience':1,'sources':['s01-t01'],'entities':[]},
        {'op':'relate','a':'new:0','b':'new:1','directed':True}]},ensure_ascii=False))
    result=run_dream(store,first,llm,embed,clock,cfg)
    assert result.status=='applied' and store.version==1
    assert store.conn.execute('SELECT COUNT(*) FROM event_edges').fetchone()[0]==1
    store.add_cold(window('s02',1));clock.set(AT+timedelta(days=1))
    llm.text=json.dumps({'ops':[
        {'op':'revise','id':'m1','text':'他最近常在深夜做成像实验。','sources':['s02-t01']},
        {'op':'merge','ids':['m1','m2'],'text':'他经常深夜做实验。','sources':['s02-t01']},
        {'op':'touch','id':'m2','sources':['s02-t01']},
        {'op':'relate','a':'new:1','b':'m2','directed':False},
        {'op':'new','text':'他今天谈到了实验。','sources':['s02-t01'],'entities':[]}]},ensure_ascii=False)
    # new:1 is a successful merge even though relate follows it.
    result=run_dream(store,window('s02',1),llm,embed,clock,cfg)
    assert result.status=='applied' and store.version==2
    snap=store.snapshot(embed)
    assert snap.memories[0]['text']=='他最近常在深夜做成像实验。'
    assert snap.memories[0]['salience']==1
    assert len(snap.memories[2]['sources'])>=2
    assert any(row[3]=='inherit:m1' and row[2]=='new' for row in snap.memories[2]['event_rows'])
    assert any(row['kind']=='revise' for row in snap.memories[0]['lineage'])
    assert ('m3','m1') in snap.merges
    store.close()


def test_invalid_ops_rejected_and_invalid_json_keeps_pending():
    store=Store();embed=HashEmbedder();cfg=preset('P1','hash');clock=SimClock(AT)
    turns=window();store.add_cold(turns)
    bad=StaticLLM('not json')
    assert run_dream(store,turns,bad,embed,clock,cfg).status=='failed'
    assert store.pending_count()==2 and store.version==0
    ops=[{'op':'unknown','sources':['s01-t01']},
         {'op':'new','text':'x','sources':['not-here']},
         {'op':'new','text':'','sources':['s01-t01']},
         {'op':'merge','ids':['m1'],'text':'x','sources':['s01-t01']},
         {'op':'relate','a':'new:2','b':'m1'},
         {'op':'new','text':'他在实验室做事。','salience':99,'sources':['s01-t01'],'entities':[]}]
    good=StaticLLM(json.dumps({'ops':ops},ensure_ascii=False))
    result=run_dream(store,turns,good,embed,clock,cfg)
    assert result.n_rejected==5
    assert store.conn.execute('SELECT salience FROM memories').fetchone()[0]==1
    assert store.conn.execute("SELECT reason FROM dream_ops WHERE status='applied'").fetchone()[0]=='invalid_salience_defaulted'
    assert store.pending_count()==0
    store.close()


def test_entity_exact_and_self_filter():
    s=Store()
    ids,new,warnings=align(s.conn,[{'name':'小周','aliases':['周同学']},{'name':'Lumina'}],AT.isoformat())
    assert ids==['e1'] and new==['e1'] and 'self_entity' in warnings
    ids,new,_=align(s.conn,[{'id':'e1'},{'name':'周同学','aliases':['小周同学']}],AT.isoformat())
    assert ids==['e1'] and not new
    assert s.conn.execute('SELECT COUNT(*) FROM entity_aliases').fetchone()[0]==2
    s.close()


def test_dream_prompt_independent_of_recall_events():
    s=Store();embed=HashEmbedder();turns=window();s.add_cold(turns)
    clock=SimClock(AT)
    run_dream(s,turns,StaticLLM('{"ops":[{"op":"new","text":"他在实验室。","sources":["s01-t01"]}]}'),embed,clock,preset('P1','hash'))
    next_window=window('s02',1)
    a=render_prompt(s,next_window,[],AT+timedelta(days=1),preset('P1','hash'))
    s.conn.execute("INSERT INTO strength_events VALUES('m1',?,0.5,'recall','test')",((AT+timedelta(hours=1)).isoformat(),));s.conn.commit()
    b=render_prompt(s,next_window,[],AT+timedelta(days=1),preset('P6','hash'))
    assert a==b
    s.close()


def test_relate_can_precede_new_and_unseen_id_is_rejected():
    store=Store();embed=HashEmbedder();clock=SimClock(AT);cfg=preset('P1','hash')
    turns=window();store.add_cold(turns)
    ops={'ops':[
        {'op':'relate','a':'new:1','b':'new:2','directed':True},
        {'op':'new','text':'他在实验室。','sources':['s01-t01']},
        {'op':'new','text':'他做成像实验。','sources':['s01-t01']},
        {'op':'touch','id':'m99','sources':['s01-t01']}]}
    result=run_dream(store,turns,StaticLLM(json.dumps(ops,ensure_ascii=False)),embed,clock,cfg)
    assert result.n_rejected==1
    assert store.conn.execute("SELECT a,b FROM event_edges WHERE component='relate'").fetchone()[:]==('m1','m2')
    assert store.conn.execute("SELECT reason FROM dream_ops WHERE op_index=3").fetchone()[0]=='id_not_reminded'
    store.close()


def test_malformed_operation_fields_are_rejected_individually():
    store=Store();embed=HashEmbedder();clock=SimClock(AT);cfg=preset('P1','hash')
    turns=window();store.add_cold(turns)
    ops={'ops':[
        {'op':'touch','id':['m1'],'sources':['s01-t01']},
        {'op':'new','text':'x','sources':None},
        {'op':'merge','ids':[{'id':'m1'},'m2'],'text':'x','sources':['s01-t01']},
        {'op':'relate','a':['m1'],'b':'m1'},
        {'op':'new','text':'他今晚在实验室。','sources':['s01-t01'],'salience':'bad'}]}
    result=run_dream(store,turns,StaticLLM(json.dumps(ops,ensure_ascii=False)),embed,clock,cfg)
    assert result.status=='applied' and result.n_rejected==4
    assert store.conn.execute("SELECT reason FROM dream_ops WHERE op_index=4").fetchone()[0]=='invalid_salience_defaulted'
    store.close()


def test_prompt_stays_stage_independent_after_inherited_recall():
    store=Store();embed=HashEmbedder();clock=SimClock(AT);cfg=preset('P1','hash')
    first=window();store.add_cold(first)
    initial=StaticLLM(json.dumps({'ops':[
        {'op':'new','text':'他在实验室。','sources':['s01-t01']},
        {'op':'new','text':'他在做成像。','sources':['s01-t01']}]},ensure_ascii=False))
    run_dream(store,first,initial,embed,clock,cfg)
    store.add_trace(AT+timedelta(hours=1),['m1'],[],[])
    second=window('s02',1);store.add_cold(second);clock.set(second[-1].time)
    run_dream(store,second,StaticLLM('{"ops":[]}'),embed,clock,cfg)
    third=window('s03',2);store.add_cold(third);clock.set(third[-1].time)
    merged=StaticLLM('{"ops":[{"op":"merge","ids":["m1","m2"],"text":"他常做实验。","sources":["s03-t01"]}]}')
    run_dream(store,third,merged,embed,clock,cfg)
    child=store.snapshot(embed).memories[2]
    assert any(kind=='recall' and ref=='inherit:m1' for _,_,kind,ref in child['event_rows'])
    fourth=window('s04',3)
    from memlab.integrate import remind
    a=render_prompt(store,fourth,remind(store,fourth,embed,preset('P1','hash')),AT+timedelta(days=3),preset('P1','hash'))
    b=render_prompt(store,fourth,remind(store,fourth,embed,preset('P6','hash')),AT+timedelta(days=3),preset('P6','hash'))
    assert a==b
    store.close()
