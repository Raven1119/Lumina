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


def _add_memory(backend, evidence_id, text, hours, ref=None, mention_refs=None,
                mention_surfaces=None, surface=None):
    metadata = {
        "evidence_id": evidence_id,
        "subject_entity_ref": ref,
        "subject_entity_surface": surface,
        "mention_entity_refs": list(mention_refs or ()),
        "mention_entity_surfaces": list(mention_surfaces or ()),
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


# --- Production Slice 2: the entity subset channel consumes ALL ENTITY /
# REFERS_TO edges (subject + generic mention edges).
# Shadow evidence: docs/experiments/multi_entity_recall_gain/RESULT_CROWDED.md.

def _build_mention_corpus(tmp_path):
    """Crowded corpus: the 导师 fact ev1 is squeezed out of the global fused
    top-k, so only the generic mention edge ``ev1 -> entity:e_wang`` can
    recover it for a 王老师-targeted query. Mirrors the crowded shadow corpus:
    ev1 first, 32 semantic distractors in the middle, the rest last."""
    backend = RealMagmaBackend(tmp_path / "magma")
    ids = []
    ids.append(_add_memory(
        backend, "ev1", "小林的导师是王老师。", 0, ref="E_XIAOLIN",
        surface="小林",
        mention_refs=["E_XIAOLIN", "E_WANG"],
        mention_surfaces=["小林", "王老师"],
    ))
    people = ["周明", "郑浩", "吴倩", "何勇", "高飞", "林涛", "罗欣", "梁雪"]
    flavors = [
        "的导师是李教授。", "的学生是张倩。", "推荐同事加入项目组。", "在东南大学任教。",
    ]
    hour = 1
    index = 0
    for name in people:
        for flavor in flavors:
            ids.append(_add_memory(
                backend, f"ev-d{index}", f"{name}{flavor}", hour, ref=f"E_D{index}",
                surface=name,
            ))
            hour += 1
            index += 1
    # ev2's subject is 小林 (surface present in text), NOT 王老师: without the
    # generic mention edge, no subject REFERS_TO edge points at entity:e_wang,
    # so the E_WANG subset channel stays empty and the recovery test is truly
    # attributable to the mention edge.
    ids.append(_add_memory(
        backend, "ev2", "王老师推荐小林加入项目。", hour, ref="E_XIAOLIN",
        surface="小林",
        mention_refs=["E_WANG", "E_XIAOLIN"],
        mention_surfaces=["王老师", "小林"],
    ))
    ids.append(_add_memory(
        backend, "ev3", "小林今年23岁。", hour + 1, ref="E_XIAOLIN",
        surface="小林",
        mention_refs=["E_XIAOLIN"], mention_surfaces=["小林"],
    ))
    ids.append(_add_memory(
        backend, "ev-linsu", "林素在南京大学读书。", hour + 2, ref="E_LINSU",
        surface="林素",
        mention_refs=["E_LINSU"], mention_surfaces=["林素"],
    ))
    ids.append(_add_memory(
        backend, "ev-user", "我叫林岚。", hour + 3, ref="E_001",
        surface="林岚",
        mention_refs=["E_001"], mention_surfaces=["林岚"],
    ))
    backend.create_relationships(ids)
    backend.persist()
    return backend


def test_subject_only_corpus_preserves_necessary_user_facts_and_provenance(
    tmp_path, monkeypatch,
):
    """Required user facts survive bounded traversal and its candidate changes.

    The old paths[:10] projection also pinned one irrelevant question-mention
    distractor. Removing that truncation changes ev-d21 to ev-d71; this is not
    a change to the necessary gold facts, and full byte identity is no longer
    the retrieval contract.
    """
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_corpus(tmp_path)
    from memory.graph_db import LinkSubType, LinkType

    generic_edges = [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.ENTITY
        and link.properties.get("sub_type") == LinkSubType.REFERS_TO.value
        and "role" not in link.properties
    ]
    assert generic_edges == []

    context = _adapter(backend, tmp_path).recall("我是谁？", _POLICY)
    assert [item.evidence_id for item in context.evidence[:2]] == ["ev-u1", "ev-u2"]
    assert len(context.evidence) <= _POLICY.max_evidence_items
    for item in context.evidence:
        source_id = backend.find_memory_id(item.evidence_id)
        assert source_id is not None
        source = backend.trg.graph_db.get_node(source_id)
        assert item.text == source.content_narrative
        assert item.provenance.turn_id == source.attributes["provenance"]["turn_id"]
    if len(context.evidence) > 2:
        assert context.evidence[2].evidence_id.startswith("ev-d")
        assert context.evidence[2].text.endswith("问我是谁。")
    third_party = _adapter(backend, tmp_path).recall("小林是谁？", _POLICY)
    assert [item.evidence_id for item in third_party.evidence] == ["ev-x1"]
    absent = _adapter(backend, tmp_path).recall("我的银行卡号是多少？", _POLICY)
    assert absent.evidence == ()


def test_generic_mention_edge_recovers_crowded_event_via_target_ref(tmp_path, monkeypatch):
    """Query resolved to 王老师's ref: the crowded global path alone misses
    ev1; the generic mention edge carries it into the E_WANG subset channel.

    The public facade resolves only CURRENT_USER today, so this exercises the
    existing backend-level ``target_entity_ref`` seam directly — the same seam
    the validated shadow used (RESULT_CROWDED.md).
    """
    backend = _build_mention_corpus(tmp_path)
    query = "王老师的学生是谁？"

    # The new full-history name-aware lexical channel can independently find
    # ev1. Disable that one channel in both arms to keep this a causal test of
    # generic mention edges, without changing its required evidence answer.
    monkeypatch.setattr(backend._lexical_index, "rank", lambda **_kwargs: [])

    global_only = backend.recall(query, _POLICY)
    global_texts = [candidate.text for candidate in global_only]
    # calibration: the corpus is crowded enough that the global path misses ev1
    assert "小林的导师是王老师。" not in global_texts

    targeted = backend.recall(query, _POLICY, target_entity_ref="E_WANG")
    targeted_texts = [candidate.text for candidate in targeted]
    assert "小林的导师是王老师。" in targeted_texts
    # candidate bound and leakage hygiene at the backend seam
    assert len(targeted) <= _POLICY.max_nodes
    for candidate in targeted:
        assert "entity:" not in candidate.text
        assert "[SAME_ENTITY]" not in candidate.text


def test_current_user_self_query_unchanged_with_generic_edges_in_graph(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_mention_corpus(tmp_path)
    context = _adapter(backend, tmp_path).recall("我是谁？", _POLICY)
    assert "我叫林岚。" in _evidence_texts(context)
    _assert_no_internal_leak(context)
    negative = _adapter(backend, tmp_path).recall("我的银行卡号是多少？", _POLICY)
    assert negative.evidence == ()
    _assert_no_internal_leak(negative)


def test_mention_corpus_bounds_and_no_internal_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_mention_corpus(tmp_path)
    for query in ("王老师的学生是谁？", "小林的导师是谁？", "我是谁？"):
        context = _adapter(backend, tmp_path).recall(query, _POLICY)
        assert len(context.evidence) <= _POLICY.max_evidence_items
        assert len(context.rendered_text) <= _POLICY.max_chars
        for token in ("[SAME_ENTITY]", "E_WANG", "E_XIAOLIN", "entity:"):
            assert token not in context.rendered_text


# --- Production Slice 3: query-side ordinary entity exact lookup -----------
#
# The adapter facade first runs the unchanged CURRENT_USER classification;
# only when it returns None does it ask the backend for a deterministic
# exact-surface lookup over persisted EntityNode canonical_surfaces
# (0 hits -> None, exactly one distinct ref -> that ref, >1 refs -> None).


def test_adapter_auto_resolves_ordinary_entity_and_recovers_crowded_event(
    tmp_path, monkeypatch,
):
    """Case 1: "王老师的学生是谁？" auto-resolves E_WANG through the facade —
    no monkeypatching, no backend seam — and the generic mention edge
    carries the crowded-out mentor event into evidence."""
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_mention_corpus(tmp_path)
    query = "王老师的学生是谁？"
    assert backend.resolve_target_entity_ref(query) == "E_WANG"
    context = _adapter(backend, tmp_path).recall(query, _POLICY)
    assert "小林的导师是王老师。" in _evidence_texts(context)
    _assert_no_internal_leak(context)


def test_adapter_auto_resolves_second_ordinary_entity(tmp_path, monkeypatch):
    """Case 2: a second ordinary entity resolves by its own surface."""
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_mention_corpus(tmp_path)
    query = "小林的导师是谁？"
    assert backend.resolve_target_entity_ref(query) == "E_XIAOLIN"
    context = _adapter(backend, tmp_path).recall(query, _POLICY)
    assert "小林的导师是王老师。" in _evidence_texts(context)
    _assert_no_internal_leak(context)


def test_lookup_resolves_exact_surface_only_when_node_persisted(tmp_path):
    """Case 3: Tlhey resolves once its EntityNode exists; without the node
    the same query resolves to None."""
    backend = RealMagmaBackend(tmp_path / "magma")
    assert backend.resolve_target_entity_ref("Tlhey是谁？") is None
    memory_id = _add_memory(
        backend, "ev-tlhey", "Tlhey是项目组的新成员。", 0,
        ref="E_TLHEY", surface="Tlhey",
    )
    backend.create_relationships([memory_id])
    assert backend.resolve_target_entity_ref("Tlhey是谁？") == "E_TLHEY"


def test_self_query_still_uses_current_user_ref_with_lookup_active(
    tmp_path, monkeypatch,
):
    """Case 4: the lookup alone finds no surface in a self query, so the
    E_001 hit below comes from the unchanged CURRENT_USER classification."""
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_mention_corpus(tmp_path)
    assert backend.resolve_target_entity_ref("我是谁？") is None
    context = _adapter(backend, tmp_path).recall("我是谁？", _POLICY)
    assert "我叫林岚。" in _evidence_texts(context)
    _assert_no_internal_leak(context)


def test_shared_surface_across_two_refs_resolves_none(tmp_path):
    """Case 5: two Alexes — one canonical_surface mapping to two distinct
    refs must never guess; the lookup returns None."""
    backend = RealMagmaBackend(tmp_path / "magma")
    first = _add_memory(
        backend, "ev-alex-a", "Alex在东南大学任教。", 0,
        ref="E_ALEX_A", surface="Alex",
    )
    second = _add_memory(
        backend, "ev-alex-b", "Alex加入了新项目。", 1,
        ref="E_ALEX_B", surface="Alex",
    )
    backend.create_relationships([first, second])
    assert backend.resolve_target_entity_ref("Alex是谁？") is None


def test_unknown_entity_query_falls_back_to_legacy_global_path(
    tmp_path, monkeypatch,
):
    """Case 6: an unknown entity resolves None, so the facade takes the
    byte-identical legacy global path (proven against the flag-off path,
    which is exactly the pre-lookup behavior for a non-self query)."""
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    backend = _build_mention_corpus(tmp_path)
    query = "孙悟空是谁？"
    assert backend.resolve_target_entity_ref(query) is None
    with_lookup = _adapter(backend, tmp_path).recall(query, _POLICY)
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "false")
    legacy = _adapter(backend, tmp_path).recall(query, _POLICY)
    assert _evidence_texts(with_lookup) == _evidence_texts(legacy)
    assert with_lookup.rendered_text == legacy.rendered_text


def test_lookup_stable_across_persist_and_reload(tmp_path):
    """Case 7: lookup results survive restart — same resolutions before and
    after persist + reload."""
    backend = RealMagmaBackend(tmp_path / "magma")
    memory_id = _add_memory(
        backend, "ev-tlhey", "Tlhey是项目组的新成员。", 0,
        ref="E_TLHEY", surface="Tlhey",
    )
    alex = _add_memory(
        backend, "ev-alex", "Alex在东南大学任教。", 1,
        ref="E_ALEX", surface="Alex",
    )
    backend.create_relationships([memory_id, alex])
    backend.persist()
    reloaded = RealMagmaBackend(tmp_path / "magma")
    for candidate in (backend, reloaded):
        assert candidate.resolve_target_entity_ref("Tlhey是谁？") == "E_TLHEY"
        assert candidate.resolve_target_entity_ref("Alex是谁？") == "E_ALEX"
        assert candidate.resolve_target_entity_ref("孙悟空是谁？") is None
