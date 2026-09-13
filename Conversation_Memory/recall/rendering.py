from __future__ import annotations

from datetime import datetime
import json

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


def _render_header(item: MemoryEvidence, source_context_roles=None) -> str:
    speaker = _speaker_label(item)
    if source_context_roles is None:
        return f"[{speaker}]"
    # This is the source statement time, not a claim about when its fact held.
    spoken_at = item.provenance.source_timestamp
    try:
        parsed = datetime.fromisoformat(spoken_at)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            spoken_at = "unknown"
    except (TypeError, ValueError):
        spoken_at = "unknown"
    timezone = item.provenance.source_timezone
    if not isinstance(timezone, str) or not timezone.strip():
        timezone = "unknown"
    fields = [speaker, f"spoken_at={json.dumps(spoken_at, ensure_ascii=False)}",
              f"timezone={json.dumps(timezone, ensure_ascii=False)}"]
    subject, obj = source_context_roles.get(item.evidence_id, (None, None))
    for role, label in (("subject", subject), ("object", obj)):
        if label is not None:
            fields.append(f"{role}_binding={label}")
    return "[" + " | ".join(fields) + "]"


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
    source_context_roles: dict[str, tuple[str | None, str | None]] | None = None,
    _rendered_blocks: list[str] | None = None,
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
        lines = [f"{_render_header(item, source_context_roles)}\n{item.text}"
                 for item in pending.values()]
        extra = sum(map(len, lines)) + max(0, len(lines) - 1) + bool(parts and lines)
        if len(selected) + len(pending) > count or used + extra > max_chars:
            truncated = True
            continue
        selected.extend(pending.values())
        seen.update(pending)
        parts.extend(lines)
        used += extra
    if _rendered_blocks is not None:
        # Capture exact successful blocks; callers never split/paraphrase text.
        _rendered_blocks.extend(parts)
    return tuple(selected), "\n".join(parts), truncated
