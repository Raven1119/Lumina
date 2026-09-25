from datetime import datetime
import json
from pathlib import Path

import pytest

from eval_set.build import simulate_hot
from lab.hotcold import HotCold
from memlab.clock import TZ
from memlab.timeparse import memory_intervals,parse
from memlab.types import Turn

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name',('dev_a','dev_b','holdout_c'))
def test_hot_matches_builder_at_every_probe(name):
    d=json.loads((ROOT/'eval_set'/'built'/f'{name}.dialogue.json').read_text())
    g=json.loads((ROOT/'eval_set'/'built'/f'{name}.gold.json').read_text())
    turns=[Turn(t['id'],s['id'],t['role'],datetime.fromisoformat(t['time']),t['text'])
           for s in d['sessions'] for t in s['turns']]
    hot=HotCold();index=0
    for p in sorted(g['probes'],key=lambda p:p['time']):
        when=datetime.fromisoformat(p['time'])
        while index<len(turns) and turns[index].time<when:
            hot.add(turns[index]);index+=1
        assert hot.ids()==simulate_hot([{'id':t.id,'role':t.role,'time':t.time} for t in turns],when)


CASES=[
 ('2026-03-29T21:30:00+08:00','昨天下午','2026-03-28T12:00:00+08:00','2026-03-28T18:00:00+08:00'),
 ('2026-03-31T13:00:00+08:00','上周二','2026-03-24T05:00:00+08:00','2026-03-25T05:00:00+08:00'),
 ('2026-04-06T12:00:00+08:00','上周三晚上','2026-04-01T18:00:00+08:00','2026-04-02T05:00:00+08:00'),
 ('2026-04-12T20:00:00+08:00','三月初','2026-03-01T05:00:00+08:00','2026-03-11T05:00:00+08:00'),
 ('2026-03-28T20:00:00+08:00','这周末','2026-03-28T05:00:00+08:00','2026-03-30T05:00:00+08:00'),
 ('2026-05-21T23:30:00+08:00','前天晚上','2026-05-19T18:00:00+08:00','2026-05-20T05:00:00+08:00'),
 ('2026-05-26T13:00:00+08:00','上周三','2026-05-20T05:00:00+08:00','2026-05-21T05:00:00+08:00'),
 ('2026-06-13T20:00:00+08:00','五月上旬','2026-05-01T05:00:00+08:00','2026-05-11T05:00:00+08:00'),
 ('2026-06-08T12:00:00+08:00','上周四','2026-06-04T05:00:00+08:00','2026-06-05T05:00:00+08:00'),
 ('2026-06-10T15:00:00+08:00','明天下午','2026-06-11T12:00:00+08:00','2026-06-11T18:00:00+08:00'),
 ('2026-09-24T21:00:00+08:00','前天晚上','2026-09-22T18:00:00+08:00','2026-09-23T05:00:00+08:00'),
 ('2026-10-13T12:00:00+08:00','上周五放学那会儿','2026-10-09T16:00:00+08:00','2026-10-09T19:00:00+08:00'),
 ('2026-10-18T20:00:00+08:00','九月中旬','2026-09-11T05:00:00+08:00','2026-09-21T05:00:00+08:00'),
 ('2026-10-06T20:00:00+08:00','国庆前一天晚上','2026-09-30T18:00:00+08:00','2026-10-01T05:00:00+08:00'),
 ('2026-10-03T12:00:00+08:00','国庆后半段','2026-10-04T05:00:00+08:00','2026-10-08T05:00:00+08:00'),
 ('2026-10-18T12:00:00+08:00','国庆那几天','2026-10-01T05:00:00+08:00','2026-10-08T05:00:00+08:00'),
 ('2026-10-02T20:00:00+08:00','上周六','2026-09-26T05:00:00+08:00','2026-09-27T05:00:00+08:00'),
]


@pytest.mark.parametrize('when,text,start,end',CASES)
def test_time_table(when,text,start,end):
    span=parse(text,datetime.fromisoformat(when))
    assert [(x.start,x.end) for x in span]==[(datetime.fromisoformat(start),datetime.fromisoformat(end))]


def test_time_gold_coverage_and_d8():
    for name in ('dev_a','dev_b','holdout_c'):
        g=json.loads((ROOT/'eval_set'/'built'/f'{name}.gold.json').read_text())
        d=json.loads((ROOT/'eval_set'/'built'/f'{name}.dialogue.json').read_text())
        by_id={t['id']:datetime.fromisoformat(t['time']) for s in d['sessions'] for t in s['turns']}
        for p in g['probes']:
            if p['category']!='时间':continue
            spans=parse(p['message'],datetime.fromisoformat(p['time']))
            assert any(span.contains(by_id[tid]) for group in p['must_surface'] for tid in group for span in spans),p['id']
    now=datetime.fromisoformat('2026-06-10T15:00:00+08:00')
    assert memory_intervals(parse('明天下午',now),now)==[]
    assert memory_intervals(parse('今天下午',now),now)==[]
    assert parse('语义不明白的时刻',now)==[]
    both=parse('昨天晚上和上周三晚上',datetime.fromisoformat('2026-04-06T12:00:00+08:00'))
    assert len(both)==2
    dawn=parse('昨天凌晨',datetime.fromisoformat('2026-04-06T03:00:00+08:00'))[0]
    assert dawn.start.isoformat()=='2026-04-05T00:00:00+08:00'
