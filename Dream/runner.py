"""Explicit memory-v1 Dream and read-only inspection commands."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from Conversation_Memory.engine.clock import TZ
from Conversation_Memory.engine.embed import resolve_embedder
from Conversation_Memory.facade import MemoryV1
from Conversation_Memory.model_client import build_memory_model_from_env
from Conversation_Memory.engine.config import preset
from model_policy import model_for
from core.cold_draft_store import ColdDraftStore
from core.env_loader import load_env_file
from core.memory_adapter import draft_turns_to_memory

ROOT = Path(__file__).resolve().parents[1]


def _service_stopped() -> bool:
    """Refuse cursor changes if this workspace has a known app server process."""
    proc = Path('/proc')
    if not proc.is_dir():
        return False
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmd = (entry/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='ignore')
            cwd = (entry/'cwd').resolve()
        except OSError:
            continue
        if (('uvicorn' in cmd or 'gunicorn' in cmd or 'core.main' in cmd)
                and ('core.main' in cmd or 'core/main' in cmd)
                and (cwd == ROOT or ROOT in cwd.parents)):
            return False
    return True


def build_default_runner(*, memory_dir: Path | None = None,
                         cold_path: Path | None = None):
    load_env_file(ROOT/'.env.local', override=False)
    path = memory_dir or Path(os.environ.get('LUMINA_MEMORY_DIR', 'data/memory_v1'))
    cold = ColdDraftStore(cold_path or Path(os.environ.get(
        'LUMINA_DRAFT_STORE_PATH', 'data/draft/hot_drafts.jsonl')).parent/'cold_drafts.jsonl')
    model = build_memory_model_from_env(path/'cache')
    def embedder():
        override = os.environ.get('LUMINA_EMBED_MODEL_PATH')
        return resolve_embedder('bge-m3', False, path/'embed_cache',
                                hf_home=ROOT/'Memory_lab'/'cache'/'hf',
                                model_path=Path(override) if override else None)
    memory = MemoryV1(path, embedder_factory=embedder,
                      model_factory=(lambda: model) if model else None,
                      config=replace(preset('P8', 'bge-m3'),
                                     llm_model=model_for('memory')))
    return memory, cold, model


def _cold_turns(cold: ColdDraftStore):
    source = cold.list_all_turns()
    now = datetime.now(TZ)
    return draft_turns_to_memory(source, now, skip_untimed=True), sum(
        turn.created_at is None for turn in source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Memory v1 Dream and inspection')
    parser.add_argument('command', choices=('run', 'inspect', 'rebuild', 'cursor-end'))
    parser.add_argument('--memory-dir', type=Path)
    parser.add_argument('--cold-path', type=Path)
    args = parser.parse_args(argv)
    if args.command in {'rebuild', 'cursor-end'} and not _service_stopped():
        parser.error('stop the Chat service before changing the Cold cursor')
    memory, cold, model = build_default_runner(memory_dir=args.memory_dir,
                                              cold_path=args.cold_path)
    if args.command == 'inspect':
        print(json.dumps(memory.inspect(datetime.now(TZ)), ensure_ascii=False))
        return 0
    turns, skipped = _cold_turns(cold)
    if args.command == 'cursor-end':
        memory.set_cursor_to_end(turns)
        print(json.dumps({'status':'cursor_at_end','turns':len(turns),
                          'untimed_skipped':skipped}))
        return 0
    if args.command == 'rebuild':
        if model is None:
            parser.error('real memory model is required for rebuild')
        estimated = (sum(len(t.text) for t in turns)*2)//3
        if estimated > 500_000:
            parser.error('estimated input tokens exceed 500000; cursor unchanged')
        memory.set_cursor_to_start()
        outcomes = []
        while memory.unintegrated_turn_count(turns) >= 2:
            window = memory._next_window(turns)
            if not window:
                break
            result = memory.dream_once(turns, manual=True, now=window[-1].time)
            outcomes.append(result.status)
            if result.status != 'applied' or model.input_tokens > 500_000:
                break
        print(json.dumps({'outcomes':outcomes,'input_tokens':model.input_tokens,
                          'output_tokens':model.output_tokens,'calls':model.calls,
                          'untimed_skipped':skipped}))
        return 0 if all(status == 'applied' for status in outcomes) else 1
    result = memory.dream_once(turns, manual=True)
    print(json.dumps(result.__dict__, ensure_ascii=False))
    return 0 if result.status in {'applied','no_window'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
