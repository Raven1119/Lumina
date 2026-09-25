"""Build blinded pairwise judging packets from two answer runs.

Usage (from Memory_lab/):
    python judge/build_packets.py --round judge/rounds/<name> --labels B1,P6 \
        --pair dev_a:<run_dir_of_first_label>:<run_dir_of_second_label> \
        --pair dev_b:<run_dir>:<run_dir>

Each run directory holds the probes.jsonl and config.json of one answer run. For every set
this writes packet_<set>_1.json and packet_<set>_2.json (same items, X/Y swapped),
key_<set>.json (which label is X in order 1) and meta.json. Items: every probe at its base
time, plus the +60-day variant of 淡忘/保留 probes. Only the judge-facing packets are
blinded; the key stays next to them, so judges must be told not to open other files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval_set'))
from build import simulate_hot  # noqa: E402

WD = '一二三四五六日'


def load_rows(run_dir):
    rows = {}
    for line in (Path(run_dir) / 'probes.jsonl').read_text(encoding='utf-8').splitlines():
        r = json.loads(line)
        rows[(r['probe_id'], r['variant_days'])] = r
    return rows


def fmt(t):
    who = '他' if t['role'] == 'user' else '林素'
    return f"[{t['id']}] {t['time']:%m-%d} 周{WD[t['time'].weekday()]} {t['time']:%H:%M} {who}：{t['text']}"


def build(round_dir, labels, pairs):
    round_dir.mkdir(parents=True, exist_ok=True)
    meta = {'labels': labels, 'sets': {}}
    for set_name, run_a, run_b in pairs:
        dialogue = json.loads((ROOT / 'eval_set' / 'built' / f'{set_name}.dialogue.json').read_text(encoding='utf-8'))
        gold = json.loads((ROOT / 'eval_set' / 'built' / f'{set_name}.gold.json').read_text(encoding='utf-8'))
        turns = [dict(t, time=datetime.fromisoformat(t['time'])) for s in dialogue['sessions'] for t in s['turns']]
        rows = {labels[0]: load_rows(run_a), labels[1]: load_rows(run_b)}
        rng = random.Random(f'judge-{round_dir.name}-{set_name}')
        items, key = [], {}
        for p in gold['probes']:
            variants = [0] + ([60] if p['category'] in ('淡忘', '保留') else [])
            for v in variants:
                k = (p['id'], v)
                if any(k not in rows[label] for label in labels):
                    raise SystemExit(f'{set_name} {k} missing from a run; answer runs need --answer-scope primary or all')
                t = datetime.fromisoformat(p['time'])
                when = t + timedelta(days=v)
                hot = simulate_hot([{'id': x['id'], 'role': x['role'], 'time': x['time']} for x in turns], t)
                last = [x for x in turns if x['id'] in hot][-6:]
                spec = next((g for g in p['gap_variants'] if g['offset_days'] == v), None) if v else p
                tags = set(sum(spec['must_surface_tags'], [])) | set(spec['must_not_surface_tags'])
                facts = [pl['summary'] for pl in gold['plants'] if set(pl['tags']) & tags]
                flip = rng.random() < 0.5
                iid = f"{set_name[-1]}-{len(items) + 1:02d}"
                x_label, y_label = (labels[1], labels[0]) if flip else (labels[0], labels[1])
                key[iid] = {'probe': p['id'], 'variant': v, 'category': p['category'],
                            'soft': bool(p.get('soft')), 'X': x_label, 'Y': y_label}
                items.append({'id': iid,
                              '时间': f"{when:%Y-%m-%d} 周{WD[when.weekday()]} {when:%H:%M}",
                              '说明': ('（这是 60 天后的变体：在最后一轮对话之后，过了 60 多天没有任何对话，他才发来这条消息。）' if v else ''),
                              '此前最近几轮': [fmt(x) for x in last],
                              '他发来': p['message'],
                              '相关事实（出题人标注）': facts,
                              '评分要点': {'should': p['rubric']['should'], 'should_not': p['rubric']['should_not']},
                              '出题人备注': p.get('note', ''),
                              'X': rows[x_label][k]['answer']['text'],
                              'Y': rows[y_label][k]['answer']['text']})
        transcript = '\n'.join(fmt(t) for t in turns)
        for order in (1, 2):
            its = items if order == 1 else [dict(it, X=it['Y'], Y=it['X']) for it in items]
            (round_dir / f'packet_{set_name}_{order}.json').write_text(
                json.dumps({'transcript': transcript, 'items': its}, ensure_ascii=False, indent=1), encoding='utf-8')
        (round_dir / f'key_{set_name}.json').write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding='utf-8')
        meta['sets'][set_name] = {
            'runs': {labels[0]: str(run_a), labels[1]: str(run_b)},
            'configs_sha256': {label: hashlib.sha256((Path(run) / 'config.json').read_bytes()).hexdigest()
                               for label, run in zip(labels, (run_a, run_b))},
            'items': len(items)}
    (round_dir / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--round', required=True, type=Path)
    ap.add_argument('--labels', required=True, help='two labels, e.g. B1,P6')
    ap.add_argument('--pair', action='append', required=True, help='set:run_dir_first:run_dir_second')
    args = ap.parse_args()
    labels = [x.strip() for x in args.labels.split(',')]
    if len(labels) != 2 or labels[0] == labels[1]:
        raise SystemExit('--labels needs two distinct names')
    pairs = [tuple(p.split(':', 2)) for p in args.pair]
    build(args.round, labels, pairs)
    print(args.round)


if __name__ == '__main__':
    main()
