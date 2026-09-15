"""Segment-oriented Cold Draft ownership over turn-oriented JSONL storage."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from itertools import islice
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
    def __init__(
        self, path: str | Path, *, source_window_segments: int = 0,
        source_window_bytes: int = 1_048_576,
    ) -> None:
        self._path = Path(path)
        if (type(source_window_segments) is not int or source_window_segments < 0
                or type(source_window_bytes) is not int or source_window_bytes < 1):
            raise ValueError("cold_source_window_limits_invalid")
        self._source_window_segments = source_window_segments
        self._source_window_bytes = source_window_bytes
        self._source_window: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._source_window_size = 0
        self._source_postings: dict[str, dict[tuple[str, int], None]] = {}
        self._source_turn_ids: dict[tuple[str, str], int] = {}
        self._source_stamp = None
        self._source_window_error = "cold_source_window_disabled"
        if source_window_segments:
            try:
                before = self._source_file_stamp()
                segments, _ = self._reconstruct_segments(
                    self._physical_lines(self._read_bytes()))
                if before != self._source_file_stamp():
                    raise OSError("cold_source_window_changed")
                self._refresh_source_window(segments, expected_stamp=before)
            except OSError:
                self._source_window_error = "cold_source_window_unavailable"

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
                self._refresh_source_window(segments)
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
        segments[safe_segment_id] = _Segment(aggregate, ())
        self._refresh_source_window(segments)
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
                self._refresh_source_window(segments)
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
            self._refresh_source_window(segments)
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
            self._refresh_source_window(segments)
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
        self._refresh_source_window(segments)
        return True


    @staticmethod
    def _source_types():
        # DTO-only imports keep default Cold independent of Memory/model loading.
        try:
            from adapter.models import SourceExcerpt, SourceMemoryContext, SourceProvenance
        except ModuleNotFoundError as exc:
            if exc.name not in {"adapter", "adapter.models"}:
                raise
            from Conversation_Memory.adapter.models import (
                SourceExcerpt, SourceMemoryContext, SourceProvenance,
            )
        return SourceExcerpt, SourceMemoryContext, SourceProvenance

    @staticmethod
    def _source_features(text):
        from Conversation_Memory.adapter._anchor_fusion import _query_features
        return _query_features(text)

    def _source_file_stamp(self):
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _refresh_source_window(self, segments, *, expected_stamp=...) -> None:
        """Build from a snapshot already read for initialization/owner writes.

        The retained suffix is bounded by immutable UTF-8 JSON bytes and count.
        State/cursor writes cannot alter source age or its byte charge. Existing
        atomic owner operations still have full-file costs.
        """
        if not self._source_window_segments:
            return
        if expected_stamp is ...:
            try:
                expected_stamp = self._source_file_stamp()
            except OSError:
                self._invalidate_source_window("cold_source_window_unavailable")
                return
        window, sizes, size = OrderedDict(), {}, 0
        for segment_id, segment in segments.items():
            aggregate = {key: value for key, value in segment.aggregate.items()
                         if key not in {"state", "consumed_at"}}
            charge = len(self._encode_record(aggregate)) - 1
            window[segment_id], sizes[segment_id] = aggregate, charge
            size += charge
            while (len(window) > self._source_window_segments
                   or size > self._source_window_bytes):
                oldest, _ = window.popitem(last=False)
                size -= sizes.pop(oldest)
        # Only retain/index source bodies that survived both bounds.
        self._source_window = deepcopy(window)
        self._source_window_size = size
        self._source_postings, self._source_turn_ids = {}, {}
        for segment_id, aggregate in self._source_window.items():
            for index, turn in enumerate(aggregate["turns"]):
                turn_id = turn.get("turn_id", f"{segment_id}:turn:{index:04d}")
                key = (segment_id, turn_id)
                # A duplicate native ID is not an unambiguous source location.
                self._source_turn_ids[key] = (
                    -1 if key in self._source_turn_ids else index)
                for feature in self._source_features(turn["text"]):
                    self._source_postings.setdefault(feature, {})[(segment_id, index)] = None
        try:
            if expected_stamp != self._source_file_stamp():
                self._invalidate_source_window("cold_source_window_stale")
                return
            self._source_stamp = expected_stamp
            self._source_window_error = None
        except OSError:
            self._invalidate_source_window("cold_source_window_unavailable")

    def _invalidate_source_window(self, error):
        self._source_window.clear()
        self._source_postings.clear()
        self._source_turn_ids.clear()
        self._source_window_size = 0
        self._source_window_error = error

    def _source_status(self):
        if self._source_window_error is None:
            try:
                if self._source_stamp != self._source_file_stamp():
                    self._invalidate_source_window("cold_source_window_stale")
            except OSError:
                self._invalidate_source_window("cold_source_window_unavailable")
        return self._source_window_error

    @property
    def source_window_status(self) -> dict[str, Any]:
        error = self._source_status()
        return {"segments": len(self._source_window), "bytes": self._source_window_size,
                "max_segments": self._source_window_segments,
                "max_bytes": self._source_window_bytes, "safe_error_code": error}

    def _source_excerpt(self, segment_id, index, start, end, ingestion_version):
        SourceExcerpt, _, SourceProvenance = self._source_types()
        aggregate = self._source_window[segment_id]
        turn = aggregate["turns"][index]
        native = "turn_id" in turn
        timestamp = datetime.fromisoformat(
            turn["created_at"] if native else aggregate["created_at"])
        # Same deterministic v1 projection as ColdDraftSegmentConverter, without
        # invoking its pending-only conversion or changing a consumed record.
        offset = timestamp.utcoffset()
        minutes = int(offset.total_seconds() // 60)
        timezone = ("UTC" if minutes == 0 else
                    f"{'+' if minutes >= 0 else '-'}{abs(minutes)//60:02d}:{abs(minutes)%60:02d}")
        provenance = SourceProvenance(
            segment_id=segment_id, conversation_id=f"cold-draft:{segment_id}",
            turn_id=turn.get("turn_id", f"{segment_id}:turn:{index:04d}"),
            source_role=turn["role"], source_timestamp=timestamp.isoformat(),
            source_timezone=turn["source_timezone"] if native else timezone,
            ingestion_version=ingestion_version,
            timezone_source=turn["timezone_source"] if native else "legacy_segment_fallback",
        )
        evidence_id = sha256(self._encode_record({
            "segment": segment_id, "turn": provenance.turn_id,
            "start": start, "end": end,
        })).hexdigest()
        return SourceExcerpt(evidence_id, turn["text"][start:end], provenance,
                             index, start, end, len(turn["text"]))

    @staticmethod
    def _render_source_ranges(items):
        parts, segment = [], None
        compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        for item in items:
            p = item.provenance
            if p.segment_id != segment:
                segment = p.segment_id
                parts.append("[SOURCE " + compact({"segment": segment, "session": p.conversation_id}) + "]")
            role = "USER" if p.source_role == "user" else "LUMINA"
            parts.append("[" + role + " " + compact({
                "turn": item.turn_index, "turn_id": p.turn_id,
                "spoken_at": p.source_timestamp, "timezone": p.source_timezone,
                "timezone_source": p.timezone_source,
                "range": [item.source_start, item.source_end, item.turn_length],
            }) + "]\n" + item.text)
        return "\n".join(parts)

    def read_source_refs(
        self, refs, *, query: str = "", before: int = 0, after: int = 0,
        max_refs: int = 64, max_chars: int = 8000, max_bytes: int = 32768,
        max_items: int = 32,
    ):
        """Read exact citations and optional same-segment adjacent turns.

        No archive read, ingestion, model call, or consumption is permitted here.
        Whole exact ranges are packed or omitted. Overlap/adjacency merges only
        within a turn; unread character gaps remain separate labelled ranges.
        """
        _, SourceMemoryContext, _ = self._source_types()
        if (not isinstance(query, str)
                or any(type(value) is not int or value < 0 for value in
                       (before, after, max_refs, max_chars, max_bytes, max_items))
                or before > 32 or after > 32 or max_refs > 256 or max_items > 256):
            return SourceMemoryContext(query if isinstance(query, str) else "",
                                       safe_error_code="cold_source_limits_invalid")
        error = self._source_status()
        if error:
            return SourceMemoryContext(query, truncated=True, safe_error_code=error)
        try:
            references = tuple(islice(iter(refs), max_refs + 1))
        except TypeError:
            return SourceMemoryContext(query, safe_error_code="cold_source_refs_invalid")
        incomplete = len(references) > max_refs
        ranges, anchor_ranges, versions = {}, {}, {}
        for ref in references[:max_refs]:
            if not isinstance(ref, Mapping):
                incomplete = True
                continue
            segment_id, turn_id = ref.get("segment_id"), ref.get("turn_id")
            if not isinstance(segment_id, str) or not isinstance(turn_id, str):
                incomplete = True
                continue
            index = self._source_turn_ids.get((segment_id, turn_id), -1)
            start, end = ref.get("source_start"), ref.get("source_end")
            if (index < 0 or type(start) is not int or type(end) is not int):
                incomplete = True
                continue
            text = self._source_window[segment_id]["turns"][index]["text"]
            if not 0 <= start < end <= len(text):
                incomplete = True
                continue
            version = ref.get("ingestion_version", "cold-source-window-v1")
            if not isinstance(version, str) or not version.strip():
                incomplete = True
                continue
            item = self._source_excerpt(segment_id, index, start, end, version)
            p = item.provenance
            if (any(key in ref and ref[key] != getattr(p, key) for key in (
                    "conversation_id", "source_role", "source_timestamp",
                    "source_timezone", "timezone_source"))
                    or ("supporting_span" in ref and ref["supporting_span"] != item.text)):
                incomplete = True
                continue
            key = (segment_id, index)
            ranges.setdefault(key, []).append((start, end))
            anchor_ranges.setdefault(key, []).append((start, end))
            versions.setdefault(key, version)
            turns = self._source_window[segment_id]["turns"]
            for adjacent in range(max(0, index-before), min(len(turns), index+after+1)):
                if adjacent != index:
                    key = (segment_id, adjacent)
                    ranges.setdefault(key, []).append((0, len(turns[adjacent]["text"])))
                    versions.setdefault(key, version)
        order = {segment: index for index, segment in enumerate(self._source_window)}
        def merge(items):
            groups = {}
            for item in items:
                groups.setdefault((item.provenance.segment_id, item.turn_index), []).append(
                    (item.source_start, item.source_end))
            merged_items = []
            for key in sorted(groups, key=lambda key: (order[key[0]], key[1])):
                merged = []
                for start, end in sorted(groups[key]):
                    if merged and start <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                    else:
                        merged.append((start, end))
                merged_items.extend(self._source_excerpt(*key, start, end, versions[key])
                                    for start, end in merged)
            return merged_items

        selected = []
        # Support spans receive budget before optional context. A full neighboring
        # turn that cannot fit cannot displace an already packed exact citation.
        for group in (anchor_ranges, ranges):
            candidates = merge(self._source_excerpt(*key, start, end, versions[key])
                               for key, spans in group.items() for start, end in spans)
            for item in candidates:
                proposed = merge((*selected, item))
                rendered = self._render_source_ranges(proposed)
                if (len(proposed) > max_items or len(rendered) > max_chars
                        or len(rendered.encode("utf-8")) > max_bytes):
                    incomplete = True
                    continue
                selected = proposed
        error = self._source_status()
        if error:
            return SourceMemoryContext(query, truncated=True, safe_error_code=error)
        error = ("cold_source_partial" if selected else "cold_source_unavailable") if incomplete else None
        return SourceMemoryContext(query, tuple(selected), self._render_source_ranges(selected),
                                   incomplete, error)

    def search_recent_sources(
        self, query: str, *, limit: int = 8, max_refs: int = 64,
        snippet_chars: int = 400, before: int = 0, after: int = 0,
        max_chars: int = 8000, max_bytes: int = 32768, max_items: int = 32,
    ):
        """Independent lexical access to recent dialogue, including no-Fact turns."""
        _, SourceMemoryContext, _ = self._source_types()
        if (not isinstance(query, str) or not query.strip() or len(query) > 2000
                or any(type(value) is not int or value < 1 for value in
                       (limit, max_refs, snippet_chars)) or max_refs > 256
                or limit > max_refs or snippet_chars > 8000):
            return SourceMemoryContext(query if isinstance(query, str) else "",
                                       safe_error_code="cold_source_query_invalid")
        error = self._source_status()
        if error:
            return SourceMemoryContext(query, truncated=True, safe_error_code=error)
        features = self._source_features(query)
        postings = sorted((self._source_postings[feature] for feature in features
                           if feature in self._source_postings), key=len)
        candidates = {}
        for posting in postings:
            for key in posting:
                candidates.setdefault(key, None)
                if len(candidates) >= max_refs:
                    break
            if len(candidates) >= max_refs:
                break
        ranked = sorted(candidates, key=lambda key: -sum(
            key in self._source_postings.get(feature, {}) for feature in features))
        refs = []
        for segment_id, index in ranked[:limit]:
            turn = self._source_window[segment_id]["turns"][index]
            text = turn["text"]
            # Snippets retain original offsets even when a match is in a long tail.
            matches = [re.search(re.escape(feature.split(":", 1)[-1]), text, re.IGNORECASE)
                       for feature in features]
            position = min((match.start() for match in matches if match), default=0)
            start = max(0, position - snippet_chars//4)
            end = min(len(text), start + snippet_chars)
            refs.append({"segment_id": segment_id,
                         "turn_id": turn.get("turn_id", f"{segment_id}:turn:{index:04d}"),
                         "source_start": start, "source_end": end})
        return self.read_source_refs(refs, query=query, before=before, after=after,
            max_refs=max_refs, max_chars=max_chars, max_bytes=max_bytes, max_items=max_items)

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
