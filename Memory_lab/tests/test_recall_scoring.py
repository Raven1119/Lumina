import json
import math
from datetime import datetime,timedelta

import numpy as np
import pytest

from lab.llm import CachedLLM,FakeLLM,LLMResult
from lab.replay import run_set
from lab.scoring import intersects,score_probe
from memlab.clock import SimClock
from memlab.config import preset
from memlab.embed import HashEmbedder
from memlab.integrate import consume_traces,run_dream
from memlab.layers import ppmi,time_kernel,transition
from memlab.recall import recall
from memlab.snapshot import Snapshot
from memlab.store import Store
from memlab.strength import strength
from memlab.types import Cue,RecalledMemory,RecallResult,RawHit,Turn

AT=datetime.fromisoformat('2026-03-04T23:50:00+08:00')


def test_strength_anchors_dormancy_and_recall_dedup():
    cfg=preset('P6','hash')
    for days,expected in [(21,.5),(60,.1)]:
        _,pi,_=strength([(AT,1)],1,AT+timedelta(days=days),cfg)
        assert pi==pytest.approx(expected,rel=.01)
    assert not strength([(AT,1)],1,AT+timedelta(days=60),cfg)[2]
    assert strength([(AT,1)],1,AT+timedelta(days=120),cfg)[2]
    s=Store();emb=HashEmbedder();clock=SimClock(AT)
    window=[Turn('s01-t01','s01','user',AT,'我在做实验'),Turn('s01-t02','s01','assistant',AT+timedelta(seconds=1),'好')]
    s.add_cold(window)
    class Static:
        def complete(self,**kwargs):return LLMResult('{"ops":[{"op":"new","text":"他在做实验。","sources":["s01-t01"]}]}',{},False,'x')
    run_dream(s,window,Static(),emb,clock,cfg)
    s.add_trace(AT+timedelta(hours=1),['m1'],[],['m1'])
    s.add_trace(AT+timedelta(hours=2),['m1'],[],[])
    with s.dream_transaction():consume_traces(s,cfg,AT+timedelta(hours=3),'d2')
    assert s.conn.execute("SELECT COUNT(*) FROM strength_events WHERE kind='recall'").fetchone()[0]==1
    s.add_trace(AT+timedelta(hours=4),[],[],['m1'])
    with s.dream_transaction():consume_traces(s,cfg,AT+timedelta(hours=5),'d3')
    assert s.conn.execute("SELECT COUNT(*) FROM strength_events WHERE kind='recall'").fetchone()[0]==1
    s.close()


def test_layers_mass_ppmi_direction_and_dangling():
    cfg=preset('P6','hash')
    assert time_kernel(19,6)==0
    assert time_kernel(6,6)==pytest.approx(math.exp(-1))
    assert ppmi(2,2,2,10,3)==pytest.approx(math.log(5)*(1-math.exp(-2/3)))
    one=np.zeros(256,dtype=np.float32);one[0]=1
    two=np.zeros(256,dtype=np.float32);two[1]=1
    def mem(mid,v,t):return {'id':mid,'embedding':v,'occurrences':(t,), 'entities':('e1',) if mid!='m3' else (),
                            'events':((t,1),),'salience':1,'sources':(),'text':mid,'created_at':t}
    snapshot=Snapshot(1,(mem('m1',one,AT),mem('m2',two,AT+timedelta(hours=1)),
                         mem('m3',two,AT+timedelta(days=2))),
                      ({'id':'e1','name':'小周','aliases':()},),
                      ({'a':'m1','b':'m2','component':'relate','weight':1,'directed':1,
                        'last_reinforced':AT.isoformat(),'note':''},),
                      frozenset({('m3','m2')}),(),HashEmbedder())
    P=transition(snapshot,AT+timedelta(hours=1),cfg)
    assert np.allclose(P.sum(axis=1),1)
    assert P[0,1]>P[1,0]  # directed event weight and reverse rho
    assert P[3,0]==pytest.approx(.5) and P[3,1]==pytest.approx(.5)  # entity degree
    # A node with no enabled-layer edges gets an exact self-loop.
    no_layers=preset('P1','hash')
    P0=transition(snapshot,AT,no_layers)
    assert np.allclose(P0,np.eye(4))


def test_recall_is_read_only_and_raw_window_excludes_hot():
    s=Store();emb=HashEmbedder();cfg=preset('P6','hash');clock=SimClock(AT)
    turns=[Turn('s01-t01','s01','user',AT,'小周在实验室做成像'),
           Turn('s01-t02','s01','assistant',AT+timedelta(seconds=30),'那台仪器还好用吗')]
    s.add_cold(turns)
    class Static:
        def complete(self,**kwargs):return LLMResult('{"ops":[{"op":"new","text":"小周在实验室做成像。","sources":["s01-t01"],"entities":[{"name":"小周"}]}]}',{},False,'x')
    run_dream(s,turns,Static(),emb,clock,cfg)
    old=s.snapshot(emb);before=s.version
    future=AT+timedelta(days=1)
    result=recall(old,Cue('小周的成像实验',(),frozenset({'s01-t02'})),future,cfg)
    assert s.version==before
    assert all(hit.turn_id!='s01-t02' for hit in result.raw)
    assert any(hit.turn_id=='s01-t01' for hit in result.raw)
    assert result.near or result.core
    s.add_cold([Turn('s02-t01','s02','user',future,'今天的新事')])
    assert old.version==s.version  # Cold alone never changes graph version
    s.close()


def test_source_scoring_loose_and_correct_plus_distractor():
    m=RecalledMemory('m1','x',1,1,1,('near',),('s01-t01','s02-t01'),'today')
    bad=RecalledMemory('m2','y',.5,1,1,('near',),('s02-t01',),'today')
    result=RecallResult(near=(m,bad),raw=(RawHit('s02-t01',AT,'user','x',.1,'content'),))
    p={'category':'接续','must_surface':[['s01-t03']],'must_not_surface':['s02-t01'],'soft':False}
    scored=score_probe(result,p)
    assert not scored['all']['complete_strict'] and scored['all']['complete_loose']
    exact=score_probe(result,{**p,'must_surface':[['s01-t01']]})
    assert exact['only_distractor']==1 # mixed-source m1 is not interference-only
    assert scored['raw_distractor']==1
    assert intersects({'s01-t01'},{'s01-t03'},True)


def test_fake_e2e_byte_determinism_and_holdout_guard(tmp_path):
    embed=HashEmbedder();cache=tmp_path/'llm'
    first=CachedLLM(FakeLLM(embed),cache)
    a=run_set('dev_a','P6',first,embed,tmp_path/'a')
    second=CachedLLM(FakeLLM(embed),cache,cache_only=True)
    b=run_set('dev_a','P6',second,embed,tmp_path/'b')
    assert b['timing']['new_model_calls']==0
    for name in ('summary.json','probes.jsonl','memories.md'):
        assert (tmp_path/'a'/name).read_bytes()==(tmp_path/'b'/name).read_bytes()
    assert a['summary']['probe_version_checks']>30
    with pytest.raises(ValueError,match='holdout'):
        run_set('holdout_c','P6',second,embed,tmp_path/'forbidden')
