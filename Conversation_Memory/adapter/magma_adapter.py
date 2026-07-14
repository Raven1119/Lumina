from __future__ import annotations

import hashlib
from datetime import datetime

from ingestion.entities import extract_entities
from ingestion.state_store import IngestionStateStore
from ingestion.temporal import normalize_temporal_references
from recall.rendering import bound_evidence

from .backend import MemoryBackend
from .models import (
    ColdDraftSegment,
    IngestionResult,
    MemoryContext,
    MemoryEvidence,
    RecallPolicy,
    SourceProvenance,
)


class MagmaMemoryAdapter:
    def __init__(
        self,
        backend: MemoryBackend,
        state_store: IngestionStateStore,
        *,
        ingestion_version: str = "cold-draft-v1",
        configured_entities: tuple[str, ...] = (),
    ):
        self.backend = backend
        self.state_store = state_store
        self.ingestion_version = ingestion_version
        self.configured_entities = configured_entities

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
        if not isinstance(query, str) or not query.strip():
            return MemoryContext(query if isinstance(query, str) else "", safe_error_code="invalid_query")
        try:
            candidates = self.backend.recall(query, policy)
            items = []
            for candidate in candidates:
                raw = candidate.metadata.get("provenance")
                evidence_id = candidate.metadata.get("evidence_id")
                if not isinstance(raw, dict) or not isinstance(evidence_id, str):
                    continue
                try:
                    provenance = SourceProvenance(**raw)
                except TypeError:
                    continue
                items.append(MemoryEvidence(evidence_id, candidate.text, candidate.timestamp, candidate.score, provenance))
            items.sort(key=lambda item: (
                -(item.score if item.score is not None else -1.0),
                item.timestamp or "",
                item.evidence_id,
            ))
            limit = min(policy.top_k, policy.max_evidence_items)
            evidence, rendered, truncated = bound_evidence(items, count=limit, max_chars=policy.max_chars)
            return MemoryContext(query.strip(), evidence, rendered, truncated)
        except Exception:
            return MemoryContext(query.strip(), safe_error_code="recall_unavailable")
