"""Owner-built, read-only multilingual search view for calibrated FirstHit.

The original EVENT vectors, writer encoder and graph links are never changed.
An index is published only after all eligible facts are encoded and mapped.
Read calls check its source signature and never repair/rebuild it implicitly.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from ._anchor_fusion import _query_features

MODEL_ID = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
MODEL_REVISION = 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
MODEL_SNAPSHOT_ENV = 'LUMINA_CALIBRATED_MODEL_SNAPSHOT'
TEXT_VIEW = 'canonical-prefix-only-v1'
_COMMON = {'cjk:用户', 'cjk:以前', 'cjk:之前', 'cjk:现在', 'cjk:我们',
           'user', 'stated:', 'lumina', 'the', 'and', 'what', 'when', 'that'}


def retrieval_text(text: str) -> str:
    """Remove only an exact writer-added outer role prefix; keep all claims."""
    for prefix in ('User stated: ', 'Lumina stated: '):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def lexical_features(text: str) -> frozenset[str]:
    return frozenset(x for x in _query_features(text) if x not in _COMMON
                     and (not x.startswith('part:') or len(x) > 7))


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _model_location() -> str:
    override = os.environ.get(MODEL_SNAPSHOT_ENV)
    if override:
        path = Path(override)
        if not path.is_dir() or path.name != MODEL_REVISION:
            raise ValueError('calibrated_model_snapshot_revision_mismatch')
        return str(path)
    return MODEL_ID


def _model_identity(location: str, model: Any) -> dict:
    if location == MODEL_ID:
        from huggingface_hub import snapshot_download
        snapshot = Path(snapshot_download(MODEL_ID, revision=MODEL_REVISION,
                                          local_files_only=True))
    else:
        snapshot = Path(location)
    weights = snapshot / 'model.safetensors'
    if not weights.is_file():
        weights = snapshot / 'pytorch_model.bin'
    if not weights.is_file():
        raise ValueError('calibrated_model_weights_missing')
    return {'model': MODEL_ID, 'revision': MODEL_REVISION,
            'weights_sha256': _sha(weights),
            'config_sha256': _sha(snapshot/'config.json'),
            'modules_sha256': _sha(snapshot/'modules.json'),
            'max_seq_length': int(model.max_seq_length),
            'dimension': int(model.get_sentence_embedding_dimension()),
            'text_view': TEXT_VIEW}


@dataclass(frozen=True)
class SearchHit:
    node_id: str
    cosine: float
    raw_metric: str
    raw_score: float
    lexical_support: float
    name_support: float
    channels: tuple[str, ...]
    discovery_rrf: float


class CalibratedReadIndex:
    """An atomically replaceable derived search view owned by RealMagmaBackend."""

    def __init__(self, backend):
        import faiss
        from sentence_transformers import SentenceTransformer

        self.backend = backend
        location = _model_location()
        model = SentenceTransformer(location, revision=MODEL_REVISION if location == MODEL_ID else None,
                                    local_files_only=True)
        self.identity = _model_identity(location, model)
        view = backend.first_hit_view()
        self.source_version = view.version
        self.source_signature = backend._entity_membership_signature()
        if self.source_signature is None:
            raise ValueError('calibrated_source_signature_unavailable')
        self.node_ids = tuple(sorted((node_id for node_id in backend.trg.graph_db.nodes
                                      if view.is_fact(node_id)), key=lambda node_id:
                                      (view.stable_id(node_id), node_id)))
        self.id_to_position = {node_id: pos for pos,node_id in enumerate(self.node_ids)}
        graph = backend.trg.graph_db
        texts = [retrieval_text(graph.get_node(node_id).content_narrative) for node_id in self.node_ids]
        if not all(t.strip() for t in texts):
            raise ValueError('calibrated_empty_fact_text')
        lengths = [len(model.tokenizer.encode(text,add_special_tokens=True,truncation=False)) for text in texts]
        self.fact_token_max = max(lengths,default=0)
        self.facts_truncated = sum(length>self.identity['max_seq_length'] for length in lengths)
        raw = (np.asarray(model.encode(texts,batch_size=32,convert_to_numpy=True,
                                      show_progress_bar=False),dtype=np.float32)
               if texts else np.empty((0,self.identity['dimension']),dtype=np.float32))
        if raw.shape != (len(self.node_ids),self.identity['dimension']) or not np.isfinite(raw).all():
            raise ValueError('calibrated_fact_vector_invalid')
        norms = np.linalg.norm(raw,axis=1)
        if np.any(norms<=0):
            raise ValueError('calibrated_fact_vector_zero')
        self.fact_norm_min = float(norms.min()) if norms.size else 0.0
        self.fact_norm_max = float(norms.max()) if norms.size else 0.0
        self.vectors = np.ascontiguousarray(raw / norms[:,None],dtype=np.float32)
        self.index = faiss.IndexFlatIP(self.identity['dimension'])
        self.index.add(self.vectors)
        self.features = tuple(lexical_features(text) for text in texts)
        postings: dict[str,set[int]] = defaultdict(set)
        for pos,features in enumerate(self.features):
            for feature in features:postings[feature].add(pos)
        self.postings = {key:frozenset(value) for key,value in postings.items()}
        self.model = model
        self.coverage_digest = sha256(json.dumps(self.node_ids,separators=(',',':')).encode()).hexdigest()
        self._check()

    def _check(self):
        if (self.source_signature != self.backend._entity_membership_signature()
                or self.source_version != self.backend.first_hit_view().version
                or self.index.ntotal != len(self.node_ids)
                or len(self.id_to_position) != len(self.node_ids)):
            raise ValueError('calibrated_index_stale')

    def _lexical(self, query_features: frozenset[str], pos: int) -> float:
        shared=query_features.intersection(self.features[pos])
        if not shared:return 0.0
        n=len(self.node_ids)
        numerator=sum(math.log((n+1)/(len(self.postings[f])+1))+1 for f in shared)
        denominator=sum(math.log((n+1)/(len(self.postings.get(f,()))+1))+1 for f in query_features)
        return min(1.0,numerator/denominator) if denominator else 0.0

    def search(self, query: str, *, target_entity_refs=(), limit=20) -> tuple[tuple[SearchHit,...],dict,np.ndarray]:
        self._check()
        if not isinstance(query,str) or not query.strip() or type(limit) is not int or not 1<=limit<=20:
            raise ValueError('calibrated_query_invalid')
        qraw=np.asarray(self.model.encode([query],convert_to_numpy=True,
                                           show_progress_bar=False),dtype=np.float32).reshape(-1)
        qnorm=float(np.linalg.norm(qraw))
        if qraw.shape!=(self.identity['dimension'],) or not np.isfinite(qraw).all() or qnorm<=0:
            raise ValueError('calibrated_query_vector_invalid')
        q=np.ascontiguousarray((qraw/qnorm).reshape(1,-1),dtype=np.float32)
        count=min(limit,len(self.node_ids))
        distances,positions=self.index.search(q,count)
        dense=[int(pos) for pos in positions[0] if pos>=0]
        qfeatures=lexical_features(query)
        postings=[(len(self.postings[f]),f) for f in qfeatures if f in self.postings]
        postings.sort()
        lex_candidates=set()
        for _,feature in postings:
            lex_candidates.update(self.postings[feature])
            if len(lex_candidates)>=64:break
        lexical=sorted(lex_candidates,key=lambda pos:(-self._lexical(qfeatures,pos),self.node_ids[pos]))[:count]
        refs=tuple(dict.fromkeys(ref for ref in target_entity_refs if isinstance(ref,str) and ref!='E_001'))
        members=self.backend._entity_membership_for_recall() or {}
        source_db=self.backend.trg.vector_db
        entity_positions=set()
        for ref in refs:
            member=members.get('entity:'+ref.casefold())
            if member is None:continue
            for old_position in member.positions:
                node_id=source_db.index_to_id.get(int(old_position))
                if node_id in self.id_to_position:entity_positions.add(self.id_to_position[node_id])
        entity=sorted(entity_positions,key=lambda pos:(-float(q[0] @ self.vectors[pos]),self.node_ids[pos]))[:count]
        lists=(('dense',dense),('lexical',lexical),('entity',entity))
        ranked={}
        for channel,items in lists:
            for rank,pos in enumerate(items,1):
                state=ranked.setdefault(pos,[0.0,set()])
                state[0]+=1/(60+rank)
                state[1].add(channel)
        candidates=sorted(ranked,key=lambda pos:(-ranked[pos][0],self.node_ids[pos]))[:limit]
        similarities=(q @ self.vectors[candidates].T).reshape(-1) if candidates else ()
        hits=tuple(SearchHit(self.node_ids[pos],float(similarities[i]),'IP_cosine',float(similarities[i]),
                             self._lexical(qfeatures,pos),float(pos in entity_positions),
                             tuple(sorted(ranked[pos][1])),ranked[pos][0])
                   for i,pos in enumerate(candidates))
        tokens=len(self.model.tokenizer.encode(query,add_special_tokens=True,truncation=False))
        self._check()
        return hits,{'model_identity':self.identity,'coverage_digest':self.coverage_digest,
                     'facts_indexed':len(self.node_ids),'fact_token_max':self.fact_token_max,
                     'facts_truncated':self.facts_truncated,'query_vector_norm':qnorm,
                     'fact_vector_norm_min':self.fact_norm_min,'fact_vector_norm_max':self.fact_norm_max,
                     'query_tokens':tokens,'query_truncated':tokens>self.identity['max_seq_length'],
                     'dense_candidates':len(dense),'lexical_candidates':len(lexical),
                     'entity_candidates':len(entity),'union_candidates':len(hits),
                     'metric':'IP_cosine','query_text_view':'original',
                     'fact_text_view':TEXT_VIEW},q[0]

    def score_nodes(self, query_vector, query: str, node_ids) -> dict[str, tuple[float,float]]:
        """Score visited graph facts in the same query/model space; no encode or repair."""
        self._check()
        features=lexical_features(query)
        return {node_id:(float(query_vector @ self.vectors[self.id_to_position[node_id]]),
                         self._lexical(features,self.id_to_position[node_id]))
                for node_id in node_ids if node_id in self.id_to_position}
