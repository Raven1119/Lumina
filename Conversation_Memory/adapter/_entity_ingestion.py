"""Versioned Formation progress in the existing ingestion checkpoint owner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .entity_consolidation import EntityBinding, stable_entity_ref
from .grounded_formation import (
    FORMATION_ENTITY_VERSION,
    FormationError,
    form_grounded_memory_batch,
    serialize_grounded_memory_batch,
)
from .models import IngestionResult, SourceProvenance
from .user_self import CURRENT_USER_ENTITY_REF


def _source_digest(segment) -> str:
    raw = json.dumps(asdict(segment), default=str, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bind_mentions(batch, segment, backend) -> list[dict[str, Any]]:
    """Resolve all roles against one source-ordered view, retaining ambiguity."""
    turns = {turn.turn_id: turn for turn in segment.turns}
    records: dict[str, dict[str, Any]] = {}
    overlay: dict[str, dict[str, str]] = {}
    distinct = {mention.id: set(mention.distinct_from) for mention in batch.mentions}
    for mention in batch.mentions:
        for other in mention.distinct_from:
            distinct[other].add(mention.id)
    pending = list(batch.mentions)
    for _ in range(len(pending) + 1):
        remaining = []
        for mention in pending:
            if mention.same_as and mention.same_as not in records:
                remaining.append(mention)
                continue
            persisted = backend.find_entity_candidates(mention.surface, limit=21)
            overflow = len(persisted) >= 21
            candidates = {
                candidate.entity_ref: candidate.canonical_surface
                for candidate in persisted
            }
            candidates.update(overlay.get(mention.surface, {}))
            excluded = {
                records[mid]["entity_ref"]
                for mid in distinct[mention.id] if mid in records
            }
            ref = None
            if mention.identity == "current_user":
                ref = CURRENT_USER_ENTITY_REF
            elif mention.identity == "unresolved":
                pass
            elif mention.same_as:
                prior = records[mention.same_as]
                prior_ref = prior["entity_ref"]
                # Explicit same-as may add a name; it cannot silently merge
                # independently persisted identities with conflicting refs.
                if prior_ref and not overflow and not (set(candidates) - {prior_ref}):
                    ref = prior_ref
            elif mention.identity == "new" or (
                mention.identity == "named" and not overflow
                and excluded and set(candidates).issubset(excluded)
            ):
                ref = stable_entity_ref(mention.id)
            elif mention.identity == "named" and not overflow:
                available = set(candidates) - excluded
                if len(available) == 1:
                    ref = next(iter(available))
                elif not available:
                    ref = stable_entity_ref(mention.id)
            if ref in excluded:
                ref = None
            turn = turns[mention.turn_id]
            provenance = SourceProvenance(
                segment.segment_id, segment.conversation_id, turn.turn_id,
                turn.role, turn.timestamp.isoformat(), turn.source_timezone,
                FORMATION_ENTITY_VERSION, turn.timezone_source,
            )
            record = {
                "mention_id": mention.id,
                "surface": mention.surface,
                "entity_ref": ref,
                "candidate_entity_refs": sorted(candidates) if ref is None else [],
                "candidates_truncated": overflow,
                "identity": mention.identity,
                "source_start": mention.source_start,
                "source_end": mention.source_end,
                "source_role": mention.source_role,
                "provenance": asdict(provenance),
                "identity_source_refs": [asdict(item) for item in mention.identity_source_refs],
            }
            records[mention.id] = record
            if ref:
                overlay.setdefault(mention.surface, {})[ref] = mention.surface
        if not remaining:
            return [records[mention.id] for mention in batch.mentions]
        if len(remaining) == len(pending):
            raise ValueError("mention_identity_cycle")
        pending = remaining
    raise ValueError("mention_identity_cycle")


def _validate_records(raw, batch, segment) -> bool:
    if not isinstance(raw, list) or len(raw) != len(batch.mentions):
        return False
    turns = {turn.turn_id: turn for turn in segment.turns}
    for record, mention in zip(raw, batch.mentions):
        turn = turns[mention.turn_id]
        if not isinstance(record, dict) or set(record) != {
            "mention_id", "surface", "entity_ref", "candidate_entity_refs",
            "identity", "source_start", "source_end", "source_role",
            "provenance", "identity_source_refs",
            "candidates_truncated",
        }:
            return False
        if any(record[key] != value for key, value in (
            ("mention_id", mention.id), ("surface", mention.surface),
            ("identity", mention.identity), ("source_start", mention.source_start),
            ("source_end", mention.source_end), ("source_role", turn.role),
            ("identity_source_refs", [asdict(item) for item in mention.identity_source_refs]),
            ("provenance", asdict(SourceProvenance(
                segment.segment_id, segment.conversation_id, turn.turn_id,
                turn.role, turn.timestamp.isoformat(), turn.source_timezone,
                FORMATION_ENTITY_VERSION, turn.timezone_source,
            ))),
        )):
            return False
        ref = record["entity_ref"]
        candidates = record["candidate_entity_refs"]
        if type(record["candidates_truncated"]) is not bool:
            return False
        if (ref is not None and (not isinstance(ref, str) or not ref.strip())) or (
            not isinstance(candidates, list)
            or not all(isinstance(item, str) and item for item in candidates)
            or len(set(candidates)) != len(candidates)
        ):
            return False
        if mention.identity == "current_user" and ref != CURRENT_USER_ENTITY_REF:
            return False
        if (mention.identity == "new" and not mention.same_as
                and ref != stable_entity_ref(mention.id)):
            return False
        if mention.identity == "unresolved" and ref is not None:
            return False
    by_id = {record["mention_id"]: record for record in raw}
    for mention in batch.mentions:
        ref = by_id[mention.id]["entity_ref"]
        if mention.same_as and ref is not None:
            if by_id[mention.same_as]["entity_ref"] != ref:
                return False
        if ref is not None and any(by_id[mid]["entity_ref"] == ref for mid in mention.distinct_from):
            return False
    return True


def ingest_entity_formation(adapter, segment) -> IngestionResult:
    """Persist extraction, verification and binding before graph mutations."""
    from .magma_adapter import _formed_event_metadata, _formed_source_turn

    version = FORMATION_ENTITY_VERSION
    invalid = adapter._validate(segment)
    if invalid:
        return IngestionResult(segment.segment_id, version, "failed",
                               safe_error_code=invalid)
    store, backend = adapter.state_store, adapter.backend
    key = store.key(segment.segment_id, version)
    digest = _source_digest(segment)
    try:
        state = store.get(key)
        if state is None:
            state = {
                "schema_version": version, "source_digest": digest,
                "segment_id": segment.segment_id, "ingestion_version": version,
                "status": "pending", "memory_ids": [],
            }
        elif (
            not set(state).issubset({
                "schema_version", "source_digest", "segment_id", "ingestion_version",
                "status", "memory_ids", "extracted", "verified", "mentions",
            })
            or state.get("schema_version") != version
            or state.get("source_digest") != digest
            or state.get("segment_id") != segment.segment_id
            or state.get("ingestion_version") != version
            or state.get("status") not in {"pending", "in_progress", "completed"}
            or not isinstance(state.get("memory_ids"), list)
            or not all(isinstance(mid, str) and mid for mid in state["memory_ids"])
            or ("verified" in state and "extracted" not in state)
            or ("mentions" in state and "verified" not in state)
            or (state.get("status") in {"in_progress", "completed"}
                and not {"extracted", "verified", "mentions"} <= state.keys())
        ):
            raise ValueError("state_corrupt")
    except (ValueError, OSError):
        return IngestionResult(segment.segment_id, version, "failed",
                               safe_error_code="state_corrupt")

    def checkpoint(stage, payload):
        if stage not in {"extracted", "verified"}:
            raise ValueError("formation_stage_invalid")
        state[stage] = payload
        store.put(key, state)

    try:
        batch = form_grounded_memory_batch(
            segment, adapter.formation_model,
            extracted_checkpoint=state.get("extracted"),
            verified_checkpoint=state.get("verified"), checkpoint=checkpoint,
        )
    except FormationError as error:
        return IngestionResult(segment.segment_id, version, "failed",
                               retryable=True, safe_error_code=error.code)
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed",
                               retryable=True, safe_error_code="state_write_failed")
    try:
        if "mentions" not in state:
            state["mentions"] = _bind_mentions(batch, segment, backend)
            state["verified"] = serialize_grounded_memory_batch(batch)
            store.put(key, state)
        if not _validate_records(state["mentions"], batch, segment):
            raise ValueError("state_corrupt")
        if len(state["memory_ids"]) > len(batch.units):
            raise ValueError("state_corrupt")
        if state["status"] == "completed":
            if len(state["memory_ids"]) != len(batch.units):
                raise ValueError("state_corrupt")
            if any(backend.find_memory_id(unit.id) != memory_id
                   for unit, memory_id in zip(batch.units, state["memory_ids"])):
                raise ValueError("state_corrupt")
            return IngestionResult(segment.segment_id, version, "completed",
                                   tuple(state["memory_ids"]), already_ingested=True)
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed",
                               retryable=True, safe_error_code="entity_consolidation_failed")

    memory_ids = []
    try:
        state["status"] = "in_progress"
        store.put(key, state)
        backend.upsert_entity_mentions(state["mentions"])
        # Even a zero-fact window must durably project its mentions.
        backend.persist()
        records = {record["mention_id"]: record for record in state["mentions"]}
        roles = {item.unit_id: item for item in batch.unit_mentions}
        for unit in batch.units:
            memory_id = backend.find_memory_id(unit.id)
            if memory_id is not None:
                repair = getattr(backend, "ensure_event_persisted", None)
                if callable(repair):
                    repair(memory_id)
            if memory_id is None:
                role = roles[unit.id]
                subject = records.get(role.subject, {})
                obj = records.get(role.object, {})
                subject_ref = subject.get("entity_ref")
                metadata = _formed_event_metadata(
                    segment, unit, _formed_source_turn(segment, unit),
                    ingestion_version=version,
                    configured_entities=adapter.configured_entities,
                    subject_entity_binding=(
                        EntityBinding(unit.id, subject_ref, subject["surface"])
                        if subject_ref else None
                    ),
                )
                # v2 roles come exclusively from verified mention handles;
                # legacy first-person heuristics cannot resolve a missing role.
                metadata["subject_entity_ref"] = subject_ref
                metadata["object_entity_ref"] = obj.get("entity_ref")
                metadata["object_entity_surface"] = obj.get("surface")
                participants = [records[mid] for mid in role.mentions if records[mid]["entity_ref"]]
                metadata["mention_entity_refs"] = [r["entity_ref"] for r in participants]
                metadata["mention_entity_surfaces"] = [r["surface"] for r in participants]
                metadata["mention_ids"] = list(role.mentions)
                memory_id = backend.add_event(
                    unit.text, _formed_source_turn(segment, unit).timestamp, metadata,
                )
                backend.persist()
            memory_ids.append(memory_id)
            state["memory_ids"] = list(memory_ids)
            store.put(key, state)
        backend.create_relationships(memory_ids)
        backend.persist()
        state["status"] = "completed"
        store.put(key, state)
        return IngestionResult(segment.segment_id, version, "completed", tuple(memory_ids))
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed", tuple(memory_ids),
                               retryable=True, safe_error_code="memory_write_failed")
