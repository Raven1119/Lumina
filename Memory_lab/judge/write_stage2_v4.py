"""Append phase-2 repair and independent DeepSeek audit evidence."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from judge.account_v4 import count
from judge.report_v4 import answer_examples,decision,render_round

ROOT=Path(__file__).resolve().parents[1]
BEFORE=ROOT/'runs'/'v4_stage1'
AFTER=ROOT/'runs'/'v4_stage2'
ROUND=ROOT/'judge'/'rounds'/'v4_p2_nocheck_vs_check'
JUDGE=ROUND/'judge_deepseek_v4'
CATS=('fabricated','speaker_reversed','overgeneralized','date_error','person_confusion')


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def jsonl(path):return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def render():
    judged=read(JUDGE/'summary.json')
    if judged.get('missing'):raise ValueError('incomplete blind judgment')
    a,b=judged['labels'];o=judged['overall']
    answer=decision(judged,7)
    wrong_rise=(o[b]['wrong']-o[a]['wrong'])*100
    runs={}
    audit={}
    for set_name in ('dev_a','dev_b'):
        runs[(set_name,'nocheck')]=BEFORE/f'answer_{set_name}_P6d_fulltrace'
        runs[(set_name,'check')]=AFTER/f'answer_{set_name}_P6d_check'
        for label in ('nocheck','check'):
            audit[(set_name,label)]=read(runs[(set_name,label)]/'audit_v4.json')
    totals={label:{cat:sum(audit[(s,label)]['counts'][cat] for s in ('dev_a','dev_b'))
                   for cat in CATS} for label in ('nocheck','check')}
    ns={label:sum(audit[(s,label)]['n'] for s in ('dev_a','dev_b')) for label in ('nocheck','check')}
    rates={label:{cat:totals[label][cat]/ns[label] for cat in CATS} for label in ('nocheck','check')}
    goals=all(rates['check'][cat]<rates['nocheck'][cat] for cat in ('overgeneralized','date_error'))
    other_nonrise=all(rates['check'][cat]<=rates['nocheck'][cat] for cat in ('fabricated','speaker_reversed','person_confusion'))
    adopted=(answer!='check更差' and wrong_rise<=2 and goals and other_nonrise)
    (AFTER/'decision_v4.json').write_text(json.dumps({'adopted':adopted,'answer':answer,
        'wrong_rise_pp':wrong_rise,'target_both_strictly_down':goals,'other_three_nonrise':other_nonrise,
        'rates':rates},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    usage=count(datetime.fromisoformat('2026-09-26T14:57:00+00:00'))
    lines=['## 阶段 2：程序写入校验与一次修复', '',
           '### 执行与调用', '',
           '在已采纳的 P6d 上仅新增两类程序检查：单一逻辑日的习惯化措辞、指定相对日期表达。每个 Dream 对本次新建或修改的记忆检查一次；同一 Dream 的所有问题合并为一次 `purpose=repair` DeepSeek 调用。候选改写重新校验，仍有问题则保持原正文；成功改写在 lineage 写 `kind=repair`，不追加强化事件。整合提示词与调用保持 `integrate_v1`。审计使用 v3 五类证据口径，由 DeepSeek 独立判断前后最终记忆。',
           '', '| 用途 | 新缓存响应数 | 输入 token | 输出 token |',
           '| --- | ---: | ---: | ---: |']
    for purpose,row in usage.items():
        lines.append(f'| {purpose} | {row["calls"]} | {row["input_tokens"]} | {row["output_tokens"]} |')
    lines += ['', '阶段开始预计最多约 700 次新调用、约 160 万输入 token；实际是 UTC 14:57 后新写入缓存的响应，包含校验前审计中的格式重试与校验后运行。无法由缓存确证的在途失败请求不计入。',
              '', '### 记忆层对照', '',
              '| 集合 | 配置 | 淡忘 +60 | 保留 +60 | cue | @1 | @3 | MRR | 强化事件 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for set_name in ('dev_a','dev_b'):
        for label in ('nocheck','check'):
            summary=read(runs[(set_name,label)]/'summary.json');m=summary['measure']['overall']
            lines.append(f'| {set_name} | {label} | '
                         f'{round(summary["by_category"]["淡忘"]["all_complete"]*3)}/3 | '
                         f'{round(summary["by_category"]["保留"]["all_complete"]*2)}/2 | '
                         f'{m["complete_cue"]:.2f} | {m["complete_at_1"]:.2f} | '
                         f'{m["complete_at_3"]:.2f} | {m["mrr"]:.3f} | '
                         f'{summary["recall_strengthening_events"]} |')
    lines += ['', '### DeepSeek 五类写入审计', '',
              '| 集合 | 配置 | 最终记忆 n | 编造 | 说话人弄反 | 概括过度 | 日期错误 | 人物混淆 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for set_name in ('dev_a','dev_b'):
        for label in ('nocheck','check'):
            result=audit[(set_name,label)]
            vals=' | '.join(f'{result["counts"][cat]}/{result["n"]} ({result["rates"][cat]:.1%})' for cat in CATS)
            lines.append(f'| {set_name} | {label} | {result["n"]} | {vals} |')
    for label in ('nocheck','check'):
        vals=' | '.join(f'{totals[label][cat]}/{ns[label]} ({rates[label][cat]:.1%})' for cat in CATS)
        lines.append(f'| 合计 | {label} | {ns[label]} | {vals} |')
    lines += ['', '### 所有程序校验命中的记忆', '']
    nflagged=nrepaired=0
    for set_name in ('dev_a','dev_b'):
        flags=jsonl(runs[(set_name,'check')]/'repair_log.jsonl')
        lines += [f'**{set_name}：{len(flags)} 条命中**', '']
        for row in flags:
            nflagged+=1
            repaired=row['status']=='repaired'
            nrepaired+=repaired
            after=row['after'] if repaired else row['before']
            lines += [f'- {row["id"]}，{"已修复" if repaired else "未修复、保留原版"}；原因：{"；".join(row["reasons"])}',
                      f'  - 修复前：{row["before"]}',f'  - 最终正文：{after}']
            if not repaired and row.get('proposed'):
                lines.append(f'  - 未采纳候选：{row["proposed"]}')
    lines += ['', f'程序共命中 {nflagged} 条次，成功修复 {nrepaired} 条次；同一记忆在不同 Dream 被检查时按条次列出。',
              '', '### 审计标记的全部问题记忆及理由', '']
    for set_name in ('dev_a','dev_b'):
        for label in ('nocheck','check'):
            result=audit[(set_name,label)]
            evidence={r['id']:r['text'] for r in read(runs[(set_name,label)]/'audit_evidence_v4.json')}
            flagged=[r for r in result['rows'] if any(r['flags'].values())]
            lines += [f'**{set_name} / {label}：{len(flagged)} 条审计标记**', '']
            for row in flagged:
                reasons='；'.join(f'{cat}：{row["reasons"][cat]}' for cat in CATS if row['flags'][cat])
                lines.append(f'- {row["id"]}：{evidence[row["id"]]}。{reasons}')
            if not flagged:lines.append('- 无。')
    lines += ['', '### 回答层盲评', '',render_round(ROUND,JUDGE,7),answer_examples(ROUND,JUDGE,15),
              '### 采纳与偏差', '',
              f'- 回答层：{answer}（N=7）；说错率增幅 {wrong_rise:+.2f} 个百分点，E=2.0 个百分点。',
              f'- 程序目标按两集最终记忆合计比例解释，要求概括过度和日期错误都严格下降，另三类不上升；前者 {rates["nocheck"]["overgeneralized"]:.1%}→{rates["check"]["overgeneralized"]:.1%}，后者 {rates["nocheck"]["date_error"]:.1%}→{rates["check"]["date_error"]:.1%}。按任务卡三项采纳规则，**{"采纳校验修复" if adopted else "不采纳校验修复，最终配置保持 P6d"}**。',
              '- 既有 `audit_prompt_v1.md` 尾句称由 Codex 直接审阅，与本次主人指令冲突；模型审计保留其五类定义和分母口径，运行时去掉这句旧执行说明，提示词文件本身未修改。',
              '- 程序检查按字面匹配“常”等词；任务卡未定义逻辑日时区，本实验沿用回放带时区时间的本地日期。未采纳的修复候选不写入记忆图。',
              '', '## 阶段 3–4', '', '后续阶段的实测结果按阶段完成顺序追加。', '']
    return '\n'.join(lines),adopted


if __name__=='__main__':
    doc=ROOT/'docs'/'RESULTS_v4.md';original=doc.read_text(encoding='utf-8')
    marker='## 阶段 2–4\n\n后续阶段的实测结果按阶段完成顺序追加。\n'
    if marker not in original:raise ValueError('stage-1 placeholder missing')
    section,adopted=render()
    doc.write_text(original.replace(marker,section),encoding='utf-8')
    print(doc,doc.stat().st_size,'adopted',adopted)
