"""Deterministic Chinese temporal intervals; never infer from system time."""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta

from .clock import TZ, logical_day, logical_start
from .types import Interval

_CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
       "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return (_CN.get(left, 1) if left else 1) * 10 + (_CN.get(right, 0) if right else 0)
    return _CN[value]


def _at(day, hour=5):
    return datetime(day.year, day.month, day.day, hour, tzinfo=TZ)


def _day_span(day):
    return Interval(_at(day), _at(day + timedelta(days=1)))


def _modify(day, modifier: str | None):
    if modifier in ("凌晨",):
        return Interval(_at(day, 0), _at(day, 5))
    if modifier in ("早上", "早晨", "今早"):
        return Interval(_at(day, 5), _at(day, 8))
    if modifier == "上午":
        return Interval(_at(day, 8), _at(day, 12))
    if modifier == "中午":
        return Interval(_at(day, 11), _at(day, 14))
    if modifier == "下午":
        return Interval(_at(day, 12), _at(day, 18))
    if modifier == "傍晚":
        return Interval(_at(day, 17), _at(day, 19))
    if modifier in ("晚上", "晚", "今晚", "昨晚"):
        return Interval(_at(day, 18), _at(day + timedelta(days=1), 5))
    if modifier == "放学那会儿":
        return Interval(_at(day, 16), _at(day, 19))
    return _day_span(day)


def _modifier(text: str):
    for term in ("放学那会儿", "凌晨", "早上", "早晨", "上午", "中午", "下午", "傍晚", "晚上", "晚"):
        if term in text:
            return term
    return None


def parse(text: str, now: datetime) -> list[Interval]:
    """Return a raw, left-closed/right-open interval or empty on unknown phrasing."""
    try:
        pieces=[part for part in re.split(r'、|以及|和|与',text) if part]
        if len(pieces)>1:
            spans=[span for piece in pieces for span in parse(piece,now)]
            if spans:return list(dict.fromkeys(spans))
        now = now.astimezone(TZ)
        today = logical_day(now)
        mod = _modifier(text)
        if "刚才" in text:
            return [Interval(now - timedelta(hours=3), now)]
        if "最近" in text:
            return [Interval(now - timedelta(days=14), now)]
        if "前几天" in text:
            return [Interval(_at(today - timedelta(days=5)), _at(today))]

        holiday = re.search(r"(国庆|五一)(前一天|后半段|前半段|那几天)?", text)
        if holiday:
            month, day, length = (10, 1, 7) if holiday[1] == "国庆" else (5, 1, 5)
            first = datetime(now.year, month, day, 5, tzinfo=TZ)
            if first > now + timedelta(days=7):
                first = first.replace(year=now.year - 1)
            date = first.date()
            suffix = holiday[2]
            if suffix == "前一天":
                return [_modify(date - timedelta(days=1), mod)]
            if suffix == "后半段":
                start = 3 if length == 7 else 2
                return [Interval(_at(date + timedelta(days=start)), _at(date + timedelta(days=length)))]
            if suffix == "前半段":
                end = 3 if length == 7 else 2
                return [Interval(_at(date), _at(date + timedelta(days=end)))]
            return [Interval(_at(date), _at(date + timedelta(days=length)))]

        month = re.search(r"([一二两三四五六七八九十]|1[012]|[1-9])月(初|上旬|中旬|下旬|底|月底|\d{1,2}[号日]|[一二三四五六七八九十]{1,3}[号日])", text)
        if month:
            m = _number(month[1])
            part = month[2]
            year = now.year
            if datetime(year, m, 1, tzinfo=TZ) > now:
                year -= 1
            days = calendar.monthrange(year, m)[1]
            if part in ("初", "上旬"):
                start, end = 1, 11
            elif part == "中旬":
                start, end = 11, 21
            elif part == "下旬":
                start, end = 21, days + 1
            elif part in ("底", "月底"):
                start, end = days - 6, days + 1
            else:
                start = _number(part[:-1]); end = start + 1
            first = datetime(year, m, start, tzinfo=TZ).date()
            last = (datetime(year, m, 1, tzinfo=TZ) + timedelta(days=end - 1)).date()
            if end == days + 1:
                last = (datetime(year + (m == 12), (m % 12) + 1, 1, tzinfo=TZ)).date()
            return [Interval(_at(first), _at(last))] if end > start else []

        weekday = re.search(r"(上|这)?(周|星期)([一二三四五六日天]|末)", text)
        if weekday:
            monday = today - timedelta(days=today.weekday())
            if weekday[1] == "上":
                monday -= timedelta(days=7)
            if weekday[3] == "末":
                return [Interval(_at(monday + timedelta(days=5)), _at(monday + timedelta(days=7)))]
            day = monday + timedelta(days=min(6,"一二三四五六日天".index(weekday[3])))
            return [_modify(day, mod)]

        relative = re.search(r"(前天|昨天|今日|今天|今早|今晚|昨晚|明天|后天|现在|这会儿|([一二两三四五六七八九十]|\d+)天前)", text)
        if relative:
            word = relative[1]
            offset = {"前天": -2, "昨天": -1, "昨晚": -1, "今日": 0, "今天": 0,
                      "今早": 0, "今晚": 0, "现在": 0, "这会儿": 0, "明天": 1, "后天": 2}.get(word)
            if offset is None:
                offset = -_number(relative[2])
            day = today + timedelta(days=offset)
            if mod == '凌晨':
                day = now.date() + timedelta(days=offset)
            return [_modify(day, mod or ("早上" if word == "今早" else "晚上" if word in ("今晚", "昨晚") else None))]
        return []
    except (ValueError, KeyError, OverflowError, TypeError):
        return []


def memory_intervals(intervals: list[Interval], now: datetime) -> list[Interval]:
    """D8: current logical day and future intervals do not seed memories."""
    boundary = logical_start(now)
    return [span for span in intervals if span.end <= boundary]


def clipped_intervals(intervals: list[Interval], now: datetime) -> list[Interval]:
    return [Interval(span.start, min(span.end, now)) for span in intervals if span.start < now]
