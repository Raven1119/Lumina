"""Explicit source-only prototype using existing Memory owners."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json

from Conversation_Memory.recall.hindsight_scoring import score_hindsight_post_rerank
from .models import IngestionResult, SourceExcerpt, SourceMemoryContext, SourceProvenance
from ._source_backend import SOURCE_DENSE_UNAVAILABLE

SOURCE_VERSION = "source-window-v1"
SOURCE_NEIGHBORS = 2


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def _source_rows(segment, fits):
    """Exact non-overlapping ranges bounded by the actual embedding tokenizer.

    No language-specific or question-dependent boundary exists. Every character
    retains eligibility, including long-turn tails. Neighbors are read separately.
    """
    rows = []
    for turn_index, turn in enumerate(segment.turns):
        start = 0
        while start < len(turn.content):
            low, high, end = start + 1, len(turn.content), None
            while low <= high:
                middle = (low + high) // 2
                if fits(turn.content[start:middle]):
                    end, low = middle, middle + 1
                else:
                    high = middle - 1
            if end is None:
                raise ValueError("source_embedding_window_unavailable")
            provenance = SourceProvenance(
                segment.segment_id, segment.conversation_id, turn.turn_id,
                turn.role, turn.timestamp.isoformat(), turn.source_timezone,
                SOURCE_VERSION, turn.timezone_source)
            eid = _hash([SOURCE_VERSION, segment.segment_id, turn.turn_id, start, end])
            rows.append({"text": turn.content[start:end], "timestamp": turn.timestamp,
                "metadata": {"evidence_id": eid, "memory_kind": SOURCE_VERSION,
                    "provenance": asdict(provenance), "turn_index": turn_index,
                    "source_start": start, "source_end": end, "turn_length": len(turn.content)}})
            start = end
    for index, row in enumerate(rows):
        row["metadata"].update(block_index=index, block_count=len(rows),
                               segment_id=segment.segment_id)
    return rows


def ingest_sources(adapter, segment):
    """Independent checkpoint; never consume Cold or complete Formation."""
    def result(status, **kw):
        return IngestionResult(segment.segment_id, SOURCE_VERSION, status, **kw)
    error = adapter._validate(segment)
    if error:
        return result("failed", safe_error_code=error)
    if len(segment.turns) > 32 or sum(len(t.content) for t in segment.turns) > 20000:
        return result("failed", safe_error_code="source_window_exceeded")
    key = adapter.state_store.key(segment.segment_id, SOURCE_VERSION)
    try:
        rows = _source_rows(segment, adapter.backend.source_text_fits)
        manifest = [{"text": r["text"], "metadata": r["metadata"]} for r in rows]
        fingerprint = _hash(manifest)
        previous = adapter.state_store.get(key)
        if previous is not None and (previous.get("source_fingerprint") != fingerprint
                                     or previous.get("manifest") != manifest):
            return result("failed", safe_error_code="source_checkpoint_mismatch")
        if previous is None:
            adapter.state_store.put(key, {"status": "in_progress",
                "source_fingerprint": fingerprint, "manifest": manifest})
        ids = []
        for row in rows:
            memory_id = adapter.backend.add_source(**row)
            adapter.backend.ensure_source_persisted(memory_id)
            ids.append(memory_id)
        adapter.backend.persist()
        if previous is None or previous.get("status") != "completed":
            adapter.state_store.put(key, {"status": "completed",
                "source_fingerprint": fingerprint, "manifest": manifest, "memory_ids": ids})
        return result("completed", memory_ids=tuple(ids),
                      already_ingested=bool(previous and previous.get("status") == "completed"))
    except Exception:
        return result("failed", retryable=True, safe_error_code="source_index_unavailable")


def _excerpt(candidate):
    raw = candidate.metadata
    if raw.get("memory_kind") != SOURCE_VERSION:
        raise ValueError("source_kind_required")
    item = SourceExcerpt(raw["evidence_id"], candidate.text,
        SourceProvenance(**raw["provenance"]), raw["turn_index"],
        raw["source_start"], raw["source_end"], raw["turn_length"])
    if (item.provenance.ingestion_version != SOURCE_VERSION
            or not 0 <= item.source_start < item.source_end <= item.turn_length
            or len(item.text) != item.source_end - item.source_start):
        raise ValueError("invalid_source_range")
    return item


def render_source(item):
    p = item.provenance
    header = {"session": p.conversation_id, "turn": item.turn_index,
              "role": "USER" if p.source_role == "user" else "LUMINA", "spoken_at": p.source_timestamp,
              "timezone": p.source_timezone,
              "range": [item.source_start, item.source_end, item.turn_length]}
    return "[SOURCE " + json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "]\n" + item.text


def recall_sources(adapter, query, policy):
    """Bounded positional context replaces source-inapplicable fact graph paths.

    +/-2 blocks never cross a segment. Anchors and neighbors share max_nodes.
    BGE receives the whole neighborhood if it fits; otherwise the whole anchor.
    Packing keeps whole groups or omits them, deduplicating exact source ranges.
    """
    adapter.last_source_read = {}
    if not isinstance(query, str) or not query.strip():
        return SourceMemoryContext(query if isinstance(query, str) else "",
                                   safe_error_code="invalid_query")
    query = query.strip()
    try:
        anchors = tuple(adapter.backend.source_candidates(query, policy))
        dense_error = (SOURCE_DENSE_UNAVAILABLE if
            getattr(adapter.backend, "last_source_stats", {}).get("dense_error_code")
            == SOURCE_DENSE_UNAVAILABLE else None)
        if not anchors:
            return SourceMemoryContext(query, truncated=bool(dense_error), safe_error_code=dense_error)
        available = {c.metadata["evidence_id"]: c for c in anchors}
        groups, truncated = [], bool(dense_error)
        for anchor in anchors:
            neighbors = adapter.backend.source_neighbors(anchor, before=SOURCE_NEIGHBORS,
                after=SOURCE_NEIGHBORS, limit=2 * SOURCE_NEIGHBORS + 1,
                known_candidates=available, max_nodes=policy.max_nodes)
            index = anchor.metadata["block_index"]
            wanted = min(anchor.metadata["block_count"], index + SOURCE_NEIGHBORS + 1) - max(0, index - SOURCE_NEIGHBORS)
            truncated = truncated or len(neighbors) < wanted
            group = []
            for candidate in neighbors:
                eid = candidate.metadata["evidence_id"]
                if eid not in available and len(available) >= policy.max_nodes:
                    truncated = True
                    continue
                available.setdefault(eid, candidate)
                group.append(candidate)
            if anchor not in group:
                group.append(anchor)
            groups.append(tuple(sorted(group, key=lambda c: c.metadata["block_index"])))
        reranker = adapter._get_bge_reranker()
        if reranker is None:
            raise ValueError("source_reranker_unavailable")
        scoring, omitted_context = [], 0
        for anchor, group in zip(anchors, groups):
            text = "\n".join(render_source(_excerpt(c)) for c in group)
            if not reranker.fits_pair(query, text):
                text = render_source(_excerpt(anchor))
                omitted_context += 1
            if not reranker.fits_pair(query, text):
                raise ValueError("source_reranker_window_exceeded")
            scoring.append(text)
        raw_scores = reranker.score(query, tuple(scoring))
        from .magma_adapter import _candidate_snapshot_reference_time
        timestamps = tuple(c.metadata["provenance"]["source_timestamp"] for c in anchors)
        scores = score_hindsight_post_rerank(raw_scores, timestamps,
                                            now=_candidate_snapshot_reference_time(tuple(c.metadata["provenance"]["source_timestamp"] for c in available.values())))
        ranked = sorted(range(len(anchors)), key=lambda i: (-scores[i].final_score, i))
        selected, parts, seen = [], [], set()
        for i in ranked:
            if policy.final_min_score is not None and scores[i].final_score < policy.final_min_score:
                continue
            pending = [_excerpt(c) for c in groups[i] if c.metadata["evidence_id"] not in seen]
            blocks = [render_source(item) for item in pending]
            rendered = "\n".join([*parts, *blocks])
            if len(selected) + len(pending) > policy.max_evidence_items or len(rendered) > policy.max_chars:
                truncated = True
                continue
            selected.extend(pending)
            parts.extend(blocks)
            seen.update(item.evidence_id for item in pending)
        adapter.last_source_read = {"anchors": anchors, "candidates": tuple(available.values()),
            "groups": groups, "scoring_texts": tuple(scoring),
            "bge_context_omitted": omitted_context, "bge_silent_truncations": 0,
            "selected_source_chars": sum(len(e.text) for e in selected),
            "rendered_chars": len("\n".join(parts))}
        return SourceMemoryContext(query, tuple(selected), "\n".join(parts), truncated, dense_error)
    except Exception:
        return SourceMemoryContext(query, safe_error_code="source_recall_unavailable")
