"""Owner-local working context, with real storage and no live provider calls."""
import json
import subprocess
import sys

import pytest

from Nervous.provider import BudgetPause, ProviderCalls
from Nervous.storage import read_json
from working_context import compact, estimate_request_tokens, retry_failed_compaction, should_auto_compact


LIMITS = {'calls': 10, 'output_tokens': 30000, 'request_bytes': 1000000}


def segments(count):
    return [{'ref': f'decision-{index}', 'content': {'result': f'known-{index}'}}
            for index in range(count)]


def summarize(role, wire):
    payload = json.loads(wire['messages'][0]['content'])
    refs = [item['ref'] for item in payload['segments']]
    return {'content': [{'type': 'text', 'text': json.dumps({
        'summary': f'Known results through {refs[-1]}; conditional failures remain conditional.',
        'source_refs': refs})}], 'stop_reason': 'end_turn',
        'usage': {'input_tokens': 500, 'output_tokens': 40}}


def test_three_compactions_keep_tail_and_restart_does_not_repeat(tmp_path):
    path = tmp_path / 'handoff.json'
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, summarize)
    for revision, count in enumerate((4, 6, 8), 1):
        history = segments(count)
        original = json.loads(json.dumps(history))
        state = compact(path, calls, role='execution', scope='run-1',
                        segments=history, instructions='Keep exact action outcomes.',
                        retain=2, trigger=4)
        assert state['revision'] == revision
        assert state['source_refs'] == [f'decision-{i}' for i in range(count - 2)]
        assert state['covered_until'] == f'decision-{count - 3}'
        assert history == original
        reopened = ProviderCalls(tmp_path / 'calls', LIMITS,
                                 lambda *_: (_ for _ in ()).throw(AssertionError('resampled')))
        assert compact(path, reopened, role='execution', scope='run-1',
                       segments=history, instructions='Keep exact action outcomes.',
                       retain=2, trigger=4) == state
    assert calls.summary()['calls'] == 3
    assert all(record['purpose'] == 'compaction' for _, record in calls.records())


def test_saved_summary_response_survives_process_exit_and_new_events(tmp_path):
    script = '''
import json, os, sys
from pathlib import Path
import working_context
from Nervous.provider import ProviderCalls
from Nervous.storage import write_json
root = Path(sys.argv[1])
def crash_before_owner_commit(path, value):
    if value['pending'] is None:
        os._exit(29)
    write_json(path, value)
working_context.write_json = crash_before_owner_commit
calls = ProviderCalls(root / 'calls', {'calls': 1, 'output_tokens': 2048, 'request_bytes': 10000},
    lambda *_: {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({
        'summary': 'Condition p applies only to candidate A.', 'source_refs': ['r0']})}]})
working_context.compact(root / 'summary.json', calls, role='mind', scope='goal-1',
    segments=[{'ref': f'r{i}', 'content': f'result-{i}'} for i in range(4)],
    instructions='Preserve the condition.', retain=2, trigger=4, max_output_tokens=2048)
'''
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 29, result.stderr
    assert read_json(tmp_path / 'summary.json')['accepted'] is None
    calls = ProviderCalls(tmp_path / 'calls', {'calls': 1, 'output_tokens': 2048,
                          'request_bytes': 10000}, lambda *_: pytest.fail('known response resampled'))
    state = compact(tmp_path / 'summary.json', calls, role='mind', scope='goal-1',
                    segments=[{'ref': f'r{i}', 'content': f'result-{i}'} for i in range(5)],
                    instructions='A newer instruction cannot rewrite the saved request.',
                    retain=2, trigger=4, max_summary_chars=1)
    assert state['summary'] == 'Condition p applies only to candidate A.'
    assert state['source_refs'] == ['r0', 'r1']
    assert calls.summary()['calls'] == 1
    assert read_json(tmp_path / 'summary.json')['pending'] is None


@pytest.mark.parametrize('count', [0, 1, 2, 3])
def test_no_prefix_or_threshold_does_not_admit_a_call(tmp_path, count):
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: pytest.fail('unnecessary summary'))
    path = tmp_path / 'summary.json'
    assert compact(path, calls, role='execution', scope='run-1', segments=segments(count),
                   instructions='', retain=2, trigger=4) is None
    assert not path.exists()
    assert calls.summary()['calls'] == 0


@pytest.mark.parametrize('reply', [
    {'summary': '', 'source_refs': ['decision-0']},
    {'summary': 'unknown source', 'source_refs': ['different-run']},
    {'summary': 'uncited claim', 'source_refs': []},
    {'summary': 'wrong type', 'source_refs': 'decision-0'},
])
def test_invalid_summary_retains_accepted_projection_without_resampling(tmp_path, reply):
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, summarize)
    path = tmp_path / 'summary.json'
    original = compact(path, calls, role='execution', scope='run-1', segments=segments(4),
                       instructions='', retain=2, trigger=4)
    calls.transport = lambda *_: {'content': [{'type': 'text', 'text': json.dumps(reply)}]}
    with pytest.raises(BudgetPause, match='working_context_summary_invalid'):
        compact(path, calls, role='execution', scope='run-1', segments=segments(6),
                instructions='', retain=2, trigger=4, max_summary_chars=100)
    assert read_json(path)['accepted'] == original
    calls.transport = lambda *_: pytest.fail('same invalid result resampled')
    with pytest.raises(BudgetPause, match='working_context_summary_invalid'):
        compact(path, calls, role='execution', scope='run-1', segments=segments(6),
                instructions='', retain=2, trigger=4)
    assert calls.summary()['calls'] == 2


def test_unknown_summary_call_stays_unknown_and_is_charged(tmp_path):
    def unknown(*_):
        raise ConnectionError('connection ended without a known answer')
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, unknown)
    kwargs = dict(role='mind', scope='goal-1', segments=segments(4), instructions='', retain=2, trigger=4)
    with pytest.raises(ConnectionError):
        compact(tmp_path / 'summary.json', calls, **kwargs)
    calls.transport = lambda *_: pytest.fail('unknown call blindly replayed')
    with pytest.raises(BudgetPause, match='provider_call_outcome_unknown'):
        compact(tmp_path / 'summary.json', calls, **kwargs)
    assert calls.summary()['calls'] == 1
    assert calls.summary()['allocated_output_tokens'] == 8192


def test_scope_and_immutable_sources_are_checked_before_any_new_call(tmp_path):
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, summarize)
    path = tmp_path / 'summary.json'
    compact(path, calls, role='execution', scope='run-1', segments=segments(4),
            instructions='', retain=2, trigger=4)
    calls.transport = lambda *_: pytest.fail('conflicting context dispatched')
    for role, scope in [('mind', 'run-1'), ('execution', 'run-2')]:
        with pytest.raises(ValueError, match='working_context_scope_conflict'):
            compact(path, calls, role=role, scope=scope, segments=segments(6), instructions='')
    changed = segments(6)
    changed[0]['content'] = 'rewritten past result'
    with pytest.raises(ValueError, match='working_context_source_conflict'):
        compact(path, calls, role='execution', scope='run-1', segments=changed, instructions='')
    assert calls.summary()['calls'] == 1


def test_complete_native_rounds_and_tail_are_not_text_filtered(tmp_path):
    native = [
        {'role': 'assistant', 'content': [
            {'type': 'thinking', 'thinking': 'conditional reasoning', 'signature': 'signed'},
            {'type': 'tool_use', 'id': 'a', 'name': 'IPython', 'input': {'code': 'x = 2'}},
            {'type': 'tool_use', 'id': 'b', 'name': 'IPython', 'input': {'code': 'print(x)'}}]},
        {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'a', 'content': 'ok'},
            {'type': 'tool_result', 'tool_use_id': 'b', 'content': '2'}]}]
    history = [{'ref': f'round-{i}', 'content': native} for i in range(4)]
    frozen = json.loads(json.dumps(history))
    seen = []
    def transport(role, wire):
        seen.append(wire)
        return summarize(role, wire)
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, transport)
    state = compact(tmp_path / 'summary.json', calls, role='execution', scope='run-1',
                    segments=history, instructions='', retain=2, trigger=4)
    assert json.loads(seen[0]['messages'][0]['content'])['segments'] == frozen[:2]
    assert [item for item in history if item['ref'] not in state['source_refs']] == frozen[2:]
    assert history == frozen
    assert 'tools' not in seen[0]
    assert seen[0]['thinking'] == {'type': 'disabled'}


def test_whole_wire_estimate_and_capacity_pause_do_not_truncate_a_segment(tmp_path):
    wire = {'system': '请保留条件。', 'tools': [{'name': 'read', 'description': '观察'}],
            'messages': [{'role': 'assistant', 'content': [
                {'type': 'thinking', 'thinking': '假设', 'signature': 's' * 400}]}]}
    assert estimate_request_tokens(wire) >= 400 + len('请保留条件。观察假设'.encode('utf-8'))
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: pytest.fail('over-budget request'))
    history = segments(4)
    history[0]['content'] = '必须完整保留。' * 1000
    with pytest.raises(BudgetPause, match='working_context_capacity_exhausted'):
        compact(tmp_path / 'summary.json', calls, role='execution', scope='run-1',
                segments=history, instructions='', retain=2, trigger=4, max_request_bytes=3000)
    with pytest.raises(BudgetPause, match='working_context_capacity_exhausted'):
        compact(tmp_path / 'summary.json', calls, role='execution', scope='run-1',
                segments=segments(4), instructions='', retain=2, trigger=4,
                max_context_tokens=2048, max_output_tokens=2048)
    assert not (tmp_path / 'summary.json').exists()
    assert calls.summary()['calls'] == 0


def test_explicit_zero_request_budget_cannot_be_treated_as_default(tmp_path):
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: pytest.fail('zero budget dispatched'))
    with pytest.raises(ValueError, match='invalid_working_context_limits'):
        compact(tmp_path / 'summary.json', calls, role='execution', scope='run-1',
                segments=segments(4), instructions='', retain=2, trigger=4,
                max_request_bytes=0)
    assert calls.summary()['calls'] == 0


@pytest.mark.parametrize('response,reason', [
    ({'content': [{'type': 'text', 'text': '{}'}], 'stop_reason': 'max_tokens'}, 'incomplete'),
    ({'content': [{'type': 'tool_use', 'id': 'call-1', 'name': 'IPython', 'input': {}}]}, 'invalid'),
    ({'content': [{'type': 'text', 'text': 'unstructured reply'}]}, 'invalid'),
])
def test_incomplete_or_nontext_summary_cannot_advance_coverage(tmp_path, response, reason):
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: response)
    path = tmp_path / 'summary.json'
    with pytest.raises(BudgetPause, match=f'working_context_summary_{reason}'):
        compact(path, calls, role='mind', scope='goal-1', segments=segments(4),
                instructions='', retain=2, trigger=4)
    assert read_json(path)['accepted'] is None
    assert calls.summary()['calls'] == 1


def test_pause_between_local_admission_and_dispatch_recovers_original_request(tmp_path, monkeypatch):
    import working_context
    path = tmp_path / 'summary.json'
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, summarize)
    write = working_context.write_json
    def pause_after_admission(target, value):
        write(target, value)
        if value['pending']:
            calls.request_pause()
    monkeypatch.setattr(working_context, 'write_json', pause_after_admission)
    with pytest.raises(BudgetPause, match='user_pause_requested'):
        compact(path, calls, role='mind', scope='goal-1', segments=segments(4),
                instructions='original instruction', retain=2, trigger=4)
    frozen = read_json(path)['pending']['wire']
    assert calls.summary()['calls'] == 0
    monkeypatch.setattr(working_context, 'write_json', write)
    reopened = ProviderCalls(tmp_path / 'calls', LIMITS, summarize)
    state = compact(path, reopened, role='mind', scope='goal-1', segments=segments(5),
                    instructions='new instruction', retain=2, trigger=4)
    assert state['source_refs'] == ['decision-0', 'decision-1']
    assert reopened.records()[0][1]['wire'] == frozen
    assert reopened.summary()['calls'] == 1


def test_corrupt_projection_is_not_silently_rebuilt_or_used(tmp_path):
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, summarize)
    path = tmp_path / 'summary.json'
    compact(path, calls, role='mind', scope='goal-1', segments=segments(4),
            instructions='', retain=2, trigger=4)
    damaged = read_json(path)
    damaged['accepted']['summary'] = 'a tampered stronger conclusion'
    path.write_text(json.dumps(damaged), encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='working_context_integrity_failure'):
        compact(path, calls, role='mind', scope='goal-1', segments=segments(6),
                instructions='', retain=2, trigger=4)
    assert path.read_bytes() == before
    assert calls.summary()['calls'] == 1


@pytest.mark.parametrize('count,size,ratio,reserved,expected', [
    (150000, 200000, .85, 50000, True),
    (140000, 200000, .85, 50000, False),
    (850000, 1000000, .85, 50000, True),
    (840000, 1000000, .85, 50000, False),
    (140000, 200000, .70, 50000, True),
    (139999, 200000, .70, 50000, False),
    (0, 200000, .85, 50000, False),
])
def test_kimi_ratio_and_reserve_thresholds(count, size, ratio, reserved, expected):
    assert should_auto_compact(count, size, trigger_ratio=ratio,
                               reserved_context_size=reserved) is expected


@pytest.mark.parametrize('role,inner_ref', [('execution', 'event-000004'), ('mind', 'source')])
def test_visible_owner_result_refs_are_citable_without_changing_coverage(tmp_path, role, inner_ref):
    # Minimized from the first real comparison's rejected source_refs. Both
    # references were already present in owner-supplied structured ref fields.
    history = [
        {'ref': 'segment-0', 'content': {'results': [{'ref': inner_ref, 'value': 'observed'}]}},
        {'ref': 'segment-1', 'content': {'results': [{'ref': 'later-result'}]}}]
    reply = {'summary': 'The observed result retains its original scope.', 'source_refs': [inner_ref]}
    calls = ProviderCalls(tmp_path / 'calls', LIMITS,
        lambda *_: {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(reply)}]})
    state = compact(tmp_path / 'summary.json', calls, role=role, scope='goal-1',
                    segments=history, instructions='', retain=1, trigger=2)
    assert state['summary'] == reply['summary']
    assert state['cited_refs'] == [inner_ref]
    assert state['source_refs'] == ['segment-0']
    assert state['covered_until'] == 'segment-0'
    payload = json.loads(calls.records()[0][1]['wire']['messages'][0]['content'])
    assert payload['protocol'] == 'working-context-v4'
    assert set(payload['citable_refs']) == {'segment-0', inner_ref}
    assert state['citable_refs'] == payload['citable_refs']


@pytest.mark.parametrize('ref', ['future-result', 'quoted-ref', 'different-goal-ref'])
def test_citation_directory_excludes_retained_tail_and_refs_mentioned_only_in_text(tmp_path, ref):
    history = [
        {'ref': 'segment-0', 'content': {'ref': 'known-result',
            'text': 'A quoted string is not a ref declaration: {"ref":"quoted-ref"}'}},
        {'ref': 'segment-1', 'content': {'ref': 'future-result'}}]
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: {'content': [
        {'type': 'text', 'text': json.dumps({'summary': 'An ungrounded citation.', 'source_refs': [ref]})}]})
    path = tmp_path / 'summary.json'
    with pytest.raises(BudgetPause, match='working_context_summary_invalid'):
        compact(path, calls, role='mind', scope='goal-1', segments=history,
                instructions='', retain=1, trigger=2)
    assert read_json(path)['accepted'] is None
    payload = json.loads(calls.records()[0][1]['wire']['messages'][0]['content'])
    assert set(payload['citable_refs']) == {'segment-0', 'known-result'}


def test_next_summary_preserves_previous_legal_nested_citations(tmp_path):
    history = [{'ref': f'segment-{i}', 'content': {'ref': f'result-{i}'}} for i in range(4)]
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: {'content': [
        {'type': 'text', 'text': json.dumps({'summary': 'Keep the earlier conditional result.',
                                           'source_refs': ['result-0']})}]})
    path = tmp_path / 'summary.json'
    compact(path, calls, role='mind', scope='goal-1', segments=history[:2],
            instructions='', retain=1, trigger=2)
    current = compact(path, calls, role='mind', scope='goal-1', segments=history,
                      instructions='', retain=1, trigger=2)
    assert current['revision'] == 2
    assert current['source_refs'] == ['segment-0', 'segment-1', 'segment-2']
    assert current['cited_refs'] == ['result-0']
    payload = json.loads(calls.records()[1][1]['wire']['messages'][0]['content'])
    assert [part['ref'] for part in payload['segments']] == ['segment-1', 'segment-2']
    assert set(payload['citable_refs']) == {
        'segment-0', 'segment-1', 'segment-2', 'result-0', 'result-1', 'result-2'}


@pytest.mark.parametrize('legacy_ref', ['segment-0', 'event-000004'])
def test_frozen_v1_attempt_keeps_original_citation_contract(tmp_path, legacy_ref):
    from Nervous.provider import MODEL
    from Nervous.storage import canonical, fingerprint, write_json
    history = [
        {'ref': 'segment-0', 'content': {'results': [{'ref': 'event-000004'}]}},
        {'ref': 'segment-1', 'content': {}}]
    payload = {'previous_summary': None, 'previous_source_refs': [], 'segments': history[:1]}
    wire = {'model': MODEL, 'max_tokens': 128, 'thinking': {'type': 'disabled'},
        'temperature': 0, 'system': 'Frozen v1 instruction: cite only segment IDs.',
        'messages': [{'role': 'user', 'content': canonical(payload)}]}
    # Explicitly constructed historical fixture, not a rewritten real campaign.
    pending = {'role': 'execution', 'scope': 'goal-1', 'revision': 1,
        'base_digest': fingerprint(None), 'source_refs': ['segment-0'],
        'source_digest': fingerprint(history[:1]), 'input_digest': fingerprint(payload),
        'wire': wire, 'max_summary_chars': 6000}
    pending['operation'] = fingerprint(pending)
    document = {'format': 'working-context-v1', 'accepted': None, 'pending': pending}
    path = tmp_path / 'summary.json'
    write_json(path, {**document, 'sha256': fingerprint(document)})
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: {'content': [
        {'type': 'text', 'text': json.dumps({'summary': 'The original answer.', 'source_refs': [legacy_ref]})}]})
    calls.call('execution', wire, operation=pending['operation'], purpose='compaction')
    calls.transport = lambda *_: pytest.fail('Frozen v1 response resampled')
    before_context = path.read_bytes()
    before_call = (tmp_path / 'calls' / '0001.json').read_bytes()
    kwargs = dict(role='execution', scope='goal-1', segments=history,
                  instructions='New v2 instructions do not change a frozen v1 call.', retain=1, trigger=2)
    if legacy_ref == 'event-000004':
        with pytest.raises(BudgetPause, match='working_context_summary_invalid'):
            compact(path, calls, **kwargs)
        assert path.read_bytes() == before_context
    else:
        accepted = compact(path, calls, **kwargs)
        assert accepted['cited_refs'] == ['segment-0']
        assert 'protocol' not in accepted and 'citable_refs' not in accepted
        accepted_bytes = path.read_bytes()
        assert compact(path, calls, **kwargs) == accepted
        assert path.read_bytes() == accepted_bytes
    assert calls.summary()['calls'] == 1
    assert (tmp_path / 'calls' / '0001.json').read_bytes() == before_call


@pytest.mark.parametrize('response,reason', [
    ({'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '{"summary":"' + 'x' * 6976}]},
     'working_context_summary_incomplete'),
    ({'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({
        'summary': 'x' * 6976, 'source_refs': ['unknown-ref']})}]}, 'working_context_summary_invalid'),
])
def test_explicit_summary_retry_preserves_old_attempt_and_claims_new_response_once(tmp_path, response, reason):
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, summarize)
    path = tmp_path / 'background.json'
    kwargs = dict(role='mind', scope='goal-1', instructions='Preserve conditional failures.', retain=2, trigger=4)
    accepted = compact(path, calls, segments=segments(4), max_output_tokens=128, **kwargs)
    calls.transport = lambda *_: response
    with pytest.raises(BudgetPause, match=reason):
        compact(path, calls, segments=segments(6), max_output_tokens=2048, **kwargs)
    original = read_json(path)
    old_pending = original['pending']
    old_call = (tmp_path / 'calls' / '0002.json').read_bytes()
    calls.transport = lambda *_: pytest.fail('Retry admission must not dispatch')
    assert retry_failed_compaction(path, calls, role='mind', scope='goal-1') is True
    assert calls.summary()['calls'] == 2
    saved = read_json(path)
    retry = saved['pending']
    assert saved['accepted'] == accepted
    assert retry['retry_of'] == old_pending['operation'] != retry['operation']
    for key in ('source_refs', 'source_digest', 'input_digest', 'base_digest', 'revision',
                'protocol', 'citable_refs', 'max_summary_chars', 'max_request_bytes', 'max_context_tokens'):
        assert retry[key] == old_pending[key]
    assert retry['wire']['system'] == old_pending['wire']['system']
    assert retry['wire']['messages'][:-2] == old_pending['wire']['messages']
    assert retry['wire']['messages'][-2] == {'role': 'assistant', 'content': response['content']}
    assert retry['wire']['messages'][-1]['role'] == 'user'
    assert retry['wire']['max_tokens'] == 8192 and old_pending['wire']['max_tokens'] == 2048
    feedback = retry['wire']['messages'][-1]['content']
    assert reason in feedback and '6000' in feedback and 'do not copy the whole reference directory' in feedback
    assert len(feedback) < 1000 and 'x' * 6976 not in feedback
    assert 'drafting target' in feedback and 'not an acceptance limit' in feedback
    archive = path.with_name(f'background.failed-{old_pending["operation"]}.json')
    archived = read_json(archive)
    assert archived['context'] == original and archived['failure'] == reason
    archive_bytes = archive.read_bytes()
    assert (tmp_path / 'calls' / '0002.json').read_bytes() == old_call
    assert retry_failed_compaction(path, calls, role='mind', scope='goal-1') is False
    reopened = ProviderCalls(calls.directory, LIMITS, summarize)
    result = compact(path, reopened, segments=segments(6), max_summary_chars=1, **kwargs)
    assert result['revision'] == 2 and result['call_ref'] == retry['operation']
    assert reopened.records()[-1][1]['wire'] == retry['wire']
    assert reopened.records()[-1][1]['request_bytes'] == estimate_request_tokens(retry['wire'])
    assert reopened.summary()['calls'] == 3
    assert reopened.summary()['allocated_output_tokens'] == 128 + 2048 + 8192
    reopened.transport = lambda *_: pytest.fail('A completed retry was resampled')
    assert compact(path, reopened, segments=segments(6), **kwargs) == result
    assert retry_failed_compaction(path, reopened, role='mind', scope='goal-1') is False
    assert archive.read_bytes() == archive_bytes
    assert (tmp_path / 'calls' / '0002.json').read_bytes() == old_call


@pytest.mark.parametrize('failure_type', ['reserved', 'failed'])
def test_explicit_summary_retry_refuses_unknown_provider_outcomes(tmp_path, failure_type):
    failure = SystemExit if failure_type == 'reserved' else ConnectionError
    def transport(*_):
        raise failure('No received response')
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, transport)
    path = tmp_path / 'background.json'
    with pytest.raises(failure):
        compact(path, calls, role='mind', scope='goal-1', segments=segments(4),
                instructions='', retain=2, trigger=4)
    before = path.read_bytes()
    ledger = (tmp_path / 'calls' / '0001.json').read_bytes()
    with pytest.raises(BudgetPause, match='provider_call_outcome_unknown'):
        retry_failed_compaction(path, calls, role='mind', scope='goal-1')
    assert path.read_bytes() == before
    assert (tmp_path / 'calls' / '0001.json').read_bytes() == ledger
    assert not list(tmp_path.glob('*.failed-*.json'))


def test_valid_summary_response_uses_normal_resume_instead_of_explicit_retry(tmp_path, monkeypatch):
    import working_context
    path = tmp_path / 'background.json'
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, summarize)
    save = working_context._save
    def crash_before_commit(target, accepted, pending=None):
        if pending is None:
            raise OSError('before accepted projection')
        save(target, accepted, pending)
    monkeypatch.setattr(working_context, '_save', crash_before_commit)
    kwargs = dict(role='mind', scope='goal-1', segments=segments(4), instructions='', retain=2, trigger=4)
    with pytest.raises(OSError, match='before accepted projection'):
        compact(path, calls, **kwargs)
    original = path.read_bytes()
    calls.transport = lambda *_: pytest.fail('Known valid response was resampled')
    assert retry_failed_compaction(path, calls, role='mind', scope='goal-1') is False
    assert path.read_bytes() == original and not list(tmp_path.glob('*.failed-*.json'))
    monkeypatch.setattr(working_context, '_save', save)
    assert compact(path, calls, **kwargs)['revision'] == 1
    assert calls.summary()['calls'] == 1


@pytest.mark.parametrize('crash_after_save', [False, True])
def test_summary_retry_archive_and_atomic_admission_survive_crash(tmp_path, monkeypatch, crash_after_save):
    import working_context
    path = tmp_path / 'background.json'
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: {
        'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '{'}]})
    kwargs = dict(role='mind', scope='goal-1', segments=segments(4), instructions='', retain=2, trigger=4)
    with pytest.raises(BudgetPause, match='working_context_summary_incomplete'):
        compact(path, calls, max_output_tokens=2048, **kwargs)
    original = path.read_bytes()
    old_operation = read_json(path)['pending']['operation']
    save = working_context._save
    attempted = []
    def crash_at_admission(target, accepted, pending=None):
        attempted.append(pending)
        if crash_after_save:
            save(target, accepted, pending)
        raise OSError('retry admission interrupted')
    monkeypatch.setattr(working_context, '_save', crash_at_admission)
    with pytest.raises(OSError, match='retry admission interrupted'):
        retry_failed_compaction(path, calls, role='mind', scope='goal-1')
    archive = path.with_name(f'background.failed-{old_operation}.json')
    archived = archive.read_bytes()
    assert read_json(archive)['context'] == json.loads(original)
    assert calls.summary()['calls'] == 1
    assert crash_after_save or path.read_bytes() == original
    monkeypatch.setattr(working_context, '_save', save)
    assert retry_failed_compaction(path, calls, role='mind', scope='goal-1') is (not crash_after_save)
    assert read_json(path)['pending'] == attempted[0]
    assert archive.read_bytes() == archived and len(list(tmp_path.glob('*.failed-*.json'))) == 1
    calls.transport = summarize
    def crash_before_retry_commit(target, accepted, pending=None):
        if pending is None:
            raise OSError('retry response received before owner commit')
        save(target, accepted, pending)
    monkeypatch.setattr(working_context, '_save', crash_before_retry_commit)
    with pytest.raises(OSError, match='retry response received before owner commit'):
        compact(path, calls, **kwargs)
    assert calls.summary()['calls'] == 2
    reopened = ProviderCalls(calls.directory, LIMITS, lambda *_: pytest.fail('Retry response resampled'))
    assert retry_failed_compaction(path, reopened, role='mind', scope='goal-1') is False
    monkeypatch.setattr(working_context, '_save', save)
    assert compact(path, reopened, **kwargs)['call_ref'] == attempted[0]['operation']
    assert reopened.summary()['calls'] == 2
    assert archive.read_bytes() == archived


def test_explicit_summary_retry_checks_budget_before_archiving_or_admitting(tmp_path):
    limits = dict(LIMITS, output_tokens=2048)
    calls = ProviderCalls(tmp_path / 'calls', limits, lambda *_: {
        'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '{'}]})
    path = tmp_path / 'background.json'
    with pytest.raises(BudgetPause, match='working_context_summary_incomplete'):
        compact(path, calls, role='mind', scope='goal-1', segments=segments(4),
                instructions='', retain=2, trigger=4, max_output_tokens=2048)
    original = path.read_bytes()
    with pytest.raises(BudgetPause, match='provider_budget_exhausted'):
        retry_failed_compaction(path, calls, role='mind', scope='goal-1')
    assert path.read_bytes() == original and not list(tmp_path.glob('*.failed-*.json'))
    assert calls.summary()['calls'] == 1


def receipt_segment(index, source_ref):
    return {'ref': f'activity-{index}', 'content': {'pieces': [{
        'ref': f'history:activation-{index}:4', 'event_type': 'CAPABILITY_OBSERVED',
        'content': {'payload': {'capability': 'read_evidence', 'observation': {
            'capability': 'read_evidence', 'origin': 'execution', 'text': json.dumps({
                'read_result': 'sources-v1', 'sources': [{
                    'ref': source_ref, 'text': 'Only the measured subset is observed.', 'origin': 'execution'}]})}}}}]}}


def test_v4_directory_reads_only_typed_validated_receipts_and_rebuilds_old_coverage(tmp_path):
    from Nervous.storage import write_json, fingerprint
    history = [receipt_segment(0, 'source:prior-receipt'), receipt_segment(1, 'source:next-receipt'),
               receipt_segment(2, 'source:future-receipt')]
    source_text = json.dumps({'read_result': 'sources-v1', 'sources': [
        {'ref': 'source:fiction', 'text': 'A quoted source.', 'origin': 'execution'}]})
    pieces = history[1]['content']['pieces']
    pieces.append({'ref': 'history:activation-1:5', 'event_type': 'MODEL_OUTPUT_RECORDED',
                   'content': {'payload': {'text': source_text}}})
    pieces.append({'ref': 'history:activation-1:0', 'event_type': 'ACTIVATION_STARTED',
                   'content': {'payload': {'input': {'evidence': [
                       {'ref': 'source:owner-text', 'text': source_text, 'origin': 'execution'}]}}}})
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: {
        'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({
            'summary': 'The original condition remains.', 'source_refs': ['source:prior-receipt']})}]})
    path = tmp_path / 'background.json'
    first = compact(path, calls, role='mind', scope='goal-1', segments=history[:2],
                    instructions='', retain=1, trigger=2)
    # Synthetic v2 accepted fixture has the historical directory omission.
    old = read_json(path)
    old['accepted'].update(protocol='working-context-v2', citable_refs=['activity-0', 'history:activation-0:4'])
    body = {key: value for key, value in old.items() if key != 'sha256'}
    write_json(path, {**body, 'sha256': fingerprint(body)})
    second = compact(path, calls, role='mind', scope='goal-1', segments=history,
                     instructions='', retain=1, trigger=2)
    assert first['revision'] == 1 and second['revision'] == 2
    assert {'source:prior-receipt', 'source:next-receipt'} <= set(second['citable_refs'])
    assert 'source:future-receipt' not in second['citable_refs']
    assert 'source:fiction' not in second['citable_refs']
    sent = json.loads(calls.records()[-1][1]['wire']['messages'][0]['content'])
    assert sent['segments'] == history[1:2]  # Rebuilt refs do not re-send old history.
    malformed = receipt_segment(0, 'source:bad')
    observation = malformed['content']['pieces'][0]['content']['payload']['observation']
    receipt = json.loads(observation['text'])
    receipt['sources'][0]['origin'] = 'invented'
    observation['text'] = json.dumps(receipt)
    with pytest.raises(ValueError, match='invalid_read_source'):
        compact(tmp_path / 'malformed.json', calls, role='mind', scope='goal-1',
                segments=[malformed, history[-1]], instructions='', retain=1, trigger=2)


@pytest.mark.parametrize('envelope', ['json', 'plain', 'crlf'])
def test_v4_accepts_only_a_whole_json_fence_without_rewriting_summary(tmp_path, envelope):
    text = json.dumps({'summary': 'An unchanged conditional statement.', 'source_refs': ['decision-0']})
    fence = {'json': f'```json\n{text}\n```', 'plain': f'```\n{text}\n```',
             'crlf': f'```json\r\n{text}\r\n```'}[envelope]
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: {
        'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': fence}]})
    result = compact(tmp_path / 'background.json', calls, role='mind', scope='goal-1',
                     segments=segments(2), instructions='', retain=1, trigger=2)
    assert result['summary'] == 'An unchanged conditional statement.'
    assert calls.records()[0][1]['response']['content'][0]['text'] == fence


@pytest.mark.parametrize('text', [
    'Here is the summary:\n```json\n{"summary":"x","source_refs":["decision-0"]}\n```',
    '```json\n{"summary":"x","source_refs":["decision-0"]}\n```\nExtra explanation.',
    '```json\n{"summary":"x","source_refs":["decision-0"]}\n```\n```json\n{}\n```',
])
def test_v4_fence_does_not_extract_json_from_other_model_prose(tmp_path, text):
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: {
        'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': text}]})
    with pytest.raises(BudgetPause, match='working_context_summary_invalid'):
        compact(tmp_path / 'background.json', calls, role='mind', scope='goal-1',
                segments=segments(2), instructions='', retain=1, trigger=2)


def test_explicit_v4_retry_preserves_failed_v2_and_upgrades_only_verified_frozen_prefix(tmp_path):
    from Nervous.provider import MODEL
    from Nervous.storage import canonical, fingerprint, write_json
    from working_context import _summary
    history = [receipt_segment(0, 'source:4d66b491d0f2246c61b4c2a5'),
               receipt_segment(1, 'source:16fb4f4e01e0e3ca5f1cf353'),
               receipt_segment(2, 'source:future-receipt')]
    accepted = {'role': 'mind', 'scope': 'goal-1', 'protocol': 'working-context-v2', 'revision': 1,
        'source_refs': ['activity-0'], 'source_digest': fingerprint(history[:1]),
        'citable_refs': ['activity-0', 'history:activation-0:4'], 'summary': 'Prior scoped observation.'}
    payload = {'protocol': 'working-context-v2', 'citable_refs': accepted['citable_refs'] +
               ['activity-1', 'history:activation-1:4'], 'previous_summary': accepted['summary'],
               'previous_source_refs': accepted['source_refs'], 'segments': history[1:2]}
    wire = {'model': MODEL, 'max_tokens': 8192, 'thinking': {'type': 'disabled'}, 'temperature': 0,
            'system': 'Frozen v2 instructions.', 'messages': [{'role': 'user', 'content': canonical(payload)}]}
    pending = {'role': 'mind', 'scope': 'goal-1', 'protocol': 'working-context-v2', 'revision': 2,
        'base_digest': fingerprint(accepted), 'source_refs': ['activity-0', 'activity-1'],
        'source_digest': fingerprint(history[:2]), 'input_digest': fingerprint(payload),
        'wire': wire, 'max_summary_chars': 6000, 'citable_refs': payload['citable_refs']}
    pending['operation'] = fingerprint(pending)
    body = {'format': 'working-context-v1', 'accepted': accepted, 'pending': pending}
    path = tmp_path / 'background.json'
    write_json(path, {**body, 'sha256': fingerprint(body)})
    invalid = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '```json\n' + json.dumps({
        'summary': '测' * 6180, 'source_refs': ['source:4d66b491d0f2246c61b4c2a5',
                                             'source:16fb4f4e01e0e3ca5f1cf353']}) + '\n```'}]}
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: invalid)
    calls.call('mind', wire, operation=pending['operation'], purpose='compaction')
    old_context, old_call = path.read_bytes(), (calls.directory / '0001.json').read_bytes()
    kwargs = dict(role='mind', scope='goal-1', segments=history, instructions='', retain=1, trigger=2)
    with pytest.raises(BudgetPause, match='working_context_summary_invalid'):
        compact(path, calls, **kwargs)
    assert path.read_bytes() == old_context
    changed = json.loads(canonical(history))
    changed[0]['content']['pieces'][0]['content']['payload']['observation']['origin'] = 'computation'
    with pytest.raises(ValueError, match='working_context_source_conflict'):
        retry_failed_compaction(path, calls, role='mind', scope='goal-1', segments=changed)
    assert path.read_bytes() == old_context
    assert retry_failed_compaction(path, calls, role='mind', scope='goal-1', segments=history)
    new = read_json(path)['pending']
    new_payload = json.loads(new['wire']['messages'][0]['content'])
    assert new['protocol'] == new_payload['protocol'] == 'working-context-v4'
    assert new['wire']['system'] == wire['system']
    assert new_payload['segments'] == payload['segments']
    assert new_payload['previous_summary'] == payload['previous_summary']
    assert new['input_digest'] == fingerprint(new_payload) != pending['input_digest']
    assert new['source_refs'] == pending['source_refs'] and new['source_digest'] == pending['source_digest']
    assert new['base_digest'] == pending['base_digest'] and new['revision'] == pending['revision']
    assert {'source:4d66b491d0f2246c61b4c2a5', 'source:16fb4f4e01e0e3ca5f1cf353'} <= set(new['citable_refs'])
    assert 'source:future-receipt' not in new['citable_refs']
    feedback = new['wire']['messages'][-1]['content']
    assert '6180 characters' in feedback and '6000 characters as a drafting target' in feedback
    assert new['wire']['messages'][-2] == {'role': 'assistant', 'content': invalid['content']}
    assert len(feedback) < 1000
    assert _summary(invalid, new)['summary'] == '测' * 6180
    good = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '```json\n' + json.dumps({
        'summary': 'The original observations remain scoped.',
        'source_refs': ['source:4d66b491d0f2246c61b4c2a5', 'source:16fb4f4e01e0e3ca5f1cf353']}) + '\n```'}]}
    with pytest.raises(BudgetPause, match='working_context_summary_invalid'):
        _summary(good, pending)  # v2 never receives the new envelope interpretation.
    calls.transport = lambda *_: good
    result = compact(path, calls, **kwargs)
    assert result['protocol'] == 'working-context-v4' and result['call_ref'] == new['operation']
    assert calls.summary()['calls'] == 2
    assert compact(path, calls, **kwargs) == result
    assert (calls.directory / '0001.json').read_bytes() == old_call
    archive = read_json(path.with_name(f'background.failed-{pending["operation"]}.json'))
    assert archive['context'] == json.loads(old_context)


@pytest.mark.parametrize('boundary', ['request', 'context', 'task'])
def test_retry_accounts_for_the_verbatim_rejected_response_before_admission(tmp_path, boundary):
    response = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({
        'summary': 'x' * 6661, 'source_refs': ['unknown-ref']})}]}
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: response)
    path = tmp_path / 'background.json'
    bounds = {'max_request_bytes': 8000} if boundary == 'request' else (
        {'max_context_tokens': 15000} if boundary == 'context' else {})
    with pytest.raises(BudgetPause, match='working_context_summary_invalid'):
        compact(path, calls, role='mind', scope='goal-1', segments=segments(2),
                instructions='', retain=1, trigger=2, **bounds)
    if boundary == 'task':
        # The response body alone fits; the complete correction request does not.
        calls.limits = dict(LIMITS, request_bytes=calls.summary()['request_bytes'] +
                           len(response['content'][0]['text']))
    original, ledger = path.read_bytes(), (calls.directory / '0001.json').read_bytes()
    reason = 'provider_budget_exhausted' if boundary == 'task' else 'working_context_capacity_exhausted'
    with pytest.raises(BudgetPause, match=reason):
        retry_failed_compaction(path, calls, role='mind', scope='goal-1')
    assert path.read_bytes() == original and (calls.directory / '0001.json').read_bytes() == ledger
    assert not list(tmp_path.glob('*.failed-*.json')) and calls.summary()['calls'] == 1


@pytest.mark.parametrize('protocol', ['working-context-v1', 'working-context-v2', 'working-context-v3'])
def test_old_protocols_keep_hard_character_limit_while_v4_uses_output_and_request_budgets(tmp_path, protocol):
    from working_context import _summary
    text = '测' * 6478
    response = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({
        'summary': text, 'source_refs': ['decision-0']}, ensure_ascii=False)}]}
    historical = {'protocol': protocol, 'max_summary_chars': 6000,
                  'source_refs': ['decision-0'], 'citable_refs': ['decision-0']}
    with pytest.raises(BudgetPause, match='working_context_summary_invalid'):
        _summary(response, historical)
    calls = ProviderCalls(tmp_path / 'calls', LIMITS, lambda *_: response)
    result = compact(tmp_path / 'background.json', calls, role='mind', scope='goal-1',
                     segments=segments(2), instructions='', retain=1, trigger=2)
    assert result['protocol'] == 'working-context-v4' and result['summary'] == text
    assert calls.records()[0][1]['response'] == response
    assert calls.records()[0][1]['wire']['max_tokens'] == 8192
    assert 'Summary target: 6000 characters.' in calls.records()[0][1]['wire']['system']
    incomplete = dict(response, stop_reason='max_tokens')
    with pytest.raises(BudgetPause, match='working_context_summary_incomplete'):
        _summary(incomplete, dict(historical, protocol='working-context-v4'))
