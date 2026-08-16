"""Current-user entity binding — retrieval metadata only.

Classifies whether a validated GroundedMemoryUnit's subject, or a recall
query's retrieval target, is the current user. Detection produces the
resolution-time contextual role ``CURRENT_USER``; the only persistent entity
reference this module can produce is the generic ``CURRENT_USER_ENTITY_REF``
(``"E_001"``). It is retrieval metadata and never participates in fact
authorization, validator rescue, or provenance/safety contracts. The marker
text is injected only into the BGE scoring projection at recall time; stored
factual text and user-visible evidence are never modified.

Rule classifiers are the shadow-validated Stage-1 forms
(docs/experiments/user_self_binding_shadow/RESULT.md): zh-centric with
minimal en coverage. The role/ref separation is validated by
docs/experiments/context_role_entityref/RESULT.md.
"""

from __future__ import annotations

import re

from .grounded_formation import GroundedMemoryUnit
from .models import ColdDraftSegment

CURRENT_USER_ROLE = "CURRENT_USER"
CURRENT_USER_ENTITY_REF = "E_001"

_ZH_SELF_NAME_PATTERN = re.compile(
    r"我(?:名叫|叫|的名字是|是)(?P<name>[^，。,.!！?？；;\s]+)"
    r"|叫我(?P<call_name>[^，。,.!！?？；;\s]+)"
)
_EN_SELF_NAME_PATTERN = re.compile(
    r"\b(?:my name is|i am|i'm|call me)\s+(?P<name>[^,.!?;，。！？；]+)",
    re.IGNORECASE,
)
_ZH_ADDRESSED_NAME_PATTERN = re.compile(
    r"你(?:叫|是|的名字是)(?P<name>[^，。,.!！?？；;\s]+)"
)
_EN_ADDRESSED_NAME_PATTERN = re.compile(
    r"\b(?:your name is|you are|you're)\s+(?P<name>[^,.!?;，。！？；]+)",
    re.IGNORECASE,
)
_ACCEPTANCE_PATTERN = re.compile(
    r"^(?:对|嗯|是的|没错|好的|行|可以|yes|yeah|yep|exactly|right|correct)"
    r"[。!！.!]*$",
    re.IGNORECASE,
)
_ZH_FIRST_PERSON_SUBJECTS = frozenset({"我"})
_EN_FIRST_PERSON_SUBJECTS = frozenset(
    {"i", "me", "my", "mine", "we", "us", "our"},
)

_FOCUS_PATTERNS = (
    "是谁",
    "在哪",
    "的学校",
    "喜欢",
    "多大",
    "住哪",
    "做什么",
    "来自哪",
    "是哪个",
    "是什么样",
)
_FRAMING_SPLITTERS = ("说过", "提到", "知道", "朋友")
_EN_FIRST_PERSON_TOKEN = re.compile(
    r"\b(?:i|me|my|mine|we|us|our)\b", re.IGNORECASE,
)

# Formation speaker-role normalization surfaces (observed MiniMax-M3 output
# conventions, not schema fields). Exact-match after casefold and a leading
# "the " strip only: "用户的妻子" / "user's wife" etc. do not qualify.
_FORMATION_USER_SUBJECT_SURFACE_ZH = "用户"
_FORMATION_USER_SUBJECT_SURFACE_EN = "user"


def _is_formation_user_surface(subject: str) -> bool:
    if subject == _FORMATION_USER_SUBJECT_SURFACE_ZH:
        return True
    normalized = subject.casefold()
    if normalized.startswith("the "):
        normalized = normalized[4:]
    return normalized == _FORMATION_USER_SUBJECT_SURFACE_EN


def classify_subject_entity_ref(
    unit: GroundedMemoryUnit,
    segment: ColdDraftSegment,
) -> str | None:
    """Return the current user's generic EntityRef iff the validated unit's
    subject is the current user.

    Consumes validator-accepted units only; the result is stored as the
    ``subject_entity_ref`` metadata field.
    """
    role = _classify_subject_role(unit, segment)
    return CURRENT_USER_ENTITY_REF if role == CURRENT_USER_ROLE else None


def _classify_subject_role(
    unit: GroundedMemoryUnit,
    segment: ColdDraftSegment,
) -> str | None:
    turns = {turn.turn_id: turn for turn in segment.turns}
    user_spans = [
        ref.supporting_span
        for ref in unit.source_refs
        if ref.turn_id in turns and turns[ref.turn_id].role == "user"
    ]
    assistant_spans = [
        ref.supporting_span
        for ref in unit.source_refs
        if ref.turn_id in turns and turns[ref.turn_id].role == "assistant"
    ]
    subject = unit.subject.strip()
    if not subject:
        return None

    # 1. first-person subject appearing in a cited user-role span
    if _is_first_person_subject(subject) and any(
        subject in span for span in user_spans
    ):
        return CURRENT_USER_ROLE
    # 1b. Formation speaker-normalized "用户" subject: bind only when the
    # grounded source itself establishes the proposition is about the
    # current user — a first-person reference in a cited user-role span,
    # or an assistant addressed reference confirmed by user acceptance.
    if _is_formation_user_surface(subject):
        if any(_contains_first_person(span) for span in user_spans):
            return CURRENT_USER_ROLE
        if any("你" in span for span in assistant_spans) and any(
            _ACCEPTANCE_PATTERN.match(span.strip()) for span in user_spans
        ):
            return CURRENT_USER_ROLE
        return None
    # 2. self-naming in a cited user-role span
    if any(subject in _self_names(span) for span in user_spans):
        return CURRENT_USER_ROLE
    # 3. assistant addressed naming + user acceptance in cited spans
    if any(subject in _addressed_names(span) for span in assistant_spans):
        if any(_ACCEPTANCE_PATTERN.match(span.strip()) for span in user_spans):
            return CURRENT_USER_ROLE
    # 4. segment-level self-name binding (single-user runtime)
    for turn in segment.turns:
        if turn.role == "user" and subject in _self_names(turn.content):
            return CURRENT_USER_ROLE
    return None


def classify_target_entity_ref(query: str) -> str | None:
    """Return the current user's generic EntityRef iff the recall query's
    retrieval target is the current user."""
    role = _classify_target_role(query)
    return CURRENT_USER_ENTITY_REF if role == CURRENT_USER_ROLE else None


def _classify_target_role(query: str) -> str | None:
    """Return CURRENT_USER_ROLE iff the query's retrieval target is the user."""
    text = query.strip()
    if not text:
        return None
    focus_index = -1
    for pattern in _FOCUS_PATTERNS:
        index = text.find(pattern)
        if index >= 0 and (focus_index < 0 or index < focus_index):
            focus_index = index
    if focus_index >= 0:
        tail = text[:focus_index]
        for splitter in _FRAMING_SPLITTERS:
            tail = tail.split(splitter)[-1]
        return CURRENT_USER_ROLE if _contains_first_person(tail) else None
    return CURRENT_USER_ROLE if _contains_first_person(text) else None


def entity_marked_text(entity_ref: str | None, text: str) -> str:
    """BGE scoring projection: prefix the constant SAME_ENTITY marker, if any.

    The marker is a fixed equality signal, not the ref itself: callers invoke
    this only for (query, candidate) pairs whose entity refs are equal, so the
    same token lands on both sides regardless of which entity is involved.
    Shadow evidence: ``docs/experiments/same_entity_marker/``,
    ``docs/experiments/same_entity_generalization/``,
    ``docs/experiments/context_role_entityref/``.
    """
    if entity_ref is None:
        return text
    return f"[SAME_ENTITY] {text}"


def candidate_entity_ref(metadata: object) -> str | None:
    """Read ``subject_entity_ref`` from candidate metadata; absent → None."""
    if not isinstance(metadata, dict):
        return None
    ref = metadata.get("subject_entity_ref")
    return ref if isinstance(ref, str) and ref.strip() else None


def _is_first_person_subject(subject: str) -> bool:
    return (
        subject in _ZH_FIRST_PERSON_SUBJECTS
        or subject.casefold() in _EN_FIRST_PERSON_SUBJECTS
    )


def _contains_first_person(text: str) -> bool:
    return "我" in text or bool(_EN_FIRST_PERSON_TOKEN.search(text))


def _self_names(text: str) -> set[str]:
    names = set()
    for match in _ZH_SELF_NAME_PATTERN.finditer(text):
        names.add(match.group("name") or match.group("call_name"))
    names.update(
        match.group("name").strip()
        for match in _EN_SELF_NAME_PATTERN.finditer(text)
    )
    return names


def _addressed_names(text: str) -> set[str]:
    names = {
        match.group("name")
        for match in _ZH_ADDRESSED_NAME_PATTERN.finditer(text)
    }
    names.update(
        match.group("name").strip()
        for match in _EN_ADDRESSED_NAME_PATTERN.finditer(text)
    )
    return names
