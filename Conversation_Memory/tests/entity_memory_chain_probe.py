"""Provider-free score-input / packaging contrast over captured A candidates.

No retrieval, writes, prompt changes, or new gold. B2 explicitly changes the
fixed BGE scoring text for a proven two-fact path, then applies the original
Hindsight formula and inclusive floor. It is a diagnostic, not production code.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.source_root / "Conversation_Memory"), str(args.source_root)]
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from adapter.models import BackendCandidate, SourceProvenance
    from adapter.magma_adapter import _source_timestamp, _candidate_snapshot_reference_time
    from adapter.user_self import candidate_entity_ref, entity_marked_text
    from recall.bge_reranker import BgeReranker, BGE_MODEL, BGE_REVISION, BGE_MAX_LENGTH
    from recall.hindsight_scoring import score_hindsight_post_rerank
    source = json.loads(args.input.read_text(encoding="utf-8"))
    scorer = BgeReranker()
    results = []

    def identity(candidate):
        return candidate.metadata.get("evidence_id")

    def score_explicit_views(query, target_ref, candidates):
        # Keep A's scoring texts explicit even after production adopts B2.
        # This is only the pre-existing per-pair entity marker projection.
        values = [None] * len(candidates)
        for marked in [False, True]:
            indices = [i for i, c in enumerate(candidates) if (target_ref is not None and candidate_entity_ref(c.metadata) == target_ref) == marked]
            if not indices:
                continue
            projected_query = entity_marked_text(target_ref, query) if marked else query
            texts = [entity_marked_text(target_ref, candidates[i].text) if marked else candidates[i].text for i in indices]
            for i, score in zip(indices, scorer.score(projected_query, texts), strict=True):
                values[i] = score
        return tuple(values)

    def pack(ranked, candidates, chains):
        by_id = {identity(c): c for c in candidates}
        selected, seen, chars, truncated = [], set(), 0, False
        for index in ranked:
            candidate = candidates[index]
            eid = identity(candidate)
            ids = chains.get(eid, [eid])
            pending = [mid for mid in ids if mid not in seen]
            texts = ["[" + ("USER" if by_id[mid].metadata["provenance"]["source_role"] == "user" else "LUMINA") + "]\n" + by_id[mid].text for mid in pending]
            extra = sum(map(len, texts)) + max(0, len(texts) - 1) + bool(selected and pending)
            if len(selected) + len(pending) > source["policy"]["max_evidence_items"] or chars + extra > source["policy"]["max_chars"]:
                truncated = True
                continue
            selected.extend(pending)
            seen.update(pending)
            chars += extra
        return {"selected": selected, "chars": chars, "truncated": truncated}

    for scenario in source["scenarios"]:
        contexts = {q["query"]: q for q in scenario["recalls"]}
        for row in scenario["candidate_calls"]:
            candidates = [BackendCandidate(**c) for c in row["candidates"]]
            by_id = {identity(c): c for c in candidates}
            chains = {}
            for c in candidates:
                chain = c.metadata.get("association_chain_evidence_ids", [])
                if len(chain) != 2 or chain[-1] != identity(c) or any(mid not in by_id for mid in chain):
                    continue
                left, right = [by_id[mid] for mid in chain]
                roles = [{node.metadata.get("subject_entity_ref"), node.metadata.get("object_entity_ref")} for node in [left, right]]
                if None in roles[0] or None in roles[1] or not roles[0].intersection(roles[1]):
                    continue
                for node in [left, right]:
                    SourceProvenance(**node.metadata["provenance"])
                    if not node.metadata.get("source_refs"):
                        raise ValueError("chain_source_refs_missing")
                chains[identity(c)] = chain
            if not chains:
                continue
            refs = {m["entity_ref"] for m in scenario["mention_records"] if m.get("entity_ref") and m["surface"] in row["query"]}
            target_ref = next(iter(refs)) if len(refs) == 1 else None
            timestamps = tuple(_source_timestamp(c) for c in candidates)
            now = _candidate_snapshot_reference_time(timestamps)
            raw_a = score_explicit_views(row["query"], target_ref, candidates)
            scores_a = score_hindsight_post_rerank(raw_a, timestamps, now=now)
            views = [replace(c, text="\n".join(by_id[mid].text for mid in chains[identity(c)])) if identity(c) in chains else c for c in candidates]
            raw_b2 = score_explicit_views(row["query"], target_ref, views)
            scores_b2 = score_hindsight_post_rerank(raw_b2, timestamps, now=now)
            floor = source["policy"]["final_min_score"]
            def ranked(scores):
                return sorted((i for i, s in enumerate(scores) if floor is None or s.final_score >= floor), key=lambda i: (-scores[i].final_score, i))
            arms = {"A": pack(ranked(scores_a), candidates, {}), "B1": pack(ranked(scores_a), candidates, chains), "B2": pack(ranked(scores_b2), candidates, chains)}
            expected_ids = {mid for chain in chains.values() for mid in chain}
            for arm in arms.values():
                arm["chain_complete"] = expected_ids.issubset(arm["selected"])
            original = contexts[row["query"]]
            arms["A"]["reproduces_original_selected"] = arms["A"]["selected"] == [e["evidence_id"] for e in original["evidence"]]
            results.append({"scenario": scenario["id"], "query": row["query"], "candidates": [{"id": identity(c), "text": c.text, "b2_scoring_text": views[i].text, "a": asdict(scores_a[i]), "b2": asdict(scores_b2[i])} for i, c in enumerate(candidates)], "arms": arms})
    result = {"schema": "entity-memory-chain-score-probe-v1", "provider_calls": 0, "retrieval_calls": 0, "fixture_sha256": source["fixture_sha256"], "model": BGE_MODEL, "revision": BGE_REVISION, "max_length": BGE_MAX_LENGTH, "policy": source["policy"], "changed_variable": "B1 adds bridge packaging only; B2 additionally scores terminal candidates on literal complete two-fact chain text. Other candidate scores and all weights/floors remain fixed.", "queries": results}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{ "scenario": r["scenario"], "arms": r["arms"]} for r in results], ensure_ascii=False))


if __name__ == "__main__":
    main()
