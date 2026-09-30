"""Render every model-produced preference and wrong-history reason into a portable report."""
from __future__ import annotations

import json
from pathlib import Path

from judge.aggregate import load
from judge.build_packets import load_rows

LAB = Path(__file__).resolve().parents[1]


def run_path(value):
    path = Path(value)
    return path if path.is_absolute() or path.exists() else LAB / path


def percent(value):
    return '—' if value is None else f'{value*100:.1f}%'


def decision(summary: dict, noise: int | None) -> str:
    if noise is None:
        return '噪声带未确定'
    a, b = summary['labels']
    p = summary['overall']['pairs']
    net = p[b]-p[a]
    return f'{b}更好' if net > noise else f'{b}更差' if net < -noise else '无可测差异'


def render_round(round_dir: Path, judge_dir: Path, noise: int | None = None) -> str:
    labels, merged = load(round_dir, judge_dir)
    rows = list(merged.values())
    summary = json.loads((judge_dir/'summary.json').read_text(encoding='utf-8'))
    a, b = labels
    p = summary['overall']['pairs']
    o = summary['overall']
    ptext = f'{o["sign_test_p"]:.4g}' if o['sign_test_p'] is not None else '—'
    lines = [f'### `{round_dir.name}`：{a} vs {b}', '',
             '| n | 一致胜（旧） | 一致胜（新） | 平 | 不一致 | 说错率旧/新 | 活人感旧/新 | 符号检验 p | 噪声带判定 |',
             '| ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- |',
             f'| {o["n"]} | {p[a]} | {p[b]} | {p["tie"]} | {p["split"]} | '
             f'{percent(o[a]["wrong"])} / {percent(o[b]["wrong"])} | '
             f'{o[a]["alive"]:.2f} / {o[b]["alive"]:.2f} | {ptext} | {decision(summary, noise)} |', '',
             '| 类别 | n | 旧胜 | 新胜 | 平 | 不一致 |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for cat, entry in summary['by_category'].items():
        pairs=entry['pairs']
        lines.append(f'| {cat} | {entry["n"]} | {pairs[a]} | {pairs[b]} | {pairs["tie"]} | {pairs["split"]} |')
    lines += ['', '**全部非平局条目**（偏好及理由依次为原顺序、X/Y 交换顺序）：', '']
    for row in rows:
        if row['prefer'] == ['tie','tie']:
            continue
        probe=row['probe']+(f'+{row["variant"]}' if row['variant'] else '')
        lines.append(f'- {probe}［{row["category"]}］：{row["prefer"][0]} / {row["prefer"][1]}。理由一：{row["reason"][0]} 理由二：{row["reason"][1]}')
    lines += ['', '**全部说错往事标记**（两遍分别列出）：', '']
    wrong=0
    for row in rows:
        probe=row['probe']+(f'+{row["variant"]}' if row['variant'] else '')
        for label in labels:
            for order, judgment in enumerate(row['judgments'][label],1):
                if judgment['wrong']:
                    wrong+=1
                    lines.append(f'- {probe}［{row["category"]}］{label}，第 {order} 遍：{judgment["note"]}')
    if not wrong:lines.append('- 无。')
    return '\n'.join(lines)+'\n'


def answer_examples(round_dir: Path, judge_dir: Path, cap: int = 15) -> str:
    labels, merged = load(round_dir, judge_dir)
    meta=json.loads((round_dir/'meta.json').read_text(encoding='utf-8'))
    selected=[r for r in merged.values() if r['prefer'] != ['tie','tie']]
    selected.sort(key=lambda r: (r['category'] not in ('淡忘','保留'), r['set'], r['probe'], r['variant']))
    lines=['**目标探针回答原文对照**（按目标类别优先，最多 15 条）：','']
    cache={}
    for row in selected[:cap]:
        set_name=row['set']
        if set_name not in cache:
            cache[set_name]={label:load_rows(run_path(meta['sets'][set_name]['runs'][label])) for label in labels}
        key=(row['probe'],row['variant'])
        lines += [f'- {row["probe"]}+{row["variant"]}［{row["category"]}］',
                  f'  - {labels[0]}：{cache[set_name][labels[0]][key]["answer"]["text"]}',
                  f'  - {labels[1]}：{cache[set_name][labels[1]][key]["answer"]["text"]}']
    if not selected:lines.append('- 无非平局条目。')
    return '\n'.join(lines)+'\n'
