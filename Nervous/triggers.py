"""Trusted foreground policies. Watch data never supplies executable predicates."""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TriggerSpec:
    id: str
    version: int
    source: str
    target: str
    kind: str
    priority: int
    recipe: str
    predicate: Callable[[dict], bool]
    reason: str = ''


class Registry:
    def __init__(self, specs):
        self.specs = tuple(specs)
        if len({(s.id, s.version) for s in self.specs}) != len(self.specs):
            raise ValueError('duplicate_trigger_spec')

    def match(self, event):
        return tuple(s for s in self.specs
                     if (s.source, s.target, s.kind) == (event.source, event.target, event.kind)
                     and s.predicate(event.data))


def _object(data):
    from collections.abc import Mapping
    return isinstance(data, Mapping)


DEFAULT_TRIGGERS = tuple(
    TriggerSpec(kind + ':' + source, 1, source, target, kind, priority, recipe, _object)
    for source, target, kind, priority, recipe in (
        ('user', 'mind', 'user.input', 2, 'owner'),
        ('user', 'nervous', 'user.control', 0, 'control'),
        ('user', 'nervous', 'review.schedule', 1, 'owner'),
        ('nervous', 'execution', 'execution.control', 0, 'control'),
        ('nervous', 'mind', 'control.notice', 2, 'control'),
        ('user', 'mind', 'mind.retry', 2, 'owner'),
        ('execution', 'mind', 'execution.changed', 2, 'related'),
        ('execution', 'mind.results', 'execution.snapshot', 1, 'related'),
        ('execution', 'mind.results', 'evidence.result', 1, 'continuation'),
        ('execution', 'mind.results', 'execution.receipt', 1, 'continuation'),
        ('execution', 'mind.results', 'prediction.receipt', 1, 'continuation'),
        ('mind.analysis', 'mind.results', 'analysis.result', 1, 'continuation'),
        ('mind', 'mind.analysis', 'analysis.request', 1, 'continuation'),
        ('mind.results', 'mind.analysis', 'analysis.request', 1, 'continuation'),
        ('nervous', 'mind', 'attention.signal', 2, 'related'),
        ('nervous', 'mind.results', 'view.result', 1, 'continuation'),
        ('nervous', 'execution', 'mind.decision', 1, 'continuation'),
        *((source, 'nervous', kind, 1, 'continuation')
          for source in ('mind', 'mind.results') for kind in ('mind.effects', 'view.read')),
        *((source, 'execution', kind, 1, 'continuation')
          for source in ('mind', 'mind.results')
          for kind in ('execution.inspect', 'evidence.read', 'prediction.watch', 'mind.decision')),
    ))


# Inputs are owner-produced facts about an observed safe boundary. The policy
# does not scan a workspace or infer that a prediction is true/false.
OUTCOME_TRIGGERS = tuple(
    TriggerSpec(identity, 1, 'execution', 'mind', 'execution.outcome', 2, 'related',
                lambda data, key=key: bool(data[key]), reason)
    for identity, key, reason in (
        ('execution.request', 'requests',
         'Execution explicitly requests high-level judgment; its question is an attributed actor judgment.'),
        ('prediction.changed', 'changed_predictions',
         'A declared prediction observation changed. Check its source, action and conditions before comparing.'),
        ('execution.feedback_budget', 'budget_feedback',
         'Execution yielded its remaining allocation for pending feedback; budget exhaustion proves no business conclusion.'),
        ('execution.completion_feedback', 'completion_feedback',
         'Execution proposed completion; outstanding guidance or computation results require business-result feedback.'),
        ('execution.result', 'significant_result',
         'Execution reached a significant result or outside wait; assess current business evidence and remaining conditions.'),
    ))


def execution_reasons(facts):
    return tuple(spec for spec in OUTCOME_TRIGGERS if spec.predicate(facts))
