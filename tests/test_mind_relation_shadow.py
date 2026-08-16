"""Unit tests for scripts/mind_relation_shadow.py (shadow-only harness)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from Conversation_Memory.adapter.controlled_relation import (
    UNRESOLVED,
    ControlledRelationResolver,
)
from scripts import mind_relation_shadow as mrs


_CASE = {
    "case_id": "rel-zh-01",
    "stratum": "single_zh",
    "message": "CM-707 相机的采集帧频是多少？",
    "recall_needed": True,
    "expected_relation_ids": ["CAMERA_CAPTURE_RATE"],
    "out_of_vocabulary": False,
    "memory": {
        "correct": [
            {
                "subject": "CM-707",
                "relation": "frames per second",
                "value": "50 fps",
                "text": "Camera CM-707 captures at 50 frames per second.",
            }
        ],
        "distractor": {
            "subject": "CM-707",
            "relation": "link behavior",
            "value": "AUTO",
            "text": "Port on CM-707 uses link mode AUTO.",
        },
    },
    "rationale": "需要记忆中的相机采集速率关系。",
}

_OOV_CASE = {
    "case_id": "rel-oov-01",
    "stratum": "out_of_vocabulary",
    "message": "设备 UN-413 当前用的是哪个配置档案？",
    "recall_needed": True,
    "expected_relation_ids": [],
    "out_of_vocabulary": True,
    "memory": {
        "correct": [
            {
                "subject": "UN-413",
                "relation": "configuration",
                "value": "PROFILE-JADE",
                "text": "Unit UN-413 uses service profile PROFILE-JADE.",
            }
        ],
        "distractor": None,
    },
    "rationale": "词表外关系：resolver 应真实 UNRESOLVED 并 fail-open 放行。",
}

_NO_MEMORY_CASE = {
    "case_id": "rel-nm-01",
    "stratum": "no_memory",
    "message": "1 到 10 之间有多少个偶数？",
    "recall_needed": False,
    "expected_relation_ids": [],
    "out_of_vocabulary": False,
    "memory": None,
    "rationale": "公共知识，无需记忆。",
}


def _write_cases(tmp_path: Path, cases: list[dict]) -> Path:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_cases_accepts_valid_set(tmp_path: Path) -> None:
    path = _write_cases(tmp_path, [_CASE, _OOV_CASE, _NO_MEMORY_CASE])
    cases = mrs.load_cases(path)
    assert [case["case_id"] for case in cases] == [
        "rel-zh-01",
        "rel-oov-01",
        "rel-nm-01",
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda c: c.pop("expected_relation_ids"), id="missing_field"),
        pytest.param(lambda c: c.update(recall_needed="yes"), id="bad_label"),
        pytest.param(
            lambda c: c.update(expected_relation_ids=["NOT_A_REAL_ID"]),
            id="unknown_relation_id",
        ),
        pytest.param(
            lambda c: c.update(out_of_vocabulary=True,
                               expected_relation_ids=["CAMERA_CAPTURE_RATE"]),
            id="oov_with_expected_ids",
        ),
    ],
)
def test_load_cases_rejects_invalid(tmp_path: Path, mutate) -> None:
    case = json.loads(json.dumps(_CASE))
    mutate(case)
    path = _write_cases(tmp_path, [case])
    with pytest.raises(ValueError):
        mrs.load_cases(path)


def test_load_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = _write_cases(tmp_path, [_CASE, json.loads(json.dumps(_CASE))])
    with pytest.raises(ValueError, match="duplicate"):
        mrs.load_cases(path)


def test_parse_v3rel_valid_output() -> None:
    recall, surfaces = mrs.parse_v3rel_output(
        '{"recall": true, "relations": ["相机采集速率", "network port link mode"]}'
    )
    assert recall is True
    assert surfaces == ("相机采集速率", "network port link mode")


def test_parse_v3rel_valid_empty_relations() -> None:
    recall, surfaces = mrs.parse_v3rel_output(
        ' {"recall": false, "relations": []} \n'
    )
    assert recall is False
    assert surfaces == ()


@pytest.mark.parametrize(
    "raw",
    [
        "true",  # v2-style bare word is a protocol failure under v3rel
        '{"recall": "true", "relations": []}',  # recall not bool
        '{"recall": true}',  # missing relations
        '{"recall": true, "relations": "camera capture rate"}',  # not a list
        '{"recall": true, "relations": ["a", "b", "c"]}',  # over budget
        '{"recall": true, "relations": ["  "]}',  # empty surface
        '{"recall": true, "relations": [42]}',  # non-string surface
        "```json\n{\"recall\": true, \"relations\": []}\n```",  # fenced
        '{"recall": true, "relations": [], "extra": 1} trailing',
    ],
)
def test_parse_v3rel_rejects_invalid_output(raw: str) -> None:
    with pytest.raises(ValueError):
        mrs.parse_v3rel_output(raw)


def test_v3rel_prompt_lists_full_vocabulary_and_json_contract() -> None:
    system_prompt, _user = mrs.build_v3rel_prompt("消息", [])
    for canonical_id in (
        "FILTER_MESH_CLASS",
        "ROTOR_BALANCE_GRADE",
        "CABLE_SHEATH_COMPOUND",
        "VALVE_SEAT_COMPOUND",
        "ALARM_CLEARANCE_POLICY",
        "SUPPORT_CONTACT_CHANNEL",
        "CAMERA_CAPTURE_RATE",
        "PORT_LINK_MODE",
        "RESERVOIR_USABLE_VOLUME",
        "SENSOR_INSTALLATION_ANGLE",
    ):
        assert canonical_id in system_prompt
    assert "滤网目数等级" in system_prompt  # bilingual aliases shown
    assert '"recall"' in system_prompt and '"relations"' in system_prompt


def test_decide_v3rel_fail_open_on_protocol_failure() -> None:
    class _GarbageClient:
        client_kind = "model"

        def generate(self, recent_context, user_message, *, system_prompt):
            return "I think the answer is true!"

    decide = mrs.build_v3rel_decide(_GarbageClient())
    outcome = decide("message", [])
    assert outcome["parse_ok"] is False
    # fail-open: recall allowed, no surfaces supplied
    assert outcome["recall"] is True
    assert outcome["surfaces"] == ()


def test_decide_v3rel_resolves_surfaces_through_real_resolver() -> None:
    class _SurfaceClient:
        client_kind = "model"

        def generate(self, recent_context, user_message, *, system_prompt):
            return '{"recall": true, "relations": ["采样帧频"]}'

    decide = mrs.build_v3rel_decide(_SurfaceClient())
    outcome = decide("message", [])
    assert outcome["parse_ok"] is True
    assert outcome["recall"] is True
    assert outcome["surfaces"] == ("采样帧频",)
    # query-side canonicalization done by the real ControlledRelationResolver
    assert outcome["resolved_ids"] == ("CAMERA_CAPTURE_RATE",)


def test_downstream_oracle_surfaces_reject_wrong_relation(tmp_path: Path) -> None:
    result = mrs.evaluate_downstream(
        _CASE,
        ("camera capture rate",),
        state_path=tmp_path / "state.json",
    )
    assert result["correct_present"] is True
    assert result["distractor_present"] is False


def test_downstream_baseline_admits_distractor(tmp_path: Path) -> None:
    result = mrs.evaluate_downstream(
        _CASE,
        None,
        state_path=tmp_path / "state.json",
    )
    assert result["correct_present"] is True
    assert result["distractor_present"] is True


def test_downstream_unresolved_surface_fails_open(tmp_path: Path) -> None:
    result = mrs.evaluate_downstream(
        _CASE,
        ("alarm reset semantics",),  # off-vocabulary surface -> UNRESOLVED
        state_path=tmp_path / "state.json",
    )
    assert result["correct_present"] is True
    assert result["distractor_present"] is True


def test_downstream_oov_memory_survives(tmp_path: Path) -> None:
    result = mrs.evaluate_downstream(
        _OOV_CASE,
        ("service profile",),  # resolves UNRESOLVED on query side
        state_path=tmp_path / "state.json",
    )
    assert result["correct_present"] is True
    assert result["distractor_present"] is None


def test_summarize_counts_protocol_and_unsafe_separately(tmp_path: Path) -> None:
    records = [
        # valid, safe true case
        {"case_id": "t1", "recall_needed_label": True, "parse_ok": True,
         "recall": True, "surfaces": ["采样帧频"],
         "resolved_ids": ["CAMERA_CAPTURE_RATE"]},
        # protocol failure -> fail-open, must count as protocol_invalid only
        {"case_id": "t2", "recall_needed_label": True, "parse_ok": False,
         "recall": True, "surfaces": [], "resolved_ids": []},
        # unsafe: recall=true case declined
        {"case_id": "t3", "recall_needed_label": True, "parse_ok": True,
         "recall": False, "surfaces": [], "resolved_ids": []},
        # unsafe: no-memory control allowed
        {"case_id": "n1", "recall_needed_label": False, "parse_ok": True,
         "recall": True, "surfaces": [], "resolved_ids": []},
        # safe no-memory control
        {"case_id": "n2", "recall_needed_label": False, "parse_ok": True,
         "recall": False, "surfaces": [], "resolved_ids": []},
        # extraction miss: resolved id differs from the label
        {"case_id": "t4", "recall_needed_label": True, "parse_ok": True,
         "recall": True, "surfaces": ["端口工作方式"],
         "resolved_ids": ["PORT_LINK_MODE"]},
    ]
    cases = [
        {"case_id": "t1", "recall_needed": True, "stratum": "single_zh",
         "expected_relation_ids": ["CAMERA_CAPTURE_RATE"],
         "out_of_vocabulary": False},
        {"case_id": "t2", "recall_needed": True, "stratum": "single_zh",
         "expected_relation_ids": ["CAMERA_CAPTURE_RATE"],
         "out_of_vocabulary": False},
        {"case_id": "t3", "recall_needed": True, "stratum": "single_en",
         "expected_relation_ids": ["PORT_LINK_MODE"],
         "out_of_vocabulary": False},
        {"case_id": "n1", "recall_needed": False, "stratum": "no_memory",
         "expected_relation_ids": [], "out_of_vocabulary": False},
        {"case_id": "n2", "recall_needed": False, "stratum": "no_memory",
         "expected_relation_ids": [], "out_of_vocabulary": False},
        {"case_id": "t4", "recall_needed": True, "stratum": "single_zh",
         "expected_relation_ids": ["CAMERA_CAPTURE_RATE"],
         "out_of_vocabulary": False},
    ]
    summary = mrs.summarize(records, cases)
    assert summary["protocol_valid"] == pytest.approx(5 / 6)
    unsafe_reasons = {item["case_id"]: item["reason"]
                      for item in summary["unsafe_failures"]}
    assert unsafe_reasons == {
        "t3": "recall_needed_declined",
        "n1": "no_memory_allowed",
    }
    # extraction accuracy only counts parse-ok recall=true relation cases
    assert summary["extraction_accuracy"]["single_zh"] == pytest.approx(0.5)
    # t1..t4/n1/n2 each have one record -> no fluctuation
    assert summary["fluctuating_cases"] == []


def test_summarize_lists_run_level_fluctuation() -> None:
    records = [
        {"case_id": "f1", "recall_needed_label": True, "parse_ok": True,
         "recall": True, "surfaces": ["采样帧频"],
         "resolved_ids": ["CAMERA_CAPTURE_RATE"]},
        {"case_id": "f1", "recall_needed_label": True, "parse_ok": True,
         "recall": True, "surfaces": ["CAMERA_CAPTURE_RATE"],
         "resolved_ids": ["UNRESOLVED"]},
        {"case_id": "f2", "recall_needed_label": True, "parse_ok": True,
         "recall": True, "surfaces": ["采样帧频"],
         "resolved_ids": ["CAMERA_CAPTURE_RATE"]},
    ]
    cases = [
        {"case_id": "f1", "recall_needed": True, "stratum": "single_zh",
         "expected_relation_ids": ["CAMERA_CAPTURE_RATE"],
         "out_of_vocabulary": False},
        {"case_id": "f2", "recall_needed": True, "stratum": "single_zh",
         "expected_relation_ids": ["CAMERA_CAPTURE_RATE"],
         "out_of_vocabulary": False},
    ]
    summary = mrs.summarize(records, cases)
    assert summary["fluctuating_cases"] == ["f1"]


def test_resolver_unresolved_sentinel_is_real() -> None:
    # Guard the approval requirement: UNRESOLVED comes from the resolver,
    # never from an empty relations list.
    resolver = ControlledRelationResolver()
    assert resolver.resolve_query_relations(("完全不相关的短语",)) == (UNRESOLVED,)
    assert resolver.resolve_query_relations(()) == ()


def test_run_downstream_covers_baseline_oracle_candidate(tmp_path: Path) -> None:
    records = [
        {"case_id": "rel-zh-01", "run_index": 1, "parse_ok": True,
         "recall": True, "surfaces": ["相机采集速率"]},
        {"case_id": "rel-zh-01", "run_index": 2, "parse_ok": True,
         "recall": True, "surfaces": ["camera capture rate"]},
        {"case_id": "rel-nm-01", "run_index": 1, "parse_ok": True,
         "recall": False, "surfaces": []},
    ]
    cases = [json.loads(json.dumps(_CASE)), json.loads(json.dumps(_NO_MEMORY_CASE))]
    results = mrs.run_downstream(cases, records, state_dir=tmp_path / "states")

    # no-memory case has no memory fixture and is skipped entirely
    assert set(results["baseline"]) == {"rel-zh-01"}
    assert set(results["oracle"]) == {"rel-zh-01"}
    assert len(results["candidate"]) == 2
    # baseline admits the wrong-relation distractor; oracle rejects it
    assert results["baseline"]["rel-zh-01"]["distractor_present"] is True
    assert results["oracle"]["rel-zh-01"]["distractor_present"] is False
    summary = mrs.summarize_downstream(results)
    assert summary["wrong_relation_rejection"]["baseline"] == 0.0
    assert summary["wrong_relation_rejection"]["oracle"] == 1.0
    assert summary["wrong_relation_rejection"]["candidate"] == 1.0
    assert summary["correct_evidence_survival"]["candidate"] == 1.0
    assert summary["correct_evidence_kills"] == []


def test_summarize_downstream_flags_correct_evidence_kill(tmp_path: Path) -> None:
    # A model surface that resolves to the WRONG id must be caught as a kill.
    records = [
        {"case_id": "rel-zh-01", "run_index": 1, "parse_ok": True,
         "recall": True, "surfaces": ["储液罐可用容积"]},
    ]
    cases = [json.loads(json.dumps(_CASE))]
    results = mrs.run_downstream(cases, records, state_dir=tmp_path / "states")
    summary = mrs.summarize_downstream(results)
    assert summary["correct_evidence_kills"] == [
        {"case_id": "rel-zh-01", "run_index": 1}
    ]
    assert summary["correct_evidence_survival"]["candidate"] == 0.0


_COMPOUND_CASE = {
    "case_id": "rel-cp-x",
    "stratum": "compound",
    "message": "CP-615 的相机采样帧频和端口工作方式分别是什么？",
    "recall_needed": True,
    "expected_relation_ids": ["CAMERA_CAPTURE_RATE", "PORT_LINK_MODE"],
    "out_of_vocabulary": False,
    "memory": {
        "correct": [
            {
                "subject": "CP-615",
                "relation": "frames per second",
                "value": "72 fps",
                "text": "Camera CP-615 captures at 72 frames per second.",
            },
            {
                "subject": "CP-615",
                "relation": "link behavior",
                "value": "AUTO",
                "text": "Port CP-615 uses link mode AUTO.",
            },
        ],
        "distractor": {
            "subject": "CP-615",
            "relation": "mesh category",
            "value": "MESH-15",
            "text": "Filter on CP-615 uses mesh classification MESH-15.",
        },
    },
    "rationale": "复合关系：两个正确证据都必须存活。",
}


def test_compound_requires_both_correct_units(tmp_path: Path) -> None:
    # Both expected relations supplied: both correct units survive,
    # distractor rejected.
    both = mrs.evaluate_downstream(
        _COMPOUND_CASE,
        ("camera capture rate", "network port link mode"),
        state_path=tmp_path / "both.json",
    )
    assert both == {"correct_present": True, "distractor_present": False}
    # Only one of the two relations supplied: the other correct unit is
    # filtered, which must surface as correct_present=False (partial kill).
    partial = mrs.evaluate_downstream(
        _COMPOUND_CASE,
        ("camera capture rate",),
        state_path=tmp_path / "partial.json",
    )
    assert partial["correct_present"] is False
