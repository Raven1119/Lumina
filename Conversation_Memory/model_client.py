"""Memory model client with its own response cache and shared model selection."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import httpx
from model_policy import ANTHROPIC_MESSAGES_URL, model_for


ANTHROPIC_ENDPOINT = ANTHROPIC_MESSAGES_URL


@dataclass(frozen=True)
class MemoryModelResult:
    text: str
    usage: dict
    cache_hit: bool
    cache_key: str


class MemoryModel:
    def __init__(self, *, api_key: str, model: str, cache_dir: Path,
                 http_client: httpx.Client | None = None):
        self._api_key = api_key
        self.model = model
        self.cache_dir = Path(cache_dir)
        self._client = http_client
        self._lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls_by_purpose: dict[str, int] = {}

    def complete(self, *, system: str, messages: list[dict], max_tokens: int,
                 purpose: str, attempt: int = 0) -> MemoryModelResult:
        payload = {'model': self.model, 'max_tokens': max_tokens,
                   'temperature': 0.0, 'prompt_version': purpose + '_v1',
                   'system': system, 'messages': messages, 'attempt': attempt}
        key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                        separators=(',', ':')).encode()).hexdigest()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / (key + '.json')
        with self._lock:
            if cache_path.is_file():
                saved = json.loads(cache_path.read_text(encoding='utf-8'))
                return MemoryModelResult(saved['text'], saved['usage'], True, key)
            body = {'model': self.model, 'max_tokens': max_tokens,
                    'temperature': 0, 'thinking': {'type': 'disabled'},
                    'system': system, 'messages': messages}
            headers = {'X-Api-Key': self._api_key, 'anthropic-version': '2023-06-01',
                       'content-type': 'application/json'}
            try:
                client = self._client or httpx.Client(timeout=180)
                response = client.post(ANTHROPIC_ENDPOINT, headers=headers, json=body)
                if response.status_code == 400:
                    body.pop('temperature')
                    response = client.post(ANTHROPIC_ENDPOINT, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
                text = ''.join(part.get('text', '') for part in data.get('content', [])
                               if part.get('type') == 'text')
                if not text.strip():
                    raise ValueError('empty_response')
                usage = data.get('usage') or {}
                usage = {'input_tokens': int(usage.get('input_tokens', 0)),
                         'output_tokens': int(usage.get('output_tokens', 0))}
            except Exception:
                raise RuntimeError('memory_model_call_failed') from None
            finally:
                if self._client is None and 'client' in locals():
                    client.close()
            saved = {'text': text, 'usage': usage, 'model': self.model,
                     'purpose': purpose, 'attempt': attempt}
            temp_path = cache_path.with_suffix('.tmp')
            temp_path.write_text(json.dumps(saved, ensure_ascii=False, sort_keys=True), encoding='utf-8')
            os.replace(temp_path, cache_path)
            self.calls += 1
            self.input_tokens += usage['input_tokens']
            self.output_tokens += usage['output_tokens']
            self.calls_by_purpose[purpose] = self.calls_by_purpose.get(purpose, 0) + 1
            return MemoryModelResult(text, usage, False, key)


def build_memory_model_from_env(cache_dir: Path, environ: Mapping[str, str] | None = None,
                                *, http_client: httpx.Client | None = None) -> MemoryModel | None:
    """Use the shared runtime model policy without changing Memory's cache."""
    env = os.environ if environ is None else environ
    if env.get('LUMINA_MODEL_MODE', 'mock').strip().lower() != 'real':
        return None
    key = env.get('DEEPSEEK_API_KEY', '').strip()
    if not key:
        return None
    model = model_for('memory', env)
    return MemoryModel(api_key=key, model=model, cache_dir=cache_dir, http_client=http_client)
