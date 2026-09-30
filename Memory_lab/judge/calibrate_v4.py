"""Compare archived Claude and independent DeepSeek judgments without new scoring."""
from __future__ import annotations

import json
from math import sqrt
from pathlib import Path

from judge.aggregate import block, load

ROOT = Path(__file__).resolve().parent / 'rounds'
ROUNDS = ('v1_B1_vs_P6', 'v2_P6_e1_vs_e2', 'v2_P6_v1_vs_e1',
          'v2_e1_B1_vs_P6', 'v2_e2_B1_vs_P6')


def ranks(values):
    ordered = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    for start in range(len(values)):
        if start and values[ordered[start]] == values[ordered[start-1]]:
            continue
        end = start + 1
        while end < len(values) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        for pos in range(start, end):
            result[ordered[pos]] = (start + end - 1) / 2
    return result


def spearman(left, right):
    a, b = ranks(left), ranks(right)
    ma, mb = sum(a)/len(a), sum(b)/len(b)
    denom = sqrt(sum((x-ma)**2 for x in a)*sum((x-mb)**2 for x in b))
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/denom if denom else None


def compare(round_dir):
    labels, claude = load(round_dir)
    deep_dir = round_dir / ('judge_deepseek' if round_dir.name == ROUNDS[0] else 'judge_deepseek_v4')
    _, deepseek = load(round_dir, deep_dir)
    if set(claude) != set(deepseek):
        raise ValueError('incomplete judge round: ' + round_dir.name)
    ck = block(labels, list(claude.values()))['pairs']
    dk = block(labels, list(deepseek.values()))['pairs']
    direction = lambda p: 1 if p[labels[0]] > p[labels[1]] else -1 if p[labels[1]] > p[labels[0]] else 0
    jointly_consistent = agrees = wrong_same = wrong_total = 0
    c_alive, d_alive = [], []
    by_category = {}
    for key in sorted(claude):
        c, d = claude[key], deepseek[key]
        if c['prefer'][0] == c['prefer'][1] != 'tie' and d['prefer'][0] == d['prefer'][1] != 'tie':
            jointly_consistent += 1
            cat = by_category.setdefault(c['category'], {'agree': 0, 'total': 0})
            cat['total'] += 1
            if c['prefer'][0] == d['prefer'][0]:
                agrees += 1
                cat['agree'] += 1
        for label in labels:
            for cj, dj in zip(c['judgments'][label], d['judgments'][label]):
                wrong_total += 1
                wrong_same += cj['wrong'] == dj['wrong']
                c_alive.append(cj['alive'])
                d_alive.append(dj['alive'])
    return {'round': round_dir.name, 'labels': labels, 'claude_pairs': ck, 'deepseek_pairs': dk,
            'direction_same': direction(ck) == direction(dk),
            'item_agreement': {'agree': agrees, 'total': jointly_consistent,
                               'rate': agrees/jointly_consistent if jointly_consistent else None},
            'wrong_agreement': {'agree': wrong_same, 'total': wrong_total, 'rate': wrong_same/wrong_total},
            'alive_spearman': spearman(c_alive, d_alive), 'by_category': by_category}


if __name__ == '__main__':
    rows = [compare(ROOT / name) for name in ROUNDS]
    target = Path(__file__).resolve().parent / 'calibration_v4.json'
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(target)
