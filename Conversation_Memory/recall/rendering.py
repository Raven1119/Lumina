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
    return bound_evidence_groups([[item] for item in items], count=count, max_chars=max_chars)


def bound_evidence_groups(
    groups: list[list[MemoryEvidence]],
    *,
    count: int,
    max_chars: int,
) -> tuple[tuple[MemoryEvidence, ...], str, bool]:
    """Render whole source facts and whole dependency bundles, or omit them.

    A group is supplied by evidence selection, never inferred by rendering.
    Shared bridge facts are rendered once. An over-budget group is skipped so
    a later independent fact can still fit; truncated truthfully records this.
    """
    selected: list[MemoryEvidence] = []
    parts: list[str] = []
    used = 0
    truncated = False
    seen: set[str] = set()
    for group in groups:
        pending: dict[str, MemoryEvidence] = {}
        for item in group:
            if item.evidence_id not in seen:
                pending.setdefault(item.evidence_id, item)
        lines = [f"{_render_header(item)}\n{item.text}" for item in pending.values()]
        extra = sum(map(len, lines)) + max(0, len(lines) - 1) + bool(parts and lines)
        if len(selected) + len(pending) > count or used + extra > max_chars:
            truncated = True
            continue
        selected.extend(pending.values())
        seen.update(pending)
        parts.extend(lines)
        used += extra
    return tuple(selected), "\n".join(parts), truncated
