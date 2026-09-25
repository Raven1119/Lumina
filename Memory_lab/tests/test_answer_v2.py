"""Offline contracts for answer timing, cache purpose, scope and recall cutoffs."""
from dataclasses import replace
from datetime import datetime,timedelta
from importlib import import_module
import json

import numpy as np
import pytest

from lab.answer import answer_messages
from lab.hotcold import HotCold
from lab.llm import CacheMiss,CachedLLM,FakeLLM
from lab.replay import run_set
from memlab.config import preset
from memlab.embed import HashEmbedder
from memlab.snapshot import Snapshot
from memlab.types import Cue,Turn
recall_module=import_module('memlab.recall')


def at(value):return datetime.fromisoformat(value)


def test_answer_messages_v1_and_v2_exact_time_formats():
    first=Turn('t1','s','user',at('2026-04-04T12:00:00+08:00'),'昨天下午有点累')
    last=Turn('t2','s','assistant',at('2026-04-04T12:30:00+08:00'),'先歇一会儿')
    probe={'message':'好累'}
    hot=[first,last]
    now=at('2026-04-05T01:30:00+08:00')
    summary_until=at('2026-04-03T18:00:00+08:00')
    assert answer_messages(probe,hot,'旧摘要',summary_until,now,'v1')==[
        {'role':'user','content':'近期对话摘要：旧摘要'},
        {'role':'user','content':'昨天下午有点累'},
        {'role':'assistant','content':'先歇一会儿'},
        {'role':'user','content':'好累'}]
    expected=[
        {'role':'user','content':'近期对话摘要（截至 4月3日）：旧摘要'},
        {'role':'user','content':'（4月4日 周六 12:00）昨天下午有点累'},
        {'role':'assistant','content':'先歇一会儿'},
        {'role':'user','content':'（4月5日 周日 01:30，距上一句13小时）好累'}]
    assert answer_messages(probe,hot,'旧摘要',summary_until,now,'v2')==expected
    assert answer_messages(probe,hot,'旧摘要',summary_until,now,'v3')==expected
    soon=at('2026-04-04T13:00:00+08:00')
    assert answer_messages(probe,hot,'',None,soon,'v2')[-1]['content']=='（4月4日 周六 13:00）好累'
    assert answer_messages(probe,[], '',None,now,'v2')==[
        {'role':'user','content':'（4月5日 周日 01:30）好累'}]
    after=at('2026-06-11T18:00:00+08:00')
    old=Turn('t3','s','assistant',after-timedelta(days=61),'上次聊到这里')
    assert answer_messages({'message':'食堂今天的菜又咸了'},[old],'',None,after,'v2')[-1]['content']==(
        '（6月11日 周四 18:00，距上一句61天）食堂今天的菜又咸了')


def test_summary_timestamp_is_last_evicted_turn():
    hot=HotCold();start=at('2026-04-01T08:00:00+08:00')
    turns=[Turn(f't{i}','s','user' if i%2==0 else 'assistant',start+timedelta(minutes=i),str(i))
           for i in range(26)]
    for turn in turns[:-1]:assert hot.add(turn)==[]
    moved=hot.add(turns[-1])
    assert moved==turns[:14]
    assert hot.summary_until==turns[13].time


def test_new_calls_are_restricted_by_purpose(tmp_path):
    cache=CachedLLM(FakeLLM(),tmp_path/'llm',allow_new={'answer'})
    kwargs={'system':'s','messages':[{'role':'user','content':'m'}],'max_tokens':20}
    for purpose in ('dream','summary'):
        with pytest.raises(CacheMiss):cache.complete(purpose=purpose,**kwargs)
    cache.complete(purpose='answer',**kwargs)
    assert cache.new_calls_by_purpose=={'answer':1}
    assert cache.complete(purpose='answer',**kwargs).cache_hit
    with pytest.raises(CacheMiss):
        CachedLLM(FakeLLM(),tmp_path/'other',cache_only=True,allow_new={'answer'}).complete(
            purpose='answer',**kwargs)


def test_recall_cutoff_filters_outputs_but_not_diffusion(monkeypatch):
    class Embedder:
        def encode(self,texts):return np.array([[1.,0.,0.,0.,0.] for _ in texts],dtype=np.float32)
    when=at('2026-04-05T12:00:00+08:00')
    embeddings=[np.array([1.,0.,0.,0.,0.],dtype=np.float32),
                np.array([.9,.435,0.,0.,0.],dtype=np.float32),
                np.array([.2,0.,.98,0.,0.],dtype=np.float32),
                np.array([.1,0.,0.,.995,0.],dtype=np.float32),
                np.array([0.,0.,0.,0.,1.],dtype=np.float32)]
    memories=tuple({'id':f'm{i+1}','text':f'm{i+1}','embedding':embeddings[i],
                    'sources':(), 'occurrences':(when,), 'created_at':when,
                    'events':((when,1),),'salience':1,'entities':()}
                   for i in range(5))
    snap=Snapshot(1,memories,(),(),frozenset(),(),Embedder())
    monkeypatch.setattr(recall_module,'strengths',lambda memories,now,cfg:
                        [(b,pi,False) for b,pi in zip((10,9,8,7,6),(.05,.9,.05,.9,.9))])
    monkeypatch.setattr(recall_module,'transition',lambda snapshot,now,cfg:
                        np.full((5,5),.2))
    cfg=replace(preset('P6r10'),semantic_seeds=2,near_cap=1,core_cap=1,
                dup_cos=.99,remote_ratio=0,raw_enabled=False,time_channel=False,
                entity_channel=False)
    cue=Cue('test',(),frozenset())
    result=recall_module.recall(snap,cue,when,cfg)
    sections=[result.near,result.remote,result.core]
    assert all(section for section in sections)
    assert {m.id for section in sections for m in section}=={'m2','m4','m5'}
    assert result.diagnostics['activation_total']==pytest.approx(1.0)
    assert recall_module.recall(snap,cue,when,replace(cfg,pi_recall=0))==recall_module.recall(
        snap,cue,when,replace(preset('P6'),semantic_seeds=2,near_cap=1,core_cap=1,
                              dup_cos=.99,remote_ratio=0,raw_enabled=False,time_channel=False,
                              entity_channel=False))


def test_primary_scope_answers_only_gold_variants(tmp_path):
    embedder=HashEmbedder()
    run_set('dev_a','B1',CachedLLM(FakeLLM(embedder),tmp_path/'cache'),embedder,
            tmp_path/'run',answer=True,answer_prompt='v2',answer_scope='primary')
    rows=[json.loads(line) for line in (tmp_path/'run'/'probes.jsonl').read_text().splitlines()]
    assert sum(r['answer'] is not None for r in rows)==35
    assert all((r['answer'] is not None)==(r['variant_days']==0 or
                r['category'] in ('淡忘','保留') and r['variant_days']==60) for r in rows)
    assert all(r['answer']['context']['prompt_version']=='v2' for r in rows if r['answer'])
