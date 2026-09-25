from dataclasses import replace
from datetime import datetime,timedelta
import subprocess
import sys

import numpy as np
import pytest

from lab.scoring import score_probe
from memlab.config import preset
from memlab.embed import HashEmbedder
from memlab.integrate import run_dream
from memlab.layers import build_layers,diffuse,transition
from memlab.rawwindow import search_raw
from memlab.recall import recall
from memlab.snapshot import Snapshot
from memlab.store import Store
from memlab.clock import SimClock
from lab.llm import LLMResult
from memlab.types import Cue,RawHit,RecallResult,Turn

AT=datetime.fromisoformat('2026-03-04T23:50:00+08:00')


class FixedEmbedder:
    identity={'model':'fixed','dimension':2}
    def encode(self,texts):return np.tile(np.array([[1.,0.]],dtype=np.float32),(len(texts),1))


def mem(mid,v,when):
    return {'id':mid,'text':mid,'embedding':np.array(v,dtype=np.float32),
            'occurrences':(when,),'events':((when,1.),),'salience':1.,
            'sources':(mid+'-t01',),'entities':(),'created_at':when}


def test_diffusion_conserves_mass_and_zero_lambda():
    start=np.array([1.,0.,0.])
    P=np.array([[0.,1.,0.],[0.,0.,1.],[0.,0.,1.]])
    assert np.array_equal(diffuse(start,P,0,3),start)
    got=diffuse(start,P,.5,3)
    assert got.sum()==pytest.approx(1)
    assert all(x>=0 for x in got)


def test_degree_cap_and_event_decay():
    memories=tuple(mem('m'+str(i),[1,0],AT) for i in range(1,8))
    snapshot=Snapshot(1,memories,(),(),frozenset(),(),FixedEmbedder())
    cfg=replace(preset('P2g','hash'),k_sem=6,c_sem=.3,degree_cap=2)
    rows=build_layers(snapshot,AT,cfg)['semantic']
    assert all(len(row)<=2 for row in rows.values())
    event=({'a':'m1','b':'m2','component':'relate','weight':1.,'directed':0,
            'last_reinforced':AT.isoformat(),'note':''},)
    snap=Snapshot(1,memories,(),event,frozenset(),(),FixedEmbedder())
    cfg=replace(preset('P5','hash'),semantic_layer=False,time_layer=False,entity_layer=False)
    assert build_layers(snap,AT,cfg)['event']
    assert not build_layers(snap,AT+timedelta(days=1000),cfg)['event']


def test_cooccurrence_excludes_merge_parent_and_near_deduplicates():
    entries=(mem('m1',[1,0],AT),mem('m2',[1,0],AT),mem('m3',[0,1],AT+timedelta(days=1)))
    cfg=replace(preset('P6','hash'),semantic_layer=False,time_layer=False,entity_layer=False,
                relate_edges=False,merge_edges=False,corecall=False)
    plain=Snapshot(1,entries,(),(),frozenset(),(),FixedEmbedder())
    linked=Snapshot(1,entries,(),(),frozenset({('m2','m1')}),(),FixedEmbedder())
    assert build_layers(plain,AT+timedelta(days=1),cfg)['event'][0][1]>0
    assert 0 not in build_layers(linked,AT+timedelta(days=1),cfg)['event']
    recall_cfg=replace(preset('P1','hash'),semantic_seeds=2,near_cap=2,raw_enabled=False)
    selected=recall(plain,Cue('x'),AT+timedelta(days=1),recall_cfg)
    assert len(selected.near)==1


def test_remote_only_nonseed_and_dormant_core_excluded():
    now=AT+timedelta(days=1)
    m1=mem('m1',[1,0],AT)
    m2=mem('m2',[.7,.7141428],AT)
    m3=mem('m3',[0,1],AT-timedelta(days=200))
    snapshot=Snapshot(1,(m1,m2,m3),(),(),frozenset(),(),FixedEmbedder())
    cfg=replace(preset('P6','hash'),semantic_seeds=1,c_sem=.5,
                near_cap=1,core_cap=3,time_channel=False,entity_channel=False,
                time_layer=False,entity_layer=False,event_layer=False,raw_enabled=False)
    result=recall(snapshot,Cue('x'),now,cfg)
    assert [x.id for x in result.near]==['m1']
    assert [x.id for x in result.remote]==['m2']
    assert all(x.id!='m3' for x in result.core)
    assert result.diagnostics['activation_total']==pytest.approx(1)


def test_raw_interval_and_snapshot_refresh_on_cold_append():
    s=Store();embed=HashEmbedder()
    t=Turn('s01-t01','s01','user',AT,'昨晚的小周和成像实验')
    s.add_cold([t]);old=s.snapshot(embed)
    assert len(old.cold)==1
    next_turn=Turn('s01-t02','s01','assistant',AT+timedelta(minutes=1),'好')
    s.add_cold([next_turn]);new=s.snapshot(embed)
    assert len(old.cold)==1 and len(new.cold)==2 and old.version==new.version
    now=AT+timedelta(days=1)
    cfg=preset('B1','hash')
    result=recall(new,Cue('昨天晚上小周',hot_ids=frozenset({'s01-t02'})),now,cfg)
    assert any(x.turn_id=='s01-t01' and x.via=='interval' for x in result.raw)
    assert all(x.turn_id!='s01-t02' for x in result.raw)
    s.close()


def test_forgetting_scored_only_on_variant():
    probe={'category':'淡忘','must_surface':[],'must_not_surface':['s01-t01'],'soft':False}
    result=RecallResult(raw=(RawHit('s01-t01',AT,'user','old',1,'content'),))
    assert score_probe(result,probe,variant=False)['forgetting_pass'] is None
    assert score_probe(result,probe,variant=True)['forgetting_pass'] is False


def test_old_graph_snapshot_survives_dream_commit():
    class Static:
        def __init__(self,text):self.text=text
        def complete(self,**kwargs):return LLMResult(self.text,{},False,'k')
    store=Store();embed=HashEmbedder();cfg=preset('P1','hash');clock=SimClock(AT)
    first=[Turn('s01-t01','s01','user',AT,'今晚在实验室做实验'),
           Turn('s01-t02','s01','assistant',AT+timedelta(seconds=1),'嗯')]
    store.add_cold(first)
    run_dream(store,first,Static('{"ops":[{"op":"new","text":"他在实验室。","sources":["s01-t01"]}]}'),embed,clock,cfg)
    old=store.snapshot(embed)
    with pytest.raises(TypeError):old.memories[0]['text']='mutated'
    old_result=recall(old,Cue('实验室'),AT+timedelta(days=1),cfg)
    second=[Turn('s02-t01','s02','user',AT+timedelta(days=2),'现在在家做实验'),
            Turn('s02-t02','s02','assistant',AT+timedelta(days=2,seconds=1),'嗯')]
    store.add_cold(second);clock.set(second[-1].time)
    run_dream(store,second,Static('{"ops":[{"op":"revise","id":"m1","text":"他现在在家做实验。","sources":["s02-t01"]}]}'),embed,clock,cfg)
    new=store.snapshot(embed)
    assert old.version==1 and new.version==2
    assert old.memories[0]['text']=='他在实验室。'
    assert new.memories[0]['text']=='他现在在家做实验。'
    assert recall(old,Cue('实验室'),AT+timedelta(days=1),cfg)==old_result
    store.close()


def test_offline_imports_do_not_load_model_dependencies():
    code="import sys, memlab, lab; assert not {'torch','sentence_transformers','httpx'} & set(sys.modules)"
    subprocess.run([sys.executable,'-c',code],check=True)
