"""Write-side generic EntityRef slice (production migration slice 2).

A validated GroundedMemoryUnit whose subject is the current user persists
generic ``subject_entity_ref="E_001"`` metadata, a graph-only EntityNode, and
a single ``Event --ENTITY/REFERS_TO(role=subject)--> EntityNode`` edge. The
write is idempotent across retry and restart, the EntityNode never enters the
VectorDB or the temporal chain (slice 1 boundary), and unbound third-party
units create no entity structure. Shadow evidence:
``docs/experiments/entity_composite_e2e/`` and
``docs/experiments/context_role_entityref/``.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adapter.backend import RealMagmaBackend
from adapter.grounded_formation import FORMATION_VERSION
from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import ColdDraftSegment, ColdDraftTurn
from adapter.user_self import CURRENT_USER_ENTITY_REF
from ingestion.state_store import IngestionStateStore

_REAL_MAGMA_VENV = (
    Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
)

pytestmark = pytest.mark.skipif(
    Path(sys.executable).resolve() != _REAL_MAGMA_VENV.resolve(),
    reason="real MAGMA test runs in the isolated Conversation Memory environment",
)

_ENTITY_NODE_ID = "entity:e_001"


class _FakeFormationModel:
    def __init__(self, units):
        self.units = units

    def generate(self, recent_context, user_message, *, system_prompt):
        assert recent_context == []
        return json.dumps({"units": self.units}, ensure_ascii=False)


def _segment(segment_id, *turns):
    return ColdDraftSegment(
        segment_id=segment_id,
        conversation_id=f"{segment_id}-conversation",
        state="pending_digest",
        turns=tuple(
            ColdDraftTurn(
                turn_id=turn_id,
                role=role,
                content=text,
                timestamp=datetime(2026, 8, 12, index, tzinfo=UTC),
                source_timezone="Asia/Shanghai",
                timezone_source="client",
            )
            for index, (turn_id, role, text) in enumerate(turns)
        ),
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        source_timezone="Asia/Shanghai",
        schema_version="2",
    )


def _unit_payload(text, subject, relation, value, refs):
    return {
        "text": text,
        "subject": subject,
        "relation": relation,
        "value": value,
        "source_refs": [
            {"turn_id": turn_id, "supporting_span": span}
            for turn_id, span in refs
        ],
        "referenced_time": None,
    }


def _ingest(tmp_path, segment, units, dirname="magma"):
    model = _FakeFormationModel(units)
    backend = RealMagmaBackend(tmp_path / dirname)
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / f"{dirname}-state.json"),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    )
    result = adapter.ingest(segment)
    assert result.status == "completed"
    return backend, result


def _entity_nodes(backend):
    from memory.graph_db import NodeType

    return [
        node
        for node in backend.trg.graph_db.nodes.values()
        if node.node_type == NodeType.ENTITY
    ]


def _refers_to_links(backend):
    from memory.graph_db import LinkSubType, LinkType

    return [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.ENTITY
        and link.properties.get("sub_type") == LinkSubType.REFERS_TO.value
    ]


def test_current_user_unit_persists_e001_node_and_single_refers_to_edge(tmp_path):
    introduction = "我叫林岚，来自东海大学物理学院，现在大二"
    backend, result = _ingest(
        tmp_path,
        _segment("entity-segment-1", ("u1", "user", introduction)),
        [_unit_payload(
            "林岚 来自东海大学物理学院", "林岚", "来自", "东海大学物理学院",
            [("u1", "我叫林岚，来自东海大学物理学院")],
        )],
    )
    memory_id = result.memory_ids[0]

    event = backend.trg.graph_db.get_node(memory_id)
    assert event.attributes["subject_entity_ref"] == CURRENT_USER_ENTITY_REF
    assert CURRENT_USER_ENTITY_REF == "E_001"

    entity_nodes = _entity_nodes(backend)
    assert [node.node_id for node in entity_nodes] == [_ENTITY_NODE_ID]
    assert entity_nodes[0].attributes.get("entity_ref") == CURRENT_USER_ENTITY_REF
    assert not entity_nodes[0].embedding_vector
    # graph-only: the EntityNode is never vector-indexed
    assert _ENTITY_NODE_ID not in backend.trg.vector_db.id_to_index

    refers_to = _refers_to_links(backend)
    assert [
        (link.source_node_id, link.target_node_id, link.properties.get("role"))
        for link in refers_to
    ] == [(memory_id, _ENTITY_NODE_ID, "subject")]
    # single canonical edge only: no reverse MENTIONED_IN edge
    from memory.graph_db import LinkSubType, LinkType

    assert not [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.ENTITY
        and link.properties.get("sub_type") == LinkSubType.MENTIONED_IN.value
    ]


def test_entity_write_idempotent_across_retry_and_reload(tmp_path):
    introduction = "我叫林岚，来自东海大学物理学院，现在大二"
    backend, result = _ingest(
        tmp_path,
        _segment("entity-segment-1", ("u1", "user", introduction)),
        [_unit_payload(
            "林岚 来自东海大学物理学院", "林岚", "来自", "东海大学物理学院",
            [("u1", "我叫林岚，来自东海大学物理学院")],
        )],
    )
    # simulate ingestion retry: relationship step runs again on the same ids
    backend.create_relationships(list(result.memory_ids))
    backend.persist()

    reloaded = RealMagmaBackend(tmp_path / "magma")
    entity_nodes = _entity_nodes(reloaded)
    assert [node.node_id for node in entity_nodes] == [_ENTITY_NODE_ID]
    assert len(_refers_to_links(reloaded)) == 1
    event = reloaded.trg.graph_db.get_node(result.memory_ids[0])
    assert event.attributes["subject_entity_ref"] == CURRENT_USER_ENTITY_REF


def test_second_current_user_event_reuses_node_and_temporal_chain_stays_pure(
    tmp_path,
):
    backend, first = _ingest(
        tmp_path,
        _segment("entity-segment-1", ("u1", "user", "我叫林岚。")),
        [_unit_payload("我叫林岚。", "我", "叫", "林岚",
                       [("u1", "我叫林岚")])],
    )
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "magma-state.json"),
        ingestion_version=FORMATION_VERSION,
        formation_model=_FakeFormationModel([_unit_payload(
            "我现在大三。", "我", "现在", "大三",
            [("u1", "我现在大三")],
        )]),
    )
    second = adapter.ingest(
        _segment("entity-segment-2", ("u1", "user", "我现在大三。")),
    )
    assert second.status == "completed"

    # both current-user events share one EntityNode
    assert [node.node_id for node in _entity_nodes(backend)] == [_ENTITY_NODE_ID]
    refers_to = _refers_to_links(backend)
    assert sorted(link.source_node_id for link in refers_to) == sorted(
        [first.memory_ids[0], second.memory_ids[0]],
    )
    assert all(link.target_node_id == _ENTITY_NODE_ID for link in refers_to)

    # slice 1 boundary: the entity node (created between the two events, with
    # a current timestamp) never enters the temporal chain
    from memory.graph_db import LinkType

    temporal_links = [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.TEMPORAL
    ]
    assert all(
        _ENTITY_NODE_ID not in (link.source_node_id, link.target_node_id)
        for link in temporal_links
    )
    assert any(
        link.source_node_id == first.memory_ids[0]
        and link.target_node_id == second.memory_ids[0]
        for link in temporal_links
    )


def test_third_party_unit_creates_no_entity_node(tmp_path):
    backend, result = _ingest(
        tmp_path,
        _segment("entity-segment-3", ("u1", "user", "我朋友小林开了家公司。")),
        [_unit_payload("小林开了家公司。", "小林", "开了", "家公司",
                       [("u1", "小林开了家公司。")])],
    )
    assert result.memory_ids
    event = backend.trg.graph_db.get_node(result.memory_ids[0])
    assert event.attributes["subject_entity_ref"] is None
    assert _entity_nodes(backend) == []
    assert _refers_to_links(backend) == []
