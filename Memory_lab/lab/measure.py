"""Measurement v2: how much of the history a recall hands over, and whether it beats chance.

The v1 source-coverage score saturates when a few long, revise-accumulated memories cover
most of a storyline: any recalled item whose sources touch one gold turn counts as a hit.
This module adds measurements that separate "recalled the right thing" from "recalled a
lot of history":

- size       distinct source turns per view (cue = near+remote, core, raw, all)
- complete   v1-style strict complete hit per view; core is cue-independent, so it is
             scored on its own instead of being credited to the cue
- chance     exact chance level of the same hit for (a) a uniform random set of the same
             number of Cold turns, (b) the same number of memories drawn uniformly from the
             awake memory pool at probe time
- rank       rank of the first memory (score order, diagnostics.top_scores, top 20) whose
             sources hit each group; complete@k and MRR@20
- written    whether stored memories, including dormant ones, cover every group at all
             (upper bound: separates write-side from recall-side misses)
- specific   complete hit counting only raw turns and memories whose sources span at most
             SPECIFIC_MAX_SESSIONS sessions (long storyline memories excluded)
- time       for 时间-category probes with a parsed interval: share of cue-memory sources
             inside the interval, and complete hit counting only raw turns inside it and memories with
             at least INTERVAL_SHARE_MIN of their sources inside it
- trap_rank  rank of the first memory that hits must_not_surface and no must group

Everything is read-only and deterministic (exact combinatorics, no sampling). It never
touches Dream inputs, so --cache-only reruns reuse the existing Dream cache.
"""
from __future__ import annotations

from datetime import datetime
from math import comb

from memlab.strength import strengths

RANK_KS = (1, 3, 6)
SPECIFIC_MAX_SESSIONS = 3
INTERVAL_SHARE_MIN = 0.5
VIEWS = ('cue', 'core', 'raw', 'cue_raw', 'all')


def _session(turn_id):
    return turn_id.rsplit('-t', 1)[0]


def _hit(sources, group):
    return not set(sources).isdisjoint(group)


def _complete(sources, groups):
    return all(_hit(sources, g) for g in groups) if groups else None


def chance_turns(universe_size, groups, drawn):
    """Exact P(a uniform `drawn`-subset of `universe_size` turns hits every group).

    `groups` are sets already restricted to the universe. Inclusion-exclusion over groups.
    """
    if not groups:
        return None
    if any(not g for g in groups) or drawn <= 0:
        return 0.0
    if drawn >= universe_size:
        return 1.0
    total = comb(universe_size, drawn)
    p = 0.0
    for mask in range(1 << len(groups)):
        chosen = [g for i, g in enumerate(groups) if mask >> i & 1]
        union = len(set().union(*chosen)) if chosen else 0
        rest = universe_size - union
        p += (-1) ** len(chosen) * (comb(rest, drawn) / total if rest >= drawn else 0.0)
    return min(1.0, max(0.0, p))


def chance_memories(pool_sources, groups, drawn):
    """Exact P(`drawn` memories picked uniformly from the pool jointly hit every group).

    `pool_sources` is a list of source sets, one per pool memory.
    """
    if not groups:
        return None
    n = len(pool_sources)
    if drawn <= 0 or n == 0:
        return 0.0
    drawn = min(drawn, n)
    total = comb(n, drawn)
    hitters = [{i for i, src in enumerate(pool_sources) if _hit(src, g)} for g in groups]
    p = 0.0
    for mask in range(1 << len(groups)):
        chosen = [h for i, h in enumerate(hitters) if mask >> i & 1]
        union = len(set().union(*chosen)) if chosen else 0
        rest = n - union
        p += (-1) ** len(chosen) * (comb(rest, drawn) / total if rest >= drawn else 0.0)
    return min(1.0, max(0.0, p))


def _intervals(result, now):
    spans = []
    for start, end in result.diagnostics.get('time_intervals', []):
        start, end = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if start < now:
            spans.append((start, min(end, now)))
    return spans


def measure_probe(result, probe, snapshot, now, cfg):
    groups = [set(g) for g in probe.get('must_surface', [])]
    traps = set(probe.get('must_not_surface', []))
    cold_time = {t.id: t.time for t in snapshot.cold}
    universe = set(cold_time)
    by_id = {m['id']: m for m in snapshot.memories}

    cue_items = [*result.near, *result.remote]
    raw_ids = {r.turn_id for r in result.raw}

    def sources(items):
        return {s for m in items for s in m.sources}

    views = {'cue': sources(cue_items), 'core': sources(result.core), 'raw': raw_ids}
    views['cue_raw'] = views['cue'] | raw_ids
    views['all'] = views['cue_raw'] | views['core']

    out = {'size': {name: len(views[name]) for name in VIEWS},
           'n_groups': len(groups)}
    out['size'].update({'cold': len(universe), 'cue_items': len(cue_items)})

    # Awake pool at probe time (dormancy depends on `now`, so gap variants shrink it).
    if snapshot.memories:
        state = strengths(snapshot.memories, now, cfg)
        pool = [m for m, row in zip(snapshot.memories, state) if not bool(row[2])]
    else:
        pool = []
    out['size']['pool'] = len(pool)

    ranking = [row['id'] for row in result.diagnostics.get('top_scores', [])
               if row.get('score', 0) > 0 and row['id'] in by_id]

    def first_rank(group):
        return next((r for r, mid in enumerate(ranking, 1) if _hit(by_id[mid]['sources'], group)), None)

    trap_rank = None
    if traps:
        trap_rank = next((r for r, mid in enumerate(ranking, 1)
                          if _hit(by_id[mid]['sources'], traps)
                          and not any(_hit(by_id[mid]['sources'], g) for g in groups)), None)
    out['trap_rank'] = trap_rank

    if not groups:
        out.update({'complete': None, 'chance_turns': None, 'chance_memories_cue': None,
                    'rank': None, 'written_complete': None, 'specific_complete': None,
                    'time': None})
        return out

    in_universe = [g & universe for g in groups]
    out['complete'] = {name: _complete(views[name], groups) for name in VIEWS}
    out['chance_turns'] = {name: chance_turns(len(universe), in_universe, len(views[name] & universe))
                           for name in ('cue_raw', 'all')}
    out['chance_memories_cue'] = chance_memories([set(m['sources']) for m in pool], groups, len(cue_items))

    ranks = [first_rank(g) for g in groups]
    out['rank'] = {'group_ranks': ranks,
                   'complete_at': {str(k): all(r is not None and r <= k for r in ranks) for k in RANK_KS},
                   'mrr': sum(1 / r if r else 0.0 for r in ranks) / len(ranks)}

    out['written_complete'] = all(any(_hit(m['sources'], g) for m in snapshot.memories) for g in groups)

    specific = [m for m in cue_items if len({_session(s) for s in m.sources}) <= SPECIFIC_MAX_SESSIONS]
    out['specific_complete'] = _complete(sources(specific) | raw_ids, groups)

    spans = _intervals(result, now) if probe.get('category') == '时间' else []
    if spans:
        def inside(turn_id):
            at = cold_time.get(turn_id)
            return at is not None and any(a <= at < b for a, b in spans)
        cue_sources = [s for m in cue_items for s in m.sources]
        share = sum(inside(s) for s in cue_sources) / len(cue_sources) if cue_sources else None
        focused = [m for m in cue_items
                   if m.sources and sum(inside(s) for s in m.sources) / len(m.sources) >= INTERVAL_SHARE_MIN]
        in_raw = {t for t in raw_ids if inside(t)}
        out['time'] = {'intervals': len(spans), 'interval_share': share,
                       'interval_complete': _complete(sources(focused) | in_raw, groups)}
    else:
        out['time'] = None
    return out


def _mean(values):
    values = [float(v) for v in values if v is not None]
    return sum(values) / len(values) if values else None


def summarize_measure(records):
    """Per category on the primary variant (+60 days for 淡忘/保留), plus an overall row over
    every probe that has must_surface groups. Rows marked not_scored are skipped."""
    rows = [r for r in records
            if r.get('variant_days', 0) == (60 if r['category'] in ('淡忘', '保留') else 0)
            and not r.get('not_scored') and 'measure' in r]

    def block(items):
        scored = [r['measure'] for r in items if r['measure']['n_groups']]
        m = {'n': len(items), 'n_with_groups': len(scored)}
        for name in VIEWS:
            m[f'size_{name}'] = _mean(r['measure']['size'][name] for r in items)
        m['size_pool'] = _mean(r['measure']['size']['pool'] for r in items)
        if scored:
            for name in VIEWS:
                m[f'complete_{name}'] = _mean(x['complete'][name] for x in scored)
            m['chance_turns_cue_raw'] = _mean(x['chance_turns']['cue_raw'] for x in scored)
            m['chance_turns_all'] = _mean(x['chance_turns']['all'] for x in scored)
            m['chance_memories_cue'] = _mean(x['chance_memories_cue'] for x in scored)
            if m['complete_cue'] is not None and m['chance_memories_cue'] is not None:
                m['lift_cue'] = m['complete_cue'] - m['chance_memories_cue']
            for k in RANK_KS:
                m[f'complete_at_{k}'] = _mean(x['rank']['complete_at'][str(k)] for x in scored)
            m['mrr'] = _mean(x['rank']['mrr'] for x in scored)
            m['written_complete'] = _mean(x['written_complete'] for x in scored)
            m['specific_complete'] = _mean(x['specific_complete'] for x in scored)
            timed = [x['time'] for x in scored if x['time']]
            if timed:
                m['n_timed'] = len(timed)
                m['interval_share'] = _mean(t['interval_share'] for t in timed)
                m['interval_complete'] = _mean(t['interval_complete'] for t in timed)
        traps = [r['measure']['trap_rank'] for r in items if r['measure']['trap_rank'] is not None]
        m['trap_ranks'] = traps
        return m

    by = {}
    for r in rows:
        by.setdefault(r['category'], []).append(r)
    out = {'by_category': {cat: block(items) for cat, items in sorted(by.items())},
           'overall': block([r for r in rows if r['measure']['n_groups']])}
    return out


def probe_values(records, metric):
    """Per-probe values on the primary variant for paired comparisons across stages."""
    values = {}
    for r in records:
        if r.get('variant_days', 0) != (60 if r['category'] in ('淡忘', '保留') else 0):
            continue
        if r.get('not_scored') or 'measure' not in r or not r['measure']['n_groups']:
            continue
        m = r['measure']
        if metric == 'mrr':
            values[r['probe_id']] = m['rank']['mrr']
        elif metric.startswith('complete_at_'):
            values[r['probe_id']] = float(m['rank']['complete_at'][metric.rsplit('_', 1)[1]])
        elif metric.startswith('complete_'):
            values[r['probe_id']] = float(m['complete'][metric[len('complete_'):]])
        elif metric == 'specific_complete':
            values[r['probe_id']] = float(m['specific_complete'])
        else:
            raise ValueError(metric)
    return values


COLUMNS = (
    ('n_with_groups', '有组探针', 'd'),
    ('complete_cue', 'cue 完全', 'f'),
    ('chance_memories_cue', '随机记忆', 'f'),
    ('lift_cue', '提升', 'f'),
    ('complete_at_1', '@1', 'f'),
    ('complete_at_3', '@3', 'f'),
    ('complete_at_6', '@6', 'f'),
    ('mrr', 'MRR@20', 'f'),
    ('written_complete', '已写入', 'f'),
    ('specific_complete', '具体命中', 'f'),
    ('complete_cue_raw', 'cue+原文', 'f'),
    ('chance_turns_cue_raw', '随机轮次(cue+原文)', 'f'),
    ('complete_all', 'all', 'f'),
    ('chance_turns_all', '随机轮次(all)', 'f'),
    ('complete_core', '仅心上之事', 'f'),
    ('size_cue', 'cue 来源数', '1'),
    ('size_all', 'all 来源数', '1'),
    ('size_pool', '记忆池', '1'),
)


def _cell(value, kind):
    if value is None:
        return '—'
    if kind == 'd':
        return str(int(value))
    if kind == '1':
        return f'{value:.1f}'
    return f'{value:.3f}'


def measure_table(rows, label_key, label_title):
    """rows: list of (label, block) where block is a summarize_measure block."""
    lines = ['| ' + label_title + ' | ' + ' | '.join(title for _, title, _ in COLUMNS) + ' |',
             '| --- | ' + ' | '.join('---:' for _ in COLUMNS) + ' |']
    for label, block in rows:
        lines.append('| ' + label + ' | ' + ' | '.join(_cell(block.get(key), kind) for key, _, kind in COLUMNS) + ' |')
    return lines


def time_table(rows):
    lines = ['| 阶段 | 时间类探针（有区间） | 区间内来源占比 | 区间内完全命中 |', '| --- | ---: | ---: | ---: |']
    for label, block in rows:
        lines.append(f"| {label} | {_cell(block.get('n_timed'), 'd')} | {_cell(block.get('interval_share'), 'f')} | "
                     f"{_cell(block.get('interval_complete'), 'f')} |")
    return lines


LEGEND = [
    '口径（详见 `lab/measure.py`）：',
    '',
    '- cue = 近联想 + 远联想；心上之事与线索无关，单列为“仅心上之事”，不计入 cue。',
    '- 随机记忆：从探针时刻未沉睡的记忆池里均匀抽取与 cue 同样条数的记忆，完全命中的精确概率。提升 = cue 完全 − 随机记忆。',
    '- 随机轮次：从当时全部 Cold 轮次里均匀抽取与该视图同样多的轮次，完全命中的精确概率。',
    '- @k：按召回分数排序（top 20），每组第一条命中记忆的名次都 ≤ k 才算完全命中。MRR@20 按组平均。',
    '- 已写入：存储中的记忆（包括沉睡记忆）能命中每一组的比例，是写入侧覆盖上限。',
    '- 具体命中：只计原文和来源不超过 3 个会话的记忆，排除长主线记忆的“顺带命中”。',
    '- 时间区间：只看“时间”类探针。区间内来源占比 = cue 记忆的来源里落在所问时间段内的比例；区间内完全命中只计该时间段内的原文，和一半以上来源落在该时间段内的记忆。',
    '- 淡忘/保留取 +60 天变体；对照类没有金标准组，不进入有组统计。',
]


PAIRED_METRICS = ('complete_cue', 'complete_at_1', 'complete_at_3', 'mrr', 'specific_complete')


def write_comparison_v2(root, names, stages):
    """Suite-level tables for measurement v2: comparison_v2.json and comparison_v2.md."""
    import json
    from pathlib import Path

    from .report import write_json
    from .scoring import paired_delta

    root = Path(root)

    def load(name, stage):
        run = root / f'{name}_{stage}'
        summary = json.loads((run / 'summary.json').read_text(encoding='utf-8'))
        records = [json.loads(line) for line in (run / 'probes.jsonl').read_text(encoding='utf-8').splitlines()]
        return summary.get('measure'), records

    data = {'overall': {}, 'by_category': {}, 'paired_deltas': []}
    lines = ['# 阶段对照：测量 v2', '', *LEGEND]
    for name in names:
        loaded = {stage: load(name, stage) for stage in stages}
        overall = [(stage, loaded[stage][0]['overall']) for stage in stages if loaded[stage][0]]
        data['overall'][name] = dict(overall)
        lines += ['', f'## {name}：有组探针合计', '', *measure_table(overall, 'stage', '阶段'),
                  '', f'### {name}：时间区间', '', *time_table(overall)]
        lines += ['', f'### {name}：淡忘（+60 天）干扰记忆在召回排序中的名次', '',
                  '| 阶段 | 名次（空 = 未进入前 20） |', '| --- | --- |']
        for stage in stages:
            m = loaded[stage][0]
            if m and '淡忘' in m['by_category']:
                ranks = m['by_category']['淡忘']['trap_ranks']
                lines.append(f"| {stage} | {', '.join(map(str, ranks)) or '空'} |")
        categories = sorted({cat for stage in stages if loaded[stage][0]
                             for cat in loaded[stage][0]['by_category']})
        data['by_category'][name] = {stage: loaded[stage][0]['by_category'] for stage in stages if loaded[stage][0]}
        for metric, title in (('complete_at_3', '@3'), ('lift_cue', '提升（cue 完全 − 随机记忆）')):
            lines += ['', f'### {name}：各类别 {title}', '',
                      '| 阶段 | ' + ' | '.join(categories) + ' |',
                      '| --- | ' + ' | '.join('---:' for _ in categories) + ' |']
            for stage in stages:
                m = loaded[stage][0]
                if not m:
                    continue
                cells = [_cell(m['by_category'].get(cat, {}).get(metric), 'f') for cat in categories]
                lines.append(f'| {stage} | ' + ' | '.join(cells) + ' |')
        lines += ['', f'### {name}：相邻阶段配对差值（有组探针，95% bootstrap CI，seed 0）', '',
                  '| 对照 | 指标 | n | 差值 | 95% CI |', '| --- | --- | ---: | ---: | --- |']
        for a, b in zip(stages, stages[1:]):
            for metric in PAIRED_METRICS:
                left = probe_values(loaded[a][1], metric)
                right = probe_values(loaded[b][1], metric)
                ids = sorted(set(left) & set(right))
                if not ids:
                    continue
                d = paired_delta([left[i] for i in ids], [right[i] for i in ids])
                data['paired_deltas'].append({'set': name, 'from': a, 'to': b, 'metric': metric, 'n': len(ids), **d})
                lines.append(f"| {a}→{b} | {metric} | {len(ids)} | {d['delta']:.3f} | "
                             f"[{d['ci'][0]:.3f}, {d['ci'][1]:.3f}] |")
    write_json(root / 'comparison_v2.json', data)
    (root / 'comparison_v2.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
