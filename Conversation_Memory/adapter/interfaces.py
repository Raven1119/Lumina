from typing import Protocol

from .models import (
    ColdDraftSegment,
    IngestionResult,
    MemoryContext,
    RecallPolicy,
)


class MemoryIngestor(Protocol):
    def ingest(self, segment: ColdDraftSegment) -> IngestionResult: ...


class MemoryRetriever(Protocol):
    def recall(self, query: str, policy: RecallPolicy) -> MemoryContext: ...
