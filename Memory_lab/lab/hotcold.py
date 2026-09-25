"""Pair-aligned Hot compaction, matching eval_set.build.simulate_hot."""
from __future__ import annotations

from dataclasses import dataclass,field
from datetime import datetime

from memlab.types import Turn


@dataclass
class HotCold:
    hot:list[Turn]=field(default_factory=list)
    summary:str=''
    summary_until:datetime|None=None

    def add(self,turn:Turn)->list[Turn]:
        self.hot.append(turn)
        if turn.role!='assistant' or len(self.hot)<=24:return []
        desired=len(self.hot)-12
        boundary=desired-(desired%2)
        moved=self.hot[:boundary]
        self.hot=self.hot[boundary:]
        self.summary_until=moved[-1].time
        return moved

    def ids(self)->frozenset[str]:
        return frozenset(t.id for t in self.hot)
