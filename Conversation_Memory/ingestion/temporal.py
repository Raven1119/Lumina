"""Deterministic MAGMA-style temporal parsing for Lumina memory events.

The public layers mirror upstream MAGMA's MIT-licensed ``TemporalParser``
architecture, but the implementation is Lumina-owned and uses aware Draft Turn
V2 timestamps, IANA timezones, calendar intervals, and span-aware matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from adapter.models import (
    ColdDraftTurn,
    NormalizedTemporalReference,
)


Language = Literal["en", "zh", "und"]


@dataclass(frozen=True)
class _RelativeRule:
    phrase: str
    unit: Literal["day", "week", "month", "year"]
    offset: int
    language: Language


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    mention: NormalizedTemporalReference
    layer: int

    @property
    def length(self) -> int:
        return self.end - self.start


_RELATIVE_RULES = (
    _RelativeRule("day before yesterday", "day", -2, "en"),
    _RelativeRule("day after tomorrow", "day", 2, "en"),
    _RelativeRule("yesterday", "day", -1, "en"),
    _RelativeRule("tomorrow", "day", 1, "en"),
    _RelativeRule("today", "day", 0, "en"),
    _RelativeRule("this week", "week", 0, "en"),
    _RelativeRule("last week", "week", -1, "en"),
    _RelativeRule("week before", "week", -1, "en"),
    _RelativeRule("week ago", "week", -1, "en"),
    _RelativeRule("next week", "week", 1, "en"),
    _RelativeRule("this month", "month", 0, "en"),
    _RelativeRule("last month", "month", -1, "en"),
    _RelativeRule("month ago", "month", -1, "en"),
    _RelativeRule("next month", "month", 1, "en"),
    _RelativeRule("this year", "year", 0, "en"),
    _RelativeRule("last year", "year", -1, "en"),
    _RelativeRule("next year", "year", 1, "en"),
    _RelativeRule("今天", "day", 0, "zh"),
    _RelativeRule("今日", "day", 0, "zh"),
    _RelativeRule("昨天", "day", -1, "zh"),
    _RelativeRule("昨日", "day", -1, "zh"),
    _RelativeRule("明天", "day", 1, "zh"),
    _RelativeRule("明日", "day", 1, "zh"),
    _RelativeRule("前天", "day", -2, "zh"),
    _RelativeRule("后天", "day", 2, "zh"),
    _RelativeRule("本周", "week", 0, "zh"),
    _RelativeRule("这周", "week", 0, "zh"),
    _RelativeRule("本星期", "week", 0, "zh"),
    _RelativeRule("这个星期", "week", 0, "zh"),
    _RelativeRule("上周", "week", -1, "zh"),
    _RelativeRule("上星期", "week", -1, "zh"),
    _RelativeRule("上个星期", "week", -1, "zh"),
    _RelativeRule("下周", "week", 1, "zh"),
    _RelativeRule("下星期", "week", 1, "zh"),
    _RelativeRule("下个星期", "week", 1, "zh"),
    _RelativeRule("本月", "month", 0, "zh"),
    _RelativeRule("这个月", "month", 0, "zh"),
    _RelativeRule("上月", "month", -1, "zh"),
    _RelativeRule("上个月", "month", -1, "zh"),
    _RelativeRule("下月", "month", 1, "zh"),
    _RelativeRule("下个月", "month", 1, "zh"),
    _RelativeRule("今年", "year", 0, "zh"),
    _RelativeRule("本年", "year", 0, "zh"),
    _RelativeRule("去年", "year", -1, "zh"),
    _RelativeRule("上一年", "year", -1, "zh"),
    _RelativeRule("明年", "year", 1, "zh"),
    _RelativeRule("下一年", "year", 1, "zh"),
)

_EN_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_ZH_WEEKDAY_DIGITS = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}
_ZH_WEEK_PREFIXES = {
    "上周": -1,
    "上星期": -1,
    "上个星期": -1,
    "下周": 1,
    "下星期": 1,
    "下个星期": 1,
}
_ZH_DIRECTED_WEEKDAY = re.compile(
    "(" + "|".join(
        re.escape(value)
        for value in sorted(_ZH_WEEK_PREFIXES, key=len, reverse=True)
    ) + r")([一二三四五六日天])"
)
_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")

_MONTH_NAMES = {
    name: index
    for index, name in enumerate(
        (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november",
            "december",
        ),
        start=1,
    )
}
_MONTH_PATTERN = "|".join(_MONTH_NAMES)

class _TemporalParser:
    """Internal English/Chinese parser used only by memory ingestion."""

    def __init__(self) -> None:
        self.relative_patterns = tuple(sorted(
            _RELATIVE_RULES,
            key=lambda rule: (-len(rule.phrase), rule.phrase),
        ))

    def parse(
        self,
        text: str,
        base_timestamp: datetime,
        source_timezone: str,
    ) -> tuple[NormalizedTemporalReference, ...]:
        if not isinstance(text, str):
            raise ValueError("text_required")
        zone = _source_zone(source_timezone)
        local_base = _localized_reference(base_timestamp, zone)
        candidates = [
            *self._relative_candidates(text, base_timestamp, local_base, zone, source_timezone),
            *self._weekday_candidates(text, base_timestamp, local_base, zone, source_timezone),
            *self._absolute_candidates(text, base_timestamp, zone, source_timezone),
        ]
        return tuple(
            candidate.mention
            for candidate in _select_non_overlapping(candidates)
        )

    def _relative_candidates(
        self,
        text: str,
        base_timestamp: datetime,
        local_base: datetime,
        zone: tzinfo,
        source_timezone: str,
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for rule in self.relative_patterns:
            for start, end in _phrase_spans(text, rule.phrase, rule.language):
                interval_start, interval_end = _relative_interval(
                    local_base,
                    zone,
                    rule.unit,
                    rule.offset,
                )
                candidates.append(_Candidate(
                    start,
                    end,
                    _mention(
                        text[start:end],
                        interval_start,
                        interval_end,
                        base_timestamp,
                        source_timezone,
                        f"deterministic_relative_{rule.unit}",
                        rule.language,
                    ),
                    0,
                ))
        return candidates

    def _weekday_candidates(
        self,
        text: str,
        base_timestamp: datetime,
        local_base: datetime,
        zone: tzinfo,
        source_timezone: str,
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        current_monday = local_base.date() - timedelta(days=local_base.weekday())

        for match in _ZH_DIRECTED_WEEKDAY.finditer(text):
            direction = _ZH_WEEK_PREFIXES[match.group(1)]
            target_date = (
                current_monday
                + timedelta(weeks=direction, days=_ZH_WEEKDAY_DIGITS[match.group(2)])
            )
            start, end = _day_interval(target_date, zone)
            candidates.append(_Candidate(
                match.start(),
                match.end(),
                _mention(
                    match.group(0), start, end, base_timestamp, source_timezone,
                    "deterministic_directed_weekday", "zh",
                ),
                1,
            ))

        lowered = text.lower()
        for weekday, day_number in _EN_WEEKDAYS.items():
            escaped = re.escape(weekday)
            patterns = (
                (rf"\b(?:last|previous)\s+{escaped}\b", -1),
                (rf"\b{escaped}\s+(?:before|prior)\b", -1),
                (rf"\bnext\s+{escaped}\b", 1),
            )
            for pattern, direction in patterns:
                for match in re.finditer(pattern, lowered, re.IGNORECASE):
                    target_date = _nearest_directed_weekday(
                        local_base.date(), day_number, direction
                    )
                    start, end = _day_interval(target_date, zone)
                    candidates.append(_Candidate(
                        match.start(),
                        match.end(),
                        _mention(
                            text[match.start():match.end()],
                            start,
                            end,
                            base_timestamp,
                            source_timezone,
                            "deterministic_directed_weekday",
                            "en",
                        ),
                        1,
                    ))
        return candidates

    def _absolute_candidates(
        self,
        text: str,
        base_timestamp: datetime,
        zone: tzinfo,
        source_timezone: str,
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []

        for match in re.finditer(
            r"(?<!\d)(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})(?:日|号)",
            text,
        ):
            self._append_absolute_candidate(
                candidates, match, base_timestamp, zone, source_timezone,
                int(match.group(1)), int(match.group(2)), int(match.group(3)), "zh",
            )
        for match in re.finditer(
            r"(?<!\d)(\d{4})年\s*(\d{1,2})月(?!\s*\d{1,2}(?:日|号))",
            text,
        ):
            self._append_absolute_candidate(
                candidates, match, base_timestamp, zone, source_timezone,
                int(match.group(1)), int(match.group(2)), None, "zh",
            )
        for match in re.finditer(r"(?<!\d)(\d{4})年(?!\s*\d{1,2}月)", text):
            self._append_absolute_candidate(
                candidates, match, base_timestamp, zone, source_timezone,
                int(match.group(1)), None, None, "zh",
            )
        for match in re.finditer(
            r"(?<!\d)(\d{4})([-/])(\d{1,2})\2(\d{1,2})(?!\d)",
            text,
        ):
            self._append_absolute_candidate(
                candidates, match, base_timestamp, zone, source_timezone,
                int(match.group(1)), int(match.group(3)), int(match.group(4)), "und",
            )

        for match in re.finditer(
            rf"\b(\d{{1,2}})\s+({_MONTH_PATTERN})\s+(\d{{4}})\b",
            text,
            re.IGNORECASE,
        ):
            self._append_absolute_candidate(
                candidates, match, base_timestamp, zone, source_timezone,
                int(match.group(3)), _MONTH_NAMES[match.group(2).lower()],
                int(match.group(1)), "en",
            )
        for match in re.finditer(
            rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+(\d{{4}})\b",
            text,
            re.IGNORECASE,
        ):
            self._append_absolute_candidate(
                candidates, match, base_timestamp, zone, source_timezone,
                int(match.group(3)), _MONTH_NAMES[match.group(1).lower()],
                int(match.group(2)), "en",
            )
        for match in re.finditer(
            rf"\b({_MONTH_PATTERN})\s+(\d{{4}})\b",
            text,
            re.IGNORECASE,
        ):
            self._append_absolute_candidate(
                candidates, match, base_timestamp, zone, source_timezone,
                int(match.group(2)), _MONTH_NAMES[match.group(1).lower()], None, "en",
            )
        for match in re.finditer(r"\b(20\d{2})\b(?![-/]\d)", text):
            self._append_absolute_candidate(
                candidates, match, base_timestamp, zone, source_timezone,
                int(match.group(1)), None, None, "und",
            )
        return candidates

    @staticmethod
    def _append_absolute_candidate(
        candidates: list[_Candidate],
        match: re.Match[str],
        base_timestamp: datetime,
        zone: tzinfo,
        source_timezone: str,
        year: int,
        month: int | None,
        day: int | None,
        language: Language,
    ) -> None:
        try:
            if day is not None and month is not None:
                start, end = _day_interval(date(year, month, day), zone)
                method = "deterministic_absolute_date"
            elif month is not None:
                start, end = _month_interval(year, month, zone)
                method = "deterministic_absolute_month"
            else:
                start, end = _year_interval(year, zone)
                method = "deterministic_absolute_year"
        except ValueError:
            return
        candidates.append(_Candidate(
            match.start(),
            match.end(),
            _mention(
                match.group(0), start, end, base_timestamp, source_timezone,
                method, language,
            ),
            2,
        ))

def normalize_temporal_references(
    turn: ColdDraftTurn,
    timezone_name: str | None = None,
) -> tuple[NormalizedTemporalReference, ...]:
    return _DEFAULT_PARSER.parse(
        turn.content,
        turn.timestamp,
        timezone_name or turn.source_timezone,
    )


def _source_zone(name: str) -> tzinfo:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("source_timezone_invalid")
    candidate = name.strip()
    offset_match = _OFFSET.fullmatch(candidate)
    if offset_match:
        hours = int(offset_match.group(2))
        minutes = int(offset_match.group(3))
        if hours > 23 or minutes > 59:
            raise ValueError("source_timezone_invalid")
        delta = timedelta(hours=hours, minutes=minutes)
        if offset_match.group(1) == "-":
            delta = -delta
        return timezone(delta)
    try:
        return ZoneInfo(candidate)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("source_timezone_invalid") from exc


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp_timezone_required")


def _localized_reference(value: datetime, zone: tzinfo) -> datetime:
    _require_aware(value)
    return value.astimezone(zone)


def _local_midnight(value: date, zone: tzinfo) -> datetime:
    return datetime.combine(value, time.min, tzinfo=zone)


def _day_interval(value: date, zone: tzinfo) -> tuple[datetime, datetime]:
    return (
        _local_midnight(value, zone),
        _local_midnight(value + timedelta(days=1), zone),
    )


def _month_interval(year: int, month: int, zone: tzinfo) -> tuple[datetime, datetime]:
    start = _local_midnight(date(year, month, 1), zone)
    next_year, next_month = _shift_month(year, month, 1)
    end = _local_midnight(date(next_year, next_month, 1), zone)
    return start, end


def _year_interval(year: int, zone: tzinfo) -> tuple[datetime, datetime]:
    return (
        _local_midnight(date(year, 1, 1), zone),
        _local_midnight(date(year + 1, 1, 1), zone),
    )


def _relative_interval(
    local_base: datetime,
    zone: tzinfo,
    unit: str,
    offset: int,
) -> tuple[datetime, datetime]:
    if unit == "day":
        return _day_interval(local_base.date() + timedelta(days=offset), zone)
    if unit == "week":
        monday = local_base.date() - timedelta(days=local_base.weekday())
        start_date = monday + timedelta(weeks=offset)
        return _local_midnight(start_date, zone), _local_midnight(
            start_date + timedelta(weeks=1), zone
        )
    if unit == "month":
        year, month = _shift_month(local_base.year, local_base.month, offset)
        return _month_interval(year, month, zone)
    if unit == "year":
        return _year_interval(local_base.year + offset, zone)
    raise ValueError("temporal_unit_invalid")


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + offset
    shifted_year, shifted_zero_month = divmod(index, 12)
    return shifted_year, shifted_zero_month + 1


def _nearest_directed_weekday(
    base: date,
    weekday: int,
    direction: int,
) -> date:
    if direction < 0:
        distance = (base.weekday() - weekday) % 7 or 7
        return base - timedelta(days=distance)
    distance = (weekday - base.weekday()) % 7 or 7
    return base + timedelta(days=distance)


def _phrase_spans(
    text: str,
    phrase: str,
    language: Language,
) -> list[tuple[int, int]]:
    if language == "en":
        pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
    else:
        pattern = re.compile(re.escape(phrase))
    return [(match.start(), match.end()) for match in pattern.finditer(text)]


def _mention(
    original: str,
    start: datetime,
    end: datetime,
    reference: datetime,
    source_timezone: str,
    method: str,
    language: Language,
) -> NormalizedTemporalReference:
    return NormalizedTemporalReference(
        original_expression=original,
        normalized_start=_utc_iso(start),
        normalized_end=_utc_iso(end),
        reference_timestamp=_utc_iso(reference),
        reference_timezone=source_timezone,
        normalization_method=method,
        normalization_confidence=1.0,
        language=language,
    )


def _utc_iso(value: datetime) -> str:
    _require_aware(value)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _select_non_overlapping(candidates: list[_Candidate]) -> list[_Candidate]:
    selected: list[_Candidate] = []
    occupied: list[tuple[int, int]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-item.length, item.start, item.layer),
    ):
        if any(candidate.start < end and start < candidate.end for start, end in occupied):
            continue
        selected.append(candidate)
        occupied.append((candidate.start, candidate.end))
    return sorted(selected, key=lambda item: (item.start, item.end))


_DEFAULT_PARSER = _TemporalParser()
