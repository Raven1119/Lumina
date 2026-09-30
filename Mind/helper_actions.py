"""Typed, forgiving helper action parsing shared by dialogue and event thoughts."""
from __future__ import annotations

import json


def object_from_text(text):
    # A provider may return a complete JSON object after our JSON prefill.
    # In that case the recorded response starts with a broken outer prefix,
    # followed by the complete object. Recover that object without retrying.
    decoder = json.JSONDecoder(strict=False)
    for offset, character in enumerate(text):
        if character != '{':
            continue
        try:
            value, _ = decoder.raw_decode(text, offset)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and any(k in value for k in ('行动', '回复', '理解', '说')):
            return value
    return None


def actions_from_object(value):
    if not isinstance(value, dict):
        return []
    raw_actions = value.get('行动')
    if isinstance(raw_actions, str):
        raw_actions = [raw_actions]
    if not isinstance(raw_actions, list):
        return []
    # A complete but compact model response can use ["读", "path"] for one
    # action. Accept only this unambiguous two-item spelling.
    if (len(raw_actions) == 2 and raw_actions[0] in ('读', '回忆', '取消', '搁置')
            and isinstance(raw_actions[1], str)):
        raw_actions = [{raw_actions[0]: raw_actions[1]}]
    actions = []
    for item in raw_actions:
        if isinstance(item, str):
            name, separator, content = item.partition(' ')
            if separator and name in ('读', '回忆', '取消', '搁置'):
                item = {name: content.strip()}
        if not isinstance(item, dict) or len(item) != 1:
            continue
        name, content = next(iter(item.items()))
        if name in ('读', '回忆', '取消', '搁置'):
            if isinstance(content, str) and content.strip():
                actions.append({name: content.strip()})
        elif name == '派活':
            if isinstance(content, dict) and set(content) == {'目标', '理由', '验收', '背景'} and all(
                    isinstance(field, str) and field.strip() for field in content.values()):
                actions.append({name: content})
        elif name == '答复':
            if isinstance(content, dict) and set(content) == {'帮手', '内容'} and all(
                    isinstance(field, str) and field.strip() for field in content.values()):
                actions.append({name: content})
    return actions


def parse_event(text):
    value = object_from_text(text)
    if value is None:
        return None
    speech = value.get('说', '')
    thought = value.get('思绪', '')
    carry = value.get('带着', [])
    return {'speech': speech if isinstance(speech, str) else '',
            'thought': thought if isinstance(thought, str) else '',
            'carry': [item for item in carry if isinstance(item, str)] if isinstance(carry, list) else [],
            'actions': actions_from_object(value)}
