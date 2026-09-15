"""Isolated maintenance check: manual Dream, real MAGMA, first-hit and Cold.

Run with Conversation_Memory/.venv/Scripts/python.exe -m
scripts.first_hit_memory_check [--work-dir NEW_OR_EMPTY_DIRECTORY].
No evaluation corpus, provider requests, credentials or existing state is used.
The synthetic directory is retained; this entry never resets or deletes a path.
"""
from __future__ import annotations

import argparse
import contextlib
import gc
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from unittest.mock import patch
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]


class CheckFailure(RuntimeError):
    def __init__(self, stage, code):
        super().__init__(code)
        self.stage, self.code = stage, code


def _require(condition, stage, code):
    if not condition:
        raise CheckFailure(stage, code)


def prepare_work_dir(requested=None):
    """Require a new/empty explicit path; reject links and protected ancestry."""
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="lumina-first-hit-check-"))
    candidate = Path(requested).absolute()
    for entry in (candidate, *candidate.parents):
        if not entry.exists() and not entry.is_symlink():
            continue
        info = entry.lstat()
        if entry.is_symlink() or (getattr(info, "st_file_attributes", 0)
                                 & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise CheckFailure("isolation", "work_dir_link_rejected")
    resolved = candidate.resolve()
    protected = (ROOT / "data", ROOT / ".git", ROOT / ".codex",
                 ROOT / "Conversation_Memory" / "upstream")
    if any(resolved == root or root in resolved.parents or resolved in root.parents
           for root in protected):
        raise CheckFailure("isolation", "work_dir_protected")
    if candidate.exists() and (not candidate.is_dir() or any(candidate.iterdir())):
        raise CheckFailure("isolation", "work_dir_not_empty")
    candidate.mkdir(parents=True, exist_ok=True)
    return resolved


# Fixed synthetic propositions are source-exact; this is orchestration checking,
# not a substitute for Formation semantic verification quality measurement.
_SOURCES = (
    ("check-old", "check-old-turn", "Mira studies Filter Amber.",
     ("Mira", "Filter Amber"), "Mira", "studies", "Filter Amber"),
    ("check-new", "check-new-turn", "Filter Amber retains fine dust.",
     ("Filter Amber",), "Filter Amber", "retains", "fine dust"),
)


def _formation_output(source):
    _sid, turn_id, text, names, subject, relation, value = source
    handles = {name: f"mention-{index}" for index, name in enumerate(names)}
    return {"mentions": [
        {"handle": handle, "surface": name, "turn_id": turn_id,
         "occurrence": 0, "identity": "named"}
        for name, handle in handles.items()
    ], "units": [{
        "text": text, "subject": subject, "relation": relation, "value": value,
        "source_refs": [{"turn_id": turn_id, "supporting_span": text}],
        "referenced_time": None, "subject_mention": handles[subject],
        "object_mention": handles.get(value), "mentions": list(handles.values()),
    }]}


class _FixedFormation:
    """Exercise the real extraction/verification interfaces with fixed outputs."""
    client_kind = "model"

    def __init__(self):
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        _require(recent_context == [], "formation", "unexpected_formation_context")
        payload = json.loads(user_message)
        if system_prompt.startswith("Extract source-grounded"):
            _require(len(payload["turns"]) == 1, "formation", "unexpected_source_window")
            turn = payload["turns"][0]
            for source in _SOURCES:
                if turn == {"turn_id": source[1], "role": "user", "text": source[2]}:
                    self.calls.append("extract")
                    return json.dumps(_formation_output(source))
            raise CheckFailure("formation", "unexpected_synthetic_source")
        _require(system_prompt.startswith("Verify ONLY"), "formation", "unexpected_model_operation")
        self.calls.append("verify")
        return json.dumps({
            "units": [{"supported": True} for _ in payload["units"]],
            "mentions": [{"supported": True, "identity_supported": True}
                         for _ in payload["mentions"]],
        })


def _source_turn(text, turn_id, minute=0, role="user"):
    return {"turn_id": turn_id, "role": role, "text": text,
            "created_at": f"2026-09-16T12:{minute:02d}:00+00:00",
            "source_timezone": "Asia/Shanghai", "timezone_source": "client"}


def _file_fingerprints(root):
    return {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def _link_snapshot(memory):
    return tuple(sorted((link.link_id, link.source_node_id, link.target_node_id,
                         json.dumps(link.properties, sort_keys=True))
                        for link in memory.backend.trg.graph_db.links.values()))


def _run(work_dir):
    memory_root = ROOT / "Conversation_Memory"
    if str(memory_root) not in sys.path:
        sys.path.insert(0, str(memory_root))
    from adapter.first_hit import FirstHitPolicy
    from adapter.grounded_formation import FORMATION_ENTITY_VERSION
    from adapter.models import RecallPolicy
    from core.cold_draft_store import ColdDraftStore
    from Dream.cold_draft_digest import ColdDraftDigestionTask, ColdDraftSegmentConverter
    from Dream.models import DreamRunPolicy
    from Dream.runner import DreamRunner, RealMemoryIngestorProvider

    policy = FirstHitPolicy(max_seeds=1)
    output = RecallPolicy(max_chars=8000, max_evidence_items=8)
    cold_path, state_path = work_dir / "cold.jsonl", work_dir / "ingestion_state.json"
    magma_path = work_dir / "magma"
    cold = ColdDraftStore(cold_path, source_window_segments=2)
    model = _FixedFormation()
    provider = RealMemoryIngestorProvider(magma_path, state_path, model,
                                          first_hit=policy, cold_store=cold)
    runner = DreamRunner(cold, ColdDraftDigestionTask(cold, provider))
    source_records = []
    writing_started = perf_counter()
    for index, source in enumerate(_SOURCES):
        record = cold.append_segment([_source_turn(source[2], source[1], index)],
                                     segment_id=source[0])
        source_records.append(record)
        report = runner.run_once(DreamRunPolicy(max_segments=1,
            ingestion_version=FORMATION_ENTITY_VERSION, stop_on_error=True))
        _require(report.consumed == 1 and report.failed == 0,
                 "manual_dream", "synthetic_ingestion_failed")
    write_seconds = perf_counter() - writing_started
    memory = provider.get(FORMATION_ENTITY_VERSION)
    _require(model.calls == ["extract", "verify"] * 2, "formation", "formation_call_count_invalid")
    stage = memory.state_store.get(memory.state_store.key("check-new", "first-hit-v1"))
    _require(stage and stage["status"] == "completed" and len(stage["link_plan"]) == 1,
             "links", "local_link_plan_missing")
    _require(all(memory.state_store.get(memory.state_store.key(source[0], version))["status"] == "completed"
                 for source in _SOURCES for version in (FORMATION_ENTITY_VERSION, "first-hit-v1")),
             "checkpoint", "combined_completion_missing")
    graph = memory.backend.trg.graph_db
    semantic = [link for link in graph.links.values()
                if getattr(link.link_type, "value", link.link_type) == "SEMANTIC"]
    _require(len(semantic) == 2 and all(link.properties.get("algorithm") == "first-hit-v1"
                                       for link in semantic), "links", "semantic_writer_not_replaced")
    _require(memory.backend.trg.llm_controller is None, "generation", "upstream_generator_enabled")
    snapshot = _link_snapshot(memory)
    converter = ColdDraftSegmentConverter()
    for record in source_records:
        result = memory.ingest(converter.convert(record, FORMATION_ENTITY_VERSION))
        _require(result.status == "completed" and result.already_ingested,
                 "retry", "completed_ingestion_retry_failed")
    _require(snapshot == _link_snapshot(memory) and len(model.calls) == 4,
             "retry", "retry_duplicated_work")
    memory.backend.persist()
    fingerprints = _file_fingerprints(work_dir)
    before = memory.recall_associative("Mira", output, include_sources=True)
    expected = {source[2] for source in _SOURCES}
    _require(before.safe_error_code is None, "association", "associative_channel_unavailable")
    _require({item.text for item in before.facts.evidence} == expected,
             "association", "associated_facts_missing")
    _require({item.text for item in before.sources.evidence} == expected,
             "source", "source_expansion_missing")
    diagnostics = dict(memory._last_first_hit_diagnostics)
    _require(diagnostics.get("seeds_returned") == 1 and diagnostics.get("edges_read", 0) > 0,
             "association", "graph_expansion_not_exercised")
    _require(_file_fingerprints(work_dir) == fingerprints, "read_only", "query_modified_storage")
    del graph, memory, runner, provider, cold
    gc.collect()
    restart_started = perf_counter()
    restarted_cold = ColdDraftStore(cold_path, source_window_segments=2)
    restarted_provider = RealMemoryIngestorProvider(magma_path, state_path, model,
        first_hit=policy, cold_store=restarted_cold)
    restarted = restarted_provider.get(FORMATION_ENTITY_VERSION)
    restart_seconds = perf_counter() - restart_started
    after = restarted.recall_associative("Mira", output, include_sources=True)
    _require(after == before and _link_snapshot(restarted) == snapshot,
             "restart", "restart_result_changed")
    _require(_file_fingerprints(work_dir) == fingerprints, "read_only", "restart_query_modified_storage")
    for index in range(2):
        restarted_cold.append_segment([_source_turn(
            "Could the Violet corridor be a useful nickname?", f"no-fact-{index}", index+2)],
            segment_id=f"no-fact-{index}")
    before_expired_read = _file_fingerprints(work_dir)
    expired = restarted.recall_associative("Mira", output, include_sources=True)
    _require({item.text for item in expired.facts.evidence} == expected,
             "expiry", "facts_lost_with_source_expiry")
    _require(not expired.sources.evidence and expired.sources.safe_error_code == "cold_source_unavailable",
             "expiry", "expired_source_still_visible")
    raw = restarted.recall_recent_sources("Violet corridor", output)
    _require(raw.evidence and raw.safe_error_code is None, "raw_lexical", "unformed_source_missing")
    _require(all(item.provenance.segment_id.startswith("no-fact-") for item in raw.evidence),
             "raw_lexical", "expired_postings_retained")
    _require(len(model.calls) == 4 and _file_fingerprints(work_dir) == before_expired_read,
             "read_only", "raw_query_modified_storage_or_generated")
    _require(len(expired.rendered_text) <= output.max_chars and len(after.rendered_text) <= output.max_chars,
             "budget", "combined_character_budget_exceeded")
    return {"status": "passed", "implementation": "first-hit-v1", "backend": "real_magma",
            "synthetic_segments_ingested": 2, "formation_fixture_calls": len(model.calls),
            "provider_generation_requests": 0, "facts_after_restart": len(after.facts.evidence),
            "local_semantic_links": len(semantic), "planned_logical_links": len(stage["link_plan"]),
            "retry_idempotent": True, "restart_equal": True, "query_read_only": True,
            "source_expiry_preserves_facts": True, "independent_raw_search": True,
            "source_utf8_bytes": sum(len(item.text.encode("utf-8")) for item in after.sources.evidence),
            "nodes_read": diagnostics.get("nodes_read"), "edges_read": diagnostics.get("edges_read"),
            "matrix_size": diagnostics.get("matrix_size"),
            "seed_search_seconds": diagnostics.get("seed_search_seconds"),
            "solve_seconds": diagnostics.get("solve_seconds"),
            "write_seconds": write_seconds, "restart_seconds": restart_seconds,
            "semantic_quality": "not_evaluated"}


def run_check(work_dir=None):
    directory = prepare_work_dir(work_dir)
    # MAGMA package import executes llm_judge's dotenv loader. This maintenance
    # process explicitly suppresses it and disables network/model downloads.
    # Real graph, FAISS, embedding, Formation interfaces and persistence remain.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
            patch.dict(os.environ, {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                                   "HF_HUB_DISABLE_TELEMETRY": "1",
                                   "OPENAI_API_KEY": "first-hit-check-placeholder"}), \
            patch("dotenv.load_dotenv", return_value=False), \
            patch("socket.socket.connect", side_effect=RuntimeError("network_disabled_for_check")):
        return _run(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Isolated real-MAGMA first-hit maintenance check")
    parser.add_argument("--work-dir", type=Path, default=None,
                        help="New or empty isolated directory; default creates a fresh temporary directory")
    args = parser.parse_args(argv)
    try:
        result = run_check(args.work_dir)
    except CheckFailure as error:
        result = {"status": "failed", "stage": error.stage, "safe_error_code": error.code}
    except Exception:
        result = {"status": "failed", "stage": "run", "safe_error_code": "first_hit_check_failed"}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
