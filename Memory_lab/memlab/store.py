"""Single-writer SQLite state. Chat may append traces and Cold only."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .types import Turn

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cold_turns(turn_id TEXT PRIMARY KEY, session_id TEXT, role TEXT,
 time TEXT, text TEXT, seq INTEGER, integrated_by TEXT);
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, text TEXT NOT NULL, salience REAL,
 created_at TEXT, created_by_dream TEXT, embedding BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS memory_versions(memory_id TEXT, version INTEGER, text TEXT,
 at TEXT, dream_id TEXT, op_index INTEGER, kind TEXT);
CREATE TABLE IF NOT EXISTS memory_merges(child_id TEXT, parent_id TEXT, dream_id TEXT,
 PRIMARY KEY(child_id,parent_id));
CREATE TABLE IF NOT EXISTS memory_sources(memory_id TEXT, turn_id TEXT, dream_id TEXT,
 PRIMARY KEY(memory_id,turn_id));
CREATE TABLE IF NOT EXISTS occurrences(memory_id TEXT, at TEXT, dream_id TEXT,
 PRIMARY KEY(memory_id,at));
CREATE TABLE IF NOT EXISTS strength_events(memory_id TEXT, at TEXT, weight REAL, kind TEXT, ref TEXT,
 UNIQUE(memory_id,at,kind,ref));
CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, name TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS entity_aliases(entity_id TEXT, alias TEXT,
 PRIMARY KEY(entity_id,alias));
CREATE TABLE IF NOT EXISTS memory_entities(memory_id TEXT, entity_id TEXT,
 PRIMARY KEY(memory_id,entity_id));
CREATE TABLE IF NOT EXISTS event_edges(a TEXT, b TEXT, component TEXT, weight REAL,
 directed INTEGER, last_reinforced TEXT, note TEXT,
 PRIMARY KEY(a,b,component));
CREATE TABLE IF NOT EXISTS dream_runs(id TEXT PRIMARY KEY, at TEXT, first_turn TEXT,
 last_turn TEXT, status TEXT, cache_key TEXT, prompt_version TEXT, model TEXT,
 n_ops INTEGER, n_rejected INTEGER, usage TEXT, error TEXT, response TEXT);
CREATE TABLE IF NOT EXISTS dream_ops(dream_id TEXT, op_index INTEGER, op_json TEXT,
 status TEXT, reason TEXT, result_id TEXT, PRIMARY KEY(dream_id,op_index));
CREATE TABLE IF NOT EXISTS recall_traces(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT,
 near_json TEXT, remote_json TEXT, core_json TEXT, consumed_by TEXT);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO meta VALUES('version','0')")
        self.conn.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")
        self.conn.commit()
        self._snapshot = None

    def close(self):
        self.conn.close()

    @property
    def version(self) -> int:
        return int(self.conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])

    @contextmanager
    def dream_transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='version'")
            self.conn.commit()
            self._snapshot = None
        except BaseException:
            self.conn.rollback()
            raise

    def add_cold(self, turns: list[Turn]):
        start = self.conn.execute("SELECT COALESCE(MAX(seq),0) FROM cold_turns").fetchone()[0]
        self.conn.executemany("INSERT OR IGNORE INTO cold_turns VALUES(?,?,?,?,?,?,NULL)",
                              [(t.id,t.session_id,t.role,t.time.isoformat(),t.text,start+i+1)
                               for i,t in enumerate(turns)])
        self.conn.commit()
        self._snapshot = None  # Cold raw-window visibility changes without a graph-version bump.

    def pending(self, cap: int | None = None) -> list[Turn]:
        sql = "SELECT * FROM cold_turns WHERE integrated_by IS NULL ORDER BY seq"
        rows = self.conn.execute(sql + (" LIMIT ?" if cap else ""), (cap,) if cap else ()).fetchall()
        return [Turn(r['turn_id'],r['session_id'],r['role'],datetime.fromisoformat(r['time']),r['text']) for r in rows]

    def pending_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM cold_turns WHERE integrated_by IS NULL").fetchone()[0]

    def all_cold(self) -> tuple[Turn, ...]:
        rows = self.conn.execute("SELECT * FROM cold_turns ORDER BY seq").fetchall()
        return tuple(Turn(r['turn_id'],r['session_id'],r['role'],datetime.fromisoformat(r['time']),r['text']) for r in rows)

    def add_trace(self, at: datetime, near: list[str], remote: list[str], core: list[str]):
        self.conn.execute("INSERT INTO recall_traces(at,near_json,remote_json,core_json,consumed_by) VALUES(?,?,?,?,NULL)",
                          (at.isoformat(),json.dumps(near),json.dumps(remote),json.dumps(core)))
        self.conn.commit()

    def snapshot(self, embedder=None):
        from .snapshot import build_snapshot
        if self._snapshot is None or self._snapshot.version != self.version:
            self._snapshot = build_snapshot(self, embedder)
        return self._snapshot

    def set_embedder_identity(self, identity: dict):
        value = json.dumps(identity,sort_keys=True,ensure_ascii=False)
        current = self.conn.execute("SELECT value FROM meta WHERE key='embedder_identity'").fetchone()
        if current and current[0] != value:
            raise ValueError("embedder identity differs from stored graph")
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES('embedder_identity',?)",(value,))
        self.conn.commit()


def next_id(conn, table: str, prefix: str) -> str:
    if table not in {"memories", "entities", "dream_runs"}:
        raise ValueError("invalid id table")
    value = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] + 1
    return f"{prefix}{value}"
