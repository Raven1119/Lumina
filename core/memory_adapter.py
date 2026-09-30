"""Core-owned conversion from Hot/Cold Draft provenance to memory input."""
from __future__ import annotations

import hashlib
from datetime import datetime

from Conversation_Memory.engine.clock import TZ
from Conversation_Memory.engine.types import Turn
from core.contracts import DraftTurn


def draft_turns_to_memory(turns: list[DraftTurn] | tuple[DraftTurn, ...],
                          now: datetime, *, skip_untimed: bool = False) -> list[Turn]:
    result = []
    for index, turn in enumerate(turns):
        if turn.created_at is None and skip_untimed:
            continue
        at = (turn.created_at or now).astimezone(TZ)
        # Legacy Hot turns lack provenance. Their time is unknown, so use the
        # injected clock and a stable content/index key only within this read.
        tid = turn.turn_id or hashlib.sha256(
            f'{index}:{turn.role}:{turn.text}'.encode('utf-8')).hexdigest()
        result.append(Turn(tid, 'chat', turn.role, at, turn.text))
    return result
