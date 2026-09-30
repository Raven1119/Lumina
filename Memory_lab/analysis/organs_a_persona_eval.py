"""Frozen dev_a persona comparison, using the production Mind and Language path.

The prior 46 generated P8 replies are read-only evidence. Every new HTTP
attempt, including invalid judge responses, is reserved in one SQLite ledger.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'Memory_lab')]

from config.lumina import load_config
from core.contracts import DraftTurn
from core.draft_store import HotDraftSummary, JsonlDraftStore
from core.dialogue_io import DialogueIO
from core.message_runtime import MessageRuntime
from Conversation_Memory.answer import fallback_answer_v5, parse_answer_v5_tolerant
from Conversation_Memory.engine.embed import resolve_embedder
from Conversation_Memory.facade import MemoryV1
from Language.channel import LanguageChannel
from Memory_lab.analysis.organs_a_eval import (
    AnswerModel, BudgetExhausted, Calls, EmptyState, EvaluationMismatch,
    ProviderFailure, judge_arm, packets, write,
)
from Mind.runner import DialogueRunner
from Nervous.bus import EventBus
from Nervous.scheduler import DialogueScheduler
from lab.answer import answer_messages, answer_system
from lab.llm import CachedLLM, RealLLM
from lab.replay import run_set

LIMIT = 260
BATCH = 5
PRIOR_ORIGINS = Counter({'new_P8_same_parameters':46, 'archived_P8':14})


def prior_evidence(previous: Path):
    config = json.loads((previous/'evaluation_config.json').read_text())
    live_config = json.loads((ROOT/'Memory_lab/runs/v51/dev_a_P8/config.json').read_text())
    if config['baseline_config'] != live_config or (
        config['answer_max_tokens'], config['temperature'],
        config['thinking'], config['prefill']
    ) != (1000, 0, 'disabled', False):
        raise EvaluationMismatch('previous_P8_configuration_changed')
    rows = json.loads((previous/'answers.json').read_text())
    if len(rows) != 60 or Counter(row['p8_origin'] for row in rows) != PRIOR_ORIGINS:
        raise EvaluationMismatch('previous_P8_sources_changed')
    con = sqlite3.connect('file:'+str(previous/'budget.sqlite')+'?mode=ro&immutable=1', uri=True)
    ledger = {operation:json.loads(request) for operation,request in
              con.execute("SELECT operation,request FROM calls WHERE purpose='p8_missing_answer' AND status='received'")}
    con.close()
    if len(ledger) != 46:
        raise EvaluationMismatch('previous_P8_request_receipts_missing')
    reuse = {}
    for row in rows:
        if row['p8_origin'] != 'new_P8_same_parameters':
            continue
        label = f"{row['id']}+{row['variant']}"
        body = ledger[label+':p8']
        if body['model'] != live_config['llm'] or body['max_tokens'] != 1000 or (
            body['temperature'], body['thinking']
        ) != (0, {'type':'disabled'}) or body['messages'][-1]['role'] != 'user':
            raise EvaluationMismatch('previous_P8_request_parameters_changed')
        reuse[label] = row['p8']
    return live_config, reuse


class TimedModel(AnswerModel):
    def __init__(self, calls, prefix):
        super().__init__(calls, prefix)
        self.durations = {'mind':[], 'language':[]}

    def complete_answer(self, system, messages):
        start = time.monotonic()
        try:
            return super().complete_answer(system, messages)
        finally:
            self.durations['mind'].append(round((time.monotonic()-start)*1000, 2))

    def complete_text(self, system, messages):
        start = time.monotonic()
        try:
            return super().complete_text(system, messages)
        finally:
            self.durations['language'].append(round((time.monotonic()-start)*1000, 2))


def answer_arm(out: Path, calls: Calls, previous: Path):
    config, reuse = prior_evidence(previous)
    embed = resolve_embedder('bge-m3', False, ROOT/'Memory_lab/cache/embed',
                             hf_home=ROOT/'Memory_lab/cache/hf')
    cached = CachedLLM(RealLLM(config['llm']), ROOT/'Memory_lab/cache/llm', True)
    persona = (ROOT/'prompts/chat_background.md').read_text(encoding='utf-8')
    completed = []

    def observe(store, embedding, cfg, hot, probe, now, row):
        label = row['probe_id']+'+'+str(row['variant_days'])
        dest = out/'answers'/f'{label}.json'
        if dest.exists():
            saved = json.loads(dest.read_text())
            if label in reuse and saved['p8'] != reuse[label]:
                raise EvaluationMismatch('saved_P8_changed')
            completed.append(saved)
            return
        if label in reuse:
            baseline, origin, p8_ms = reuse[label], 'reused_recent_P8_46', None
        else:
            system = answer_system(now, row['rendered'])
            messages = answer_messages(probe, hot.hot, hot.summary, hot.summary_until, now)
            start = time.monotonic()
            raw = calls.complete(label+':p8_current', 'p8_regenerated', system, messages)
            p8_ms = round((time.monotonic()-start)*1000, 2)
            baseline, _, found, *_ = parse_answer_v5_tolerant(raw)
            if not found:
                baseline = fallback_answer_v5(raw)
            origin = 'regenerated_current_P8_14'

        folder = out/'thoughts'/label
        facade = MemoryV1(folder/'unused-memory', embedder_factory=lambda:embedding, config=cfg)
        facade._local.store, facade._local.embedder = store, embedding
        facade.record_trace = lambda *_: None
        original_recall = facade.recall_and_render
        first = [True]
        def checked_recall(*args):
            recalled = original_recall(*args)
            if first[0]:
                first[0] = False
                if recalled.block != row['rendered']:
                    raise EvaluationMismatch('P8_memory_changed_before_model_call')
            return recalled
        facade.recall_and_render = checked_recall
        drafts = JsonlDraftStore(folder/'hot.jsonl')
        if not (folder/'bus.sqlite').exists():
            summary = (HotDraftSummary(hot.summary, 1, 1, '2026-01-01T00:00:00.000000Z',
                        hot.summary_until.isoformat() if hot.summary_until else None)
                       if hot.summary else None)
            drafts.replace_contents_atomically(summary, [
                DraftTurn(role=t.role, text=t.text, turn_id=t.id, created_at=t.time,
                          source_timezone='Asia/Shanghai', timezone_source='configured_default')
                for t in hot.hot])
        class Clock:
            def now(self): return now
        model = TimedModel(calls, label)
        runtime = MessageRuntime(hot_store=drafts, model_client=model,
                                 chat_background=persona, memory=facade, clock=Clock(),
                                 default_timezone='Asia/Shanghai')
        io = DialogueIO(runtime, threading.Lock())
        bus = EventBus(folder/'bus.sqlite')
        settings = load_config(overrides={'mind':{'protocol':'a2'},
                   'language':{'render':'always'},
                   'workspace':{'path':str(folder/'empty-workspace')}})
        channel = LanguageChannel(bus, io, settings)
        scheduler = DialogueScheduler(bus, None, channel, lambda _:None)
        scheduler.runner = DialogueRunner(bus, io, settings, EmptyState(), scheduler.emit)
        bus.publish(label, 'user.message', {'message':probe['message']})
        scheduler.drain_once()
        prepared = bus.get(label+':prepared')
        if prepared['read']['block'] != row['rendered'] or prepared['state_block'] != '':
            raise EvaluationMismatch('P8_memory_or_empty_state_changed')
        speeches = [bus.get(f'{label}:{step}:0:done')
                    for step in range(1, settings['mind']['dialogue_max_steps']+1)]
        speeches = [speech for speech in speeches if speech is not None]
        if not speeches or len(model.durations['language']) != len(speeches):
            raise EvaluationMismatch('every_speech_must_be_rendered')
        if any(s['rephrase_status'] not in ('unchanged','rephrased') for s in speeches):
            raise EvaluationMismatch('language_result_missing')
        result = {
            'probe':probe, 'id':row['probe_id'], 'variant':row['variant_days'],
            'time':row['time'], 'category':row['category'], 'soft':row['soft'],
            'p8':baseline, 'p8_origin':origin,
            'mind':'\n'.join(s['mind_text'] for s in speeches),
            'a2':'\n'.join(s['final_text'] for s in speeches),
            'rephrase':True, 'choices':[True for _ in speeches],
            'speeches':speeches, 'hot_ids':[t.id for t in hot.hot],
            'memory_sha256':hashlib.sha256(row['rendered'].encode()).hexdigest(),
            'p8_ms':p8_ms, 'mind_ms':model.durations['mind'],
            'language_ms':model.durations['language'],
        }
        write(dest, result)
        completed.append(result)
        bus.close()
        print('answer', label, len(completed), 'attempts',
              sum(group['attempts'] for group in calls.summary().values()), flush=True)

    replay = out/'replay'
    index = 1
    while replay.exists():
        replay = out/f'replay_{index}'
        index += 1
    run_set('dev_a', 'P8', cached, embed, replay, probe_observer=observe)
    if len(completed) != 60 or Counter(r['p8_origin'] for r in completed) != Counter({
        'reused_recent_P8_46':46, 'regenerated_current_P8_14':14}):
        raise EvaluationMismatch('answer_arm_incomplete')
    write(out/'answers.json', completed)
    write(out/'evaluation_config.json', {
        'previous_source':str(previous), 'baseline_config':config,
        'p8_reused':46, 'p8_regenerated':14, 'mind_protocol':'a2',
        'language_render':'always', 'model':config['llm'], 'temperature':0,
        'thinking':'disabled', 'max_tokens':1000, 'prefill':False,
        'set':'dev_a', 'probes':60,
    })
    return completed


def validate_persona(raw, items):
    text = raw.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', text, flags=re.S|re.I)
    if fenced:
        text = fenced.group(1)
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('persona_items_missing')
    rows = data['items']
    expected = {item['id'] for item in items}
    if len(rows) != len(items) or {r.get('id') for r in rows if isinstance(r, dict)} != expected:
        raise ValueError('persona_ids_mismatch')
    for row in rows:
        if row.get('更像') not in ('X','Y','平') or not isinstance(row.get('理由'), str):
            raise ValueError('persona_result_invalid')
    return rows


def persona_arm(out: Path, calls: Calls, rows, comparison: str):
    if comparison not in ('p8','mind'):
        raise ValueError('invalid_persona_comparison')
    packet, _ = packets(rows)
    rng = random.Random('organs-a-persona-'+comparison)
    second = 'P8' if comparison == 'p8' else 'Mind'
    key, first = {}, []
    for item, source in zip(packet, rows):
        label = {'Final':source['a2'], second:source['p8' if comparison=='p8' else 'mind']}
        x, y = ('Final', second) if rng.random() < 0.5 else (second, 'Final')
        key[item['id']] = {'X':x, 'Y':y, 'probe':source['id'], 'variant':source['variant']}
        first.append({'id':item['id'], '时间':item['时间'], '说明':item['说明'],
                      '对话上下文':item['证据轮次'], '当前消息':item['他发来'],
                      'X':label[x], 'Y':label[y]})
    target = out/f'persona_{comparison}'
    target.mkdir(parents=True, exist_ok=True)
    write(target/'blind_key.json', key)
    prompt = (ROOT/'Memory_lab/judge/persona_prompt_organs_a.md').read_text(encoding='utf-8').strip()
    persona = (ROOT/'prompts/chat_background.md').read_text(encoding='utf-8')
    system = prompt+'\n\n'+persona
    invalid = []
    all_results = []
    for order in (1,2):
        items = first if order==1 else [{**item,'X':item['Y'],'Y':item['X']} for item in first]
        write(target/f'blind_packet_{order}.json', items)
        results = []
        for start in range(0, len(items), BATCH):
            batch = items[start:start+BATCH]
            skeleton = {'items':[{'id':item['id'], '更像':'X 或 Y 或 平', '理由':'一句话'}
                                 for item in batch]}
            for attempt in range(3):
                payload = {'items':batch,
                    '批量输出格式':'只返回一个 JSON 对象，顶层 items 数组逐项对应输入；每项保留 id、更像、理由。更像只能是 X、Y、平。',
                    'output_skeleton':skeleton}
                if attempt:
                    payload['格式提醒'] = ('前一次输出未通过格式校验。必须保留每个输入 id，'
                                         '输出同样数量的 items；不要省略顶层 items 数组。')
                if attempt==2:
                    payload['再次提醒'] = '每项只填 id、更像、理由，不要输出数组以外的文字。'
                user = json.dumps(payload, ensure_ascii=False, separators=(',',':'))
                operation = f'persona_{comparison}:{order}:{start}:{attempt}'
                raw = calls.complete(operation, f'persona_{comparison}', system,
                                     [{'role':'user','content':user}], 6000)
                try:
                    results.extend(validate_persona(raw, batch))
                    break
                except (ValueError,TypeError,KeyError,AttributeError) as error:
                    invalid.append({'operation':operation,'error':type(error).__name__+':'+str(error)})
                    write(target/'invalid.json', invalid)
            else:
                raise ProviderFailure('persona_invalid_after_three_attempts')
            print('persona', comparison, order, start+len(batch), '/', len(items), flush=True)
        write(target/f'judge_{order}.json', results)
        all_results.append({r['id']:r for r in results})
    merged = []
    for iid, labels in key.items():
        preferences = []
        reasons = []
        for order, results in enumerate(all_results, 1):
            result = results[iid]
            mapping = labels if order==1 else {'X':labels['Y'], 'Y':labels['X']}
            preferences.append('tie' if result['更像']=='平' else mapping[result['更像']])
            reasons.append(result['理由'])
        merged.append({**labels,'prefer':preferences,'reason':reasons})
    counts = Counter(
        row['prefer'][0] if row['prefer'][0]==row['prefer'][1] else 'split'
        for row in merged)
    summary = {'comparison':comparison,'n':len(merged),
               'pairs':{'Final':counts['Final'],second:counts[second],
                        'tie':counts['tie'],'split':counts['split']},
               'invalid_attempts':len(invalid),'rows':merged}
    write(target/'summary.json', summary)
    return summary


def adoption_gate(original, persona):
    if original['overall']['n'] != 60 or persona['n'] != 60:
        raise EvaluationMismatch('adoption_requires_60_probes')
    pairs = original['overall']['pairs']
    voice_pairs = persona['pairs']
    scored = [row for row in original['rows'] if all(
        len(row['judgments'][label]) == 1 for label in ('P8','A2'))]
    def flags(label):
        judgments = [row['judgments'][label][0] for row in scored]
        return {'wrong':sum(int(j['wrong']) for j in judgments),
                'should_not':sum(int(any(value==1 for value in j['should_not']))
                                  for j in judgments)}
    p8, final = flags('P8'), flags('A2')
    conditions = {
        'original_loss_minus_win_lte_3':pairs['P8']-pairs['A2'] <= 3,
        'wrong_delta_lte_2':final['wrong']-p8['wrong'] <= 2,
        'should_not_delta_lte_2':final['should_not']-p8['should_not'] <= 2,
        'persona_loss_lte_win':voice_pairs['P8'] <= voice_pairs['Final'],
    }
    return {'default_protocol':'a2' if all(conditions.values()) else 'a1',
            'language_render':'always','conditions':conditions,
            'original_pairs':pairs,'persona_pairs':voice_pairs,
            'n_scored_original_first_pass':len(scored),
            'p8_flags':p8,'final_flags':final,
            'wrong_delta':final['wrong']-p8['wrong'],
            'should_not_delta':final['should_not']-p8['should_not']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--previous-eval', type=Path, required=True)
    parser.add_argument('--stage', choices=('answer','original_judge','persona_p8',
                        'persona_mind','all'), default='all')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    config, _ = prior_evidence(args.previous_eval)
    calls = Calls(args.out/'budget.sqlite', config['llm'], limit=LIMIT)
    try:
        rows = (answer_arm(args.out, calls, args.previous_eval)
                if args.stage in ('answer','all')
                else json.loads((args.out/'answers.json').read_text()))
        # The frozen v2 aggregator also reports how often Language was used.
        # Add its old bookkeeping fields to receipts from an interrupted run.
        for row in rows:
            row.setdefault('rephrase',True)
            row.setdefault('choices',[True for _ in row['speeches']])
        write(args.out/'answers.json',rows)
        if args.stage in ('original_judge','all'):
            judge_arm(args.out, calls, rows)
        if args.stage in ('persona_p8','all'):
            persona = persona_arm(args.out, calls, rows, 'p8')
            original = json.loads((args.out/'summary.json').read_text())
            write(args.out/'decision.json', adoption_gate(original, persona))
        if args.stage in ('persona_mind','all'):
            used = sum(group['attempts'] for group in calls.summary().values())
            if LIMIT-used < 2*((len(rows)+BATCH-1)//BATCH):
                write(args.out/'persona_mind_skipped.json',
                      {'reason':'insufficient_remaining_attempt_budget','used':used})
            else:
                persona_arm(args.out, calls, rows, 'mind')
    finally:
        write(args.out/'cost.json', calls.summary())


if __name__ == '__main__':
    main()
