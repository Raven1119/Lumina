"""Generate A/A answers after shuffling entries inside each memory section."""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lab.answer import answer_system  # noqa: E402
from lab.llm import CachedLLM, RealLLM  # noqa: E402


def shuffle_sections(block: str, seed: int) -> str:
    """Keep headers and section positions; reorder only their bullet entries."""
    lines = block.splitlines()
    output: list[str] = []
    section: list[str] = []
    section_index = 0

    def flush() -> None:
        nonlocal section_index
        if section:
            random.Random(f'{seed}:{section_index}').shuffle(section)
            output.extend(section)
            section.clear()
            section_index += 1

    for line in lines:
        if line.startswith('【') and line.endswith('】'):
            flush()
            output.append(line)
        elif line.startswith('- '):
            section.append(line)
        else:
            flush()
            output.append(line)
    flush()
    return '\n'.join(output) + ('\n' if block.endswith('\n') else '')


def primary(row: dict) -> bool:
    return not row['not_scored'] and (row['variant_days'] == 0 or
        row['variant_days'] == 60 and row['category'] in ('淡忘', '保留'))


def run(set_name: str, seed: int) -> None:
    source = ROOT / 'answers_v3' / f'answer_{set_name}_P6u'
    output = ROOT / 'answers_v4' / f'answer_{set_name}_P6u_noise{seed}'
    output.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in (source / 'probes.jsonl').read_text(encoding='utf-8').splitlines()]
    rows = [r for r in rows if primary(r)]
    if len(rows) != 35:
        raise ValueError(f'{set_name}: expected 35 primary rows, got {len(rows)}')
    print(f'{set_name} seed {seed}: 预计最多 {len(rows)} 次新调用，约 {sum(len(r["rendered"])+sum(len(m["content"]) for m in r["answer"]["context"]["messages"]) for r in rows)//2} 输入 token（字符粗估）', flush=True)
    llm = CachedLLM(RealLLM(), ROOT / 'cache' / 'llm', allow_new={'answer_noise'})
    for row in rows:
        block = shuffle_sections(row['rendered'], seed)
        context = row['answer']['context']
        system = answer_system(datetime.fromisoformat(row['time']), block, 'v2')
        result = llm.complete(system=system, messages=context['messages'], max_tokens=1000,
                              purpose='answer_noise')
        row['rendered'] = block
        row['answer'] = {'text': result.text, 'cache_key': result.cache_key,
                         'cache_hit': result.cache_hit, 'context': context}
    (output / 'probes.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows), encoding='utf-8')
    config = json.loads((source / 'config.json').read_text(encoding='utf-8'))
    config['noise_seed'] = seed
    config['noise_source'] = str(source)
    (output / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    status = {'purpose': 'answer_noise', 'new_model_calls': llm.new_calls,
              'new_input_tokens': llm.new_input_tokens, 'new_output_tokens': llm.new_output_tokens,
              'cache_hits': llm.hits}
    (output / 'run_status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(status, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--set', choices=('dev_a', 'dev_b'), required=True)
    ap.add_argument('--seed', type=int, choices=(1, 2), required=True)
    args = ap.parse_args()
    run(args.set, args.seed)
