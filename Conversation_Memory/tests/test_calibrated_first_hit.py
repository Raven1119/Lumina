"""Opt-in calibrated reader contracts with isolated graph and no model/provider."""
from types import SimpleNamespace

import numpy as np
import pytest

from Conversation_Memory.adapter._calibrated_index import (
    CalibratedReadIndex, SearchHit, lexical_features, retrieval_text,
)
from Conversation_Memory.adapter._calibrated_recall import amplitude_weights, parameters, recall_calibrated
from Conversation_Memory.adapter.first_hit import FirstHitPolicy, solve_first_hit
from Conversation_Memory.adapter.models import BackendCandidate, RecallPolicy


class View:
    version = 0
    def __init__(self, edges): self.edges = edges
    def check(self, version=None):
        if version is not None and version != self.version: raise ValueError('stale')
    def stable_id(self, key): return key
    def eligible(self, key): return key in self.edges
    def is_fact(self, key): return key in self.edges
    def outgoing_weight(self, key): return sum(weight for _,weight in self.edges[key])
    def cursor(self, key, version):
        for target,weight in self.edges[key]: yield (-weight,target,target,'SEMANTIC')


class Index:
    def __init__(self, scores, *, stale=False):
        p=parameters()
        self.identity={'model':p['model'],'revision':p['revision'],
                       'weights_sha256':p['model_weights_sha256'],'text_view':p['text_view']}
        self.scores=scores;self.stale=stale;self.calls=0
    def search(self, query, *, target_entity_refs=()):
        self.calls+=1
        if self.stale: raise ValueError('calibrated_index_stale')
        hits=tuple(SearchHit(key,self.scores[key][0],'IP_cosine',self.scores[key][0],
                             self.scores[key][1],0,('dense',),1/(60+i))
                   for i,key in enumerate(self.scores,1))
        return hits,{'metric':'IP_cosine'},np.array([1.],dtype=np.float32)
    def score_nodes(self, query_vector, query, node_ids):
        return {key:self.scores.get(key,(0.,0.)) for key in node_ids}


class Backend:
    def __init__(self, scores, edges, *, stale=False, versions=None):
        self.index=Index(scores,stale=stale)
        self.view=View(edges)
        self.versions=versions or {}
        self.builds=0
    def calibrated_read_index(self): return self.index
    def first_hit_view(self): return self.view
    def resolve_target_entity_refs(self, query, *, limit): return ()
    def first_hit_candidate(self, key):
        version=self.versions.get(key,'grounded-formation-v6')
        text=f'User stated: {key} only, with no inferred permission.'
        provenance=dict(segment_id='synthetic',conversation_id='synthetic',turn_id=key,
            source_role='user',source_timestamp='2026-01-01T00:00:00+00:00',
            source_timezone='UTC',ingestion_version=version,timezone_source='client')
        return BackendCandidate(text,'2026-01-01T00:00:00+00:00',None,
                                {'evidence_id':key,'provenance':provenance})


def adapter(scores, edges, *, cold=None, stale=False, versions=None):
    backend=Backend(scores,edges,stale=stale,versions=versions)
    return SimpleNamespace(backend=backend,first_hit=FirstHitPolicy(),cold_store=cold,
                           _calibrated_index_error=None)


def test_weak_seed_multiplicity_preserves_absolute_mass_in_actual_solver():
    for size in (1,2,5):
        b=np.array(amplitude_weights([.05]*size))
        assert b.sum()==pytest.approx(.05)
        h,_=solve_first_hit(np.zeros((size,size)),b,np.zeros(size))
        assert h.sum()==pytest.approx(.05)
    assert amplitude_weights([0,0])==(0.,0.)
    assert sum(amplitude_weights([.2,.4]))==pytest.approx(.4)
    P=np.array([[0.,.6],[.2,0.]])
    weak=np.array(amplitude_weights([.1,.4]))
    strong=np.array(amplitude_weights([.2,.8]))
    h_weak,_=solve_first_hit(P,weak,np.array([.6,.2]))
    h_strong,_=solve_first_hit(P,strong,np.array([.6,.2]))
    assert h_strong==pytest.approx(2*h_weak)
    assert solve_first_hit(P,np.zeros(2),np.array([.6,.2]))[0]==pytest.approx([0,0])
    with pytest.raises(ValueError): amplitude_weights([1.1])


def test_weak_direct_abstains_normally_and_stale_index_never_repairs():
    weak=adapter({'a':(.2,0.)},{'a':[]})
    result=recall_calibrated(weak,'What happened before?',RecallPolicy())
    assert result.rendered_text=='' and result.safe_error_code is None
    assert weak._last_calibrated_read_diagnostics['candidate_outcomes']['a']['reason']=='below_threshold'
    broken=adapter({'a':(1.,0.)},{'a':[]},stale=True)
    result=recall_calibrated(broken,'What happened before?',RecallPolicy())
    assert result.rendered_text=='' and result.safe_error_code=='calibrated_index_stale'
    assert broken.backend.builds==0


def test_weak_bridge_transmits_but_direct_and_associated_share_admission():
    memory=adapter({'a':(1.,0.)},{'a':[('b',1.)],'b':[]})
    result=recall_calibrated(memory,'Past event?',RecallPolicy(max_evidence_items=3))
    assert tuple(item.evidence_id for item in result.facts.evidence)==('a',)
    assert 'a only' in result.rendered_text and 'b only' not in result.rendered_text
    snapshot=memory._last_first_hit_snapshot
    assert snapshot.h['b']>0 and snapshot.b[1]==0
    assert memory._last_calibrated_read_diagnostics['candidate_outcomes']['b']['reason']=='below_threshold'


def test_complete_body_survives_failed_optional_source_and_mixed_versions():
    class UnavailableCold:
        def read_source_refs(self,*args,**kwargs): raise RuntimeError('offline')
    memory=adapter({'a':(1.,0.),'b':(1.,0.)},{'a':[],'b':[]},cold=UnavailableCold(),
                   versions={'a':'grounded-formation-v5','b':'grounded-formation-v6'})
    result=recall_calibrated(memory,'Past event?',RecallPolicy(max_evidence_items=2),
                             include_sources=True)
    assert len(result.facts.evidence)==2
    assert all(item.text in result.rendered_text for item in result.facts.evidence)
    assert {item.provenance.ingestion_version for item in result.facts.evidence}=={
        'grounded-formation-v5','grounded-formation-v6'}
    assert result.safe_error_code=='cold_source_unavailable'
    assert result.sources.evidence==()


def test_model_fingerprint_failure_is_visible():
    memory=adapter({'a':(1.,0.)},{'a':[]})
    memory.backend.index.identity['weights_sha256']='wrong'
    result=recall_calibrated(memory,'Past event?',RecallPolicy())
    assert result.rendered_text==''
    assert result.safe_error_code=='calibrated_index_fingerprint_mismatch'


def test_cjk_posting_can_also_score_and_prefix_view_keeps_negation():
    shared=lexical_features('河水采样') & lexical_features('采样河水')
    assert 'cjk:采样' in shared
    index=CalibratedReadIndex.__new__(CalibratedReadIndex)
    index.node_ids=('fact',)
    index.features=(lexical_features('采样河水'),)
    index.postings={feature:frozenset({0}) for feature in index.features[0]}
    assert index._lexical(lexical_features('河水采样'),0)>0
    assert retrieval_text('User stated: 没有授权打开盒子。')=='没有授权打开盒子。'
    assert retrieval_text('User stated: User stated: 没有授权')=='User stated: 没有授权'


def test_owner_rebuild_covers_incremental_facts_and_restart_without_read_repair(monkeypatch):
    import sentence_transformers
    from Conversation_Memory.adapter import _calibrated_index as module
    from Conversation_Memory.adapter.backend import RealMagmaBackend
    class Encoder:
        max_seq_length=128
        tokenizer=SimpleNamespace(encode=lambda text,**kw:list(range(len(text)+2)))
        def __init__(self,*args,**kwargs): pass
        def encode(self,texts,**kwargs):
            result=[]
            for text in texts:
                vector=np.zeros(384,dtype=np.float32)
                vector[0 if 'needle' in text else 1]=1
                result.append(vector)
            return np.stack(result)
    monkeypatch.setattr(sentence_transformers,'SentenceTransformer',Encoder)
    monkeypatch.setattr(module,'_model_location',lambda:'synthetic')
    monkeypatch.setattr(module,'_model_identity',lambda *a:{'dimension':384,'max_seq_length':128})
    class Graph:
        def __init__(self,nodes):self.nodes=nodes
        def get_node(self,node_id):return self.nodes[node_id]
    class Owner:
        rebuild_calibrated_read_index=RealMagmaBackend.rebuild_calibrated_read_index
        calibrated_read_index=RealMagmaBackend.calibrated_read_index
        def __init__(self,nodes):
            self.trg=SimpleNamespace(graph_db=Graph(nodes),vector_db=SimpleNamespace(index_to_id={}))
            self._calibrated_read_index=None
            self.view=View({key:[] for key in nodes})
        def first_hit_view(self):return self.view
        def _entity_membership_signature(self):return (len(self.trg.graph_db.nodes),self.view.version)
        def _entity_membership_for_recall(self):return {}
    nodes={'a':SimpleNamespace(content_narrative='User stated: needle in box')}
    owner=Owner(nodes)
    owner.rebuild_calibrated_read_index()
    first=owner.calibrated_read_index()
    assert first.search('needle')[0][0].node_id=='a'
    nodes['b']=SimpleNamespace(content_narrative='User stated: other item')
    owner.view=View({'a':[],'b':[]})
    owner.view.version=1
    with pytest.raises(ValueError,match='calibrated_index_stale'):
        owner.calibrated_read_index()
    assert owner._calibrated_read_index is first
    owner.rebuild_calibrated_read_index()
    assert set(owner.calibrated_read_index().node_ids)=={'a','b'}
    restarted=Owner(dict(nodes))
    restarted.rebuild_calibrated_read_index()
    assert restarted.calibrated_read_index().coverage_digest==owner.calibrated_read_index().coverage_digest
