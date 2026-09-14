"""Transient, bounded search and literal range access over the source owner.

There is no fact formation, identity inference, parent-completeness requirement
or persistent reader state. Every excerpt exposed by either operation remains
in the accumulated evidence context; it cannot be silently replaced or forgotten.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
from .models import RecallPolicy, SourceExcerpt, SourceMemoryContext
from ._source_backend import SourceRangePreflightError
from .source_memory import _excerpt, _hash, render_source


@dataclass(frozen=True)
class SourceReadLimits:
    max_chars: int = 5000
    max_node_reads: int = 200
    max_searches: int = 3
    max_reads: int = 6
    search_candidates: int = 10
    search_results: int = 5
    snippet_chars: int = 120
    read_chars: int = 2000

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError("invalid_source_read_limits")
        if self.search_results > self.search_candidates:
            raise ValueError("invalid_source_read_limits")


def merge_ranges(items):
    """Union exact overlapping/adjacent ranges without bridging unobserved text."""
    grouped = {}
    for item in items:
        p = item.provenance
        grouped.setdefault((p.segment_id, p.turn_id), []).append(item)
    merged = []
    for group in grouped.values():
        group.sort(key=lambda x: (x.source_start, x.source_end))
        active = group[0]
        for item in group[1:]:
            if (item.provenance != active.provenance or item.turn_index != active.turn_index
                    or item.turn_length != active.turn_length):
                raise ValueError("source_range_conflict")
            if item.source_start > active.source_end:
                merged.append(active); active = item; continue
            overlap = min(active.source_end, item.source_end) - item.source_start
            if active.text[item.source_start-active.source_start:item.source_start-active.source_start+overlap] != item.text[:overlap]:
                raise ValueError("source_range_conflict")
            end = max(active.source_end, item.source_end)
            text = active.text + item.text[max(0, active.source_end-item.source_start):]
            active = replace(active, text=text, source_end=end,
                evidence_id=_hash([active.provenance.ingestion_version,
                    active.provenance.segment_id, active.provenance.turn_id, active.source_start, end]))
        merged.append(active)
    segment_order = {}
    for item in merged:
        segment_order.setdefault(item.provenance.segment_id, len(segment_order))
    return tuple(sorted(merged, key=lambda x: (segment_order[x.provenance.segment_id], x.turn_index, x.source_start)))


def render_ranges(items):
    """Show original positions; a selected range is never called a whole session."""
    if not items:
        return ""
    compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    parts = ["[SOURCE RANGES: selected original text; unshown ranges and other history may exist.]"]
    segment = None
    for item in items:
        p = item.provenance
        if p.segment_id != segment:
            segment = p.segment_id
            parts.append("[SOURCE " + compact({"segment": segment, "session": p.conversation_id}) + "]")
        header = {"turn": item.turn_index, "spoken_at": p.source_timestamp,
                  "timezone": p.source_timezone,
                  "range": [item.source_start, item.source_end, item.turn_length]}
        role = "USER" if p.source_role == "user" else "LUMINA"
        parts.append("[" + role + " " + compact(header) + "]\n" + item.text)
    return "\n".join(parts)


class SourceReader:
    def __init__(self, adapter, question, limits):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("invalid_query")
        if not isinstance(limits, SourceReadLimits):
            raise ValueError("invalid_source_read_limits")
        self.adapter, self.question, self.limits = adapter, question, limits
        self.searches = self.reads = self.node_reads = 0
        self.evidence = ()
        self.known = {}
        self.trace = []
        self._search_cache = {}

    def context(self):
        return SourceMemoryContext(self.question, self.evidence,
                                   render_ranges(self.evidence), truncated=True)

    def remaining(self):
        return {"searches": self.limits.max_searches-self.searches,
                "reads": self.limits.max_reads-self.reads,
                "node_reads": self.limits.max_node_reads-self.node_reads,
                "rendered_chars": self.limits.max_chars-len(self.context().rendered_text)}

    def _admit(self, item):
        merged = merge_ranges((*self.evidence, item))
        if len(render_ranges(merged)) <= self.limits.max_chars:
            self.evidence = merged
            return item
        low, high, accepted = 1, len(item.text), None
        while low <= high:
            size = (low + high)//2
            cut = replace(item, text=item.text[:size], source_end=item.source_start+size,
                evidence_id=_hash([item.provenance.ingestion_version, item.provenance.segment_id,
                                  item.provenance.turn_id, item.source_start, item.source_start+size]))
            if len(render_ranges(merge_ranges((*self.evidence, cut)))) <= self.limits.max_chars:
                accepted, low = cut, size+1
            else:
                high = size-1
        if accepted is not None:
            self.evidence = merge_ranges((*self.evidence, accepted))
        return accepted

    def _result(self, operation, excerpts=(), *, error=None, details=None):
        result = {"operation": operation, "sources": [], "remaining": self.remaining()}
        if error:
            result["error"] = error
        if details:
            result.update(details)
        for item in excerpts:
            p = item.provenance
            result["sources"].append({"segment_id": p.segment_id,
                "session": p.conversation_id, "turn": item.turn_index,
                "role": "USER" if p.source_role == "user" else "LUMINA",
                "spoken_at": p.source_timestamp, "timezone": p.source_timezone,
                "range": [item.source_start, item.source_end, item.turn_length], "text": item.text})
        return result

    def search(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            return self._result("search", error="invalid_query")
        query = query.strip()
        if query in self._search_cache:
            result = deepcopy(self._search_cache[query])
            result.update(cached=True, remaining=self.remaining())
            self.trace.append({"operation": "search", "query": query, "cached": True})
            return result
        self.searches += 1
        if self.searches > self.limits.max_searches:
            self.searches = self.limits.max_searches
            return self._result("search", error="search_budget_exhausted")
        allocation = min(20, self.remaining()["node_reads"]//2)
        if allocation < 1:
            return self._result("search", error="node_budget_exhausted")
        reserved = 2*allocation
        self.node_reads += reserved  # Failed operations retain a conservative charge.
        record = {"operation": "search", "query": query, "reserved_node_reads": reserved}
        try:
            policy = RecallPolicy(top_k=min(self.limits.search_candidates, allocation),
                                  max_nodes=allocation, max_chars=self.limits.max_chars)
            candidates = tuple(self.adapter.backend.source_candidates(query, policy))
            stats = deepcopy(self.adapter.backend.last_source_stats)
            actual = stats["dense_node_reads"] + stats["lexical_node_reads"]
            if not 0 <= actual <= reserved:
                raise ValueError("source_search_accounting_invalid")
            self.node_reads -= reserved-actual
            record.update(backend_stats=stats, candidates=candidates, node_reads=actual)
            self.known.update((c.metadata["evidence_id"], c) for c in candidates)
            if candidates:
                scorer = self.adapter._get_bge_reranker()
                if scorer is None:
                    raise ValueError("source_reranker_unavailable")
                texts = tuple(render_source(_excerpt(c)) for c in candidates)
                if not all(scorer.fits_pair(query, text) for text in texts):
                    raise ValueError("source_reranker_window_exceeded")
                raw = tuple(scorer.score(query, texts))
                from recall.hindsight_scoring import score_hindsight_post_rerank
                from .magma_adapter import _candidate_snapshot_reference_time
                times = tuple(c.metadata["provenance"]["source_timestamp"] for c in candidates)
                scores = score_hindsight_post_rerank(raw, times, now=_candidate_snapshot_reference_time(times))
                order = sorted(range(len(candidates)), key=lambda i: (-scores[i].final_score, i))
                record.update(scoring_texts=texts, raw_scores=raw, ranked_indices=order)
            else:
                order = []
            shown = []
            for i in order[:self.limits.search_results]:
                item = _excerpt(candidates[i])
                until = min(item.source_end, item.source_start+self.limits.snippet_chars)
                item = replace(item, text=item.text[:until-item.source_start], source_end=until,
                    evidence_id=_hash([item.provenance.ingestion_version, item.provenance.segment_id,
                                      item.provenance.turn_id, item.source_start, until]))
                admitted = self._admit(item)
                if admitted is not None:
                    shown.append(admitted)
            result = self._result("search", shown, details={"query": query,
                                  "candidates_available": len(candidates), "cached": False,
                                  "segment_turn_counts": stats.get("segment_turn_counts", {})})
            self._search_cache[query] = deepcopy(result)
        except Exception:
            result = self._result("search", error="source_search_unavailable")
        record["result"] = deepcopy(result); self.trace.append(record)
        return result

    def read(self, segment_id, start_turn, end_turn, *, start_char=0, end_char=None):
        self.reads += 1
        if self.reads > self.limits.max_reads:
            self.reads = self.limits.max_reads
            return self._result("read", error="read_budget_exhausted")
        record = {"operation": "read", "segment_id": segment_id, "start_turn": start_turn,
                  "end_turn": end_turn, "start_char": start_char, "end_char": end_char}
        reserved = self.remaining()["node_reads"]
        self.node_reads += reserved
        try:
            items = self.adapter.backend.source_read_range(segment_id, start_turn, end_turn,
                start_char=start_char, end_char=end_char, limit=reserved,
                max_chars=self.limits.read_chars, known_candidates=self.known)
            stats = deepcopy(self.adapter.backend.last_source_stats["last_range"])
            actual = stats["new_node_reads"]
            if not 0 <= actual <= reserved:
                raise ValueError("source_read_accounting_invalid")
            self.node_reads -= reserved-actual
            record.update(backend_stats=stats, node_reads=actual)
            shown = []
            next_range = stats["next_range"]
            for candidate in items:
                item = _excerpt(candidate)
                admitted = self._admit(item)
                if admitted is not None:
                    shown.append(admitted)
                if admitted is None or admitted.source_end < item.source_end:
                    next_range = {"segment_id": segment_id, "start_turn": item.turn_index,
                                  "end_turn": end_turn, "start_char": item.source_start if admitted is None else admitted.source_end,
                                  "end_char": end_char}
                    break
            result = self._result("read", shown, details={"segment_turn_count": stats["segment_turn_count"],
                "indexed_turn_count": stats["indexed_turn_count"],
                "indexed_turn_extent": stats["indexed_turn_extent"],
                "requested_complete": stats["requested_complete"] and next_range is None,
                "next_range": next_range})
        except SourceRangePreflightError:
            self.node_reads -= reserved
            record["node_reads"] = 0
            result = self._result("read", error="source_range_unavailable")
        except Exception:
            result = self._result("read", error="source_range_unavailable")
        record["result"] = deepcopy(result); self.trace.append(record)
        return result
