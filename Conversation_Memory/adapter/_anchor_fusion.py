"""Private dense/lexical anchor fusion for Lumina Recall.

Algorithm source:

- ``upstream/MAGMA/memory/query_engine.py::QueryEngine._keyword_search``
- ``upstream/MAGMA/memory/query_engine.py::QueryEngine._rrf_fusion``
- ``upstream/MAGMA/memory/memory_builder.py::MemoryBuilder._index_text_basic``

This module preserves the upstream lowercase/split tokenization, stop-word
list, exact and length-four partial matching, bigrams, lexical weights
``+5/+1/+3``, top-40 lexical cutoff, and rank-one RRF with ``k=60``. It
replaces the upstream persistent keyword index with a deterministic,
``max_nodes``-bounded graph scan because Lumina does not build that index.
Dataset/session routing, full-scan fallback, query classification, reranking,
answer formatting, debug output, and narrative generation are excluded.

Upstream MAGMA is distributed under the MIT License:
Copyright (c) 2024 Anonymous Authors.
"""

from __future__ import annotations

from datetime import datetime
from itertools import islice
from typing import Any, Iterable, Sequence

_STOP_WORDS = {
    "the",
    "a",
    "an",
    "is",
    "was",
    "are",
    "were",
    "what",
    "when",
    "where",
    "who",
    "how",
    "did",
    "does",
    "do",
}
_RRF_K = 60
_LEXICAL_LIMIT = 40
_PROVENANCE_FIELDS = (
    "segment_id",
    "conversation_id",
    "turn_id",
    "source_role",
    "source_timestamp",
    "source_timezone",
    "ingestion_version",
)
_TIMEZONE_SOURCES = {
    "client",
    "configured_default",
    "legacy_segment_fallback",
}


def _is_projectable_event(
    node: Any,
    *,
    event_node_type: type[Any],
    node_type: Any,
) -> bool:
    """Return whether a MAGMA node can safely become Lumina evidence."""
    try:
        if (
            not isinstance(node, event_node_type)
            or getattr(node, "node_type", None) != node_type.EVENT
        ):
            return False
        node_id = getattr(node, "node_id", None)
        text = getattr(node, "content_narrative", None)
        timestamp = getattr(node, "timestamp", None)
        metadata = getattr(node, "attributes", None)
        if (
            not isinstance(node_id, str)
            or not node_id.strip()
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
            or not isinstance(metadata, dict)
        ):
            return False

        evidence_id = metadata.get("evidence_id")
        provenance = metadata.get("provenance")
        if (
            not isinstance(evidence_id, str)
            or not evidence_id.strip()
            or not isinstance(provenance, dict)
            or not all(
                isinstance(provenance.get(field), str)
                and bool(provenance[field].strip())
                for field in _PROVENANCE_FIELDS
            )
            or provenance.get("source_role") not in {"user", "assistant"}
            or provenance.get(
                "timezone_source",
                "legacy_segment_fallback",
            )
            not in _TIMEZONE_SOURCES
        ):
            return False

        source_timestamp = datetime.fromisoformat(
            provenance["source_timestamp"].strip()
        )
        return (
            source_timestamp.tzinfo is not None
            and source_timestamp.utcoffset() is not None
        )
    except Exception:
        return False


def _text_index_keys(text: str) -> tuple[str, ...]:
    """Build the word/bigram keys used by MAGMA's basic keyword index."""
    words = text.lower().split()
    keys: dict[str, None] = {}
    for word in words:
        clean_word = word.strip('.,!?;:"')
        if len(clean_word) >= 2:
            keys.setdefault(clean_word, None)
    for index in range(len(words) - 1):
        bigram = f"{words[index]} {words[index + 1]}".strip('.,!?;:"')
        keys.setdefault(bigram, None)
    return tuple(keys)


def _lexical_score(query: str, text: str) -> int | None:
    """Apply MAGMA's general keyword scoring to one event text."""
    words = query.lower().split()
    content_lower = text.lower()
    index_keys = _text_index_keys(text)
    matched_keywords: list[str] = []

    for word in words:
        if word in _STOP_WORDS:
            continue
        clean_word = word.strip('.,!?;:"\'-')
        if len(clean_word) >= 2 and clean_word in index_keys:
            matched_keywords.append(clean_word)
        if len(clean_word) >= 4:
            for key in index_keys:
                if clean_word in key or key in clean_word:
                    matched_keywords.append(f"{clean_word}~{key}")

    for index in range(len(words) - 1):
        bigram = f"{words[index]} {words[index + 1]}"
        if bigram in index_keys:
            matched_keywords.append(bigram)

    if not matched_keywords:
        return None

    score = len(matched_keywords) * 5
    for word in words:
        if word not in _STOP_WORDS and word in content_lower:
            score += 1
    for index in range(len(words) - 1):
        if f"{words[index]} {words[index + 1]}" in content_lower:
            score += 3
    return score


def _stable_node_key(node: Any) -> tuple[str, str]:
    metadata = getattr(node, "attributes", None)
    evidence_id = metadata.get("evidence_id", "") if isinstance(metadata, dict) else ""
    node_id = getattr(node, "node_id", "")
    return (
        evidence_id if isinstance(evidence_id, str) else "",
        node_id if isinstance(node_id, str) else "",
    )


def _rank_lexical_events(
    *,
    graph_nodes: Iterable[tuple[str, Any]],
    query: str,
    max_nodes: int,
    event_node_type: type[Any],
    node_type: Any,
) -> list[Any]:
    """Return at most 40 ranked events after at most ``max_nodes`` graph reads."""
    ranked: list[tuple[int, tuple[str, str], Any]] = []
    for _stored_node_id, node in islice(graph_nodes, max_nodes):
        try:
            if not _is_projectable_event(
                node,
                event_node_type=event_node_type,
                node_type=node_type,
            ):
                continue
            score = _lexical_score(query, node.content_narrative)
            if score is None:
                continue
            ranked.append((score, _stable_node_key(node), node))
        except Exception:
            continue

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:_LEXICAL_LIMIT]]


def _rrf_fuse(
    ranked_lists: Sequence[Sequence[Any]],
    *,
    limit: int,
    k: int = _RRF_K,
) -> list[tuple[Any, float]]:
    """Fuse ranked node lists using upstream MAGMA's rank-one RRF formula."""
    node_scores: dict[str, tuple[Any, float, tuple[str, str]]] = {}
    for ranked_list in ranked_lists:
        for rank, node in enumerate(ranked_list, start=1):
            try:
                node_id = getattr(node, "node_id", None)
                if not isinstance(node_id, str) or not node_id.strip():
                    continue
                stable_key = _stable_node_key(node)
            except Exception:
                continue
            contribution = 1.0 / (k + rank)
            if node_id in node_scores:
                existing_node, existing_score, existing_key = node_scores[node_id]
                node_scores[node_id] = (
                    existing_node,
                    existing_score + contribution,
                    existing_key,
                )
            else:
                node_scores[node_id] = (node, contribution, stable_key)

    fused = list(node_scores.values())
    fused.sort(key=lambda item: (-item[1], item[2]))
    return [(node, score) for node, score, _key in fused[:limit]]
