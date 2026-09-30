"""Cache-only all-probe equality through core's DraftTurn conversion."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from Conversation_Memory.engine.embed import resolve_embedder
from Conversation_Memory.facade import MemoryV1
from core.contracts import DraftTurn
from core.memory_adapter import draft_turns_to_memory
from lab.llm import CachedLLM, RealLLM
from lab.replay import run_set


def verify(name: str, out: Path) -> dict:
    embedder = resolve_embedder('bge-m3', False, ROOT/'cache'/'embed',
                                hf_home=ROOT/'cache'/'hf')
    llm = CachedLLM(RealLLM('deepseek-flash'), ROOT/'cache'/'llm', True)
    facade = MemoryV1(out, embedder_factory=lambda: embedder)
    count = 0
    mismatches = []
    def observe(store, embedding, cfg, hot, probe, now, row):
        nonlocal count
        facade.config = cfg
        facade._local.store = store
        facade._local.embedder = embedding
        drafts = [DraftTurn(role=t.role, text=t.text, turn_id=t.id,
                            created_at=t.time, source_timezone='Asia/Shanghai',
                            timezone_source='configured_default') for t in hot.hot]
        converted = draft_turns_to_memory(drafts, now)
        actual = facade.recall_and_render(probe['message'], converted, now).block
        if actual != row['rendered']:
            mismatches.append({'probe_id':row['probe_id'],
                               'variant_days':row['variant_days']})
        count += 1
    run_set(name, 'P8', llm, embedder, out, probe_observer=observe)
    return {'set':name,'probes':count,'mismatches':mismatches,
            'new_model_calls':0}


if __name__ == '__main__':
    root = ROOT/'runs'/'memory_v1'
    results = [verify(name, root/f'consistency_{name}_P8')
               for name in ('dev_a', 'dev_b')]
    print(json.dumps(results, ensure_ascii=False))
    raise SystemExit(any(row['mismatches'] for row in results))
