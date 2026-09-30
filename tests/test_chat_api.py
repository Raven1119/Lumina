"""Public Chat contracts after the memory-v1 cutover."""
from fastapi.testclient import TestClient

from core.main import create_app
from core.model_client import MockModelClient


def _client(tmp_path, model=None):
    return TestClient(create_app(draft_store_path=tmp_path/'hot.jsonl',
        cold_draft_path=tmp_path/'cold.jsonl', memory_dir=tmp_path/'memory',
        env_file_path=None, model_client=model or MockModelClient(),
        enable_compaction=False, recall_enabled=False))


def test_chat_rejects_empty_message_without_writing(tmp_path):
    client=_client(tmp_path)
    assert client.post('/api/chat',json={'message':'  '}).status_code == 400
    assert client.get('/api/history').json()['turns']==[]


def test_chat_mock_reply_and_history(tmp_path):
    client=_client(tmp_path)
    result=client.post('/api/chat',json={'message':'你好'})
    assert result.status_code==200
    assert result.json()['phase']=='mock_chat'
    assert [t['role'] for t in client.get('/api/history').json()['turns']]==['user','assistant']


def test_provider_failure_falls_back_without_private_details(tmp_path):
    class Failing:
        client_kind='model'
        def complete_answer(self, *_): raise RuntimeError('private provider token')
    client=_client(tmp_path, Failing())
    result=client.post('/api/chat',json={'message':'你好'})
    assert result.status_code==200
    assert result.json()['response']['type']=='fallback'
    assert 'private' not in result.text
