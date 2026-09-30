"""Probe-time evidence for the six remote-association targets."""
from __future__ import annotations

from Conversation_Memory.engine.recall import event_components
from Conversation_Memory.engine.usage import used_memories

REMOTE_IDS={'A11','A12','A13','B11','B12','B13'}


def remote_diagnostic(probe,snapshot,result,now,cfg,answer_text='',context_texts=()):
    if probe['id'] not in REMOTE_IDS:return None
    source_ids=set().union(*(set(g) for g in probe.get('must_surface',[])))
    memories=list(snapshot.memories);ids={m['id']:i for i,m in enumerate(memories)}
    targets=[m for m in memories if set(m['sources'])&source_ids]
    related=[s['id'] for s in result.diagnostics.get('seeds',{}).get('semantic',[])[:5]
             if s['id'] in ids]
    selected={m.id:part for part,items in [('near',result.near),('remote',result.remote),('core',result.core)] for m in items}
    ranks={row['id']:i+1 for i,row in enumerate(result.diagnostics.get('top_scores',[]))}
    entity_names={e['id']:e['name'] for e in snapshot.entities}
    findings=[]
    for m in targets:
        i=ids[m['id']]
        names=[entity_names[e] for e in m['entities'] if e in entity_names]
        used=used_memories(answer_text,context_texts,[{'id':m['id'],'text':m['text'],'entity_names':names}],cfg.usage_min_overlap)
        findings.append({'id':m['id'],'text':m['text'],'sources':list(m['sources']),
                         'rank':ranks.get(m['id'],'>20'),'section':selected.get(m['id'],'absent'),
                         'raw_sources':[hit.turn_id for hit in result.raw if hit.turn_id in m['sources']],
                         'used_in_answer':m['id'] in used if answer_text else None,
                         'cue_related':[{'id':mid,'text':memories[ids[mid]]['text'],
                                         'event_components':event_components(snapshot,now,cfg,ids[mid],i)}
                                        for mid in related if mid!=m['id']]})
    return {'probe_id':probe['id'],'target_memories':findings,'missing_write':not bool(findings)}
