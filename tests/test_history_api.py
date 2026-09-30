"""History remains a projection of original Hot and Cold turns."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from core.contracts import DraftTurn
from core.draft_store import HotDraftSummary
from core.main import create_app
from core.model_client import MockModelClient


def _app(tmp_path: Path, **kwargs):
    return create_app(draft_store_path=tmp_path/'hot.jsonl',
        cold_draft_path=tmp_path/'cold.jsonl', memory_dir=tmp_path/'memory',
        compaction_state_path=tmp_path/'state.json', model_client=MockModelClient(),
        env_file_path=None, recall_enabled=False, **kwargs)


def _turn(index):
    return DraftTurn(role='user' if index%2==0 else 'assistant',
        text=f'body-{index}', turn_id=f'turn-{index:03d}',
        created_at=datetime(2026,8,1,tzinfo=UTC)+timedelta(minutes=index),
        source_timezone='Asia/Shanghai', timezone_source='client')


def test_empty_history(tmp_path):
    assert TestClient(_app(tmp_path)).get('/api/history').json() == {
        'turns':[], 'has_more':False, 'next_before':None}


def test_summary_and_internal_fields_never_enter_history(tmp_path):
    app = _app(tmp_path)
    app.state.hot_draft_store.replace_contents_atomically(
        HotDraftSummary('private summary',1,2,'2026-08-01T01:00:00Z'),
        [_turn(0),_turn(1)])
    response = TestClient(app).get('/api/history')
    assert response.status_code == 200
    assert [t['content'] for t in response.json()['turns']] == ['body-0','body-1']
    for private in ('private summary','source_timezone','record_type'):
        assert private not in response.text


def test_cold_hot_overlap_and_pagination(tmp_path):
    app = _app(tmp_path)
    turns = [_turn(i) for i in range(6)]
    app.state.cold_draft_store.append_segment([t.storage_turn() for t in turns[:4]])
    for turn in turns[2:]:
        app.state.hot_draft_store.append_turn(turn)
    client = TestClient(app)
    newest = client.get('/api/history?limit=2').json()
    assert [t['turn_id'] for t in newest['turns']] == ['turn-004','turn-005']
    older = client.get('/api/history',params={'limit':4,'before':newest['next_before']}).json()
    assert [t['turn_id'] for t in older['turns']] == ['turn-000','turn-001','turn-002','turn-003']
    assert not older['has_more']
    assert client.get('/api/history?before=missing').status_code == 400


def test_chat_history_still_works_after_compaction(tmp_path):
    class Model:
        client_kind='model'
        def complete_answer(self, system, messages): return '{"回复":"回答"}'
        def summarize_hot_draft(self, old, moved): return '对话摘要'
    app = create_app(draft_store_path=tmp_path/'hot.jsonl',
        cold_draft_path=tmp_path/'cold.jsonl', memory_dir=tmp_path/'memory',
        compaction_state_path=tmp_path/'state.json', model_client=Model(),
        env_file_path=None, recall_enabled=False,
        retain_recent_raw_turns=2, max_raw_turns_before_compression=4)
    client=TestClient(app)
    for index in range(3):
        assert client.post('/api/chat',json={'message':f'问题{index}'}).status_code == 200
    history=client.get('/api/history').json()['turns']
    assert len(history)==6
    assert [turn['content'] for turn in history][::2]==['问题0','问题1','问题2']
