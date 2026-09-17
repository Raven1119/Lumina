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
    GroundedMemoryBatch,
    GroundedMemoryUnit,
    GroundedUnitMentions,
    SourceRef,
    can_repair_grounded_memory_batch,
    form_grounded_memory_batch,
    repair_grounded_memory_batch,
    serialize_grounded_memory_batch,
)
from .models import IngestionResult, SourceProvenance
from .reliable_formation import (
    FORMATION_RELIABLE_VERSION,
    FORMATION_RELIABLE_VERSION_V5,
    FORMATION_RELIABLE_VERSION_V6,
    digest as _reliable_digest,
    form_reliable_batch,
    form_reliable_bodies,
    form_reliable_structure,
)
from .user_self import CURRENT_USER_ENTITY_REF
from ._first_hit_ingestion import (
    FirstHitIngestionError,
    complete_first_hit_stage,
    load_first_hit_stage,
    prepare_first_hit_stage,
)


def _source_digest(segment) -> str:
    raw = json.dumps(asdict(segment), default=str, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bind_mentions(batch, segment, backend, *, version=FORMATION_ENTITY_VERSION,
                   explicit_identity_refs=None) -> list[dict[str, Any]]:
    """Resolve all roles against one source-ordered view, retaining ambiguity."""
    explicit_identity_refs = explicit_identity_refs or {}
    reliable = version in {FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5,
                           FORMATION_RELIABLE_VERSION_V6}
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
                # Source-verified local identity takes precedence over a name
                # candidate set. Reusing this ref does not merge its namesakes;
                # the distinct-from guard below still forbids actual conflicts.
                if prior_ref:
                    ref = prior_ref
            elif reliable and mention.identity == "named":
                ref = explicit_identity_refs.get(mention.id)
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
                version, turn.timezone_source,
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


def _validate_records(raw, batch, segment, *, version=FORMATION_ENTITY_VERSION,
                      explicit_identity_refs=None) -> bool:
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
                version, turn.timezone_source,
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
        if (version in {FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5,
                        FORMATION_RELIABLE_VERSION_V6}
                and mention.identity == "named"
                and not mention.same_as and ref != (explicit_identity_refs or {}).get(mention.id)):
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


def _identity_source_candidates(adapter, segment):
    """Snapshot actual prior occurrences through the existing Cold owner.

    Missing/oversized history simply leaves identities unresolved. Same-name
    graph lookup supplies candidates, never identity authorization.
    """
    cold = getattr(adapter, "cold_store", None)
    if cold is None:
        return ()
    records = adapter.backend.list_entity_mentions("\n".join(t.content for t in segment.turns), limit=24)
    selected, refs = [], []
    for record in records:
        p = record.get("provenance", {})
        if not record.get("entity_ref") or not isinstance(p, dict):
            continue
        if p.get("segment_id") == segment.segment_id:
            continue
        selected.append(record)
        refs.append({**p, "source_start": record["source_start"], "source_end": record["source_end"],
                     "supporting_span": record["surface"]})
    if not refs:
        return ()
    result = cold.read_source_refs(refs, max_refs=24, max_items=24, max_chars=12000,
                                   max_bytes=48000, whole_turns=True)
    if result.safe_error_code:
        return ()
    sources = {(x.provenance.segment_id, x.provenance.turn_id): x for x in result.evidence
               if x.source_start == 0 and x.source_end == x.turn_length}
    return tuple({"entity_ref": r["entity_ref"], "mention_id": r["mention_id"],
                  "surface": r["surface"], "provenance": r["provenance"],
                  "source_text": sources[(r["provenance"]["segment_id"], r["provenance"]["turn_id"])].text}
                 for r in selected if (r["provenance"]["segment_id"], r["provenance"]["turn_id"]) in sources)


def ingest_entity_formation(adapter, segment) -> IngestionResult:
    """Persist extraction, verification and binding before graph mutations."""
    from .magma_adapter import _formed_event_metadata, _formed_source_turn

    version = adapter.ingestion_version
    reliable = version in {FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5,
                           FORMATION_RELIABLE_VERSION_V6}
    reliable_v5 = version in {FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6}
    invalid = adapter._validate(segment)
    if invalid:
        return IngestionResult(segment.segment_id, version, "failed",
                               safe_error_code=invalid)
    store, backend = adapter.state_store, adapter.backend
    key = store.key(segment.segment_id, version)
    digest = _source_digest(segment)
    try:
        state = store.get(key)
        new_state = state is None
        if state is None:
            state = {
                "schema_version": version, "source_digest": digest,
                "segment_id": segment.segment_id, "ingestion_version": version,
                "status": "pending", "memory_ids": [],
            }
        elif reliable_v5:
            if (not set(state).issubset({"schema_version", "source_digest", "segment_id", "ingestion_version",
                                        "status", "memory_ids", "formation", "mentions", "bodies"})
                    or state.get("schema_version") != version or state.get("source_digest") != digest
                    or state.get("segment_id") != segment.segment_id or state.get("ingestion_version") != version
                    or state.get("status") not in {"pending", "in_progress", "bodies_persisted", "partial", "completed"}
                    or not isinstance(state.get("memory_ids"), list)
                    or not all(isinstance(mid, str) and mid for mid in state["memory_ids"])
                    or ("bodies" in state and "formation" not in state)
                    or ("mentions" in state and not {"formation", "bodies"} <= state.keys())
                    or (state.get("status") == "bodies_persisted" and "bodies" not in state)
                    or state.get("status") in {"partial", "completed"}
                    and not {"formation", "mentions", "bodies"} <= state.keys()):
                raise ValueError("state_corrupt")
        elif reliable:
            if (not set(state).issubset({"schema_version", "source_digest", "segment_id", "ingestion_version",
                                        "status", "memory_ids", "formation", "mentions"})
                    or state.get("schema_version") != version or state.get("source_digest") != digest
                    or state.get("segment_id") != segment.segment_id or state.get("ingestion_version") != version
                    or state.get("status") not in {"pending", "in_progress", "partial", "completed"}
                    or not isinstance(state.get("memory_ids"), list)
                    or not all(isinstance(mid, str) and mid for mid in state["memory_ids"])
                    or "mentions" in state and "formation" not in state
                    or state.get("status") in {"in_progress", "partial", "completed"}
                    and not {"formation", "mentions"} <= state.keys()):
                raise ValueError("state_corrupt")
        elif (
            not set(state).issubset({
                "schema_version", "source_digest", "segment_id", "ingestion_version",
                "status", "memory_ids", "extracted", "verified", "mentions",
                "repair", "repair_verified",
            })
            or state.get("schema_version") != version
            or state.get("source_digest") != digest
            or state.get("segment_id") != segment.segment_id
            or state.get("ingestion_version") != version
            or state.get("status") not in {"pending", "in_progress", "partial", "completed"}
            or not isinstance(state.get("memory_ids"), list)
            or not all(isinstance(mid, str) and mid for mid in state["memory_ids"])
            or ("verified" in state and "extracted" not in state)
            or ("mentions" in state and "verified" not in state)
            or ("repair" in state and not {"extracted", "verified", "mentions"} <= state.keys())
            or ("repair_verified" in state and "repair" not in state)
            or (state.get("status") in {"in_progress", "partial", "completed"}
                and not {"extracted", "verified", "mentions"} <= state.keys())
        ):
            raise ValueError("state_corrupt")
    except (ValueError, OSError) as error:
        # The existing store wraps both read I/O and invalid JSON as ValueError;
        # retain its cause so a temporary read failure is not called corruption.
        retryable = isinstance(error, OSError) or isinstance(error.__cause__, OSError)
        return IngestionResult(segment.segment_id, version, "failed",
                               retryable=retryable,
                               safe_error_code="state_read_failed" if retryable else "state_corrupt")
    try:
        association = load_first_hit_stage(
            adapter, segment, digest, new_formation=new_state,
        )
    except FirstHitIngestionError as error:
        return IngestionResult(segment.segment_id, version, "failed",
                               retryable=error.retryable, safe_error_code=error.code)
    if new_state:
        try:
            store.put(key, state)
        except Exception:
            return IngestionResult(segment.segment_id, version, "failed",
                                   retryable=True, safe_error_code="state_write_failed")
    if reliable_v5:
        return _ingest_reliable_v5(adapter, segment, state, store, key, association)

    def checkpoint(stage, payload):
        allowed = {"formation"} if reliable else {"extracted", "verified", "repair", "repair_verified"}
        if stage not in allowed:
            raise ValueError("formation_stage_invalid")
        state[stage] = payload
        if stage == "repair_verified":
            # The appended facts are not durable yet. Save both changes in the
            # same atomic replacement so restart never sees a partial manifest
            # claiming to cover the extended batch.
            state["status"] = "in_progress"
        store.put(key, state)

    try:
        context = {}
        if reliable:
            batch, context = form_reliable_batch(
                segment, adapter.formation_model, progress=state.get("formation"),
                checkpoint=checkpoint, identity_candidates=lambda: _identity_source_candidates(adapter, segment),
            )
        else:
            batch = form_grounded_memory_batch(
                segment, adapter.formation_model,
                extracted_checkpoint=state.get("extracted"),
                verified_checkpoint=state.get("repair_verified", state.get("verified")),
                checkpoint=checkpoint,
            )
            if "repair_verified" in state:
                original = form_grounded_memory_batch(
                    segment, adapter.formation_model, verified_checkpoint=state["verified"],
                )
                if (batch.mentions != original.mentions
                        or batch.units[:len(original.units)] != original.units
                        or batch.unit_mentions[:len(original.unit_mentions)] != original.unit_mentions):
                    raise FormationError("formation_checkpoint_invalid")
    except FormationError as error:
        return IngestionResult(segment.segment_id, version, "failed",
                               retryable=error.retryable, safe_error_code=error.code)
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed",
                               retryable=True, safe_error_code="state_write_failed")
    try:
        if "mentions" not in state:
            state["mentions"] = _bind_mentions(batch, segment, backend, version=version,
                                                explicit_identity_refs=context.get("explicit_identity_refs"))
            if not reliable:
                state["verified"] = serialize_grounded_memory_batch(batch)
            store.put(key, state)
        if not _validate_records(state["mentions"], batch, segment, version=version,
                                 explicit_identity_refs=context.get("explicit_identity_refs")):
            raise ValueError("state_corrupt")
        if len(state["memory_ids"]) > len(batch.units):
            raise ValueError("state_corrupt")
        pending_issues = any(issue.status == "pending" for issue in batch.issues)
        if state["status"] in {"completed", "partial"}:
            if (state["status"] == "partial") != pending_issues:
                raise ValueError("state_corrupt")
            if len(state["memory_ids"]) != len(batch.units):
                raise ValueError("state_corrupt")
            if any(backend.find_memory_id(unit.id) != memory_id
                   for unit, memory_id in zip(batch.units, state["memory_ids"])):
                raise ValueError("state_corrupt")
            if association is not None and (
                association["status"] != "completed"
                or association["evidence_ids"] != [unit.id for unit in batch.units]
            ):
                # The association checkpoint precedes either terminal v2
                # state. An inconsistent pair is not a resumable planning gap.
                raise FirstHitIngestionError("first_hit_checkpoint_invalid")
            if pending_issues and (reliable or "repair_verified" in state):
                return IngestionResult(segment.segment_id, version, "failed",
                                       tuple(state["memory_ids"]), retryable=False,
                                       safe_error_code="formation_processing_incomplete")
            if not pending_issues:
                if reliable:
                    ensure = getattr(backend, "ensure_event_persisted", None)
                    repaired = False
                    if callable(ensure):
                        for memory_id in state["memory_ids"]:
                            repaired = bool(ensure(memory_id)) or repaired
                    if repaired:
                        backend.persist()
                return IngestionResult(segment.segment_id, version, "completed",
                                       tuple(state["memory_ids"]), already_ingested=True)
    except FirstHitIngestionError as error:
        return IngestionResult(segment.segment_id, version, "failed",
                               tuple(state["memory_ids"]), retryable=error.retryable,
                               safe_error_code=error.code)
    except ValueError as error:
        return IngestionResult(segment.segment_id, version, "failed",
                               retryable=isinstance(error.__cause__, OSError),
                               safe_error_code="entity_consolidation_failed")
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed",
                               retryable=True, safe_error_code="entity_consolidation_failed")

    if state["status"] == "partial" and not reliable:
        try:
            batch = repair_grounded_memory_batch(
                segment, adapter.formation_model,
                extracted_checkpoint=state["extracted"],
                verified_checkpoint=state["verified"],
                repair_checkpoint=state.get("repair"), checkpoint=checkpoint,
            )
            if "repair_verified" not in state:
                return IngestionResult(segment.segment_id, version, "failed",
                                       tuple(state["memory_ids"]), retryable=False,
                                       safe_error_code="formation_processing_incomplete")
            pending_issues = any(issue.status == "pending" for issue in batch.issues)
        except FormationError as error:
            return IngestionResult(segment.segment_id, version, "failed",
                                   tuple(state["memory_ids"]), retryable=error.retryable,
                                   safe_error_code=error.code)
        except Exception:
            return IngestionResult(segment.segment_id, version, "failed",
                                   tuple(state["memory_ids"]), retryable=True,
                                   safe_error_code="state_write_failed")

    memory_ids = []
    try:
        if association is not None:
            prepare_first_hit_stage(adapter, association, batch, state["mentions"])
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
                origin = (next(t for t in segment.turns if t.turn_id == context["origins"][unit.id])
                          if reliable else _formed_source_turn(segment, unit))
                time = context.get("times", {}).get(unit.id)
                time_turn = next((t for t in segment.turns if time and t.turn_id == time["turn_id"]), None)
                metadata = _formed_event_metadata(
                    segment, unit, origin,
                    ingestion_version=version,
                    configured_entities=adapter.configured_entities,
                    subject_entity_binding=(
                        EntityBinding(unit.id, subject_ref, subject["surface"])
                        if subject_ref else None
                    ),
                    **({"referenced_time_turn": time_turn} if reliable else {}),
                )
                if reliable:
                    metadata.update(origin_turn_id=origin.turn_id,
                                    formation_receipts=context["receipts"],
                                    source_package_ids=[ref.turn_id for ref in unit.source_refs],
                                    used_source_ids=context["used_sources"][context["fact_ids"][unit.id]])
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
                    unit.text, origin.timestamp, metadata,
                    **({"automatic_semantic": False} if association is not None or reliable else {}),
                )
                backend.persist()
            memory_ids.append(memory_id)
            state["memory_ids"] = list(memory_ids)
            store.put(key, state)
        backend.create_relationships(
            memory_ids,
            **({"automatic_entity_links": False} if association is not None or reliable else {}),
        )
        backend.persist()
        if association is not None:
            complete_first_hit_stage(adapter, association)
        state["status"] = "partial" if pending_issues else "completed"
        store.put(key, state)
        if pending_issues:
            return IngestionResult(segment.segment_id, version, "failed", tuple(memory_ids),
                                   retryable=not reliable and "repair" not in state and can_repair_grounded_memory_batch(
                                       segment, extracted_checkpoint=state["extracted"], batch=batch,
                                   ), safe_error_code="formation_processing_incomplete")
        return IngestionResult(segment.segment_id, version, "completed", tuple(memory_ids))
    except FirstHitIngestionError as error:
        return IngestionResult(segment.segment_id, version, "failed", tuple(memory_ids),
                               retryable=error.retryable, safe_error_code=error.code)
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed", tuple(memory_ids),
                               retryable=True, safe_error_code="memory_write_failed")


def _ensure_body_vector_durable(backend, memory_id) -> bool:
    """v5/v6 body durability gate: an existing body EVENT must hold its vector.

    A restart between graph and vector persistence leaves the EVENT findable
    by evidence id while its vector is missing. Repair reuses the persisted
    embedding; a backend without the repair capability fails closed instead
    of advancing a body that recall can never reach.
    """
    ensure = getattr(backend, "ensure_event_persisted", None)
    if not callable(ensure):
        raise ValueError("memory_repair_unavailable")
    return bool(ensure(memory_id))


def _ingest_reliable_v5(adapter, segment, state, store, key, association) -> IngestionResult:
    """v5/v6 body/structure decoupling: F2-accepted bodies persist before G stages.

    A G-stage failure leaves durable projection-free EVENTs and a
    ``bodies_persisted`` checkpoint; recovery adds only the missing structure.
    """
    from .backend import _EVENT_PROJECTION_KEYS
    from .magma_adapter import _formed_event_metadata
    from ._reliable_projection import reliable_unit_id

    version = adapter.ingestion_version
    backend = adapter.backend
    turns = {turn.turn_id: turn for turn in segment.turns}

    def checkpoint(stage, payload):
        if stage != "formation":
            raise ValueError("formation_stage_invalid")
        state[stage] = payload
        store.put(key, state)

    try:
        accepted, f1, f2, progress = form_reliable_bodies(
            segment, adapter.formation_model, progress=state.get("formation"),
            checkpoint=checkpoint, version=version)
    except FormationError as error:
        return IngestionResult(segment.segment_id, version, "failed",
                               tuple(state["memory_ids"]), retryable=error.retryable,
                               safe_error_code=error.code)
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed",
                               tuple(state["memory_ids"]), retryable=True,
                               safe_error_code="state_write_failed")
    body_units = tuple(
        GroundedMemoryUnit(
            reliable_unit_id(segment, fact, version=version), fact["text"], None, None, None,
            tuple(SourceRef(tid, turns[tid].content) for tid in fact["allowed_source_ids"]),
            version, None)
        for fact in accepted)
    manifest = _reliable_digest([asdict(unit) for unit in body_units])
    if "bodies" in state:
        saved = state["bodies"]
        if (not isinstance(saved, dict) or set(saved) != {"memory_ids", "manifest"}
                or saved["manifest"] != manifest
                or not isinstance(saved["memory_ids"], list)
                or saved["memory_ids"] != state["memory_ids"]
                or len(saved["memory_ids"]) != len(body_units)
                or any(backend.find_memory_id(unit.id) != memory_id
                       for unit, memory_id in zip(body_units, saved["memory_ids"]))):
            return IngestionResult(segment.segment_id, version, "failed",
                                   tuple(state["memory_ids"]), safe_error_code="state_corrupt")
        # The manifest proves the EVENTs; durability also requires each vector.
        try:
            repaired = False
            for memory_id in saved["memory_ids"]:
                repaired = _ensure_body_vector_durable(backend, memory_id) or repaired
            if repaired:
                backend.persist()
        except Exception:
            return IngestionResult(segment.segment_id, version, "failed",
                                   tuple(state["memory_ids"]), retryable=True,
                                   safe_error_code="memory_write_failed")
    else:
        receipts = {stage: {field: progress["stages"][stage][field]
                            for field in ("request_digest", "response_digest", "parsed_digest", "execution")}
                    for stage in ("F1", "F2")}
        used_sources = {f["fact_id"]: d["used_source_ids"] for f, d in zip(f1["facts"], f2)}
        memory_ids = []
        try:
            state["status"] = "in_progress"
            store.put(key, state)
            for unit, fact in zip(body_units, accepted):
                memory_id = backend.find_memory_id(unit.id)
                if memory_id is not None and _ensure_body_vector_durable(backend, memory_id):
                    backend.persist()
                if memory_id is None:
                    origin = turns[fact["origin_turn_id"]]
                    metadata = _formed_event_metadata(
                        segment, unit, origin, ingestion_version=version,
                        configured_entities=adapter.configured_entities)
                    metadata.update(origin_turn_id=origin.turn_id,
                                    formation_receipts=receipts,
                                    source_package_ids=[ref.turn_id for ref in unit.source_refs],
                                    used_source_ids=used_sources[fact["fact_id"]])
                    # Projection attributes belong to the structure phase only.
                    for field in _EVENT_PROJECTION_KEYS:
                        metadata.pop(field, None)
                    memory_id = backend.add_event(unit.text, origin.timestamp, metadata,
                                                  automatic_semantic=False)
                    backend.persist()
                memory_ids.append(memory_id)
                state["memory_ids"] = list(memory_ids)
                store.put(key, state)
            backend.create_relationships(memory_ids, automatic_entity_links=False)
            backend.persist()
            # No body enters bodies_persisted without its durable vector.
            repaired = False
            for memory_id in memory_ids:
                repaired = _ensure_body_vector_durable(backend, memory_id) or repaired
            if repaired:
                backend.persist()
            state["bodies"] = {"memory_ids": list(memory_ids), "manifest": manifest}
            state["memory_ids"] = list(memory_ids)
            state["status"] = "bodies_persisted"
            store.put(key, state)
        except Exception:
            return IngestionResult(segment.segment_id, version, "failed", tuple(memory_ids),
                                   retryable=True, safe_error_code="memory_write_failed")
    try:
        batch, context = form_reliable_structure(
            segment, adapter.formation_model, progress=progress, checkpoint=checkpoint,
            identity_candidates=lambda: _identity_source_candidates(adapter, segment),
            accepted=accepted, f1=f1, f2=f2, version=version)
    except FormationError as error:
        # Bodies are already durable projection-free EVENTs; only the optional
        # structure is missing, so the body checkpoint stays authoritative.
        return IngestionResult(segment.segment_id, version, "failed",
                               tuple(state["memory_ids"]), retryable=error.retryable,
                               safe_error_code=error.code)
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed",
                               tuple(state["memory_ids"]), retryable=True,
                               safe_error_code="state_write_failed")
    try:
        if "mentions" not in state:
            state["mentions"] = _bind_mentions(batch, segment, backend, version=version,
                                               explicit_identity_refs=context.get("explicit_identity_refs"))
            store.put(key, state)
        if not _validate_records(state["mentions"], batch, segment, version=version,
                                 explicit_identity_refs=context.get("explicit_identity_refs")):
            raise ValueError("state_corrupt")
        if len(state["memory_ids"]) != len(batch.units):
            raise ValueError("state_corrupt")
        pending_issues = any(issue.status == "pending" for issue in batch.issues)
        if state["status"] in {"completed", "partial"}:
            if (state["status"] == "partial") != pending_issues:
                raise ValueError("state_corrupt")
            if any(backend.find_memory_id(unit.id) != memory_id
                   for unit, memory_id in zip(batch.units, state["memory_ids"])):
                raise ValueError("state_corrupt")
            if association is not None and (
                association["status"] != "completed"
                or association["evidence_ids"] != [unit.id for unit in batch.units]
            ):
                # The association checkpoint precedes either terminal state.
                # An inconsistent pair is not a resumable planning gap.
                raise FirstHitIngestionError("first_hit_checkpoint_invalid")
            if pending_issues:
                return IngestionResult(segment.segment_id, version, "failed",
                                       tuple(state["memory_ids"]), retryable=False,
                                       safe_error_code="formation_processing_incomplete")
            ensure = getattr(backend, "ensure_event_persisted", None)
            repaired = False
            if callable(ensure):
                for memory_id in state["memory_ids"]:
                    repaired = bool(ensure(memory_id)) or repaired
            if repaired:
                backend.persist()
            return IngestionResult(segment.segment_id, version, "completed",
                                   tuple(state["memory_ids"]), already_ingested=True)
    except FirstHitIngestionError as error:
        return IngestionResult(segment.segment_id, version, "failed",
                               tuple(state["memory_ids"]), retryable=error.retryable,
                               safe_error_code=error.code)
    except ValueError as error:
        return IngestionResult(segment.segment_id, version, "failed",
                               tuple(state["memory_ids"]),
                               retryable=isinstance(error.__cause__, OSError),
                               safe_error_code="entity_consolidation_failed")
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed",
                               tuple(state["memory_ids"]), retryable=True,
                               safe_error_code="entity_consolidation_failed")

    memory_ids = list(state["memory_ids"])
    try:
        # FirstHit planning happens once, here, with complete bindings; bodies
        # without structure simply have no first-hit links yet.
        if association is not None:
            prepare_first_hit_stage(adapter, association, batch, state["mentions"])
        backend.upsert_entity_mentions(state["mentions"])
        backend.persist()
        records = {record["mention_id"]: record for record in state["mentions"]}
        roles = {item.unit_id: item for item in batch.unit_mentions}
        for unit, memory_id in zip(batch.units, memory_ids):
            role = roles[unit.id]
            subject = records.get(role.subject, {})
            obj = records.get(role.object, {})
            origin = turns[context["origins"][unit.id]]
            time = context.get("times", {}).get(unit.id)
            time_turn = next((t for t in segment.turns if time and t.turn_id == time["turn_id"]), None)
            full = _formed_event_metadata(
                segment, unit, origin, ingestion_version=version,
                configured_entities=adapter.configured_entities,
                subject_entity_binding=(
                    EntityBinding(unit.id, subject["entity_ref"], subject["surface"])
                    if subject.get("entity_ref") else None
                ),
                referenced_time_turn=time_turn)
            participants = [records[mid] for mid in role.mentions if records[mid]["entity_ref"]]
            projection = {
                "subject": full["subject"], "relation": full["relation"], "value": full["value"],
                "subject_entity_ref": subject.get("entity_ref"),
                "subject_entity_surface": subject.get("surface") if subject.get("entity_ref") else None,
                "object_entity_ref": obj.get("entity_ref"),
                "object_entity_surface": obj.get("surface"),
                # The body phase attached F1/F2 receipts; the structure phase
                # grows them to the full four-stage set, entries preserved.
                "formation_receipts": context["receipts"],
                "mention_entity_refs": [r["entity_ref"] for r in participants],
                "mention_entity_surfaces": [r["surface"] for r in participants],
                "mention_ids": list(role.mentions),
                "referenced_time": full["referenced_time"],
                "temporal_mentions": full["temporal_mentions"],
                "dates_mentioned": full["dates_mentioned"],
            }
            backend.update_event_projection(memory_id, projection)
        backend.create_relationships(memory_ids, automatic_entity_links=False)
        backend.persist()
        if association is not None:
            complete_first_hit_stage(adapter, association)
        state["status"] = "partial" if pending_issues else "completed"
        store.put(key, state)
        if pending_issues:
            return IngestionResult(segment.segment_id, version, "failed", tuple(memory_ids),
                                   retryable=False, safe_error_code="formation_processing_incomplete")
        return IngestionResult(segment.segment_id, version, "completed", tuple(memory_ids))
    except FirstHitIngestionError as error:
        return IngestionResult(segment.segment_id, version, "failed", tuple(memory_ids),
                               retryable=error.retryable, safe_error_code=error.code)
    except Exception:
        return IngestionResult(segment.segment_id, version, "failed", tuple(memory_ids),
                               retryable=True, safe_error_code="memory_write_failed")
