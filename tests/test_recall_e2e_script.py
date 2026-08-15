from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import scripts.recall_e2e_test as recall_e2e
from Conversation_Memory.adapter.models import IngestionResult, MemoryContext
from core.main import create_app
from core.model_client import MockModelClient
from scripts.recall_e2e_test import (
    CONVERSATION_MEMORY_ROOT,
    DEFAULT_WORK_DIR,
    FIXED_TURNS,
    INGESTION_VERSION,
    ROOT,
    SANDBOX_MARKER,
    SANDBOX_MARKER_CONTENT,
    SandboxSafetyError,
    cleanup_test_sandbox,
    reset_test_sandbox,
    run_acceptance,
    validate_sandbox_path,
)


IS_ISOLATED_ENV = (
    Path(sys.prefix).resolve()
    == (CONVERSATION_MEMORY_ROOT / ".venv").resolve()
)


class _DeterministicBgeScorer:
    _TARGET_TERMS = {
        "我准备睡觉了。": ("准备睡觉", "早点睡"),
        "我明天有什么安排？": ("没有实验室例会",),
        "打印机怎么样了？": ("打印机", "坏了"),
        "我现在最终选择什么？": ("Beta",),
        "我一开始考虑的是什么？": ("Alpha",),
        "小林下周去哪？": ("小林", "上海"),
        "下周三项目评审安排在哪个房间？": ("307", "海棠会议室"),
        "服务端口是多少？": ("5433",),
        "上线后检查什么？": ("健康状态",),
        "我下个月有什么计划？": ("可能", "下个月", "上海"),
    }

    def __init__(self, state):
        self._state = state

    def score(self, query, candidate_texts):
        self._state["score_calls"] += 1
        terms = self._TARGET_TERMS.get(query, ())
        return tuple(
            float(sum(term in text for term in terms))
            for text in candidate_texts
        )


@pytest.fixture(scope="module", autouse=True)
def fake_e2e_bge_factory():
    state = {
        "factory_calls": 0,
        "score_calls": 0,
        "real_constructor_calls": 0,
    }
    patcher = pytest.MonkeyPatch()
    real_bge_class = recall_e2e.magma_adapter_module.BgeReranker

    def forbid_real_constructor(*args, **kwargs):
        del args, kwargs
        state["real_constructor_calls"] += 1
        raise AssertionError("real BGE must not load in ordinary pytest")

    def create_fake_scorer():
        state["factory_calls"] += 1
        return _DeterministicBgeScorer(state)

    patcher.setattr(real_bge_class, "__init__", forbid_real_constructor)
    patcher.setattr(
        recall_e2e.magma_adapter_module,
        "_create_bge_reranker",
        create_fake_scorer,
    )
    try:
        yield state
    finally:
        patcher.undo()


@pytest.fixture(scope="module")
def kept_acceptance(tmp_path_factory, fake_e2e_bge_factory):
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
    assert "我昨天完成了膜实验" in FIXED_TURNS[0].text
    assert "我上周一更换了溶剂，下周一准备复查" in FIXED_TURNS[4].text


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
    assert report["dream"] == {
        "attempted": 1,
        "failed": 0,
        "second_attempted": 0,
    }

    records = [
        json.loads(line)
        for line in (
            sandbox / "draft" / "cold_drafts.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == len(FIXED_TURNS)
    assert {record["record_type"] for record in records} == {"cold_turn"}
    assert len({record["segment_id"] for record in records}) == 1
    assert [record["segment_turn_index"] for record in records] == list(
        range(len(FIXED_TURNS))
    )
    assert {record["segment_turn_count"] for record in records} == {
        len(FIXED_TURNS)
    }
    assert len({
        (
            record["schema_version"],
            record["segment_created_at"],
            record["source"],
        )
        for record in records
    }) == 1
    assert {record["state"] for record in records} == {"consumed"}
    assert len({record["consumed_at"] for record in records}) == 1

    expected_turns = [
        recall_e2e._draft_turn(turn).storage_turn()
        for turn in FIXED_TURNS
    ]
    assert [
        {
            key: record[key]
            for key in (
                "turn_id",
                "role",
                "text",
                "created_at",
                "source_timezone",
                "timezone_source",
            )
        }
        for record in records
    ] == expected_turns
    assert report["cold_draft"]["pending_created"] == 1
    assert report["cold_draft"]["consumed"] == 1


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
        "assistant_self_memory",
        "zh_temporal",
        "zh_previous_week",
        "zh_next_week",
        "negative_source_bounded",
    ],
)
def test_real_recall_query_checks_pass(kept_acceptance, check):
    report, _ = kept_acceptance
    assert report["recall"]["checks"][check] is True


def test_real_recall_summary_is_ten_of_ten(kept_acceptance):
    report, _ = kept_acceptance
    assert report["recall"]["queries"] == 10
    assert report["recall"]["passed"] == 10
    assert report["recall"]["failed"] == 0


def test_real_magma_grounded_bge_report_has_production_policy_gate(
    kept_acceptance,
    fake_e2e_bge_factory,
):
    report, _ = kept_acceptance
    scoring = report['grounded_bge_acceptance']
    assert scoring['post_rerank_scoring'] == 'hindsight'
    assert scoring['hindsight_commit'] == (
        'f1c825d88471d069aec0480446d071c589ab10bd'
    )
    assert scoring['final_min_score'] is None
    assert scoring['recency'] == 'linear_365_day_floor_0_1'
    assert scoring['recency_alpha'] == 0.2
    assert scoring['temporal_signal'] == 'neutral_0_5'
    assert scoring['temporal_alpha'] == 0.2
    assert scoring['proof_signal'] == 'neutral_0_5'
    assert scoring['proof_count_alpha'] == 0.1
    acceptance = report["grounded_bge_acceptance"]

    assert acceptance["result"] == "PASS"
    assert acceptance["model"] == "BAAI/bge-reranker-v2-m3"
    assert acceptance["revision"] == (
        "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    )
    assert acceptance["queries"] == 11
    assert acceptance["top1"] == sum(
        row["top1"] for row in acceptance["rows"]
    )
    assert acceptance["top_k"] == 10
    assert acceptance["max_graph_depth"] == 1
    assert acceptance["max_nodes"] == 20
    assert acceptance["score_floor"] is None
    assert acceptance["max_evidence_items"] == 3
    assert acceptance["depth0_positive_complete"] == 11
    assert acceptance["depth1_positive_complete"] == 11
    assert 0 <= acceptance["no_answer_empty_depth0"] <= 6
    assert 0 <= acceptance["no_answer_empty_depth1"] <= 6
    assert 0 <= acceptance["near_miss_empty_depth0"] <= 3
    assert 0 <= acceptance["near_miss_empty_depth1"] <= 3
    assert acceptance["xiaolin"] is True
    assert acceptance["meeting_role_evidence"] is True
    assert acceptance["meeting_roles"] == ["assistant", "user"]
    assert acceptance["assistant_self_memory"] is True
    assert acceptance["lazy_load"] is True
    assert acceptance["instance_reuse"] is True
    assert acceptance["factory_calls"] == 1
    assert acceptance["public_score_exposure"] == "none"
    assert len(acceptance["rows"]) == 11
    assert len(acceptance["graphiti_rows"]) == 20
    assert all(
        0 < row["candidate_count"] <= 20
        for row in acceptance["rows"]
    )
    assert all(
        row["depth1_complete"]
        for row in acceptance["graphiti_rows"]
        if row["kind"] == "positive"
    )
    assert all(
        0 <= row["depth0_candidate_count"] <= 10
        and 0 <= row["depth1_candidate_count"] <= 20
        for row in acceptance["graphiti_rows"]
    )
    assert all(
        row["top2_rendered_chars"] > 0 for row in acceptance["rows"]
    )
    assert all(
        row["top2_token_estimate"] > 0 for row in acceptance["rows"]
    )
    assert all(
        "score" not in row and "text" not in row
        for row in acceptance["rows"]
    )
    assert acceptance["median_magma_ms"] >= 0
    assert acceptance["median_bge_ms"] >= 0
    assert acceptance["median_total_ms"] >= 0
    assert acceptance["median_recall_latency_depth0"] >= 0
    assert acceptance["median_recall_latency_depth1"] >= 0
    assert acceptance["median_bge_latency_depth0"] >= 0
    assert acceptance["median_bge_latency_depth1"] >= 0
    assert fake_e2e_bge_factory["factory_calls"] >= 3
    assert fake_e2e_bge_factory["score_calls"] > 0
    assert fake_e2e_bge_factory["real_constructor_calls"] == 0


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
    assert all(
        text not in serialized
        for text in recall_e2e._grounded_bge_private_texts()
    )
    assert "traceback" not in serialized.lower()
    assert "logit" not in serialized.lower()
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


def test_chat_recall_does_not_trigger_dream_or_pending_ingestion(
    tmp_path,
    monkeypatch,
):
    class SharedMemorySpy:
        ingestion_version = "grounded-span-v2"

        def __init__(self):
            self.recall_calls = []
            self.ingest_calls = []

        def recall(self, query, policy):
            self.recall_calls.append((query, policy))
            return MemoryContext(query=query, rendered_text="bounded recalled memory")

        def ingest(self, segment):
            self.ingest_calls.append(segment.segment_id)
            return IngestionResult(
                segment_id=segment.segment_id,
                ingestion_version=self.ingestion_version,
                status="completed",
                memory_ids=(f"memory-{segment.segment_id}",),
            )

    memory = SharedMemorySpy()
    app = create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "compaction.json",
        model_client=MockModelClient(),
        env_file_path=None,
        enable_compaction=False,
        recall_enabled=True,
        memory_retriever=memory,
    )
    cold_store = app.state.cold_draft_store
    cold_store.append_segment(
        [{"role": "user", "text": "pending memory"}],
        segment_id="pending-before-chat",
    )
    dream_calls = []
    runner = app.state.dream_runner
    assert runner is not None
    original_run_once = runner.run_once

    def tracked_run_once(policy):
        dream_calls.append(policy)
        return original_run_once(policy)

    monkeypatch.setattr(runner, "run_once", tracked_run_once)

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "recall this"})

    assert response.status_code == 200
    assert len(memory.recall_calls) == 1
    assert dream_calls == []
    assert memory.ingest_calls == []
    assert [record["segment_id"] for record in cold_store.list_pending()] == [
        "pending-before-chat"
    ]

    dream_response = client.post("/api/dream/run")

    assert dream_response.status_code == 200
    assert len(dream_calls) == 1
    assert memory.ingest_calls == ["pending-before-chat"]
    assert cold_store.list_pending() == []


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
