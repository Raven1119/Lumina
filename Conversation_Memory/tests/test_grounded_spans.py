from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from adapter._grounded_spans import (
    GroundedSpanUnit,
    build_grounded_spans,
)
from adapter.models import ColdDraftTurn


def _turn(
    content: str,
    *,
    role: str = "user",
    turn_id: str = "turn-1",
) -> ColdDraftTurn:
    return ColdDraftTurn(
        turn_id=turn_id,
        role=role,
        content=content,
        timestamp=datetime(2026, 8, 1, tzinfo=UTC),
        source_timezone="Asia/Shanghai",
        timezone_source="synthetic_fixture",
    )


def test_exact_source_fidelity_and_same_turn_split() -> None:
    source = "我今晚准备早点睡。另外实验室打印机坏了。"

    units = build_grounded_spans((_turn(source),))

    assert [unit.text for unit in units] == [
        "我今晚准备早点睡。",
        "另外实验室打印机坏了。",
    ]
    assert [(unit.start, unit.end) for unit in units] == [
        (0, len("我今晚准备早点睡。")),
        (len("我今晚准备早点睡。"), len(source)),
    ]
    assert all(source[unit.start:unit.end] == unit.text for unit in units)


def test_assistant_turns_are_eligible_and_preserve_role() -> None:
    source = "你明天还有实验室例会。"
    units = build_grounded_spans((
        _turn(source, role="assistant"),
    ))

    assert len(units) == 1
    assert units[0].source_role == "assistant"
    assert units[0].text == source
    assert source[units[0].start:units[0].end] == units[0].text


@pytest.mark.parametrize(
    ("source", "required"),
    [
        ("我明天其实没有实验室例会。", "没有"),
        ("我可能下个月去上海。", "可能"),
        ("服务端口改成5433。", "5433"),
        ("下周三的项目评审安排在海棠会议室 307。", "海棠会议室 307"),
    ],
)
def test_correction_uncertainty_and_hard_details_are_exact(
    source: str,
    required: str,
) -> None:
    units = build_grounded_spans((_turn(source),))

    assert len(units) == 1
    assert units[0].text == source
    assert required in units[0].text
    assert source[units[0].start:units[0].end] == units[0].text


def test_multi_turn_conversation_units_remain_independent() -> None:
    turns = (
        _turn("我的朋友小林下周要出差。", turn_id="turn-1"),
        _turn("去哪里？", role="assistant", turn_id="turn-2"),
        _turn("去上海。", turn_id="turn-3"),
    )

    units = build_grounded_spans(turns)

    assert [(unit.turn_id, unit.source_role, unit.text) for unit in units] == [
        ("turn-1", "user", "我的朋友小林下周要出差。"),
        ("turn-2", "assistant", "去哪里？"),
        ("turn-3", "user", "去上海。"),
    ]


def test_low_signal_user_spans_are_retained() -> None:
    source = "你好。谢谢。哈哈。好。"

    units = build_grounded_spans((_turn(source),))

    assert [unit.text for unit in units] == [
        "你好。",
        "谢谢。",
        "哈哈。",
        "好。",
    ]


def test_assistant_only_input_produces_grounded_units() -> None:
    units = build_grounded_spans((
        _turn("好的。", role="assistant"),
        _turn("还需要什么？", role="assistant", turn_id="turn-2"),
    ))

    assert [unit.source_role for unit in units] == [
        "assistant",
        "assistant",
    ]
    assert [unit.text for unit in units] == ["好的。", "还需要什么？"]


def test_long_source_is_bounded_without_overlap_or_loss() -> None:
    source = "甲" * 321

    units = build_grounded_spans((_turn(source),))

    assert [len(unit.text) for unit in units] == [160, 160, 1]
    assert [(unit.start, unit.end) for unit in units] == [
        (0, 160),
        (160, 320),
        (320, 321),
    ]
    assert "".join(unit.text for unit in units) == source


def test_output_and_unit_identity_are_deterministic() -> None:
    turns = (_turn("abc?def", turn_id="stable-turn"),)

    first = build_grounded_spans(turns)
    second = build_grounded_spans(turns)

    assert first == second
    assert [unit.unit_id for unit in first] == [
        "grounded_span_v2:stable-turn:0:4",
        "grounded_span_v2:stable-turn:4:7",
    ]
    assert [field.name for field in fields(GroundedSpanUnit)] == [
        "unit_id",
        "turn_id",
        "source_role",
        "text",
        "start",
        "end",
    ]


def test_source_coverage_has_only_documented_whitespace_gaps() -> None:
    source = "  第一段。\n第二段？第三段!  "

    units = build_grounded_spans((_turn(source),))
    coverage = [0] * len(source)
    for unit in units:
        assert source[unit.start:unit.end] == unit.text
        for index in range(unit.start, unit.end):
            coverage[index] += 1

    assert all(count <= 1 for count in coverage)
    uncovered = [
        source[index]
        for index, count in enumerate(coverage)
        if count == 0
    ]
    assert uncovered == ["\n", " ", " "]
    assert all(character.isspace() for character in uncovered)
