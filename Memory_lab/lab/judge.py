"""Answer judgement contract. Selection is deliberately left disabled in v1."""
from __future__ import annotations

from typing import Protocol


class Judge(Protocol):
    def evaluate(self,answer:str,rubric:dict)->dict: ...
    def compare(self,left:str,right:str,rubric:dict)->dict: ...


class NoJudge:
    def evaluate(self,answer:str,rubric:dict)->dict:
        return {'status':'not_judged','should':None,'should_not':None}

    def compare(self,left:str,right:str,rubric:dict)->dict:
        return {'status':'not_judged','ab':None,'ba':None,'winner':None}
