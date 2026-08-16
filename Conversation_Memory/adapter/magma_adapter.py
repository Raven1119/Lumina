from __future__ import annotations

import os
from datetime import UTC, datetime
from math import isfinite
from numbers import Real
from pathlib import Path
from threading import Lock
from typing import Any

from ingestion.entities import extract_entities
from ingestion.state_store import IngestionStateStore
from ingestion.temporal import normalize_temporal_references
from recall.bge_reranker import BgeReranker
from recall.hindsight_scoring import score_hindsight_post_rerank
from recall.rendering import bound_evidence

from ._grounded_spans import GroundedSpanUnit, build_grounded_spans
from .backend import MemoryBackend
from .controlled_relation import UNRESOLVED, ControlledRelationResolver
from .grounded_formation import (
    FORMATION_VERSION,
    FormationError,
    FormationModel,
    GroundedMemoryUnit,
    deserialize_grounded_memory_units,
    form_grounded_memory_units,
    serialize_grounded_memory_units,
    validate_persisted_grounded_memory_units,
)
from .models import (
    ColdDraftSegment,
    ColdDraftTurn,
    IngestionResult,
    MemoryContext,
    MemoryEvidence,
    RecallPolicy,
    SourceProvenance,
)
from .user_self import (
    candidate_entity_ref,
    classify_subject_entity_ref,
    classify_target_entity_ref,
    entity_marked_text,
)


_GROUNDED_SPAN_INGESTION_VERSION = "grounded-span-v2"
_RELATION_RESOLVER = ControlledRelationResolver()
_USER_SELF_BINDING_ENV = "LUMINA_USER_SELF_BINDING_ENABLED"


def _user_self_binding_enabled() -> bool:
    raw = os.environ.get(_USER_SELF_BINDING_ENV)
    if raw is None:
        return True
    return raw.strip().casefold() not in {"0", "false", "no", "off"}


class MagmaMemoryAdapter:
    def __init__(
        self,
        backend: MemoryBackend,
        state_store: IngestionStateStore,
        *,
        ingestion_version: str = _GROUNDED_SPAN_INGESTION_VERSION,
        configured_entities: tuple[str, ...] = (),
        formation_model: FormationModel | None = None,
    ):
        if (
            formation_model is not None
            and ingestion_version != FORMATION_VERSION
        ):
            raise ValueError("formation_ingestion_version_required")
        self.backend = backend
        self.state_store = state_store
        self.ingestion_version = ingestion_version
        self.configured_entities = configured_entities
        self.formation_model = formation_model
        self._bge_reranker = None
        self._bge_reranker_load_attempted = False
        self._bge_reranker_lock = Lock()

    @classmethod
    def create_real(
        cls,
        persist_dir,
        state_store: IngestionStateStore | None = None,
        *,
        fail_if_unavailable: bool = False,
        **kwargs,
    ) -> "MagmaMemoryAdapter":
        from . import backend as backend_module
        try:
            backend = backend_module.RealMagmaBackend(persist_dir)
        except Exception:
            if fail_if_unavailable:
                raise
            backend = backend_module.UnavailableMemoryBackend()
        if state_store is None:
            state_store = IngestionStateStore(
                Path(persist_dir).parent / "ingestion_state.json"
            )
        return cls(backend, state_store, **kwargs)

    def _validate(self, segment: ColdDraftSegment) -> str | None:
        if not segment.segment_id:
            return "invalid_segment_id"
        if segment.state != "pending_digest":
            return "segment_not_pending"
        if segment.schema_version not in {"1", "2"}:
            return "unsupported_schema_version"
        if not segment.turns:
            return "invalid_turns"
        for turn in segment.turns:
            if turn.role not in {"user", "assistant"}:
                return "invalid_role"
            if not turn.content.strip():
                return "invalid_content"
            if turn.timestamp.tzinfo is None or turn.timestamp.utcoffset() is None:
                return "timestamp_timezone_required"
            if not turn.source_timezone:
                return "invalid_source_timezone"
            if turn.timezone_source not in {
                "client",
                "configured_default",
                "legacy_segment_fallback",
            }:
                return "invalid_timezone_source"
        return None

    def ingest(self, segment: ColdDraftSegment) -> IngestionResult:
        if self.formation_model is not None:
            return _ingest_grounded_formation(
                segment,
                model=self.formation_model,
                backend=self.backend,
                state_store=self.state_store,
                ingestion_version=self.ingestion_version,
                configured_entities=self.configured_entities,
            )
        return _ingest_grounded_spans(
            segment,
            backend=self.backend,
            state_store=self.state_store,
            ingestion_version=self.ingestion_version,
            configured_entities=self.configured_entities,
        )
    def recall(self, query: str, policy: RecallPolicy) -> MemoryContext:
        if not isinstance(query, str) or not query.strip():
            return MemoryContext(query if isinstance(query, str) else "", safe_error_code="invalid_query")
        normalized_query = query.strip()
        try:
            target_entity_ref = (
                classify_target_entity_ref(normalized_query)
                if _user_self_binding_enabled()
                else None
            )
        except Exception:
            target_entity_ref = None
        try:
            candidates = tuple(
                self.backend.recall(normalized_query, policy)
                if target_entity_ref is None
                else self.backend.recall(
                    normalized_query,
                    policy,
                    target_entity_ref=target_entity_ref,
                )
            )
        except Exception:
            return MemoryContext(normalized_query, safe_error_code="recall_unavailable")

        try:
            query_relation_ids = _RELATION_RESOLVER.resolve_query_relations(
                policy.relation_surfaces or (),
            )
            rerankable = tuple(
                (index, candidate)
                for index, candidate in enumerate(candidates)
                if (
                    isinstance(candidate.text, str)
                    and candidate.text.strip()
                    and _relation_compatible(candidate, query_relation_ids)
                )
            )
        except Exception:
            return MemoryContext(
                normalized_query,
                safe_error_code="recall_unavailable",
            )
        if not rerankable:
            return MemoryContext(normalized_query)

        try:
            reranker = self._get_bge_reranker()
            if reranker is None:
                raise RuntimeError("BGE reranker is unavailable")
            scores = _score_candidates(
                reranker, normalized_query, target_entity_ref, rerankable,
            )
            source_timestamps = tuple(
                _source_timestamp(candidate)
                for _, candidate in rerankable
            )
            hindsight_scores = score_hindsight_post_rerank(
                scores,
                source_timestamps,
                now=_candidate_snapshot_reference_time(source_timestamps),
            )
            ranked = sorted(
                zip(rerankable, hindsight_scores, strict=True),
                key=lambda item: (-item[1].final_score, item[0][0]),
            )
            if policy.final_min_score is not None:
                minimum_final_score = float(policy.final_min_score)
                ranked = [
                    item
                    for item in ranked
                    if item[1].final_score >= minimum_final_score
                ]
            projected = []
            for (_original_index, candidate), _score in ranked:
                raw = candidate.metadata.get("provenance")
                evidence_id = candidate.metadata.get("evidence_id")
                if not isinstance(raw, dict) or not isinstance(evidence_id, str):
                    continue
                try:
                    provenance = SourceProvenance(**raw)
                except (TypeError, ValueError):
                    continue
                projected.append((candidate, MemoryEvidence(
                    evidence_id,
                    candidate.text,
                    candidate.timestamp,
                    provenance,
                )))

            items = [item for _, item in projected]
            evidence, rendered, truncated = bound_evidence(
                items,
                count=policy.max_evidence_items,
                max_chars=policy.max_chars,
            )
            return MemoryContext(normalized_query, evidence, rendered, truncated)
        except Exception:
            return MemoryContext(normalized_query, safe_error_code="recall_unavailable")

    def _get_bge_reranker(self):
        if self._bge_reranker_load_attempted:
            return self._bge_reranker
        with self._bge_reranker_lock:
            if self._bge_reranker_load_attempted:
                return self._bge_reranker
            try:
                self._bge_reranker = _create_bge_reranker()
            except Exception:
                self._bge_reranker = None
            self._bge_reranker_load_attempted = True
            return self._bge_reranker


def _create_bge_reranker():
    return BgeReranker()


def _score_candidates(
    reranker,
    normalized_query: str,
    target_entity_ref: str | None,
    rerankable,
) -> tuple[float, ...]:
    """Score candidates with the per-pair SAME_ENTITY scoring projection.

    The entity marker enters a (query, candidate) pair only when both sides
    carry the same non-null entity ref; every other pair is scored with the
    byte-identical current texts. BGE scores pairs independently, so
    splitting into two score calls does not change per-pair semantics.
    """
    marked_texts: list[str] = []
    marked_positions: list[int] = []
    plain_texts: list[str] = []
    plain_positions: list[int] = []
    for position, (_index, candidate) in enumerate(rerankable):
        ref = candidate_entity_ref(candidate.metadata)
        if target_entity_ref is not None and ref == target_entity_ref:
            marked_positions.append(position)
            marked_texts.append(entity_marked_text(ref, candidate.text))
        else:
            plain_positions.append(position)
            plain_texts.append(candidate.text)
    scores: list[float | None] = [None] * len(rerankable)
    if marked_texts:
        marked_scores = _validate_reranker_scores(
            reranker.score(
                entity_marked_text(target_entity_ref, normalized_query),
                tuple(marked_texts),
            ),
            len(marked_texts),
        )
        for position, score in zip(marked_positions, marked_scores, strict=True):
            scores[position] = score
    if plain_texts:
        plain_scores = _validate_reranker_scores(
            reranker.score(normalized_query, tuple(plain_texts)),
            len(plain_texts),
        )
        for position, score in zip(plain_positions, plain_scores, strict=True):
            scores[position] = score
    return tuple(
        score for score in scores if score is not None
    )


def _relation_compatible(
    candidate,
    query_relation_ids: tuple[str | None, ...],
) -> bool:
    if (
        not query_relation_ids
        or any(item is UNRESOLVED for item in query_relation_ids)
    ):
        return True
    metadata = candidate.metadata
    if not isinstance(metadata, dict):
        return True
    subject = metadata.get("subject")
    relation = metadata.get("relation")
    value = metadata.get("value")
    if not all(isinstance(item, str) for item in (subject, relation, value)):
        return True
    memory_relation_id = _RELATION_RESOLVER.resolve_memory(
        relation=relation,
        subject=subject,
        value=value,
        text=candidate.text,
    )
    return (
        memory_relation_id is UNRESOLVED
        or memory_relation_id in query_relation_ids
    )


def _validate_reranker_scores(raw_scores, expected_count: int):
    try:
        scores = tuple(raw_scores)
    except TypeError as error:
        raise RuntimeError('reranker returned malformed scores') from error
    if len(scores) != expected_count:
        raise RuntimeError('reranker returned malformed scores')

    normalized = []
    for score in scores:
        if isinstance(score, bool) or not isinstance(score, Real):
            raise RuntimeError('reranker returned malformed scores')
        value = float(score)
        if not isfinite(value):
            raise RuntimeError('reranker returned malformed scores')
        normalized.append(value)
    return tuple(normalized)


def _source_timestamp(candidate) -> str | None:
    provenance = candidate.metadata.get('provenance')
    if not isinstance(provenance, dict):
        return None
    source_timestamp = provenance.get('source_timestamp')
    if not isinstance(source_timestamp, str) or not source_timestamp.strip():
        return None
    return source_timestamp


def _candidate_snapshot_reference_time(
    source_timestamps: tuple[str | None, ...],
) -> datetime:
    '''Return the persisted as-of watermark for this bounded candidate set.

    Hindsight accepts an explicit query timestamp as the anchor for recency
    scoring and otherwise falls back to server wall time. Lumina's Recall
    facade has no persisted query timestamp, so the newest truthful source
    timestamp in the deterministic candidate snapshot is its reproducible
    logical as-of time.
    '''

    observed: list[datetime] = []
    for source_timestamp in source_timestamps:
        if source_timestamp is None:
            continue
        timestamp = datetime.fromisoformat(source_timestamp)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError('source timestamp must be timezone-aware')
        observed.append(timestamp.astimezone(UTC))
    if not observed:
        raise ValueError('candidate snapshot has no reference timestamp')
    return max(observed)



def _ingest_grounded_formation(
    segment: ColdDraftSegment,
    *,
    model: FormationModel,
    backend: MemoryBackend,
    state_store: IngestionStateStore,
    ingestion_version: str,
    configured_entities: tuple[str, ...] = (),
) -> IngestionResult:
    version = ingestion_version
    invalid = MagmaMemoryAdapter(
        backend,
        state_store,
        ingestion_version=version,
        configured_entities=configured_entities,
    )._validate(segment)
    if invalid:
        return IngestionResult(
            segment.segment_id, version, "failed", retryable=False,
            safe_error_code=invalid,
        )
    key = state_store.key(segment.segment_id, version)
    try:
        state = state_store.get(key)
    except ValueError:
        return IngestionResult(
            segment.segment_id, version, "failed", retryable=False,
            safe_error_code="state_corrupt",
        )
    if state is not None and "formed_units" not in state:
        return IngestionResult(
            segment.segment_id,
            version,
            "failed",
            retryable=False,
            safe_error_code="state_corrupt",
        )
    semantic_unit_ids: set[str] = set()
    if state is None:
        try:
            units = form_grounded_memory_units(
                segment,
                model,
                semantic_unit_ids=semantic_unit_ids,
            )
        except FormationError as error:
            return IngestionResult(
                segment.segment_id, version, "failed", retryable=True,
                safe_error_code=error.code,
            )
        formed_units = serialize_grounded_memory_units(units)
        try:
            state_store.put(
                key,
                _formed_state_record(
                    segment.segment_id,
                    ingestion_version=version,
                    status="pending",
                    units=units,
                    formed_units=formed_units,
                    memory_ids=(),
                    semantic_unit_ids=semantic_unit_ids,
                ),
            )
        except Exception:
            return IngestionResult(
                segment.segment_id, version, "failed", retryable=True,
                safe_error_code="state_write_failed",
            )
    else:
        state_error, units = _formed_state_units(
            state,
            segment=segment,
            ingestion_version=version,
        )
        if state_error is not None or units is None:
            return IngestionResult(
                segment.segment_id, version, "failed", retryable=False,
                safe_error_code=state_error or "state_corrupt",
            )
        formed_units = serialize_grounded_memory_units(units)
        semantic_unit_ids.update(state.get("semantic_unit_ids", ()))
        if state["status"] == "completed":
            return IngestionResult(
                segment.segment_id, version, "completed",
                tuple(state["memory_ids"]), already_ingested=True,
            )
    if not units:
        try:
            state_store.put(
                key,
                _formed_state_record(
                    segment.segment_id,
                    ingestion_version=version,
                    status="completed",
                    units=units,
                    formed_units=formed_units,
                    memory_ids=(),
                    semantic_unit_ids=semantic_unit_ids,
                ),
            )
        except Exception:
            return IngestionResult(
                segment.segment_id, version, "failed", retryable=True,
                safe_error_code="state_write_failed",
            )
        return IngestionResult(segment.segment_id, version, "completed")
    memory_ids: list[str] = []
    try:
        state_store.put(
            key,
            _formed_state_record(
                segment.segment_id,
                ingestion_version=version,
                status="in_progress",
                units=units,
                formed_units=formed_units,
                memory_ids=memory_ids,
                semantic_unit_ids=semantic_unit_ids,
            ),
        )
        for unit in units:
            existing = backend.find_memory_id(unit.id)
            if existing is not None:
                memory_id = existing
            else:
                source_turn = _formed_source_turn(segment, unit)
                memory_id = backend.add_event(
                    unit.text,
                    source_turn.timestamp,
                    _formed_event_metadata(
                        segment,
                        unit,
                        source_turn,
                        ingestion_version=version,
                        configured_entities=configured_entities,
                    ),
                )
                backend.persist()
            memory_ids.append(memory_id)
            state_store.put(
                key,
                _formed_state_record(
                    segment.segment_id,
                    ingestion_version=version,
                    status="in_progress",
                    units=units,
                    formed_units=formed_units,
                    memory_ids=memory_ids,
                    semantic_unit_ids=semantic_unit_ids,
                ),
            )
        backend.create_relationships(memory_ids)
        backend.persist()
        state_store.put(
            key,
            _formed_state_record(
                segment.segment_id,
                ingestion_version=version,
                status="completed",
                units=units,
                formed_units=formed_units,
                memory_ids=memory_ids,
                semantic_unit_ids=semantic_unit_ids,
            ),
        )
        return IngestionResult(
            segment.segment_id, version, "completed", tuple(memory_ids),
        )
    except Exception:
        try:
            state_store.put(
                key,
                _formed_state_record(
                    segment.segment_id,
                    ingestion_version=version,
                    status="failed",
                    units=units,
                    formed_units=formed_units,
                    memory_ids=memory_ids,
                    semantic_unit_ids=semantic_unit_ids,
                    safe_error_code="memory_write_failed",
                ),
            )
        except Exception:
            pass
        return IngestionResult(
            segment.segment_id, version, "failed", tuple(memory_ids),
            retryable=True, safe_error_code="memory_write_failed",
        )


def _formed_state_units(
    state: dict[str, Any],
    *,
    segment: ColdDraftSegment,
    ingestion_version: str,
) -> tuple[str | None, tuple[GroundedMemoryUnit, ...] | None]:
    required_fields = {
        "segment_id", "ingestion_version", "status", "unit_ids",
        "formed_units", "memory_ids",
    }
    if (
        not required_fields.issubset(state)
        or not set(state).issubset(
            required_fields | {"safe_error_code", "semantic_unit_ids"}
        )
        or state.get("segment_id") != segment.segment_id
        or state.get("ingestion_version") != ingestion_version
        or state.get("status") not in {
            "pending", "in_progress", "completed", "failed",
        }
        or not isinstance(state.get("unit_ids"), list)
        or not isinstance(state.get("memory_ids"), list)
        or not all(
            isinstance(memory_id, str) and bool(memory_id)
            for memory_id in state.get("memory_ids", ())
        )
        or not isinstance(state.get("semantic_unit_ids", []), list)
        or not all(
            isinstance(unit_id, str) and bool(unit_id)
            for unit_id in state.get("semantic_unit_ids", ())
        )
        or len(set(state.get("semantic_unit_ids", ())))
        != len(state.get("semantic_unit_ids", ()))
    ):
        return "state_corrupt", None
    try:
        units = deserialize_grounded_memory_units(state["formed_units"])
    except ValueError:
        return "state_corrupt", None
    if tuple(state["unit_ids"]) != tuple(unit.id for unit in units):
        return "grounded_manifest_mismatch", None
    semantic_unit_ids = frozenset(state.get("semantic_unit_ids", ()))
    if (
        not semantic_unit_ids.issubset(unit.id for unit in units)
        or
        len(state["memory_ids"]) > len(units)
        or (
            state["status"] == "completed"
            and len(state["memory_ids"]) != len(units)
        )
        or not validate_persisted_grounded_memory_units(
            units, segment, semantic_unit_ids,
        )
    ):
        return "state_corrupt", None
    return None, units


def _formed_state_record(
    segment_id: str,
    *,
    ingestion_version: str,
    status: str,
    units: tuple[GroundedMemoryUnit, ...],
    formed_units: list[dict[str, Any]],
    memory_ids: tuple[str, ...] | list[str],
    semantic_unit_ids: set[str],
    safe_error_code: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "segment_id": segment_id,
        "ingestion_version": ingestion_version,
        "status": status,
        "unit_ids": [unit.id for unit in units],
        "formed_units": formed_units,
        "memory_ids": list(memory_ids),
        "semantic_unit_ids": sorted(semantic_unit_ids),
    }
    if safe_error_code is not None:
        record["safe_error_code"] = safe_error_code
    return record


def _formed_source_turn(
    segment: ColdDraftSegment,
    unit: GroundedMemoryUnit,
) -> ColdDraftTurn:
    turns = {turn.turn_id: turn for turn in segment.turns}
    order = {turn.turn_id: index for index, turn in enumerate(segment.turns)}
    if unit.referenced_time is not None:
        matches = [
            ref for ref in unit.source_refs
            if unit.referenced_time in ref.supporting_span
        ]
        if len(matches) != 1:
            raise ValueError("formed referenced-time anchor mismatch")
        return turns[matches[0].turn_id]
    first_ref = min(unit.source_refs, key=lambda ref: order[ref.turn_id])
    return turns[first_ref.turn_id]


def _formed_event_metadata(
    segment: ColdDraftSegment,
    unit: GroundedMemoryUnit,
    source_turn: ColdDraftTurn,
    *,
    ingestion_version: str,
    configured_entities: tuple[str, ...],
) -> dict[str, Any]:
    turns = {turn.turn_id: turn for turn in segment.turns}
    refs = []
    for ref in unit.source_refs:
        turn = turns[ref.turn_id]
        start = turn.content.find(ref.supporting_span)
        if start < 0:
            raise ValueError("formed source span mismatch")
        refs.append({
            "turn_id": ref.turn_id,
            "supporting_span": ref.supporting_span,
            "source_start": start,
            "source_end": start + len(ref.supporting_span),
            "source_role": turn.role,
            "source_timestamp": turn.timestamp.isoformat(),
            "source_timezone": turn.source_timezone,
            "timezone_source": turn.timezone_source,
        })
    first_ref = next(
        ref for ref in refs if ref["turn_id"] == source_turn.turn_id
    )
    formed_turn = ColdDraftTurn(
        turn_id=source_turn.turn_id,
        role=source_turn.role,
        content=unit.text,
        timestamp=source_turn.timestamp,
        source_timezone=source_turn.source_timezone,
        timezone_source=source_turn.timezone_source,
    )
    temporal_mentions = [
        item.__dict__ for item in normalize_temporal_references(formed_turn)
    ]
    provenance = SourceProvenance(
        segment_id=segment.segment_id,
        conversation_id=segment.conversation_id,
        turn_id=source_turn.turn_id,
        source_role=source_turn.role,
        source_timestamp=source_turn.timestamp.isoformat(),
        source_timezone=source_turn.source_timezone,
        ingestion_version=ingestion_version,
        timezone_source=source_turn.timezone_source,
    )
    return {
        "evidence_id": unit.id,
        "grounded_memory_unit_id": unit.id,
        "grounded_memory_unit_text": unit.text,
        "subject": unit.subject,
        "relation": unit.relation,
        "value": unit.value,
        "subject_entity_ref": classify_subject_entity_ref(unit, segment),
        "source_refs": refs,
        "formation_version": unit.formation_version,
        "referenced_time": unit.referenced_time,
        "turn_id": source_turn.turn_id,
        "source_start": first_ref["source_start"],
        "source_end": first_ref["source_end"],
        "role": source_turn.role,
        "entities": list(extract_entities(unit.text, configured_entities)),
        "temporal_mentions": [dict(item) for item in temporal_mentions],
        "dates_mentioned": [
            {
                "original": item["original_expression"],
                "parsed": item["normalized_start"],
            }
            for item in temporal_mentions
        ],
        "provenance": provenance.__dict__,
    }

def _ingest_grounded_spans(
    segment: ColdDraftSegment,
    *,
    backend: MemoryBackend,
    state_store: IngestionStateStore,
    ingestion_version: str,
    configured_entities: tuple[str, ...] = (),
) -> IngestionResult:
    version = ingestion_version
    invalid = MagmaMemoryAdapter(
        backend,
        state_store,
        ingestion_version=version,
        configured_entities=configured_entities,
    )._validate(segment)
    if invalid:
        return IngestionResult(
            segment.segment_id,
            version,
            "failed",
            retryable=False,
            safe_error_code=invalid,
        )

    units = build_grounded_spans(segment.turns)
    unit_ids = tuple(unit.unit_id for unit in units)
    key = state_store.key(segment.segment_id, version)
    try:
        state = state_store.get(key)
    except ValueError:
        return IngestionResult(
            segment.segment_id,
            version,
            "failed",
            retryable=False,
            safe_error_code="state_corrupt",
        )

    if state is not None:
        state_error = _grounded_state_error(
            state,
            segment_id=segment.segment_id,
            ingestion_version=version,
            unit_ids=unit_ids,
        )
        if state_error is not None:
            return IngestionResult(
                segment.segment_id,
                version,
                "failed",
                retryable=False,
                safe_error_code=state_error,
            )
        if state["status"] == "completed":
            return IngestionResult(
                segment.segment_id,
                version,
                "completed",
                tuple(state["memory_ids"]),
                already_ingested=True,
            )
    else:
        try:
            state_store.put(
                key,
                _grounded_state_record(
                    segment.segment_id,
                    ingestion_version=version,
                    status="pending",
                    unit_ids=unit_ids,
                    memory_ids=(),
                ),
            )
        except Exception:
            return IngestionResult(
                segment.segment_id,
                version,
                "failed",
                retryable=True,
                safe_error_code="state_write_failed",
            )

    if not units:
        try:
            state_store.put(
                key,
                _grounded_state_record(
                    segment.segment_id,
                    ingestion_version=version,
                    status="completed",
                    unit_ids=unit_ids,
                    memory_ids=(),
                ),
            )
        except Exception:
            return IngestionResult(
                segment.segment_id,
                version,
                "failed",
                retryable=True,
                safe_error_code="state_write_failed",
            )
        return IngestionResult(segment.segment_id, version, "completed")

    turns_by_id = {turn.turn_id: turn for turn in segment.turns}
    memory_ids: list[str] = []
    try:
        state_store.put(
            key,
            _grounded_state_record(
                segment.segment_id,
                ingestion_version=version,
                status="in_progress",
                unit_ids=unit_ids,
                memory_ids=memory_ids,
            ),
        )
        for unit in units:
            existing = backend.find_memory_id(unit.unit_id)
            if existing is not None:
                memory_id = existing
            else:
                source_turn = turns_by_id[unit.turn_id]
                memory_id = backend.add_event(
                    unit.text,
                    source_turn.timestamp,
                    _grounded_event_metadata(
                        segment,
                        unit,
                        source_turn,
                        ingestion_version=version,
                        configured_entities=configured_entities,
                    ),
                )
                backend.persist()
            memory_ids.append(memory_id)
            state_store.put(
                key,
                _grounded_state_record(
                    segment.segment_id,
                    ingestion_version=version,
                    status="in_progress",
                    unit_ids=unit_ids,
                    memory_ids=memory_ids,
                ),
            )

        backend.create_relationships(memory_ids)
        backend.persist()
        state_store.put(
            key,
            _grounded_state_record(
                segment.segment_id,
                ingestion_version=version,
                status="completed",
                unit_ids=unit_ids,
                memory_ids=memory_ids,
            ),
        )
        return IngestionResult(
            segment.segment_id,
            version,
            "completed",
            tuple(memory_ids),
        )
    except Exception:
        try:
            state_store.put(
                key,
                _grounded_state_record(
                    segment.segment_id,
                    ingestion_version=version,
                    status="failed",
                    unit_ids=unit_ids,
                    memory_ids=memory_ids,
                    safe_error_code="memory_write_failed",
                ),
            )
        except Exception:
            pass
        return IngestionResult(
            segment.segment_id,
            version,
            "failed",
            tuple(memory_ids),
            retryable=True,
            safe_error_code="memory_write_failed",
        )


def _grounded_state_error(
    state: dict[str, Any],
    *,
    segment_id: str,
    ingestion_version: str,
    unit_ids: tuple[str, ...],
) -> str | None:
    required_fields = {
        "segment_id",
        "ingestion_version",
        "status",
        "unit_ids",
        "memory_ids",
    }
    if (
        not required_fields.issubset(state)
        or not set(state).issubset(required_fields | {"safe_error_code"})
        or state.get("segment_id") != segment_id
        or state.get("ingestion_version") != ingestion_version
        or state.get("status")
        not in {"pending", "in_progress", "completed", "failed"}
        or not isinstance(state.get("unit_ids"), list)
        or not all(
            isinstance(unit_id, str) and bool(unit_id)
            for unit_id in state.get("unit_ids", ())
        )
        or not isinstance(state.get("memory_ids"), list)
        or not all(
            isinstance(memory_id, str) and bool(memory_id)
            for memory_id in state.get("memory_ids", ())
        )
        or len(state.get("memory_ids", ())) > len(state.get("unit_ids", ()))
        or (
            state.get("status") == "completed"
            and len(state.get("memory_ids", ()))
            != len(state.get("unit_ids", ()))
        )
    ):
        return "state_corrupt"
    if tuple(state["unit_ids"]) != unit_ids:
        return "grounded_manifest_mismatch"
    return None


def _grounded_state_record(
    segment_id: str,
    *,
    ingestion_version: str,
    status: str,
    unit_ids: tuple[str, ...] | list[str],
    memory_ids: tuple[str, ...] | list[str],
    safe_error_code: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "segment_id": segment_id,
        "ingestion_version": ingestion_version,
        "status": status,
        "unit_ids": list(unit_ids),
        "memory_ids": list(memory_ids),
    }
    if safe_error_code is not None:
        record["safe_error_code"] = safe_error_code
    return record


def _grounded_event_metadata(
    segment: ColdDraftSegment,
    unit: GroundedSpanUnit,
    source_turn: ColdDraftTurn,
    *,
    ingestion_version: str,
    configured_entities: tuple[str, ...],
) -> dict[str, Any]:
    if unit.source_role != source_turn.role:
        raise ValueError("grounded unit source role mismatch")
    span_turn = ColdDraftTurn(
        turn_id=source_turn.turn_id,
        role=source_turn.role,
        content=unit.text,
        timestamp=source_turn.timestamp,
        source_timezone=source_turn.source_timezone,
        timezone_source=source_turn.timezone_source,
    )
    temporal_mentions = [
        item.__dict__ for item in normalize_temporal_references(span_turn)
    ]
    provenance = SourceProvenance(
        segment_id=segment.segment_id,
        conversation_id=segment.conversation_id,
        turn_id=source_turn.turn_id,
        source_role=unit.source_role,
        source_timestamp=source_turn.timestamp.isoformat(),
        source_timezone=source_turn.source_timezone,
        ingestion_version=ingestion_version,
        timezone_source=source_turn.timezone_source,
    )
    return {
        "evidence_id": unit.unit_id,
        "turn_id": unit.turn_id,
        "source_start": unit.start,
        "source_end": unit.end,
        "role": unit.source_role,
        "entities": list(extract_entities(unit.text, configured_entities)),
        "temporal_mentions": [dict(item) for item in temporal_mentions],
        "dates_mentioned": [
            {
                "original": item["original_expression"],
                "parsed": item["normalized_start"],
            }
            for item in temporal_mentions
        ],
        "provenance": provenance.__dict__,
    }
