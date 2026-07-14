from typing import Protocol, Sequence

from .models import (
    BackendCandidate,
    ColdDraftSegment,
    IngestionResult,
    MemoryContext,
    RecallPolicy,
    RelevanceScoreResult,
)


class MemoryIngestor(Protocol):
    def ingest(self, segment: ColdDraftSegment) -> IngestionResult: ...


class MemoryRetriever(Protocol):
    def recall(self, query: str, policy: RecallPolicy) -> MemoryContext: ...


class RelevanceScorer(Protocol):
    def score(
        self,
        query: str,
        candidates: Sequence[BackendCandidate],
    ) -> RelevanceScoreResult: ...
