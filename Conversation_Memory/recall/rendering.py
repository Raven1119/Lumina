from __future__ import annotations

from adapter.models import MemoryEvidence


def bound_evidence(items: list[MemoryEvidence], *, count: int, max_chars: int) -> tuple[tuple[MemoryEvidence, ...], str, bool]:
    selected: list[MemoryEvidence] = []
    parts: list[str] = []
    used = 0
    truncated = len(items) > count
    for item in items[:count]:
        prefix = "\n" if parts else ""
        available = max_chars - used - len(prefix)
        if available <= 0:
            truncated = True
            break
        text = item.text
        if len(text) > available:
            text = text[:available]
            truncated = True
        selected.append(
            MemoryEvidence(
                item.evidence_id,
                text,
                item.timestamp,
                item.provenance,
                item.relevance_score,
            )
        )
        parts.append(text)
        used += len(prefix) + len(text)
        if len(text) < len(item.text):
            break
    return tuple(selected), "\n".join(parts), truncated
