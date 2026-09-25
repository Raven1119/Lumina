"""Unblind and aggregate a judging round.

Usage (from Memory_lab/):  python judge/aggregate.py judge/rounds/<name>
Reads meta.json, key_<set>.json and the judges' out_<set>_1.json / out_<set>_2.json, and
writes summary.json and summary.md in the round directory.

- should / should_not: mean over answers of the per-answer mean item score
- wrong: share of judgments flagging a false or unsupported claim about the past
- alive: mean 1-5 rating
- pairs: a label wins an item only if both orders prefer it; `split` = the orders disagree
- sign_test_p: two-sided sign test over consistent wins
"""
from __future__ import annotations

import json
import sys
from math import comb
from pathlib import Path


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def sign_test(a, b):
    n = a + b
    if n == 0:
        return None
    k = max(a, b)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def load(round_dir):
    meta = json.loads((round_dir / 'meta.json').read_text(encoding='utf-8'))
    labels = meta['labels']
    merged = {}
    for set_name in meta['sets']:
        key = json.loads((round_dir / f'key_{set_name}.json').read_text(encoding='utf-8'))
        for order in (1, 2):
            out = json.loads((round_dir / f'out_{set_name}_{order}.json').read_text(encoding='utf-8'))
            ids = {it['id'] for it in out['items']}
            if ids != set(key):
                raise SystemExit(f'{set_name} order {order}: item ids do not match the key')
            for it in out['items']:
                k = key[it['id']]
                to_label = {'X': k['X'], 'Y': k['Y']} if order == 1 else {'X': k['Y'], 'Y': k['X']}
                row = merged.setdefault(it['id'], {**k, 'set': set_name, 'judgments': {l: [] for l in labels},
                                                    'prefer': [], 'reason': []})
                for side in ('X', 'Y'):
                    row['judgments'][to_label[side]].append(it[side])
                row['prefer'].append('tie' if it['prefer'] == 'tie' else to_label[it['prefer']])
                row['reason'].append(it['reason'])
    return labels, merged


def block(labels, rows):
    out = {'n': len(rows)}
    for label in labels:
        js = [j for r in rows for j in r['judgments'][label]]
        out[label] = {'should': mean([mean(j['should']) for j in js if j['should']]),
                      'should_not': mean([mean(j['should_not']) for j in js if j['should_not']]),
                      'wrong': mean([1.0 if j['wrong'] else 0.0 for j in js]),
                      'alive': mean([j['alive'] for j in js])}
    pairs = {labels[0]: 0, labels[1]: 0, 'tie': 0, 'split': 0}
    for r in rows:
        a, b = r['prefer']
        pairs[a if a == b else 'split'] += 1
    out['pairs'] = pairs
    out['sign_test_p'] = sign_test(pairs[labels[0]], pairs[labels[1]])
    return out


def fmt(v, digits=2):
    return '—' if v is None else f'{v:.{digits}f}'


def main(round_dir):
    round_dir = Path(round_dir)
    labels, merged = load(round_dir)
    rows = list(merged.values())
    sets = sorted({r['set'] for r in rows})
    cats = sorted({r['category'] for r in rows})
    summary = {'labels': labels,
               'overall': block(labels, rows),
               'by_set': {s: block(labels, [r for r in rows if r['set'] == s]) for s in sets},
               'by_category': {c: block(labels, [r for r in rows if r['category'] == c]) for c in cats},
               'agreement': {'prefer': sum(r['prefer'][0] == r['prefer'][1] for r in rows),
                             'wrong_flags': sum(r['judgments'][l][0]['wrong'] == r['judgments'][l][1]['wrong']
                                                for r in rows for l in labels),
                             'items': len(rows)},
               'wrong_notes': [{'item': r['probe'] + (f"+{r['variant']}" if r['variant'] else ''), 'label': l,
                                'note': next(j['note'] for j in r['judgments'][l] if j['wrong'])}
                               for r in rows for l in labels if any(j['wrong'] for j in r['judgments'][l])]}
    (round_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    a, b = labels
    lines = [f'# 回答层评审：{a} vs {b}', '',
             f"两遍评审（交换 X/Y 顺序）偏好一致 {summary['agreement']['prefer']}/{summary['agreement']['items']}；"
             f"“说错往事”标记一致 {summary['agreement']['wrong_flags']}/{2 * summary['agreement']['items']}。", '',
             f'| 范围 | n | 要点 {a} | 要点 {b} | 说错 {a} | 说错 {b} | 活人感 {a} | 活人感 {b} | {a} 胜 | {b} 胜 | 平 | 不一致 | 符号检验 p |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    def line(name, blk):
        return (f"| {name} | {blk['n']} | {fmt(blk[a]['should'])} | {fmt(blk[b]['should'])} | "
                f"{fmt(blk[a]['wrong'], 3)} | {fmt(blk[b]['wrong'], 3)} | {fmt(blk[a]['alive'])} | {fmt(blk[b]['alive'])} | "
                f"{blk['pairs'][a]} | {blk['pairs'][b]} | {blk['pairs']['tie']} | {blk['pairs']['split']} | {fmt(blk['sign_test_p'], 3)} |")
    lines.append(line('合计', summary['overall']))
    for s in sets:
        lines.append(line(s, summary['by_set'][s]))
    for c in cats:
        lines.append(line(c, summary['by_category'][c]))
    lines += ['', '## 被标为“说错往事”的回答', '']
    lines += [f"- {w['item']}（{w['label']}）：{w['note']}" for w in summary['wrong_notes']] or ['- 无']
    lines += ['', '## 非平局条目', '']
    for r in rows:
        if r['prefer'] == ['tie', 'tie']:
            continue
        tag = r['probe'] + (f"+{r['variant']}" if r['variant'] else '')
        lines.append(f"- {tag}［{r['category']}］偏好 {r['prefer'][0]} / {r['prefer'][1]}：{r['reason'][0]}")
    (round_dir / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(round_dir / 'summary.md')


if __name__ == '__main__':
    main(sys.argv[1])
