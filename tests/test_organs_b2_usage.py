"""Read-only trial report and durable count aggregation on a fake database."""
from datetime import date

from Mind.usage import record_thought
from Nervous.bus import EventBus
from scripts.usage_report import summarize


def test_usage_records_count_without_conversation_bodies(tmp_path):
    path=tmp_path/'usage.sqlite';bus=EventBus(path)
    bus.put('t:step:1:request',{'messages':[{'role':'user','content':'PRIVATE CONVERSATION'}]})
    bus.put('t:step:1:response',{'failed':False,'raw':{'choices':[],
        'usage':{'prompt_tokens':11,'completion_tokens':3}}})
    call={'id':'c1','function':{'name':'delegate','arguments':'{}'}}
    bus.put('t:1:1:action',call)
    bus.put('t:1:1:result',{'status':'error','error_kind':'missing_parameter',
                              'text':'没办成：delegate 缺少 goal，或它是空的。'})
    bus.put('t:step:1:protocol_residue',True)
    bus.put('t:step:1:duplicate_speech','PRIVATE CONVERSATION')
    stats=record_thought(bus,'t','dialogue')
    assert stats['calls']['dialogue_mind']==1
    assert stats['tools']['delegate']==1
    assert stats['tool_errors']['missing_parameter']==1
    assert stats['protocol_residue']==1
    assert stats['no_speech']==1
    default=summarize(path,date(2026,1,1))
    assert '模型调用·dialogue_mind | 1' in default
    assert '工具错误·missing_parameter | 1' in default
    assert 'PRIVATE CONVERSATION' not in default
    assert '没办成：' not in default
    detail=summarize(path,date(2026,1,1),show_errors=True)
    assert '没办成：delegate 缺少 goal' in detail
    assert 'PRIVATE CONVERSATION' not in detail
    assert len(summarize(path,date(2099,1,1)).splitlines())==2


def test_report_uses_read_only_sqlite_connection(tmp_path,monkeypatch):
    path=tmp_path/'usage.sqlite';EventBus(path).record_usage('helper:H1',{
        'type':'helper','result':'做不到','calls':{'helper':2},
        'tokens':{'helper':{'input':7,'output':5}}})
    import scripts.usage_report as report
    original=report.sqlite3.connect
    seen=[]
    def connect(uri,*,uri_flag=False,**kwargs):
        seen.append((uri,uri_flag))
        return original(uri,uri=uri_flag,**kwargs)
    monkeypatch.setattr(report.sqlite3,'connect',lambda path,**kwargs:connect(
        path,uri_flag=kwargs.get('uri',False)))
    output=report.summarize(path,date(2026,1,1))
    assert seen and 'mode=ro' in seen[0][0] and seen[0][1]
    assert '帮手结果·做不到 | 1' in output
