"""Scripted real-owner repetition delivery, evidence read and NoChange recovery."""
from collections import Counter
from contextlib import contextmanager, ExitStack
import json

import pytest

from Execution.model import EXECUTION_PROTOCOL
from Execution.runtime import Execution
from Execution.test_runtime import Python, native
from Mind.organ import MindOrgan
from Mind.test_core_loop import source_records
from Mind.test_organ_events import final
from Nervous.organ import NervousOrgan


GOAL = 'Inspect the authorized measurements and preserve their actual scope.'


@contextmanager
def chain(root, workspace, transport, *, first=False):
    with ExitStack() as stack:
        nervous = stack.enter_context(NervousOrgan(root / 'nervous', transport=transport,
            limits={'calls': 16, 'output_tokens': 300000, 'request_bytes': 3000000}))
        initial = nervous.initialize(goal=GOAL if first else None,
            workspace=workspace if first else None, repetition_mode='mind' if first else None)
        mind = MindOrgan(root / 'mind', nervous.calls, goal=initial['goal'], execution_protocol=EXECUTION_PROTOCOL)
        stack.callback(mind.close)
        execution = Execution(root / 'execution', nervous.calls, workspace=workspace, ipython=Python(workspace),
                              repetition_mode=initial['repetition_mode'])
        stack.callback(execution.close)
        yield nervous, mind, execution


def repetition_record(wire):
    for record in source_records(wire):
        try:
            value = json.loads(record['text'])
        except ValueError:
            continue
        if isinstance(value, dict) and value.get('version') == 'execution-repetition-2':
            return value
    return None


@pytest.mark.parametrize('crash_before_ack', [False, True])
def test_same_mind_reads_exact_sample_then_nochange_retains_execution_evidence(tmp_path, monkeypatch, crash_before_ack):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    root = tmp_path / 'state'
    counts, delivered, sample_reads = Counter(), [], []
    actual_execution_facts = []

    def transport(role, wire):
        counts[role] += 1
        if role == 'execution':
            number = counts[role]
            if number == 4:
                fact = json.loads(wire['messages'][-1]['content'])['repetition_observation']
                actual_execution_facts.append(fact)
                expected = {key: value for key, value in delivered[0].items() if key != 'review_activity'}
                assert {key: value for key, value in fact.items() if key != 'review_activity'} == expected
            assert number <= 5
            return native('ipython', {'code': 'pass'}, str(number)) if number < 5 else native('wait', {'event_type': 'MORE_DATA'})
        assert role == 'mind'
        if counts[role] == 1:
            return final(next={'type': 'directive', 'text': 'Inspect the measurements while preserving their scope.'})
        if counts[role] == 2:
            fact = repetition_record(wire)
            assert fact is not None and fact['count'] == 3
            assert fact['records_ref'].startswith('source:') and fact['sample_ref'].startswith('source:')
            delivered.append(fact)
            return native('read_evidence', {'refs': [fact['sample_ref']]}, 'read-sample')
        if counts[role] == 3:
            source = next(record for record in source_records(wire) if record['ref'] == delivered[0]['sample_ref'])
            sample = json.loads(source['text'])
            assert sample['code'] == 'pass'
            assert sample['result']['ok'] is True and sample['result']['truncated'] is False
            sample_reads.append(sample)
            return final()
        pytest.fail('NoChange must not create another review of unchanged evidence.')

    interrupted = False
    complete = NervousOrgan.complete

    def cut(self, event_id, target, *, emitted=()):
        nonlocal interrupted
        if (crash_before_ack and not interrupted and counts['mind'] == 3
                and any(event.kind == 'mind.decision' for event in emitted)):
            assert target == 'mind.results'  # The remote sample read resumed this activity.
            interrupted = True
            raise SystemExit('Mind accepted NoChange before Nervous acknowledgement')
        return complete(self, event_id, target, emitted=emitted)

    monkeypatch.setattr(NervousOrgan, 'complete', cut)
    with chain(root, workspace, transport, first=True) as (nervous, mind, execution):
        if crash_before_ack:
            with pytest.raises(SystemExit):
                nervous.run(mind, execution)
            assert counts == {'mind': 3, 'execution': 3}
            assert mind.status()['active'] is None
        else:
            nervous.run(mind, execution)
    with chain(root, workspace, transport) as (nervous, mind, execution):
        result = nervous.run(mind, execution)
        assert result['execution']['status'] == 'waiting'
        assert not any(result['pending'].values()) and not result['mind']['unresolved']
        assert counts == {'mind': 3, 'execution': 5}
        assert len(sample_reads) == len(actual_execution_facts) == 1
        changes = [event for event in nervous._load()['events'] if event['kind'] == 'execution.changed']
        repetitions = [event for event in changes if event['data']['snapshot'].get('repetition_observation')]
        assert len(repetitions) == 1
        assert repetitions[0]['source'] == 'execution'
        assert repetitions[0]['data']['snapshot']['repetition_observation'] == delivered[0]
        assert len([event for event in nervous._load()['events'] if event['kind'] == 'user.input']) == 1
        before = dict(counts)
        nervous.run(mind, execution)
        assert counts == before
