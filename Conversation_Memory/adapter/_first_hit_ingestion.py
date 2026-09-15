"""Recoverable local association stage in the existing ingestion state owner.

Formation retains its own strict v2 checkpoint. This independent version key
reserves new work before Formation starts and freezes each verified batch's
association plan before any of that batch's graph mutations.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict

from .grounded_formation import FORMATION_ENTITY_VERSION


FIRST_HIT_VERSION = "first-hit-v1"


class FirstHitIngestionError(RuntimeError):
    def __init__(self, code, *, retryable=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _plan_digest(evidence_ids, link_plan):
    encoded = json.dumps([evidence_ids, link_plan], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _put(adapter, stage):
    try:
        adapter.state_store.put(
            adapter.state_store.key(stage["segment_id"], FIRST_HIT_VERSION), stage,
        )
    except Exception as error:
        raise FirstHitIngestionError("first_hit_state_write_failed", retryable=True) from error


def _valid_number(value):
    return type(value) in {int, float} and math.isfinite(value) and 0 < value <= 1


def _validate_stage(stage, segment, digest, profile):
    if (
        set(stage) != {"schema_version", "formation_version", "segment_id",
                       "source_digest", "profile", "status", "evidence_ids",
                       "link_plan", "plan_digest"}
        or stage["schema_version"] != FIRST_HIT_VERSION
        or stage["formation_version"] != FORMATION_ENTITY_VERSION
        or stage["segment_id"] != segment.segment_id
        or stage["source_digest"] != digest
        or stage["profile"] != profile
        or stage["status"] not in {"pending", "planned", "completed"}
    ):
        raise FirstHitIngestionError("first_hit_checkpoint_invalid")
    ids, plan = stage["evidence_ids"], stage["link_plan"]
    if (not isinstance(ids, list) or not all(isinstance(item, str) and item for item in ids)
            or len(set(ids)) != len(ids) or not isinstance(plan, list)
            or (stage["status"] == "pending" and (ids or plan))):
        raise FirstHitIngestionError("first_hit_checkpoint_invalid")
    pairs, counts = set(), {}
    for link in plan:
        if (not isinstance(link, dict)
                or set(link) != {"source_evidence_id", "target_evidence_id", "weight",
                                 "algorithm", "first_hit", "link_score"}):
            raise FirstHitIngestionError("first_hit_checkpoint_invalid")
        source, target = link["source_evidence_id"], link["target_evidence_id"]
        if (not isinstance(source, str) or source not in ids
                or not isinstance(target, str) or not target or target in ids
                or link["algorithm"] != FIRST_HIT_VERSION
                or not all(_valid_number(link[field])
                           for field in ("weight", "first_hit", "link_score"))
                or not math.isclose(link["link_score"], link["weight"] * link["first_hit"],
                                    rel_tol=1e-12, abs_tol=1e-15)
                or (source, target) in pairs):
            raise FirstHitIngestionError("first_hit_checkpoint_invalid")
        pairs.add((source, target))
        counts[source] = counts.get(source, 0) + 1
        if counts[source] > min(profile["max_links"], 3):
            raise FirstHitIngestionError("first_hit_checkpoint_invalid")
    if stage["plan_digest"] != _plan_digest(ids, plan):
        raise FirstHitIngestionError("first_hit_checkpoint_invalid")


def load_first_hit_stage(adapter, segment, digest, *, new_formation):
    """Reserve only genuinely new Formation work; never migrate old records."""
    policy = getattr(adapter, "first_hit", None)
    try:
        stage = adapter.state_store.get(
            adapter.state_store.key(segment.segment_id, FIRST_HIT_VERSION),
        )
    except Exception as error:
        retryable = isinstance(error, OSError) or isinstance(error.__cause__, OSError)
        raise FirstHitIngestionError(
            "first_hit_state_read_failed" if retryable else "first_hit_checkpoint_invalid",
            retryable=retryable,
        ) from error
    if stage is None and (policy is None or not new_formation):
        return None
    if policy is None:
        raise FirstHitIngestionError("first_hit_configuration_required")
    profile = asdict(policy)
    if stage is None:
        stage = {
            "schema_version": FIRST_HIT_VERSION,
            "formation_version": FORMATION_ENTITY_VERSION,
            "segment_id": segment.segment_id, "source_digest": digest,
            "profile": profile, "status": "pending", "evidence_ids": [],
            "link_plan": [], "plan_digest": _plan_digest([], []),
        }
        # Saving this reservation first closes the crash window between
        # starting v2 and recognizing its association completion responsibility.
        _put(adapter, stage)
    try:
        _validate_stage(stage, segment, digest, profile)
    except FirstHitIngestionError:
        raise
    except Exception as error:
        raise FirstHitIngestionError("first_hit_checkpoint_invalid") from error
    return stage


def prepare_first_hit_stage(adapter, stage, batch, mention_records):
    """Freeze plans for all new facts, excluding the complete repaired batch."""
    ids = [unit.id for unit in batch.units]
    previous = stage["evidence_ids"]
    if ids[:len(previous)] != previous:
        raise FirstHitIngestionError("first_hit_checkpoint_invalid")
    if stage["status"] != "pending" and ids == previous:
        return
    records = {record["mention_id"]: record for record in mention_records}
    roles = {item.unit_id: item for item in batch.unit_mentions}
    plan = list(stage["link_plan"])
    if any(link["target_evidence_id"] in ids for link in plan):
        raise FirstHitIngestionError("first_hit_checkpoint_invalid")
    try:
        for unit in batch.units[len(previous):]:
            role = roles[unit.id]
            mention_ids = tuple(dict.fromkeys((role.subject, role.object, *role.mentions)))
            refs = tuple(dict.fromkeys(
                records[mid]["entity_ref"] for mid in mention_ids
                if mid in records and records[mid]["entity_ref"]
            ))
            activation = adapter._activate_first_hit(
                unit.text, target_entity_refs=refs, exclude_evidence_ids=tuple(ids),
            )
            if activation.safe_error_code:
                raise FirstHitIngestionError("first_hit_activation_unavailable", retryable=True)
            endpoints = []
            for candidate, first_hit, _attention in activation.candidates:
                target = candidate.metadata.get("evidence_id")
                if not isinstance(target, str) or not target or target in ids or first_hit <= 0:
                    continue
                memory_id = adapter.backend.find_memory_id(target)
                if memory_id is None or not _valid_number(first_hit):
                    raise FirstHitIngestionError("first_hit_activation_unavailable", retryable=True)
                endpoints.append((target, memory_id, first_hit))
            # Encode the new fact once for the entire bounded candidate set.
            similarities = adapter.backend.endpoint_similarities(
                unit.text, tuple(dict.fromkeys(mid for _, mid, _ in endpoints)),
            ) if endpoints else {}
            candidates = {}
            for target, memory_id, first_hit in endpoints:
                similarity = similarities[memory_id]
                if similarity == 0:
                    continue
                if not _valid_number(similarity):
                    raise FirstHitIngestionError("first_hit_activation_unavailable", retryable=True)
                link_score = first_hit * similarity
                if link_score <= 0:
                    continue
                link = {"source_evidence_id": unit.id, "target_evidence_id": target,
                        "weight": similarity, "algorithm": FIRST_HIT_VERSION,
                        "first_hit": first_hit, "link_score": link_score}
                if target not in candidates or link_score > candidates[target]["link_score"]:
                    candidates[target] = link
            ranked = sorted(candidates.values(), key=lambda item: (
                -item["link_score"], -item["first_hit"], item["target_evidence_id"],
            ))
            plan.extend(ranked[:min(stage["profile"]["max_links"], 3)])
    except FirstHitIngestionError:
        raise
    except Exception as error:
        raise FirstHitIngestionError("first_hit_planning_failed", retryable=True) from error
    updated = {**stage, "evidence_ids": ids, "link_plan": plan,
               "plan_digest": _plan_digest(ids, plan), "status": "planned"}
    _put(adapter, updated)
    stage.update(updated)


def complete_first_hit_stage(adapter, stage):
    """Persist idempotent edges before completing the combined ingestion."""
    if stage["status"] == "completed":
        return
    if stage["status"] != "planned":
        raise FirstHitIngestionError("first_hit_checkpoint_invalid")
    try:
        adapter.backend.apply_first_hit_links(stage["link_plan"])
        adapter.backend.persist()
    except Exception as error:
        raise FirstHitIngestionError("first_hit_link_write_failed", retryable=True) from error
    updated = {**stage, "status": "completed"}
    _put(adapter, updated)
    stage.update(updated)
