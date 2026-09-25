"""Normalized local embeddings with a shared SQLite text cache."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Protocol

import numpy as np

MINILM_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MINILM_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"


class Embedder(Protocol):
    identity: dict
    def encode(self, texts: list[str]) -> np.ndarray: ...


def normalize(vectors: np.ndarray) -> np.ndarray:
    v = np.asarray(vectors,dtype=np.float32)
    if v.ndim == 1: v = v[None,:]
    norm = np.linalg.norm(v,axis=1,keepdims=True)
    return v / np.maximum(norm,1e-12)


def lexical_tokens(text: str) -> list[str]:
    cjk = re.findall(r"[\u3400-\u9fff]",text)
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]*",text.lower())
    return [a+b for a,b in zip(cjk,cjk[1:])] + words


def lexical_features(text: str) -> frozenset[str]:
    return frozenset(lexical_tokens(text))


class HashEmbedder:
    identity = {"model":"hash-bigram-v1","revision":"2","dimension":256,"weights_sha256":"none"}

    def encode(self,texts:list[str])->np.ndarray:
        out=np.zeros((len(texts),256),dtype=np.float32)
        for row,text in enumerate(texts):
            for token in lexical_tokens(text):
                digest=hashlib.sha256(token.encode('utf-8')).digest()
                out[row,int.from_bytes(digest[:4],'big')%256]+=1
        return normalize(out)


def _sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


class SentenceEmbedder:
    def __init__(self,model_id:str,revision:str|None,snapshot:Path):
        from sentence_transformers import SentenceTransformer  # optional, never imported by offline tests
        self.model=SentenceTransformer(str(snapshot),local_files_only=True)
        weights=next((p for p in (snapshot/'model.safetensors',snapshot/'pytorch_model.bin') if p.is_file()),None)
        if weights is None:
            weights=next(iter(snapshot.rglob('*.safetensors')),None)
        if weights is None: raise ValueError('model weights missing')
        self.identity={"model":model_id,"revision":revision or snapshot.name,
                       "dimension":int(self.model.get_sentence_embedding_dimension()),
                       "weights_sha256":_sha(weights)}

    def encode(self,texts:list[str])->np.ndarray:
        if not texts: return np.zeros((0,self.identity['dimension']),dtype=np.float32)
        return normalize(np.asarray(self.model.encode(texts,batch_size=32,
                                      convert_to_numpy=True,show_progress_bar=False),dtype=np.float32))


class CachedEmbedder:
    def __init__(self,base:Embedder,root:Path):
        self.base=base
        self.identity=base.identity
        short=hashlib.sha256(json.dumps(self.identity,sort_keys=True).encode()).hexdigest()[:16]
        root.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(root/f'{short}.sqlite')
        self.conn.execute('CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY,dim INTEGER,data BLOB)')
        self.conn.commit()

    def encode(self,texts:list[str])->np.ndarray:
        if not texts:return np.zeros((0,self.identity['dimension']),dtype=np.float32)
        keys=[hashlib.sha256(t.encode('utf-8')).hexdigest() for t in texts]
        found={}
        for key in set(keys):
            row=self.conn.execute('SELECT dim,data FROM vectors WHERE key=?',(key,)).fetchone()
            if row: found[key]=np.frombuffer(row[1],dtype=np.float32).copy()
        missing={key:text for key,text in zip(keys,texts) if key not in found}
        if missing:
            batch=self.base.encode(list(missing.values()))
            for (key,_),vector in zip(missing.items(),batch):
                found[key]=vector
                self.conn.execute('INSERT OR IGNORE INTO vectors VALUES(?,?,?)',(key,len(vector),vector.tobytes()))
            self.conn.commit()
        return np.stack([found[key] for key in keys])


def resolve_embedder(choice:str='auto',allow_download:bool=False,cache_root:Path|None=None):
    """Resolve only the permitted bge-m3 then the pinned multilingual MiniLM."""
    if choice=='hash':return HashEmbedder()
    os.environ.setdefault('HF_HOME',str(Path(__file__).resolve().parents[1]/'cache'/'hf'))
    from huggingface_hub import snapshot_download  # optional real-run import
    cache_root=cache_root or Path(__file__).resolve().parents[1]/'cache'/'embed'
    attempts=[]
    if choice in ('auto','bge-m3'):
        override=os.environ.get('LUMINA_MEMLAB_EMBED_MODEL')
        if override and Path(override).is_dir():attempts.append(('BAAI/bge-m3',None,Path(override)))
        try:
            local=Path(snapshot_download('BAAI/bge-m3',local_files_only=True))
            attempts.append(('BAAI/bge-m3',local.name,local))
        except Exception:pass
        if not attempts and allow_download:
            try:
                local=Path(snapshot_download('BAAI/bge-m3'))
                attempts.append(('BAAI/bge-m3',local.name,local))
            except Exception:
                if choice=='bge-m3':raise
    if choice in ('auto','minilm') and not attempts:
        override=os.environ.get('LUMINA_CALIBRATED_MODEL_SNAPSHOT')
        if override and Path(override).is_dir() and Path(override).name==MINILM_REVISION:
            attempts.append((MINILM_ID,MINILM_REVISION,Path(override)))
        else:
            try:
                local=Path(snapshot_download(MINILM_ID,revision=MINILM_REVISION,local_files_only=True))
                attempts.append((MINILM_ID,MINILM_REVISION,local))
            except Exception:pass
    if not attempts:raise RuntimeError('no permitted real embedding model available')
    model,revision,path=attempts[0]
    return CachedEmbedder(SentenceEmbedder(model,revision,path),cache_root)
