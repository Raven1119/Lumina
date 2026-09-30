"""Append the frozen holdout comparison and final v4 decision without scoring answers."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from judge.account_v4 import count
from judge.report_v4 import answer_examples, decision, render_round

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / 'runs' / 'v4_stage4'
ROUND = ROOT / 'judge' / 'rounds' / 'v4_p4_holdout_B1_vs_final'
JUDGE = ROUND / 'judge_deepseek_v4'
START = datetime.fromisoformat('2026-09-26T18:05:00+00:00')
ALL_START = datetime.fromisoformat('2026-09-26T13:50:00+00:00')


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def render(until: datetime) -> str:
    judged = read(JUDGE / 'summary.json')
    if judged.get('missing') or judged['labels'] != ['B1', 'final']:
        raise ValueError('holdout blind comparison incomplete')
    runs = {'B1': RUNS / 'answer_holdout_B1', 'final': RUNS / 'answer_holdout_final'}
    summaries = {label: read(path / 'summary.json') for label, path in runs.items()}
    configs = {label: read(path / 'config.json') for label, path in runs.items()}
    if any(cfg['set'] != 'holdout_c' or not cfg['answer'] or cfg['answer_scope'] != 'primary'
           for cfg in configs.values()):
        raise ValueError('not a single-campaign holdout answer comparison')
    if configs['B1']['preset'] != 'B1' or configs['final']['preset'] != 'P6':
        raise ValueError('wrong frozen presets')
    if configs['final']['usage_source'] != 'dream' or configs['final']['write_check'] != 'none':
        raise ValueError('final configuration drift')
    usage = count(START, until)
    all_usage = count(ALL_START, until)
    total_calls = sum(row['calls'] for row in all_usage.values())
    total_input = sum(row['input_tokens'] for row in all_usage.values())
    total_output = sum(row['output_tokens'] for row in all_usage.values())
    if total_input > 15_000_000:
        raise ValueError('input cap exceeded; stop and report separately')
    overall = judged['overall']
    wrong_rise = (overall['final']['wrong'] - overall['B1']['wrong']) * 100
    answer_verdict = decision(judged, 7)
    old, new = summaries['B1'], summaries['final']
    cue_improved = new['measure']['overall']['complete_cue'] > old['measure']['overall']['complete_cue']
    protected = all(new['by_category'][cat]['all_complete'] >= old['by_category'][cat]['all_complete']
                    for cat in ('淡忘', '保留'))
    supports_final = answer_verdict != 'final更差' and wrong_rise <= 2 and cue_improved and protected
    lines = [
        '## 阶段 4：冻结配置的保留集验证', '',
        '### 执行与调用', '',
        '阶段 1–3 完成、P6d 冻结后才启用 holdout_c。B1 与 P6d 各运行一次；'
        '每次同一回放同时记录记忆层 suite 与 primary 回答。'
        '随后由 DeepSeek 按盲评包做两遍独立评审，第二遍交换 X/Y。保留集之后没有调配置。',
        '', '| 用途 | 新缓存响应数 | 输入 token | 输出 token |',
        '| --- | ---: | ---: | ---: |',
    ]
    for purpose, row in usage.items():
        lines.append(f'| {purpose} | {row["calls"]} | {row["input_tokens"]} | {row["output_tokens"]} |')
    lines += [
        '', '阶段开始预计最多约 500 次新增调用、约 150 万输入 token；'
        '实际按 UTC 起止时间内新写入缓存的响应计，不能确证未落盘的在途失败请求。',
        '', '### 记忆层对照', '',
        '| 配置 | 可评分主探针 | 淡忘 +60 | 保留 +60 | cue | @1 | @3 | MRR | 强化事件 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for label, summary in summaries.items():
        measure = summary['measure']['overall']
        lines.append(f'| {label} | {measure["n"]} | '
                     f'{round(summary["by_category"]["淡忘"]["all_complete"]*3)}/3 | '
                     f'{round(summary["by_category"]["保留"]["all_complete"]*2)}/2 | '
                     f'{measure["complete_cue"]:.2f} | {measure["complete_at_1"]:.2f} | '
                     f'{measure["complete_at_3"]:.2f} | {measure["mrr"]:.3f} | '
                     f'{summary["recall_strengthening_events"]} |')
    lines += ['', '### 回答层盲评', '', render_round(ROUND, JUDGE, 7),
              answer_examples(ROUND, JUDGE, 15), '### 采纳与偏差', '',
              f'- 按 N=7，回答层判为“{answer_verdict}”；DeepSeek 标记说错率变化 {wrong_rise:+.2f} 个百分点，E=2.0。'
              f'预定记忆目标（cue 改善、淡忘/保留不退步）{"满足" if cue_improved and protected else "未满足"}。'
              f'三项规则对 P6d 相对 B1 的保留集验证{"均满足" if supports_final else "未全部满足"}；'
              '这是冻结配置的外部验证，不产生新的采纳或回退。'
              + ('回答无可测差异；若视为支持 P6d，依据只有记忆层目标指标。' if answer_verdict == '无可测差异' else ''),
              '- 任务卡未单列保留集的记忆目标；在查看保留集结果前选定“cue 改善且 +60 天淡忘/保留不退步”，'
              '以避免事后挑选指标。B1 使用 scripted 仅为开启与回答模式相同的滚动摘要，B1 本身不做强化。',
              '', '## 最终配置、证据边界与下一步', '',
              '- 最终配置：P6d = P6（π_recall=0、`integrate_v1`、`render_v1`、`answer_v2`）'
              '加 Dream 独立“用上”判断；`write_check=none`。阶段 1 采纳 P6d，阶段 2 拒绝写入检查，'
              '阶段 3–4 只验证冻结配置，没有再改动。',
              '- 决策依据：阶段 1 回答无可测差异、说错率在 E 内，淡忘/保留保持且 dev_b 记忆指标改善；'
              '阶段 2 的写入检查使说错率上升超过 E，且日期错误基线为 0 因而无法严格下降。'
              '阶段 3 P6d 相对 B1 记忆 cue 与保留改善，回答净胜 3 未超过 N；'
              f'阶段 4 的保留集规则{"支持" if supports_final else "未支持"} P6d 相对 B1。',
              '- 未解决：竞争性填充延长到 16 周时，摘要关键词仅丢失 6/48（12.5%），'
              '低于 50% 预设门槛；“摘要装不下”的子集只有 6 条，双遍一致胜为 2 比 2，'
              '因此无法据此声称在该条件下有回答收益。'
              '阶段 1 的 dev_a cue 由 .92 降至 .88；阶段 2 的审计日期错误 0→0、写入检查说错率增加 3.57 个百分点。',
              '- 下一步：若继续研究“摘要装不下”，应先另造能稳定达到 ≥50% 关键词丢失的新开发集，'
              '再做一次冻结配置对照；现有 16 周压力集不满足这一门槛。保留集已消费，不用于调参或重跑。',
              '', f'本次可核算新增模型调用总计 {total_calls} 次；输入 {total_input} token，输出 {total_output} token。'
              '所有回答优劣和说错往事标记均来自 DeepSeek 盲评，Codex 未重新给回答打分。',
              '', '离线测试：阶段 4 收尾在 Memory_lab 执行 `python -m pytest tests -q`，84 passed；'
              '`git diff --check` 通过。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--until', required=True, help='exclusive UTC ISO timestamp')
    args = ap.parse_args()
    doc = ROOT / 'docs' / 'RESULTS_v4.md'
    original = doc.read_text(encoding='utf-8')
    marker = '## 阶段 4\n\n保留集结果在阶段 1–3 完成并冻结配置后追加。\n'
    if marker not in original:
        raise ValueError('stage-3 placeholder missing')
    doc.write_text(original.replace(marker, render(datetime.fromisoformat(args.until))), encoding='utf-8')
    print(doc, doc.stat().st_size)
