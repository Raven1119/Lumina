"""Probe variants and deterministic time transforms."""
from __future__ import annotations

from datetime import datetime,timedelta


def transform(at:datetime,anchor:datetime,days:float=0,scale:float=1)->datetime:
    return anchor+(at-anchor)*scale+timedelta(days=days)


def variants(probe,at,gap_sweep):
    result=[(0,at,probe,False)]
    for v in probe.get('gap_variants',[]):
        result.append((v['offset_days'],at+timedelta(days=v['offset_days']),{**probe,**v},True))
    if probe['category'] in ('淡忘','保留'):
        used={days for days,_,_,_ in result}
        alternate=next((p for _,_,p,is_variant in result if is_variant),probe)
        for days in gap_sweep:
            if days not in used:
                result.append((days,at+timedelta(days=days),alternate if days>0 else probe,days>0))
    return result
