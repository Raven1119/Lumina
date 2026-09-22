"""Frozen synthetic, real-provider / real-MAGMA acceptance; never production data.

Run with the prepared Conversation_Memory/.venv Python. --source-root can point
at a git-archive snapshot to keep baseline imports independent of working edits.
The output deliberately contains synthetic source/output for independent review.
It never serializes environment values, HTTP headers, or exception messages.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time


def _dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _source_fingerprints(source):
    paths = [p for area in ("adapter", "ingestion", "recall") for p in (source / "Conversation_Memory" / area).glob("*.py")]
    paths += [source / name for name in ("core/model_client.py", "core/contracts.py", "core/__init__.py", "Dream/runner.py") if (source / name).exists()]
    return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _finalize(report):
    calls = report["calls"]
    report["totals"] = {key: sum(s["scores"].get(key, 0) for s in report["scenarios"]) for key in ("facts_expected", "facts_matched", "relations_expected", "relations_matched", "attributes_expected", "attributes_matched", "events_emitted", "mentions_expected", "mentions_persisted", "query_complete", "query_count", "necessary_evidence_hits", "necessary_evidence_total")}
    report["totals"]["model_calls"] = len(calls)
    report["totals"]["new_provider_calls"] = sum(not c.get("reused_provider_output", False) for c in calls)
    report["totals"]["reused_provider_outputs"] = sum(c.get("reused_provider_output", False) for c in calls)
    report["totals"]["provider_elapsed_ms"] = round(sum(c["elapsed_ms"] for c in calls), 3)
    report["totals"]["new_provider_elapsed_ms"] = round(sum(c["elapsed_ms"] for c in calls if not c.get("reused_provider_output", False)), 3)
    for key in ("query_combined_complete", "necessary_mention_hits", "necessary_mention_total", "wrong_identity_evidence_hits", "unresolved_mentions", "forbidden_fact_count", "processing_failed_segments"):
        report["totals"][key] = sum(s["scores"].get(key, 0) for s in report["scenarios"])
    if report["scenarios"] and all("after_retry" in s for s in report["scenarios"]):
        metric_keys = [k for k, v in report["scenarios"][0]["scores"].items() if isinstance(v, int)]
        report["post_retry_totals"] = {k: sum(s["after_retry"]["scores"].get(k, 0) for s in report["scenarios"]) for k in metric_keys}
    usage = [h.get("usage", {}) for c in calls for h in c["http_responses"]]
    keys = {key for row in usage for key, value in row.items() if isinstance(value, (int, float))}
    report["totals"]["tokens"] = {key: sum(row.get(key, 0) for row in usage if isinstance(row.get(key, 0), (int, float))) for key in keys}
    new_usage = [h.get("usage", {}) for c in calls if not c.get("reused_provider_output", False) for h in c["http_responses"]]
    report["totals"]["new_provider_tokens"] = {key: sum(row.get(key, 0) for row in new_usage if isinstance(row.get(key, 0), (int, float))) for key in keys}
    report["complete"] = True
    report["measurement_only"] = "Surface and provenance checks are lower bounds; unmatched facts and semantic precision require source review. Complete means harness execution completed."


def _contains(value, alternatives):
    return any(item.casefold() in str(value).casefold() for item in alternatives)


def _fact_matches(node, expected):
    attrs = node["attributes"]
    refs = attrs.get("source_refs", [])
    if not any(ref.get("turn_id") == expected["turn"] for ref in refs):
        return False
    direct = _contains(attrs.get("subject", ""), expected["subject"]) and _contains(attrs.get("value", ""), expected["value"])
    inverse = expected.get("inverse")
    if inverse:
        direct = direct or (_contains(attrs.get("subject", ""), inverse["subject"]) and _contains(attrs.get("value", ""), inverse["value"]))
    if not direct or not _contains(attrs.get("relation", ""), expected["relation"]):
        return False
    if expected.get("polarity") == "negative":
        return bool(re.search(r"没有|未|不|\b(?:no|not|never|didn't)\b", node["text"], re.I))
    return True


def _category(prompt):
    text = prompt.casefold()
    if text.startswith("repair"):
        return "repair"
    if "select from the candidate" in text:
        return "mention_selection"
    if "fully entailed" in text or "verify" in text or "validate" in text:
        return "semantic_validation"
    if "entity mentions" in text and "atomic facts" not in text:
        return "mention_extraction"
    if "repair" in text:
        return "repair"
    if "extract" in text:
        return "formation_extraction"
    return "other"


def _inject_fault(output, rule):
    """Alter exactly one frozen target, never manufacture a missing candidate."""
    if not rule:
        return output, None
    raw = output.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.S | re.I)
    try:
        value = json.loads(fenced.group(1) if fenced else raw)
    except (ValueError, TypeError):
        return output, {"rule": rule, "applied": False, "reason": "provider_json_unparseable"}
    if not isinstance(value, dict):
        return output, {"rule": rule, "applied": False, "reason": "provider_not_object"}
    if rule["kind"] == "unit_source_span":
        targets = [(i, u) for i, u in enumerate(value.get("units", [])) if rule["subject_contains"].casefold() in str(u.get("subject", "")).casefold() and re.sub(r"\s+", "", str(u.get("value", ""))).casefold() == rule["value_without_spaces"].casefold() and u.get("source_refs")]
        if len(targets) == 1:
            index, target = targets[0]
            original = copy.deepcopy(target)
            target["source_refs"][0]["supporting_span"] = rule["replacement_span"]
    elif rule["kind"] == "mention_occurrence":
        targets = [(i, m) for i, m in enumerate(value.get("mentions", [])) if m.get("surface") == rule["surface"]]
        if len(targets) == 1:
            index, target = targets[0]
            original = copy.deepcopy(target)
            target["occurrence"] = rule["replacement_occurrence"]
    else:
        raise ValueError("unknown_frozen_fault_rule")
    if len(targets) != 1:
        return output, {"rule": rule, "applied": False, "reason": "target_not_unique", "target_count": len(targets)}
    return json.dumps(value, ensure_ascii=False), {"rule": rule, "applied": True, "target_index": index, "original_candidate": original, "presented_candidate": copy.deepcopy(target)}


class _AuditedModel:
    client_kind = "model"

    def __init__(self, client, calls, replay=(), replay_only=False):
        self.client, self.calls, self.current = client, calls, None
        self.replay = list(replay)
        self.replay_only = replay_only
        self.fault_rule = None
        self.scenario_id = None
        self.phase = "initial_ingest"
        self.forbid_new_extraction = False
        # Response hooks observe every actual HTTP response, including failures.
        client._http_client.event_hooks["response"].append(self._response)

    def _response(self, response):
        response.read()
        row = {"status": response.status_code}
        try:
            payload = response.json()
            row["usage"] = payload.get("usage", {})
            row["provider_model"] = payload.get("model")
            row["stop_reason"] = payload.get("stop_reason")
        except Exception:
            row["invalid_json"] = True
        if self.current is not None:
            self.current["http_responses"].append(row)

    def generate(self, recent_context, user_message, *, system_prompt):
        try:
            payload = json.loads(user_message)
        except ValueError:
            payload = user_message
        prompt_hash = hashlib.sha256(system_prompt.encode()).hexdigest()
        for index, previous in enumerate(self.replay):
            if previous.get("prompt_sha256") == prompt_hash and previous.get("input") == payload and "output" in previous:
                row = copy.deepcopy(self.replay.pop(index))
                row["reused_provider_output"] = True
                row.update(scenario_id=self.scenario_id, phase=self.phase)
                self.calls.append(row)
                return self._present(row)
        if self.replay_only:
            raise RuntimeError("replay_output_missing")
        if self.forbid_new_extraction and _category(system_prompt) == "formation_extraction":
            raise RuntimeError("new_extraction_forbidden_by_acceptance")
        row = {"category": _category(system_prompt), "prompt_sha256": prompt_hash, "input_chars": len(system_prompt) + len(user_message), "input": payload, "http_responses": [], "scenario_id": self.scenario_id, "phase": self.phase}
        self.calls.append(row)
        self.current = row
        started = time.perf_counter()
        try:
            result = self.client.generate(recent_context, user_message, system_prompt=system_prompt)
            row["output"] = result
            return self._present(row)
        except Exception as error:
            row["error_type"] = type(error).__name__
            raise
        finally:
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.current = None

    def _present(self, row):
        if self.fault_rule and row["category"] == "formation_extraction":
            output, audit = _inject_fault(row["output"], self.fault_rule)
            row["presented_output"], row["fault_injection"] = output, audit
            return output
        return row["output"]


def _snapshots(backend):
    from memory.graph_db import NodeType
    events, entities = [], []
    for node in backend.trg.graph_db.nodes.values():
        record = {"id": node.node_id, "text": getattr(node, "content", ""), "attributes": node.attributes}
        if node.node_type == NodeType.EVENT:
            if not record["text"]:
                record["text"] = node.attributes.get("grounded_memory_unit_text", "")
            events.append(record)
        elif node.node_type == NodeType.ENTITY:
            entities.append(record)
    return events, entities


def _observe_backend(backend, records):
    original = backend.recall

    def observed(*args, **kwargs):
        candidates = original(*args, **kwargs)
        records.append({"query": args[0] if args else kwargs.get("query"), "candidates": [asdict(c) for c in candidates], "stats": getattr(backend, "last_recall_stats", None)})
        return candidates

    backend.recall = observed


def _read_queries(adapter, scenario, policy):
    rows = []
    for query in scenario["queries"]:
        started = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            context = adapter.recall(query["query"], policy)
        row = {"id": query["id"], **asdict(context), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "injection_chars": len(context.rendered_text)}
        if query.get("required_mentions") and hasattr(adapter, "recall_mentions"):
            row["mentions"] = [asdict(m) for m in adapter.recall_mentions(query["query"]).mentions]
        rows.append(row)
    return rows


def _score(scenario, report):
    events = report["events"]
    matches = {fact["id"]: [node["attributes"].get("evidence_id") for node in events if _fact_matches(node, fact)] for fact in scenario["facts"]}
    evidence_nodes = {node["attributes"].get("evidence_id"): node for node in events}
    identities = []
    for check in scenario.get("identity_checks", []):
        value = dict(check)
        if check["kind"] in {"same", "different"}:
            a, b = matches.get(check["left_fact"], []), matches.get(check["right_fact"], [])
            left = evidence_nodes[a[0]]["attributes"].get("subject_entity_ref") if a else None
            right = evidence_nodes[b[0]]["attributes"].get("subject_entity_ref") if b else None
            value["observed"] = "unknown" if not left or not right else "same" if left == right else "different"
            value["passed"] = value["observed"] == check["kind"]
        elif check["kind"] == "unresolved":
            found = [m for m in report.get("mention_records", []) if m.get("surface") == check["surface"] and m.get("provenance", {}).get("turn_id") == check["turn"]]
            value["passed"] = bool(found) and all(not m.get("entity_ref") for m in found)
        else:
            ids = matches.get(check["fact"], [])
            ref = evidence_nodes[ids[0]]["attributes"].get("subject_entity_ref") if ids else None
            value["passed"] = bool(ref) and any(m.get("surface") == check["surface"] and m.get("entity_ref") == ref for m in report.get("mention_records", []))
        identities.append(value)
    mentions = []
    for expected in scenario["mentions"]:
        # Exact source surface/turn; multiple occurrences count as one expected pair.
        found = any(m.get("surface") == expected["surface"] and m.get("provenance", {}).get("turn_id") == expected["turn"] for m in report.get("mention_records", []))
        if not found:
            for node in events:
                attrs = node["attributes"]
                if not any(r.get("turn_id") == expected["turn"] for r in attrs.get("source_refs", [])):
                    continue
                bindings = attrs.get("mention_entity_refs", [])
                surfaces = [attrs.get("subject_entity_surface", "")]
                surfaces += attrs.get("mention_entity_surfaces", [])
                surfaces += [b.get("canonical_surface", b.get("surface", "")) for b in bindings if isinstance(b, dict)]
                if any(expected["surface"] == s for s in surfaces):
                    found = True
        mentions.append({**expected, "persisted": found})
    forbidden = []
    for rule in scenario.get("forbidden_facts", []):
        for node in events:
            if not any(r.get("turn_id") == rule["turn"] for r in node["attributes"].get("source_refs", [])):
                continue
            if rule.get("allow_qualified") and re.search(r"假如|如果|听说|尚未确认|未确认|\b(?:if|heard|unconfirmed)\b", node["text"], re.I):
                continue
            forbidden.append({"turn": rule["turn"], "evidence_id": node["attributes"].get("evidence_id"), "reason": rule["reason"]})
    for query, context in zip(scenario["queries"], report["recalls"]):
        selected = {e["evidence_id"] for e in context["evidence"]}
        context["required_fact_hits"] = {fid: bool(selected.intersection(matches[fid])) for fid in query["required_facts"]}
        context["complete"] = all(context["required_fact_hits"].values())
        if query.get("required_mentions"):
            context["complete"] = all(any(m["surface"] == surface for m in context.get("mentions", [])) for surface in query["required_mentions"])
        context["required_mention_hits"] = {surface: any(m["surface"] == surface for m in context.get("mentions", [])) for surface in query.get("required_mentions", [])}
        context["combined_complete"] = all(context["required_fact_hits"].values()) and all(context["required_mention_hits"].values())
        context["wrong_identity_evidence_ids"] = sorted({eid for fid in query.get("wrong_identity_facts", []) for eid in matches[fid] if eid in selected})
    object_checks = []
    for fact in scenario["facts"]:
        if "object" not in fact:
            continue
        ids = matches[fact["id"]]
        attrs = evidence_nodes[ids[0]]["attributes"] if ids else {}
        object_checks.append({"fact": fact["id"], "explicit_object_bound": bool(attrs.get("object_entity_ref"))})
    matched_ids = {eid for ids in matches.values() for eid in ids}
    relation_ids = {fact["id"] for fact in scenario["facts"] if "object" in fact}
    scores = {"facts_expected": len(matches), "facts_matched": sum(bool(x) for x in matches.values()), "relations_expected": len(relation_ids), "relations_matched": sum(bool(matches[fid]) for fid in relation_ids), "attributes_expected": len(matches) - len(relation_ids), "attributes_matched": sum(bool(ids) for fid, ids in matches.items() if fid not in relation_ids), "fact_evidence_ids": matches, "events_emitted": len(events), "unmatched_event_ids_for_manual_review": [eid for eid in evidence_nodes if eid not in matched_ids], "mentions_expected": len(mentions), "mentions_persisted": sum(m["persisted"] for m in mentions), "mention_checks": mentions, "identity_checks": identities, "object_checks": object_checks, "forbidden_certain_facts": forbidden, "query_complete": sum(r["complete"] for r in report["recalls"]), "query_count": len(report["recalls"]), "necessary_evidence_hits": sum(sum(r["required_fact_hits"].values()) for r in report["recalls"]), "necessary_evidence_total": sum(len(r["required_fact_hits"]) for r in report["recalls"])}
    scores.update(
        query_combined_complete=sum(r["combined_complete"] for r in report["recalls"]),
        necessary_mention_hits=sum(sum(r["required_mention_hits"].values()) for r in report["recalls"]),
        necessary_mention_total=sum(len(r["required_mention_hits"]) for r in report["recalls"]),
        wrong_identity_evidence_hits=sum(len(r["wrong_identity_evidence_ids"]) for r in report["recalls"]),
        unresolved_mentions=sum(not m.get("entity_ref") for m in report.get("mention_records", [])),
        forbidden_fact_count=len(forbidden),
        processing_failed_segments=sum(i["status"] != "completed" for i in report.get("ingestion", [])),
    )
    return scores


def _state_context(args, fixture_sha256):
    if args.state_root is None:
        if args.resume_state:
            raise ValueError("resume_requires_task_state_root")
        return tempfile.TemporaryDirectory(prefix="lumina-entity-acceptance-")
    root = args.state_root.resolve()
    allowed_parent = (args.config_root / "Conversation_Memory/tests").resolve()
    if root.parent != allowed_parent or not root.name.startswith(".memory_reliability_state_"):
        raise ValueError("state_root_must_be_task_owned_test_directory")
    marker = {"schema": "memory-reliability-isolated-state-v1", "fixture_sha256": fixture_sha256}
    marker_path = root / "acceptance_state_marker.json"
    if args.resume_state:
        if json.loads(marker_path.read_text(encoding="utf-8")) != marker:
            raise ValueError("state_marker_mismatch")
    else:
        root.mkdir(exist_ok=False)
        _dump(marker_path, marker)
    return contextlib.nullcontext(str(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--config-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--fixture", type=Path, default=Path(__file__).parent / "fixtures/entity_memory_holdout.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--replay", type=Path, help="Reuse exact successful provider outputs from an interrupted harness run; no resampling.")
    parser.add_argument("--replay-only", action="store_true", help="Forbid new provider calls; missing exact replay output fails safely.")
    parser.add_argument("--retry-failed-once", action="store_true", help="Observe one real restart retry of failed segments; retain every attempt and call.")
    parser.add_argument("--recall-after-retry", action="store_true", help="Also measure public recalls after the one explicit retry, retaining initial metrics separately.")
    parser.add_argument("--state-root", type=Path, help="Explicit task-owned test state retained for a later public-ingest checkpoint continuation.")
    parser.add_argument("--resume-state", action="store_true", help="Resume only an existing matching task marker; never create or use production state.")
    parser.add_argument("--forbid-new-extraction", action="store_true", help="Allow new repair/verification calls but never resample an extraction window.")
    parser.add_argument("--max-output-tokens", type=int, help="Explicit development budget; otherwise use the source checkout's Dream budget (legacy snapshot: 2000).")
    args = parser.parse_args()
    if args.recall_after_retry and not args.retry_failed_once:
        parser.error("--recall-after-retry requires --retry-failed-once")
    source = args.source_root.resolve()
    # Explicit historical checkouts still use organ-local imports internally.
    # Keep their path compatibility in this cross-checkout CLI only.
    sys.path[:0] = [str(source / "Conversation_Memory"), str(source)]
    from dotenv import dotenv_values
    from core.model_client import build_model_client_from_env, DEEPSEEK_MODEL
    from Conversation_Memory.adapter.backend import RealMagmaBackend
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from Conversation_Memory.adapter.models import ColdDraftSegment, ColdDraftTurn, RecallPolicy
    from Conversation_Memory.adapter import grounded_formation
    from Conversation_Memory.ingestion.state_store import IngestionStateStore
    fixture_bytes = args.fixture.read_bytes()
    fixture = json.loads(fixture_bytes)
    env = {**dotenv_values(args.config_root / ".env.local"), **os.environ}
    dream_source = source / "Dream/runner.py"
    configured_budget = re.search(r"_FORMATION_MAX_TOKENS\s*=\s*(\d+)", dream_source.read_text(encoding="utf-8")) if dream_source.exists() else None
    max_output_tokens = args.max_output_tokens or (int(configured_budget.group(1)) if configured_budget else 2000)
    client = build_model_client_from_env(env, max_tokens_override=max_output_tokens)
    if getattr(client, "client_kind", None) != "model":
        raise SystemExit("real_provider_configuration_missing")
    calls = []
    replay = json.loads(args.replay.read_text(encoding="utf-8"))["calls"] if args.replay else []
    model = _AuditedModel(client, calls, replay, args.replay_only)
    model.forbid_new_extraction = args.forbid_new_extraction
    version = getattr(grounded_formation, "FORMATION_ENTITY_VERSION", grounded_formation.FORMATION_VERSION)
    report = {"schema": "entity-memory-acceptance-v1", "label": args.label, "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(), "runtime_model": DEEPSEEK_MODEL, "max_output_tokens": max_output_tokens, "ingestion_version": version, "policy": fixture["policy"], "source_files_sha256": _source_fingerprints(source), "calls": calls, "scenarios": []}
    _dump(args.output, report)
    report["resumed_isolated_state"] = args.resume_state
    with _state_context(args, report["fixture_sha256"]) as sandbox_name:
        sandbox = Path(sandbox_name)
        for scenario in fixture["scenarios"]:
            model.fault_rule = scenario.get("fault_injection")
            model.scenario_id = scenario["id"]
            model.phase = "initial_ingest"
            print("RUN " + args.label + " " + scenario["id"], flush=True)
            local = sandbox / scenario["id"]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                backend = RealMagmaBackend(local / "magma", upstream_dir=args.config_root / "Conversation_Memory/upstream/MAGMA")
            adapter = MagmaMemoryAdapter(backend, IngestionStateStore(local / "state.json"), ingestion_version=version, formation_model=model)
            item = {"id": scenario["id"], "experiment_kind": scenario.get("experiment_kind", "natural_real_provider"), "ingestion": [], "recalls": []}
            if args.resume_state:
                item["initial_checkpoint"] = IngestionStateStore(local / "state.json").read_all()
                item["initial_events"], item["initial_entities"] = _snapshots(backend)
            item["candidate_calls"] = []
            _observe_backend(backend, item["candidate_calls"])
            report["scenarios"].append(item)
            segments = []
            for index, source_turns in enumerate(scenario["segments"]):
                turns = tuple(ColdDraftTurn(turn["id"], turn["role"], turn["text"], datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=index * 10 + i), "UTC", "client") for i, turn in enumerate(source_turns))
                segment = ColdDraftSegment(f"holdout-{scenario['id']}-{index}", f"holdout-{scenario['id']}", "pending_digest", turns, turns[0].timestamp, "UTC", "2")
                segments.append(segment)
                started = time.perf_counter()
                before = len(calls)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    result = adapter.ingest(segment)
                item["ingestion"].append({**asdict(result), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "model_calls": len(calls) - before})
                _dump(args.output, report)
            item["events"], item["entities"] = _snapshots(backend)
            item["checkpoint"] = IngestionStateStore(local / "state.json").read_all()
            item["mention_records"] = []
            for checkpoint in item["checkpoint"].values():
                item["mention_records"].extend(checkpoint.get("mentions", []))
            policy = RecallPolicy(**fixture["policy"])
            item["recalls"] = _read_queries(adapter, scenario, policy)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                restarted_backend = RealMagmaBackend(local / "magma", upstream_dir=args.config_root / "Conversation_Memory/upstream/MAGMA")
            restarted = MagmaMemoryAdapter(restarted_backend, IngestionStateStore(local / "state.json"), ingestion_version=version, formation_model=model)
            item["restart_candidate_calls"] = []
            _observe_backend(restarted_backend, item["restart_candidate_calls"])
            # Reuse the same fixed read-only BGE scorer; backend/state really reload.
            restarted._bge_reranker = adapter._bge_reranker
            restarted._bge_reranker_load_attempted = adapter._bge_reranker_load_attempted
            item["restart_recall_identical"] = True
            for query, previous in zip(scenario["queries"], item["recalls"]):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    current = asdict(restarted.recall(query["query"], policy))
                item["restart_recall_identical"] &= all(current[k] == previous[k] for k in current)
            before = len(calls)
            count_before = len(restarted_backend.trg.graph_db.nodes)
            state_before = (local / "state.json").read_bytes() if (local / "state.json").exists() else None
            completed = [segment for segment, result in zip(segments, item["ingestion"]) if result["status"] == "completed"]
            item["duplicate_results"] = [asdict(restarted.ingest(segment)) for segment in completed]
            item["duplicate_no_new_calls"] = len(calls) == before
            item["duplicate_no_new_nodes"] = len(restarted_backend.trg.graph_db.nodes) == count_before
            item["duplicate_state_unchanged"] = ((local / "state.json").read_bytes() if (local / "state.json").exists() else None) == state_before
            item["scores"] = _score(scenario, item)
            if args.retry_failed_once:
                model.phase = "failed_retry_after_restart"
                item["failed_retries"] = []
                for segment, initial in zip(segments, item["ingestion"]):
                    if initial["status"] == "completed":
                        continue
                    before = len(calls)
                    nodes_before = set(restarted_backend.trg.graph_db.nodes)
                    checkpoint_before = IngestionStateStore(local / "state.json").read_all()
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        retry = restarted.ingest(segment)
                    item["failed_retries"].append({"initial_error": initial.get("safe_error_code"), "result": asdict(retry), "model_calls": len(calls) - before, "nodes_added": sorted(set(restarted_backend.trg.graph_db.nodes) - nodes_before), "checkpoint_before": checkpoint_before, "checkpoint_after": IngestionStateStore(local / "state.json").read_all()})
                item["after_retry_events"], item["after_retry_entities"] = _snapshots(restarted_backend)
                if args.recall_after_retry:
                    calls_before = len(item["restart_candidate_calls"])
                    checkpoint = IngestionStateStore(local / "state.json").read_all()
                    retried = {x["result"]["segment_id"]: x["result"] for x in item["failed_retries"]}
                    post = {"events": item["after_retry_events"], "entities": item["after_retry_entities"], "checkpoint": checkpoint, "mention_records": [m for c in checkpoint.values() for m in c.get("mentions", [])], "ingestion": [retried.get(x["segment_id"], x) for x in item["ingestion"]], "recalls": _read_queries(restarted, scenario, policy)}
                    post["candidate_calls"] = item["restart_candidate_calls"][calls_before:]
                    post["scores"] = _score(scenario, post)
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        final_backend = RealMagmaBackend(local / "magma", upstream_dir=args.config_root / "Conversation_Memory/upstream/MAGMA")
                    final_adapter = MagmaMemoryAdapter(final_backend, IngestionStateStore(local / "state.json"), ingestion_version=version, formation_model=model)
                    final_adapter._bge_reranker = adapter._bge_reranker
                    final_adapter._bge_reranker_load_attempted = adapter._bge_reranker_load_attempted
                    post["restart_recall_identical"] = True
                    for query, previous in zip(scenario["queries"], post["recalls"]):
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            current = asdict(final_adapter.recall(query["query"], policy))
                        post["restart_recall_identical"] &= all(current[k] == previous[k] for k in current)
                    before_calls, before_nodes = len(calls), len(final_backend.trg.graph_db.nodes)
                    before_state = (local / "state.json").read_bytes()
                    final_completed = [seg for seg, result in zip(segments, post["ingestion"]) if result["status"] == "completed"]
                    post["duplicate_results"] = [asdict(final_adapter.ingest(seg)) for seg in final_completed]
                    post["duplicate_no_new_calls"] = len(calls) == before_calls
                    post["duplicate_no_new_nodes"] = len(final_backend.trg.graph_db.nodes) == before_nodes
                    post["duplicate_state_unchanged"] = (local / "state.json").read_bytes() == before_state
                    item["after_retry"] = post
            _dump(args.output, report)
            print("DONE " + scenario["id"] + " facts=" + str(item["scores"]["facts_matched"]) + "/" + str(item["scores"]["facts_expected"]), flush=True)
    _finalize(report)
    _dump(args.output, report)
    print(json.dumps(report["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
