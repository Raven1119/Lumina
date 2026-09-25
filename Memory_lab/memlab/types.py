"""Small immutable transfer values; timestamps are timezone-aware."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Turn:
    id: str
    session_id: str
    role: str
    time: datetime
    text: str


@dataclass(frozen=True)
class Cue:
    message: str
    recent: tuple[Turn, ...] = ()
    hot_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def contains(self, at: datetime) -> bool:
        return self.start <= at < self.end


@dataclass(frozen=True)
class RecalledMemory:
    id: str
    text: str
    score: float
    pi: float
    B: float
    parts: tuple[str, ...]
    sources: tuple[str, ...]
    time_label: str


@dataclass(frozen=True)
class RawHit:
    turn_id: str
    time: datetime
    role: str
    text: str
    score: float
    via: str


@dataclass(frozen=True)
class RecallResult:
    near: tuple[RecalledMemory, ...] = ()
    remote: tuple[RecalledMemory, ...] = ()
    core: tuple[RecalledMemory, ...] = ()
    raw: tuple[RawHit, ...] = ()
    diagnostics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DreamResult:
    dream_id: str
    status: str
    cache_key: str
    cache_hit: bool
    usage: dict
    n_ops: int
    n_rejected: int
    new_entities: tuple[str, ...] = ()
