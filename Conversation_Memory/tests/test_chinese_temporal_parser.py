from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from adapter.models import ColdDraftTurn
from ingestion import temporal as temporal_module
from ingestion.temporal import normalize_temporal_references


BASE_NY = datetime(2026, 7, 15, 4, 5, tzinfo=UTC)


def _mentions(
    text: str,
    base: datetime = BASE_NY,
    zone: str = "America/New_York",
):
    return normalize_temporal_references(ColdDraftTurn(
        "turn", "user", text, base, zone, "client",
    ))


def _one(text: str, base: datetime = BASE_NY, zone: str = "America/New_York"):
    mentions = _mentions(text, base, zone)
    assert len(mentions) == 1
    return mentions[0]


def test_parser_source_is_local_and_exposes_only_the_ingestion_entrypoint():
    source = Path(temporal_module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in (
        "openai", "requests", "httpx", "socket", "sentence_transformer",
        "transformers", "encoder.encode", "graph_db", "timeconstraints",
    ):
        assert forbidden not in source
    assert not hasattr(temporal_module, "TemporalParser")


@pytest.mark.parametrize(
    "expression,start,end",
    [
        ("昨天", "2026-07-14T04:00:00Z", "2026-07-15T04:00:00Z"),
        ("本周", "2026-07-13T04:00:00Z", "2026-07-20T04:00:00Z"),
        ("上个月", "2026-06-01T04:00:00Z", "2026-07-01T04:00:00Z"),
        ("明年", "2027-01-01T05:00:00Z", "2028-01-01T05:00:00Z"),
    ],
)
def test_chinese_relative_calendar_units(expression, start, end):
    mention = _one(expression)
    assert (mention.normalized_start, mention.normalized_end) == (start, end)
    assert mention.language == "zh"


@pytest.mark.parametrize(
    "english,chinese",
    [
        ("today", "今天"),
        ("yesterday", "昨日"),
        ("next week", "下星期"),
        ("last month", "上月"),
        ("last year", "去年"),
    ],
)
def test_english_and_chinese_share_calendar_intervals(english, chinese):
    en = _one(english)
    zh = _one(chinese)
    assert (en.normalized_start, en.normalized_end) == (
        zh.normalized_start,
        zh.normalized_end,
    )


@pytest.mark.parametrize(
    "expression,start",
    [
        ("上周一", "2026-07-06T04:00:00Z"),
        ("下星期天", "2026-07-26T04:00:00Z"),
        ("上个星期日", "2026-07-12T04:00:00Z"),
    ],
)
def test_directed_weekdays_use_previous_or_next_calendar_week(expression, start):
    assert _one(expression).normalized_start == start


def test_undirected_weekday_is_not_guessed():
    assert _mentions("周一") == ()


@pytest.mark.parametrize(
    "expression,start,end",
    [
        ("2026年7月15日", "2026-07-15T04:00:00Z", "2026-07-16T04:00:00Z"),
        ("2026年7月", "2026-07-01T04:00:00Z", "2026-08-01T04:00:00Z"),
        ("2026年", "2026-01-01T05:00:00Z", "2027-01-01T05:00:00Z"),
        ("2026/07/15", "2026-07-15T04:00:00Z", "2026-07-16T04:00:00Z"),
    ],
)
def test_absolute_calendar_dates(expression, start, end):
    mention = _one(expression)
    assert (mention.normalized_start, mention.normalized_end) == (start, end)


def test_longest_match_and_multiple_mentions_preserve_source_order():
    mentions = _mentions("我昨天完成实验，今天提交报告，下周一复查。")
    assert [item.original_expression for item in mentions] == [
        "昨天", "今天", "下周一",
    ]


def test_invalid_and_unsupported_dates_are_not_invented_or_rewritten():
    text = "错误日期2026年2月30日，另有7月15日和农历三月。"
    assert _mentions(text, zone="Asia/Shanghai") == ()
    assert text == "错误日期2026年2月30日，另有7月15日和农历三月。"


def test_cross_midnight_uses_the_source_local_date():
    mention = _one("今天", datetime(2026, 7, 15, 3, 55, tzinfo=UTC))
    assert mention.reference_timestamp == "2026-07-15T03:55:00Z"
    assert (mention.normalized_start, mention.normalized_end) == (
        "2026-07-14T04:00:00Z", "2026-07-15T04:00:00Z",
    )


def test_dst_and_non_dst_day_lengths_follow_the_named_timezone():
    ny = _one(
        "今天", datetime(2026, 3, 8, 16, tzinfo=UTC), "America/New_York"
    )
    shanghai = _one(
        "今天", datetime(2026, 3, 8, 4, tzinfo=UTC), "Asia/Shanghai"
    )
    ny_start = datetime.fromisoformat(ny.normalized_start.replace("Z", "+00:00"))
    ny_end = datetime.fromisoformat(ny.normalized_end.replace("Z", "+00:00"))
    sh_start = datetime.fromisoformat(
        shanghai.normalized_start.replace("Z", "+00:00")
    )
    sh_end = datetime.fromisoformat(
        shanghai.normalized_end.replace("Z", "+00:00")
    )
    assert (ny_end - ny_start).total_seconds() == 23 * 3600
    assert (sh_end - sh_start).total_seconds() == 24 * 3600


def test_real_month_and_year_boundaries_include_leap_years():
    february = _one(
        "上个月", datetime(2024, 3, 31, 12, tzinfo=UTC), "UTC"
    )
    next_year = _one(
        "明年", datetime(2026, 12, 31, 23, tzinfo=UTC), "UTC"
    )
    assert (february.normalized_start, february.normalized_end) == (
        "2024-02-01T00:00:00Z", "2024-03-01T00:00:00Z",
    )
    assert (next_year.normalized_start, next_year.normalized_end) == (
        "2027-01-01T00:00:00Z", "2028-01-01T00:00:00Z",
    )


def test_reference_must_be_aware_and_timezone_must_be_valid():
    with pytest.raises(ValueError, match="timestamp_timezone_required"):
        _mentions("昨天", datetime(2026, 7, 15), "Asia/Shanghai")
    with pytest.raises(ValueError, match="source_timezone_invalid"):
        _mentions("昨天", BASE_NY, "Mars/Olympus")
