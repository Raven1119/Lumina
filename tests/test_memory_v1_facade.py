"""Offline contracts for the promoted memory v1 boundary."""

from datetime import datetime
from threading import Lock, Thread

from Conversation_Memory.engine.embed import (
    BGE_M3_REVISION, BGE_M3_WEIGHTS_SHA256, HashEmbedder,
)
from Conversation_Memory.engine.types import Turn
from Conversation_Memory.facade import MemoryV1


AT = datetime.fromisoformat('2026-03-01T08:00:00+08:00')


class LocalBGE(HashEmbedder):
    identity = {**HashEmbedder.identity, 'model': 'BAAI/bge-m3',
                'revision': BGE_M3_REVISION, 'weights_sha256': BGE_M3_WEIGHTS_SHA256}


class StaticModel:
    model = 'test-flash'

    def __init__(self, lock=None):
        self.calls = []
        self.lock = lock

    def complete(self, **kwargs):
        if self.lock is not None:
            assert not self.lock.locked()
        self.calls.append(kwargs)
        class Reply:
            text = '{"ops":[]}'
            cache_key = 'offline'
            cache_hit = False
            usage = {'input_tokens': 0, 'output_tokens': 0}
        return Reply()


def _turns():
    return [Turn('t01', 's1', 'user', AT, '今天想起了很久以前的实验'),
            Turn('t02', 's1', 'assistant', AT, '我记得这件事')]


def test_facade_is_lazy_read_only_then_idempotent_trace(tmp_path):
    directory = tmp_path / 'memory'
    memory = MemoryV1(directory, embedder_factory=LocalBGE)
    assert not directory.exists()
    store = memory._store()
    vector = LocalBGE().encode(['他以前做过这项实验'])[0].tobytes()
    store.conn.execute('INSERT INTO memories VALUES(?,?,?,?,?,?)',
                       ('m1', '他以前做过这项实验', 1, AT.isoformat(), 'd1', vector))
    store.conn.execute('INSERT INTO occurrences VALUES(?,?,?)', ('m1', AT.isoformat(), 'd1'))
    store.conn.execute('INSERT INTO strength_events VALUES(?,?,?,?,?)',
                       ('m1', AT.isoformat(), 1, 'new', 'd1'))
    store.conn.commit()
    read = memory.recall_and_render('实验', (), AT)
    assert read.context_ids and '他以前做过这项实验' in read.block
    assert store.conn.execute('SELECT COUNT(*) FROM recall_traces').fetchone()[0] == 0
    memory.record_trace('assistant-turn-1', AT, read, {'理解': '想起实验'})
    memory.record_trace('assistant-turn-1', AT, read, {'理解': '重复'})
    rows = store.conn.execute('SELECT assistant_turn_id,noticed_json FROM recall_traces').fetchall()
    assert len(rows) == 1 and rows[0]['assistant_turn_id'] == 'assistant-turn-1'
    assert '想起实验' in rows[0]['noticed_json']


def test_one_window_cursor_and_model_call_outside_chat_lock(tmp_path):
    writer_lock = Lock()
    model = StaticModel(writer_lock)
    memory = MemoryV1(tmp_path / 'memory', embedder_factory=LocalBGE,
                      model_factory=lambda: model, commit_lock=writer_lock)
    cold = _turns()
    assert not memory.has_cold_cursor()
    memory.set_cursor_to_start()
    assert memory.unintegrated_turn_count(cold) == 2
    result = memory.dream_once(cold, now=AT)
    assert result.status == 'applied' and result.window_turns == 2
    assert len(model.calls) == 1 and memory.unintegrated_turn_count(cold) == 0
    assert memory.dream_once(cold).status == 'no_window'


def test_each_thread_owns_its_sqlite_connection(tmp_path):
    memory = MemoryV1(tmp_path / 'memory', embedder_factory=LocalBGE)
    first = memory._store().conn
    other = []
    def read_in_worker():
        conn = memory._store().conn
        other.append((conn, conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0]))
    worker = Thread(target=read_in_worker)
    worker.start()
    worker.join()
    assert other and other[0][0] is not first
    assert other[0][1] == '0'


def test_busy_and_three_failures_pause_auto_but_allow_manual(tmp_path):
    class InvalidModel(StaticModel):
        def complete(self, **kwargs):
            result = super().complete(**kwargs)
            result.text = 'not json'
            return result
    memory = MemoryV1(tmp_path/'memory', embedder_factory=LocalBGE,
                      model_factory=InvalidModel)
    cold = _turns()
    memory.set_cursor_to_start()
    memory._dream_lock.acquire()
    try:
        assert memory.dream_once(cold).status == 'busy'
    finally:
        memory._dream_lock.release()
    for _ in range(3):
        assert memory.dream_once(cold).status == 'failed'
    assert memory.auto_paused()
    assert memory.dream_once(cold).status == 'paused'
    assert memory.dream_once(cold, manual=True).status == 'failed'
    assert memory.unintegrated_turn_count(cold) == 2


def test_hex_turn_aliases_restore_original_sources(tmp_path):
    class AliasModel(StaticModel):
        def complete(self, **kwargs):
            if kwargs['purpose'] == 'dream':
                assert '[t01]' in kwargs['messages'][0]['content']
                assert '0123456789abcdef0123456789abcdef' not in kwargs['messages'][0]['content']
                reply = super().complete(**kwargs)
                reply.text = '{"ops":[{"op":"new","text":"他说要保留原始证据",' \
                             '"salience":2,"sources":["t01"],"entities":[]}]}'
                return reply
            return super().complete(**kwargs)
    memory = MemoryV1(tmp_path/'memory', embedder_factory=LocalBGE,
                      model_factory=AliasModel)
    cold = [Turn('0123456789abcdef0123456789abcdef','s','user',AT,'他说要保留原始证据'),
            Turn('fedcba9876543210fedcba9876543210','s','assistant',AT,'我会保留')]
    memory.set_cursor_to_start()
    assert memory.dream_once(cold).status == 'applied'
    row = memory._store().conn.execute('SELECT turn_id FROM memory_sources').fetchone()
    assert row[0] == cold[0].id


def test_trace_snapshot_leaves_new_trace_for_next_window(tmp_path):
    memory = MemoryV1(tmp_path/'memory', embedder_factory=LocalBGE)
    class AppendingModel(StaticModel):
        def complete(self, **kwargs):
            memory._store().add_trace(AT, [], [], [], assistant_turn_id='later')
            return super().complete(**kwargs)
    model = AppendingModel()
    memory._model_factory = lambda: model
    memory.set_cursor_to_start()
    memory._store().add_trace(AT, [], [], [], assistant_turn_id='t02')
    assert memory.dream_once(_turns()).status == 'applied'
    rows = memory._store().conn.execute(
        'SELECT assistant_turn_id,consumed_by FROM recall_traces ORDER BY id').fetchall()
    assert rows[0]['consumed_by'] is not None
    assert rows[1]['assistant_turn_id'] == 'later' and rows[1]['consumed_by'] is None
