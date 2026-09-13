"""Repetition facts obey real action, Task, completion and restart boundaries."""
import json

import pytest

from Execution.ipython_control import IPythonResult
from Execution.runtime import Execution
from Execution.test_repetition import build
from Execution.test_runtime import Python, decision, native
from Execution.test_stage1 import AUTHORITY, contract, decision as task_decision
from Nervous.organ import Event
from Nervous.provider import ProviderCalls
from Nervous.storage import canonical, fingerprint


def owner_with_answers(root, answers, *, stage1=False):
    workspace = root / 'workspace'
    workspace.mkdir()
    iterator = iter(answers)
    calls = ProviderCalls(root / 'private' / 'calls',
        {'calls': 40, 'output_tokens': 1000000, 'request_bytes': 10000000},
        lambda role, wire: next(iterator))
    control = Python(workspace)
    owner = Execution(root / 'private' / 'execution', calls, workspace, control,
                      repetition_mode='mind', **({'stage1_authority': AUTHORITY} if stage1 else {}))
    return owner, calls, control


def acknowledge(owner, event, identity):
    owner.published(event.event_id)
    return owner.handle(decision(owner, identity, None, snapshot=event.document()['data']['snapshot']))


def test_source_a_b_a_needs_fresh_actions_and_never_revives_old_evidence(tmp_path):
    owner, calls, control = owner_with_answers(tmp_path, [
        native('ipython', {'code': 'pass'}, str(i)) for i in range(16)])
    seen = []
    try:
        source = owner.workspace / 'measurement.txt'
        source.write_text('A', encoding='utf-8')
        owner.handle(decision(owner, 'start', 'Check the current measured observation.'))
        for index, value in enumerate(('A', 'B', 'A')):
            source.write_text(value, encoding='utf-8')
            assert owner.poll() == ()
            for count in range(1, 4):
                assert owner.advance()
                events = owner.poll()
                if count < 3:
                    assert events == ()
            event, = events
            fact = event.document()['data']['snapshot']['repetition_observation']
            assert fact['count'] == 3
            seen.append(fact)
            acknowledge(owner, event, 'review-' + str(index))
            assert owner.poll() == ()
            owner.close()
            owner = Execution(owner.directory, calls, ipython=control)
            assert owner.poll() == ()
        assert len({fact['id'] for fact in seen}) == 3
        assert len({fact['execution_ref'] for fact in seen}) == 1
        assert seen[0]['source_versions_ref'] == seen[2]['source_versions_ref']
        assert seen[0]['source_versions_ref'] != seen[1]['source_versions_ref']
        # Two observations without an action reset the occurrence boundary even
        # when the final file content returns to the last acknowledged version.
        source.write_text('B', encoding='utf-8')
        assert owner.poll() == ()
        source.write_text('A', encoding='utf-8')
        assert owner.poll() == ()
        assert 'repetition_observation' not in owner.capture()
        for count in range(1, 4):
            assert owner.advance()
            events = owner.poll()
            if count < 3:
                assert events == ()
        event, = events
        assert event.data['snapshot']['repetition_observation']['id'] not in {fact['id'] for fact in seen}
    finally:
        owner.close()


def test_explicit_environment_read_persists_scope_boundary_before_restart(tmp_path):
    from Nervous.views import read_views
    owner, calls, control = owner_with_answers(tmp_path, [
        native('ipython', {'code': 'pass'}, str(i)) for i in range(6)])
    try:
        source = owner.workspace / 'measurement.txt'
        source.write_text('A', encoding='utf-8')
        owner.handle(decision(owner, 'start', 'Check the current measured observation.'))
        for _ in range(3):
            owner.advance()
        first, = owner.poll()
        first_fact = first.document()['data']['snapshot']['repetition_observation']
        acknowledge(owner, first, 'first-reviewed')
        source.write_text('B', encoding='utf-8')
        read = read_views(['view:execution.environment'], mind=None, execution=owner,
                          attention_view=lambda: {})
        result = json.loads(read['observation']['text'])['sources'][0]
        refreshed = json.loads(result['text'])['content']
        assert owner.read_source(refreshed['files'][0]['ref'])['text'] == 'B'
        assert 'repetition_observation' not in refreshed
        # The owner returned an actual B observation. A crash before the next
        # ordinary poll must not lose this already exposed scope transition.
        owner.close()
        source.write_text('A', encoding='utf-8')
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == ()
        for count in range(1, 4):
            owner.advance()
            events = owner.poll()
            if count < 3:
                assert events == ()
        repeated, = events
        next_fact = repeated.document()['data']['snapshot']['repetition_observation']
        assert next_fact['id'] != first_fact['id'] and next_fact['count'] == 3
        assert next_fact['source_versions_ref'] == first_fact['source_versions_ref']
    finally:
        owner.close()


def test_real_wait_does_not_create_repetition_and_restart_stays_quiet(tmp_path):
    owner, calls, control = owner_with_answers(tmp_path, [
        *(native('ipython', {'code': 'pass'}, str(i)) for i in range(3)),
        native('wait', {'event_type': 'INPUT_AVAILABLE'})])
    try:
        owner.handle(decision(owner, 'start', 'Await the missing input after checking current evidence.'))
        for _ in range(3):
            owner.advance()
        repeated, = owner.poll()
        acknowledge(owner, repeated, 'repetition-reviewed')
        owner.advance()
        assert owner.run_state().status == 'waiting'
        assert 'repetition_observation' not in owner.capture()
        assert owner.poll() == ()  # The unchanged baseline result was already reviewed.
        before = calls.summary()['calls']
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == () and not owner.advance()
        assert calls.summary()['calls'] == before
    finally:
        owner.close()


def test_deferred_original_completion_settles_before_any_repetition_review(tmp_path):
    owner, calls, control = owner_with_answers(tmp_path, [
        *(native('ipython', {'code': 'pass'}, str(i)) for i in range(3)),
        native('claim_complete', {})])
    try:
        (owner.workspace / '.lumina-complete').write_text('done', encoding='utf-8')
        owner.handle(decision(owner, 'start', 'Complete only after checking the supplied acceptance.'))
        for _ in range(4):
            assert owner.advance()
        assert owner.actor.completion_review_pending()
        event, = owner.poll()
        assert 'proposed completion' in event.data['reason']
        assert 'repetition_observation' not in event.data['snapshot']
        acknowledge(owner, event, 'completion-reviewed')
        before = calls.summary()['calls']
        assert owner.advance()
        assert owner.run_state().status == 'completed'
        assert calls.summary()['calls'] == before
        assert owner.poll() == ()
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == () and not owner.advance()
        assert calls.summary()['calls'] == before
    finally:
        owner.close()


def test_new_task_and_run_cannot_reuse_a_completed_tasks_repetition(tmp_path):
    task_a = contract('task-a', 'Assess source coverage.', 'Preserve the observed scope.')
    task_b = contract('task-b', 'Assess source consistency.', 'Preserve unresolved differences.')
    owner, calls, control = owner_with_answers(tmp_path, [
        *(native('ipython', {'code': 'pass'}, str(i)) for i in range(3)),
        native('claim_complete', {}),
        *(native('ipython', {'code': 'pass'}, str(i + 4)) for i in range(3))], stage1=True)
    try:
        (owner.workspace / '.lumina-complete').write_text('done:' + fingerprint(task_a)[:24], encoding='utf-8')
        owner.handle(task_decision(owner, 'start-a', task_a))
        for _ in range(3):
            owner.advance()
        event_a, = owner.poll()
        fact_a = event_a.document()['data']['snapshot']['repetition_observation']
        owner.published(event_a.event_id)
        owner.handle(task_decision(owner, 'review-a', task_a, None))
        owner.advance()
        assert owner.run_state().status == 'completed'
        # Settle the ordinary completed-result event before switching Tasks.
        for event in owner.poll():
            owner.published(event.event_id)
            owner.handle(task_decision(owner, 'settled-a', task_a, None))
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == () and not owner.advance()
        owner.handle(task_decision(owner, 'start-b', task_b))
        assert owner.poll() == ()
        assert 'repetition_observation' not in owner.capture()
        for count in range(1, 4):
            owner.advance()
            events = owner.poll()
            if count < 3:
                assert events == ()
        event_b, = events
        fact_b = event_b.document()['data']['snapshot']['repetition_observation']
        assert fact_b['count'] == 3
        assert fact_b['execution_ref'] != fact_a['execution_ref']
        assert fact_b['task_binding'] != fact_a['task_binding']
        assert fact_b['id'] != fact_a['id']
        records_a = json.loads(owner.read_source(fact_a['records_ref'])['text'])['records']
        records_b = json.loads(owner.read_source(fact_b['records_ref'])['text'])['records']
        assert set((fact_b['execution_ref'], record['action_ref']) for record in records_b).isdisjoint(
            (fact_a['execution_ref'], record['action_ref']) for record in records_a)
    finally:
        owner.close()


@pytest.mark.parametrize('failure', ['unknown_result', 'unfinished'])
def test_unknown_or_unfinished_action_blocks_repetition_and_replay_after_restart(tmp_path, failure):
    owner, calls, control = build(tmp_path)
    try:
        for _ in range(2):
            owner.advance()
        if failure == 'unknown_result':
            control.execute = lambda code: IPythonResult(False, error_code='isolated_kernel_failed')
            owner.advance()
        else:
            def interrupt(code):
                raise SystemExit('action started without a received result')
            control.execute = interrupt
            with pytest.raises(SystemExit):
                owner.advance()
        before = calls.summary()['calls']
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == ()
        assert not owner.advance()
        assert owner.state['unknown_action']
        assert 'repetition_observation' not in owner.capture()
        assert calls.summary()['calls'] == before
    finally:
        owner.close()


def test_three_equal_truncated_prefixes_do_not_certify_equal_full_results(tmp_path):
    owner, calls, control = build(tmp_path)
    try:
        control.execute = lambda code: IPythonResult(True, output='common prefix', truncated=True,
                                                     original_output_chars=20000)
        for _ in range(3):
            owner.advance()
            assert owner.poll() == ()
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == ()
        assert 'repetition_observation' not in owner.capture()
    finally:
        owner.close()


@pytest.mark.parametrize('output', [chr(0x6C49) * 10000, '"' * 10000], ids=['unicode', 'escaped'])
def test_full_result_sample_does_not_overflow_the_repetition_event(tmp_path, output):
    from Nervous.organ import MAX_EVENT_BYTES
    owner, calls, control = build(tmp_path)
    try:
        control.execute = lambda code: IPythonResult(True, output=output, original_output_chars=len(output))
        for _ in range(3):
            owner.advance()
        event, = owner.poll()
        assert len(canonical(event.document()).encode('utf-8')) <= MAX_EVENT_BYTES
        fact = event.document()['data']['snapshot']['repetition_observation']
        sample = json.loads(owner.read_source(fact['sample_ref'])['text'])
        assert sample['result']['output'] == output
        assert sample['result']['truncated'] is False
    finally:
        owner.close()


def test_pursuit_repetition_fact_fits_existing_mind_evidence_bound(tmp_path):
    from Mind.cognition import MAX_EVIDENCE_CHARS
    task = contract('task-sized', 'Assess the current sources.', 'Preserve scope and uncertainty.')
    owner, calls, control = owner_with_answers(tmp_path, [
        native('ipython', {'code': 'pass'}, str(i)) for i in range(3)], stage1=True)
    try:
        owner.handle(task_decision(owner, 's' * 39, task))
        owner.handle(task_decision(owner, 'r' * 39, task, None))
        for _ in range(3):
            owner.advance()
        event, = owner.poll()
        fact = event.document()['data']['snapshot']['repetition_observation']
        assert len(fact['task_binding']) == 64 and len(fact['review_activity']) == 48
        assert len(canonical(fact)) <= MAX_EVIDENCE_CHARS
        for key in ('records_ref', 'sample_ref', 'source_versions_ref'):
            assert owner.read_source(fact[key])['ref'] == fact[key]
    finally:
        owner.close()


@pytest.mark.parametrize('action', ['stop', 'revoke'])
def test_owner_control_blocks_repetition_review_and_further_actions_after_restart(tmp_path, action):
    task = contract('task-controlled', 'Assess the current sources.', 'Preserve scope and uncertainty.')
    owner, calls, control = owner_with_answers(tmp_path, [
        native('ipython', {'code': 'pass'}, str(i)) for i in range(3)], stage1=True)
    try:
        owner.handle(task_decision(owner, 'start-controlled', task))
        for _ in range(3):
            owner.advance()
        owner.handle(Event('control-' + action, 'nervous', 'execution', 'execution.control',
                           {'action': action, 'control_ref': 'owner-control:' + action}))
        before = calls.summary()['calls']
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.poll() == () and not owner.advance()
        assert 'repetition_observation' not in owner.capture()
        assert calls.summary()['calls'] == before
        assert owner.state['control']['action'] == action
    finally:
        owner.close()
