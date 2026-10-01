"""Tolerant text-only parsing for non-dialogue thought handoff."""
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
            'protocol_residue': '行动' in value}
