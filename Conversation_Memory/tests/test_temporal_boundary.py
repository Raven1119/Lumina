"""Regression: temporal linking consumes EVENT nodes only.

Shadow evidence: docs/experiments/entity_temporal_boundary/RESULT.md — the
pinned upstream ``_create_temporal_links`` sorts every graph node by
timestamp, so a non-temporal entity node whose timestamp lands between two
events steals the new event's temporal predecessor. The Lumina-owned backend
override keeps the temporal chain pure while leaving current all-EVENT
production behavior byte-identical.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from Conversation_Memory.adapter.backend import RealMagmaBackend

_REAL_MAGMA_VENV = (
    Path(__file__).resolve().parents[1] / ".venv"
)


@pytest.mark.skipif(
    Path(sys.prefix).resolve() != _REAL_MAGMA_VENV.resolve(),
    reason="real MAGMA test runs in the isolated Conversation Memory environment",
)
def test_entity_node_does_not_pollute_event_temporal_chain(tmp_path):
    backend = RealMagmaBackend(tmp_path / "magma")
    t_first = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
    t_entity = t_first + timedelta(hours=1)
    t_second = t_first + timedelta(hours=2)
    first_id = backend.add_event(
        "First event.", timestamp=t_first, metadata={"evidence_id": "ev-1"},
    )

    from memory.graph_db import EventNode, LinkType, NodeType

    entity_node = EventNode(
        node_id="entity:test",
        node_type=NodeType.ENTITY,
        timestamp=t_entity,
        content_narrative="",
        attributes={"entity_ref": "E_TEST"},
        embedding_vector=None,
    )
    backend.trg.graph_db.add_node(entity_node)

    second_id = backend.add_event(
        "Second event.", timestamp=t_second, metadata={"evidence_id": "ev-2"},
    )

    temporal_links = [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.TEMPORAL
    ]
    # Only the two event-chain edges (PRECEDES + SUCCEEDS) may exist; the
    # non-temporal entity node never participates.
    assert len(temporal_links) == 2
    assert all(
        "entity:test" not in (link.source_node_id, link.target_node_id)
        for link in temporal_links
    )
    # The second event's temporal predecessor is the first EVENT, not the
    # chronologically adjacent entity node.
    assert any(
        link.source_node_id == first_id and link.target_node_id == second_id
        for link in temporal_links
    )
    assert any(
        link.source_node_id == second_id and link.target_node_id == first_id
        for link in temporal_links
    )


@pytest.mark.skipif(
    Path(sys.prefix).resolve() != _REAL_MAGMA_VENV.resolve(),
    reason="real MAGMA test runs in the isolated Conversation Memory environment",
)
def test_plain_event_temporal_chain_unchanged(tmp_path):
    """Without non-EVENT nodes the chain matches upstream behavior exactly."""
    backend = RealMagmaBackend(tmp_path / "magma")
    t_first = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
    first_id = backend.add_event(
        "First event.", timestamp=t_first, metadata={"evidence_id": "ev-1"},
    )
    second_id = backend.add_event(
        "Second event.",
        timestamp=t_first + timedelta(hours=2),
        metadata={"evidence_id": "ev-2"},
    )

    from memory.graph_db import LinkSubType, LinkType

    temporal_links = [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.TEMPORAL
    ]
    assert len(temporal_links) == 2
    precedes = [
        link
        for link in temporal_links
        if link.properties.get("sub_type") == LinkSubType.PRECEDES.value
    ]
    succeeds = [
        link
        for link in temporal_links
        if link.properties.get("sub_type") == LinkSubType.SUCCEEDS.value
    ]
    assert [(link.source_node_id, link.target_node_id) for link in precedes] == [
        (first_id, second_id),
    ]
    assert [(link.source_node_id, link.target_node_id) for link in succeeds] == [
        (second_id, first_id),
    ]
