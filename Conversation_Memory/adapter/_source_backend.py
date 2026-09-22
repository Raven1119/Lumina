"""Opt-in Cold-derived source index in the existing graph/vector owners.

NARRATIVE is a storage carrier, not a verified fact. Source experiments use a
separate physical namespace. Source truth and multi-turn rendering stay above
this private boundary; no temporal or entity-fact links are created here.
"""
from __future__ import annotations

from copy import deepcopy
from bisect import bisect_left, bisect_right, insort
from datetime import datetime
from numbers import Integral
from time import perf_counter
from types import SimpleNamespace

from ._anchor_fusion import LexicalEventIndex, _rrf_fuse
from .models import BackendCandidate, SourceProvenance

SOURCE_KIND = "source-window-v1"
SOURCE_DENSE_UNAVAILABLE = "source_dense_unavailable"
SOURCE_BGE_TOKEN_LIMIT = 256


class SourceRangePreflightError(ValueError):
    """The range was rejected before any graph projection was attempted."""


def _record(backend, **values):
    if not hasattr(backend, "source_stats"):
        backend.source_stats = {}
    for key, value in values.items():
        backend.source_stats[key] = backend.source_stats.get(key, 0) + value


def _signature(backend):
    graph, vectors = backend.trg.graph_db, backend.trg.vector_db
    return (id(graph.nodes), len(graph.nodes), id(vectors.index),
            int(vectors.index.ntotal), len(vectors.id_to_index), len(vectors.index_to_id))


def _source_type(backend):
    value = getattr(backend._node_type, "NARRATIVE", None)
    if value is None:
        raise ValueError("source_node_type_unavailable")
    return value


def _validate_node(backend, node):
    if (not isinstance(node, backend._event_node_type)
            or not isinstance(node.node_id, str) or not node.node_id.strip()
            or node.node_type != _source_type(backend)
            or not isinstance(node.content_narrative, str) or not node.content_narrative
            or not isinstance(node.timestamp, datetime) or node.timestamp.tzinfo is None
            or node.timestamp.utcoffset() is None or not isinstance(node.attributes, dict)):
        raise ValueError("source_node_invalid")
    meta = node.attributes
    try:
        provenance = SourceProvenance(**meta["provenance"])
        required = (provenance.segment_id, provenance.conversation_id, provenance.turn_id,
                    provenance.source_timestamp, provenance.source_timezone, provenance.ingestion_version)
        spoken_at = datetime.fromisoformat(provenance.source_timestamp)
        if (any(not isinstance(value, str) or not value.strip() for value in required)
                or spoken_at.tzinfo is None or spoken_at.utcoffset() is None
                or node.timestamp != spoken_at
                or provenance.timezone_source not in {"client", "configured_default", "legacy_segment_fallback"}):
            raise ValueError("invalid_provenance")
    except (KeyError, TypeError, ValueError):
        raise ValueError("source_node_invalid") from None
    index, count = meta.get("block_index"), meta.get("block_count")
    start, end, length = (meta.get(key) for key in ("source_start", "source_end", "turn_length"))
    if (meta.get("memory_kind") != SOURCE_KIND or meta.get("evidence_id") != node.node_id
            or not isinstance(meta.get("segment_id"), str)
            or meta["segment_id"] != meta["provenance"]["segment_id"]
            or type(index) is not int or type(count) is not int or not 0 <= index < count
            or provenance.ingestion_version != SOURCE_KIND
            or type(meta.get("turn_index")) is not int or meta["turn_index"] < 0
            or any(type(value) is not int for value in (start, end, length))
            or not 0 <= start < end <= length or len(node.content_narrative) != end - start):
        raise ValueError("source_node_invalid")
    return meta["segment_id"], index, count


def _index_node(backend, node, *, check_only=False):
    """Increment one derived position/text entry; stored source stays authoritative."""
    segment, index, count = _validate_node(backend, node)
    meta, node_id = node.attributes, node.node_id
    provenance = meta["provenance"]
    conversation, turn_id = provenance["conversation_id"], provenance["turn_id"]
    start, end = meta["source_start"], meta["source_end"]
    slot = backend._source_segments.get(segment)
    if slot and (slot["count"] != count or slot["conversation_id"] != conversation
                 or slot["ids"].get(index, node_id) != node_id
                 or slot["turns"].get(meta["turn_index"], turn_id) != turn_id):
        raise ValueError("source_block_conflict")
    key = (segment, turn_id)
    turn = backend._source_turns.get(key)
    if turn:
        if (turn["provenance"] != provenance or turn["turn_index"] != meta["turn_index"]
                or turn["length"] != meta["turn_length"]):
            raise ValueError("source_turn_conflict")
        at = bisect_left(turn["starts"], start)
        if start in turn["ids"]:
            if turn["ids"][start] != node_id or turn["ends"][start] != end:
                raise ValueError("source_range_conflict")
        elif ((at and turn["ends"][turn["starts"][at - 1]] > start)
              or (at < len(turn["starts"]) and end > turn["starts"][at])):
            raise ValueError("source_range_conflict")
    previous = backend._source_nodes.get(node_id)
    if previous is not None:
        if (previous.content_narrative != node.content_narrative
                or previous.timestamp != node.timestamp or previous.attributes != meta):
            raise ValueError("source_provenance_conflict")
        return
    if check_only:
        return
    if slot is None:
        slot = backend._source_segments[segment] = {
            "count": count, "ids": {}, "turns": {}, "conversation_id": conversation,
            "first_index": index, "first_timestamp": node.timestamp,
        }
        session = backend._source_sessions.setdefault(conversation, {"segments": {}, "count": 0})
        session["segments"][segment] = slot
        session["count"] += count
    if index < slot["first_index"]:
        slot.update(first_index=index, first_timestamp=node.timestamp)
    if turn is None:
        turn = backend._source_turns[key] = {
            "provenance": deepcopy(provenance), "turn_index": meta["turn_index"],
            "length": meta["turn_length"], "starts": [], "ids": {}, "ends": {}, "covered": 0,
        }
    insort(turn["starts"], start)
    turn["ids"][start], turn["ends"][start] = node_id, end
    turn["covered"] += end - start
    slot["ids"][index], slot["turns"][meta["turn_index"]] = node_id, turn_id
    backend._source_nodes[node_id] = node
    backend._source_lexical.add(node)
    backend._source_present = True


def _index_vector(backend, node):
    """Grow a retained FAISS bitmap geometrically, avoiding per-append full copies."""
    vectors = backend.trg.vector_db
    position = vectors.id_to_index.get(node.node_id)
    if position is None:
        return  # graph-before-vector interruption: repair only in an explicit write
    if (not isinstance(position, Integral) or isinstance(position, bool)
            or not 0 <= position < vectors.index.ntotal
            or vectors.index_to_id.get(int(position)) != node.node_id):
        raise ValueError("source_vector_mapping_invalid")
    position = int(position)
    previous = backend._source_vector_positions.get(position)
    if previous is not None:
        if previous != node.node_id:
            raise ValueError("source_vector_mapping_invalid")
        return
    import faiss
    import numpy as np
    bitmap = backend._source_vector_bitmap
    required = position // 8 + 1
    if bitmap is None or len(bitmap) < required:
        size = max(8, len(bitmap) * 2 if bitmap is not None else 0)
        while size < required:
            size *= 2
        replacement = np.zeros(size, dtype=np.uint8)
        copied = len(bitmap) if bitmap is not None else 0
        if bitmap is not None:
            replacement[:len(bitmap)] = bitmap
        backend._source_vector_bitmap = bitmap = replacement
        backend._source_vector_selector = faiss.IDSelectorBitmap(bitmap)
        _record(backend, source_selector_builds=1, source_selector_bytes_copied=copied)
    bitmap[position // 8] |= 1 << (position % 8)
    backend._source_vector_positions[position] = node.node_id


def _refresh_written_node(backend, node):
    started = perf_counter()
    try:
        _index_node(backend, node)
        _index_vector(backend, node)
        backend._source_view_signature = _signature(backend)
    except Exception:
        backend._source_view_signature = None
        backend._source_view_error = "source_index_unavailable"
        raise
    finally:
        _record(backend, index_node_updates=1, index_update_ms=(perf_counter() - started) * 1000)


def rebuild(backend):
    """Reconstruct derived views at load/write time, never repair during reads."""
    started = perf_counter()
    backend._source_nodes = {}
    backend._source_segments = {}
    backend._source_turns = {}
    backend._source_sessions = {}
    backend._source_lexical = LexicalEventIndex()
    backend._source_event_count = 0
    backend._source_present = False
    backend._source_view_error = None
    backend._source_vector_positions = {}
    backend._source_vector_bitmap = None
    backend._source_vector_selector = None
    inspected = 0
    try:
        for node in backend.trg.graph_db.nodes.values():
            inspected += 1
            if getattr(node, "node_type", None) == backend._node_type.EVENT:
                backend._source_event_count += 1
            if getattr(node, "attributes", {}).get("memory_kind") != SOURCE_KIND:
                continue
            backend._source_present = True
            _index_node(backend, node)
            _index_vector(backend, node)
        backend._source_view_signature = _signature(backend)
    except Exception as exc:
        backend._source_view_signature = None
        backend._source_view_error = str(exc) if isinstance(exc, ValueError) else "source_index_unavailable"
    backend.last_source_stats = {"operation": "rebuild", "graph_nodes_inspected": inspected,
        "source_nodes": len(backend._source_nodes),
        "source_vectors": len(backend._source_vector_positions),
        "rebuild_ms": (perf_counter() - started) * 1000}
    _record(backend, index_rebuilds=1, index_nodes_inspected=inspected,
            index_rebuild_ms=backend.last_source_stats["rebuild_ms"])


def _check(backend):
    if getattr(backend, "_source_event_count", 0):
        raise ValueError("source_store_contains_facts")
    if getattr(backend, "_source_view_error", None):
        raise ValueError(backend._source_view_error)
    if getattr(backend, "_source_view_signature", None) != _signature(backend):
        raise ValueError("source_index_stale")


def _token_count(backend, text):
    model = backend.trg.encoder.model
    maximum = model.max_seq_length
    if type(maximum) is not int or maximum < 1:
        raise ValueError("source_embedding_window_unavailable")
    encoded = model.tokenizer(text, add_special_tokens=True, truncation=False,
                              return_attention_mask=False, return_token_type_ids=False)
    _record(backend, minilm_tokenizer_checks=1)
    return len(encoded["input_ids"]), maximum


def text_fits(backend, text):
    if not isinstance(text, str) or not text:
        return False
    started = perf_counter()
    count, maximum = _token_count(backend, text)
    if not hasattr(backend, "_source_bge_tokenizer"):
        from transformers import AutoTokenizer
        from Conversation_Memory.recall.bge_reranker import BGE_MODEL, BGE_REVISION
        loaded_at = perf_counter()
        backend._source_bge_tokenizer = AutoTokenizer.from_pretrained(
            BGE_MODEL, revision=BGE_REVISION, local_files_only=True)
        _record(backend, bge_tokenizer_loads=1,
                bge_tokenizer_load_ms=(perf_counter() - loaded_at) * 1000)
    encoded = backend._source_bge_tokenizer(text, add_special_tokens=True,
        truncation=False, return_attention_mask=False, return_token_type_ids=False)
    bge_count = len(encoded["input_ids"])
    fits = count <= maximum and bge_count <= SOURCE_BGE_TOKEN_LIMIT
    fit = {"minilm_tokens": count, "minilm_max_tokens": maximum,
           "bge_tokens": bge_count, "bge_source_max_tokens": SOURCE_BGE_TOKEN_LIMIT,
           "fits": fits}
    backend.last_source_stats["last_fit"] = fit
    _record(backend, source_fit_checks=1, bge_tokenizer_checks=1,
            source_fit_rejections=int(not fits), source_fit_ms=(perf_counter() - started) * 1000)
    return fits


def _ensure_vector(backend, node):
    import numpy as np
    vectors = backend.trg.vector_db
    position = vectors.id_to_index.get(node.node_id)
    if position is not None:
        if (not isinstance(position, Integral) or isinstance(position, bool)
                or not 0 <= position < vectors.index.ntotal
                or vectors.index_to_id.get(int(position)) != node.node_id):
            raise ValueError("source_vector_mapping_invalid")
        return False
    if not isinstance(node.embedding_vector, list) or not node.embedding_vector:
        raise ValueError("source_embedding_missing")
    if not vectors.add_vector(node.node_id, np.asarray(node.embedding_vector, dtype=np.float32),
            metadata={"timestamp": node.timestamp.isoformat(), "memory_kind": SOURCE_KIND}):
        raise ValueError("source_vector_write_failed")
    return True


def add(backend, text, timestamp, metadata):
    _check(backend)
    if not text_fits(backend, text):
        raise ValueError("source_embedding_window_exceeded")
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("source_timestamp_invalid")
    meta = deepcopy(metadata)
    if meta.get("memory_kind", SOURCE_KIND) != SOURCE_KIND:
        raise ValueError("source_kind_invalid")
    meta["memory_kind"] = SOURCE_KIND
    node = backend._event_node_type(node_id=meta.get("evidence_id"), node_type=_source_type(backend),
        timestamp=timestamp, content_narrative=text, attributes=meta, embedding_vector=None)
    segment, index, count = _validate_node(backend, node)
    _index_node(backend, node, check_only=True)
    previous = backend.trg.graph_db.get_node(node.node_id)
    slot = backend._source_segments.get(segment)
    if slot and (slot["count"] != count or slot["ids"].get(index, node.node_id) != node.node_id):
        raise ValueError("source_block_conflict")
    started = perf_counter()
    embedding_ms = 0.0
    if previous is not None:
        _validate_node(backend, previous)
        if (previous.content_narrative != text or previous.timestamp != timestamp or previous.attributes != meta):
            raise ValueError("source_provenance_conflict")
        node = previous
    else:
        encoded_at = perf_counter()
        vector = backend.trg.encoder.encode(text)
        embedding_ms = (perf_counter() - encoded_at) * 1000
        if len(vector.shape) == 2:
            vector = vector[0]
        node.embedding_vector = vector.tolist()
        backend.trg.graph_db.add_node(node)
    try:
        vector_added = _ensure_vector(backend, node)
    finally:
        _refresh_written_node(backend, node)
    backend.last_source_stats.update(operation="add", embedding_calls=int(previous is None),
        embedding_ms=embedding_ms, graph_nodes_added=int(previous is None), vector_added=vector_added,
        operation_ms=(perf_counter() - started) * 1000)
    _record(backend, source_add_calls=1, source_embedding_calls=int(previous is None),
            source_embedding_ms=embedding_ms, source_nodes_added=int(previous is None),
            source_vectors_added=int(vector_added), source_add_ms=backend.last_source_stats["operation_ms"])
    return node.node_id


def ensure_persisted(backend, node_id):
    _check(backend)
    node = backend.trg.graph_db.get_node(node_id)
    _validate_node(backend, node)
    started = perf_counter()
    try:
        vector_added = _ensure_vector(backend, node)
    finally:
        _refresh_written_node(backend, node)
    backend.last_source_stats.update(operation="ensure", embedding_calls=0,
        vector_added=vector_added, operation_ms=(perf_counter() - started) * 1000)
    _record(backend, source_ensure_calls=1, source_vectors_repaired=int(vector_added))


def _candidate(node, score=None):
    return BackendCandidate(node.content_narrative, node.timestamp.isoformat(), score, deepcopy(node.attributes))


def candidates(backend, query, policy):
    _check(backend)
    if not isinstance(query, str) or not query.strip():
        return []
    import faiss
    import numpy as np
    started = perf_counter()
    stats = {"operation": "candidates", "source_nodes": len(backend._source_nodes),
        "source_vectors": len(backend._source_vector_positions), "dense_node_reads": 0,
        "lexical_node_reads": 0, "embedding_calls": 0}
    backend.last_source_stats = stats
    dense = []
    limit = min(policy.top_k, policy.max_nodes)
    if backend._source_vector_selector is not None:
        try:
            enriched = backend.trg.keyword_enricher.enrich_query(query)
            count, maximum = _token_count(backend, enriched)
            stats.update(query_embedding_tokens=count, query_embedding_truncated=count > maximum)
            encoded_at = perf_counter()
            stats["embedding_calls"] = 1  # Count an attempted encode even when it raises.
            try:
                vector = np.asarray(backend.trg.encoder.encode(enriched), dtype=np.float32).reshape(1, -1)
            finally:
                stats["embedding_ms"] = (perf_counter() - encoded_at) * 1000
            search_at = perf_counter()
            try:
                _distances, positions = backend.trg.vector_db.index.search(vector,
                    min(limit, len(backend._source_vector_positions)),
                    params=faiss.SearchParameters(sel=backend._source_vector_selector))
            finally:
                stats["dense_search_ms"] = (perf_counter() - search_at) * 1000
            for position in positions[0]:
                if position < 0:
                    continue
                node_id = backend.trg.vector_db.index_to_id.get(int(position))
                node = backend._source_nodes.get(node_id)
                stats["dense_node_reads"] += 1
                if node is not None:
                    dense.append(node)
        except Exception:
            # Query-time dense failure does not invalidate the checked source view.
            # Never repair an index or disclose the exception body during a read.
            dense = []
            stats["dense_error_code"] = SOURCE_DENSE_UNAVAILABLE
    class CountedGraph:
        def get_node(self, node_id):
            stats["lexical_node_reads"] += 1
            return backend._source_nodes.get(node_id)
    lexical_at = perf_counter()
    lexical = backend._source_lexical.rank(graph_db=CountedGraph(), query=query,
        max_nodes=policy.max_nodes, event_node_type=backend._event_node_type,
        node_type=SimpleNamespace(EVENT=_source_type(backend)), score_posting_overlap=True)
    stats["lexical_ms"] = (perf_counter() - lexical_at) * 1000
    fused = _rrf_fuse((dense, lexical), limit=limit)
    stats.update(dense_candidates=len(dense), lexical_candidates=len(lexical),
        candidates_returned=len(fused), operation_ms=(perf_counter() - started) * 1000)
    stats["segment_turn_counts"] = {
        node.attributes["segment_id"]: (
            max(backend._source_segments[node.attributes["segment_id"]]["turns"]) + 1
            if len(backend._source_segments[node.attributes["segment_id"]]["ids"])
               == backend._source_segments[node.attributes["segment_id"]]["count"] else None)
        for node, _score in fused}
    _record(backend, source_candidate_calls=1,
            source_dense_failures=int("dense_error_code" in stats),
            query_embedding_calls=stats["embedding_calls"],
            query_embedding_ms=stats.get("embedding_ms", 0),
            query_embedding_truncations=int(stats.get("query_embedding_truncated", False)),
            dense_search_ms=stats.get("dense_search_ms", 0), lexical_ms=stats["lexical_ms"],
            dense_node_reads=stats["dense_node_reads"], lexical_node_reads=stats["lexical_node_reads"],
            candidates_returned=len(fused), source_candidate_ms=stats["operation_ms"])
    return [_candidate(node, score) for node, score in fused]


def neighbors(backend, candidate, *, before=2, after=2, limit=5,
              known_candidates=None, max_nodes=None):
    _check(backend)
    if any(type(x) is not int or x < 0 for x in (before, after)) or type(limit) is not int or limit < 1:
        raise ValueError("source_neighbor_bound_invalid")
    if max_nodes is not None and (type(max_nodes) is not int or max_nodes < 1):
        raise ValueError("source_neighbor_bound_invalid")
    known = {} if known_candidates is None else known_candidates
    if not isinstance(known, dict):
        raise ValueError("source_known_candidates_invalid")
    anchor = backend._source_nodes.get(candidate.metadata.get("evidence_id"))
    if anchor is None:
        raise ValueError("source_anchor_missing")
    segment, center, count = _validate_node(backend, anchor)
    slots = backend._source_segments[segment]["ids"]
    indices, radius = [center], 1
    while len(indices) < limit and (radius <= before or radius <= after):
        if radius <= before and center - radius >= 0:
            indices.append(center - radius)
        if len(indices) < limit and radius <= after and center + radius < count:
            indices.append(center + radius)
        if center - radius < 0 and center + radius >= count:
            break
        radius += 1
    result, projected, missed = [], 0, False
    for index in sorted(indices):
        node_id = slots.get(index)
        if node_id is None:
            continue
        if node_id in known:
            result.append(known[node_id])
            continue
        if max_nodes is not None and len(known) >= max_nodes:
            missed = True
            continue
        node = backend.trg.graph_db.get_node(node_id)
        projected += 1
        if node is not None:
            item = _candidate(node, candidate.score if index == center else None)
            known[node_id] = item
            result.append(item)
    stats = backend.last_source_stats
    stats["neighbor_calls"] = stats.get("neighbor_calls", 0) + 1
    stats["neighbor_position_reads"] = stats.get("neighbor_position_reads", 0) + len(indices)
    stats["neighbor_new_candidates"] = stats.get("neighbor_new_candidates", 0) + projected
    stats["neighbors_returned"] = stats.get("neighbors_returned", 0) + len(result)
    stats["source_neighbors_truncated"] = stats.get("source_neighbors_truncated", False) or missed
    _record(backend, source_neighbor_calls=1, neighbor_position_reads=len(indices),
            neighbor_new_candidates=projected, neighbors_returned=len(result),
            neighbor_budget_truncations=int(missed))
    return result


def _navigation_bounds(limit, known_candidates, max_nodes):
    if type(limit) is not int or limit < 1:
        raise ValueError("source_navigation_bound_invalid")
    if max_nodes is not None and (type(max_nodes) is not int or max_nodes < 1):
        raise ValueError("source_navigation_bound_invalid")
    known = {} if known_candidates is None else known_candidates
    if not isinstance(known, dict) or (max_nodes is not None and len(known) > max_nodes):
        raise ValueError("source_known_candidates_invalid")
    return known


def _project_source(backend, node_id, known, stats):
    node = backend._source_nodes.get(node_id)
    if node is None:
        raise ValueError("source_node_missing")
    if node_id in known:
        item = known[node_id]
        if (not isinstance(item, BackendCandidate) or item.text != node.content_narrative
                or item.metadata != node.attributes or item.timestamp != node.timestamp.isoformat()):
            raise ValueError("source_known_candidate_mismatch")
        return item
    node = backend.trg.graph_db.get_node(node_id)
    stats["new_node_reads"] += 1
    _validate_node(backend, node)
    item = _candidate(node)
    known[node_id] = item
    return item


def locate(backend, refs, *, limit, known_candidates=None, max_nodes=None, max_refs=None):
    """Navigate literal refs to source blocks, never promote a fact's bindings.

    Flat refs combine a fact/mention provenance with its exact turn offsets and
    supporting_span. Ingestion versions are deliberately not compared across
    fact/source representations. A multi-block span is verified in full before
    any of its blocks become navigation hits. Failed validation may consume
    inspected-node budget, but cannot authorize the unverified locator.
    """
    _check(backend)
    max_nodes = limit if max_nodes is None else max_nodes
    known = _navigation_bounds(limit, known_candidates, max_nodes)
    if not isinstance(refs, (tuple, list)):
        raise ValueError("source_refs_invalid")
    max_refs = limit if max_refs is None else max_refs
    if type(max_refs) is not int or max_refs < 1:
        raise ValueError("source_navigation_bound_invalid")
    stats = {"refs_requested": len(refs), "refs_checked": 0, "refs_matched": 0,
             "refs_invalid": 0, "refs_missing": 0, "refs_mismatched": 0,
             "position_reads": 0, "new_node_reads": 0, "candidates_returned": 0,
             "truncated": len(refs) > max_refs}
    backend.last_source_stats["last_locate"] = stats
    output = {}
    fields = ("segment_id", "conversation_id", "turn_id", "source_role",
              "source_timestamp", "source_timezone", "timezone_source")
    for ref in refs[:max_refs]:
        stats["refs_checked"] += 1
        if not isinstance(ref, dict):
            stats["refs_invalid"] += 1
            continue
        start, end, span = ref.get("source_start"), ref.get("source_end"), ref.get("supporting_span")
        if (any(not isinstance(ref.get(k), str) or not ref[k] for k in fields)
                or type(start) is not int or type(end) is not int or not 0 <= start < end
                or not isinstance(span, str) or len(span) != end - start):
            stats["refs_invalid"] += 1
            continue
        turn = backend._source_turns.get((ref["segment_id"], ref["turn_id"]))
        if turn is None:
            stats["refs_missing"] += 1
            continue
        if end > turn["length"] or any(ref[k] != turn["provenance"][k] for k in fields):
            stats["refs_mismatched"] += 1
            continue
        starts = turn["starts"]
        lo, hi = max(0, bisect_right(starts, start) - 1), bisect_left(starts, end)
        if hi - lo > limit:
            stats["truncated"] = True
            continue
        positions = starts[lo:hi]
        stats["position_reads"] += len(positions)
        ids = [turn["ids"][position] for position in positions]
        if (len(set(output).union(ids)) > limit
                or (max_nodes is not None and len(set(known).union(ids)) > max_nodes)):
            stats["truncated"] = True
            continue
        pieces, found, cursor = [], [], start
        for position, node_id in zip(positions, ids):
            if position > cursor or turn["ends"][position] <= cursor:
                break
            item = _project_source(backend, node_id, known, stats)
            until = min(end, turn["ends"][position])
            pieces.append(item.text[cursor - position:until - position])
            found.append(item)
            cursor = until
        if cursor != end:
            stats["refs_missing"] += 1
            continue
        if "".join(pieces) != span:
            stats["refs_mismatched"] += 1
            continue
        stats["refs_matched"] += 1
        for item in found:
            output.setdefault(item.metadata["evidence_id"], item)
    stats["candidates_returned"] = len(output)
    _record(backend, source_locate_calls=1, source_locate_refs_checked=stats["refs_checked"],
            source_locate_position_reads=stats["position_reads"],
            source_locate_node_reads=stats["new_node_reads"],
            source_locate_matched=stats["refs_matched"],
            source_locate_invalid=stats["refs_invalid"] + stats["refs_mismatched"],
            source_locate_missing=stats["refs_missing"], source_locate_truncations=int(stats["truncated"]))
    return list(output.values())


def parent(backend, candidate, *, limit, known_candidates=None, max_nodes=None):
    """Return the complete indexed conversation parent, or no partial substitute.

    Completeness concerns currently indexed segments, not an entire topic or
    unseen Cold history. Cold fallback conversation IDs may identify only one
    segment. Across real shared IDs, segment-first timestamps order segments;
    original block order is preserved within each segment.
    """
    _check(backend)
    max_nodes = limit if max_nodes is None else max_nodes
    known = _navigation_bounds(limit, known_candidates, max_nodes)
    node = backend._source_nodes.get(candidate.metadata.get("evidence_id"))
    if node is None:
        raise ValueError("source_anchor_missing")
    _validate_node(backend, node)
    conversation = node.attributes["provenance"]["conversation_id"]
    session = backend._source_sessions[conversation]
    stats = {"conversation_id": conversation, "declared_blocks": session["count"],
             "indexed_segments": len(session["segments"]), "complete": False,
             "truncated": False, "reason": None, "position_reads": 0, "new_node_reads": 0,
             "order_basis": "segment_first_spoken_at_then_segment_id_then_original_block_index"}
    backend.last_source_stats["last_parent"] = stats
    result = []
    try:
        if session["count"] > limit:
            stats.update(truncated=True, reason="source_parent_limit")
            return []
        ids = []
        for segment, slot in sorted(session["segments"].items(),
                                    key=lambda entry: (entry[1]["first_timestamp"], entry[0])):
            if len(slot["ids"]) != slot["count"]:
                stats["reason"] = "source_parent_incomplete"
                return []
            for turn_id in slot["turns"].values():
                turn = backend._source_turns[(segment, turn_id)]
                if turn["covered"] != turn["length"]:
                    stats["reason"] = "source_parent_incomplete"
                    return []
            ids.extend(slot["ids"][i] for i in range(slot["count"]))
        stats["position_reads"] = len(ids)
        if max_nodes is not None and len(set(known).union(ids)) > max_nodes:
            stats.update(truncated=True, reason="source_parent_node_budget")
            return []
        result = [_project_source(backend, node_id, known, stats) for node_id in ids]
        stats["complete"] = True
        return result
    finally:
        stats["candidates_returned"] = len(result)
        _record(backend, source_parent_calls=1, source_parent_complete=int(stats["complete"]),
                source_parent_position_reads=stats["position_reads"],
                source_parent_node_reads=stats["new_node_reads"],
                source_parent_truncations=int(stats["truncated"]),
                source_parent_incomplete=int(stats["reason"] == "source_parent_incomplete"))


def read_range(backend, segment_id, start_turn, end_turn, *, start_char=0,
               end_char=None, limit, max_chars, known_candidates=None):
    """Read a bounded literal range; incomplete results retain an exact cursor.

    Turn numbers are original positions inside one Cold segment, not a topic or
    inferred identity. This path never requires a whole conversation parent.
    ``limit`` bounds additional graph projections; ``max_chars`` bounds source
    body characters before the facade applies its rendered evidence allowance.
    """
    _check(backend)
    if (not isinstance(segment_id, str) or not segment_id
            or any(type(v) is not int for v in (start_turn, end_turn, start_char, limit, max_chars))
            or not 0 <= start_turn <= end_turn or start_char < 0 or limit < 0 or max_chars < 1
            or (end_char is not None and (type(end_char) is not int or end_char < 1))):
        raise SourceRangePreflightError("source_range_invalid")
    known = {} if known_candidates is None else known_candidates
    if not isinstance(known, dict):
        raise SourceRangePreflightError("source_known_candidates_invalid")
    slot = backend._source_segments.get(segment_id)
    if slot is None or not slot["turns"]:
        raise SourceRangePreflightError("source_range_missing")
    final_turn = max(slot["turns"])
    if end_turn > final_turn:
        raise SourceRangePreflightError("source_range_invalid")
    stats = {"segment_id": segment_id, "requested_start_turn": start_turn,
             "requested_end_turn": end_turn,
             "segment_turn_count": final_turn + 1 if len(slot["ids"]) == slot["count"] else None,
             "indexed_turn_count": len(slot["turns"]), "indexed_turn_extent": final_turn + 1,
             "new_node_reads": 0, "position_reads": 0, "source_chars": 0,
             "requested_complete": False, "next_range": None, "reason": None}
    backend.last_source_stats["last_range"] = stats
    result = []
    try:
        from .source_memory import _hash
        for index in range(start_turn, end_turn + 1):
            turn_id = slot["turns"].get(index)
            turn = backend._source_turns.get((segment_id, turn_id))
            # The first turn's validation precedes every possible projection.
            range_error = SourceRangePreflightError if index == start_turn else ValueError
            if turn is None:
                raise range_error("source_range_missing")
            first = start_char if index == start_turn else 0
            stop = end_char if index == end_turn and end_char is not None else turn["length"]
            if not 0 <= first < stop <= turn["length"]:
                raise range_error("source_range_invalid")
            starts = turn["starts"]
            position = max(0, bisect_right(starts, first) - 1)
            cursor = first
            while cursor < stop:
                stats["next_range"] = {"segment_id": segment_id, "start_turn": index,
                                       "end_turn": end_turn, "start_char": cursor,
                                       "end_char": end_char}
                if position >= len(starts):
                    raise ValueError("source_range_missing")
                begin = starts[position]
                node_id = turn["ids"][begin]
                stats["position_reads"] += 1
                if begin > cursor or turn["ends"][begin] <= cursor:
                    raise ValueError("source_range_missing")
                if node_id not in known and stats["new_node_reads"] >= limit:
                    stats["reason"] = "source_range_node_budget"
                    return result
                room = max_chars - stats["source_chars"]
                if room <= 0:
                    stats["reason"] = "source_range_character_budget"
                    return result
                item = _project_source(backend, node_id, known, stats)
                until = min(stop, turn["ends"][begin], cursor + room)
                text = item.text[cursor - begin:until - begin]
                if len(text) != until - cursor:
                    raise ValueError("source_range_missing")
                meta = deepcopy(item.metadata)
                meta.update(source_start=cursor, source_end=until,
                            evidence_id=_hash([SOURCE_KIND, segment_id, turn_id, cursor, until]))
                result.append(BackendCandidate(text, item.timestamp, None, meta))
                stats["source_chars"] += len(text)
                cursor = until
                if cursor >= turn["ends"][begin]:
                    position += 1
        stats.update(requested_complete=True, next_range=None)
        return result
    finally:
        stats["ranges_returned"] = len(result)
        _record(backend, source_range_calls=1,
                source_range_position_reads=stats["position_reads"],
                source_range_node_reads=stats["new_node_reads"],
                source_range_chars=stats["source_chars"],
                source_range_incomplete=int(not stats["requested_complete"]))
