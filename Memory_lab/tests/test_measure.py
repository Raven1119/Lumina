from datetime import datetime,timedelta
from itertools import combinations

import numpy as np

from lab.measure import chance_memories,chance_turns,measure_probe,probe_values,summarize_measure
from memlab.config import preset
from memlab.snapshot import Snapshot
from memlab.types import RawHit,RecallResult,RecalledMemory,Turn

NOW=datetime.fromisoformat('2026-04-06T12:00:00+08:00')


def brute_turns(H,groups,s):
    draws=list(combinations(range(H),s))
    return sum(all(set(d)&g for g in groups) for d in draws)/len(draws)


def brute_memories(pool,groups,k):
    draws=list(combinations(range(len(pool)),k))
    return sum(all(any(pool[i]&g for i in d) for g in groups) for d in draws)/len(draws)


def test_chance_turns_is_exact():
    for groups in ([{0,1},{5}],[{0,1,2}],[{0,1},{1,2},{7}]):
        for s in range(0,9):
            assert abs(chance_turns(8,groups,s)-brute_turns(8,groups,s))<1e-12
    assert chance_turns(8,[set()],3)==0.0
    assert chance_turns(8,[],3) is None


def test_chance_memories_is_exact():
    pool=[{'a'},{'b','c'},{'d'},{'a','d'},set(),{'x'}]
    for groups in ([{'a'}],[{'a'},{'d'}],[{'c'},{'x'},{'a'}]):
        for k in range(0,7):
            want=brute_memories(pool,groups,k) if k else 0.0
            assert abs(chance_memories(pool,groups,k)-want)<1e-12


def _turn(tid,day,hour):
    return Turn(tid,tid.split('-')[0],'user',datetime(2026,4,day,hour,tzinfo=NOW.tzinfo),tid)


def _mem(mid,sources):
    at=NOW-timedelta(days=2)
    return {'id':mid,'text':mid,'embedding':np.zeros(2,dtype=np.float32),'occurrences':(at,),
            'events':((at,1.),),'salience':1.,'sources':tuple(sources),'entities':(),'created_at':at}


def _item(m,part):
    return RecalledMemory(m['id'],m['text'],1.,1.,0.,(part,),m['sources'],'')


def fixture():
    cold=[_turn('s01-t01',1,20),_turn('s01-t03',1,21),_turn('s02-t01',2,20),_turn('s03-t01',3,20),
          _turn('s04-t01',4,20),_turn('s05-t01',5,9),_turn('s05-t03',5,10)]
    story=_mem('m1',['s01-t03','s02-t01','s03-t01','s04-t01','s05-t01'])   # 5 sessions: a storyline
    focused=_mem('m2',['s01-t01'])
    trap=_mem('m3',['s02-t01'])
    other=_mem('m4',['s05-t03'])
    snap=Snapshot(3,(story,focused,trap,other),(),(),frozenset(),tuple(cold),None)
    return snap,story,focused,trap,other


def test_views_rank_specific_written_and_time():
    snap,story,focused,trap,other=fixture()
    probe={'category':'时间','must_surface':[['s01-t01'],['s01-t03']],'must_not_surface':['s02-t01']}
    diagnostics={'time_intervals':[('2026-04-01T18:00:00+08:00','2026-04-02T05:00:00+08:00')],
                 'top_scores':[{'id':'m3','score':.9},{'id':'m1','score':.5},{'id':'m2','score':.4},
                               {'id':'m4','score':0.0}]}
    result=RecallResult(near=(_item(trap,'near'),_item(story,'near')),core=(_item(focused,'core'),),
                        raw=(RawHit('s01-t01',NOW,'user','',1.,'interval'),),diagnostics=diagnostics)
    m=measure_probe(result,probe,snap,NOW,preset('P6','hash'))
    assert m['size']['cue']==5 and m['size']['core']==1 and m['size']['cold']==7 and m['size']['pool']==4
    # Core is cue-independent and is not credited to the cue view.
    assert m['complete']['cue'] is False and m['complete']['core'] is False
    assert m['complete']['cue_raw'] is True and m['complete']['all'] is True
    # m3 hits only the trap; m1 covers group 2; m2 (rank 3) is the first to hit group 1.
    assert m['trap_rank']==1
    assert m['rank']['group_ranks']==[3,2]
    assert m['rank']['complete_at']=={'1':False,'3':True,'6':True}
    assert abs(m['rank']['mrr']-(1/3+1/2)/2)<1e-12
    assert m['written_complete'] is True
    # The storyline memory spans 5 sessions and does not count as a specific hit.
    assert m['specific_complete'] is False
    assert m['time']['intervals']==1
    assert abs(m['time']['interval_share']-1/6)<1e-12
    assert m['time']['interval_complete'] is False
    # Two memories drawn from four: must include m2 (only hitter of group 1) and m1 (only hitter of group 2).
    assert abs(m['chance_memories_cue']-1/6)<1e-12


def test_no_groups_and_summary_uses_primary_variant():
    snap,story,focused,trap,other=fixture()
    cfg=preset('P6','hash')
    empty=RecallResult(diagnostics={'top_scores':[]})
    m=measure_probe(empty,{'must_surface':[],'must_not_surface':['s02-t01']},snap,NOW,cfg)
    assert m['complete'] is None and m['rank'] is None and m['trap_rank'] is None
    hit=RecallResult(near=(_item(focused,'near'),),diagnostics={'top_scores':[{'id':'m2','score':1.}]})
    probe={'must_surface':[['s01-t01']],'must_not_surface':[]}
    good=measure_probe(hit,probe,snap,NOW,cfg)
    assert good['time'] is None   # time metrics only for 时间-category probes
    records=[{'probe_id':'X1','category':'实体','variant_days':0,'measure':good},
             {'probe_id':'X2','category':'保留','variant_days':0,'measure':good},
             {'probe_id':'X2','category':'保留','variant_days':60,'measure':m},
             {'probe_id':'X3','category':'时间','variant_days':0,'not_scored':True,'measure':good}]
    s=summarize_measure(records)
    assert set(s['by_category'])=={'实体','保留'}
    assert s['by_category']['实体']['complete_at_1']==1.0
    assert s['by_category']['保留']['n_with_groups']==0
    assert s['overall']['n']==1
    assert probe_values(records,'complete_at_1')=={'X1':1.0}
