"""Shared persistence helpers for the maintained P8 and P9 patterns."""
from __future__ import annotations

import json


WRITE_KINDS = frozenset(('new', 'revise', 'merge', 'touch', 'pattern'))


def changed_ids(store, dream_id: str) -> set[str]:
    return {row['result_id'] for row in store.conn.execute(
        "SELECT result_id,op_json FROM dream_ops WHERE dream_id=? AND status='applied' AND result_id IS NOT NULL",
        (dream_id,)) if json.loads(row['op_json']).get('op') in ('new', 'revise', 'merge', 'touch')}


def pattern_ids(snapshot) -> set[str]:
    return {m['id'] for m in snapshot.memories
            if any(version['kind'] == 'pattern' for version in m['lineage'])}


def _source_union(conn, ids):
    sources=set(); occurrences=set(); events=[]
    for mid in ids:
        sources.update(row[0] for row in conn.execute(
            'SELECT turn_id FROM memory_sources WHERE memory_id=?',(mid,)))
        occurrences.update(row[0] for row in conn.execute(
            'SELECT at FROM occurrences WHERE memory_id=?',(mid,)))
        events.extend((mid,row['at'],row['weight'],row['kind']) for row in conn.execute(
            'SELECT at,weight,kind FROM strength_events WHERE memory_id=?',(mid,))
            if row['kind'] in WRITE_KINDS)
    return sorted(sources),sorted(occurrences),events
