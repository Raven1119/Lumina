"""Exact entity alignment; the conversation participants are not entities."""
from __future__ import annotations

import unicodedata

from .store import next_id

SELF_NAMES={"我","你","他","她","用户","林素","lumina"}


def norm(value:str)->str:
    return unicodedata.normalize('NFKC',value).strip().lower()


def is_self(name:str)->bool:
    return norm(name) in SELF_NAMES


def align(conn,items:list,at:str)->tuple[list[str],list[str],list[str]]:
    ids=[]; created=[]; warnings=[]
    known=conn.execute('SELECT id,name FROM entities ORDER BY CAST(SUBSTR(id,2) AS INTEGER)').fetchall()
    by_name={norm(r['name']):r['id'] for r in known}
    for row in conn.execute('SELECT entity_id,alias FROM entity_aliases'):
        by_name[norm(row['alias'])]=row['entity_id']
    for item in items if isinstance(items,list) else []:
        if not isinstance(item,dict):
            warnings.append('invalid_entity');continue
        name=str(item.get('name','')).strip()
        raw_aliases=item.get('aliases',[])
        aliases=[str(a).strip() for a in raw_aliases if isinstance(a,str) and a.strip()] if isinstance(raw_aliases,list) else []
        if any(is_self(x) for x in [name,*aliases] if x):
            warnings.append('self_entity');continue
        requested=item.get('id')
        row=conn.execute('SELECT id FROM entities WHERE id=?',(requested,)).fetchone() if isinstance(requested,str) else None
        eid=row['id'] if row else next((by_name[norm(x)] for x in [name,*aliases] if norm(x) in by_name),None)
        if eid is None:
            if not name:
                warnings.append('empty_entity');continue
            eid=next_id(conn,'entities','e')
            conn.execute('INSERT INTO entities VALUES(?,?,?)',(eid,name,at))
            by_name[norm(name)]=eid
            created.append(eid)
        for alias in [name,*aliases]:
            if alias and alias != conn.execute('SELECT name FROM entities WHERE id=?',(eid,)).fetchone()[0]:
                conn.execute('INSERT OR IGNORE INTO entity_aliases VALUES(?,?)',(eid,alias))
                by_name[norm(alias)]=eid
        if eid not in ids:ids.append(eid)
    return ids,created,warnings


def matches(text:str,entities:list[dict])->list[str]:
    """Longest exact substring wins at each offset."""
    hay=norm(text)
    candidates=[]
    for ent in entities:
        for label in [ent['name'],*ent.get('aliases',[])]:
            value=norm(label)
            if not value or is_self(value):continue
            if all(ord(c)<128 for c in value):
                if len(value)<3:continue
            elif len(value)<2:continue
            start=hay.find(value)
            while start>=0:
                candidates.append((start,-len(value),ent['id']))
                start=hay.find(value,start+1)
    occupied=set(); result=[]
    for pos,neglen,eid in sorted(candidates):
        span=set(range(pos,pos-neglen))
        if occupied.intersection(span):continue
        occupied.update(span)
        if eid not in result:result.append(eid)
    return result
