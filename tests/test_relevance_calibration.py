from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scripts.relevance_calibration as calibration
from scripts.relevance_calibration import (
    CALIBRATION_FIXTURE,
    CalibrationObservation,
    CalibrationSafetyError,
    cleanup_calibration_sandbox,
    load_calibration_dataset,
    metrics_for_threshold,
    reset_calibration_sandbox,
    run_calibration,
    select_recommendation,
    sweep_thresholds,
    validate_calibration_path,
)


ROOT = Path(__file__).resolve().parents[1]
IS_ISOLATED_ENV = (
    Path(sys.prefix).resolve()
    == (ROOT / "Conversation_Memory" / ".venv").resolve()
)


@pytest.fixture(scope="module")
def kept_calibration(tmp_path_factory):
    if not IS_ISOLATED_ENV:
        pytest.skip("real calibration runs in Conversation_Memory/.venv")
    sandbox = tmp_path_factory.mktemp("relevance-calibration") / "sandbox"
    report = run_calibration(sandbox, keep_data=True)
    try:
        yield report, sandbox
    finally:
        cleanup_calibration_sandbox(sandbox)


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "data" / "draft",
        ROOT / "data" / "conversation_memory",
        ROOT / "Conversation_Memory" / "upstream" / "MAGMA",
        ROOT,
        ROOT / "data",
        Path.home(),
        Path(ROOT.anchor),
    ],
)
def test_calibration_rejects_production_sensitive_and_broad_paths(path):
    with pytest.raises(CalibrationSafetyError):
        validate_calibration_path(path)


def test_calibration_refuses_existing_unmarked_directory(tmp_path):
    sandbox = tmp_path / "unowned"
    sandbox.mkdir()
    sentinel = sandbox / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(CalibrationSafetyError, match="sandbox_marker_required"):
        reset_calibration_sandbox(sandbox)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_calibration_marker_owns_only_its_sandbox(tmp_path):
    parent_sentinel = tmp_path / "keep.txt"
    parent_sentinel.write_text("keep", encoding="utf-8")
    sandbox = tmp_path / "owned"
    reset_calibration_sandbox(sandbox)
    assert (sandbox / calibration.SANDBOX_MARKER).read_text(encoding="utf-8") == calibration.SANDBOX_MARKER_CONTENT
    cleanup_calibration_sandbox(sandbox)
    assert not sandbox.exists()
    assert parent_sentinel.read_text(encoding="utf-8") == "keep"


def test_fixture_has_balanced_required_labels_and_categories():
    dataset = load_calibration_dataset(CALIBRATION_FIXTURE)
    positives = [item for item in dataset.queries if item.label == "relevant"]
    negatives = [item for item in dataset.queries if item.label == "irrelevant"]
    assert len(positives) == 20
    assert len(negatives) == 20
    assert all(item.expected_turn_ids for item in positives)
    assert all(not item.expected_turn_ids for item in negatives)
    assert {
        "exact_overlap",
        "semantic_paraphrase",
        "causal",
        "temporal",
        "entity",
        "synonym",
        "short",
        "long",
    } <= {item.category for item in positives}
    assert {
        "different_topic",
        "same_domain_absent_fact",
        "same_entity_wrong_attribute",
        "wrong_cause",
        "same_keywords_different_event",
        "absent_person",
        "absent_time",
        "near_neighbor",
    } <= {item.category for item in negatives}


def test_threshold_metrics_define_false_injection_and_empty_accuracy():
    observations = (
        CalibrationObservation("relevant", 0.8),
        CalibrationObservation("relevant", 0.4),
        CalibrationObservation("irrelevant", 0.7),
        CalibrationObservation("irrelevant", 0.2),
    )
    metrics = metrics_for_threshold(observations, 0.7)
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["positive_recall"] == 0.5
    assert metrics["false_injection_rate"] == 0.5
    assert metrics["empty_accuracy"] == 0.5


def test_threshold_boundary_is_inclusive():
    observations = (
        CalibrationObservation("relevant", 0.5),
        CalibrationObservation("irrelevant", 0.5),
    )
    metrics = metrics_for_threshold(observations, 0.5)
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1


def test_recommendation_uses_explicit_recall_and_false_injection_rule():
    observations = tuple(
        [CalibrationObservation("relevant", 0.8)] * 18
        + [CalibrationObservation("relevant", 0.6)] * 2
        + [CalibrationObservation("irrelevant", 0.2)] * 20
    )
    recommendation = select_recommendation(
        sweep_thresholds(observations, thresholds=(0.2, 0.8, 0.85))
    )
    assert recommendation["threshold"] == 0.8
    assert recommendation["positive_recall"] == 0.9
    assert recommendation["false_injection_rate"] == 0.0
    assert recommendation["usable"] is True


def test_overlapping_scores_produce_no_forced_recommendation():
    observations = tuple(
        [CalibrationObservation("relevant", 0.4)] * 20
        + [CalibrationObservation("irrelevant", 0.7)] * 20
    )
    recommendation = select_recommendation(
        sweep_thresholds(observations, thresholds=(0.3, 0.5, 0.8))
    )
    assert recommendation == {
        "threshold": None,
        "rule": recommendation["rule"],
        "usable": False,
        "limitation_code": "threshold_not_recommended",
    }


def test_failed_calibration_still_cleans_owned_sandbox_by_default(tmp_path, monkeypatch):
    sandbox = tmp_path / "failed-cleanup"
    monkeypatch.setattr(calibration, "_validate_isolated_runtime", lambda: None)

    def fail(*args, **kwargs):
        raise calibration.CalibrationFailure("scoring", "synthetic_failure")

    monkeypatch.setattr(calibration, "_execute_calibration", fail)
    report = run_calibration(sandbox, keep_data=False)
    assert report["result"] == "FAIL"
    assert report["failures"] == [{"stage": "scoring", "code": "synthetic_failure"}]
    assert report["cleanup"]["passed"] is True
    assert not sandbox.exists()


def test_real_calibration_reports_actual_overlap_without_forcing_threshold(kept_calibration):
    report, _ = kept_calibration
    assert report["result"] == "PARTIAL"
    assert report["samples"] == {"positive": 20, "negative": 20}
    assert report["overlap"]["exists"] is True
    assert report["recommendation"]["threshold"] is None
    assert report["recommendation"]["usable"] is False
    assert report["recommendation"]["limitation_code"] == "threshold_not_recommended"


def test_real_calibration_performance_is_bounded_and_local(kept_calibration):
    report, _ = kept_calibration
    performance = report["performance"]
    assert performance["runs"] >= 3
    assert performance["additional_relevance_embedding_calls"] == 0
    assert performance["additional_llm_calls"] == 0
    assert performance["additional_llm_tokens"] == 0
    assert performance["candidate_count_scored_max"] <= 20
    assert performance["model_reloads"] == 0


def test_kept_calibration_report_has_no_queries_vectors_paths_or_uuids(kept_calibration):
    report, sandbox = kept_calibration
    report_path = sandbox / "reports" / "relevance_calibration_result.json"
    serialized = report_path.read_text(encoding="utf-8")
    assert json.loads(serialized) == report
    assert str(sandbox) not in serialized
    assert str(ROOT) not in serialized
    assert all(
        item.query not in serialized
        for item in load_calibration_dataset().queries
    )
    assert "traceback" not in serialized.lower()
    assert "openai_api_key" not in serialized.lower()
    assert "query_vector" not in serialized.lower()
    assert "evidence_vector" not in serialized.lower()


def test_keep_data_retains_calibration_artifacts(kept_calibration):
    report, sandbox = kept_calibration
    assert report["cleanup"] == {"mode": "keep-data", "passed": True}
    assert (sandbox / calibration.SANDBOX_MARKER).is_file()
    assert (sandbox / "conversation_memory" / "magma" / "graph.json").is_file()
    assert (sandbox / "reports" / "relevance_calibration_result.json").is_file()
