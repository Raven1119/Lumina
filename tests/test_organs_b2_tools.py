"""Native Mind tool contracts with temporary bus and workspace only."""
import json
from types import SimpleNamespace

from Mind.tools import MindTools
from Nervous.bus import EventBus
from config.lumina import load_config


def call(name, arguments, call_id='call-1'):
    return {'id': call_id, 'type': 'function',
            'function': {'name': name, 'arguments': arguments}}


def test_tool_errors_are_results_and_never_publish_actions(tmp_path):
    bus = EventBus(tmp_path/'bus.sqlite')
    config = load_config(overrides={'workspace': {'path': str(tmp_path/'workspace')}})
    tools = MindTools(bus, SimpleNamespace(), config)
    user = {'created_at': '2026-10-01T00:00:00+00:00'}
    cases = [
        (call('delegate', '{}'), '缺少 goal', 'missing_parameter'),
        (call('delegate', 'broken'), '参数没看懂', 'invalid_json'),
        (call('unknown', '{}'), '没有叫 unknown 的工具', 'unknown_tool'),
        (call('answer_helper', '{"helper":"H1","content":"是"}'), '不存在或已经结束', 'helper_unavailable'),
        (call('read_file', '{"path":"../secret"}'), '没读到：', 'read_failed'),
    ]
    for index, (request, expected, reason) in enumerate(cases):
        result = tools.execute(f't:{index}', request, user)
        assert expected in result['text']
        assert result['error_kind'] == reason
        assert tools.execute(f't:{index}', request, user) == result
    assert not bus.pending('execution')


def test_delegate_is_idempotent_and_tool_limit_reports_error(tmp_path):
    bus = EventBus(tmp_path/'bus.sqlite')
    config = load_config()
    tools = MindTools(bus, SimpleNamespace(), config)
    user = {'created_at': '2026-10-01T00:00:00+00:00'}
    request = call('delegate', json.dumps({'goal': '写表', 'reason': '核对',
        'acceptance': '文件存在', 'context': '合成数据'}))
    result = tools.execute('t:1', request, user)
    assert result['text'].startswith('已派出帮手 H')
    assert tools.execute('t:1', request, user) == result
    assert len(bus.pending('execution')) == 1
    blocked = tools.execute('t:2', request, user, too_many=True)
    assert blocked['error_kind'] == 'too_many'
    assert len(bus.pending('execution')) == 1


def test_reasoning_and_tool_results_keep_provider_order():
    from Mind.runner import DialogueRunner
    message = {'content': '{"说":""}', 'reasoning_content': 'private reasoning'}
    calls = [call('read_file', '{"path":"a"}', 'a'),
             call('recall', '{"clue":"b"}', 'b')]
    continuation = DialogueRunner._continue_with_tools(message, calls,
        [{'text': 'first'}, {'text': 'second'}])
    assert continuation[0]['reasoning_content'] == 'private reasoning'
    assert [item['tool_call_id'] for item in continuation[1:]] == ['a', 'b']
    assert [item['content'] for item in continuation[1:]] == ['first', 'second']
