"""Read-only comparison of the two Organ A judgments on the original 14 P8 cases.

The output preserves both orders and their original reasons. It makes no model
request and does not assign new scores or infer a causal mechanism.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def diagnose(first: Path, second: Path) -> list[dict]:
    original = json.loads((first / 'answers.json').read_text(encoding='utf-8'))
    cases = [(row['id'], row['variant']) for row in original
             if row['p8_origin'] == 'archived_P8']
    if len(cases) != 14 or len(set(cases)) != 14:
        raise ValueError('expected_14_original_P8_cases')
    rounds = []
    for directory in (first, second):
        summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
        rows = {(row['probe'], row['variant']): row for row in summary['rows']}
        if any(case not in rows for case in cases):
            raise ValueError('missing_judgment_case')
        rounds.append(rows)
    return [
        {'probe': probe, 'variant_days': variant,
         'first': {'order_preferences': rounds[0][probe, variant]['prefer'],
                   'reasons': rounds[0][probe, variant]['reason']},
         'second': {'order_preferences': rounds[1][probe, variant]['prefer'],
                    'reasons': rounds[1][probe, variant]['reason']}}
        for probe, variant in cases
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--first', required=True, type=Path)
    parser.add_argument('--second', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    rows = diagnose(args.first, args.second)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print('cases', len(rows), 'new_calls', 0)


if __name__ == '__main__':
    main()
