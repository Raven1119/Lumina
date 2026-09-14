"""Source-faithful views over the existing source read; no new retrieval policy."""
from __future__ import annotations

from copy import deepcopy

from .models import ExperienceContext, SourceExperience, SourceRangeReference
from .source_memory import render_source
from .source_reader import merge_ranges


def _views(evidence, segment_counts):
    """Join only observed adjacent ranges; never infer an episode or identity."""
    groups = []
    for item in merge_ranges(evidence):
        if groups:
            previous = groups[-1][-1]
            same_segment = item.provenance.segment_id == previous.provenance.segment_id
            next_turn = (item.turn_index == previous.turn_index + 1
                         and previous.source_end == previous.turn_length
                         and item.source_start == 0)
            if same_segment and next_turn:
                groups[-1].append(item)
                continue
        groups.append([item])
    views = []
    for group in groups:
        first, last = group[0], group[-1]
        sid = first.provenance.segment_id
        count = segment_counts.get(sid)
        reference = (SourceRangeReference(sid, 0, count - 1) if count is not None else
                     SourceRangeReference(sid, first.turn_index, last.turn_index,
                                          first.source_start, last.source_end))
        complete = (count is not None and first.turn_index == 0
                    and last.turn_index == count - 1
                    and all(e.source_start == 0 and e.source_end == e.turn_length for e in group))
        views.append(SourceExperience(reference, tuple(group),
                     "\n".join(render_source(e) for e in group), not complete))
    return tuple(views)


def recall_experiences(adapter, cue, policy):
    """Reuse A's search, scoring and selected evidence; organize literal output only.

    No extra search, graph projection, tokenizer check or model call is added.
    Original source labels remain visible, so the view renders within the same
    budget as the original selection. Expansion is a separate explicit read.
    """
    adapter.last_experience_read = {}
    source = adapter.recall_sources(cue, policy)
    if source.safe_error_code or not source.evidence:
        return ExperienceContext(source.query, truncated=source.truncated,
                                 safe_error_code=source.safe_error_code)
    try:
        counts = getattr(adapter.backend, "last_source_stats", {}).get("segment_turn_counts", {})
        views = _views(source.evidence, counts)
        text = "\n".join(view.rendered_text for view in views)
        if len(text) > len(source.rendered_text) or len(text) > policy.max_chars:
            raise ValueError("source_view_budget_exceeded")
        adapter.last_experience_read = {
            "generated_calls": 0, "source_selection": "recall_sources",
            "source_read": deepcopy(adapter.last_source_read),
            "selected_source_chars": sum(len(e.text) for v in views for e in v.evidence),
            "rendered_chars": len(text)}
        return ExperienceContext(source.query, views, text,
                                 source.truncated or any(v.truncated for v in views))
    except Exception:
        return ExperienceContext(source.query, safe_error_code="experience_view_unavailable")
