from __future__ import annotations

from datetime import UTC, datetime

from adapter.models import MemoryEvidence


# Deterministic separation of retrieval from context serialization follows the
# bounded-set-first shape of upstream MAGMA's TRGMemory.query and its event
# timestamp serialization (MIT, Copyright (c) 2024 Anonymous Authors).  MAGMA's
# narrative synthesis, causal rendering, prompts, and benchmark formatting are
# deliberately not used here.
def _aware_utc_timestamp(item: MemoryEvidence) -> datetime:
    if not isinstance(item.timestamp, str) or not item.timestamp.strip():
        raise ValueError("evidence timestamp must be an aware RFC 3339 value")
    timestamp = datetime.fromisoformat(item.timestamp.strip())
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("evidence timestamp must be aware")
    return timestamp.astimezone(UTC)


def _render_timestamp(item: MemoryEvidence) -> str:
    return _aware_utc_timestamp(item).isoformat().replace("+00:00", "Z")


def bound_evidence(
    items: list[MemoryEvidence],
    *,
    count: int,
    max_chars: int,
    intent: str | None = None,
) -> tuple[tuple[MemoryEvidence, ...], str, bool]:
    if intent is not None:
        bounded = list(items[:count])
        if intent == "WHEN":
            bounded.sort(
                key=lambda item: (_aware_utc_timestamp(item), item.evidence_id)
            )

        selected: list[MemoryEvidence] = []
        parts: list[str] = []
        used = 0
        truncated = len(items) > count
        for item in bounded:
            prefix = "\n" if parts else ""
            available = max_chars - used - len(prefix)
            if available <= 0:
                truncated = True
                break
            full_line = f"[{_render_timestamp(item)}] {item.text}"
            line = full_line
            if len(line) > available:
                line = line[:available]
                truncated = True
            selected.append(item)
            parts.append(line)
            used += len(prefix) + len(line)
            if len(line) < len(full_line):
                break
        return tuple(selected), "\n".join(parts), truncated

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
            )
        )
        parts.append(text)
        used += len(prefix) + len(text)
        if len(text) < len(item.text):
            break
    return tuple(selected), "\n".join(parts), truncated
