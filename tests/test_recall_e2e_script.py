from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.recall_e2e_test as recall_e2e
from scripts.recall_e2e_test import (
    CONVERSATION_MEMORY_ROOT,
    DEFAULT_WORK_DIR,
    FIXED_TURNS,
    INGESTION_VERSION,
    ROOT,
    SANDBOX_MARKER,
    SANDBOX_MARKER_CONTENT,
    SandboxSafetyError,
    SOURCE_TIMEZONE,
    cleanup_test_sandbox,
    reset_test_sandbox,
    run_acceptance,
    validate_sandbox_path,
)


IS_ISOLATED_ENV = (
    Path(sys.prefix).resolve()
    == (CONVERSATION_MEMORY_ROOT / ".venv").resolve()
)


@pytest.fixture(scope="module")
def kept_acceptance(tmp_path_factory):
    if not IS_ISOLATED_ENV:
        pytest.skip("real recall E2E runs in Conversation_Memory/.venv")
    sandbox = tmp_path_factory.mktemp("recall-e2e-kept") / "sandbox"
    report = run_acceptance(sandbox, keep_data=True)
    try:
        yield report, sandbox
    finally:
        cleanup_test_sandbox(sandbox)


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "data" / "draft",
        ROOT / "data" / "draft" / "nested",
        ROOT / "data" / "conversation_memory",
        ROOT / "data" / "conversation_memory" / "nested",
        ROOT / "Conversation_Memory" / "upstream" / "MAGMA",
        ROOT / "Conversation_Memory" / "upstream" / "MAGMA" / "data",
    ],
)
def test_rejects_production_and_upstream_paths(path):
    with pytest.raises(SandboxSafetyError, match="production_or_sensitive_path_refused"):
        validate_sandbox_path(path)


@pytest.mark.parametrize(
    "path",
    [ROOT, ROOT / "data", ROOT / "core", Path.home(), Path(ROOT.anchor)],
)
def test_rejects_broad_or_repository_code_paths(path):
    with pytest.raises(SandboxSafetyError):
        validate_sandbox_path(path)


def test_default_work_dir_is_isolated_from_production():
    safe = validate_sandbox_path(DEFAULT_WORK_DIR)
    assert safe == DEFAULT_WORK_DIR.resolve()
    assert safe != (ROOT / "data" / "draft").resolve()
    assert safe != (ROOT / "data" / "conversation_memory").resolve()


def test_existing_directory_without_marker_is_refused(tmp_path):
    sandbox = tmp_path / "unowned"
    sandbox.mkdir()
    sentinel = sandbox / "do-not-delete.txt"
    sentinel.write_text("owned by someone else", encoding="utf-8")
    with pytest.raises(SandboxSafetyError, match="sandbox_marker_required"):
        reset_test_sandbox(sandbox)
    assert sentinel.read_text(encoding="utf-8") == "owned by someone else"


def test_sandbox_initialization_creates_exact_marker(tmp_path):
    sandbox = tmp_path / "owned"
    assert reset_test_sandbox(sandbox) == sandbox.resolve()
    assert (sandbox / SANDBOX_MARKER).read_text(encoding="utf-8") == SANDBOX_MARKER_CONTENT
    cleanup_test_sandbox(sandbox)


def test_owned_sandbox_can_be_reset_without_deleting_parent(tmp_path):
    parent_sentinel = tmp_path / "parent-sentinel.txt"
    parent_sentinel.write_text("keep", encoding="utf-8")
    sandbox = tmp_path / "owned"
    reset_test_sandbox(sandbox)
    (sandbox / "generated.txt").write_text("temporary", encoding="utf-8")
    reset_test_sandbox(sandbox)
    assert not (sandbox / "generated.txt").exists()
    assert parent_sentinel.read_text(encoding="utf-8") == "keep"
    cleanup_test_sandbox(sandbox)


def test_cleanup_refuses_unmarked_directory(tmp_path):
    sandbox = tmp_path / "unowned"
    sandbox.mkdir()
    with pytest.raises(SandboxSafetyError, match="sandbox_marker_required"):
        cleanup_test_sandbox(sandbox)
    assert sandbox.exists()


def test_cleanup_removes_only_marked_sandbox(tmp_path):
    sandbox = tmp_path / "owned"
    reset_test_sandbox(sandbox)
    cleanup_test_sandbox(sandbox)
    assert not sandbox.exists()
    assert tmp_path.exists()


def test_fixed_conversation_has_required_order_and_aware_timestamps():
    assert [turn.role for turn in FIXED_TURNS] == [
        "user", "assistant", "user", "assistant", "user", "assistant"
    ]
    assert [turn.timestamp for turn in FIXED_TURNS] == [
        "2026-07-14T10:00:00+08:00",
        "2026-07-14T10:05:00+08:00",
        "2026-07-14T11:00:00+08:00",
        "2026-07-14T11:05:00+08:00",
        "2026-07-15T09:00:00+08:00",
        "2026-07-15T09:05:00+08:00",
    ]
    assert "solvent evaporated too quickly" in FIXED_TURNS[2].text
    assert "changed the solvent today" in FIXED_TURNS[4].text


def test_real_e2e_result_is_pass(kept_acceptance):
    report, _ = kept_acceptance
    assert report["result"] == "PASS"
    assert report["failures"] == []


def test_real_compaction_created_pending_then_consumed(kept_acceptance):
    report, sandbox = kept_acceptance
    assert report["cold_draft"] == {
        "compacted": True,
        "pending_created": 1,
        "consumed": 1,
        "raw_order_preserved": True,
    }
    record = json.loads((sandbox / "draft" / "cold_drafts.jsonl").read_text(encoding="utf-8"))
    assert record["state"] == "consumed"
    assert [(item["role"], item["text"]) for item in record["turns"]] == [
        (turn.role, turn.text) for turn in FIXED_TURNS
    ]
    assert record["schema_version"] == 2
    assert [item["turn_id"] for item in record["turns"]] == [
        turn.turn_id for turn in FIXED_TURNS
    ]
    assert all(item["source_timezone"] == SOURCE_TIMEZONE for item in record["turns"])
    assert all(item["timezone_source"] == "client" for item in record["turns"])


def test_real_dream_completed_without_failure(kept_acceptance):
    report, sandbox = kept_acceptance
    assert report["dream"] == {"attempted": 1, "failed": 0, "second_attempted": 0}
    state = json.loads((sandbox / "conversation_memory" / "ingestion_state.json").read_text(encoding="utf-8"))
    assert len(state) == 1
    key, value = next(iter(state.items()))
    assert key.endswith(f":{INGESTION_VERSION}")
    assert value["status"] == "completed"


def test_real_magma_graph_and_vectors_are_persisted(kept_acceptance):
    report, sandbox = kept_acceptance
    assert report["magma"] == {"events": 6, "vectors": 6, "persisted": True}
    assert (sandbox / "conversation_memory" / "magma" / "graph.json").is_file()
    assert (sandbox / "conversation_memory" / "magma" / "vectors" / "metadata.json").is_file()


@pytest.mark.parametrize(
    "check",
    [
        "exact_overlap",
        "semantic_paraphrase",
        "behavior_change",
        "temporal",
        "entity",
        "negative_source_bounded",
    ],
)
def test_real_recall_query_checks_pass(kept_acceptance, check):
    report, _ = kept_acceptance
    assert report["recall"]["checks"][check] is True


def test_real_recall_summary_is_six_of_six(kept_acceptance):
    report, _ = kept_acceptance
    assert report["recall"]["queries"] == 6
    assert report["recall"]["passed"] == 6
    assert report["recall"]["failed"] == 0


def test_real_e2e_keeps_gate_disabled_compatible_and_does_not_invent_threshold(kept_acceptance):
    report, _ = kept_acceptance
    assert report["relevance_gate"] == {
        "disabled_compatibility": True,
        "calibration_queries": 40,
        "recommended_threshold": None,
        "enabled_validation": "threshold_not_recommended",
        "restart_consistent": None,
    }


def test_real_provenance_and_temporal_mapping_pass(kept_acceptance):
    report, _ = kept_acceptance
    assert report["provenance"] == {
        "passed": True,
        "temporal_normalization_passed": True,
        "timestamp_mapping": "native_per_turn_v2",
    }


def test_real_recall_bounds_pass(kept_acceptance):
    report, _ = kept_acceptance
    assert report["bounds"] == {
        "top_k": True,
        "max_evidence_items": True,
        "max_chars": True,
    }


def test_real_restart_recall_passes(kept_acceptance):
    report, _ = kept_acceptance
    assert report["restart_recall"]["passed"] is True


def test_real_second_dream_is_idempotent(kept_acceptance):
    report, _ = kept_acceptance
    assert report["idempotency"] == {
        "passed": True,
        "node_count_stable": True,
        "vector_count_stable": True,
        "state_stable": True,
        "evidence_ids_stable": True,
    }


def test_kept_report_contains_no_paths_raw_dialogue_or_node_uuids(kept_acceptance):
    report, sandbox = kept_acceptance
    report_path = sandbox / "reports" / "recall_e2e_result.json"
    serialized = report_path.read_text(encoding="utf-8")
    assert json.loads(serialized) == report
    assert str(sandbox) not in serialized
    assert str(ROOT) not in serialized
    assert all(turn.text not in serialized for turn in FIXED_TURNS)
    assert "traceback" not in serialized.lower()
    assert "openai_api_key" not in serialized.lower()
    assert report["leak_checks"]["passed"] is True


def test_keep_data_retains_sandbox_and_report(kept_acceptance):
    report, sandbox = kept_acceptance
    assert report["cleanup"] == {"mode": "keep-data", "passed": True}
    assert (sandbox / SANDBOX_MARKER).is_file()
    assert (sandbox / "reports" / "recall_e2e_result.json").is_file()


def test_default_real_run_cleans_sandbox(tmp_path):
    if not IS_ISOLATED_ENV:
        pytest.skip("real recall E2E runs in Conversation_Memory/.venv")
    sandbox = tmp_path / "default-cleanup"
    report = run_acceptance(sandbox, keep_data=False)
    assert report["result"] == "PASS"
    assert report["cleanup"] == {"mode": "default", "passed": True}
    assert not sandbox.exists()


def test_failed_pipeline_still_cleans_owned_sandbox_by_default(tmp_path, monkeypatch):
    sandbox = tmp_path / "failed-default-cleanup"
    monkeypatch.setattr(recall_e2e, "_validate_isolated_runtime", lambda: None)

    def fail_pipeline(*args, **kwargs):
        raise recall_e2e.AcceptanceFailure("recall", "synthetic_failure")

    monkeypatch.setattr(recall_e2e, "_execute_pipeline", fail_pipeline)
    report = run_acceptance(sandbox, keep_data=False)

    assert report["result"] == "FAIL"
    assert report["failures"] == [
        {"stage": "recall", "code": "synthetic_failure"}
    ]
    assert report["cleanup"] == {"mode": "default", "passed": True}
    assert not sandbox.exists()


def test_chat_runtime_does_not_import_recall_e2e_or_dream():
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "core").glob("*.py")
    )
    assert "recall_e2e_test" not in production
    assert "DreamRunner" not in production
    assert "MemoryRetriever" not in production


def test_upstream_magma_is_clean_after_real_e2e(kept_acceptance):
    upstream = ROOT / "Conversation_Memory" / "upstream" / "MAGMA"
    status = subprocess.run(
        ["git", "-C", str(upstream), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    )
    diff = subprocess.run(
        ["git", "-C", str(upstream), "diff", "--stat"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
    assert diff.stdout == ""
