"""DeepSeek evidence audit using the v3 five-category rubric."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from judge.audit_memories import CATEGORIES, export  # noqa: E402
from lab.llm import CachedLLM, RealLLM  # noqa: E402


def audit_system() -> str:
    old = (ROOT / 'judge' / 'audit_prompt_v1.md').read_text(encoding='utf-8')
    rubric = old.split('本次仓库主人要求由 Codex', 1)[0]
    return rubric + ('\n本轮由 DeepSeek 独立审阅。严格返回 JSON 对象 {"items":['
                     '{"id":"m1","flags":{"fabricated":false,"speaker_reversed":false,'
                     '"overgeneralized":false,"date_error":false,"person_confusion":false},'
                     '"reasons":{"fabricated":"","speaker_reversed":"",'
                     '"overgeneralized":"","date_error":"","person_confusion":""}}]}。'
                     '每个输入 ID 恰好一项。标记为 true 时写一句可核对的理由。')


def validate(text: str, expected: set[str]) -> list[dict]:
    value = text.strip()
    fence = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', value, re.S | re.I)
    if fence:
        value = fence.group(1)
    data = json.loads(value)
    rows = data if isinstance(data, list) else data['items']
    if not isinstance(rows, list) or len(rows) != len(expected) or {r['id'] for r in rows} != expected:
        raise ValueError('audit ids mismatch')
    for row in rows:
        if set(row['flags']) != set(CATEGORIES) or set(row['reasons']) != set(CATEGORIES):
            raise ValueError('audit categories mismatch')
        if any(type(row['flags'][cat]) is not bool or not isinstance(row['reasons'][cat], str) or
               row['flags'][cat] and not row['reasons'][cat].strip() for cat in CATEGORIES):
            raise ValueError('invalid audit flag or reason')
    return rows


def run(run_dir: Path, batch_size: int = 5) -> dict:
    evidence = run_dir / 'audit_evidence_v4.json'
    rows = export(run_dir, evidence)
    batches = [rows[i:i+batch_size] for i in range(0, len(rows), batch_size)]
    print(f'{run_dir.name}: 预计至多 {len(batches)*3} 次审计调用（含重试），约 {sum(len(json.dumps(b,ensure_ascii=False)) for b in batches)//2} 输入 token（字符粗估）', flush=True)
    llm = CachedLLM(RealLLM(), ROOT / 'cache' / 'llm', allow_new={'audit'})
    judged = []
    for batch in batches:
        user = json.dumps(batch, ensure_ascii=False, separators=(',', ':'))
        for attempt in range(3):
            response = llm.complete(system=audit_system(), messages=[{'role': 'user', 'content': user}],
                                    max_tokens=5000, purpose='audit', attempt=attempt)
            try:
                valid = validate(response.text, {r['id'] for r in batch})
                break
            except (ValueError, KeyError, TypeError):
                if attempt == 2:
                    raise
        judged.extend(valid)
    counts = {cat: sum(r['flags'][cat] for r in judged) for cat in CATEGORIES}
    result = {'n': len(judged), 'counts': counts,
              'rates': {cat: counts[cat]/len(judged) if judged else None for cat in CATEGORIES},
              'rows': judged, 'model': llm.model,
              'usage': {'new_calls': llm.new_calls, 'input_tokens': llm.new_input_tokens,
                        'output_tokens': llm.new_output_tokens, 'cache_hits': llm.hits}}
    (run_dir / 'audit_v4.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'n': result['n'], 'counts': counts, **result['usage']}, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('run', type=Path)
    args = ap.parse_args()
    run(args.run)
