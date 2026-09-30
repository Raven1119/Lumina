"""Dream-owned implicit utterance traces; never exposed by Snapshot."""
from __future__ import annotations

from datetime import datetime

import numpy as np

from .clock import logical_day
from .pattern_v2 import clauses


def write_window(conn, window, embedder):
    """Idempotently persist only the user's clauses after a successful Dream."""
    pending=[]
    for turn in window:
        if turn.role != 'user':
            continue
        for index,text in enumerate(clauses(turn.text)):
            if conn.execute('SELECT 1 FROM trace_clauses WHERE turn_id=? AND clause_index=?',
                            (turn.id,index)).fetchone():
                continue
            pending.append((turn,index,text))
    vectors=embedder.encode([text for _,_,text in pending]) if pending else ()
    for (turn,index,text),vector in zip(pending,vectors):
        conn.execute('INSERT OR IGNORE INTO trace_clauses VALUES(?,?,?,?,?,?,?)',
                     (turn.id,index,text,turn.text,turn.time.isoformat(),
                      logical_day(turn.time).isoformat(),np.asarray(vector,dtype=np.float32).tobytes()))
    return len(pending)


def read_rows(conn):
    rows=[]
    for row in conn.execute('SELECT * FROM trace_clauses ORDER BY at,turn_id,clause_index'):
        rows.append({'turn_id':row['turn_id'],'index':row['clause_index'],
                     'text':row['text'],'original':row['original'],
                     'at':datetime.fromisoformat(row['at']),
                     'day':__import__('datetime').date.fromisoformat(row['day']),
                     'embedding':np.frombuffer(row['embedding'],dtype=np.float32)})
    return rows
