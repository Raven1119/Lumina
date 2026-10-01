"""The six bounded native tools exposed to dialogue and event Mind thoughts."""
from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path, PureWindowsPath

from Execution.pool import TERMINAL


def _spec(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name,
        'description': description,
        'parameters': {'type': 'object', 'properties': {
            key: {'type': 'string', 'description': value}
            for key, value in properties.items()}, 'required': required}}}


MIND_TOOLS = [
    _spec('read_file', '读工作区里的一个文本文件。结果在下一步给你。',
          {'path': '工作区里的相对路径，例如 inbox/a.csv 或 tasks/H1a2b3c4d/result.csv。'}, ['path']),
    _spec('recall', '主动回忆和一条线索有关的记忆。结果在下一步给你，每条带编号。',
          {'clue': '一句线索。'}, ['clue']),
    _spec('delegate', '派一个帮手去做一件事。帮手在工作区里写代码、运行、检查，自己决定怎么做。它看不到你们的对话，只知道你写在这里的内容；它能读整个工作区，只能写自己的任务目录，不能上网。派出后它在后台做，做完、有问题或失败时会叫醒你。',
          {'goal': '要做成什么。', 'reason': '为什么要做，帮手据此在细节上取舍。',
           'acceptance': '怎样算做完，尽量写成能检查的条件。',
           'context': '帮手需要知道的事，比如相关文件的位置、他提过的要求和偏好。不要写代码或操作步骤。'},
          ['goal', 'reason', 'acceptance', 'context']),
    _spec('answer_helper', '回答帮手向你提的问题。',
          {'helper': '帮手编号。', 'content': '答复内容。'}, ['helper', 'content']),
    _spec('cancel_helper', '让帮手停下，不再继续。',
          {'helper': '帮手编号。'}, ['helper']),
    _spec('hold_question', '帮手的问题你现在答不了（比如要先问他），让它先等着。这个问题会留在你的状态里，直到你答复或取消；等得太久，它会被告知按自己的判断继续。',
          {'helper': '帮手编号。'}, ['helper']),
]

REQUIRED = {item['function']['name']: tuple(item['function']['parameters']['required'])
            for item in MIND_TOOLS}


class MindTools:
    def __init__(self, bus, io, config, pool=None):
        self.bus, self.io, self.config, self.pool = bus, io, config, pool

    @staticmethod
    def _result(text, *, ref='', status='ok', error_kind=None, references=None):
        value = {'id': ref, 'text': text, 'status': status}
        if error_kind:
            value['error_kind'] = error_kind
        if references:
            value['references'] = references
        return value

    def execute(self, key, call, user, *, too_many=False):
        saved = self.bus.get(key+':result')
        if saved is not None:
            return saved
        self.bus.put(key+':action', call)
        name = call['function']['name']
        ref = '工具:'+key
        if too_many:
            limit = self.config['mind']['tool_max_calls_per_step']
            result = self._result(f'没办成：一步最多调用 {limit} 个工具，这个没有执行。',
                                  ref=ref, status='error', error_kind='too_many')
        elif name not in REQUIRED:
            result = self._result(f'没办成：没有叫 {name} 的工具。',
                                  ref=ref, status='error', error_kind='unknown_tool')
        else:
            try:
                arguments = json.loads(call['function']['arguments'])
            except (ValueError, TypeError):
                arguments = None
            if not isinstance(arguments, dict):
                result = self._result(f'没办成：{name} 的参数没看懂。',
                                      ref=ref, status='error', error_kind='invalid_json')
            else:
                missing = next((field for field in REQUIRED[name]
                                if not isinstance(arguments.get(field), str)
                                or not arguments[field].strip()), None)
                if missing:
                    result = self._result(f'没办成：{name} 缺少 {missing}，或它是空的。',
                                          ref=ref, status='error', error_kind='missing_parameter')
                else:
                    result = self._dispatch(key, name, arguments, user)
        return self.bus.put(key+':result', result)

    def _dispatch(self, key, name, args, user):
        if name == 'read_file':
            return self._read(args['path'])
        if name == 'recall':
            return self._recall(key, args['clue'], user)
        if name == 'delegate':
            helper = 'H'+hashlib.sha256(key.encode()).hexdigest()[:8]
            contract = {'目标': args['goal'], '理由': args['reason'],
                        '验收': args['acceptance'], '背景': args['context']}
            self.bus.publish(key+':event', 'mind.spawn',
                             {'helper': helper, 'contract': contract})
            return self._result(
                f'已派出帮手 {helper}，任务目录是 tasks/{helper}/。它做完、有问题或失败时会叫醒你。',
                ref=helper)
        helper = args['helper']
        row = self.pool.get(helper) if self.pool is not None else None
        if row is None or row['status'] in TERMINAL:
            return self._result(
                f'没办成：帮手 {helper} 不存在或已经结束。手头任务里有现在的帮手编号。',
                ref=helper, status='error', error_kind='helper_unavailable')
        if name in ('answer_helper', 'hold_question') and not row.get('question'):
            return self._result(
                f'没办成：帮手 {helper} 现在没有在问你问题。要改它的任务，就取消后重新派活。',
                ref=helper, status='error', error_kind='not_questioning')
        kind = {'answer_helper': 'mind.reply', 'cancel_helper': 'mind.cancel',
                'hold_question': 'mind.hold'}[name]
        body = {'helper': helper}
        if name == 'answer_helper':
            body['content'] = args['content']
            text = f'已答复帮手 {helper}。'
        elif name == 'cancel_helper':
            text = f'已让帮手 {helper} 停下。'
        else:
            text = f'已让帮手 {helper} 先等着。'
        self.bus.publish(key+':event', kind, body)
        return self._result(text, ref=helper)

    def _read(self, value):
        ref = '文件:'+value
        try:
            relative = Path(value)
            root = Path(self.config['workspace']['path']).resolve()
            target = (root/relative).resolve()
            if (relative.is_absolute() or PureWindowsPath(value).drive
                    or '..' in relative.parts or not target.is_relative_to(root)):
                raise ValueError('路径不在工作区内')
            if not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
                raise ValueError('文件不存在')
            limit = self.config['mind']['read_max_chars']
            with target.open('r', encoding='utf-8') as stream:
                content = stream.read(limit+1)
            if len(content) > limit:
                content = content[:limit]+'\n（已截断，后续内容未读取）'
            return self._result(f'[{ref}]\n'+content, ref=ref)
        except UnicodeError:
            reason = '不是 UTF-8 文本'
        except OSError:
            reason = '文件不可读取'
        except ValueError as exc:
            reason = str(exc)
        return self._result(f'没读到：{reason}。只能读工作区里的文本文件，路径写相对路径。',
                            ref=ref, status='error', error_kind='read_failed')

    def _recall(self, key, clue, user):
        ref = '回忆:'+key
        try:
            read = self.io.recall(clue, user)
            lines = [line for line in read.block.splitlines() if line.strip()]
            references = {f'{ref}.{i}': {'id': f'{ref}.{i}', 'text': line}
                          for i, line in enumerate(lines, 1)}
            text = '\n'.join(f"[{item['id']}] {item['text']}"
                             for item in references.values()) or '（没有想起相关的事）'
            # Active recall intentionally never enters the automatic recall trace.
            return self._result(text, ref=ref, references=references)
        except Exception:
            return self._result('（没有想起相关的事）', ref=ref)
