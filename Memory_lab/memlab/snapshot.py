"""Committed, read-only graph view. A prior view survives later Dreams."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .types import Turn


class FrozenDict(dict):
    """JSON-compatible mapping that callers cannot edit in place."""
    def _deny(self,*args,**kwargs):
        raise TypeError('snapshot is immutable')
    __setitem__=_deny
    __delitem__=_deny
    clear=_deny
    pop=_deny
    popitem=_deny
    setdefault=_deny
    update=_deny


@dataclass(frozen=True)
class Snapshot:
    version:int
    memories:tuple[dict,...]
    entities:tuple[dict,...]
    event_edges:tuple[dict,...]
    merges:frozenset[tuple[str,str]]
    cold:tuple[Turn,...]
    embedder:object


def build_snapshot(store,embedder)->Snapshot:
    conn=store.conn
    memories=[]
    for row in conn.execute('SELECT * FROM memories ORDER BY CAST(SUBSTR(id,2) AS INTEGER)'):
        mid=row['id']
        vector=np.frombuffer(row['embedding'],dtype=np.float32).copy()
        vector.flags.writeable=False
        occurrences=tuple(datetime.fromisoformat(r[0]) for r in conn.execute('SELECT at FROM occurrences WHERE memory_id=? ORDER BY at',(mid,)))
        event_rows=tuple((datetime.fromisoformat(r['at']),r['weight'],r['kind'],r['ref']) for r in conn.execute('SELECT * FROM strength_events WHERE memory_id=? ORDER BY at,kind,ref',(mid,)))
        memories.append(FrozenDict({'id':mid,'text':row['text'],'salience':row['salience'],
                         'created_at':datetime.fromisoformat(row['created_at']),
                         'embedding':vector,'occurrences':occurrences,
                         'events':tuple((at,w) for at,w,_,_ in event_rows),
                         'event_rows':event_rows,
                         'sources':tuple(r[0] for r in conn.execute('SELECT turn_id FROM memory_sources WHERE memory_id=? ORDER BY turn_id',(mid,))),
                         'entities':tuple(r[0] for r in conn.execute('SELECT entity_id FROM memory_entities WHERE memory_id=? ORDER BY entity_id',(mid,))),
                         'lineage':tuple(FrozenDict(dict(r)) for r in conn.execute('SELECT * FROM memory_versions WHERE memory_id=? ORDER BY version',(mid,)))}))
    entities=[]
    for row in conn.execute('SELECT * FROM entities ORDER BY CAST(SUBSTR(id,2) AS INTEGER)'):
        entities.append(FrozenDict({'id':row['id'],'name':row['name'],
                         'aliases':tuple(r[0] for r in conn.execute('SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias',(row['id'],)))}))
    edges=tuple(FrozenDict(dict(r)) for r in conn.execute('SELECT * FROM event_edges ORDER BY a,b,component'))
    merges=frozenset((r[0],r[1]) for r in conn.execute('SELECT child_id,parent_id FROM memory_merges'))
    return Snapshot(store.version,tuple(memories),tuple(entities),edges,merges,store.all_cold(),embedder)
