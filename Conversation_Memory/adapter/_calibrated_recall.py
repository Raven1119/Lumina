"""Opt-in amplitude-preserving FirstHit read with one abstaining selection rule.

All numeric support is request-local; neither search rank nor graph activation is
represented as probability that the recalled claim is true.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from functools import lru_cache
from hashlib import sha256
import json
from math import exp, isfinite
from pathlib import Path

from Conversation_Memory.recall.rendering import render_reliable_fact

from ._first_hit_read import discover_first_hit_read
from ._reliable_recall import _budget_reason, _measure, _source_views_v2, _views_v2
from .models import AssociativeMemoryContext, MemoryContext, MemoryEvidence, SourceMemoryContext, SourceProvenance

PARAMETERS = Path(__file__).with_name('calibrated_first_hit_parameters.json')
CORPUS = Path(__file__).resolve().parents[1] / 'tests/fixtures/calibrated_associative_synthetic.json'


@lru_cache(maxsize=1)
def parameters():
    value=json.loads(PARAMETERS.read_text(encoding='utf-8'))
    if (value.get('schema')!='calibrated-first-hit-v1'
            or value.get('feature_version')!='cosine-graph-mu-lexical-v1'
            or sha256(CORPUS.read_bytes()).hexdigest()!=value.get('synthetic_corpus_sha256')
            or len(value.get('coefficients',()))!=5
            or any(not isfinite(float(v)) for v in value['coefficients'])
            or not 0<=value.get('threshold',-1)<=1):
        raise ValueError('calibrated_parameters_invalid')
    return value


def amplitude_weights(scores):
    """Return b with sum(b)=max(r), including an all-zero vector."""
    values=tuple(float(s) for s in scores)
    if any(not isfinite(s) or s<0 or s>1 for s in values):
        raise ValueError('calibrated_support_invalid')
    mass=sum(values)
    peak=max(values,default=0.)
    return tuple(peak*s/mass for s in values) if mass else tuple(0. for _ in values)


def selection_score(cosine, graph_support, entry_amplitude, lexical_support):
    coeff=parameters()['coefficients']
    values=(max(0.,min(1.,cosine)), max(0.,graph_support),entry_amplitude,lexical_support)
    z=coeff[0]+sum(a*b for a,b in zip(coeff[1:],values))
    return 1/(1+exp(-z))


def _empty(query, code=None):
    return AssociativeMemoryContext(MemoryContext(query,safe_error_code=code),
                                    SourceMemoryContext(query),safe_error_code=code)


def recall_calibrated(adapter, query, policy, *, include_sources=False, source_context_turns=0,
                      seed_only=False):
    """One real facade read; `seed_only` is an explicit evaluation control."""
    from .magma_adapter import _relation_compatible, _RELATION_RESOLVER
    if not isinstance(query,str) or not query.strip():return _empty('', 'invalid_query')
    if getattr(adapter,'_calibrated_index_error',None):return _empty(query,adapter._calibrated_index_error)
    diagnostics={'profile':'calibrated-first-hit-v1','candidate_outcomes':{},
                 'source_outcomes':{},'bge_pairs':0,'provider_requests':0,
                 'seed_only':seed_only,'cold_read_attempts':0}
    adapter._last_calibrated_read_diagnostics=diagnostics
    try:
        config=parameters()
        index=adapter.backend.calibrated_read_index()
        if (index.identity['model']!=config['model'] or index.identity['revision']!=config['revision']
                or index.identity['weights_sha256']!=config['model_weights_sha256']
                or index.identity['text_view']!=config['text_view']):
            raise ValueError('calibrated_index_fingerprint_mismatch')
        backend=adapter.backend
        view=backend.first_hit_view()
        version=view.version
        refs=tuple(backend.resolve_target_entity_refs(query,limit=adapter.first_hit.max_seeds))
        hits,search_diagnostics,qvector=index.search(query,target_entity_refs=refs)
        diagnostics['search']=search_diagnostics
        diagnostics['entry_hits']=[asdict(hit) for hit in hits]
        # Rank is for discovery only; absolute same-space support decides seeds.
        ranked=sorted((hit for hit in hits if view.eligible(hit.node_id)),
                      key=lambda hit:(-max(0.,hit.cosine),view.stable_id(hit.node_id),hit.node_id))
        ranked=ranked[:adapter.first_hit.max_seeds]
        strengths=[max(0.,min(1.,hit.cosine)) for hit in ranked]
        weights=amplitude_weights(strengths)
        seeds=tuple((hit.node_id,weight) for hit,weight in zip(ranked,weights) if weight>0)
        b_by_id=dict(seeds)
        mu=max(strengths,default=0.)
        diagnostics['mu']=mu
        diagnostics['seeds']=[{'node_id':hit.node_id,'r':strength,'b':weight,
                               'cosine':hit.cosine,'lexical_support':hit.lexical_support,
                               'name_support':hit.name_support,'channels':hit.channels}
                              for hit,strength,weight in zip(ranked,strengths,weights)]
        result=discover_first_hit_read(view,seeds,
                    replace(adapter.first_hit,max_edges=0) if seed_only else adapter.first_hit)
        adapter._last_first_hit_snapshot=result
        diagnostics['first_hit']=dict(result.stats)
        nodes=result.fact_ids
        support=index.score_nodes(qvector,query,nodes)
        view.check(version)
    except Exception as error:
        # An unhealthy search/graph is distinct from a normal abstention. No
        # old rank-normalized reader is substituted into this opt-in path.
        code=str(error) if str(error).startswith('calibrated_') else 'calibrated_read_unavailable'
        diagnostics['failure']=code
        return _empty(query,code)
    relation_ids=_RELATION_RESOLVER.resolve_query_relations(policy.relation_surfaces or ())
    rows=[]
    seed_ids=set(b_by_id)
    for node_id in nodes:
        candidate=backend.first_hit_candidate(node_id)
        if candidate is None or not _relation_compatible(candidate,relation_ids):continue
        eid=candidate.metadata.get('evidence_id')
        try:
            if not isinstance(eid,str) or not eid or not candidate.text.strip():raise ValueError()
            provenance=SourceProvenance(**candidate.metadata['provenance'])
            if not all(isinstance(v,str) and v.strip() for v in asdict(provenance).values()):raise ValueError()
            direct=node_id in seed_ids
            cosine,lexical=support[node_id]
            b=b_by_id.get(node_id,0.)
            h=result.h[node_id]
            g=max(0.,h-b)
            u=selection_score(cosine,g,mu,lexical)
            item=MemoryEvidence(eid,candidate.text,candidate.timestamp,provenance)
            rows.append((u,item,'direct' if direct else 'associated',candidate))
            diagnostics['candidate_outcomes'][eid]={'node_id':node_id,'r':max(0.,min(1.,cosine)),
                'g':g,'mu':mu,'b':b,'h':h,'lexical_support':lexical,'u':u,
                'channel':'direct' if direct else 'associated',
                'reason':'below_threshold' if u<=config['threshold'] else 'eligible'}
        except (KeyError,TypeError,ValueError,AttributeError):
            diagnostics['candidate_outcomes'][str(eid)]={'reason':'invalid_fact'}
    rows.sort(key=lambda row:(-row[0],row[1].evidence_id))
    selected=[]; by_id={}
    max_items=min(3,policy.max_evidence_items)
    max_chars=min(5000,policy.max_chars)
    max_bytes=min(20000,policy.max_bytes) if policy.max_bytes is not None else 20000
    for u,item,channel,candidate in rows:
        if u<=config['threshold']:continue
        if item.evidence_id in by_id:continue
        proposed=[*selected,(item,channel)]
        rendered='\n'.join(render_reliable_fact(value,f'M{i+1}',include_source_context=policy.include_source_context)
                            for i,(value,_) in enumerate(proposed))
        reason=_budget_reason(rendered,len(proposed),count=max_items,chars=max_chars,bytes_limit=max_bytes)
        diagnostics['candidate_outcomes'][item.evidence_id]['reason']=reason or 'selected'
        if reason:continue
        selected=proposed;by_id[item.evidence_id]=candidate
    bounded_policy=replace(policy,max_evidence_items=max_items,max_chars=max_chars,max_bytes=max_bytes)
    source_views,source_items,source_error,source_truncated={},(),None,False
    if include_sources and selected:
        source_views,source_items,source_error,source_truncated=_source_views_v2(
            adapter,query,bounded_policy,selected,by_id,source_context_turns,diagnostics)
    rendered,fact_text,source_text,visible_sources,selections,source_count=_views_v2(
        selected,source_views,source_items,bounded_policy)
    diagnostics['visible_items']=len(selected)+source_count
    diagnostics['visible_chars'],diagnostics['visible_bytes']=_measure(rendered)
    diagnostics['threshold']=config['threshold']
    diagnostics['selected_direct']=sum(channel=='direct' for _,channel in selected)
    diagnostics['selected_associated']=sum(channel=='associated' for _,channel in selected)
    truncated=bool(result.stats.get('budget_exhausted')) or any(
        row['reason'] in {'item_budget','char_budget','byte_budget'}
        for row in diagnostics['candidate_outcomes'].values())
    facts=MemoryContext(query,tuple(item for item,_ in selected),fact_text,truncated)
    sources=SourceMemoryContext(query,visible_sources,source_text,source_truncated,source_error)
    return AssociativeMemoryContext(facts,sources,rendered,truncated or source_truncated,
                                    source_error,selections)
