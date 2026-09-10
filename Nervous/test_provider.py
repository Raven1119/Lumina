"""Mechanical provider reservation and shared cost, no real calls."""
import subprocess
import sys

import pytest

from Nervous.provider import BudgetPause, MODEL, ProviderCalls
from Nervous.storage import read_json


def test_received_response_survives_process_exit_before_owner_claim(tmp_path):
    script = '''
import os, sys
from Nervous.provider import MODEL, ProviderCalls
calls = ProviderCalls(sys.argv[1], {'calls': 1, 'output_tokens': 100, 'request_bytes': 10000},
    lambda *_: {'content': [{'type': 'text', 'text': 'known result'}]})
calls.call('mind', {'model': MODEL, 'max_tokens': 100, 'messages': []},
           operation='task-1/activity-1/call-1')
os._exit(23)
'''
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 23, result.stderr
    calls = ProviderCalls(tmp_path, {'calls': 1, 'output_tokens': 100, 'request_bytes': 10000},
                          lambda *_: pytest.fail('Known response must not be sampled again'))
    saved = calls.recover('mind', 'task-1/activity-1/call-1')
    assert saved['response']['content'][0]['text'] == 'known result'
    assert calls.call('mind', saved['wire'], operation='task-1/activity-1/call-1') == saved['response']
    assert calls.summary()['calls'] == 1
    with pytest.raises(ValueError, match='provider_operation_conflict'):
        calls.call('mind', {**saved['wire'], 'max_tokens': 90}, operation='task-1/activity-1/call-1')
    assert calls.recover('mind', 'task-1/activity-2/call-1') is None


def test_reserve_precedes_dispatch_and_failed_calls_are_not_refunded(tmp_path):
    limits = {'calls': 1, 'output_tokens': 100, 'request_bytes': 10000}
    def transport(role, wire):
        saved = read_json(tmp_path / '0001.json')
        assert saved['status'] == 'reserved' and saved['role'] == role and saved['wire'] == wire
        assert 'source_build' not in saved
        raise ConnectionError('test failure')
    calls = ProviderCalls(tmp_path, limits, transport)
    wire = {'model': MODEL, 'max_tokens': 100, 'messages': []}
    with pytest.raises(ConnectionError): calls.call('mind', wire)
    resumed = ProviderCalls(tmp_path, limits, lambda *_: pytest.fail('Budget was reset'))
    assert resumed.summary()['calls'] == resumed.summary()['errors'] == 1
    with pytest.raises(BudgetPause, match='provider_budget_exhausted'):
        resumed.call('builder', wire)


def test_all_roles_share_cost_and_only_the_current_provider_is_allowed(tmp_path):
    wire = {'model': MODEL, 'max_tokens': 10, 'messages': []}
    calls = ProviderCalls(tmp_path, {'calls': 3, 'output_tokens': 30, 'request_bytes': 10000},
        lambda role, request: {'content': [], 'usage': {'input_tokens': 4, 'output_tokens': 2}})
    for role in ('mind', 'builder', 'execution'): calls.call(role, wire)
    assert calls.summary()['allocated_output_tokens'] == 30
    assert calls.summary()['usage']['input_tokens'] == 12
    with pytest.raises(ValueError, match='provider_model_conflict'):
        calls.ensure({**wire, 'model': 'other-model'})
    assert calls.summary()['calls'] == 3
