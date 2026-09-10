"""Bounded official provider dispatch and durable accounting, without organ judgment.

The owning Nervous writer serializes reservations and dispatches. A reservation
is charged even if the process or connection fails before a response is stored.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from Nervous.storage import canonical, fingerprint, read_json, write_json

MODEL = 'deepseek-v4-pro'
ENDPOINT = 'https://api.deepseek.com/anthropic/v1/messages'
REQUEST_LIMITS = {'mind': 240000, 'builder': 150000, 'execution': 150000}
CALL_FORMAT = 'provider-call-v1'


class BudgetPause(BaseException):
    """A pre-dispatch resource or uncertain-outcome stop, never NoChange."""


def anthropic_messages(messages):
    """Losslessly map the existing native text/tool dialogue to Anthropic."""
    result = []
    for message in messages:
        role = message['role']
        if role == 'system':
            continue
        blocks = []
        if role == 'tool':
            role = 'user'
            blocks.append({'type': 'tool_result', 'tool_use_id': message['tool_call_id'],
                           'content': message['content']})
        else:
            if message.get('content'):
                blocks.append({'type': 'text', 'text': message['content']})
            for call in message.get('tool_calls', ()):
                blocks.append({'type': 'tool_use', 'id': call['id'],
                               'name': call['function']['name'],
                               'input': json.loads(call['function']['arguments'])})
        if blocks:
            # A native sibling batch returns all results in one Anthropic turn.
            if (message['role'] == 'tool' and result and result[-1]['role'] == 'user'
                    and all(b['type'] == 'tool_result' for b in result[-1]['content'])):
                result[-1]['content'].extend(blocks)
            else:
                result.append({'role': role, 'content': blocks})
    return result


def native_response(response):
    content = response.get('content', [])
    return {'choices': [{'message': {'content': ''.join(
        block['text'] for block in content if block.get('type') == 'text'),
        'tool_calls': [{'id': block['id'], 'type': 'function', 'function': {
            'name': block['name'], 'arguments': canonical(block['input'])}}
            for block in content if block.get('type') == 'tool_use']}}],
        'usage': response.get('usage', {})}


class ProviderCalls:
    """Reserve, dispatch and account for calls across all current organs."""
    def __init__(self, directory, limits, transport=None):
        self.directory, self.limits, self.transport = Path(directory), limits, transport
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pause_requested = False

    def request_pause(self):
        # Signal handlers only set this flag; persistence happens at a safe boundary.
        self.pause_requested = True

    def check_pause(self):
        if self.pause_requested:
            raise BudgetPause('user_pause_requested')

    def records(self, *, role=None):
        records = []
        for path in sorted(self.directory.glob('*.json')):
            record = read_json(path)
            if record.get('format') != CALL_FORMAT:
                raise ValueError('unsupported_provider_record')
            if role is None or record['role'] == role:
                records.append((path, record))
        return records

    def summary(self):
        records = [record for _, record in self.records()]
        return self.summarize(records)

    @staticmethod
    def summarize(records):
        return {'calls': len(records),
            'allocated_output_tokens': sum(r['wire']['max_tokens'] for r in records),
            'request_bytes': sum(r['request_bytes'] for r in records),
            'usage': {key: sum(r.get('response', {}).get('usage', {}).get(key, 0) for r in records)
                      for key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')},
            'errors': sum('error' in r or 'response' not in r for r in records)}

    @classmethod
    def inspect_directory(cls, directory):
        records, issues = [], []
        for index, path in enumerate(sorted(Path(directory).glob('*.json')), 1):
            try:
                record = read_json(path)
                if not isinstance(record, dict) or record.get('format') != CALL_FORMAT:
                    raise ValueError('unsupported_provider_record')
                if record.get('role') not in REQUEST_LIMITS:
                    raise ValueError('unsupported_provider_role')
                response = record.get('response', {})
                if not isinstance(response, dict) or not isinstance(response.get('usage', {}), dict):
                    raise ValueError('invalid_provider_response')
                cls.summarize([record])
                records.append(record)
            except (OSError, ValueError, TypeError, KeyError) as error:
                issues.append({'record': index, 'kind': type(error).__name__})
        return {**cls.summarize(records),
                'unresolved': [{'role': r['role'], 'operation': r.get('operation'),
                                'purpose': r.get('purpose', 'decision'), 'status': r.get('status')}
                               for r in records if 'response' not in r],
                'diagnostic': {'issues': issues, 'complete': not issues}}

    def ensure(self, wire, *, role=None):
        self.check_pause()
        if wire.get('model') != 'deepseek-v4-pro':
            raise ValueError('provider_model_conflict')
        used = self.summary()
        request_bytes = len(json.dumps(wire, ensure_ascii=False).encode('utf-8'))
        if request_bytes > REQUEST_LIMITS.get(role, 150000):
            raise ValueError('provider_request_too_large')
        if (used['calls'] >= self.limits['calls']
                or used['request_bytes'] + request_bytes > self.limits['request_bytes']
                or used['allocated_output_tokens'] + wire['max_tokens'] > self.limits['output_tokens']):
            raise BudgetPause('provider_budget_exhausted')
        return used, request_bytes

    def recover(self, role, operation, *, purpose='decision'):
        """Claim only an explicitly identified, durably received original call.

        Return the frozen wire with its response; a caller must not interpret
        that response as the answer to a newly assembled request.
        """
        if not isinstance(operation, str) or not operation or len(operation) > 512:
            raise ValueError('invalid_provider_operation')
        matches = [record for _, record in self.records(role=role)
                   if record.get('operation') == operation
                   and record.get('purpose', 'decision') == purpose]
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError('provider_operation_ambiguous')
        record = matches[0]
        if record.get('request_sha256') != fingerprint(record['wire']):
            raise ValueError('provider_request_integrity_failure')
        if record['status'] != 'received' or 'response' not in record:
            raise BudgetPause('provider_call_outcome_unknown')
        return record

    def call(self, role, wire, *, metadata=None, operation=None, purpose='decision'):
        if role not in REQUEST_LIMITS:
            raise ValueError('unsupported_provider_role')
        if operation is not None:
            previous = self.recover(role, operation, purpose=purpose)
            if previous is not None:
                if previous['wire'] != wire or previous.get('metadata') != metadata:
                    raise ValueError('provider_operation_conflict')
                return previous['response']
        used, request_bytes = self.ensure(wire, role=role)
        path = self.directory / f'{used["calls"] + 1:04d}.json'
        record = {'format': CALL_FORMAT, 'role': role, 'wire': wire, 'request_bytes': request_bytes,
                  'started_at': time.time(), 'status': 'reserved'}
        if metadata is not None:
            record['metadata'] = metadata
        if operation is not None:
            record.update(operation=operation, purpose=purpose, request_sha256=fingerprint(wire))
        write_json(path, record)
        start = time.monotonic()
        try:
            if self.transport is not None:
                response = self.transport(role, wire)
            else:
                with httpx.Client(timeout=300 if role == 'mind' else 180) as client:
                    result = client.post(ENDPOINT,
                        headers={'x-api-key': os.environ['DEEPSEEK_API_KEY'],
                                 'anthropic-version': '2023-06-01'}, json=wire)
                    result.raise_for_status()
                    response = result.json()
            record.update(response=response, status='received')
            return response
        except Exception as error:
            record.update(error=type(error).__name__, status='failed')
            if isinstance(error, httpx.HTTPStatusError):
                body = error.response.text
                record['provider_rejection'] = {'status_code': error.response.status_code,
                    'body': body[:4000], 'original_chars': len(body), 'truncated': len(body) > 4000}
            raise
        finally:
            record['seconds'] = time.monotonic() - start
            write_json(path, record)
