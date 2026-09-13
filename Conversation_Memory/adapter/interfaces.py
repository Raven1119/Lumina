from typing import Protocol

from .models import (
    ColdDraftSegment,
    IngestionResult,
    MemoryContext,
    PreparedRecall,
    EntityMentionContext,
    RecallPolicy,
)


class MemoryIngestor(Protocol):
    def ingest(self, segment: ColdDraftSegment) -> IngestionResult: ...


class MemoryRetriever(Protocol):
    def recall(self, query: str, policy: RecallPolicy) -> MemoryContext: ...

    def prepare_recall(self, query: str, policy: RecallPolicy) -> PreparedRecall: ...

    def recall_mentions(
        self, query: str, *, limit: int = 20,
    ) -> EntityMentionContext: ...
