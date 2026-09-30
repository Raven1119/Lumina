"""Append portable stage-3 competitive-long evidence after both blind passes."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from judge.account_v4 import count
from judge.aggregate import block, load
from judge.report_v4 import answer_examples, decision, render_round

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / 'runs' / 'v4_stage3'
ROUND = ROOT / 'judge' / 'rounds' / 'v4_p3_long_B1_vs_final'
JUDGE = ROUND / 'judge_deepseek_v4'
START = datetime.fromisoformat('2026-09-26T15:25:00+00:00')


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def render(weeks: int, until: datetime) -> str:
    if weeks not in (8, 12, 16):
        raise ValueError('unsupported long-set duration')
    attempts = {}
    for length in (8, 12, 16):
        if length > weeks:
            break
        attempts[length] = {
            name: read(RUNS / (f'summary_dev_a_8w_retry' if name == 'dev_a' and length == 8
                                else f'summary_{name}_{length}w') / 'summary_loss.json')
            for name in ('dev_a', 'dev_b')
        }
    judged = read(JUDGE / 'summary.json')
    if judged.get('missing'):
        raise ValueError('blind judgment incomplete')
    labels, merged = load(ROUND, JUDGE)
    if labels != ['B1', 'final']:
        raise ValueError('wrong blind labels')
    lost = {
        f'{name}_long{weeks}w_v2': {
            row['probe_id'] for row in attempts[weeks][name]['rows']
            if not row['has_main_words']
        }
        for name in ('dev_a', 'dev_b')
    }
    lost_rows = [row for row in merged.values() if row['probe'] in lost[row['set']]]
    subset = block(labels, lost_rows)
    usage = count(START, until)
    lines = [
        '## 阶段 3：竞争性长程压测', '',
        '### 执行与调用', '',
        f'按原剧本插入点生成竞争性填充，先逐次检查 8/12/16 周的滚动摘要；最终选用 {weeks} 周。'
        '固定配置为已采纳的 P6d（`integrate_v1`、`render_v1`、`answer_v2`、Dream 用上判断、无写入检查）。'
        '在两开发长集上先运行记忆层，再运行主探针回答；两遍 DeepSeek 盲评独立调用，第二遍交换 X/Y。',
        '', '| 用途 | 新缓存响应数 | 输入 token | 输出 token |',
        '| --- | ---: | ---: | ---: |',
    ]
    for purpose, row in usage.items():
        lines.append(f'| {purpose} | {row["calls"]} | {row["input_tokens"]} | {row["output_tokens"]} |')
    lines += [
        '', '阶段开始预计 8 周方案最多约 2,000 次调用、约 500 万输入 token；'
        '因摘要门槛不足，延长尝试的追加预算与实际调用一并计入上表。'
        '统计为阶段起止 UTC 时间内新写入缓存的响应，未落盘的在途失败请求无法确证。',
        '', '### 摘要关键词丢失门槛', '',
        '沿用 v3 词面方法：探针对应植入事实与该时刻滚动摘要共享的内容片段少于两个，记为丢失。'
        '只计可评分主探针；这衡量词面保留，不证明语义遗忘。',
        '', '| 填充 | dev_a 丢失 | dev_b 丢失 | 合计 | 合计比例 | 是否达 50% |',
        '| --- | ---: | ---: | ---: | ---: | --- |',
    ]
    for length, values in attempts.items():
        missing = sum(v['missing'] for v in values.values())
        n = sum(v['n'] for v in values.values())
        lines.append(f'| {length} 周 | {values["dev_a"]["missing"]}/{values["dev_a"]["n"]} | '
                     f'{values["dev_b"]["missing"]}/{values["dev_b"]["n"]} | '
                     f'{missing}/{n} | {missing/n:.1%} | {"是" if missing/n>=.5 else "否"} |')
    if weeks == 16 and sum(v['missing'] for v in attempts[16].values()) / sum(v['n'] for v in attempts[16].values()) < .5:
        lines += ['', '16 周为任务卡上限；虽未达到 50%，仍按 §4.2 继续正式评测，结论只适用于实测的摘要丢失子集。']
    lines += ['', '### 记忆层对照', '',
              '| 集合 | 配置 | 可评分主探针 | 淡忘 +60 | 保留 +60 | cue | @1 | @3 | MRR | 强化事件 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name in ('dev_a', 'dev_b'):
        for label, path in (
            ('B1', RUNS / f'summary_{name}_{weeks}w'),
            ('final', RUNS / f'memory_{name}_final_{weeks}w'),
        ):
            summary = read(path / 'summary.json')
            measure = summary['measure']['overall']
            lines.append(f'| {name} | {label} | {measure["n"]} | '
                         f'{round(summary["by_category"]["淡忘"]["all_complete"]*3)}/3 | '
                         f'{round(summary["by_category"]["保留"]["all_complete"]*2)}/2 | '
                         f'{measure["complete_cue"]:.2f} | {measure["complete_at_1"]:.2f} | '
                         f'{measure["complete_at_3"]:.2f} | {measure["mrr"]:.3f} | '
                         f'{summary["recall_strengthening_events"]} |')
    lines += ['', '### 回答层盲评', '', render_round(ROUND, JUDGE, 7),
              '### 摘要已丢失子集', '',
              '以主探针在摘要中丢失为筛选条件；同一探针的 +60 天变体若存在也纳入该子集。'
              '子集胜负仍要求两遍偏好一致。',
              '', '| 范围 | n | B1 胜 | final 胜 | 平 | 不一致 | 符号检验 p |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name in ('dev_a_long' + str(weeks) + 'w_v2', 'dev_b_long' + str(weeks) + 'w_v2', '合计'):
        rows = lost_rows if name == '合计' else [r for r in lost_rows if r['set'] == name]
        result = block(labels, rows)
        pairs = result['pairs']
        p = result['sign_test_p']
        lines.append(f'| {name} | {result["n"]} | {pairs["B1"]} | {pairs["final"]} | '
                     f'{pairs["tie"]} | {pairs["split"]} | {p:.4g} |' if p is not None else
                     f'| {name} | {result["n"]} | {pairs["B1"]} | {pairs["final"]} | '
                     f'{pairs["tie"]} | {pairs["split"]} | — |')
    lines += ['', answer_examples(ROUND, JUDGE, 15), '### 采纳与偏差', '',
              f'- 相对 B1 的回答层按 N=7 判为“{decision(judged,7)}”；'
              f'模型标记说错率变化 {(judged["overall"]["final"]["wrong"]-judged["overall"]["B1"]["wrong"])*100:+.2f} 个百分点，E=2.0。'
              '两集保留从 0/2 升到 2/2、cue 从 0 升到正值；三项采纳规则对“P6d 相对 B1”均满足，'
              '但本阶段只验证已采纳配置，不产生新的采纳。回答无可测差异，依据只有记忆层目标指标。'
              '摘要丢失子集 2 比 2，不能声称该子集有回答收益。',
              '- dev_b 的 B04/B05 因整体后移后原题相对时间与金标区间不再匹配，被原长集构建器标为不计分；未改原金标。'
              '8/16 周分别后移 56/112 天；12 周采用实际插入长度 84 天以避免多出 28 天空白，'
              '这偏离了任务卡“56 天的倍数”的字面要求。',
              '- DeepSeek 曾把一组会话写成每对 U/L 都带标题；生成器确定性保留首六组交替问答并照旧验证。'
              '12 周生成时发现 dev_a 的首周候选选择会随解析修正变化，故中止该次生成；之后延长集直接逐字复用已落盘的前缀。'
              '中止前已写入缓存的调用仍计入上表。',
              '', '## 阶段 4', '', '保留集结果在阶段 1–3 完成并冻结配置后追加。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--weeks', type=int, required=True)
    ap.add_argument('--until', required=True, help='exclusive UTC ISO timestamp')
    args = ap.parse_args()
    doc = ROOT / 'docs' / 'RESULTS_v4.md'
    original = doc.read_text(encoding='utf-8')
    marker = '## 阶段 3–4\n\n后续阶段的实测结果按阶段完成顺序追加。\n'
    if marker not in original:
        raise ValueError('stage-2 placeholder missing')
    doc.write_text(original.replace(marker, render(args.weeks, datetime.fromisoformat(args.until))), encoding='utf-8')
    print(doc, doc.stat().st_size)
