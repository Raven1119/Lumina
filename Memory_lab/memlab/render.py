"""Human-facing, bounded four-section memory text."""
from __future__ import annotations

from datetime import timedelta

from .clock import WEEKDAYS,logical_day,period,rough_age


def memory_time_label(memory,now):
    occurrences=memory['occurrences']
    if not occurrences:return '时间不详'
    first,last=min(occurrences),max(occurrences)
    if len({logical_day(t) for t in occurrences})==1:
        return f"{first.month} 月 {first.day} 日 周{WEEKDAYS[first.weekday()]} {period(first)}，{rough_age((now-last).total_seconds()/86400)}"
    if sum(t>=now-timedelta(days=14) for t in occurrences)>=3:return '最近常提'
    return f"首次那段时间起多次，最近一次 {rough_age((now-last).total_seconds()/86400)}"


def render_memory_block(result,now,cfg)->str:
    sections=[]
    for title,items in [('此刻想起的',result.near),('顺带想到的',result.remote),('最近心上的事',result.core)]:
        if items:sections.append('【'+title+'】\n'+'\n'.join(f'- （{m.time_label}）{m.text}' for m in items))
    if result.raw:
        lines=[]
        for hit in result.raw:
            at=hit.time
            lines.append(f"- {at.month} 月 {at.day} 日 周{WEEKDAYS[at.weekday()]} {at:%H:%M} {'他' if hit.role=='user' else '我'}：{hit.text}")
        sections.append('【最近两周的原话】\n'+'\n'.join(lines))
    return '\n'.join(sections) if sections else '（此刻没有想起什么）'
