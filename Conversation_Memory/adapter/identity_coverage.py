"""Deterministic self-identity coverage guard for Grounded Formation.

Shadow-validated in ``docs/experiments/identity_coverage_guard/`` (identity
coverage 34/48 -> 48/48, wrong identity 0, duplicates 0, all guard units
accepted by the unchanged strict validator with zero LLM calls).

When Formation omits an explicit user self-identification (我叫X / 我的名字是X /
你可以叫我X), the guard deterministically constructs ONE source-grounded
identity unit from the raw source evidence and runs it through the unchanged
strict ``_validate_candidate`` (injected by the caller). It never calls an
LLM, never triggers the semantic fallback, and adds nothing when an
equivalent identity unit was already accepted. It is not a general
deterministic fact parser: self-identification only.

This module deliberately does not import ``grounded_formation`` or
``user_self`` at module level (both would create import cycles); the
validator is injected and the user-self predicates are imported lazily.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .grounded_formation import GroundedMemoryUnit
    from .models import ColdDraftSegment

# Whitespace-tolerant derivative of user_self._ZH_SELF_NAME_PATTERN, narrowed
# to validated name/appellation forms only: 我叫X / 我的名字是X / 叫我X. The
# generic "我是 X" verb branch of the shipped pattern is deliberately NOT
# included — it matches role/state expressions ("我是学生"), which are outside
# the shadow-validated self-identification scope. Widening the shipped pattern
# in user_self would change frozen Entity/Recall binding behavior, so the
# derivative lives here.
_ZH_SELF_IDENTITY_PATTERN = re.compile(
    r"我(?P<verb>名叫|叫|的名字是)\s*(?P<name>[^，。,.!！?？；;\s]+)"
    r"|叫我\s*(?P<call_name>[^，。,.!！?？；;\s]+)"
)

# Same sentence-boundary rule as the validator's _ATOMIC_BOUNDARY; duplicated
# here to keep this module import-cycle free. The validator independently
# enforces atomicity, so this only chooses the citation span.
_SENTENCE_BOUNDARY = re.compile(r"(?<!\d)[.!?。！？;；](?!\d)")

# One trailing parenthetical, e.g. the observed Formation surface
# "user (Raven)" -> base "user".
_TRAILING_PARENTHETICAL = re.compile(r"\s*\([^()]*\)\s*$")


def self_identity_coverage_unit(
    segment: ColdDraftSegment,
    accepted_units: tuple[GroundedMemoryUnit, ...],
    *,
    validate: Callable[[Any, ColdDraftSegment], GroundedMemoryUnit | None],
) -> GroundedMemoryUnit | None:
    """Return ONE strict-validated identity unit omitted by Formation, or None.

    ``validate`` must be the unchanged strict validator
    (``grounded_formation._validate_candidate`` with the default
    ``semantic_equivalence=False``); a rejected candidate simply means no
    guard unit (fail-soft, Formation output unchanged).
    """
    seen: set[tuple[str, str]] = set()
    for turn in segment.turns:
        if turn.role != "user":
            continue
        for match in _ZH_SELF_IDENTITY_PATTERN.finditer(turn.content):
            name = match.group("name") or match.group("call_name")
            if not name or (turn.turn_id, name) in seen:
                continue
            seen.add((turn.turn_id, name))
            if any(
                _is_equivalent_identity_unit(unit, name)
                for unit in accepted_units
            ):
                continue
            span = _enclosing_sentence(turn.content, match.start(), match.end())
            if turn.content.count(span) != 1:
                continue
            verb = match.group("verb")
            relation = verb.lstrip("的") if verb is not None else "叫"
            unit = validate(
                {
                    "text": span,
                    "subject": "我",
                    "relation": relation,
                    "value": name,
                    "source_refs": [
                        {"turn_id": turn.turn_id, "supporting_span": span}
                    ],
                },
                segment,
            )
            if unit is not None:
                return unit
    return None


def _is_equivalent_identity_unit(unit: GroundedMemoryUnit, name: str) -> bool:
    """Equivalence predicate: an accepted unit already expresses the
    self-identity fact iff its value is exactly the parsed self-name span and
    its subject is a first-person surface, a Formation speaker-normalized
    user surface ("用户" / "user" / "the user", including one trailing
    parenthetical such as the observed "user (Raven)"), or the name itself.
    """
    if unit.value != name:
        return False
    from .user_self import (  # lazy: user_self imports grounded_formation
        _is_first_person_subject,
        _is_formation_user_surface,
    )
    subject = unit.subject.strip()
    if _is_first_person_subject(subject) or subject == name:
        return True
    if _is_formation_user_surface(subject):
        return True
    return _is_formation_user_surface(
        _TRAILING_PARENTHETICAL.sub("", subject)
    )


def _enclosing_sentence(content: str, start: int, end: int) -> str:
    sent_start = 0
    for match in _SENTENCE_BOUNDARY.finditer(content):
        if match.end() <= start:
            sent_start = match.end()
        elif match.start() >= end:
            return content[sent_start:match.end()].strip()
    return content[sent_start:].strip()
