"""Serial Task contracts and read-only owner views through the event boundary."""
import json

import pytest

from Execution.model import EXECUTION_PROTOCOL
from Execution.ipython_control import IPythonResult
from Execution.runtime import Execution
from Execution.test_runtime import Python, native
from Nervous.organ import Event
from Nervous.provider import ProviderCalls
from Nervous.storage import fingerprint, read_json


AUTHORITY = {'ref': 'source:authorized-research', 'text': 'Research within this workspace and shared budget.',
             'mind_id': 'mind-persistent'}


def contract(identity, goal, acceptance, revision=1):
    return {'id': identity, 'revision': revision, 'intention_id': 'intention-research',
            'intention_revision': 1, 'authority_ref': AUTHORITY['ref'],
            'goal': goal, 'acceptance': acceptance}


def task_owner(task):
    token = 'done:' + fingerprint(task)[:24]
    return {'business_goal': task['goal'] + '\n\nAcceptance:\n' + task['acceptance'],
            'execution_protocol': EXECUTION_PROTOCOL.replace('exact content done', 'exact content ' + token)}


def decision(owner, identity, task, guidance='Apply the accepted task requirements.', snapshot=None):
    snapshot = snapshot or owner.capture()
    return Event(identity, 'mind.results', 'execution', 'mind.decision', {
        'activity_id': 'activity-' + identity, 'task_contract': task, 'task': task_owner(task),
        'snapshot': snapshot, 'reviewed': snapshot['reviewed'],
        'directive': {'id': 'directive-' + identity, 'text': guidance} if guidance else None,
        'owner_input': {'event_id': 'input-' + identity, 'event_type': 'OWNER_EVIDENCE',
                        'text': 'Evidence for ' + task['id']}})


def runtime(tmp_path, answers):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    calls = ProviderCalls(tmp_path / 'private' / 'calls',
        {'calls': 40, 'output_tokens': 1000000, 'request_bytes': 10000000},
        lambda role, wire: next(answers))
    control = Python(workspace)
    owner = Execution(tmp_path / 'private' / 'execution', calls, workspace, control,
                      stage1_authority=AUTHORITY)
    return owner, calls, control


def test_serial_tasks_have_separate_completion_and_receiving_context_after_restart(tmp_path):
    task_a = contract('task-a', 'Inventory inputs.', 'Write the inventory.')
    task_b = contract('task-b', 'Compare formats.', 'Write a comparison.')
    token_a = 'done:' + fingerprint(task_a)[:24]
    answers = iter([native('ipython', {'code': "(workspace / '.lumina-complete').write_text(" + repr(token_a) + ")"}),
                    native('claim_complete', {}), native('claim_complete', {}),
                    native('claim_complete', {})])
    owner, calls, control = runtime(tmp_path, answers)
    try:
        accepted_a = decision(owner, 'start-a', task_a)
        first = owner.handle(accepted_a)
        owner.advance()
        owner.advance()
        owner.handle(decision(owner, 'review-a', task_a, None))
        owner.advance()
        assert owner.status()['status'] == 'completed'
        first_run = owner.status()['execution_ref']
        accepted_b = decision(owner, 'start-b', task_b)
        second = owner.handle(accepted_b)
        assert second[0].data['execution_ref'] != first_run
        assert calls.summary()['calls'] == 3
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.handle(accepted_b) == second
        assert owner.handle(accepted_a) == first
        owner.advance()
        assert owner.status()['status'] != 'completed', 'Task A marker cannot acknowledge Task B.'
        assert owner.actor.state.latest_observation.status == 'rejected'
        wire = calls.records(role='execution')[-1][1]['wire']
        serialized = json.dumps(wire, ensure_ascii=False)
        assert 'Compare formats.' in serialized and 'Write a comparison.' in serialized
        assert AUTHORITY['text'] in serialized, 'Original scope must survive a Task switch.'
        assert AUTHORITY['ref'] in serialized
        assert 'Inventory inputs.' not in serialized
        assert 'Evidence for task-a' not in serialized
        assert calls.summary()['calls'] == 4
        assert len(owner.state['prior_runs']) == 1
        assert owner.state['task_contract'] == task_b
    finally:
        owner.close()


def test_saved_execution_views_are_bounded_readonly_and_do_not_sample_workspace(tmp_path, monkeypatch):
    task = contract('task-read', 'Inspect the supplied input.', 'Report its actual scope.')
    owner, calls, control = runtime(tmp_path, iter([
        native('ipython', {'code': "(workspace / 'result.txt').write_text('observed')"}),
        native('wait', {'event_type': 'INPUT'})]))
    owner.handle(decision(owner, 'start', task))
    owner.advance()
    owner.advance()
    run = owner.status()['execution_ref']
    owner.close()
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in owner.directory.rglob('*') if p.is_file()}
    monkeypatch.setattr(owner.evidence, 'snapshot', lambda *args: pytest.fail('A saved view cannot sample the workspace.'))
    view = owner.view('execution.state')
    assert view['content']['status'] == 'waiting'
    assert view['scope']['task'] == {'id': 'task-read', 'revision': 1}
    assert view['content']['execution_ref'] == run
    page = owner.view('execution.history', execution_ref=run, limit=1)
    assert len(page['content']) == 1 and page['next_offset'] == 1
    assert page['content'][0]['kind'] == 'IPYTHON_EXECUTION_STARTED'
    assert 'result.txt' in page['content'][0]['text']
    assert 'raw_provider_response' not in json.dumps(page)
    assert owner.view('execution.history', execution_ref='execution-unknown')['missing']
    assert calls.summary()['calls'] == 2 and owner.actor is None
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in owner.directory.rglob('*') if p.is_file()}


def test_source_observation_distinguishes_absence_from_observed_text(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([]))
    try:
        missing = owner.source_observation('later.txt')
        assert missing['ref'] is None and missing['missing'] is True
        (owner.workspace / 'later.txt').write_text('observed', encoding='utf-8')
        observed = owner.source_observation('later.txt')
        assert observed['missing'] is False
        assert owner.read_source(observed['ref'])['text'] == 'observed'
        with pytest.raises(ValueError, match='workspace_path_escape'):
            owner.source_observation('../private')
        assert calls.summary()['calls'] == 0 and owner.actor is None
    finally:
        owner.close()


def test_unsettled_task_and_unknown_outcome_cannot_be_replaced(tmp_path):
    task_a = contract('task-a', 'Inspect inputs.', 'Report actual inputs.')
    task_b = contract('task-b', 'Compare inputs.', 'Report comparison.')
    owner, calls, control = runtime(tmp_path, iter([native('ipython', {'code': 'pass'})]))
    try:
        owner.handle(decision(owner, 'start-a', task_a))
        old_run = owner.run_state().execution_id
        assert owner.handle(decision(owner, 'too-soon', task_b))[0].data['status'] == 'task_not_settled'
        control.execute = lambda code: IPythonResult(False, error_code='isolated_kernel_failed')
        owner.advance()
        assert owner.handle(decision(owner, 'unknown', task_b))[0].data['status'] == 'unknown_action'
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.run_state().execution_id == old_run and owner.state['task_contract'] == task_a
        assert Execution.inspect_directory(owner.directory)['recovery']['unknown_action']['reason'] == 'isolated_kernel_failed'
        assert calls.summary()['calls'] == 1
    finally:
        owner.close()


def test_stage1_decision_may_arrive_from_nervous_after_effects_are_applied(tmp_path):
    task = contract('task-a', 'Inspect inputs.', 'Report actual inputs.')
    owner, calls, control = runtime(tmp_path, iter([]))
    try:
        original = decision(owner, 'accepted', task)
        routed = Event(original.event_id, 'nervous', original.target, original.kind, original.data)
        assert owner.handle(routed)[0].data['status'] == 'bound'
        assert calls.summary()['calls'] == 0
    finally:
        owner.close()


@pytest.mark.parametrize('crash', [False, True])
def test_waiting_task_revision_retires_old_contract_durably_without_replaying_wait(tmp_path, monkeypatch, crash):
    original = contract('task-a', 'Inspect inputs.', 'Await the original evidence.')
    revised = contract('task-a', 'Inspect inputs under the new rule.', 'Await the replacement evidence.', revision=2)
    owner, calls, control = runtime(tmp_path, iter([
        native('wait', {'event_type': 'OLD_INPUT'}), native('wait', {'event_type': 'NEW_INPUT'})]))
    try:
        owner.handle(decision(owner, 'start', original))
        owner.advance()
        old_run = owner.run_state().execution_id
        event = decision(owner, 'revise', revised)
        if crash:
            save = owner.save
            def crash_after_transition():
                save()
                if owner.state['initializing']:
                    raise SystemExit('after task transition intent')
            monkeypatch.setattr(owner, 'save', crash_after_transition)
            with pytest.raises(SystemExit):
                owner.handle(event)
            owner.close()
            owner = Execution(owner.directory, calls, ipython=control)
            assert owner.view('execution.state')['content']['execution_ref'] is None
        receipt = owner.handle(event)
        assert receipt[0].data['status'] == 'bound'
        assert owner.run_state().execution_id != old_run
        assert calls.summary()['calls'] == 1
        assert owner.state['prior_runs'][0]['retirement']['reason'] == 'task_revision_replaced_at_wait'
        assert owner.view('execution.state', execution_ref=old_run)['content']['waiting_for'] == 'OLD_INPUT'
        owner.advance()
        assert owner.status()['waiting_for'] == 'NEW_INPUT'
        assert calls.summary()['calls'] == 2
        assert owner.handle(decision(owner, 'late-old', original))[0].data['status'] == 'stale_task'
        assert owner.state['task_contract'] == revised
    finally:
        owner.close()


def test_old_prediction_is_not_rebound_or_reported_under_revised_task(tmp_path):
    task = contract('task-a', 'Inspect inputs.', 'Await evidence.')
    revision = contract('task-a', 'Inspect revised inputs.', 'Await new evidence.', revision=2)
    owner, calls, control = runtime(tmp_path, iter([native('wait', {'event_type': 'INPUT'})]))
    try:
        owner.handle(decision(owner, 'start', task))
        owner.advance()
        owner.handle(Event('watch-old', 'mind.results', 'execution', 'prediction.watch', {
            'activity_id': 'activity-start', 'ref': 'model:' + '1' * 32,
            'task_contract': task, 'observation_file': 'old-observation.txt',
            'before_observation_ref': None, 'check_spec': {'scope': 'Old task only.'}}))
        owner.handle(decision(owner, 'revise', revision))
        (owner.workspace / 'old-observation.txt').write_text('late old observation', encoding='utf-8')
        assert owner.poll() == (), 'An old Task watch cannot create a new Task judgment.'
        assert owner.capture()['reviewed']['predictions'] == {}
        assert owner.state['predictions'][0]['task_binding'] != owner.task_binding()
        late = Event('late-watch', 'mind.results', 'execution', 'prediction.watch', {
            'activity_id': 'old-activity', 'ref': 'model:' + '2' * 32,
            'task_contract': task, 'observation_file': 'other.txt',
            'before_observation_ref': None, 'check_spec': {'scope': 'Old task only.'}})
        assert owner.handle(late)[0].data['status'] == 'stale_task'
        assert len(owner.state['predictions']) == 1
    finally:
        owner.close()


def test_history_body_reference_can_expand_only_original_approved_event(tmp_path):
    task = contract('task-a', 'Inspect inputs.', 'Record observed scope.')
    code = 'observed = ' + repr('x' * 3600)
    owner, calls, control = runtime(tmp_path, iter([native('ipython', {'code': code})]))
    try:
        owner.handle(decision(owner, 'start', task))
        owner.advance()
        run = owner.run_state().execution_id
        page = owner.view('execution.history', limit=1)
        event = page['content'][0]
        assert event['truncated']
        head = owner.view('execution.history', execution_ref=run, event_ref=event['ref'], limit=2000)
        tail = owner.view('execution.history', execution_ref=run, event_ref=event['ref'],
                          offset=head['next_offset'], limit=6000)
        original = json.loads(head['content']['text'] + tail['content']['text'])
        assert code in json.dumps(original)
        assert tail['next_offset'] is None
        assert owner.view('execution.history', event_ref='not-an-approved-event')['missing']
        assert calls.summary()['calls'] == 1
    finally:
        owner.close()


def test_typed_control_blocks_admission_and_actions_across_restart(tmp_path):
    task = contract('task-a', 'Inspect inputs.', 'Record observed scope.')
    owner, calls, control = runtime(tmp_path, iter([native('ipython', {
        'code': "(workspace / 'result.txt').write_text('observed')"})]))
    try:
        owner.handle(decision(owner, 'start', task))
        stop = Event('stop', 'nervous', 'execution', 'execution.control',
                     {'action': 'stop', 'control_ref': 'user-stop'})
        assert owner.handle(stop) == ()
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.handle(stop) == () and not owner.advance()
        assert calls.summary()['calls'] == 0
        assert not (owner.workspace / 'result.txt').exists()
        assert owner.handle(decision(owner, 'while-stopped', task))[0].data['status'] == 'control_blocked'
        assert Execution.inspect_directory(owner.directory)['control']['action'] == 'stop'
        owner.handle(Event('resume', 'nervous', 'execution', 'execution.control',
                     {'action': 'resume', 'control_ref': 'user-resume'}))
        assert owner.advance()
        assert (owner.workspace / 'result.txt').read_text() == 'observed'
        owner.handle(Event('revoke', 'nervous', 'execution', 'execution.control',
                     {'action': 'revoke', 'control_ref': 'user-revoke'}))
        owner.handle(Event('resume-after-revoke', 'nervous', 'execution', 'execution.control',
                     {'action': 'resume', 'control_ref': 'user-resume-after-revoke'}))
        assert not owner.advance() and owner.status()['control']['action'] == 'revoke'
        assert calls.summary()['calls'] == 1
    finally:
        owner.close()


def test_empty_stage1_review_neither_creates_task_nor_consumes_execution_calls(tmp_path):
    owner, calls, control = runtime(tmp_path, iter([]))
    try:
        snapshot = owner.capture()
        event = Event('empty-review', 'nervous', 'execution', 'mind.decision', {
            'activity_id': 'idle-review', 'task': None, 'task_contract': None,
            'snapshot': snapshot, 'reviewed': snapshot['reviewed'], 'directive': None, 'owner_input': None})
        result = owner.handle(event)
        assert result[0].data['status'] == 'accepted'
        assert owner.actor is None and owner.state['task_contract'] is None
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.handle(event) == result and not owner.advance()
        assert calls.summary()['calls'] == 0
    finally:
        owner.close()


def test_task_authority_and_revision_cannot_be_silently_rewritten(tmp_path):
    task = contract('task-a', 'Inspect inputs.', 'Record observed scope.')
    owner, calls, control = runtime(tmp_path, iter([]))
    try:
        owner.handle(decision(owner, 'start', task))
        changed = {**task, 'goal': 'Overwrite the old goal without a new version.'}
        with pytest.raises(ValueError, match='execution_task_version_conflict'):
            owner.handle(decision(owner, 'rewrite', changed))
        foreign = {**contract('task-b', 'Inspect elsewhere.', 'Change permission.'), 'authority_ref': 'invented'}
        with pytest.raises(ValueError, match='execution_task_authority_conflict'):
            owner.handle(decision(owner, 'foreign', foreign))
        assert owner.state['task_contract'] == task and calls.summary()['calls'] == 0
    finally:
        owner.close()


def test_first_task_stale_snapshot_preserves_reassessment_without_an_actor(tmp_path):
    task = contract('task-a', 'Inspect inputs.', 'Record observed scope.')
    owner, calls, control = runtime(tmp_path, iter([]))
    try:
        event = decision(owner, 'first-proposal', task)
        (owner.workspace / 'late.txt').write_text('New observation after the review.', encoding='utf-8')
        receipt = owner.handle(event)
        assert receipt[0].data['status'] == 'superseded'
        pending = owner.poll()
        assert len(pending) == 1
        assert pending[0].data['origin_activity_id'] == 'activity-first-proposal'
        assert pending[0].causation_id == event.event_id
        assert pending[0].data['snapshot']['files'][0]['file'] == 'late.txt'
        assert owner.actor is None and owner.state['task_contract'] is None
        assert owner.state['task_contracts'] == []
        owner.close()
        owner = Execution(owner.directory, calls, ipython=control)
        assert owner.handle(event) == receipt and owner.poll() == pending
        owner.published(pending[0].event_id)
        assert owner.poll() == ()
        assert calls.summary()['calls'] == 0
    finally:
        owner.close()
