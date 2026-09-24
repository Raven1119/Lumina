from __future__ import annotations

import os
from datetime import UTC, datetime
from dataclasses import asdict, is_dataclass
from math import isfinite
from numbers import Real
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, TYPE_CHECKING

from Conversation_Memory.ingestion.entities import extract_entities
from Conversation_Memory.ingestion.state_store import IngestionStateStore
from Conversation_Memory.ingestion.temporal import normalize_temporal_references
from Conversation_Memory.recall.bge_reranker import BgeReranker
from Conversation_Memory.recall.hindsight_scoring import score_hindsight_post_rerank
from Conversation_Memory.recall.rendering import bound_evidence_groups

from ._grounded_spans import GroundedSpanUnit, build_grounded_spans
from .backend import MemoryBackend
from .controlled_relation import UNRESOLVED, ControlledRelationResolver
from .entity_consolidation import (
    EntityBinding,
    EntityCandidate,
    EntityConsolidationError,
    MentionEntityBinding,
    resolve_entity_binding,
    resolve_mention_entity_binding,
)
from .entity_mentions import MentionExtractionError, ground_unit_mentions
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
from .reliable_formation import FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6
from .models import (
    AssociativeMemoryContext,
    ColdDraftSegment,
    ColdDraftTurn,
    IngestionResult,
    MemoryContext,
    PreparedRecall,
    MemoryEvidence,
    RecallPolicy,
    SourceProvenance,
    SourceMemoryContext,
    ExperienceContext,
    EntityMention,
    EntityMentionContext,
)
from .user_self import (
    CURRENT_USER_ENTITY_REF,
    candidate_entity_ref,
    classify_subject_entity_ref,
    classify_target_entity_ref,
    entity_marked_text,
)


if TYPE_CHECKING:
    from .first_hit import FirstHitPolicy
    from .graph_read_query import GraphReadQuery
    from core.cold_draft_store import ColdDraftStore


_GROUNDED_SPAN_INGESTION_VERSION = "grounded-span-v2"
_RELATION_RESOLVER = ControlledRelationResolver()
_USER_SELF_BINDING_ENV = "LUMINA_USER_SELF_BINDING_ENABLED"
_ENTITY_CANDIDATE_LIMIT = 20


def _user_self_binding_enabled() -> bool:
    raw = os.environ.get(_USER_SELF_BINDING_ENV)
    if raw is None:
        return True
    return raw.strip().casefold() not in {"0", "false", "no", "off"}


from .body_payload import FORMATION_BODY_VERSION


class MagmaMemoryAdapter:
    def __init__(
        self,
        backend: MemoryBackend,
        state_store: IngestionStateStore,
        *,
        ingestion_version: str = _GROUNDED_SPAN_INGESTION_VERSION,
        configured_entities: tuple[str, ...] = (),
        formation_model: FormationModel | None = None,
        first_hit: FirstHitPolicy | None = None,
        cold_store: ColdDraftStore | None = None,
        associative_read_profile: str = "first-hit-v1",
        body_recall_policy=None,
    ):
        if (
            formation_model is not None
            and ingestion_version not in {FORMATION_VERSION, "grounded-formation-v2", FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6, FORMATION_BODY_VERSION}
        ):
            raise ValueError("formation_ingestion_version_required")
        if not isinstance(associative_read_profile, str) or associative_read_profile not in {"first-hit-v1", "reliable-v1", "reliable-v2", "graph-read-v1", "graph-read-v2", "body-recall-v1", "calibrated-first-hit-v1", "semantic-associative-v1", "semantic-associative-v2"}:
            raise ValueError("invalid_associative_read_profile")
        if associative_read_profile in {"reliable-v1", "reliable-v2", "graph-read-v1", "graph-read-v2", "body-recall-v1", "calibrated-first-hit-v1", "semantic-associative-v1", "semantic-associative-v2"} and first_hit is None:
            raise ValueError("reliable_read_requires_first_hit")
        from ._body_recall import BodyRecallPolicy
        if body_recall_policy is not None and not isinstance(body_recall_policy, BodyRecallPolicy):
            raise ValueError("body_policy_invalid")
        self.body_recall_policy = body_recall_policy or BodyRecallPolicy()
        self.associative_read_profile = associative_read_profile
        self.backend = backend
        self.state_store = state_store
        self.ingestion_version = ingestion_version
        self.configured_entities = configured_entities
        self.formation_model = formation_model
        if first_hit is not None:
            from .first_hit import FirstHitPolicy
            if not isinstance(first_hit, FirstHitPolicy):
                # Existing callers use both package spellings. Revalidate an
                # equivalent policy through this module's canonical constructor.
                if (not is_dataclass(first_hit) or isinstance(first_hit, type)
                        or type(first_hit).__name__ != "FirstHitPolicy"):
                    raise ValueError("invalid_first_hit_policy")
                try:
                    first_hit = FirstHitPolicy(**asdict(first_hit))
                except (TypeError, ValueError):
                    raise ValueError("invalid_first_hit_policy") from None
            if ingestion_version not in {"grounded-formation-v2", FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6, FORMATION_BODY_VERSION}:
                raise ValueError("first_hit_requires_supported_formation_version")
        self.first_hit = first_hit
        self.cold_store = cold_store
        self._bge_reranker = None
        self._bge_reranker_load_attempted = False
        self._bge_reranker_lock = Lock()
        self._calibrated_index_error = None
        if associative_read_profile in {"calibrated-first-hit-v1", "semantic-associative-v1", "semantic-associative-v2"}:
            self._rebuild_calibrated_index()

    def _rebuild_calibrated_index(self) -> None:
        try:
            self.backend.rebuild_calibrated_read_index()
            self._calibrated_index_error = None
        except Exception:
            self._calibrated_index_error = "calibrated_index_unavailable"

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
        if self.associative_read_profile in {"calibrated-first-hit-v1", "semantic-associative-v1", "semantic-associative-v2"}:
            try:
                return self._ingest_without_calibrated_rebuild(segment)
            finally:
                self._rebuild_calibrated_index()
        return self._ingest_without_calibrated_rebuild(segment)

    def _ingest_without_calibrated_rebuild(self, segment: ColdDraftSegment) -> IngestionResult:
        if self.ingestion_version in {"grounded-formation-v2", FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5, FORMATION_RELIABLE_VERSION_V6, FORMATION_BODY_VERSION}:
            if self.formation_model is None:
                return IngestionResult(segment.segment_id, self.ingestion_version,
                                       "failed", safe_error_code="formation_model_unavailable")
            from ._entity_ingestion import ingest_entity_formation
            return ingest_entity_formation(self, segment)
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
    def recall_mentions(self, query: str, *, limit: int = 20) -> EntityMentionContext:
        """Read bounded occurrence records through Lumina DTOs, without inference."""
        if not isinstance(query, str) or not query.strip():
            return EntityMentionContext(query if isinstance(query, str) else "",
                                        safe_error_code="invalid_query")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            return EntityMentionContext(query, safe_error_code="invalid_limit")
        try:
            rows = self.backend.list_entity_mentions(query.strip(), limit=limit + 1)
            mentions = tuple(EntityMention(
                mention_id=row["mention_id"], surface=row["surface"],
                source_start=row["source_start"], source_end=row["source_end"],
                provenance=SourceProvenance(**row["provenance"]),
                resolved=row.get("entity_ref") is not None,
                ambiguous=bool(row.get("candidate_entity_refs")),
            ) for row in rows[:limit])
            return EntityMentionContext(query.strip(), mentions, len(rows) > limit)
        except Exception:
            return EntityMentionContext(query.strip(), safe_error_code="recall_unavailable")

    def recall(self, query: str | GraphReadQuery, policy: RecallPolicy) -> MemoryContext:
        dispatched = self._reliable_dispatch(query, policy)
        if dispatched is not None:
            return dispatched
        return self._recall(query, policy)

    def _reliable_dispatch(
        self, query: str | GraphReadQuery, policy: RecallPolicy,
    ) -> MemoryContext | None:
        """Reliable read profiles serve the ordinary Recall boundary too.

        The associative result's Fact channel is itself a MemoryContext; the
        combined rendering keeps the always-visible bodies plus the bounded
        source supplement. A non-None BGE/Hindsight floor stays an explicit
        policy conflict inside the associative read. Legacy profiles and
        adapters without FirstHit keep the original BGE/Hindsight read.
        """
        if self.associative_read_profile in {"semantic-associative-v1", "semantic-associative-v2"}:
            return MemoryContext(query if isinstance(query, str) else "",
                                 safe_error_code="semantic_selection_required")
        if (
            self.associative_read_profile in ("reliable-v1", "reliable-v2", "graph-read-v1", "graph-read-v2", "body-recall-v1", "calibrated-first-hit-v1")
            and self.first_hit is not None
        ):
            result = self.recall_associative(
                query, policy, include_sources=True, source_context_turns=0,
            )
            return MemoryContext(
                result.facts.query,
                result.facts.evidence,
                result.rendered_text,
                result.truncated,
                result.safe_error_code,
            )
        return None


    def _activate_first_hit(self, cue: str, *, target_entity_refs=None,
                            exclude_evidence_ids=()):
        from ._associative_recall import activate
        return activate(self, cue, target_entity_refs=target_entity_refs,
                        exclude_evidence_ids=exclude_evidence_ids)

    def recall_associative(self, cue: str | GraphReadQuery, output_policy: RecallPolicy | None = None,
                           *, include_sources: bool = False,
                           source_context_turns: int = 0) -> AssociativeMemoryContext:
        """Explicit first-hit facts and bounded owner source expansion."""
        from ._associative_recall import recall_associative
        return recall_associative(self, cue, output_policy or RecallPolicy(),
                                  include_sources=include_sources,
                                  source_context_turns=source_context_turns)

    def recall_recent_sources(self, cue: str, policy: RecallPolicy | None = None) -> SourceMemoryContext:
        """Explicit recent raw dialogue, including windows without formed facts."""
        policy = policy or RecallPolicy()
        if self.first_hit is None:
            return SourceMemoryContext(cue, safe_error_code="first_hit_not_configured")
        if self.cold_store is None:
            return SourceMemoryContext(cue, safe_error_code="cold_source_unavailable")
        try:
            return self.cold_store.search_recent_sources(
                cue, limit=policy.top_k, max_chars=policy.max_chars,
                max_items=policy.max_evidence_items,
            )
        except Exception:
            return SourceMemoryContext(cue, safe_error_code="cold_source_unavailable")

    def ingest_sources(self, segment: ColdDraftSegment) -> IngestionResult:
        """Explicit source index; never completes Formation or consumes Cold."""
        from .source_memory import ingest_sources
        return ingest_sources(self, segment)

    def recall_sources(self, query: str, policy: RecallPolicy) -> SourceMemoryContext:
        """Read typed raw source ranges from an isolated source index."""
        from .source_memory import recall_sources
        return recall_sources(self, query, policy)

    def recall_source_context(self, query: str, policy: RecallPolicy, *,
                              fact_memory=None) -> SourceMemoryContext:
        """Explicit complete-source read with optional fact/mention navigation."""
        from .source_context import recall_source_context
        return recall_source_context(self, query, policy, fact_memory=fact_memory)

    def open_source_reader(self, question: str, limits):
        """Explicit transient search/range reader; no production Recall change."""
        from .source_reader import SourceReader
        return SourceReader(self, question, limits)

    def recall_experiences(self, cue: str, policy: RecallPolicy) -> ExperienceContext:
        """Explicit zero-generation dialogue views over independently indexed sources."""
        from .source_experiences import recall_experiences
        return recall_experiences(self, cue, policy)

    def prepare_recall(self, query: str, policy: RecallPolicy) -> PreparedRecall:
        """Perform the same single read and retain its exact subset view."""
        if self.associative_read_profile == "semantic-associative-v1":
            from ._semantic_recall import prepare_semantic_recall
            return prepare_semantic_recall(self, query, policy)
        if self.associative_read_profile == "semantic-associative-v2":
            from ._semantic_recall import prepare_semantic_recall_v2
            return prepare_semantic_recall_v2(self, query, policy)
        if self.associative_read_profile in {"reliable-v1", "reliable-v2", "graph-read-v1", "graph-read-v2"}:
            from ._reliable_recall import prepare_reliable_result
            result = self.recall_associative(query, policy, include_sources=True,
                                             source_context_turns=0)
            return prepare_reliable_result(result, policy)
        dispatched = self._reliable_dispatch(query, policy)
        if dispatched is not None:
            # Other explicit read profiles still lack exact prepared blocks;
            # keep their original context without pretending it is subsettable.
            return PreparedRecall(dispatched, _dependencies=None)
        metadata = {}
        context = self._recall(query, policy, _prepared_data=metadata)
        if not context.evidence:
            return PreparedRecall(context)
        try:
            return PreparedRecall(context, metadata["blocks"], metadata["dependencies"])
        except Exception:
            # Bad private selection metadata must not erase a successful read
            # or require another retrieval to recover its original context.
            return PreparedRecall(context, _dependencies=None)

    def _recall(
        self, query: str, policy: RecallPolicy, *, _prepared_data: dict | None = None,
    ) -> MemoryContext:
        if not isinstance(query, str) or not query.strip():
            return MemoryContext(query if isinstance(query, str) else "", safe_error_code="invalid_query")
        normalized_query = query.strip()
        target_entity_refs = ()
        entity_candidates_truncated = False
        try:
            if _user_self_binding_enabled():
                target_entity_ref = classify_target_entity_ref(normalized_query)
                resolver = getattr(self.backend, "resolve_target_entity_refs", None)
                if callable(resolver):
                    entity_limit = min(20, policy.max_nodes)
                    target_entity_refs = tuple(resolver(
                        normalized_query, limit=entity_limit + 1,
                    ))
                    if target_entity_ref is not None:
                        target_entity_refs = tuple(dict.fromkeys((target_entity_ref, *target_entity_refs)))
                    entity_candidates_truncated = len(target_entity_refs) > entity_limit
                    target_entity_ref = target_entity_refs[0] if len(target_entity_refs) == 1 else None
                    target_entity_refs = target_entity_refs[:entity_limit]
                elif target_entity_ref is None:
                    # ordinary persisted entities: deterministic exact-surface
                    # lookup, only when CURRENT_USER classification found none
                    target_entity_ref = self.backend.resolve_target_entity_ref(
                        normalized_query
                    )
            else:
                target_entity_ref = None
        except Exception:
            target_entity_ref = None
            target_entity_refs = ()
        try:
            if target_entity_refs:
                candidates = tuple(self.backend.recall(
                    normalized_query, policy, target_entity_refs=target_entity_refs,
                ))
            else:
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
            stats = getattr(self.backend, "last_recall_stats", {})
            retrieval_truncated = entity_candidates_truncated or bool(
                isinstance(stats, dict) and stats.get("budget_exhausted", False)
            )
        except Exception:
            retrieval_truncated = entity_candidates_truncated

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
            return MemoryContext(normalized_query, truncated=retrieval_truncated)

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
            projected = {}
            for _original_index, candidate in rerankable:
                raw = candidate.metadata.get("provenance")
                evidence_id = candidate.metadata.get("evidence_id")
                if not isinstance(raw, dict) or not isinstance(evidence_id, str):
                    continue
                try:
                    provenance = SourceProvenance(**raw)
                except (TypeError, ValueError):
                    continue
                projected.setdefault(evidence_id, MemoryEvidence(
                    evidence_id,
                    candidate.text,
                    candidate.timestamp,
                    provenance,
                ))

            by_id = {candidate.metadata.get("evidence_id"): candidate
                     for _, candidate in rerankable}
            groups = []
            missing_dependency = False
            for (_original_index, candidate), _score in ranked:
                evidence_id = candidate.metadata.get("evidence_id")
                if evidence_id not in projected:
                    continue
                chain = _association_evidence_ids(candidate, by_id)
                if chain is None or any(eid not in projected for eid in chain):
                    missing_dependency = True
                    continue
                groups.append([projected[eid] for eid in chain])
            blocks = [] if _prepared_data is not None else None
            evidence, rendered, truncated = bound_evidence_groups(
                groups,
                count=policy.max_evidence_items,
                max_chars=policy.max_chars,
                **({"source_context_roles": _source_context_roles(rerankable, projected)}
                   if policy.include_source_context else {}),
                **({"_rendered_blocks": blocks} if blocks is not None else {}),
            )
            if _prepared_data is not None:
                _prepared_data["blocks"] = tuple(blocks)
                try:
                    # A visible bridge may itself have a dependency, even when
                    # its own packing group failed. Validate every visible item;
                    # preparation never infers dependencies from rendered labels.
                    dependencies = []
                    for item in evidence:
                        candidate = by_id[item.evidence_id]
                        if (candidate.text != item.text
                                or SourceProvenance(**candidate.metadata["provenance"]) != item.provenance):
                            raise ValueError("prepared_source_mismatch")
                        dependencies.append((item.evidence_id,
                                             _association_evidence_ids(candidate, by_id)))
                    _prepared_data["dependencies"] = tuple(dependencies)
                except Exception:
                    pass
            return MemoryContext(normalized_query, evidence, rendered,
                                 truncated or retrieval_truncated or missing_dependency)
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


def _source_context_roles(rerankable, projected):
    """Keep stored role bindings private, exposing only call-local labels.

    Labels express shared existing bindings, not a new claim that different
    labels must denote different real-world identities. Ordinary mentions and
    name surfaces cannot supply subject/object roles here.
    """
    labels: dict[str, str] = {}
    roles: dict[str, tuple[str | None, str | None]] = {}
    for _, candidate in rerankable:
        evidence_id = candidate.metadata.get("evidence_id")
        if evidence_id not in projected or evidence_id in roles:
            continue
        values = []
        for field in ("subject_entity_ref", "object_entity_ref"):
            ref = candidate.metadata.get(field)
            label = None
            if isinstance(ref, str) and ref.strip():
                label = labels.setdefault(ref, f"I{len(labels) + 1}")
            values.append(label)
        roles[evidence_id] = (values[0], values[1])
    return roles


def _association_evidence_ids(candidate, by_id) -> tuple[str, ...] | None:
    """Only an actual two-fact role projection can carry a bridge dependency."""
    evidence_id = candidate.metadata.get("evidence_id")
    raw = candidate.metadata.get("association_chain_evidence_ids")
    if raw is None:
        return (evidence_id,)
    if (not isinstance(raw, (list, tuple)) or len(raw) != 2
            or not all(isinstance(eid, str) and eid for eid in raw)
            or raw[1] != evidence_id or raw[0] == raw[1]
            or candidate.metadata.get("association_bridge_evidence_ids") != [raw[0]]
            or raw[0] not in by_id):
        return None
    bridge = by_id[raw[0]]
    bridge_roles = {bridge.metadata.get("subject_entity_ref"),
                    bridge.metadata.get("object_entity_ref")}
    endpoint_roles = {candidate.metadata.get("subject_entity_ref"),
                      candidate.metadata.get("object_entity_ref")}
    if (not all(isinstance(ref, str) and ref for ref in bridge_roles)
            or not (bridge_roles & endpoint_roles)):
        return None
    try:
        for item in (bridge, candidate):
            SourceProvenance(**item.metadata.get("provenance", {}))
    except (TypeError, ValueError):
        return None
    return tuple(raw)


def _score_candidates(
    reranker,
    normalized_query: str,
    target_entity_ref: str | None,
    rerankable,
) -> tuple[float, ...]:
    """Score source facts or complete two-fact paths using the fixed BGE model.

    The entity marker enters a (query, candidate) pair only when both sides
    carry the same non-null entity ref; every other pair is scored with the
    current fact text. A verified role path scores its bridge plus endpoint as
    one retrieval view; selection must subsequently include both original
    evidence items. This changes the scoring input, never stored fact text or
    the BGE/Hindsight formula. Missing, invalid, or over-window paths cannot
    gain context; token-fit is checked on the actual marked scoring pair.
    """
    marked_texts: list[str] = []
    marked_positions: list[int] = []
    plain_texts: list[str] = []
    plain_positions: list[int] = []
    by_id = {candidate.metadata.get("evidence_id"): candidate
             for _, candidate in rerankable}
    for position, (_index, candidate) in enumerate(rerankable):
        chain = _association_evidence_ids(candidate, by_id)
        text = candidate.text
        ref = candidate_entity_ref(candidate.metadata)
        marked = target_entity_ref is not None and ref == target_entity_ref
        if chain is not None and len(chain) == 2:
            # Both pieces already passed the same candidate compatibility and
            # source-provenance boundary. No summary or inferred fact is made.
            combined = "\n".join(by_id[eid].text for eid in chain)
            fit_query = entity_marked_text(ref, normalized_query) if marked else normalized_query
            fit_text = entity_marked_text(ref, combined) if marked else combined
            fits_pair = getattr(reranker, "fits_pair", None)
            try:
                complete_pair_fits = callable(fits_pair) and fits_pair(fit_query, fit_text) is True
            except Exception:
                complete_pair_fits = False
            if complete_pair_fits:
                text = combined
        if marked:
            marked_positions.append(position)
            marked_texts.append(entity_marked_text(ref, text))
        else:
            plain_positions.append(position)
            plain_texts.append(text)
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
def _state_entity_bindings(
    state: dict[str, Any] | None,
    units: tuple[GroundedMemoryUnit, ...],
) -> dict[str, EntityBinding]:
    if state is None or "entity_bindings" not in state:
        return {}
    raw_bindings = state["entity_bindings"]
    if not isinstance(raw_bindings, list):
        raise ValueError("state_corrupt")
    unit_ids = {unit.id for unit in units}
    bindings: dict[str, EntityBinding] = {}
    for raw in raw_bindings:
        if not isinstance(raw, dict) or set(raw) != {
            "unit_id", "entity_ref", "canonical_surface",
        }:
            raise ValueError("state_corrupt")
        try:
            binding = EntityBinding(**raw)
        except TypeError as error:
            raise ValueError("state_corrupt") from error
        if (
            binding.unit_id not in unit_ids
            or not binding.entity_ref.strip()
            or not binding.canonical_surface.strip()
            or binding.unit_id in bindings
        ):
            raise ValueError("state_corrupt")
        bindings[binding.unit_id] = binding
    return bindings


def _state_mention_entity_bindings(
    state: dict[str, Any] | None,
    units: tuple[GroundedMemoryUnit, ...],
) -> tuple[MentionEntityBinding, ...]:
    if state is None or "mention_entity_bindings" not in state:
        return ()
    raw_bindings = state["mention_entity_bindings"]
    if not isinstance(raw_bindings, list):
        raise ValueError("state_corrupt")
    refs_by_unit = {
        unit.id: {(ref.turn_id, ref.supporting_span) for ref in unit.source_refs}
        for unit in units
    }
    bindings: list[MentionEntityBinding] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in raw_bindings:
        if not isinstance(raw, dict) or set(raw) != {
            "unit_id", "entity_ref", "canonical_surface",
            "turn_id", "supporting_span",
        }:
            raise ValueError("state_corrupt")
        try:
            binding = MentionEntityBinding(**raw)
        except TypeError as error:
            raise ValueError("state_corrupt") from error
        identity = (
            binding.unit_id,
            binding.canonical_surface,
            binding.turn_id,
            binding.supporting_span,
        )
        if (
            binding.unit_id not in refs_by_unit
            or (binding.turn_id, binding.supporting_span)
            not in refs_by_unit[binding.unit_id]
            or not binding.entity_ref.strip()
            or not binding.canonical_surface.strip()
            or binding.canonical_surface not in binding.supporting_span
            or identity in seen
        ):
            raise ValueError("state_corrupt")
        seen.add(identity)
        bindings.append(binding)
    return tuple(bindings)


def _entity_candidates_with_overlay(
    persisted: tuple[EntityCandidate, ...],
    bindings: Iterable[EntityBinding | MentionEntityBinding],
) -> tuple[EntityCandidate, ...]:
    """Persisted entity candidates plus this run's not-yet-persisted bindings.

    The current user's ref is excluded from both sources: it is bound by the
    classifier, never by exact-surface matching.
    """
    candidates = {
        candidate.entity_ref: candidate
        for candidate in persisted
        if candidate.entity_ref != CURRENT_USER_ENTITY_REF
    }
    for binding in bindings:
        if binding.entity_ref != CURRENT_USER_ENTITY_REF:
            candidates.setdefault(binding.entity_ref, EntityCandidate(
                binding.entity_ref, binding.canonical_surface,
            ))
    return tuple(candidates[ref] for ref in sorted(candidates))


def _resolve_subject_entity_binding(
    segment: ColdDraftSegment,
    unit: GroundedMemoryUnit,
    candidates: tuple[EntityCandidate, ...],
) -> EntityBinding | None:
    """One unit's subject binding: current-user classifier first, then
    deterministic exact-surface matching (ambiguity fails open)."""
    current_user_ref = classify_subject_entity_ref(unit, segment)
    if current_user_ref == CURRENT_USER_ENTITY_REF:
        return EntityBinding(
            unit.id, CURRENT_USER_ENTITY_REF, unit.subject.strip(),
        )
    return resolve_entity_binding(unit, candidates)


def _subject_entity_bindings(
    segment: ColdDraftSegment,
    units: tuple[GroundedMemoryUnit, ...],
    backend: MemoryBackend,
) -> dict[str, EntityBinding]:
    """Resolve every unit's subject binding once, before the first checkpoint."""
    persisted_candidates = _backend_entity_candidates(backend)
    bindings: dict[str, EntityBinding] = {}
    for unit in units:
        binding = _resolve_subject_entity_binding(
            segment,
            unit,
            _entity_candidates_with_overlay(
                persisted_candidates, bindings.values(),
            ),
        )
        if binding is None:
            continue
        bindings[unit.id] = binding
    return bindings


def _grounded_mention_bindings(
    segment: ColdDraftSegment,
    units: tuple[GroundedMemoryUnit, ...],
    *,
    model: FormationModel,
    backend: MemoryBackend,
    subject_bindings: dict[str, EntityBinding],
) -> tuple[MentionEntityBinding, ...]:
    """Ground and bind mention surfaces for validated units of a new segment.

    A mention surface equal to the same unit's bound subject canonical surface
    reuses that subject EntityRef; every other mention keeps the deterministic
    exact-surface binding (unique match REUSE, no match stable CREATE,
    ambiguity fail-open per mention).
    """
    surfaces = ground_unit_mentions(segment, units, model)
    if not surfaces:
        return ()
    persisted = _backend_entity_candidates(backend)
    units_by_id = {unit.id: unit for unit in units}
    bindings: list[MentionEntityBinding] = []
    for surface in surfaces:
        unit = units_by_id[surface.unit_id]
        subject_binding = subject_bindings.get(unit.id)
        if (
            subject_binding is not None
            and surface.surface == subject_binding.canonical_surface
        ):
            bindings.append(MentionEntityBinding(
                unit_id=unit.id,
                entity_ref=subject_binding.entity_ref,
                canonical_surface=surface.surface,
                turn_id=surface.turn_id,
                supporting_span=surface.supporting_span,
            ))
            continue
        binding = resolve_mention_entity_binding(
            unit,
            surface=surface.surface,
            turn_id=surface.turn_id,
            supporting_span=surface.supporting_span,
            candidates=_entity_candidates_with_overlay(persisted, tuple(bindings)),
        )
        if binding is None:
            continue
        bindings.append(binding)
    return tuple(bindings)


def _backend_entity_candidates(backend: MemoryBackend) -> tuple[EntityCandidate, ...]:
    method = getattr(backend, "list_entity_candidates", None)
    if not callable(method):
        return ()
    try:
        candidates = tuple(method(
            limit=_ENTITY_CANDIDATE_LIMIT,
        ))
    except Exception as error:
        raise EntityConsolidationError("entity_candidates_unavailable") from error
    if not all(isinstance(candidate, EntityCandidate) for candidate in candidates):
        raise EntityConsolidationError("entity_candidates_invalid")
    return candidates


def _ordered_entity_bindings(
    units: tuple[GroundedMemoryUnit, ...],
    bindings: dict[str, EntityBinding],
) -> tuple[EntityBinding, ...]:
    return tuple(bindings[unit.id] for unit in units if unit.id in bindings)


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
    entity_bindings: dict[str, EntityBinding] = {}
    mention_bindings: tuple[MentionEntityBinding, ...] = ()
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
        if units:
            try:
                entity_bindings = _subject_entity_bindings(
                    segment, units, backend,
                )
                mention_bindings = _grounded_mention_bindings(
                    segment, units, model=model, backend=backend,
                    subject_bindings=entity_bindings,
                )
            except MentionExtractionError as error:
                return IngestionResult(
                    segment.segment_id, version, "failed", retryable=True,
                    safe_error_code=error.code,
                )
            except EntityConsolidationError:
                return IngestionResult(
                    segment.segment_id, version, "failed", retryable=True,
                    safe_error_code="entity_consolidation_failed",
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
                    entity_bindings=_ordered_entity_bindings(units, entity_bindings),
                    mention_entity_bindings=mention_bindings,
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
        mention_bindings = _state_mention_entity_bindings(state, units)
        if state["status"] == "completed":
            return IngestionResult(
                segment.segment_id, version, "completed",
                tuple(state["memory_ids"]), already_ingested=True,
            )
    if units and state is not None:
        try:
            entity_bindings = _state_entity_bindings(state, units)
            persisted_candidates = _backend_entity_candidates(backend)
            checkpoint_status = state["status"]
            checkpoint_memory_ids = state["memory_ids"]
            for unit in units:
                if unit.id in entity_bindings:
                    continue
                binding = _resolve_subject_entity_binding(
                    segment,
                    unit,
                    _entity_candidates_with_overlay(
                        persisted_candidates, entity_bindings.values(),
                    ),
                )
                if binding is None:
                    continue
                entity_bindings[unit.id] = binding
                state_store.put(
                    key,
                    _formed_state_record(
                        segment.segment_id,
                        ingestion_version=version,
                        status=checkpoint_status,
                        units=units,
                        formed_units=formed_units,
                        memory_ids=checkpoint_memory_ids,
                        semantic_unit_ids=semantic_unit_ids,
                        entity_bindings=_ordered_entity_bindings(
                            units, entity_bindings,
                        ),
                        mention_entity_bindings=mention_bindings,
                    ),
                )
        except (EntityConsolidationError, ValueError):
            return IngestionResult(
                segment.segment_id, version, "failed", retryable=True,
                safe_error_code="entity_consolidation_failed",
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
                    mention_entity_bindings=mention_bindings,
                ),
            )
        except Exception:
            return IngestionResult(
                segment.segment_id, version, "failed", retryable=True,
                safe_error_code="state_write_failed",
            )
        return IngestionResult(segment.segment_id, version, "completed")
    memory_ids: list[str] = []
    mention_bindings_by_unit: dict[str, list[MentionEntityBinding]] = {}
    for binding in mention_bindings:
        mention_bindings_by_unit.setdefault(binding.unit_id, []).append(binding)
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
                entity_bindings=_ordered_entity_bindings(units, entity_bindings),
                mention_entity_bindings=mention_bindings,
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
                        subject_entity_binding=entity_bindings.get(unit.id),
                        mention_entity_bindings=tuple(
                            mention_bindings_by_unit.get(unit.id, ())
                        ),
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
                    entity_bindings=_ordered_entity_bindings(units, entity_bindings),
                    mention_entity_bindings=mention_bindings,
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
                entity_bindings=_ordered_entity_bindings(units, entity_bindings),
                mention_entity_bindings=mention_bindings,
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
                    entity_bindings=_ordered_entity_bindings(units, entity_bindings),
                    mention_entity_bindings=mention_bindings,
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
            required_fields | {
                "safe_error_code", "semantic_unit_ids", "entity_bindings",
                "mention_entity_bindings",
            }
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
    try:
        _state_entity_bindings(state, units)
        _state_mention_entity_bindings(state, units)
    except ValueError:
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
    entity_bindings: tuple[EntityBinding, ...] | None = None,
    mention_entity_bindings: tuple[MentionEntityBinding, ...] | None = None,
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
    if entity_bindings is not None:
        record["entity_bindings"] = [
            {
                "unit_id": binding.unit_id,
                "entity_ref": binding.entity_ref,
                "canonical_surface": binding.canonical_surface,
            }
            for binding in entity_bindings
        ]
    if mention_entity_bindings is not None:
        record["mention_entity_bindings"] = [
            {
                "unit_id": binding.unit_id,
                "entity_ref": binding.entity_ref,
                "canonical_surface": binding.canonical_surface,
                "turn_id": binding.turn_id,
                "supporting_span": binding.supporting_span,
            }
            for binding in mention_entity_bindings
        ]
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
    subject_entity_binding: EntityBinding | None = None,
    mention_entity_bindings: tuple[MentionEntityBinding, ...] = (),
    referenced_time_turn: ColdDraftTurn | None = None,
) -> dict[str, Any]:
    turns = {turn.turn_id: turn for turn in segment.turns}
    reliable = unit.formation_version in {FORMATION_RELIABLE_VERSION, FORMATION_RELIABLE_VERSION_V5,
                                          FORMATION_RELIABLE_VERSION_V6, FORMATION_BODY_VERSION}
    if reliable and (ingestion_version != unit.formation_version
                     or source_turn.turn_id not in turns
                     or asdict(source_turn) != asdict(turns[source_turn.turn_id])):
        raise ValueError("formed origin anchor mismatch")
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
    temporal_turn = source_turn
    temporal_text = unit.text
    if reliable:
        # Statement provenance always uses the explicit origin. An approved
        # relative-time projection can have a different source anchor.
        temporal_text = ""
        if unit.referenced_time is not None and referenced_time_turn is not None:
            temporal_turn = turns.get(referenced_time_turn.turn_id)
            if (temporal_turn is None or asdict(temporal_turn) != asdict(referenced_time_turn)
                    or not any(ref.turn_id == temporal_turn.turn_id and
                               unit.referenced_time in ref.supporting_span for ref in unit.source_refs)):
                raise ValueError("formed referenced-time anchor mismatch")
            temporal_text = unit.referenced_time
    formed_turn = ColdDraftTurn(
        turn_id=temporal_turn.turn_id,
        role=temporal_turn.role,
        content=temporal_text,
        timestamp=temporal_turn.timestamp,
        source_timezone=temporal_turn.source_timezone,
        timezone_source=temporal_turn.timezone_source,
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
    subject_entity_ref = (
        subject_entity_binding.entity_ref
        if subject_entity_binding is not None
        else None if reliable else classify_subject_entity_ref(unit, segment)
    )
    metadata = {
        "evidence_id": unit.id,
        "grounded_memory_unit_id": unit.id,
        "grounded_memory_unit_text": unit.text,
        "subject": unit.subject,
        "relation": unit.relation,
        "value": unit.value,
        "subject_entity_ref": subject_entity_ref,
        "source_refs": refs,
        "formation_version": unit.formation_version,
        "referenced_time": unit.referenced_time,
        "turn_id": source_turn.turn_id,
        "source_start": first_ref["source_start"],
        "source_end": first_ref["source_end"],
        "role": source_turn.role,
        "entities": [] if reliable else list(extract_entities(unit.text, configured_entities)),
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
    if reliable:
        metadata["origin_turn_id"] = source_turn.turn_id
    if subject_entity_binding is not None:
        metadata["subject_entity_surface"] = (
            subject_entity_binding.canonical_surface
        )
    # Mention bindings are durable metadata here; create_relationships later
    # projects them into generic role-less REFERS_TO edges.
    metadata["mention_entity_refs"] = [
        binding.entity_ref for binding in mention_entity_bindings
    ]
    metadata["mention_entity_surfaces"] = [
        binding.canonical_surface for binding in mention_entity_bindings
    ]
    return metadata

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
