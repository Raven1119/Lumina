"""Explicit time and the +08:00 logical day."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

TZ = timezone(timedelta(hours=8))
WEEKDAYS = "一二三四五六日"


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass
class SimClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("clock requires timezone")
        self.value = value.astimezone(TZ)


def logical_day(at: datetime):
    return (at.astimezone(TZ) - timedelta(hours=5)).date()


def logical_start(at: datetime) -> datetime:
    day = logical_day(at)
    return datetime(day.year, day.month, day.day, 5, tzinfo=TZ)


def format_now(at: datetime) -> str:
    at = at.astimezone(TZ)
    return f"{at.year} 年 {at.month} 月 {at.day} 日 周{WEEKDAYS[at.weekday()]} {at:%H:%M}"


def period(at: datetime) -> str:
    hour = at.astimezone(TZ).hour
    if hour < 5: return "凌晨"
    if hour < 8: return "早上"
    if hour < 12: return "上午"
    if hour < 14: return "中午"
    if hour < 18: return "下午"
    return "晚上"


def rough_age(days: float) -> str:
    days = max(days, 0.0)
    if days < 1: return "今天"
    if days < 2: return "昨天"
    if days < 14: return f"{int(days)} 天前"
    if days < 56: return f"约 {round(days / 7)} 周前"
    return f"约 {max(2, round(days / 30))} 个月前"
