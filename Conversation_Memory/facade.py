"""The small read, trace, Dream, and inspection boundary for memory v1.

Cold remains the source of conversation text.  The engine's Cold table is a
rebuildable index populated only while a Dream window is processed.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .engine.clock import SimClock
from .engine.config import Config, preset
from .engine.embed import BGE_M3_REVISION, BGE_M3_WEIGHTS_SHA256
from .engine.integrate import consume_traces, run_dream
from .engine.pattern_v2 import run_pattern
from .engine.recall import recall
from .engine.render import render_memory_block, memory_time_label
from .engine.store import Store
from .engine.snapshot import build_snapshot
from .engine.strength import strengths
from .engine.types import Cue, RecallResult, Turn
from .engine.usage_quote import parse_integrated_used_v4


@dataclass(frozen=True)
class MemoryRead:
    block: str
    context_ids: tuple[str, ...]
    result: RecallResult


@dataclass(frozen=True)
class DreamOutcome:
    status: str
    window_turns: int = 0
    dream_id: str | None = None
    patterns: int = 0
    error: str | None = None


class MemoryV1:
    """One logical memory with a separate SQLite connection for each thread."""

    def __init__(self, directory: str | Path, *, embedder_factory: Callable,
                 model_factory: Callable | None = None, config: Config | None = None,
                 commit_lock=None):
        self.directory = Path(directory)
        self._embedder_factory = embedder_factory
        self._model_factory = model_factory
        self.config = config or preset('P8', 'bge-m3')
        self._local = threading.local()
        self._open_lock = threading.Lock()
        self._dream_lock = threading.Lock()
        self._commit_lock = commit_lock

    def _store(self) -> Store:
        store = getattr(self._local, 'store', None)
        if store is None:
            with self._open_lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                store = Store(self.directory / 'memory.sqlite', commit_lock=self._commit_lock)
            self._local.store = store
        return store

    def _embedder(self):
        embedder = getattr(self._local, 'embedder', None)
        if embedder is None:
            embedder = self._embedder_factory()
            if (embedder.identity.get('model') != 'BAAI/bge-m3'
                    or embedder.identity.get('revision') != BGE_M3_REVISION
                    or embedder.identity.get('weights_sha256') != BGE_M3_WEIGHTS_SHA256):
                raise ValueError('memory_v1_requires_bge_m3')
            self._store().set_embedder_identity(embedder.identity)
            self._local.embedder = embedder
        return embedder

    def recall_and_render(self, message: str, hot: tuple[Turn, ...] | list[Turn],
                          now: datetime) -> MemoryRead:
        """Pure graph read; callers may fail soft around this method."""
        store = self._store()
        embedder = self._embedder()
        result = recall(store.snapshot(embedder),
                        Cue(message, tuple(hot[-self.config.cue_recent_turns:]),
                            frozenset(turn.id for turn in hot)), now, self.config)
        ids = tuple(dict.fromkeys(m.id for m in (*result.near, *result.remote, *result.core)))
        return MemoryRead(render_memory_block(result, now, self.config), ids, result)

    def record_trace(self, assistant_turn_id: str, at: datetime,
                     read: MemoryRead, noticed: dict[str, str] | None = None,
                     reply_text: str = '') -> None:
        """Chat's only memory write, after its assistant turn has been stored."""
        result = read.result
        self._store().add_trace(at, [m.id for m in result.near],
                                [m.id for m in result.remote],
                                [m.id for m in result.core],
                                assistant_turn_id=assistant_turn_id, noticed=noticed,
                                reply_text=reply_text)

    def has_cold_cursor(self) -> bool:
        return self._store().conn.execute("SELECT 1 FROM meta WHERE key='cold_cursor'").fetchone() is not None

    def set_cursor_to_start(self) -> None:
        self._set_cursor('__START__')

    def set_cursor_to_end(self, cold: tuple[Turn, ...] | list[Turn]) -> None:
        self._set_cursor(cold[-1].id if cold else '__START__')

    def _set_cursor(self, turn_id: str) -> None:
        store = self._store()
        store.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('cold_cursor',?)", (turn_id,))
        store.conn.commit()

    def _next_window(self, cold: tuple[Turn, ...] | list[Turn]) -> list[Turn]:
        row = self._store().conn.execute("SELECT value FROM meta WHERE key='cold_cursor'").fetchone()
        if row is None:
            return []
        if row[0] == '__START__':
            start = 0
        else:
            start = next((i + 1 for i, turn in enumerate(cold) if turn.id == row[0]), -1)
            if start < 0:
                raise ValueError('cold_cursor_not_in_source')
        window = list(cold[start:start + self.config.dream_window_max_turns])
        if len(window) % 2:
            window.pop()
        return window

    def unintegrated_turn_count(self, cold: tuple[Turn, ...] | list[Turn]) -> int:
        row = self._store().conn.execute("SELECT value FROM meta WHERE key='cold_cursor'").fetchone()
        if row is None:
            return len(cold)
        if row[0] == '__START__':
            return len(cold)
        pos = next((i for i, turn in enumerate(cold) if turn.id == row[0]), -1)
        if pos < 0:
            raise ValueError('cold_cursor_not_in_source')
        return len(cold) - pos - 1

    def auto_paused(self) -> bool:
        row = self._store().conn.execute("SELECT value FROM meta WHERE key='auto_dream_paused'").fetchone()
        return bool(row and row[0] == '1')

    def dream_once(self, cold: tuple[Turn, ...] | list[Turn], *,
                   manual: bool = False, now: datetime | None = None) -> DreamOutcome:
        """Run at most one complete Cold window with a separate Dream lock."""
        if not self._dream_lock.acquire(blocking=False):
            return DreamOutcome('busy')
        try:
            if self.auto_paused() and not manual:
                return DreamOutcome('paused')
            window = self._next_window(cold)
            if not window:
                return DreamOutcome('no_window')
            store = self._store()
            embedder = self._embedder()
            if self._model_factory is None:
                return DreamOutcome('unavailable')
            llm = self._model_factory()
            store.add_cold(window)
            at = now or window[-1].time
            clock = SimClock(at)
            # Only traces committed before this window starts are eligible.
            assistant_ids={turn.id for turn in window if turn.role=='assistant'}
            selected_rows=[row for row in store.conn.execute(
                'SELECT * FROM recall_traces WHERE consumed_by IS NULL').fetchall()
                if row['assistant_turn_id'] in assistant_ids]
            selected={row['id'] for row in selected_rows}
            memory_text={m['id']:m['text'] for m in store.snapshot(embedder).memories}
            names=tuple(e['name'] for e in store.snapshot(embedder).entities)
            usage_rows=[]
            for row in selected_rows:
                index=next(i for i,t in enumerate(window) if t.id==row['assistant_turn_id'])
                ids=list(dict.fromkeys(json.loads(row['near_json'])+json.loads(row['remote_json'])+
                                            json.loads(row['core_json'])))
                usage_rows.append({'turn_id':row['assistant_turn_id'],
                                   'reply_turn_id':row['assistant_turn_id'],
                                   'context_ids':ids,'memories':[{'id':mid,'text':memory_text[mid]}
                                                                   for mid in ids if mid in memory_text],
                                   'answer':row['reply_text'],
                                   'previous_messages':[t.text for t in window[:index]],
                                   'known_entity_names':names,'trace_id':row['id']})
            done = [store.conn.execute('SELECT integrated_by FROM cold_turns WHERE turn_id=?',
                                       (turn.id,)).fetchone()[0] for turn in window]
            if all(done) and len(set(done)) == 1:
                dream_id = done[0]
            else:
                result = run_dream(store, window, llm, embedder, clock, self.config,
                                   attempt=self._failure_attempt(window),
                                   recalled_turns=usage_rows,
                                   prompt_dir=Path(__file__).parent/'prompts')
                if result.status != 'applied':
                    self._register_failure(window)
                    return DreamOutcome('failed', len(window), result.dream_id, error='integrate_parse_failed')
                dream_id = result.dream_id
            marker = 'pattern_done:' + window[-1].id
            already = store.conn.execute('SELECT 1 FROM meta WHERE key=?', (marker,)).fetchone()
            pattern = ({'status': 'already_applied', 'results': []} if already else
                       run_pattern(store, dream_id, llm, embedder, at, self.config,
                                   prompt_dir=Path(__file__).parent/'prompts'))
            if pattern['status'].startswith('parse_failed'):
                self._register_failure(window)
                return DreamOutcome('failed', len(window), dream_id, error='pattern_parse_failed')
            store.conn.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)', (marker, '1'))
            if usage_rows:
                response=store.conn.execute('SELECT response FROM dream_runs WHERE id=?',
                                            (dream_id,)).fetchone()[0]
                judged,invalid,accepted=parse_integrated_used_v4(response,usage_rows,
                                                self.config.usage_short_match)
                for row in usage_rows:
                    store.conn.execute('UPDATE recall_traces SET used_json=? WHERE id=?',
                                       (json.dumps(judged[row['reply_turn_id']]),row['trace_id']))
            consume_traces(store, self.config, at, dream_id, selected)
            store.conn.commit()
            self._set_cursor(window[-1].id)
            self._clear_failure()
            return DreamOutcome('applied', len(window), dream_id,
                                len(pattern.get('results', [])))
        except Exception:
            if 'window' in locals() and window:
                self._register_failure(window)
            return DreamOutcome('failed', len(window) if 'window' in locals() else 0,
                                error='dream_failed')
        finally:
            self._dream_lock.release()

    def _register_failure(self, window: list[Turn]) -> None:
        store = self._store()
        key = window[0].id + ':' + window[-1].id
        row = store.conn.execute("SELECT value FROM meta WHERE key='auto_dream_failures'").fetchone()
        old = json.loads(row[0]) if row else {}
        count = int(old.get('count', 0)) + 1 if old.get('window') == key else 1
        store.conn.execute("INSERT OR REPLACE INTO meta VALUES('auto_dream_failures',?)",
                           (json.dumps({'window': key, 'count': count}),))
        if count >= 3:
            store.conn.execute("INSERT OR REPLACE INTO meta VALUES('auto_dream_paused','1')")
        store.conn.commit()

    def _failure_attempt(self, window: list[Turn]) -> int:
        row = self._store().conn.execute(
            "SELECT value FROM meta WHERE key='auto_dream_failures'").fetchone()
        if not row:
            return 0
        saved = json.loads(row[0])
        return int(saved.get('count', 0)) if saved.get('window') == window[0].id + ':' + window[-1].id else 0

    def _clear_failure(self) -> None:
        store = self._store()
        store.conn.execute("DELETE FROM meta WHERE key IN ('auto_dream_failures','auto_dream_paused')")
        store.conn.commit()

    def inspect(self, now: datetime, *, limit: int = 100) -> dict:
        """Bounded, read-only presentation; no paths, errors, or raw Cold text."""
        store = self._store()
        snapshot = build_snapshot(store, None)
        state = strengths(snapshot.memories, now, self.config)
        rows = []
        for memory, (_, pi, _) in zip(snapshot.memories[:limit], state[:limit]):
            rows.append({'id': memory['id'], 'text': memory['text'],
                         'time_label': memory_time_label(memory, now),
                         'pi': float(pi), 'pattern': any(v['kind'] == 'pattern' for v in memory['lineage']),
                         'source_count': len(memory['sources'])})
        logs = [dict(row) for row in store.conn.execute(
            'SELECT id,at,status,n_ops,n_rejected FROM dream_runs ORDER BY rowid DESC LIMIT 10')]
        patterns = [{'id': memory['id'], 'text': memory['text'],
                     'time_label': memory_time_label(memory, now), 'pi': float(pi),
                     'pattern': True, 'source_count': len(memory['sources'])}
                    for memory, (_, pi, _) in zip(snapshot.memories, state)
                    if any(v['kind'] == 'pattern' for v in memory['lineage'])]
        return {'memory_count': len(snapshot.memories), 'pattern_count': sum(
                    any(v['kind'] == 'pattern' for v in m['lineage']) for m in snapshot.memories),
                'memories': rows, 'patterns': patterns, 'dream_log': logs}
