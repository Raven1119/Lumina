"""Exact observed repetitions, not a semantic no-progress classifier."""
import json

import pytest

from Execution.ipython_control import IPythonResult
from Execution.runtime import Execution
from Execution.test_runtime import decision, native, runtime


def build(tmp_path, mode='mind', codes=None):
    codes = codes or ['pass'] * 10
    owner, calls, control = runtime(tmp_path, iter(
        native('ipython', {'code': code}, str(i)) for i, code in enumerate(codes)))
    directory, workspace = owner.directory, owner.workspace
    owner.close()
    # Start the opt-in owner before creating an Actor; baseline state is immutable.
    owner.path.unlink()
    owner = Execution(directory, calls, workspace, control, repetition_mode=mode)
    owner.handle(decision(owner, 'start', 'Preserve the authorized input and deliver the checked artifact.'))
    return owner, calls, control


@pytest.mark.parametrize('mode,events', [('off', 0), ('execution', 0), ('mind', 1)])
def test_threshold_actual_wire_and_nochange_restart(tmp_path, mode, events):
    owner, calls, control = build(tmp_path, mode)
    try:
        for _ in range(2):
            assert owner.advance()
            assert owner.poll() == ()
        assert owner.advance()
        emitted = owner.poll()
        assert len(emitted) == events
        if emitted:
            evidence = emitted[0].data['snapshot']['repetition_observation']
            assert evidence['count'] == 3
            assert len(json.loads(owner.read_source(evidence['records_ref'])['text'])['records']) == 3
            assert owner.poll() == emitted
            owner.close()
            owner = Execution(owner.directory, calls, ipython=control)
            assert owner.poll() == emitted  # Durable outbox before publisher acknowledgement.
            owner.published(emitted[0].event_id)
            owner.handle(decision(owner, 'nochange', None, snapshot=emitted[0].document()['data']['snapshot']))
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == ()
        assert owner.advance()
        wire = calls.records(role='execution')[-1][1]['wire']
        projected = json.loads(wire['messages'][-1]['content']).get('repetition_observation')
        assert bool(projected) == (mode != 'off')
        if projected:
            assert projected['count'] == 3
            assert 'not proof' in projected['scope']
        assert owner.poll() == ()  # Another identical result and NoChange do not rearm.
        assert len(owner.state['deliveries']) == 1
    finally:
        owner.close()


@pytest.mark.parametrize('kind', ['new_result', 'truncated', 'new_scope', 'unfinished', 'different_action'])
def test_ineligible_or_changing_observations_do_not_trigger(tmp_path, kind):
    owner, calls, control = build(tmp_path, codes=['pass', 'pass', 'x = 1' if kind == 'different_action' else 'pass'])
    try:
        for _ in range(2):
            owner.advance()
            assert owner.poll() == ()
        if kind == 'new_result':
            control.execute = lambda code: IPythonResult(True, output='a new result')
        elif kind == 'truncated':
            control.execute = lambda code: IPythonResult(True, output='same prefix', truncated=True, original_output_chars=20000)
        elif kind == 'new_scope':
            (owner.workspace / 'measurement.txt').write_text('a new measured source', encoding='utf-8')
        elif kind == 'unfinished':
            def interrupted(code):
                raise SystemExit('long action has no observed result')
            control.execute = interrupted
        if kind == 'unfinished':
            with pytest.raises(SystemExit):
                owner.advance()
        else:
            owner.advance()
        assert owner.poll() == ()
    finally:
        owner.close()


def test_new_directive_does_not_erase_observation_before_execution_sees_it(tmp_path):
    owner, calls, control = build(tmp_path)
    try:
        for _ in range(3):
            owner.advance()
            emitted = owner.poll()
        event, = emitted
        fact = event.document()['data']['snapshot']['repetition_observation']
        owner.published(event.event_id)
        owner.handle(decision(owner, 'reassessed', 'Complete the authorized index with its source attribution.',
            snapshot=event.document()['data']['snapshot']))
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == ()
        assert owner.advance()
        wire = calls.records(role='execution')[-1][1]['wire']
        received = json.loads(wire['messages'][-1]['content'])['repetition_observation']
        assert {k: v for k, v in received.items() if k != 'review_activity'} == {
            k: v for k, v in fact.items() if k != 'review_activity'}
        assert any(m['content'].endswith('Complete the authorized index with its source attribution.')
                   for m in wire['messages'] if isinstance(m['content'], str))
        assert owner.poll() == ()  # Advice/receipt is not progress and does not rearm.
    finally:
        owner.close()
