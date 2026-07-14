"""Public, non-sensitive result models for one manual Dream run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DigestStatus = Literal["consumed", "skipped", "failed"]
_DIGEST_STATUSES = {"consumed", "skipped", "failed"}


@dataclass(frozen=True)
class DreamRunPolicy:
    max_segments: int = 10
    stop_on_error: bool = False
    ingestion_version: str = "dream-v1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_segments, int)
            or isinstance(self.max_segments, bool)
            or self.max_segments < 1
        ):
            raise ValueError("max_segments must be positive")
        if not isinstance(self.stop_on_error, bool):
            raise ValueError("stop_on_error must be boolean")
        if (
            not isinstance(self.ingestion_version, str)
            or not self.ingestion_version.strip()
        ):
            raise ValueError("ingestion_version must be non-empty")


@dataclass(frozen=True)
class SegmentDigestResult:
    segment_id: str
    status: DigestStatus
    already_ingested: bool
    consumed: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _DIGEST_STATUSES:
            raise ValueError("invalid digest status")


@dataclass(frozen=True)
class DreamRunReport:
    attempted: int
    ingested: int
    consumed: int
    skipped: int
    failed: int
    results: tuple[SegmentDigestResult, ...]

    @classmethod
    def from_results(
        cls,
        results: tuple[SegmentDigestResult, ...],
    ) -> "DreamRunReport":
        consumed = sum(item.status == "consumed" for item in results)
        return cls(
            attempted=len(results),
            ingested=sum(
                item.status == "consumed" and not item.already_ingested
                for item in results
            ),
            consumed=consumed,
            skipped=sum(item.status == "skipped" for item in results),
            failed=sum(item.status == "failed" for item in results),
            results=results,
        )
