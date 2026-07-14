from __future__ import annotations

import hashlib
from datetime import datetime

from ingestion.entities import extract_entities
from ingestion.state_store import IngestionStateStore
from ingestion.temporal import normalize_temporal_references
from recall.rendering import bound_evidence
from recall.relevance import CosineEmbeddingRelevanceScorer

from .backend import MemoryBackend
from .interfaces import RelevanceScorer
from .models import (
    ColdDraftSegment,
    IngestionResult,
    MemoryContext,
    MemoryEvidence,
    RecallPolicy,
    SourceProvenance,
)


_DEFAULT_RELEVANCE_SCORER = object()


class MagmaMemoryAdapter:
    def __init__(
        self,
        backend: MemoryBackend,
        state_store: IngestionStateStore,
        *,
        ingestion_version: str = "cold-draft-v1",
        configured_entities: tuple[str, ...] = (),
        relevance_scorer: RelevanceScorer | None | object = _DEFAULT_RELEVANCE_SCORER,
    ):
        self.backend = backend
        self.state_store = state_store
        self.ingestion_version = ingestion_version
        self.configured_entities = configured_entities
        self.relevance_scorer = (
            CosineEmbeddingRelevanceScorer()
            if relevance_scorer is _DEFAULT_RELEVANCE_SCORER
            else relevance_scorer
        )
        self.last_recall_diagnostics: dict[str, int | bool] = {
            "gate_enabled": False,
            "candidates_scored": 0,
        }

    @classmethod
    def create_real(
        cls,
        persist_dir,
        state_store: IngestionStateStore,
        **kwargs,
    ) -> "MagmaMemoryAdapter":
        from . import backend as backend_module
        try:
            backend = backend_module.RealMagmaBackend(persist_dir)
        except Exception:
            backend = backend_module.UnavailableMemoryBackend()
        return cls(backend, state_store, **kwargs)

    def _evidence_id(self, segment_id: str, turn_id: str) -> str:
        raw = f"{segment_id}\0{turn_id}\0{self.ingestion_version}".encode()
        return hashlib.sha256(raw).hexdigest()

    def _validate(self, segment: ColdDraftSegment) -> str | None:
        if not segment.segment_id:
            return "invalid_segment_id"
        if segment.state != "pending_digest":
            return "segment_not_pending"
        if segment.schema_version != "1":
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
        return None

    def ingest(self, segment: ColdDraftSegment) -> IngestionResult:
        invalid = self._validate(segment)
        if invalid:
            return IngestionResult(segment.segment_id, self.ingestion_version, "failed", retryable=False, safe_error_code=invalid)
        key = self.state_store.key(segment.segment_id, self.ingestion_version)
        try:
            state = self.state_store.get(key)
        except ValueError:
            return IngestionResult(segment.segment_id, self.ingestion_version, "failed", retryable=False, safe_error_code="state_corrupt")
        if state and state.get("status") == "completed":
            return IngestionResult(segment.segment_id, self.ingestion_version, "completed", tuple(state.get("memory_ids", [])), True)

        memory_ids = list((state or {}).get("memory_ids", []))
        try:
            if state is None:
                self.state_store.put(key, {"status": "pending", "memory_ids": []})
            self.state_store.put(key, {"status": "in_progress", "memory_ids": memory_ids})
            for turn in segment.turns:
                evidence_id = self._evidence_id(segment.segment_id, turn.turn_id)
                existing = self.backend.find_memory_id(evidence_id)
                if existing:
                    if existing not in memory_ids:
                        memory_ids.append(existing)
                    continue
                temporal = normalize_temporal_references(turn, segment.source_timezone)
                provenance = SourceProvenance(
                    segment.segment_id,
                    segment.conversation_id,
                    turn.turn_id,
                    turn.timestamp.isoformat(),
                    segment.source_timezone,
                    self.ingestion_version,
                )
                metadata = {
                    "evidence_id": evidence_id,
                    "role": turn.role,
                    "entities": list(extract_entities(turn.content, self.configured_entities)),
                    "temporal_references": [ref.__dict__ for ref in temporal],
                    "provenance": provenance.__dict__,
                    "original_text": turn.content,
                }
                memory_id = self.backend.add_event(turn.content, turn.timestamp, metadata)
                self.backend.persist()
                memory_ids.append(memory_id)
                self.state_store.put(key, {"status": "in_progress", "memory_ids": memory_ids})
            self.backend.create_relationships(memory_ids)
            self.backend.persist()
            self.state_store.put(key, {"status": "completed", "memory_ids": memory_ids})
            return IngestionResult(segment.segment_id, self.ingestion_version, "completed", tuple(memory_ids))
        except Exception:
            try:
                self.state_store.put(key, {"status": "failed", "memory_ids": memory_ids})
            except Exception:
                pass
            return IngestionResult(segment.segment_id, self.ingestion_version, "failed", tuple(memory_ids), retryable=True, safe_error_code="memory_write_failed")

    def recall(self, query: str, policy: RecallPolicy) -> MemoryContext:
        self.last_recall_diagnostics = {
            "gate_enabled": policy.min_relevance is not None,
            "candidates_scored": 0,
        }
        if not isinstance(query, str) or not query.strip():
            return MemoryContext(query if isinstance(query, str) else "", safe_error_code="invalid_query")
        try:
            candidates = self.backend.recall(query, policy)
        except Exception:
            return MemoryContext(query.strip(), safe_error_code="recall_unavailable")

        try:
            projected = []
            for candidate in candidates:
                raw = candidate.metadata.get("provenance")
                evidence_id = candidate.metadata.get("evidence_id")
                if not isinstance(raw, dict) or not isinstance(evidence_id, str):
                    continue
                try:
                    provenance = SourceProvenance(**raw)
                except TypeError:
                    continue
                projected.append((candidate, MemoryEvidence(
                    evidence_id,
                    candidate.text,
                    candidate.timestamp,
                    provenance,
                )))
            projected.sort(key=lambda pair: (
                -(pair[0].score if pair[0].score is not None else -1.0),
                pair[1].timestamp or "",
                pair[1].evidence_id,
            ))

            items: list[MemoryEvidence]
            if policy.min_relevance is None:
                items = [item for _, item in projected]
            elif not projected:
                items = []
            else:
                projected = projected[:policy.max_relevance_candidates]
                if self.relevance_scorer is None:
                    return MemoryContext(
                        query.strip(),
                        safe_error_code="relevance_unavailable",
                    )
                scoring = self.relevance_scorer.score(
                    query.strip(),
                    [candidate for candidate, _ in projected],
                )
                if scoring.safe_error_code is not None:
                    return MemoryContext(
                        query.strip(),
                        safe_error_code="relevance_unavailable",
                    )
                self.last_recall_diagnostics["candidates_scored"] = (
                    scoring.candidates_scored
                )
                if len(scoring.scored_candidates) != len(projected):
                    return MemoryContext(
                        query.strip(),
                        safe_error_code="relevance_unavailable",
                    )
                items = []
                for (candidate, item), scored in zip(
                    projected,
                    scoring.scored_candidates,
                ):
                    if scored.candidate is not candidate:
                        return MemoryContext(
                            query.strip(),
                            safe_error_code="relevance_unavailable",
                        )
                    if scored.relevance_score >= float(policy.min_relevance):
                        items.append(MemoryEvidence(
                            item.evidence_id,
                            item.text,
                            item.timestamp,
                            item.provenance,
                            scored.relevance_score,
                        ))
            limit = min(policy.top_k, policy.max_evidence_items)
            evidence, rendered, truncated = bound_evidence(items, count=limit, max_chars=policy.max_chars)
            return MemoryContext(query.strip(), evidence, rendered, truncated)
        except Exception:
            return MemoryContext(query.strip(), safe_error_code=(
                "relevance_unavailable"
                if policy.min_relevance is not None
                else "recall_unavailable"
            ))
