"""V5: full locked base and gap-directed, graph-only semantic supplement."""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json

from Conversation_Memory.recall.rendering import render_reliable_fact

from ._calibrated_recall import amplitude_weights
from ._first_hit_read import discover_first_hit_read
from ._semantic_recall_v3 import prepare_semantic_recall_v3
from .models import MemoryContext, MemoryEvidence, PreparedRecall, SourceProvenance
from .semantic_protocol import GRAPH_INTENTS, usage_guidance

GRAPH_ITEMS = 24
GRAPH_CHARS = 7000
GRAPH_BYTES = 28000
FINAL_ITEMS = 3
FINAL_CHARS = 5000
FINAL_BYTES = 20000
GRAPH_SECTION = "[OPTIONAL GRAPH SUPPLEMENT]\n"


def _fingerprint(*parts) -> str:
    return sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False,
                             default=str, separators=(",", ":")).encode()).hexdigest()


def _index_signature(index) -> str:
    return _fingerprint(index.identity, tuple(index.node_ids))


@dataclass(frozen=True)
class LockedBaseSelection:
    query: str
    panel_fingerprint: str
    base_panel_ids: tuple[str, ...]
    selected_ids: tuple[str, ...]
    selected_ranked: tuple[tuple[str, str, str], ...]
    rendered_blocks: tuple[str, ...]
    selected_cards: tuple[str, ...]
    selected_evidence: tuple[MemoryEvidence, ...]
    remaining_slots: int
    seek_graph: bool
    graph_intent: str
    graph_need: str
    index_signature: str
    graph_version: object
    index_hit_ids: tuple[str, ...]
    seeds: tuple[tuple[str, float], ...]
    base_ranked_overflow: tuple[str, ...] = ()

    @property
    def context(self) -> MemoryContext:
        return MemoryContext(self.query, self.selected_evidence,
                             "\n".join(self.rendered_blocks))


def prepare_base(adapter, query, policy):
    base = prepare_semantic_recall_v3(adapter, query, policy, seed_only=True)
    diagnostics = adapter._last_semantic_read_diagnostics
    diagnostics["profile"] = "semantic-associative-v5"
    if base.context.safe_error_code:
        return base
    index = adapter.backend.calibrated_read_index()
    view = adapter.backend.first_hit_view()
    snapshot = (
        _index_signature(index), view.version,
        tuple(diagnostics["index_hit_ids"]),
        tuple((row["node_id"], row["weight"]) for row in diagnostics["seeds"]
              if row["weight"] > 0),
    )
    if view.version != diagnostics["first_hit"]["graph_version"]:
        return PreparedRecall(MemoryContext(query, safe_error_code="graph_supplement_snapshot_mismatch"))
    return replace(base, _semantic_v4_snapshot=snapshot)


def lock_base(adapter, base: PreparedRecall, suggestions, seek_graph: bool,
              graph_intent: str, graph_need: str) -> LockedBaseSelection:
    if (type(seek_graph) is not bool or graph_intent not in GRAPH_INTENTS
            or type(graph_need) is not str
            or (not seek_graph and (graph_intent != "none" or graph_need != ""))
            or (seek_graph and (graph_intent == "none" or not graph_need.strip()
                                or len(graph_need) > 240))):
        raise ValueError("invalid_graph_intent")
    if base.context.safe_error_code or not base.context.evidence:
        raise ValueError("base_panel_unavailable")
    if type(suggestions) is not tuple or len(suggestions) > 12:
        raise ValueError("invalid_semantic_selection")
    limited = replace(base, _semantic_final_limits=(FINAL_ITEMS, FINAL_CHARS, FINAL_BYTES))
    context, _ = limited.ranked_semantic_subset(suggestions)
    selected_ids = tuple(item.evidence_id for item in context.evidence)
    selected = set(selected_ids)
    ranked = tuple(row for row in suggestions if row[0] in selected)
    ranked_by_id = {eid: (use, relation) for eid, use, relation in suggestions}
    source_blocks = dict(zip((item.evidence_id for item in base.context.evidence),
                             base._rendered_blocks))
    blocks = tuple(usage_guidance(*ranked_by_id[eid]) + "\n" + source_blocks[eid]
                   for eid in selected_ids)
    if "\n".join(blocks) != context.rendered_text or len(blocks) != len(selected_ids):
        raise ValueError("invalid_base_rendering")
    lookup = dict(base.selection_items)
    if base._semantic_v4_snapshot is None:
        raise ValueError("graph_supplement_snapshot_mismatch")
    index_signature, graph_version, index_hit_ids, seeds = base._semantic_v4_snapshot
    return LockedBaseSelection(
        query=base.context.query,
        panel_fingerprint=_fingerprint(base.selection_items),
        base_panel_ids=tuple(item.evidence_id for item in base.context.evidence),
        selected_ids=selected_ids, selected_ranked=ranked,
        rendered_blocks=blocks,
        selected_cards=tuple(lookup[eid] for eid in selected_ids),
        selected_evidence=context.evidence,
        remaining_slots=FINAL_ITEMS-len(selected_ids),
        seek_graph=seek_graph, graph_intent=graph_intent, graph_need=graph_need,
        index_signature=index_signature, graph_version=graph_version,
        index_hit_ids=index_hit_ids, seeds=seeds,
        base_ranked_overflow=tuple(row[0] for row in suggestions if row[0] not in selected),
    )


def prepare_graph_supplement(adapter, lock: LockedBaseSelection, policy):
    """Traverse only after a positive base judgment; return a graph-only panel."""
    from .magma_adapter import _relation_compatible, _RELATION_RESOLVER

    if not lock.seek_graph or lock.remaining_slots < 1:
        raise ValueError("graph_supplement_not_requested")
    diagnostics = {"profile": "semantic-associative-v5", "graph_exclusive_pool_ids": (),
                   "graph_panel_ids": (), "graph_omitted_by_item_budget": (),
                   "graph_omitted_by_char_budget": (), "graph_omitted_by_byte_budget": ()}
    adapter._last_semantic_graph_diagnostics = diagnostics
    try:
        view = adapter.backend.first_hit_view()
        index = adapter.backend.calibrated_read_index()
        if view.version != lock.graph_version or _index_signature(index) != lock.index_signature:
            raise ValueError("graph_supplement_snapshot_mismatch")
        bounds = replace(adapter.first_hit,
                         max_seeds=min(adapter.first_hit.max_seeds, 5),
                         max_nodes=min(adapter.first_hit.max_nodes, 64),
                         max_edges=min(adapter.first_hit.max_edges, 256))
        refs = tuple(adapter.backend.resolve_target_entity_refs(lock.query, limit=bounds.max_seeds))
        hits, search, qvector = index.search(lock.query, target_entity_refs=refs, limit=20)
        ranked = sorted((hit for hit in hits if view.eligible(hit.node_id)),
                        key=lambda hit: (-max(0.0, hit.cosine), view.stable_id(hit.node_id), hit.node_id))[:bounds.max_seeds]
        weights = amplitude_weights([max(0.0, min(1.0, hit.cosine)) for hit in ranked])
        seeds = tuple((hit.node_id, weight) for hit, weight in zip(ranked, weights) if weight > 0)
        if (tuple(hit.node_id for hit in hits) != lock.index_hit_ids or
                seeds != lock.seeds):
            raise ValueError("graph_supplement_snapshot_mismatch")
        result = discover_first_hit_read(view, seeds, bounds)
        adapter._last_first_hit_snapshot = result
        diagnostics.update(graph_query_search=search, graph_first_hit=dict(result.stats),
                           graph_read_arcs=result.read_arcs)
        index_ids = set(lock.index_hit_ids)
        index_evidence_ids = set()
        for node_id in index_ids:
            hit_candidate = adapter.backend.first_hit_candidate(node_id)
            if hit_candidate is not None:
                hit_eid = hit_candidate.metadata.get("evidence_id")
                if isinstance(hit_eid, str):
                    index_evidence_ids.add(hit_eid)
        reached = {arc[1] for arc in result.read_arcs}
        graph_ids = tuple(node_id for node_id in result.fact_ids
                          if node_id not in index_ids and node_id in reached)
        support = index.score_nodes(qvector, lock.query, graph_ids)
        relation_ids = _RELATION_RESOLVER.resolve_query_relations(policy.relation_surfaces or ())
        pool = {}
        for node_id in graph_ids:
            candidate = adapter.backend.first_hit_candidate(node_id)
            if candidate is None or not _relation_compatible(candidate, relation_ids):
                continue
            eid = candidate.metadata.get("evidence_id")
            if (not isinstance(eid, str) or not eid or eid in lock.base_panel_ids
                    or eid in index_evidence_ids
                    or not isinstance(candidate.text, str) or not candidate.text.strip()):
                continue
            provenance = SourceProvenance(**candidate.metadata["provenance"])
            if not all(isinstance(value, str) and value.strip()
                       for value in vars(provenance).values()):
                continue
            item = MemoryEvidence(eid, candidate.text, candidate.timestamp, provenance)
            cosine, lexical = support.get(node_id, (0.0, 0.0))
            pool.setdefault(eid, {"item": item,
                                  "group": provenance.segment_id or provenance.conversation_id,
                                  "h": result.h[node_id], "cosine": cosine,
                                  "lexical": lexical, "node_id": node_id})
        diagnostics["graph_exclusive_pool_ids"] = tuple(sorted(pool))
        # Encode the retrieval gap once, after FirstHit has bounded the pool.
        # This vector never discovers new nodes or enters Memory evidence.
        need_vector, need_cache_hit = index.encode_query(lock.graph_need)
        need_scores = index.cosine_nodes(need_vector,
                                         (row["node_id"] for row in pool.values()))
        for row in pool.values():
            row["need_cosine"] = need_scores.get(row["node_id"], 0.0)
        diagnostics["graph_need_encoding_cache_hit"] = need_cache_hit
        diagnostics["candidate_scores"] = {eid: {key: row[key] for key in
            ("group", "h", "cosine", "lexical", "need_cosine", "node_id")}
            for eid, row in pool.items()}
        groups = sorted({row["group"] for row in pool.values()})
        def first_key(row):
            return (-row["need_cosine"], -row["h"], -row["cosine"],
                    -row["lexical"], row["item"].evidence_id)
        def fill_key(row):
            return (-row["need_cosine"], -row["h"],
                    -max(row["cosine"], row["lexical"]), row["item"].evidence_id)
        representatives = [min((row for row in pool.values() if row["group"] == group),
                               key=first_key) for group in groups]
        ordered = sorted(representatives, key=first_key)
        represented = {row["item"].evidence_id for row in ordered}
        ordered.extend(sorted((row for row in pool.values()
                               if row["item"].evidence_id not in represented), key=fill_key))
        labels = {group: f"G{pos}" for pos, group in enumerate(groups, 1)}
        items, blocks, cards = [], [], []
        omitted = {"item": [], "char": [], "byte": []}
        for row in ordered:
            item = row["item"]
            eid = item.evidence_id
            if len(items) >= GRAPH_ITEMS:
                omitted["item"].append(eid)
                continue
            card = (f"[C{len(items)+1}]\nspeaker="
                    + ("USER" if item.provenance.source_role == "user" else "LUMINA")
                    + f"\nspoken_at={item.provenance.source_timestamp}"
                    + f"\nsource_group={labels[row['group']]}\ntext={item.text}")
            proposed = "\n".join((*cards, card))
            if len(proposed) > GRAPH_CHARS:
                omitted["char"].append(eid)
                continue
            if len(proposed.encode("utf-8")) > GRAPH_BYTES:
                omitted["byte"].append(eid)
                continue
            items.append(item)
            cards.append(card)
            blocks.append(render_reliable_fact(
                item, f"M{len(lock.selected_ids)+len(items)}",
                include_source_context=policy.include_source_context))
        view.check(lock.graph_version)
        index._check()
        diagnostics.update(graph_panel_ids=tuple(item.evidence_id for item in items),
                           graph_omitted_by_item_budget=tuple(omitted["item"]),
                           graph_omitted_by_char_budget=tuple(omitted["char"]),
                           graph_omitted_by_byte_budget=tuple(omitted["byte"]),
                           graph_pool_count=len(pool), graph_panel_count=len(items))
        context = MemoryContext(lock.query, tuple(items), "\n".join(blocks),
                                any(omitted.values()))
        return PreparedRecall(context, tuple(blocks),
                              tuple((item.evidence_id, ()) for item in items),
                              (lock.remaining_slots, FINAL_CHARS, FINAL_BYTES), tuple(cards))
    except ValueError as error:
        code = ("graph_supplement_snapshot_mismatch" if str(error) ==
                "graph_supplement_snapshot_mismatch" else "graph_supplement_unavailable")
    except Exception:
        code = "graph_supplement_unavailable"
    diagnostics["failure"] = code
    return PreparedRecall(MemoryContext(lock.query, safe_error_code=code))


def append_graph(lock: LockedBaseSelection, graph: PreparedRecall, suggestions):
    if graph.context.safe_error_code:
        raise ValueError(graph.context.safe_error_code)
    if type(suggestions) is not tuple or len(suggestions) > 6:
        raise ValueError("invalid_graph_supplement_selection")
    from .semantic_protocol import validate_use_relation
    relation = {"analogy": ("analogy", None),
                "same_event_detail": ("history", "same_event"),
                "disambiguation": ("history", "same_entity_background"),
                "boundary": ("history", "historical_boundary")}
    allowed_use, allowed_relation = relation[lock.graph_intent]
    for eid, use, role in suggestions:
        if (not validate_use_relation(use, role) or use != allowed_use
                or (allowed_relation is not None and role != allowed_relation)
                or eid in lock.selected_ids):
            raise ValueError("invalid_graph_supplement_selection")
    base = lock.context
    separator = "\n" if base.rendered_text else ""
    budget_chars = FINAL_CHARS-len(base.rendered_text)-len(separator)-len(GRAPH_SECTION)
    budget_bytes = (FINAL_BYTES-len(base.rendered_text.encode("utf-8"))
                    -len(separator.encode("utf-8"))-len(GRAPH_SECTION.encode("utf-8")))
    if budget_chars < 1 or budget_bytes < 1:
        return base, {"selected_order": lock.selected_ids, "rejected_by_budget": ()}
    bounded = replace(graph, _semantic_final_limits=(lock.remaining_slots,
                                                     budget_chars, budget_bytes))
    addition, packing = bounded.ranked_semantic_subset(suggestions)
    final_ids = lock.selected_ids + tuple(item.evidence_id for item in addition.evidence)
    final_text = (base.rendered_text + separator + GRAPH_SECTION + addition.rendered_text
                  if addition.rendered_text else base.rendered_text)
    if (final_ids[:len(lock.selected_ids)] != lock.selected_ids
            or not final_text.startswith(base.rendered_text)
            or len(final_ids) > FINAL_ITEMS):
        raise ValueError("graph_supplement_base_mutation")
    return MemoryContext(lock.query, base.evidence + addition.evidence, final_text,
                         base.truncated or addition.truncated), packing
