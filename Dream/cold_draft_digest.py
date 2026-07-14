"""Convert and digest one production Cold Draft segment."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from .interfaces import ColdDraftOwner, MemoryIngestorProvider
from .models import SegmentDigestResult


_CONVERSATION_MEMORY_ROOT = Path(__file__).resolve().parents[1] / "Conversation_Memory"
if str(_CONVERSATION_MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONVERSATION_MEMORY_ROOT))

from adapter.models import ColdDraftSegment, ColdDraftTurn  # noqa: E402


_PENDING = "pending_digest"
_CONSUMED = "consumed"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SAFE_INGESTION_ERRORS = {
    "invalid_segment_id",
    "segment_not_pending",
    "unsupported_schema_version",
    "invalid_turns",
    "invalid_role",
    "invalid_content",
    "timestamp_timezone_required",
    "state_corrupt",
    "memory_write_failed",
}


class ColdDraftConversionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ColdDraftSegmentConverter:
    """Map the real JSONL owner record to the existing memory DTO."""

    def convert(
        self,
        record: Mapping[str, Any],
        ingestion_version: str,
    ) -> ColdDraftSegment:
        if not isinstance(ingestion_version, str) or not ingestion_version.strip():
            raise ColdDraftConversionError("invalid_ingestion_version")
        if not isinstance(record, Mapping):
            raise ColdDraftConversionError("invalid_segment_record")

        segment_id = record.get("segment_id")
        if not isinstance(segment_id, str) or not _SAFE_IDENTIFIER.fullmatch(segment_id):
            raise ColdDraftConversionError("invalid_segment_id")
        if record.get("state") != _PENDING:
            raise ColdDraftConversionError("segment_not_pending")

        created_at = self._aware_datetime(record.get("created_at"), "invalid_created_at")
        source_timezone = self._source_timezone(record, created_at)
        conversation_id = record.get("conversation_id")
        if conversation_id is None:
            conversation_id = f"cold-draft:{segment_id}"
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ColdDraftConversionError("invalid_conversation_id")

        raw_turns = record.get("turns")
        if not isinstance(raw_turns, list) or not raw_turns:
            raise ColdDraftConversionError("invalid_turns")

        turns: list[ColdDraftTurn] = []
        turn_ids: set[str] = set()
        for index, raw_turn in enumerate(raw_turns):
            if not isinstance(raw_turn, Mapping):
                raise ColdDraftConversionError("invalid_turns")
            role = raw_turn.get("role")
            if role not in {"user", "assistant"}:
                raise ColdDraftConversionError("invalid_role")
            content = (
                raw_turn.get("content")
                if "content" in raw_turn
                else raw_turn.get("text")
            )
            if not isinstance(content, str) or not content.strip():
                raise ColdDraftConversionError("invalid_content")
            turn_id = raw_turn.get("turn_id")
            if turn_id is None:
                turn_id = f"{segment_id}:turn:{index:04d}"
            if (
                not isinstance(turn_id, str)
                or not turn_id.strip()
                or turn_id in turn_ids
            ):
                raise ColdDraftConversionError("invalid_turn_id")
            turn_ids.add(turn_id)
            timestamp_value = raw_turn.get("timestamp", record.get("created_at"))
            timestamp = self._aware_datetime(
                timestamp_value,
                "invalid_turn_timestamp",
            )
            turns.append(ColdDraftTurn(turn_id, role, content, timestamp))

        return ColdDraftSegment(
            segment_id=segment_id,
            conversation_id=conversation_id,
            state=_PENDING,
            turns=tuple(turns),
            created_at=created_at,
            source_timezone=source_timezone,
            schema_version="1",
        )

    @staticmethod
    def _aware_datetime(value: Any, invalid_code: str) -> datetime:
        if not isinstance(value, str):
            raise ColdDraftConversionError(invalid_code)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ColdDraftConversionError(invalid_code) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ColdDraftConversionError("timestamp_timezone_required")
        return parsed

    @staticmethod
    def _source_timezone(record: Mapping[str, Any], created_at: datetime) -> str:
        explicit = record.get("source_timezone", record.get("timezone"))
        if explicit is not None:
            if not isinstance(explicit, str) or not explicit.strip():
                raise ColdDraftConversionError("invalid_source_timezone")
            return explicit
        offset = created_at.utcoffset()
        if offset == timedelta(0):
            return "UTC"
        if offset is None:
            raise ColdDraftConversionError("timestamp_timezone_required")
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        total_minutes = abs(total_minutes)
        hours, minutes = divmod(total_minutes, 60)
        return f"{sign}{hours:02d}:{minutes:02d}"


class ColdDraftDigestionTask:
    def __init__(
        self,
        owner: ColdDraftOwner,
        ingestors: MemoryIngestorProvider,
        converter: ColdDraftSegmentConverter | None = None,
    ) -> None:
        self._owner = owner
        self._ingestors = ingestors
        self._converter = converter or ColdDraftSegmentConverter()

    def digest(
        self,
        record: Mapping[str, Any],
        ingestion_version: str,
    ) -> SegmentDigestResult:
        segment_id = self._report_segment_id(record)
        state = record.get("state") if isinstance(record, Mapping) else None
        if state == _CONSUMED:
            return SegmentDigestResult(segment_id, "skipped", False, True)
        if state != _PENDING:
            return SegmentDigestResult(
                segment_id,
                "skipped",
                False,
                False,
                "segment_not_pending",
            )

        try:
            segment = self._converter.convert(record, ingestion_version)
        except ColdDraftConversionError as exc:
            return SegmentDigestResult(
                segment_id,
                "failed",
                False,
                False,
                exc.code,
            )

        try:
            ingestor = self._ingestors.get(ingestion_version)
            ingestion = ingestor.ingest(segment)
        except Exception:
            return SegmentDigestResult(
                segment_id,
                "failed",
                False,
                False,
                "memory_unavailable",
            )

        already_ingested = bool(getattr(ingestion, "already_ingested", False))
        if getattr(ingestion, "status", None) != "completed":
            return SegmentDigestResult(
                segment_id,
                "failed",
                already_ingested,
                False,
                self._safe_ingestion_error(ingestion),
            )
        if (
            getattr(ingestion, "segment_id", None) != segment.segment_id
            or getattr(ingestion, "ingestion_version", None) != ingestion_version
        ):
            return SegmentDigestResult(
                segment_id,
                "failed",
                already_ingested,
                False,
                "ingestion_result_mismatch",
            )
        memory_ids = getattr(ingestion, "memory_ids", ())
        if (
            not isinstance(memory_ids, tuple)
            or len(memory_ids) != len(segment.turns)
            or not all(isinstance(item, str) and item for item in memory_ids)
        ):
            return SegmentDigestResult(
                segment_id,
                "failed",
                already_ingested,
                False,
                "memory_completion_unconfirmed",
            )

        try:
            transitioned = self._owner.mark_consumed(segment.segment_id)
        except Exception:
            transitioned = False
        if not transitioned:
            return SegmentDigestResult(
                segment_id,
                "failed",
                already_ingested,
                False,
                "cold_draft_consume_failed",
            )
        return SegmentDigestResult(
            segment_id,
            "consumed",
            already_ingested,
            True,
        )

    @staticmethod
    def _report_segment_id(record: Any) -> str:
        if isinstance(record, Mapping):
            value = record.get("segment_id")
            if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
                return value
        return "invalid-segment"

    @staticmethod
    def _safe_ingestion_error(ingestion: Any) -> str:
        code = getattr(ingestion, "safe_error_code", None)
        if code in _SAFE_INGESTION_ERRORS:
            return code
        return "memory_ingestion_failed"
