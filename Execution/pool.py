"""Durable, bounded helper pool around Execution V2.

The bus owns transport; ExecutionOrgan owns each action log and unknown-outcome
protection. One worker thread owns each live ExecutionOrgan instance.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from Execution.deepseek_model import DeepSeekModel
from Execution.execution import FileContentEquals
from Execution.organ import ExecutionOrgan
from Execution.sandbox import DockerIPython


AUTO_REPLY = '她暂时没有答复，请按你的最佳判断继续，并在回报里说明。'
TERMINAL = {'已交回', '已完成', '失败', '被拉闸', '已取消'}


def _time():
    return datetime.now(timezone.utc)


def _contract_text(contract):
    return '\n'.join(f'{label}：{contract[key]}' for key, label in (
        ('目标', '目标'), ('理由', '理由'), ('验收', '怎样算做完'), ('背景', '背景')))


class _HelperModel:
    """Expose the configured Execution model and the pending guard signal."""
    def __init__(self, owner, helper_id, prompt, base=None):
        self.owner, self.helper_id = owner, helper_id
        self.base = base or DeepSeekModel(helper_prompt=prompt)
        self.identifier = self.base.identifier
        self.tool_contracts = self.base.tool_contracts
        self.allow_model_spawn_with_ipython = True

    def decide(self, request):
        from dataclasses import replace
        if ('claim_complete()' in request.available_tools
                and not any(tool.startswith('ask_mind(') for tool in request.available_tools)):
            request = replace(request, available_tools=request.available_tools +
                              ('ask_mind(question: str) -> queued',))
        row = self.owner.get(self.helper_id)
        if row and row.get('return_requested'):
            request = replace(request, context=request.context + '\n\n系统事件 RETURN_REQUESTED（请回报）：'
                              + row['return_requested'] + '。立即停止尝试，简短回报目前结果和产出。')
        decision=self.base.decide(request)
        self.owner._note_provider(self.helper_id, decision)
        return decision


class HelperPool:
    def __init__(self, bus, config, *, workspace=None, model_factory=None,
                 organ_factory=None, clock=_time):
        self.bus, self.config = bus, config
        self.workspace = Path(workspace or config['workspace']['path']).resolve()
        self.model_factory, self.organ_factory = model_factory, organ_factory
        self.clock = clock
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._threads = {}
        self._organs = {}
        self._stopping = False

    def _key(self, helper_id):
        return 'helper:' + helper_id

    def get(self, helper_id):
        return self.bus.get(self._key(helper_id), state=True)

    def question_stale_reason(self, event):
        """Check identity and queued dispositions, not question wording."""
        with self._changed:
            helper = event['body']['helper']
            row = self.get(helper)
            if row is None:
                return 'helper_missing'
            if row['status'] in TERMINAL:
                return 'helper_terminal'
            question = row.get('question')
            if not question:
                return 'question_not_pending'
            if question.get('event_id') and question['event_id'] != event['id']:
                return 'question_superseded'
            if row.get('cancel_requested'):
                return 'question_cancelled'
            for action in self.bus.pending('execution'):
                if action['body'].get('helper') == helper and action['kind'] in ('mind.reply', 'mind.cancel'):
                    return 'answer_queued' if action['kind'] == 'mind.reply' else 'cancel_queued'
            return None

    def _save(self, row):
        self.bus.put(self._key(row['id']), row, state=True)
        self._changed.notify_all()
        return row

    def _note_provider(self, helper_id, decision):
        with self._changed:
            row=self.get(helper_id)
            if row is None:return
            row=dict(row)
            raw=getattr(decision,'raw_provider_response',None)
            usage=raw.get('usage',{}) if isinstance(raw,dict) else {}
            row['model_calls']=row.get('model_calls',0)+1
            row['input_tokens']=row.get('input_tokens',0)+int(usage.get('prompt_tokens') or 0)
            row['output_tokens']=row.get('output_tokens',0)+int(usage.get('completion_tokens') or 0)
            self._save(row)

    def _record_usage(self,row):
        self.bus.record_usage('helper:'+row['id'],{
            'type':'helper','result':row.get('outcome','未说明'),
            'status':row['status'],'decision_calls':row.get('calls',0),
            'guard_triggers':int(bool(row.get('return_requested'))),
            'auto_replies':sum(item.get('answer_source')=='自动答复' for item in row.get('qa',[])),
            'calls':{'helper':row.get('model_calls',0)},
            'tokens':{'helper':{'input':row.get('input_tokens',0),
                                'output':row.get('output_tokens',0)}}})

    def all(self):
        rows = self.bus.conn.execute("SELECT id,body,digest FROM state WHERE id LIKE 'helper:%'").fetchall()
        from Nervous.bus import unpack
        return sorted((unpack(row['body'], row['digest']) for row in rows),
                      key=lambda row: row['created_at'])

    def visible(self):
        cutoff = self.clock() - timedelta(hours=self.config['execution']['done_keep_hours'])
        return [row for row in self.all() if row['status'] not in TERMINAL
                or datetime.fromisoformat(row.get('finished_at') or row['created_at']) >= cutoff]

    def _task_dir(self, helper_id):
        if not helper_id.isalnum() or len(helper_id) > 32:
            raise ValueError('invalid_helper_id')
        root = self.workspace / 'tasks'
        root.mkdir(parents=True, exist_ok=True)
        directory = root / helper_id
        if directory.is_symlink():
            raise ValueError('symlink_task_directory')
        directory.mkdir(exist_ok=True)
        if directory.resolve() != directory or not directory.is_relative_to(root):
            raise ValueError('invalid_task_directory')
        directory.chmod(0o777)  # Docker's unprivileged uid may write only here.
        return directory

    def _register(self, event):
        body = event['body']
        helper_id, contract = body['helper'], body['contract']
        if set(contract) != {'目标', '理由', '验收', '背景'} or not all(
                isinstance(value, str) and value.strip() for value in contract.values()):
            raise ValueError('invalid_helper_contract')
        if self.get(helper_id) is not None:
            return
        directory = self._task_dir(helper_id)
        self._save({'id': helper_id, 'contract': contract, 'goal': contract['目标'],
                    'status': '排队中', 'created_at': self.clock().isoformat(),
                    'task_dir': str(directory), 'pending_reply': None,
                    'question': None, 'seen_questions': [], 'qa': [],
                    'outcome': '未说明', 'outcome_note': '', 'return_requested': None,
                    'grace_steps': 0, 'calls': 0, 'model_calls': 0,
                    'input_tokens': 0, 'output_tokens': 0, 'file_digests': [],
                    'finished_at': None, 'report': ''})

    def handle(self, event):
        interrupt = None
        with self._changed:
            body = event['body']
            helper_id = body.get('helper')
            if event['kind'] == 'mind.spawn':
                self._register(event)
            else:
                row = self.get(helper_id)
                if row and row['status'] not in TERMINAL:
                    row = dict(row)
                    if event['kind'] == 'mind.reply':
                        if row.get('question'):
                            for item in reversed(row.setdefault('qa', [])):
                                if item.get('answer') is None:
                                    item['answer'] = body['content']
                                    item['answer_source'] = body.get('source', '她答的')
                                    item['answered_at'] = self.clock().isoformat()
                                    break
                        row['pending_reply'] = body['content']
                        row['question'] = None
                        row['status'] = '进行中'
                    elif event['kind'] == 'mind.hold':
                        row['status'] = '在等答复'
                    elif event['kind'] == 'mind.cancel':
                        row['cancel_requested'] = True
                        interrupt = self._organs.get(helper_id)
                    elif event['kind'] == 'guard.return':
                        row['return_requested'] = body['reason']
                    self._save(row)
            self.bus.ack(event['id'])
            self._pump()
        if interrupt is not None:
            try:
                interrupt.interrupt()
            except (ValueError, RuntimeError):
                pass

    def _pump(self):
        active = sum(thread.is_alive() for thread in self._threads.values())
        for row in self.all():
            if active >= self.config['execution']['max_concurrent'] or self._stopping:
                break
            if row['status'] != '排队中' or row['id'] in self._threads:
                continue
            row = dict(row); row['status'] = '进行中'; self._save(row)
            thread = threading.Thread(target=self._run, args=(row['id'],),
                                      name='lumina-helper-'+row['id'], daemon=True)
            self._threads[row['id']] = thread
            thread.start(); active += 1

    def start(self):
        with self._changed:
            self._stopping = False
            for row in self.all():
                if row['status'] in TERMINAL:
                    report_id=row['id']+':report'
                    if self.bus.conn.execute('SELECT 1 FROM events WHERE id=?',(report_id,)).fetchone() is None:
                        self.bus.finish_helper(row['id'], row, self._report_body(row))
                    self._record_usage(row)
                if row['status'] in ('进行中', '在等答复'):
                    row = dict(row); row['status'] = '排队中'; self._save(row)
            self._pump()

    def stop(self):
        with self._changed:
            self._stopping = True
            self._changed.notify_all()
            threads = list(self._threads.values())
        for thread in threads:
            thread.join()

    def _organ(self, helper_id):
        row = self.get(helper_id)
        directory = Path(row['task_dir'])
        prompt_path = Path(__file__).resolve().parents[1] / 'prompts/helper.md'
        prompt = prompt_path.read_text(encoding='utf-8').strip().format(task_dir=f'/workspace/tasks/{helper_id}')
        model = self.model_factory(helper_id, prompt) if self.model_factory else _HelperModel(self, helper_id, prompt)
        control = DockerIPython(self.workspace, task_directory=directory)
        factory = self.organ_factory or ExecutionOrgan
        state_dir = self.bus.path.parent / 'helpers' / helper_id
        return factory(workspace=directory, event_log_path=state_dir/'events.jsonl',
                       checkpoint_path=state_dir/'checkpoint.json',
                       max_decisions=self.config['execution']['helper_call_cap'] + 4,
                       max_decisions_per_advance=1, max_depth=2,
                       model=model, ipython_control=control)

    def _fingerprint_files(self, directory):
        digest = hashlib.sha256()
        for path in sorted(directory.rglob('*')):
            if path.is_file() and not path.is_symlink() and 'state' not in path.relative_to(directory).parts:
                digest.update(str(path.relative_to(directory)).encode())
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(65536), b''):
                        digest.update(chunk)
        return digest.hexdigest()

    def _note_step(self, row, organ):
        row['calls'] = max(row['calls'], organ.state.decision_count)
        digest = self._fingerprint_files(Path(row['task_dir']))
        row['file_digests'] = (row['file_digests'] + [digest])[-self.config['execution']['repeat_threshold']:]
        tail = organ.repetition_tail()[-self.config['execution']['repeat_threshold']:]
        repeated = (len(tail) == self.config['execution']['repeat_threshold']
                    and len(set((item['code'], json.dumps(item['result'], sort_keys=True)) for item in tail)) == 1
                    and len(set(row['file_digests'])) == 1)
        if not row['return_requested'] and (repeated or row['calls'] >= self.config['execution']['helper_call_cap']):
            reason = '原地打转' if repeated else '调用次数达到上限'
            row['return_requested'] = reason
            self.bus.publish(row['id']+':guard:'+str(row['calls']), 'guard.return',
                             {'helper': row['id'], 'reason': reason})
        elif row['return_requested']:
            row['grace_steps'] += 1
        self._save(row)

    def _questions(self, row, organ):
        for event_id, payload in organ.cognitive_requests():
            if event_id in row['seen_questions']:
                continue
            parsed = json.loads(payload)
            row['seen_questions'].append(event_id)
            question_id = row['id']+':question:'+event_id
            row['question'] = {'text': parsed['question'], 'at': self.clock().isoformat(),
                               'event_id': question_id}
            row.setdefault('qa', []).append({'question': parsed['question'],
                'asked_at': row['question']['at'], 'answer': None,
                'event_id': question_id})
            row['status'] = '在等答复'
            self._save(row)
            self.bus.publish(row['id']+':question:'+event_id, 'agent.question',
                {'helper': row['id'], 'question': parsed['question'],
                 'contract': row['contract']})

    def _outputs(self, directory):
        return [str(path.relative_to(self.workspace)) for path in sorted(directory.rglob('*'))
                if path.is_file() and not path.is_symlink()
                and path.name not in ('.lumina-complete', '.lumina-outcome')
                and 'state' not in path.relative_to(directory).parts][:12]

    @staticmethod
    def _outcome(directory):
        try:
            lines = (directory/'.lumina-outcome').read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeError):
            return '未说明', ''
        label = lines[0].strip() if lines else ''
        if label not in ('完成', '部分完成', '做不到'):
            return '未说明', ''
        return label, '\n'.join(lines[1:]).strip()[:1000]

    @staticmethod
    def _report_body(row):
        return {'helper': row['id'], 'status': row['status'],
                'outcome': row.get('outcome','未说明'), 'summary': row.get('report',''),
                'outputs': row.get('outputs',[]), 'contract': row['contract'],
                'qa': row.get('qa',[])}

    @staticmethod
    def _failure_summary(organ, result):
        for event in reversed(organ._event_log.events):
            if event.event_type != 'MODEL_DECISION':
                continue
            raw = event.payload['frame'].raw_model_response
            if isinstance(raw, dict):
                try:
                    text = raw['choices'][0]['message']['content']
                except (KeyError, IndexError, TypeError):
                    text = None
                if isinstance(text, str) and text.strip():
                    return text.strip()[:1000]
            break
        return result.failure or '帮手未能完成。'

    def _finish(self, row, status, summary):
        if row['status'] in TERMINAL:
            return
        row['status'] = status
        if status == '已交回':
            row['outcome'], row['outcome_note'] = self._outcome(Path(row['task_dir']))
        row['report'] = (row.get('outcome_note') or summary)[:1000]
        row['outputs'] = self._outputs(Path(row['task_dir']))
        row['finished_at'] = self.clock().isoformat()
        for item in row.get('qa', []):
            if item.get('answer') is None:
                item['ended_without_answer'] = True
        row['question'] = None
        self.bus.finish_helper(row['id'], row, self._report_body(row))
        self._record_usage(row)
        self._changed.notify_all()

    def _run(self, helper_id):
        organ = None
        try:
            organ = self._organ(helper_id)
            with self._changed:
                self._organs[helper_id] = organ
            row = self.get(helper_id)
            spec = FileContentEquals('.lumina-complete', 'done')
            result = organ.run_goal(_contract_text(row['contract']), spec)
            with self._changed:
                self._note_step(dict(self.get(helper_id)), organ)
            while True:
                with self._changed:
                    row = dict(self.get(helper_id))
                    self._questions(row, organ)
                    row = dict(self.get(helper_id))
                    if row.get('cancel_requested'):
                        if result.status == 'running':
                            try: organ.interrupt()
                            except ValueError: pass
                        self._finish(row, '已取消', '已按 Mind 要求取消。')
                        break
                    if result.status in ('completed', 'failed'):
                        reason = (result.output or '已通过完成标记核验。') if result.status == 'completed' else self._failure_summary(organ,result)
                        self._finish(row, '已交回' if result.status == 'completed' else '失败', reason)
                        break
                    if self._stopping:
                        break
                    if result.status == 'waiting':
                        if result.state.waiting_for == 'RETURN_REQUESTED' and row.get('return_requested'):
                            result = organ.deliver_event('RETURN_REQUESTED',row['return_requested'],defer_actions=True)
                            continue
                        if result.state.waiting_for == 'MIND_REPLY' and row.get('pending_reply') is not None:
                            reply = row['pending_reply']; row['pending_reply'] = None
                            row['status'] = '进行中'; self._save(row)
                            result = organ.deliver_event('MIND_REPLY', reply, defer_actions=True)
                            continue
                        if result.state.waiting_for not in ('MIND_REPLY','RETURN_REQUESTED'):
                            self._finish(row, '失败', '等待了未注册的外部事件。')
                            break
                        self._changed.wait(0.2)
                        continue
                    if row.get('return_requested') and row['grace_steps'] >= self.config['execution']['return_grace_steps']:
                        try: organ.interrupt()
                        except ValueError: pass
                        self._finish(row, '被拉闸', '触发保险丝：'+row['return_requested'])
                        break
                if result.status == 'child_pending':
                    for child_ref in result.state.pending_child_refs:
                        child = organ.open_child(child_ref,
                            max_decisions=self.config['execution']['helper_call_cap'],
                            model=organ._model,
                            ipython_control=DockerIPython(self.workspace,
                                task_directory=Path(row['task_dir'])))
                        try:
                            outcome = child.run_child()
                            while outcome.status == 'running':
                                outcome = child.resume()
                            if outcome.status == 'waiting':
                                child.interrupt()
                                outcome = child.resume()
                        finally:
                            child.shutdown()
                        result = organ.accept_child(outcome)
                    continue
                result = organ.resume()
                with self._changed:
                    row = dict(self.get(helper_id))
                    self._note_step(row, organ)
        except Exception as exc:
            with self._changed:
                row = self.get(helper_id)
                if row and row['status'] not in TERMINAL:
                    if row.get('cancel_requested'):
                        self._finish(dict(row), '已取消', '已按 Mind 要求取消。')
                    else:
                        reason = ('失败：需要人工确认' if 'recovery' in str(exc).lower()
                                  or 'unresolved' in str(exc).lower() else
                                  '帮手运行失败：'+type(exc).__name__)
                        self._finish(dict(row), '失败', reason)
        finally:
            if organ is not None:
                organ.shutdown()
            with self._changed:
                self._organs.pop(helper_id, None)
                self._threads.pop(helper_id, None)
                self._pump()

    def expire_questions(self):
        cutoff = self.clock() - timedelta(hours=self.config['execution']['question_hold_hours'])
        with self._changed:
            for row in self.all():
                question = row.get('question')
                if question and row['status'] == '在等答复' and datetime.fromisoformat(question['at']) < cutoff:
                    self.bus.publish(row['id']+':auto_reply:'+question['at'], 'mind.reply',
                        {'helper': row['id'], 'content': AUTO_REPLY, 'source':'自动答复'})
