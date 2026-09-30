"""Strict memory-side comparison of two P8 replay directories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / 'probes.jsonl').read_text(encoding='utf-8').splitlines()]


def _bytes(value) -> bytes:
    """Preserve JSON field order and numeric spelling for strict value comparison."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def compare(reference: Path, candidate: Path) -> list[dict]:
    old, new = _rows(reference), _rows(candidate)
    errors = []
    if len(old) != len(new):
        errors.append({'field': 'probe_count', 'old': len(old), 'new': len(new)})
    for index, (a, b) in enumerate(zip(old, new)):
        key = (a['probe_id'], a['variant_days'])
        if key != (b['probe_id'], b['variant_days']):
            errors.append({'index': index, 'field': 'probe_key', 'old': key,
                           'new': (b['probe_id'], b['variant_days'])})
            continue
        for section in ('near', 'remote', 'core'):
            left = [(m['id'], m['score']) for m in a['recall'][section]]
            right = [(m['id'], m['score']) for m in b['recall'][section]]
            if _bytes(left) != _bytes(right):
                errors.append({'probe': key, 'field': 'recall.' + section})
        for field in ('rendered', 'score', 'measure'):
            if _bytes(a[field]) != _bytes(b[field]):
                errors.append({'probe': key, 'field': field})
    old_summary = json.loads((reference / 'summary.json').read_text(encoding='utf-8'))
    new_summary = json.loads((candidate / 'summary.json').read_text(encoding='utf-8'))
    for field in ('by_category', 'measure', 'attribution'):
        if _bytes(old_summary[field]) != _bytes(new_summary[field]):
            errors.append({'field': 'summary.' + field})
    return errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('reference', type=Path)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    differences = compare(args.reference, args.candidate)
    print(json.dumps({'differences': differences, 'count': len(differences)}, ensure_ascii=False))
    raise SystemExit(bool(differences))
