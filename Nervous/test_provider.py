"""Mechanical provider reservation and shared cost, no real calls."""
import pytest

from Nervous.provider import BudgetPause, MODEL, ProviderCalls
from Nervous.storage import read_json


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
