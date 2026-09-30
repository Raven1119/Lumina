"""Generate competitive 8/12/16-week filler with the v3 validator and schedule."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval_set.fillers.generate import SCHEDULE, _validate  # noqa: E402
from eval_set.build import assign_ids_and_times, parse_script  # noqa: E402
from lab.llm import CachedLLM, RealLLM  # noqa: E402

TOPICS = ('新人物与关系、带日期的计划', '工作或学习新进展、计划变化',
          '家庭或宠物事件、林素的具体建议', '建议后来被修正、这一周的结局')


def _one_session(text: str, sid: str, day: str, hhmm: str) -> str:
    """Keep the first six exchanges if the model repeats the header per exchange."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    header = f'## {sid} {day} {hhmm}'
    if not lines or lines[0] != header:
        return text
    turns = [line for line in lines[1:] if not line.startswith(f'## {sid} ')]
    if len(turns) > 12 and all(line.startswith('U: ' if i % 2 == 0 else 'L: ')
                                for i, line in enumerate(turns[:12])):
        return '\n'.join([header, *turns[:12]])
    return text


def _prefix_sessions(path: Path, expected: int) -> list[str]:
    body = path.read_text(encoding='utf-8').split('\n\n', 1)[1].strip()
    sessions = body.split('\n\n')
    if len(sessions) != expected:
        raise ValueError(f'{path}: expected {expected} prefix sessions, got {len(sessions)}')
    return sessions


def generate(name: str, weeks: int) -> Path:
    if name not in ('dev_a', 'dev_b') or weeks not in (8, 12, 16):
        raise ValueError('unsupported filler target')
    built = ROOT / 'eval_set' / 'built'
    gold = json.loads((built / f'{name}.gold.json').read_text(encoding='utf-8'))
    dialogue = json.loads((built / f'{name}.dialogue.json').read_text(encoding='utf-8'))
    earliest = min(datetime.fromisoformat(p['time']) for p in gold['probes'])
    before = [s for s in dialogue['sessions'] if datetime.fromisoformat(s['turns'][-1]['time']) < earliest]
    first_day = (datetime.fromisoformat(before[-1]['turns'][-1]['time']) + timedelta(days=1)).date()
    forbidden = json.loads((Path(__file__).parent / 'forbidden_words.json').read_text(encoding='utf-8'))[name]
    system = (Path(__file__).parent / 'filler_prompt_v2.md').read_text(encoding='utf-8')
    llm = CachedLLM(RealLLM(), ROOT / 'cache' / 'llm', allow_new={'filler_v2'})
    target = Path(__file__).parent / f'{name}_{weeks}w_v2.txt'
    print(f'{name} {weeks}w: 预计至多 {weeks*4*5} 次新调用（每会话最多 5 次），约 {weeks*4*1500} 输入 token（粗估）', flush=True)
    prefix_weeks = (12 if weeks == 16 and (Path(__file__).parent / f'{name}_12w_v2.txt').is_file()
                    else 8 if weeks > 8 else 0)
    prefix = Path(__file__).parent / f'{name}_{prefix_weeks}w_v2.txt'
    sessions = _prefix_sessions(prefix, prefix_weeks * 4) if prefix_weeks else []
    for week in range(prefix_weeks, weeks):
        for slot, (offset, hhmm) in enumerate(SCHEDULE):
            sid = f'f{week*4+slot+1:02d}'
            day = (first_day + timedelta(days=week*7+offset)).isoformat()
            user = json.dumps({'session_id': sid, 'date': day, 'start_time': hhmm,
                               'week': week+1, 'slot': slot+1, 'topic': TOPICS[slot],
                               'recent_sessions': sessions[-3:], 'forbidden_words': forbidden},
                              ensure_ascii=False)
            for attempt in range(5):
                response = llm.complete(system=system, messages=[{'role': 'user', 'content': user}],
                                        max_tokens=3000, purpose='filler_v2', attempt=attempt)
                try:
                    segment = _validate(_one_session(response.text, sid, day, hhmm),
                                        sid, day, hhmm, forbidden)
                    break
                except ValueError as exc:
                    error = str(exc)
            else:
                raise ValueError(f'{sid}: generation failed: {error}')
            sessions.append(segment)
    target.write_text(f'@id: {name}_long{weeks}w_v2\n@split: dev\n\n' +
                      '\n\n'.join(sessions) + '\n', encoding='utf-8')
    _, parsed = parse_script(target)
    errors = assign_ids_and_times(f'{name}_long{weeks}w_v2', parsed)
    if errors:
        raise ValueError(errors)
    print(json.dumps({'sessions': len(parsed), 'new_calls': llm.new_calls,
                      'input_tokens': llm.new_input_tokens, 'output_tokens': llm.new_output_tokens,
                      'cache_hits': llm.hits}, ensure_ascii=False), flush=True)
    return target


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('name', choices=('dev_a', 'dev_b'))
    ap.add_argument('--weeks', type=int, choices=(8, 12, 16), required=True)
    args = ap.parse_args()
    generate(args.name, args.weeks)
