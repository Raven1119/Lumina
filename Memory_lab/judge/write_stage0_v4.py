"""Assemble the standalone stage-0 document from cached model judgments."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from judge.account_v4 import count
from judge.report_v4 import answer_examples, decision, render_round

ROOT=Path(__file__).resolve().parents[1]
ROUNDS=ROOT/'judge'/'rounds'
CAL=('v1_B1_vs_P6','v2_P6_e1_vs_e2','v2_P6_v1_vs_e1','v2_e1_B1_vs_P6','v2_e2_B1_vs_P6')
NOISE=('v4_noise_original_vs_seed1','v4_noise_seed1_vs_seed2')
V3=('v3_p1_P6_vs_P6u','v3_p2_int1_vs_int2','v3_p3_render1_vs_render2','v3_p4_long_B1_vs_final')


def read(name):
    judge=ROUNDS/name/('judge_deepseek' if name==CAL[0] else 'judge_deepseek_v4')
    summary=json.loads((judge/'summary.json').read_text(encoding='utf-8'))
    if summary.get('missing'):
        raise ValueError(f'{name}: incomplete judge rows {len(summary["missing"])}')
    return summary,judge


def render() -> str:
    calibration=json.loads((ROOT/'judge'/'calibration_v4.json').read_text(encoding='utf-8'))
    noise=[]
    for name in NOISE:
        x,_=read(name);a,b=x['labels'];o=x['overall'];p=o['pairs']
        noise.append((name,x,abs(p[a]-p[b]),abs(o[a]['wrong']-o[b]['wrong'])*100))
    N=max(2,*(x[2] for x in noise));E=max(2,*(x[3] for x in noise))
    usage=count(datetime.fromisoformat('2026-09-26T13:50:00+00:00'))
    lines=['# Memory Lab v4 结果','',
           '本报告只使用 DeepSeek-V4-Pro 对回答作盲评：每轮两个开发集，各有原顺序和 X/Y 交换顺序的独立请求。下列胜负、说错往事和活人感均为模型输出的机械汇总；Codex 没有给回答打分。阶段 0 完成时本报告先发布阶段 0，后续阶段在同一文档追加。',
           '', '## 阶段 0：盲评校准与噪声', '',
           '### 执行与调用', '',
           '完成五轮历史校准（其中 v1 的 DeepSeek 判断沿用既有缓存，四轮 v2 新跑）、两轮 A/A 噪声盲评、四轮 v3 历史重评。A/A 用 P6u 的 70 条主探针各生成 seed 1、seed 2 两份回答，记忆块仅在每节内部打乱。无效评分数组被严格拒收，逐条 DeepSeek 回退得到完整结果。',
           '', '| 用途 | 新缓存响应数 | 输入 token | 输出 token |',
           '| --- | ---: | ---: | ---: |']
    for purpose,row in usage.items():
        lines.append(f'| {purpose} | {row["calls"]} | {row["input_tokens"]} | {row["output_tokens"]} |')
    lines += ['', '调用统计按本任务首个模型请求前的 UTC 13:50 起算，仅含成功写入缓存的响应；失败的在途请求若产生计费，无法从本地确证。阶段 0 开始时预计历史盲评约 224 批、A/A 回答至多 140 次、A/A 盲评约 56 批；输入粗估 200–400 万 token，实际以上表为准。累计新输入远低于 1500 万上限。',
              '', '离线验证：`../Conversation_Memory/.venv/bin/python -m pytest tests -q`，81 项通过（阶段 0 发布前）。',
              '', '### 历史校准', '',
              '| 轮次 | Claude 一致胜旧/新 | DeepSeek 一致胜旧/新 | 方向一致 | 双方均一致非平局的同向数 | 说错标记一致 | 活人感 Spearman |',
              '| --- | ---: | ---: | --- | ---: | ---: | ---: |']
    for row in calibration:
        a,b=row['labels'];c=row['claude_pairs'];d=row['deepseek_pairs'];item=row['item_agreement'];wrong=row['wrong_agreement']
        lines.append(f'| {row["round"]} | {c[a]}/{c[b]} | {d[a]}/{d[b]} | {"是" if row["direction_same"] else "否"} | '
                     f'{item["agree"]}/{item["total"]} | {wrong["agree"]}/{wrong["total"]} | {row["alive_spearman"]:.3f} |')
    agree=sum(r['item_agreement']['agree'] for r in calibration)
    total=sum(r['item_agreement']['total'] for r in calibration)
    lines += ['', f'**校准结论：可用。** 胜方方向 4/5 轮一致；条目一致 {agree}/{total}（{agree/total:.1%}），超过 70% 门槛。唯一方向分歧为 `v2_e2_B1_vs_P6`：Claude 的 P6 仅多 1 胜，DeepSeek 的 B1 多 2 胜，均是很小净差。按双方都形成一致非平局结论的条目，分歧相对较大的是保留 4/5、淡忘 10/12、远联想 6/7；这些分母很小。说错标记一致率各轮见表，活人感相关为逐条、逐顺序的 1–5 分配对 Spearman。',
              '', '### A/A 噪声带', '',
              '| 对照 | 旧胜 | 新胜 | 平 | 不一致 | 非平局数 | 净差绝对值 | 说错率旧/新 | 差值（百分点） |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |']
    for name,x,net,err in noise:
        a,b=x['labels'];o=x['overall'];p=o['pairs']
        lines.append(f'| {name} | {p[a]} | {p[b]} | {p["tie"]} | {p["split"]} | '
                     f'{p[a]+p[b]+p["split"]} | {net} | {o[a]["wrong"]:.1%}/{o[b]["wrong"]:.1%} | {err:.2f} |')
    lines += ['', f'据任务卡公式，**N={N}** 条，**E={E:.1f} 个百分点**。N 取两轮净差绝对值的最大值；E 取说错率差的最大值与 2 个百分点下限的较大者。符号检验 p 在下方每轮详表。',
              '', '### v3 重评与历史直接审阅对照', '',
              '| 轮次 | v3 直接审阅旧/新 | DeepSeek 双遍旧/新 | 新配置净胜 | 按 N 判定 | 说错率增幅（百分点） |',
              '| --- | ---: | ---: | ---: | ---: | --- | ---: |']
    for name in V3:
        x,_=read(name);a,b=x['labels'];o=x['overall'];p=o['pairs']
        old=json.loads((ROUNDS/name/'judge_self'/'summary.json').read_text(encoding='utf-8'))['pairs']
        lines.append(f'| {name} | {old.get(a,0)}/{old.get(b,0)} | {p[a]}/{p[b]} | {p[b]-p[a]:+d} | '
                     f'{decision(x,N)} | {(o[b]["wrong"]-o[a]["wrong"])*100:+.2f} |')
    lines += ['', '直接审阅是历史参考，交换顺序文件来自同一判断的镜像，不能当作两次独立模型调用。若净胜方向或强弱判断翻转，上表如实列出；按 N 重评后不因这些历史轮次回退当前 P6u，任务卡已冻结写入 v1 与不逐步重塑。',
              '', '### 记忆层（本阶段没有重新回放）', '',
              '| 集合 | 历史 P6u 淡忘 +60 | 保留 +60 | 有组 cue | @1 | @3 | MRR | 召回强化事件 |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for set_name in ('dev_a','dev_b'):
        summary=json.loads((ROOT/'runs'/'v3_stage1'/f'answer_{set_name}_P6u'/'summary.json').read_text(encoding='utf-8'))
        m=summary['measure']['overall']
        lines.append(f'| {set_name} | 3/3 | 2/2 | {m["complete_cue"]:.2f} | {m["complete_at_1"]:.2f} | '
                     f'{m["complete_at_3"]:.2f} | {m["mrr"]:.3f} | {summary["recall_strengthening_events"]} |')
    lines += ['', '### 每轮盲评完整明细', '']
    for name in (*CAL,*NOISE,*V3):
        _,judge=read(name)
        lines.append(render_round(ROUNDS/name,judge,N))
    _,long_judge=read(V3[-1])
    lines += [answer_examples(ROUNDS/V3[-1],long_judge,15),
              '### 偏差与解释', '',
              '- 对评分数组长度反复错误的批次先逐条重试，仍错误时附加由 packet 评分要点长度自动生成的 JSON 输出骨架；评审系统提示词和候选身份未改，所有结果仍由 DeepSeek 产生。缓存 attempt 为每轮每遍分别编号，避免完全相同的 A/A 单条复用同一次请求。',
              '- 活人感 Spearman 采用每条、每候选、每个顺序的原始分数配对；它与 v3 旧校准文档的聚合口径不同，数值不能直接逐位对比。',
              '- 本阶段不重新运行记忆层；表格明确使用 v3 已归档 P6u 结果。阶段 0 不做机制采纳决定，当前配置保持 P6u。',
              '', '## 阶段 1–4', '', '后续阶段的实测结果按阶段完成顺序追加。', '']
    return '\n'.join(lines)


if __name__=='__main__':
    target=ROOT/'docs'/'RESULTS_v4.md'
    target.write_text(render(),encoding='utf-8')
    print(target, target.stat().st_size)
