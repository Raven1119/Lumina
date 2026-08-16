"""Entity-conditioned retrieval slice (production migration slice 3).

When a recall query resolves a ``target_entity_ref``, the backend adds the
entity's own ``REFERS_TO(role=subject)`` events as a FAISS ``IDSelectorBatch``
subset semantic ranking — a third RRF list alongside the unchanged global
dense and lexical lists. The subset only adds candidates; BGE / Hindsight /
``final_min_score=0.144`` admission are unchanged. With no target ref the
behavior is byte-identical to the pre-slice path. Shadow evidence:
``docs/experiments/entity_conditioned_retrieval/``,
``docs/experiments/entity_composite_e2e/``.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adapter.backend import RealMagmaBackend
from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import RecallPolicy
from ingestion.state_store import IngestionStateStore

_REAL_MAGMA_VENV = (
    Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
)

pytestmark = pytest.mark.skipif(
    Path(sys.executable).resolve() != _REAL_MAGMA_VENV.resolve(),
    reason="real MAGMA test runs in the isolated Conversation Memory environment",
)

_POLICY = RecallPolicy(
    top_k=10,
    max_chars=5000,
    max_evidence_items=3,
    max_graph_depth=1,
    max_nodes=20,
    final_min_score=0.144,
)

_BASE_TS = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)


def _add_memory(backend, evidence_id, text, hours, ref=None):
    metadata = {
        "evidence_id": evidence_id,
        "subject_entity_ref": ref,
        "provenance": {
            "segment_id": f"segment-{evidence_id}",
            "conversation_id": "entity-recall-conversation",
            "turn_id": f"turn-{evidence_id}",
            "source_role": "user",
            "source_timestamp": (_BASE_TS + timedelta(hours=hours)).isoformat(),
            "source_timezone": "Asia/Shanghai",
            "timezone_source": "client",
            "ingestion_version": "grounded-formation-v1",
        },
    }
    return backend.add_event(
        text,
        timestamp=_BASE_TS + timedelta(hours=hours),
        metadata=metadata,
    )


def _build_corpus(tmp_path):
    """Crowded corpus: 16 distractor events crowd the global top-k so the
    correct user facts only return via the entity-conditioned channel."""
    backend = RealMagmaBackend(tmp_path / "magma")
    ids = []
    ids.append(_add_memory(backend, "ev-u1", "我叫林岚。", 0, ref="E_001"))
    ids.append(_add_memory(backend, "ev-u2", "我在东海大学物理学院读书。", 1, ref="E_001"))
    ids.append(_add_memory(backend, "ev-u3", "我现在大三。", 2, ref="E_001"))
    ids.append(_add_memory(backend, "ev-x1", "小林开了一家咖啡店。", 3, ref="E_XIAOLIN"))
    people = ["王芳", "李明", "张伟", "刘洋", "陈静", "杨帆", "赵磊", "孙丽"]
    hour = 4
    for index, name in enumerate(people):
        for copy in range(2):
            ids.append(_add_memory(
                backend, f"ev-d{index}{copy}", f"{name}问我是谁。", hour,
                ref=f"E_D{index}",
            ))
            hour += 1
    backend.create_relationships(ids)
    backend.persist()
    return backend


def _adapter(backend, tmp_path):
    return MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
    )


def _evidence_texts(context):
    return [item.text for item in context.evidence]


def _assert_no_internal_leak(context):
    for token in ("[SAME_ENTITY]", "E_001", "E_XIAOLIN", "entity:", "EntityRef"):
        assert token not in context.rendered_text
    assert len(context.evidence) <= 3
    assert len(context.rendered_text) <= 5000


def test_identity_query_recovers_crowded_user_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_corpus(tmp_path)
    context = _adapter(backend, tmp_path).recall("我是谁？", _POLICY)
    assert "我叫林岚。" in _evidence_texts(context)
    _assert_no_internal_leak(context)


def test_school_query_uses_entity_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_corpus(tmp_path)
    context = _adapter(backend, tmp_path).recall("我的学校是什么？", _POLICY)
    # the entity channel surfaces the user's own events as candidates;
    # unchanged BGE/admission decides the final evidence
    assert _evidence_texts(context)
    assert "小林开了一家咖啡店。" not in _evidence_texts(context)
    _assert_no_internal_leak(context)


def test_absent_bank_card_stays_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_corpus(tmp_path)
    context = _adapter(backend, tmp_path).recall("我的银行卡号是多少？", _POLICY)
    assert context.evidence == ()
    _assert_no_internal_leak(context)


def test_third_party_query_has_no_user_entity_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_corpus(tmp_path)
    context = _adapter(backend, tmp_path).recall("小林是谁？", _POLICY)
    assert _evidence_texts(context) == ["小林开了一家咖啡店。"]
    _assert_no_internal_leak(context)


def test_entity_domain_isolation(tmp_path, monkeypatch):
    """The E_001 subset channel must only carry E_001's own events."""
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_corpus(tmp_path)
    # the entity channel reads exactly the EntityNode's REFERS_TO(role=subject)
    # adjacency — verify it contains only the three user events
    from memory.graph_db import LinkSubType, LinkType

    e001_events = {
        link.source_node_id
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.ENTITY
        and link.target_node_id == "entity:e_001"
        and link.properties.get("sub_type") == LinkSubType.REFERS_TO.value
        and link.properties.get("role") == "subject"
    }
    assert len(e001_events) == 3
    texts = {
        backend.trg.graph_db.get_node(node_id).content_narrative
        for node_id in e001_events
    }
    assert "小林开了一家咖啡店。" not in texts
    assert all("问我是谁" not in text for text in texts)
    # and the user query's evidence never contains another entity's facts
    context = _adapter(backend, tmp_path).recall("我是谁？", _POLICY)
    assert "小林开了一家咖啡店。" not in _evidence_texts(context)


def test_non_entity_query_byte_identical_to_plain_path(tmp_path, monkeypatch):
    """Without a resolved target ref the third list never engages."""
    backend = _build_corpus(tmp_path)
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "false")
    disabled = _adapter(backend, tmp_path).recall("小林是谁？", _POLICY)
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    enabled = _adapter(backend, tmp_path).recall("小林是谁？", _POLICY)
    assert [item.evidence_id for item in disabled.evidence] == [
        item.evidence_id for item in enabled.evidence
    ]
    assert disabled.rendered_text == enabled.rendered_text


def test_entity_channel_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    _build_corpus(tmp_path)
    reloaded = RealMagmaBackend(tmp_path / "magma")
    context = _adapter(reloaded, tmp_path).recall("我是谁？", _POLICY)
    assert "我叫林岚。" in _evidence_texts(context)
    _assert_no_internal_leak(context)
