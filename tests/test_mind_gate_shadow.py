import json
from pathlib import Path

import pytest

from scripts.mind_gate_shadow import load_cases, run_shadow


def _write_cases(path: Path, cases: list[dict]) -> Path:
    path.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _case(case_id: str, label, **extra) -> dict:
    case = {
        "case_id": case_id,
        "message": f"message for {case_id}",
        "recall_needed": label,
        "rationale": "test rationale",
        "stratum": "test",
    }
    case.update(extra)
    return case


def test_run_shadow_writes_log_and_judges_promotion(tmp_path: Path) -> None:
    cases = [
        _case("t1", True),
        _case("f1", False),
    ]
    log_path = tmp_path / "shadow_log.jsonl"

    summary = run_shadow(
        cases,
        lambda message, context: (True, "true", True),
        runs=2,
        log_path=log_path,
        prompt_version="test-v0",
        model_name="fake-model",
    )

    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 4
    record = records[0]
    assert record["case_id"] == "t1"
    assert record["run_index"] == 1
    assert record["recall_needed_label"] is True
    assert record["shadow_recall"] is True
    assert record["parse_ok"] is True
    assert record["prompt_version"] == "test-v0"
    assert record["model"] == "fake-model"
    assert record["decided_at"].endswith("Z")

    # false_allow_rate = 100% -> promotion rejected despite perfect safety.
    assert summary["safety_failures"] == []
    assert summary["false_allow_rate"] == 1.0
    assert summary["promotion_gate_passed"] is False


def test_parse_failure_fails_open_and_is_counted(tmp_path: Path) -> None:
    cases = [_case("t1", True)]
    summary = run_shadow(
        cases,
        lambda message, context: (_ for _ in ()).throw(ValueError("bad")),
        runs=3,
        log_path=tmp_path / "log.jsonl",
        prompt_version="test-v0",
        model_name="fake-model",
    )
    # fail-open means the true-labeled case still counts as allowed.
    assert summary["safety_failures"] == []
    assert len(summary["protocol_failures"]) == 3


def test_false_decline_fails_promotion(tmp_path: Path) -> None:
    cases = [_case("t1", True), _case("f1", False)]
    counts: dict[str, int] = {}

    def decide(message, context):
        counts[message] = counts.get(message, 0) + 1
        # Decline the true case exactly once (its 2nd run); never allow f1.
        if message == "message for t1" and counts[message] == 2:
            return False, "false", True
        if message == "message for t1":
            return True, "true", True
        return False, "false", True

    summary = run_shadow(
        cases,
        decide,
        runs=3,
        log_path=tmp_path / "log.jsonl",
        prompt_version="test-v0",
        model_name="fake-model",
    )
    assert summary["safety_failures"] == ["t1"]
    assert summary["false_allow_rate"] == 0.0
    assert summary["promotion_gate_passed"] is False


def test_ambiguous_cases_are_excluded_from_metrics(tmp_path: Path) -> None:
    cases = [_case("t1", True), _case("a1", "ambiguous")]
    summary = run_shadow(
        cases,
        lambda message, context: (False, "false", True),
        runs=1,
        log_path=tmp_path / "log.jsonl",
        prompt_version="test-v0",
        model_name="fake-model",
    )
    # The ambiguous case was declined but must not appear in any metric.
    assert summary["safety_failures"] == ["t1"]
    assert summary["false_allow_rate"] == 0.0
    assert summary["evaluated_case_ids"] == ["t1"]


def test_load_cases_rejects_invalid_label(tmp_path: Path) -> None:
    path = _write_cases(tmp_path / "cases.json", [_case("x", "yes")])
    with pytest.raises(ValueError):
        load_cases(path)


def test_load_cases_roundtrip(tmp_path: Path) -> None:
    path = _write_cases(
        tmp_path / "cases.json",
        [_case("t1", True, context=[{"role": "user", "text": "earlier"}])],
    )
    cases = load_cases(path)
    assert cases[0]["case_id"] == "t1"
    assert cases[0]["context"] == [{"role": "user", "text": "earlier"}]
