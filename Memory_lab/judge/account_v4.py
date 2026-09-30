"""Count durable new cached model responses in a UTC time window."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path(__file__).resolve().parents[1] / 'cache' / 'llm'


def count(since: datetime, until: datetime | None = None) -> dict:
    buckets = {}
    for path in CACHE.glob('*.json'):
        at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if at < since or until is not None and at >= until:
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        purpose = data.get('purpose', 'unknown')
        row = buckets.setdefault(purpose, {'calls': 0, 'input_tokens': 0, 'output_tokens': 0})
        row['calls'] += 1
        row['input_tokens'] += int(data.get('usage', {}).get('input_tokens', 0))
        row['output_tokens'] += int(data.get('usage', {}).get('output_tokens', 0))
    return dict(sorted(buckets.items()))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', required=True, help='ISO UTC timestamp')
    ap.add_argument('--until', help='exclusive ISO UTC timestamp')
    args = ap.parse_args()
    at = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(timezone.utc)
    print(json.dumps(count(at(args.since), at(args.until) if args.until else None),
                     ensure_ascii=False, indent=2))
