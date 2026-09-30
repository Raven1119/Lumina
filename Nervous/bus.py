"""WAL event delivery and durable dialogue receipts. No imports from old Nervous."""
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from .event_triggers import route


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def seal(value):
    raw = encode(value)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def unpack(raw, digest):
    if hashlib.sha256(raw.encode()).hexdigest() != digest:
        raise ValueError('nervous_integrity_failure')
    return json.loads(raw)


class EventBus:
    def __init__(self, path):
        self.path = Path(path)
        self._local = threading.local()
        self.changed = threading.Condition()
        self._init_lock = threading.Lock()

    @property
    def conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            with self._init_lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self.path, timeout=30)
                conn.row_factory = sqlite3.Row
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA synchronous=FULL')
                conn.executescript('''
                    CREATE TABLE IF NOT EXISTS events(
                      seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                      kind TEXT NOT NULL, target TEXT NOT NULL, priority INTEGER NOT NULL,
                      body TEXT NOT NULL, digest TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE IF NOT EXISTS journal(
                      id TEXT PRIMARY KEY, body TEXT NOT NULL, digest TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS state(
                      id TEXT PRIMARY KEY, body TEXT NOT NULL, digest TEXT NOT NULL);
                ''')
                self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, 'conn', None)
        if conn:
            conn.close()
            del self._local.conn

    def publish(self, event_id, kind, body):
        target, priority = route(kind)
        raw, digest = seal(body)
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO events(id,kind,target,priority,body,digest) VALUES(?,?,?,?,?,?)',
                              (event_id,kind,target,priority,raw,digest))
            row = self.conn.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
            if (row['kind'],row['body'],row['digest']) != (kind,raw,digest):
                raise ValueError('event_id_conflict')
        self.wake()
        return event_id

    def pending(self, target):
        rows = self.conn.execute('SELECT * FROM events WHERE target=? AND acknowledged=0 ORDER BY priority,seq',(target,)).fetchall()
        return [{**dict(row),'body':unpack(row['body'],row['digest'])} for row in rows]

    def ack(self, event_id):
        with self.conn:
            self.conn.execute('UPDATE events SET acknowledged=1 WHERE id=?',(event_id,))
        self.wake()

    def ack_many(self, event_ids):
        with self.conn:
            self.conn.executemany('UPDATE events SET acknowledged=1 WHERE id=?', [(eid,) for eid in event_ids])
        self.wake()

    def get(self, key, *, state=False):
        table = 'state' if state else 'journal'
        row = self.conn.execute(f'SELECT body,digest FROM {table} WHERE id=?',(key,)).fetchone()
        return unpack(*row) if row else None

    def put(self, key, value, *, state=False):
        table = 'state' if state else 'journal'
        raw, digest = seal(value)
        with self.conn:
            if state:
                self.conn.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(key,raw,digest))
            else:
                self.conn.execute('INSERT OR IGNORE INTO journal VALUES(?,?,?)',(key,raw,digest))
                row = self.conn.execute('SELECT body,digest FROM journal WHERE id=?',(key,)).fetchone()
                if tuple(row)!=(raw,digest):
                    raise ValueError('journal_id_conflict')
        self.wake()
        return value

    def wait(self, key, timeout):
        until = time.monotonic()+timeout
        while True:
            result = self.get(key)
            if result is not None:
                return result
            left = until-time.monotonic()
            if left<=0:
                return None
            with self.changed:
                self.changed.wait(min(left,0.1))

    def wake(self):
        with self.changed:
            self.changed.notify_all()
