"""Opt-in monotonic semantic panel: a frozen index base, then graph-only additions."""
from __future__ import annotations

from dataclasses import asdict, replace

from Conversation_Memory.recall.rendering import render_reliable_fact

from ._first_hit_read import discover_first_hit_read
from ._semantic_recall import prepare_semantic_recall_v2
from .models import MemoryContext, MemoryEvidence, PreparedRecall, SourceProvenance

BASE_ITEMS = 32
BASE_CHARS = 9000
BASE_BYTES = 36000
FULL_ITEMS = 40
FULL_CHARS = 12000
FULL_BYTES = 48000
GRAPH_ITEMS = 8
FINAL_LIMITS = (3, 5000, 20000)


def prepare_semantic_recall_v3(adapter, query, policy, *, seed_only=False):
    """Build exactly one graph-independent base; graph discovery may only append."""
    from .magma_adapter import _relation_compatible, _RELATION_RESOLVER

    base_policy = replace(
        policy, max_evidence_items=min(policy.max_evidence_items, BASE_ITEMS),
        max_chars=min(policy.max_chars, BASE_CHARS),
        max_bytes=min(policy.max_bytes or BASE_BYTES, BASE_BYTES),
    )
    base = prepare_semantic_recall_v2(adapter, query, base_policy, seed_only=True)
    diagnostics = dict(getattr(adapter, "_last_semantic_read_diagnostics", {}))
    diagnostics["profile"] = "semantic-associative-v3"
    diagnostics["seed_only"] = seed_only
    base_ids = tuple(item.evidence_id for item in base.context.evidence)
    base_cards = tuple(card for _, card in base.selection_items) if not base.context.safe_error_code else ()
    diagnostics.update(
        base_panel_ids=base_ids,
        graph_exclusive_pool_ids=(), graph_appended_ids=(),
        graph_omitted_by_item_budget=(), graph_omitted_by_char_budget=(),
        graph_omitted_by_byte_budget=(), full_panel_ids=base_ids,
    )
    adapter._last_semantic_read_diagnostics = diagnostics
    if base.context.safe_error_code or seed_only:
        return base

    def unavailable(code):
        diagnostics["failure"] = code
        return PreparedRecall(MemoryContext(query, safe_error_code=code))

    try:
        view = adapter.backend.first_hit_view()
        version = view.version
        if diagnostics["first_hit"]["graph_version"] != version:
            return unavailable("semantic_panel_monotonicity_violation")
        index = adapter.backend.calibrated_read_index()
        bounds = replace(adapter.first_hit,
                         max_seeds=min(adapter.first_hit.max_seeds, 5),
                         max_nodes=min(adapter.first_hit.max_nodes, 64),
                         max_edges=min(adapter.first_hit.max_edges, 256))
        refs = tuple(adapter.backend.resolve_target_entity_refs(query, limit=bounds.max_seeds))
        hits, search, qvector = index.search(query, target_entity_refs=refs, limit=20)
        if tuple(hit.node_id for hit in hits) != tuple(diagnostics["index_hit_ids"]):
            return unavailable("semantic_panel_monotonicity_violation")
        diagnostics["graph_query_search"] = search
        seeds = tuple((row["node_id"], row["weight"]) for row in diagnostics["seeds"]
                      if row["weight"] > 0)
        result = discover_first_hit_read(view, seeds, bounds)
        adapter._last_first_hit_snapshot = result
        diagnostics["graph_first_hit"] = dict(result.stats)
        diagnostics["graph_read_arcs"] = result.read_arcs
        index_ids = set(diagnostics["index_hit_ids"])
        reached = {arc[1] for arc in result.read_arcs}
        graph_ids = tuple(node_id for node_id in result.fact_ids
                          if node_id not in index_ids and node_id in reached)
        support = index.score_nodes(qvector, query, graph_ids)
        relation_ids = _RELATION_RESOLVER.resolve_query_relations(policy.relation_surfaces or ())
        pool = []
        for node_id in graph_ids:
            candidate = adapter.backend.first_hit_candidate(node_id)
            if candidate is None or not _relation_compatible(candidate, relation_ids):
                continue
            eid = candidate.metadata.get("evidence_id")
            if (not isinstance(eid, str) or not eid or eid in base_ids
                    or not isinstance(candidate.text, str) or not candidate.text.strip()):
                continue
            provenance = SourceProvenance(**candidate.metadata["provenance"])
            if not all(isinstance(value, str) and value.strip()
                       for value in asdict(provenance).values()):
                continue
            item = MemoryEvidence(eid, candidate.text, candidate.timestamp, provenance)
            cosine, lexical = support.get(node_id, (0.0, 0.0))
            pool.append({"item": item, "group": provenance.segment_id or provenance.conversation_id,
                         "h": result.h[node_id], "cosine": cosine, "lexical": lexical,
                         "node_id": node_id})
        # A repeated graph visit never gives the same canonical Fact another slot.
        unique = {row["item"].evidence_id: row for row in pool}
        pool = list(unique.values())
        diagnostics["graph_exclusive_pool_ids"] = tuple(sorted(unique))
        groups = sorted({row["group"] for row in pool})
        def first_key(row):
            return (-row["h"], -row["cosine"], -row["lexical"],
                    row["item"].evidence_id)
        def fill_key(row):
            return (-row["h"], -max(row["cosine"], row["lexical"]),
                    row["item"].evidence_id)
        representatives = [min((row for row in pool if row["group"] == group),
                               key=first_key) for group in groups]
        ordered = sorted(representatives, key=first_key)
        represented = {row["item"].evidence_id for row in ordered}
        ordered.extend(sorted((row for row in pool if row["item"].evidence_id not in represented),
                              key=fill_key))

        labels = {row["source_group"]: row["source_group_label"]
                  for row in diagnostics["candidate_outcomes"].values()
                  if "source_group" in row and "source_group_label" in row}
        next_label = max((int(value[1:]) for value in labels.values()
                          if value.startswith("G") and value[1:].isdigit()), default=0) + 1
        for group in groups:
            if group not in labels:
                labels[group] = f"G{next_label}"
                next_label += 1
        items = list(base.context.evidence)
        blocks = list(base._rendered_blocks)
        cards = list(base_cards)
        added = []
        omitted = {"item": [], "char": [], "byte": []}
        max_items = min(policy.max_evidence_items, FULL_ITEMS)
        max_chars = min(policy.max_chars, FULL_CHARS)
        max_bytes = min(policy.max_bytes or FULL_BYTES, FULL_BYTES)
        for row in ordered:
            item = row["item"]
            eid = item.evidence_id
            if len(added) >= GRAPH_ITEMS or len(items) >= max_items:
                omitted["item"].append(eid)
                continue
            card = (f"[C{len(items)+1}]\nspeaker="
                    + ("USER" if item.provenance.source_role == "user" else "LUMINA")
                    + f"\nspoken_at={item.provenance.source_timestamp}"
                    + f"\nsource_group={labels[row['group']]}\ntext={item.text}")
            proposed = "\n".join((*cards, card))
            if len(proposed) > max_chars:
                omitted["char"].append(eid)
                continue
            if len(proposed.encode("utf-8")) > max_bytes:
                omitted["byte"].append(eid)
                continue
            items.append(item)
            cards.append(card)
            blocks.append(render_reliable_fact(
                item, f"M{len(items)}", include_source_context=policy.include_source_context))
            added.append(eid)
        full_ids = tuple(item.evidence_id for item in items)
        if full_ids[:len(base_ids)] != base_ids or tuple(cards[:len(base_cards)]) != base_cards:
            return unavailable("semantic_panel_monotonicity_violation")
        view.check(version)
        index._check()
        diagnostics.update(
            graph_appended_ids=tuple(added),
            graph_omitted_by_item_budget=tuple(omitted["item"]),
            graph_omitted_by_char_budget=tuple(omitted["char"]),
            graph_omitted_by_byte_budget=tuple(omitted["byte"]),
            full_panel_ids=full_ids,
        )
        context = MemoryContext(query, tuple(items), "\n".join(blocks),
                                base.context.truncated or any(omitted.values()))
        return PreparedRecall(context, tuple(blocks),
                              tuple((item.evidence_id, ()) for item in items),
                              FINAL_LIMITS, tuple(cards))
    except Exception:
        return unavailable("semantic_read_unavailable")
