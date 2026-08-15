from __future__ import annotations

from adapter.models import MemoryEvidence


# Deterministic separation of retrieval from context serialization follows the
# bounded-set-first shape of upstream MAGMA's TRGMemory.query (MIT, Copyright
# (c) 2024 Anonymous Authors). MAGMA's
# narrative synthesis, causal rendering, prompts, and benchmark formatting are
# deliberately not used here.
def _speaker_label(item: MemoryEvidence) -> str:
    if item.provenance.source_role == "user":
        return "USER"
    if item.provenance.source_role == "assistant":
        return "LUMINA"
    raise ValueError("unsupported evidence source role")


def _render_header(item: MemoryEvidence) -> str:
    return f"[{_speaker_label(item)}]"


def bound_evidence(
    items: list[MemoryEvidence],
    *,
    count: int,
    max_chars: int,
) -> tuple[tuple[MemoryEvidence, ...], str, bool]:
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
        header = _render_header(item)
        if available <= len(header) + 1:
            truncated = True
            break
        full_line = f"{header}\n{item.text}"
        line = full_line
        if len(line) > available:
            line = line[:available]
            truncated = True
        visible_text_chars = max(0, len(line) - len(header) - 1)
        text = item.text[:visible_text_chars]
        selected.append(
            MemoryEvidence(
                item.evidence_id,
                text,
                item.timestamp,
                item.provenance,
            )
        )
        parts.append(line)
        used += len(prefix) + len(line)
        if len(line) < len(full_line):
            break
    return tuple(selected), "\n".join(parts), truncated
