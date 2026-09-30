"""Append phase-1 evidence and mechanical adoption decision to RESULTS_v4.md."""
from __future__ import annotations

import json
import random
import statistics
from datetime import datetime
from pathlib import Path

from judge.account_v4 import count
from judge.report_v4 import answer_examples, decision, render_round

ROOT=Path(__file__).resolve().parents[1]
RUNS=ROOT/'runs'/'v4_stage1'
ROUND=ROOT/'judge'/'rounds'/'v4_p1_P6u_vs_P6d'
JUDGE=ROUND/'judge_deepseek_v4'


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def render():
    judged=json.loads((JUDGE/'summary.json').read_text(encoding='utf-8'))
    if judged.get('missing'):raise ValueError('incomplete blind judging')
    N=7;E=2.0
    a,b=judged['labels'];o=judged['overall']
    wrong_rise=(o[b]['wrong']-o[a]['wrong'])*100
    usage=count(datetime.fromisoformat('2026-09-26T14:30:00+00:00'))
    reports={}
    windows={}
    probes={}
    for set_name in ('dev_a','dev_b'):
        baseline=ROOT/'runs'/'v3_stage1'/f'answer_{set_name}_P6u'
        trial=RUNS/f'answer_{set_name}_P6d_fulltrace'
        reports[(set_name,'P6u')]=json.loads((baseline/'summary.json').read_text(encoding='utf-8'))
        reports[(set_name,'P6d')]=json.loads((trial/'summary.json').read_text(encoding='utf-8'))
        windows[set_name]=rows(trial/'usage_log.jsonl')
        probes[(set_name,'P6u')]=rows(baseline/'probes.jsonl')
        probes[(set_name,'P6d')]=rows(trial/'probes.jsonl')
    target=all(reports[(s,'P6d')]['by_category'][cat]['all_complete']==1.0
               for s in ('dev_a','dev_b') for cat in ('淡忘','保留'))
    total_context=sum(w['context_count'] for values in windows.values() for w in values)
    total_used=sum(w['used_count'] for values in windows.values() for w in values)
    selective=0<total_used<total_context
    memory_improvement=any(reports[(s,'P6d')]['measure']['overall'][metric]>
                           reports[(s,'P6u')]['measure']['overall'][metric]
                           for s in ('dev_a','dev_b') for metric in ('complete_cue','complete_at_1','complete_at_3','mrr'))
    adopted=(decision(judged,N)!='P6d更差' and wrong_rise<=E and target and selective and memory_improvement)
    decision_file=RUNS/'decision_v4.json'
    decision_file.write_text(json.dumps({'adopted':adopted,'answer':decision(judged,N),
                                         'wrong_rise_pp':wrong_rise,'target_preserved':target,
                                         'usage_selective':selective,'memory_improvement_somewhere':memory_improvement},
                                        ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['## 阶段 1：由 Dream 判断“用上”', '',
           '### 执行与调用', '',
           '保留 `integrate_v1`、`render_v1`、`answer_v2`、π 参数和 0.5 强化权重。每一轮用户消息记录近联想、远联想、心上之事 ID 及缓存影子回复；第一次 Dream 前也记录空记忆痕迹。每次 Dream 整合后另作一次 `purpose=usage` 的 DeepSeek 判断，非法 ID 丢弃并记日志，再只对同轮用上的 ID 按原 6 小时去重强化、建立共同召回边。两集各运行一次完整留痕的 P6d 记忆与主探针回答。',
           '', '| 用途 | 新缓存响应数 | 输入 token | 输出 token |',
           '| --- | ---: | ---: | ---: |']
    for purpose,row in usage.items():
        lines.append(f'| {purpose} | {row["calls"]} | {row["input_tokens"]} | {row["output_tokens"]} |')
    lines += ['', '阶段开始时预计两集新增最多约 700 次调用、150 万输入 token；实际按 UTC 14:30 起新写入缓存的响应统计，包括初始留痕修正前的两套运行及修正后复跑，旧 Dream/summary/部分 shadow 命中缓存。失败但未写缓存的在途调用不能确证。',
              '', '### 记忆层对照', '',
              '| 集合 | 配置 | 淡忘 +60 | 保留 +60 | cue | @1 | @3 | MRR | 强化事件 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for set_name in ('dev_a','dev_b'):
        for label in ('P6u','P6d'):
            r=reports[(set_name,label)];m=r['measure']['overall']
            lines.append(f'| {set_name} | {label} | '
                         f'{round(r["by_category"]["淡忘"]["all_complete"]*3)}/3 | '
                         f'{round(r["by_category"]["保留"]["all_complete"]*2)}/2 | '
                         f'{m["complete_cue"]:.2f} | {m["complete_at_1"]:.2f} | '
                         f'{m["complete_at_3"]:.2f} | {m["mrr"]:.3f} | '
                         f'{r["recall_strengthening_events"]} |')
    lines += ['', 'dev_a 的 cue 从 .92 降到 .88、MRR 从 .750 降到 .749；dev_b 的 @1 从 .60 升到 .64、MRR 从 .695 升到 .731。淡忘与保留两集均保持满分。',
              '', '### 每个 Dream 窗口的用上判断', '',
              '| 集合 | 窗口 | 用户轮次 | 入上下文记忆次数 | 判为用上次数 | 比例 | 非法 ID 数 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for set_name,values in windows.items():
        for row in values:
            c=row['context_count'];u=row['used_count']
            lines.append(f'| {set_name} | {row["dream_id"]} | {row["turns"]} | {c} | {u} | '
                         f'{u/c:.2%}' if c else f'| {set_name} | {row["dream_id"]} | {row["turns"]} | {c} | {u} | —',)
            lines[-1]+=f' | {len(row["invalid"])} |'
    lines += ['', f'合计 {total_used}/{total_context} = {total_used/total_context:.2%} 的上下文记忆出现次数被判为实际用上；两个集合分别为 '
              + '、'.join(f'{s} {sum(w["used_count"] for w in windows[s])}/{sum(w["context_count"] for w in windows[s])}' for s in ('dev_a','dev_b'))
              + '。这表明判断器有选择性，并不是使用准确率的证明。初始窗口无可选记忆，比例记为“—”。',
              '', '### 未沉睡记忆 π 分布', '',
              '单元格是每个探针先算未沉睡记忆中的比例，再在相同时间偏移的探针之间取平均；+60/+120 只涵盖有该变体的淡忘与保留探针。',
              '', '| 集合 | 配置 | 偏移 | 探针数 | 平均未沉睡数 | π>0.9 | π<0.1 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for set_name in ('dev_a','dev_b'):
        for label in ('P6u','P6d'):
            for offset in (0,60,120):
                chosen=[r['pi_profile'] for r in probes[(set_name,label)] if r['variant_days']==offset]
                high=[r['gt_0_9'] for r in chosen if r['gt_0_9'] is not None]
                low=[r['lt_0_1'] for r in chosen if r['lt_0_1'] is not None]
                lines.append(f'| {set_name} | {label} | +{offset} | {len(chosen)} | '
                             f'{statistics.mean(r["awake"] for r in chosen):.1f} | '
                             f'{statistics.mean(high):.2f}' if high else f'| {set_name} | {label} | +{offset} | {len(chosen)} | — | —')
                lines[-1]+=f' | {statistics.mean(low):.2f} |' if low else ' | — |'
    invalid=[(s,w['dream_id'],item) for s,values in windows.items() for w in values for item in w['invalid']]
    lines += ['', f'非法 ID/轮次条目共 {len(invalid)} 个，均丢弃：', '']
    lines += [f'- {s} {wid}：`{json.dumps(item,ensure_ascii=False)}`' for s,wid,item in invalid] or ['- 无。']
    lines += ['', '### 20 个随机抽样判断例子', '',
              '固定随机种子 4；为让少数“用上”案例也可检查，每个集合分别从已判用上与未判用上的候选记忆中各随机抽 5 个。这里转录模型判断，不由 Codex 重打分。', '']
    rng=random.Random(4)
    for set_name in ('dev_a','dev_b'):
        shadow=rows(RUNS/f'answer_{set_name}_P6d_fulltrace'/'shadow_log.jsonl')
        pairs=[]
        for turn in shadow:
            for memory in turn['memories']:
                pairs.append((turn,memory,memory['id'] in turn['used']))
        for flag in (True,False):
            group=[p for p in pairs if p[2]==flag]
            if len(group)<5:raise ValueError(f'{set_name} insufficient examples flag={flag}')
            for turn,memory,used in rng.sample(group,5):
                lines.append(f'- {set_name} {turn["turn_id"]} / {memory["id"]}，判断：{"用上" if used else "未用上"}。记忆：{memory["text"]}；回复：{turn["answer"]}')
    lines += ['', '### 回答层盲评', '', render_round(ROUND,JUDGE,N),answer_examples(ROUND,JUDGE,15),
              '### 采纳与偏差', '',
              f'- 按噪声带 N={N}，回答层为“{decision(judged,N)}”；模型标记的说错率新旧差为 {wrong_rise:+.2f} 个百分点，E={E:.1f} 个百分点。',
              f'- 目标：淡忘/保留两集保持 3/3、2/2；用上次数 {total_used}/{total_context}，有选择性；dev_b @1 和 MRR 改善，dev_a cue 小幅回退。按三项规则，**{"采纳 P6d" if adopted else "不采纳 P6d，保留 P6u"}**。'
              + (' 回答无可测差异；采纳依据只有记忆层和目标指标。' if adopted and decision(judged,N)=='无可测差异' else ''),
              '- 任务卡没有给“合理区间”的数值阈值；这里采用非零且显著少于入上下文总数的描述性口径，并展示 20 个可核对例子。该比例不是用上判断的准确率。',
              '- DeepSeek 在 B04 的“说错往事”理由中同时写了“前天时间正确”和“时间有误”；按协议原样保留模型标记，没有由 Codex 改分。这提示单条理由仍需谨慎解读。',
              '- 首次回放遗漏了第一次 Dream 前的空记忆痕迹，随后修正并重跑；两次完整回放的主探针回答文本、渲染与记忆层分数逐条相同，调用统计包含两次运行的新增调用。',
              '', '## 阶段 2–4', '', '后续阶段的实测结果按阶段完成顺序追加。', '']
    return '\n'.join(lines),adopted


if __name__=='__main__':
    doc=ROOT/'docs'/'RESULTS_v4.md'
    original=doc.read_text(encoding='utf-8')
    marker='## 阶段 1–4\n\n后续阶段的实测结果按阶段完成顺序追加。\n'
    if marker not in original:raise ValueError('stage-0 placeholder missing')
    section,adopted=render()
    doc.write_text(original.replace(marker,section),encoding='utf-8')
    print(doc,doc.stat().st_size,'adopted',adopted)
