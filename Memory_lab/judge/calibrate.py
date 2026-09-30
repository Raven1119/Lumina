"""Compare cached API judging with the existing blinded human-agent rounds."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

try:
    from .aggregate import load, main
except ImportError:
    from aggregate import load, main


def rank(values):
    order=sorted(range(len(values)),key=lambda i:values[i])
    result=[0.0]*len(values)
    for lo in range(len(values)):
        if lo and values[order[lo]]==values[order[lo-1]]:continue
        hi=lo
        while hi+1<len(values) and values[order[hi+1]]==values[order[lo]]:hi+=1
        for at in range(lo,hi+1):result[order[at]]=(lo+hi)/2+1
    return result


def spearman(x,y):
    if len(x)<2:return None
    a,b=rank(x),rank(y)
    am=sum(a)/len(a);bm=sum(b)/len(b)
    num=sum((v-am)*(w-bm) for v,w in zip(a,b))
    den=(sum((v-am)**2 for v in a)*sum((w-bm)**2 for w in b))**.5
    return num/den if den else None


def winner(rows,labels):
    counts=Counter(r['prefer'][0] for r in rows.values() if r['prefer'][0]==r['prefer'][1])
    if counts[labels[0]]==counts[labels[1]]:return 'tie'
    return max(labels,key=lambda label:counts[label])


def compare(round_dir):
    labels,claude=load(round_dir)
    _,deepseek=load(round_dir,round_dir/'judge_deepseek')
    common=sorted(set(claude)&set(deepseek))
    jointly=[];agree=0;wrong_agree=0;wrong_total=0;alive_c=[];alive_d=[];disagreements=Counter()
    for key in common:
        c,d=claude[key],deepseek[key]
        cp=c['prefer'];dp=d['prefer']
        if cp[0]==cp[1] and dp[0]==dp[1] and cp[0]!='tie' and dp[0]!='tie':
            jointly.append(key)
            agree+=cp[0]==dp[0]
            if cp[0]!=dp[0]:disagreements[c['category']]+=1
        for label in labels:
            for a,b in zip(c['judgments'][label],d['judgments'][label]):
                wrong_agree+=a['wrong']==b['wrong'];wrong_total+=1
            alive_c.append(sum(j['alive'] for j in c['judgments'][label])/2)
            alive_d.append(sum(j['alive'] for j in d['judgments'][label])/2)
    return {'round':round_dir.name,'common':len(common),'total':len(claude),
            'claude_winner':winner(claude,labels),'deepseek_winner':winner(deepseek,labels),
            'joint_non_tie':len(jointly),'preference_agree':agree,
            'wrong_agree':wrong_agree,'wrong_total':wrong_total,
            'alive_spearman':spearman(alive_c,alive_d),
            'disagreements':disagreements}


def main_report(root):
    paths=[root/'v1_B1_vs_P6',*sorted(root.glob('v2_*'))]
    rows=[]
    for path in paths:
        if not path.is_dir():continue
        main(path,path/'judge_deepseek')
        rows.append(compare(path))
    directions=sum(r['claude_winner']==r['deepseek_winner'] for r in rows)
    agree=sum(r['preference_agree'] for r in rows)
    joint=sum(r['joint_non_tie'] for r in rows)
    usable=directions>=4 and joint>0 and agree/joint>=.7
    lines=['# DeepSeek 自动评审校准','',
           '比较已有 Claude 盲评与 DeepSeek API 盲评。方向为两次交换顺序后的一致胜方；'
           '条目一致率仅计算双方都给出一致、非平局结论的条目。','',
           '| 轮次 | 完整条目 | Claude 胜方 | DeepSeek 胜方 | 方向一致 | 条目一致 | 说错标记一致 | 活人感 Spearman |',
           '| --- | ---: | --- | --- | --- | ---: | ---: | ---: |']
    for r in rows:
        sp='—' if r['alive_spearman'] is None else f"{r['alive_spearman']:.3f}"
        lines.append(f"| {r['round']} | {r['common']}/{r['total']} | {r['claude_winner']} | {r['deepseek_winner']} | {'是' if r['claude_winner']==r['deepseek_winner'] else '否'} | {r['preference_agree']}/{r['joint_non_tie']} | {r['wrong_agree']}/{r['wrong_total']} | {sp} |")
    lines+=['',f'方向一致 {directions}/{len(rows)}；条目一致 {agree}/{joint} = {agree/joint:.1%}。',
            f"判定：{'可用' if usable else '有偏差；后续结论待复核'}。",'']
    if not usable:
        counts=sum((r['disagreements'] for r in rows),Counter())
        lines+=['## 偏好分歧类别','',*(f'- {category}: {n}' for category,n in counts.most_common()),'']
    (root.parent/'CALIBRATION.md').write_text('\n'.join(lines),encoding='utf-8')
    print(root.parent/'CALIBRATION.md')
    return rows,usable


if __name__=='__main__':
    main_report(Path(__file__).resolve().parent/'rounds')
