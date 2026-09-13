"""Segment-oriented Cold Draft ownership over turn-oriented JSONL storage."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.contracts import DraftTurn


_ALLOWED_ROLES = {"user", "assistant"}
_PENDING = "pending_digest"
_CONSUMED = "consumed"
_DEFAULT_SOURCE = "hot_draft_precompression"
_RECORD_TYPE = "cold_turn"
_CURSOR_RECORD_TYPE = "dream_selection_cursor"
_TURN_KEYS = {
    "role", "text", "turn_id", "created_at", "source_timezone", "timezone_source"
}


@dataclass(frozen=True)
class PendingCount:
    count: int
    truncated: bool


@dataclass(frozen=True)
class _PhysicalLine:
    raw: bytes
    record: dict[str, Any] | None


@dataclass(frozen=True)
class _Segment:
    aggregate: dict[str, Any]
    line_positions: tuple[int, ...]


@dataclass
class _PendingCountGroup:
    segment_id: str
    turn_count: int
    next_index: int
    schema_version: int
    source: str
    segment_created_at: str
    state: str
    consumed_at: str | None
    has_native_provenance: bool


class ColdDraftStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append_segment(
        self,
        turns: list[dict[str, Any]],
        source: str = _DEFAULT_SOURCE,
        *,
        segment_id: str | None = None,
    ) -> dict[str, Any]:
        safe_turns = self._safe_turns(turns)
        safe_source = self._safe_source(source)
        safe_segment_id = self._safe_segment_id(segment_id)
        old_bytes = self._read_bytes()
        lines = self._physical_lines(old_bytes)
        segments, seen_ids = self._reconstruct_segments(lines)
        if safe_segment_id in seen_ids:
            existing = segments.get(safe_segment_id)
            if existing is not None and (
                existing.aggregate["turns"] == safe_turns
                and existing.aggregate["source"] == safe_source
            ):
                return deepcopy(existing.aggregate)
            raise ValueError("cold draft segment conflict")

        schema_version = 2 if any("turn_id" in turn for turn in safe_turns) else 1
        created_at = self._utc_now()
        aggregate = {
            "schema_version": schema_version,
            "segment_id": safe_segment_id,
            "turns": safe_turns,
            "created_at": created_at,
            "source": safe_source,
            "state": _PENDING,
        }
        physical_records = [
            {
                "record_type": _RECORD_TYPE,
                "schema_version": schema_version,
                "segment_id": safe_segment_id,
                "segment_turn_index": index,
                "segment_turn_count": len(safe_turns),
                "segment_created_at": created_at,
                "source": safe_source,
                "state": _PENDING,
                **turn,
            }
            for index, turn in enumerate(safe_turns)
        ]
        appended = b"".join(self._encode_record(record) for record in physical_records)
        separator = b"\n" if old_bytes and not old_bytes.endswith((b"\n", b"\r")) else b""
        self._replace_bytes(old_bytes + separator + appended)
        return deepcopy(aggregate)

    def list_pending(self, limit: int | None = None) -> list[dict[str, Any]]:
        try:
            segments, _ = self._reconstruct_segments(
                self._physical_lines(self._read_bytes())
            )
        except OSError:
            return []
        pending = [
            segment.aggregate
            for segment in segments.values()
            if segment.aggregate["state"] == _PENDING
        ]
        if limit is not None:
            pending = pending[: self._safe_limit(limit)]
        return deepcopy(pending)

    def list_pending_page(self, limit: int) -> list[dict[str, Any]]:
        """Return one unique pending page after the durable selection cursor.

        Like list_pending, this reconstructs the whole Cold file. Selection then
        visits at most one cycle of the valid segments, returning at most limit
        items. Consumed segments remain cursor anchors; the cursor never grants
        consumption authority. Read or cursor errors propagate to the caller.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("cold_draft_page_limit_invalid")
        lines = self._physical_lines(self._read_bytes())
        segments, _ = self._reconstruct_segments(lines)
        after, _ = self._pending_cursor(lines, segments)
        ordered = tuple(segments)
        start = ordered.index(after) + 1 if after is not None else 0
        page: list[dict[str, Any]] = []
        for offset in range(len(ordered)):
            aggregate = segments[ordered[(start + offset) % len(ordered)]].aggregate
            if aggregate["state"] == _PENDING:
                page.append(aggregate)
                if len(page) == limit:
                    break
        return deepcopy(page)

    def advance_pending_cursor(self, segment_id: str) -> bool:
        """Atomically record mechanical progress, preserving every source line."""
        if not isinstance(segment_id, str) or not segment_id:
            return False
        try:
            lines = self._physical_lines(self._read_bytes())
            segments, _ = self._reconstruct_segments(lines)
            previous, cursor_position = self._pending_cursor(lines, segments)
            if segment_id not in segments:
                return False
            if previous == segment_id and cursor_position == len(lines) - 1:
                return True
            preserved = b"".join(
                line.raw for position, line in enumerate(lines)
                if position != cursor_position
            )
            separator = b"\n" if preserved and not preserved.endswith((b"\n", b"\r")) else b""
            cursor = self._encode_record({
                "record_type": _CURSOR_RECORD_TYPE,
                "schema_version": 1,
                "after_segment_id": segment_id,
            })
            self._replace_bytes(preserved + separator + cursor)
        except (OSError, ValueError):
            return False
        return True

    @staticmethod
    def _pending_cursor(
        lines: list[_PhysicalLine], segments: OrderedDict[str, _Segment],
    ) -> tuple[str | None, int | None]:
        cursors = [
            (position, line.record) for position, line in enumerate(lines)
            if line.record is not None
            and line.record.get("record_type") == _CURSOR_RECORD_TYPE
        ]
        if not cursors:
            return None, None
        if len(cursors) != 1:
            raise ValueError("cold_draft_progress_invalid")
        position, cursor = cursors[0]
        if (
            set(cursor) != {"record_type", "schema_version", "after_segment_id"}
            or type(cursor["schema_version"]) is not int or cursor["schema_version"] != 1
            or not isinstance(cursor["after_segment_id"], str)
            or cursor["after_segment_id"] not in segments
        ):
            raise ValueError("cold_draft_progress_invalid")
        return cursor["after_segment_id"], position

    def list_all_turns(self) -> list[DraftTurn]:
        """Return turns from every valid segment in stable file/index order."""
        try:
            segments, _ = self._reconstruct_segments(
                self._physical_lines(self._read_bytes())
            )
        except OSError:
            return []
        return [
            DraftTurn.model_validate(turn)
            for segment in segments.values()
            for turn in segment.aggregate["turns"]
        ]

    def count_pending_bounded(self, limit: int) -> PendingCount:
        """Stream complete contiguous segments without retaining Draft bodies."""
        safe_limit = self._safe_limit(limit)
        if not self._path.exists():
            return PendingCount(0, False)
        count = 0
        current: _PendingCountGroup | None = None
        seen_segment_ids: set[str] = set()
        counted_pending_ids: set[str] = set()
        try:
            with self._path.open("rb") as file:
                for raw_line in file:
                    record, invalid_candidate_id = (
                        self._decode_physical_record(raw_line)
                    )
                    if record is None:
                        if current is not None:
                            seen_segment_ids.add(current.segment_id)
                        if invalid_candidate_id is not None:
                            seen_segment_ids.add(invalid_candidate_id)
                            if invalid_candidate_id in counted_pending_ids:
                                counted_pending_ids.remove(invalid_candidate_id)
                                count -= 1
                        current = None
                        continue
                    segment_id = record["segment_id"]
                    if current is None:
                        if segment_id in seen_segment_ids:
                            if segment_id in counted_pending_ids:
                                counted_pending_ids.remove(segment_id)
                                count -= 1
                            continue
                        current = self._start_count_group(record)
                        if current is None:
                            seen_segment_ids.add(segment_id)
                            continue
                    elif segment_id != current.segment_id:
                        seen_segment_ids.add(current.segment_id)
                        current = None
                        if segment_id in seen_segment_ids:
                            continue
                        current = self._start_count_group(record)
                        if current is None:
                            seen_segment_ids.add(segment_id)
                            continue
                    elif not self._advance_count_group(current, record):
                        seen_segment_ids.add(segment_id)
                        current = None
                        continue

                    if current.next_index != current.turn_count:
                        continue
                    seen_segment_ids.add(current.segment_id)
                    if (
                        current.schema_version
                        != (2 if current.has_native_provenance else 1)
                    ):
                        current = None
                        continue
                    if current.state == _PENDING:
                        count += 1
                        counted_pending_ids.add(current.segment_id)
                        if count >= safe_limit:
                            return PendingCount(count, True)
                    current = None
        except OSError:
            return PendingCount(0, True)
        return PendingCount(count, False)

    def mark_consumed(self, segment_id: str) -> bool:
        if not isinstance(segment_id, str) or not segment_id:
            return False
        try:
            old_bytes = self._read_bytes()
        except OSError:
            return False
        lines = self._physical_lines(old_bytes)
        segments, _ = self._reconstruct_segments(lines)
        segment = segments.get(segment_id)
        if segment is None:
            return False
        if segment.aggregate["state"] == _CONSUMED:
            return True

        consumed_at = self._utc_now()
        replacements: dict[int, bytes] = {}
        for position in segment.line_positions:
            record = lines[position].record
            if record is None:
                return False
            updated = dict(record)
            updated["state"] = _CONSUMED
            updated["consumed_at"] = consumed_at
            replacements[position] = self._encode_record(
                updated,
                newline=self._line_ending(lines[position].raw),
            )
        new_bytes = b"".join(
            replacements.get(position, line.raw)
            for position, line in enumerate(lines)
        )
        try:
            self._replace_bytes(new_bytes)
        except OSError:
            return False
        return True

    def _read_bytes(self) -> bytes:
        if not self._path.exists():
            return b""
        return self._path.read_bytes()

    @staticmethod
    def _physical_lines(payload: bytes) -> list[_PhysicalLine]:
        physical: list[_PhysicalLine] = []
        for raw_line in payload.splitlines(keepends=True):
            try:
                decoded: Any = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded = None
            physical.append(
                _PhysicalLine(raw_line, decoded if isinstance(decoded, dict) else None)
            )
        return physical

    @classmethod
    def _decode_physical_record(
        cls,
        raw_line: bytes,
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            decoded: Any = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, None
        if not isinstance(decoded, dict) or decoded.get("record_type") != _RECORD_TYPE:
            return None, None
        segment_id = decoded.get("segment_id")
        candidate_id = (
            segment_id
            if isinstance(segment_id, str) and segment_id.strip()
            else None
        )
        if not cls._valid_line_metadata(decoded):
            return None, candidate_id
        raw_turn = {key: decoded[key] for key in _TURN_KEYS if key in decoded}
        try:
            turn = DraftTurn.model_validate(raw_turn)
        except ValidationError:
            return None, candidate_id
        if turn.role not in _ALLOWED_ROLES or not turn.text.strip():
            return None, candidate_id
        return decoded, None

    @classmethod
    def _start_count_group(
        cls,
        record: dict[str, Any],
    ) -> _PendingCountGroup | None:
        if record["segment_turn_index"] != 0:
            return None
        consumed_at = record.get("consumed_at")
        return _PendingCountGroup(
            segment_id=record["segment_id"],
            turn_count=record["segment_turn_count"],
            next_index=1,
            schema_version=record["schema_version"],
            source=record["source"],
            segment_created_at=record["segment_created_at"],
            state=record["state"],
            consumed_at=consumed_at,
            has_native_provenance="turn_id" in record,
        )

    @staticmethod
    def _advance_count_group(
        group: _PendingCountGroup,
        record: dict[str, Any],
    ) -> bool:
        if (
            record["segment_turn_index"] != group.next_index
            or record["segment_turn_count"] != group.turn_count
            or record["schema_version"] != group.schema_version
            or record["source"] != group.source
            or record["segment_created_at"] != group.segment_created_at
            or record["state"] != group.state
            or record.get("consumed_at") != group.consumed_at
        ):
            return False
        group.next_index += 1
        group.has_native_provenance |= "turn_id" in record
        return True

    @classmethod
    def _reconstruct_segments(
        cls,
        lines: list[_PhysicalLine],
    ) -> tuple[OrderedDict[str, _Segment], set[str]]:
        grouped: OrderedDict[str, list[tuple[int, dict[str, Any]]]] = OrderedDict()
        for position, line in enumerate(lines):
            record = line.record
            if record is None or record.get("record_type") != _RECORD_TYPE:
                continue
            segment_id = record.get("segment_id")
            if not isinstance(segment_id, str) or not segment_id.strip():
                continue
            grouped.setdefault(segment_id, []).append((position, record))

        segments: OrderedDict[str, _Segment] = OrderedDict()
        for segment_id, entries in grouped.items():
            reconstructed = cls._reconstruct_segment(segment_id, entries)
            if reconstructed is not None:
                segments[segment_id] = reconstructed
        return segments, set(grouped)

    @classmethod
    def _reconstruct_segment(
        cls,
        segment_id: str,
        entries: list[tuple[int, dict[str, Any]]],
    ) -> _Segment | None:
        first = entries[0][1]
        count = first.get("segment_turn_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            return None
        if len(entries) != count:
            return None

        schema_version = first.get("schema_version")
        source = first.get("source")
        created_at = first.get("segment_created_at")
        state = first.get("state")
        consumed_at = first.get("consumed_at")
        if not cls._valid_line_metadata(first):
            return None

        by_index: dict[int, tuple[int, dict[str, Any]]] = {}
        for position, record in entries:
            index = record.get("segment_turn_index")
            if (
                not cls._valid_line_metadata(record)
                or not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index in by_index
                or record.get("segment_turn_count") != count
                or record.get("schema_version") != schema_version
                or record.get("source") != source
                or record.get("segment_created_at") != created_at
                or record.get("state") != state
                or record.get("consumed_at") != consumed_at
            ):
                return None
            by_index[index] = (position, record)
        if set(by_index) != set(range(count)):
            return None

        turns: list[dict[str, str]] = []
        positions: list[int] = []
        for index in range(count):
            position, record = by_index[index]
            raw_turn = {key: record[key] for key in _TURN_KEYS if key in record}
            try:
                turn = DraftTurn.model_validate(raw_turn)
            except ValidationError:
                return None
            if turn.role not in _ALLOWED_ROLES or not turn.text.strip():
                return None
            turns.append(turn.storage_turn())
            positions.append(position)
        expected_schema = 2 if any("turn_id" in turn for turn in turns) else 1
        if schema_version != expected_schema:
            return None

        aggregate = {
            "schema_version": schema_version,
            "segment_id": segment_id,
            "turns": turns,
            "created_at": created_at,
            "source": source,
            "state": state,
        }
        if state == _CONSUMED:
            aggregate["consumed_at"] = consumed_at
        return _Segment(aggregate, tuple(positions))

    def _replace_bytes(self, payload: bytes) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self._path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _encode_record(record: dict[str, Any], *, newline: bytes = b"\n") -> bytes:
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return encoded + newline

    @staticmethod
    def _line_ending(raw: bytes) -> bytes:
        if raw.endswith(b"\r\n"):
            return b"\r\n"
        if raw.endswith(b"\n"):
            return b"\n"
        if raw.endswith(b"\r"):
            return b"\r"
        return b""

    @classmethod
    def _valid_line_metadata(cls, record: dict[str, Any]) -> bool:
        schema_version = record.get("schema_version")
        segment_id = record.get("segment_id")
        index = record.get("segment_turn_index")
        count = record.get("segment_turn_count")
        source = record.get("source")
        created_at = record.get("segment_created_at")
        state = record.get("state")
        consumed_at = record.get("consumed_at")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version not in {1, 2}
            or not isinstance(segment_id, str)
            or not segment_id.strip()
            or not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 1
            or not isinstance(source, str)
            or not source.strip()
            or not cls._is_aware_timestamp(created_at)
            or state not in {_PENDING, _CONSUMED}
        ):
            return False
        if state == _PENDING:
            return "consumed_at" not in record
        return cls._is_aware_timestamp(consumed_at)

    @staticmethod
    def _is_aware_timestamp(value: Any) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return False
        return parsed.tzinfo is not None and parsed.utcoffset() is not None

    @staticmethod
    def _safe_turns(turns: Any) -> list[dict[str, str]]:
        if not isinstance(turns, list) or not turns:
            raise ValueError("invalid cold draft turns")
        safe: list[dict[str, str]] = []
        for raw in turns:
            if not isinstance(raw, dict):
                raise ValueError("invalid cold draft turns")
            try:
                turn = DraftTurn.model_validate(raw)
            except ValidationError as exc:
                raise ValueError("invalid cold draft turns") from exc
            if turn.role not in _ALLOWED_ROLES or not turn.text.strip():
                raise ValueError("invalid cold draft turns")
            safe.append(turn.storage_turn())
        return safe

    @staticmethod
    def _safe_source(source: Any) -> str:
        if not isinstance(source, str) or not source.strip():
            return _DEFAULT_SOURCE
        return source.strip()

    @staticmethod
    def _safe_segment_id(segment_id: Any) -> str:
        if segment_id is None:
            return uuid.uuid4().hex
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValueError("invalid cold draft segment id")
        return segment_id.strip()

    @staticmethod
    def _safe_limit(limit: Any) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return 1
        return limit

    @staticmethod
    def _utc_now() -> str:
        return (
            datetime.now(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
