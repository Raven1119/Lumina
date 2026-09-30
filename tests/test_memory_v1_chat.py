"""Offline checks of the production Chat and memory-v1 boundary."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from Conversation_Memory.answer import (answer_messages, fallback_answer_v5,
                                        parse_answer_v5_tolerant)
from Conversation_Memory.engine.types import RecallResult
from Conversation_Memory.facade import DreamOutcome, MemoryRead
from Conversation_Memory.facade import MemoryV1
from Conversation_Memory.engine.embed import BGE_M3_REVISION, BGE_M3_WEIGHTS_SHA256
from Conversation_Memory.engine.types import Turn
from core.contracts import ChatRequest, DraftTurn
from core.draft_store import HotDraftSummary, JsonlDraftStore
from core.hot_draft_compactor import HotDraftCompactor
from core.main import create_app, should_auto_dream
from core.message_runtime import MessageRuntime
from core.model_client import DeepSeekAnthropicModelClient, TruncatedSummaryError
from core.cold_draft_store import ColdDraftStore

NOW = datetime(2026, 1, 2, 10, tzinfo=timezone(timedelta(hours=8)))


class AnswerModel:
    client_kind = 'model'
    def __init__(self, answer='{"理解":"私有","借鉴":"私有","顺带":"私有","回复":"你好"}'):
        self.answer = answer
        self.calls = []
    def complete_answer(self, system, messages):
        self.calls.append((system, messages))
        return self.answer
    def summarize_hot_draft(self, old, moved):
        return '摘要'


class FakeMemory:
    def __init__(self):
        self.traces = []
        self._local = SimpleNamespace(embedder=object())
        self._dream_lock = threading.Lock()
        self.dream_calls = 0
    def recall_and_render(self, message, hot, now):
        return MemoryRead('【记忆】\nm1｜他喜欢茶', ('m1',), RecallResult())
    def record_trace(self, *args):
        self.traces.append(args)
    def inspect(self, now, limit=100):
        return {'memory_count': 1, 'pattern_count': 0, 'memories': [
            {'id':'m1','text':'他喜欢茶','time_label':'昨天','pi':1.0,
             'pattern':False,'source_count':1}], 'patterns':[], 'dream_log':[]}
    def has_cold_cursor(self): return False
    def auto_paused(self): return False
    def unintegrated_turn_count(self, cold): return 0
    def dream_once(self, cold, *, manual=False, now=None):
        self.dream_calls += 1
        return DreamOutcome('no_window')


class FixedClock:
    def now(self): return NOW


def test_single_answer_path_and_trace_after_persisted_turn(tmp_path):
    memory, model = FakeMemory(), AnswerModel()
    hot = JsonlDraftStore(tmp_path/'hot.jsonl')
    runtime = MessageRuntime(hot_store=hot, model_client=model,
        chat_background='人设', memory=memory, clock=FixedClock())
    result = runtime.handle_chat(ChatRequest(message='我喜欢什么？'))
    assert result.response.response.text == '你好'
    assert len(model.calls) == 1
    assert '他喜欢茶' in model.calls[0][0]
    assert len(hot.list_all_raw()) == 2
    assert memory.traces[0][0] == hot.list_all_raw()[-1].turn_id
    assert memory.traces[0][3]['理解'] == '私有'
    assert memory.traces[0][4] == '你好'


def test_recall_failure_is_empty_and_tolerant_answer_is_private(tmp_path):
    memory, model = FakeMemory(), AnswerModel('"理解":"内部","回复":"公开"')
    def fail(*_): raise RuntimeError('recall failed')
    memory.recall_and_render = fail
    runtime = MessageRuntime(hot_store=JsonlDraftStore(tmp_path/'hot.jsonl'),
        model_client=model, chat_background='人设', memory=memory, clock=FixedClock())
    result = runtime.handle_chat(ChatRequest(message='你好'))
    assert result.response.response.text == '公开'
    assert 'memory_recall_failed' in result.events
    assert '他喜欢茶' not in model.calls[0][0]
    assert parse_answer_v5_tolerant('{"回复":"正常"}')[0] == '正常'
    assert fallback_answer_v5('自然语言回答') == '自然语言回答'


def test_shared_request_dated_summary_and_legacy_without_date():
    old = NOW-timedelta(days=2)
    hot = [SimpleNamespace(role='user', text='你好', time=old)]
    dated = answer_messages({'message':'在吗'}, hot, '旧摘要', old, NOW)
    assert '截至 12月31日' in dated[0]['content']
    assert '距上一句2天' in dated[-1]['content']
    legacy = answer_messages({'message':'在吗'}, hot, '旧摘要', None, NOW)
    assert legacy[0]['content'] == '近期对话摘要：旧摘要'


def test_prefill_payload_and_summary_stop_reason():
    bodies = []
    replies = [
        {'content':[{'type':'text','text':'好"}'}], 'usage':{'input_tokens':3,'output_tokens':2}},
        {'content':[{'type':'text','text':'短'}], 'stop_reason':'max_tokens'},
        {'content':[{'type':'text','text':'完整'}], 'stop_reason':'end_turn'},
    ]
    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0))
    client = DeepSeekAnthropicModelClient('test','https://example.invalid','deepseek-v4-pro',
        http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    client.answer_prefill_enabled = True
    assert client.complete_answer('system',[{'role':'user','content':'hi'}]).startswith('{"理解": "')
    assert bodies[0]['messages'][-1] == {'role':'assistant','content':'{"理解": "'}
    assert client.summarize_hot_draft(None,[]) == '完整'
    assert [item['max_tokens'] for item in bodies] == [1000,2000,3000]


def test_double_truncated_summary_preserves_old_and_cold(tmp_path):
    hot, cold = JsonlDraftStore(tmp_path/'hot.jsonl'), ColdDraftStore(tmp_path/'cold.jsonl')
    old = HotDraftSummary('旧摘要',1,2,NOW.astimezone(timezone.utc).isoformat(),NOW.isoformat())
    turns = [DraftTurn(role='user' if i%2==0 else 'assistant', text=f't{i}',
        turn_id=f'{i:032x}',created_at=NOW+timedelta(minutes=i),
        source_timezone='Asia/Shanghai',timezone_source='configured_default') for i in range(6)]
    hot.replace_contents_atomically(old, turns)
    def truncated(*_): raise TruncatedSummaryError('truncated')
    compact = HotDraftCompactor(hot,cold,tmp_path/'state.json',summarizer=truncated,
                                retain_recent_raw_turns=2,max_raw_turns_before_compression=4)
    result = compact.maybe_compact()
    assert result.status == 'completed' and not result.summary_updated
    assert hot.read_summary() == old
    assert len(hot.list_all_raw()) == 2
    assert len(cold.list_all_turns()) == 4
    assert compact.last_summary_truncated


def test_api_contracts_and_memory_page(tmp_path, monkeypatch):
    monkeypatch.setattr('core.main.build_memory_model_from_env', lambda *_: None)
    memory, model = FakeMemory(), AnswerModel()
    app = create_app(draft_store_path=tmp_path/'hot.jsonl',
        cold_draft_path=tmp_path/'cold.jsonl', memory_dir=tmp_path/'mem',
        model_client=model, memory=memory, env_file_path=None,
        enable_compaction=False, clock=FixedClock())
    client = TestClient(app)
    status = client.get('/api/status').json()
    assert status['memory']['memory_count'] == 1
    assert status['dream']['unintegrated_cold_turns'] == 0
    assert 'pending_segments' not in status['dream']
    assert client.get('/api/memory').json()['memories'][0]['text'] == '他喜欢茶'
    assert client.post('/api/chat',json={'message':'问题'}).json()['response']['text'] == '你好'
    assert client.post('/api/dream/run').json()['status'] == 'no_window'
    assert memory.dream_calls == 1
    assert 'unintegrated_cold_turns' in Path('edge/static/app.js').read_text()


@pytest.mark.parametrize('disabled', ('real_mode','recall_enabled','embed_available',
                                     'has_cursor','paused','pending_turns'))
def test_auto_dream_requires_every_gate(disabled):
    kwargs = dict(real_mode=True, recall_enabled=True, embed_available=True,
                  has_cursor=True, paused=False, pending_turns=40, trigger_turns=40)
    assert should_auto_dream(**kwargs)
    kwargs[disabled] = True if disabled == 'paused' else 39 if disabled == 'pending_turns' else False
    assert not should_auto_dream(**kwargs)


class TinyBGE:
    identity = {'model':'BAAI/bge-m3', 'revision':BGE_M3_REVISION,
                'weights_sha256':BGE_M3_WEIGHTS_SHA256, 'dimension':4}
    def encode(self, texts):
        return np.array([[1.,0.,0.,0.] for _ in texts], dtype=np.float32)


class ScriptedDreamModel:
    def __init__(self, response): self.response = response
    def complete(self, **_):
        return SimpleNamespace(text=self.response, usage={'input_tokens':0,'output_tokens':0},
                               cache_hit=False, cache_key='offline')


def _cold_pair():
    return [Turn('0'*32,'s1','user',NOW,'我养了一只猫'),
            Turn('1'*32,'s1','assistant',NOW+timedelta(minutes=1),'我记住了')]


def test_dream_alias_cursor_pause_and_busy(tmp_path):
    memory = MemoryV1(tmp_path/'memory', embedder_factory=TinyBGE,
        model_factory=lambda: ScriptedDreamModel('invalid json'))
    cold = _cold_pair()
    memory.set_cursor_to_start()
    assert memory._dream_lock.acquire(False)
    assert memory.dream_once(cold).status == 'busy'
    memory._dream_lock.release()
    for _ in range(3):
        assert memory.dream_once(cold).status == 'failed'
        assert memory.unintegrated_turn_count(cold) == 2
    assert memory.auto_paused()
    assert memory.dream_once(cold).status == 'paused'

    valid = json.dumps({'ops':[{'op':'new','text':'他养了一只猫',
                               'salience':2,'sources':['t01']}], 'used':[]}, ensure_ascii=False)
    restored = MemoryV1(tmp_path/'fresh', embedder_factory=TinyBGE,
                        model_factory=lambda: ScriptedDreamModel(valid))
    restored.set_cursor_to_start()
    assert restored.dream_once(cold).status == 'applied'
    source = restored._store().conn.execute('SELECT turn_id FROM memory_sources').fetchone()[0]
    assert source == cold[0].id
    assert restored.unintegrated_turn_count(cold) == 0
