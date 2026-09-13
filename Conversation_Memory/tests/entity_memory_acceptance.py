"""Frozen synthetic, real-provider / real-MAGMA acceptance; never production data.

Run with Conversation_Memory/.venv/Scripts/python.exe. --source-root can point
at a git-archive snapshot to keep baseline imports independent of working edits.
The output deliberately contains synthetic source/output for independent review.
It never serializes environment values, HTTP headers, or exception messages.
"""
from __future__ import annotations

import argparse
import contextlib
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


class _AuditedModel:
    client_kind = "model"

    def __init__(self, client, calls, replay=(), replay_only=False):
        self.client, self.calls, self.current = client, calls, None
        self.replay = list(replay)
        self.replay_only = replay_only
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
                row = self.replay.pop(index)
                row["reused_provider_output"] = True
                self.calls.append(row)
                return row["output"]
        if self.replay_only:
            raise RuntimeError("replay_output_missing")
        row = {"category": _category(system_prompt), "prompt_sha256": prompt_hash, "input_chars": len(system_prompt) + len(user_message), "input": payload, "http_responses": []}
        self.calls.append(row)
        self.current = row
        started = time.perf_counter()
        try:
            result = self.client.generate(recent_context, user_message, system_prompt=system_prompt)
            row["output"] = result
            return result
        except Exception as error:
            row["error_type"] = type(error).__name__
            raise
        finally:
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.current = None


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
    object_checks = []
    for fact in scenario["facts"]:
        if "object" not in fact:
            continue
        ids = matches[fact["id"]]
        attrs = evidence_nodes[ids[0]]["attributes"] if ids else {}
        object_checks.append({"fact": fact["id"], "explicit_object_bound": bool(attrs.get("object_entity_ref"))})
    matched_ids = {eid for ids in matches.values() for eid in ids}
    relation_ids = {fact["id"] for fact in scenario["facts"] if "object" in fact}
    return {"facts_expected": len(matches), "facts_matched": sum(bool(x) for x in matches.values()), "relations_expected": len(relation_ids), "relations_matched": sum(bool(matches[fid]) for fid in relation_ids), "attributes_expected": len(matches) - len(relation_ids), "attributes_matched": sum(bool(ids) for fid, ids in matches.items() if fid not in relation_ids), "fact_evidence_ids": matches, "events_emitted": len(events), "unmatched_event_ids_for_manual_review": [eid for eid in evidence_nodes if eid not in matched_ids], "mentions_expected": len(mentions), "mentions_persisted": sum(m["persisted"] for m in mentions), "mention_checks": mentions, "identity_checks": identities, "object_checks": object_checks, "forbidden_certain_facts": forbidden, "query_complete": sum(r["complete"] for r in report["recalls"]), "query_count": len(report["recalls"]), "necessary_evidence_hits": sum(sum(r["required_fact_hits"].values()) for r in report["recalls"]), "necessary_evidence_total": sum(len(r["required_fact_hits"]) for r in report["recalls"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--config-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--fixture", type=Path, default=Path(__file__).parent / "fixtures/entity_memory_holdout.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--replay", type=Path, help="Reuse exact successful provider outputs from an interrupted harness run; no resampling.")
    parser.add_argument("--replay-only", action="store_true", help="Forbid new provider calls; missing exact replay output fails safely.")
    parser.add_argument("--max-output-tokens", type=int, help="Explicit development budget; otherwise use the source checkout's Dream budget (legacy snapshot: 2000).")
    args = parser.parse_args()
    source = args.source_root.resolve()
    sys.path[:0] = [str(source / "Conversation_Memory"), str(source)]
    from dotenv import dotenv_values
    from core.model_client import build_model_client_from_env, DEEPSEEK_MODEL
    from adapter.backend import RealMagmaBackend
    from adapter.magma_adapter import MagmaMemoryAdapter
    from adapter.models import ColdDraftSegment, ColdDraftTurn, RecallPolicy
    from adapter import grounded_formation
    from ingestion.state_store import IngestionStateStore
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
    version = getattr(grounded_formation, "FORMATION_ENTITY_VERSION", grounded_formation.FORMATION_VERSION)
    report = {"schema": "entity-memory-acceptance-v1", "label": args.label, "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(), "runtime_model": DEEPSEEK_MODEL, "max_output_tokens": max_output_tokens, "ingestion_version": version, "policy": fixture["policy"], "source_files_sha256": _source_fingerprints(source), "calls": calls, "scenarios": []}
    _dump(args.output, report)
    with tempfile.TemporaryDirectory(prefix="lumina-entity-acceptance-") as sandbox_name:
        sandbox = Path(sandbox_name)
        for scenario in fixture["scenarios"]:
            print("RUN " + args.label + " " + scenario["id"], flush=True)
            local = sandbox / scenario["id"]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                backend = RealMagmaBackend(local / "magma", upstream_dir=args.config_root / "Conversation_Memory/upstream/MAGMA")
            adapter = MagmaMemoryAdapter(backend, IngestionStateStore(local / "state.json"), ingestion_version=version, formation_model=model)
            item = {"id": scenario["id"], "ingestion": [], "recalls": []}
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
            for query in scenario["queries"]:
                started = time.perf_counter()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    context = adapter.recall(query["query"], policy)
                row = {"id": query["id"], **asdict(context), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "injection_chars": len(context.rendered_text)}
                if query.get("required_mentions") and hasattr(adapter, "recall_mentions"):
                    row["mentions"] = [asdict(m) for m in adapter.recall_mentions(query["query"]).mentions]
                item["recalls"].append(row)
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
            _dump(args.output, report)
            print("DONE " + scenario["id"] + " facts=" + str(item["scores"]["facts_matched"]) + "/" + str(item["scores"]["facts_expected"]), flush=True)
    _finalize(report)
    _dump(args.output, report)
    print(json.dumps(report["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
