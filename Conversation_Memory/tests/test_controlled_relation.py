import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapter.controlled_relation import (  # noqa: E402
    UNRESOLVED,
    ControlledRelationResolver,
)
from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.models import (  # noqa: E402
    BackendCandidate,
    RecallPolicy,
    SourceProvenance,
)
from ingestion.state_store import IngestionStateStore  # noqa: E402


ALIASES = {
    "FILTER_MESH_CLASS": (
        "filter mesh classification", "mesh category",
        "滤网目数等级", "筛网规格",
    ),
    "ROTOR_BALANCE_GRADE": (
        "rotor balance grade", "balance quality grade",
        "转子平衡等级", "平衡品质级别",
    ),
    "CABLE_SHEATH_COMPOUND": (
        "cable sheath compound", "jacket polymer",
        "电缆护套材料", "外被材质",
    ),
    "VALVE_SEAT_COMPOUND": (
        "valve seat compound", "seat material",
        "阀座材质", "密封座材料",
    ),
    "ALARM_CLEARANCE_POLICY": (
        "alarm clearance rule", "acknowledgement behavior",
        "报警清除规则", "确认策略",
    ),
    "SUPPORT_CONTACT_CHANNEL": (
        "support contact route", "service desk route",
        "服务联系渠道", "客服联络方式",
    ),
    "CAMERA_CAPTURE_RATE": (
        "camera capture rate", "frames per second",
        "相机采集速率", "采样帧频",
    ),
    "PORT_LINK_MODE": (
        "network port link mode", "link behavior",
        "网络端口链路模式", "端口工作方式",
    ),
    "RESERVOIR_USABLE_VOLUME": (
        "usable reservoir volume", "working capacity",
        "储液罐可用容积", "有效容量",
    ),
    "SENSOR_INSTALLATION_ANGLE": (
        "sensor installation angle", "orientation angle",
        "传感器安装角度", "装配倾角",
    ),
}


@pytest.mark.parametrize(
    ("canonical_id", "surface"),
    [
        (canonical_id, surface)
        for canonical_id, surfaces in ALIASES.items()
        for surface in surfaces
    ],
)
def test_bilingual_calibration_aliases_resolve(canonical_id, surface):
    resolver = ControlledRelationResolver()

    assert resolver.resolve_query_relations((surface,)) == (canonical_id,)


def test_compound_and_ambiguous_query_relations_resolve_independently():
    resolver = ControlledRelationResolver()

    assert resolver.resolve_query_relations((
        "camera capture rate",
        "network port link mode",
        "class (rotor balance)",
        "material (valve seat)",
        "class",
        "material",
    )) == (
        "CAMERA_CAPTURE_RATE",
        "PORT_LINK_MODE",
        "ROTOR_BALANCE_GRADE",
        "VALVE_SEAT_COMPOUND",
        UNRESOLVED,
        UNRESOLVED,
    )


@pytest.mark.parametrize(
    ("relation", "subject", "value", "text", "expected"),
    (
        (
            "class", "RB-202", "G1.0",
            "Rotor RB-202 meets balance quality class G1.0.",
            "ROTOR_BALANCE_GRADE",
        ),
        (
            "material", "VV-404", "PTFE-404",
            "Valve VV-404 uses PTFE-404 as its seat material.",
            "VALVE_SEAT_COMPOUND",
        ),
        (
            "class", "FT-101", "MESH-72",
            "Filter FT-101 uses mesh classification MESH-72.",
            "FILTER_MESH_CLASS",
        ),
        (
            "material", "CB-303", "TPE-303",
            "Cable CB-303 has sheath compound TPE-303.",
            "CABLE_SHEATH_COMPOUND",
        ),
        ("class", "UN-514", "READY", "Unit UN-514 is READY.", UNRESOLVED),
        (
            "reset semantics", "AX-211", "remote reset",
            "Alarm AX-211 uses remote reset semantics.", UNRESOLVED,
        ),
        (
            "联络入口", "QP-312", "PORTAL-QP-312",
            "设备 QP-312 的服务联络入口是 PORTAL-QP-312。", UNRESOLVED,
        ),
        (
            "configuration", "UN-413", "PROFILE-JADE",
            "Unit UN-413 uses service profile PROFILE-JADE.", UNRESOLVED,
        ),
    ),
)
def test_memory_resolution_uses_only_proven_context_rules(
    relation, subject, value, text, expected,
):
    resolver = ControlledRelationResolver()

    assert resolver.resolve_memory(
        relation=relation,
        subject=subject,
        value=value,
        text=text,
    ) == expected


def test_unseen_query_surfaces_do_not_false_canonicalize():
    resolver = ControlledRelationResolver()

    assert resolver.resolve_query_relations((
        "alarm reset semantics",
        "\u670d\u52a1\u8054\u7cfb\u5165\u53e3",
        "service profile",
    )) == (UNRESOLVED, UNRESOLVED, UNRESOLVED)


class _Backend:
    def __init__(self, candidates):
        self.candidates = candidates

    def recall(self, _query, _policy, target_entity_ref=None):
        return list(self.candidates)


class _Reranker:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def score(self, query, texts):
        self.calls.append((query, texts))
        return self.scores


def _candidate(evidence_id, *, subject, relation, value, text):
    timestamp = datetime(2026, 8, 14, tzinfo=UTC)
    provenance = SourceProvenance(
        segment_id="relation-gate-segment",
        conversation_id="relation-gate-conversation",
        turn_id=f"turn-{evidence_id}",
        source_role="user",
        source_timestamp=timestamp.isoformat(),
        source_timezone="UTC",
        ingestion_version="grounded-formation-v1",
        timezone_source="configured_default",
    )
    return BackendCandidate(
        text,
        timestamp.isoformat(),
        None,
        {
            "evidence_id": evidence_id,
            "subject": subject,
            "relation": relation,
            "value": value,
            "provenance": provenance.__dict__,
        },
    )


def test_recall_rejects_resolved_relation_mismatch_before_reranking(tmp_path):
    matching = _candidate(
        "camera", subject="CM-707", relation="frames per second",
        value="50 fps", text="Camera CM-707 captures at 50 frames per second.",
    )
    mismatching = _candidate(
        "port", subject="NP-808", relation="link behavior",
        value="FULL-DUPLEX", text="Port NP-808 uses FULL-DUPLEX link mode.",
    )
    adapter = MagmaMemoryAdapter(
        _Backend((matching, mismatching)),
        IngestionStateStore(tmp_path / "state.json"),
    )
    reranker = _Reranker((1.0,))
    adapter._bge_reranker = reranker
    adapter._bge_reranker_load_attempted = True

    context = adapter.recall(
        "CM-707 camera rate",
        RecallPolicy(
            max_graph_depth=0,
            max_evidence_items=2,
            relation_surfaces=("camera capture rate",),
        ),
    )

    assert reranker.calls == [(
        "CM-707 camera rate",
        ("Camera CM-707 captures at 50 frames per second.",),
    )]
    assert [item.evidence_id for item in context.evidence] == ["camera"]


def test_recall_fails_open_when_either_relation_side_is_unresolved(tmp_path):
    resolved_memory = _candidate(
        "camera", subject="CM-707", relation="frames per second",
        value="50 fps", text="Camera CM-707 captures at 50 frames per second.",
    )
    unresolved_memory = _candidate(
        "profile", subject="UN-413", relation="configuration",
        value="PROFILE-JADE", text="Unit UN-413 uses profile PROFILE-JADE.",
    )
    adapter = MagmaMemoryAdapter(
        _Backend((resolved_memory, unresolved_memory)),
        IngestionStateStore(tmp_path / "state.json"),
    )
    reranker = _Reranker((1.0, 0.9))
    adapter._bge_reranker = reranker
    adapter._bge_reranker_load_attempted = True

    context = adapter.recall(
        "unknown relation request",
        RecallPolicy(
            max_graph_depth=0,
            max_evidence_items=2,
            relation_surfaces=("service profile",),
        ),
    )

    assert reranker.calls == [(
        "unknown relation request",
        (
            "Camera CM-707 captures at 50 frames per second.",
            "Unit UN-413 uses profile PROFILE-JADE.",
        ),
    )]
    assert [item.evidence_id for item in context.evidence] == [
        "camera", "profile",
    ]


def test_recall_keeps_unresolved_memory_for_resolved_query(tmp_path):
    unresolved_memory = _candidate(
        "profile", subject="UN-413", relation="configuration",
        value="PROFILE-JADE", text="Unit UN-413 uses profile PROFILE-JADE.",
    )
    adapter = MagmaMemoryAdapter(
        _Backend((unresolved_memory,)),
        IngestionStateStore(tmp_path / "state.json"),
    )
    reranker = _Reranker((1.0,))
    adapter._bge_reranker = reranker
    adapter._bge_reranker_load_attempted = True

    context = adapter.recall(
        "camera rate",
        RecallPolicy(
            max_graph_depth=0,
            relation_surfaces=("camera capture rate",),
        ),
    )

    assert [item.evidence_id for item in context.evidence] == ["profile"]


def test_recall_keeps_each_independently_requested_compound_relation(tmp_path):
    camera = _candidate(
        "camera", subject="CP-615", relation="frames per second",
        value="72 fps", text="Camera CP-615 captures at 72 frames per second.",
    )
    port = _candidate(
        "port", subject="CP-615", relation="link behavior",
        value="AUTO", text="Port CP-615 uses link mode AUTO.",
    )
    angle = _candidate(
        "angle", subject="SA-110", relation="orientation angle",
        value="42 degrees", text="Sensor SA-110 is installed at 42 degrees.",
    )
    adapter = MagmaMemoryAdapter(
        _Backend((camera, port, angle)),
        IngestionStateStore(tmp_path / "state.json"),
    )
    reranker = _Reranker((1.0, 0.9))
    adapter._bge_reranker = reranker
    adapter._bge_reranker_load_attempted = True

    context = adapter.recall(
        "CP-615 camera rate and port mode",
        RecallPolicy(
            max_graph_depth=0,
            max_evidence_items=3,
            relation_surfaces=(
                "camera capture rate",
                "network port link mode",
            ),
        ),
    )

    assert reranker.calls == [(
        "CP-615 camera rate and port mode",
        (
            "Camera CP-615 captures at 72 frames per second.",
            "Port CP-615 uses link mode AUTO.",
        ),
    )]
    assert [item.evidence_id for item in context.evidence] == [
        "camera", "port",
    ]


@pytest.mark.parametrize(
    "relation_surfaces",
    (["camera capture rate"], ("",), ("camera capture rate", 3)),
)
def test_recall_policy_rejects_invalid_relation_surface_contract(
    relation_surfaces,
):
    with pytest.raises(ValueError, match="relation_surfaces"):
        RecallPolicy(relation_surfaces=relation_surfaces)
