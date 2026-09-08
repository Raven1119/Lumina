"""D1 experiment control: matched review contexts, actual organ continuation.

Nothing here is imported by production. Evaluator facts never enter actor cwd.
"""
from __future__ import annotations

import hashlib
import json
import ast
import re
import shutil
import os
import math
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from dataclasses import asdict, replace
from pathlib import Path

from Execution.deepseek_model import DeepSeekModel
from Execution.execution import IPythonCode, EventLog
from Execution import ExecutionOrgan
from Mind.behavioral_experiment import E1Config, FrozenTaskSpec, freeze_prefix, fork_prefix
from Mind.host import activation_event, run_mind_once
from Mind.organ import Evidence, MindInput, MindOrgan
from Mind.execution_steering_experiment import decision_advisory_for_execution
from Nervous.organ import Event, NervousOrgan

MODEL = 'deepseek-v4-pro'
FILES = {'task.json', 'answer.json', '.lumina-complete'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def score_result(workspace, expected):
    try:
        actual = json.loads((Path(workspace) / 'answer.json').read_text(encoding='utf-8'))
        return canonical(actual) == canonical(expected)
    except (OSError, ValueError):
        return False


def _messages(messages):
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


def _wire(payload, context):
    messages = _messages(payload['messages'])
    advisory = json.loads(context).get('mind_supervisor_directive')
    if advisory:
        messages.append({'role': 'user', 'content': [{'type': 'text',
                         'text': advisory}]})
    constraints = (' Experiment file admission permits only literal task.json, answer.json and .lumina-complete. '
        'task.json is read-only. Use direct Path("literal").read_text()/write_text()/exists() '
        'or open("literal", "r"/"w"); do not store or traverse Path objects. '
        'Use self-contained cells, simple assignments/comprehensions and direct builtin calls; '
        'no custom functions, callable values, import aliases or rebinding builtins/modules. '
        'No introspection, environment access, process/network operations or delegation.')
    return {'model': MODEL, 'system': payload['messages'][0]['content'] + constraints,
            'messages': messages, 'max_tokens': 1600, 'temperature': 0,
            'thinking': {'type': 'disabled'},
            'tools': [{'name': tool['function']['name'],
                       'description': tool['function']['description'],
                       'input_schema': tool['function']['parameters']} for tool in payload['tools']]}


def _native(response):
    content = response.get('content', [])
    return {'choices': [{'message': {'content': ''.join(
        block['text'] for block in content if block.get('type') == 'text'),
        'tool_calls': [{'id': block['id'], 'type': 'function', 'function': {
            'name': block['name'], 'arguments': canonical(block['input'])}}
            for block in content if block.get('type') == 'tool_use']}}],
        'usage': response.get('usage', {})}


def _admit_code(code):
    """Conservative experiment admission, not a general Python sandbox."""
    if len(code) > 6000:
        return False
    imports = {'json', 'pathlib', 'math', 'statistics', 'datetime', 'csv', 'io'}
    functions = {'print', 'len', 'sum', 'min', 'max', 'sorted', 'range', 'enumerate',
                 'int', 'float', 'str', 'round', 'abs', 'list', 'dict', 'set',
                 'tuple', 'bool', 'zip', 'any', 'all', 'open', 'Path'}
    methods = {'loads', 'dumps', 'load', 'dump', 'read_text', 'write_text', 'read',
               'write', 'get', 'items', 'keys', 'values', 'append', 'extend', 'sort',
               'split', 'strip', 'join', 'upper', 'lower', 'replace', 'Path', 'exists',
               'fromisoformat', 'isoformat', 'total_seconds', 'reader', 'writer',
               'DictReader', 'StringIO', 'mean', 'ceil', 'floor'}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    trusted = functions | imports | {'datetime', 'timedelta', 'timezone'}
    local_names = {node.id for node in ast.walk(tree)
                   if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    if local_names & trusted:
        return False
    # No reads from the persistent kernel's ambient namespace. Track definite
    # initialization, including RHS-before-LHS and conditional/loop scope.
    class Initialized(ast.NodeVisitor):
        def __init__(self):
            self.bound = set(trusted)

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Store):
                self.bound.add(node.id)
            elif node.id not in self.bound:
                raise ValueError('ambient_name')

        def visit_Assign(self, node):
            self.visit(node.value)
            for target in node.targets:
                self.visit(target)

        def visit_AugAssign(self, node):
            if isinstance(node.target, ast.Name) and node.target.id not in self.bound:
                raise ValueError('ambient_augmented_name')
            self.visit(node.value)
            self.visit(node.target)

        def visit_If(self, node):
            self.visit(node.test)
            before = self.bound.copy()
            for statement in node.body:
                self.visit(statement)
            after_body = self.bound
            self.bound = before.copy()
            for statement in node.orelse:
                self.visit(statement)
            self.bound &= after_body

        def visit_For(self, node):
            self.visit(node.iter)
            before = self.bound.copy()
            self.visit(node.target)
            for statement in node.body:
                self.visit(statement)
            self.bound = before.copy()
            for statement in node.orelse:
                self.visit(statement)
            self.bound = before

        def visit_ListComp(self, node):
            before = self.bound.copy()
            for generator in node.generators:
                self.visit(generator.iter)
                self.visit(generator.target)
                for condition in generator.ifs:
                    self.visit(condition)
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            else:
                self.visit(node.elt)
            self.bound = before

        visit_SetComp = visit_GeneratorExp = visit_DictComp = visit_ListComp

    try:
        Initialized().visit(tree)
    except ValueError:
        return False
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    syntax = (ast.Module, ast.Expr, ast.Import, ast.ImportFrom, ast.alias,
              ast.Assign, ast.AugAssign, ast.Name, ast.Load, ast.Store,
              ast.Constant, ast.Attribute, ast.Call, ast.keyword, ast.Subscript,
              ast.Slice, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.ListComp,
              ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
              ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
              ast.JoinedStr, ast.FormattedValue, ast.For, ast.If, ast.With,
              ast.withitem, ast.Pass, ast.Break, ast.Continue, ast.Assert,
              ast.operator, ast.unaryop, ast.boolop, ast.cmpop)
    for node in ast.walk(tree):
        if not isinstance(node, syntax):
            return False
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module]
            if any(module not in imports for module in modules):
                return False
            if any(item.asname not in (None, item.name) for item in node.names):
                return False
            if isinstance(node, ast.ImportFrom):
                members = {'pathlib': {'Path'}, 'datetime': {'datetime', 'timedelta', 'timezone'}}
                if node.level or any(item.name not in members.get(node.module, set())
                                     or item.asname not in (None, item.name) for item in node.names):
                    return False
        if isinstance(node, ast.Name):
            if node.id.startswith('_') or node.id not in trusted | local_names:
                return False
            parent = parents.get(node)
            if node.id in trusted and not (
                    isinstance(parent, ast.Call) and parent.func is node
                    or isinstance(parent, ast.Attribute) and parent.value is node):
                return False
        if isinstance(node, ast.Attribute):
            parent = parents.get(node)
            if not isinstance(node.ctx, ast.Load) or not (
                    isinstance(parent, ast.Call) and parent.func is node
                    or isinstance(parent, ast.Attribute) and parent.value is node):
                return False
        if isinstance(node, ast.Attribute) and node.attr not in methods:
            return False
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Lambda, ast.AsyncFunctionDef)):
            return False
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name not in functions:
                return False
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name not in methods:
                return False
        else:
            return False
        if name in {'open', 'Path'}:
            if not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value not in FILES:
                return False
            if name == 'Path':
                parent = parents.get(node)
                if (len(node.args) != 1 or node.keywords or not isinstance(parent, ast.Attribute)
                        or parent.attr not in {'read_text', 'write_text', 'exists'}
                        or not isinstance(parents.get(parent), ast.Call)):
                    return False
            if name == 'open':
                mode_nodes = list(node.args[1:]) + [k.value for k in node.keywords if k.arg == 'mode']
                if (len(mode_nodes) > 1 or any(not isinstance(m, ast.Constant)
                        or m.value not in {'r', 'rt', 'w', 'wt'} for m in mode_nodes)
                        or any(k.arg not in {'mode', 'encoding'} for k in node.keywords)):
                    return False
                if node.args[0].value == 'task.json' and any(m.value not in {'r', 'rt'} for m in mode_nodes):
                    return False
        if name in {'read_text', 'write_text', 'exists'}:
            receiver = node.func.value
            if (not isinstance(receiver, ast.Call) or not receiver.args
                    or not isinstance(receiver.args[0], ast.Constant)
                    or receiver.args[0].value not in FILES):
                return False
            if name == 'write_text' and receiver.args[0].value == 'task.json':
                return False
    return True


class ExecutionAdapter:
    """Existing action parser, experimental wire transport, equal arm policy."""
    identifier = MODEL
    tool_contracts = ('ipython(code: str)', 'wait(event_type: str)', 'claim_complete()')

    def __init__(self, transport):
        self.transport = transport
        self.requests = []
        self.wires = []
        self.responses = []
        self.authority_rejections = []
        self.workspace = None
        self.snapshots = []

    def decide(self, request):
        if self.workspace is not None:
            self.snapshots.append(_snapshot(self.workspace))
        self.requests.append(request)
        def send(payload):
            wire = _wire(payload, request.context)
            self.wires.append(wire)
            response = self.transport(wire)
            self.responses.append(response)
            return _native(response)
        result = DeepSeekModel(transport=send).decide(request)
        if self.wires:
            result = replace(result, provider_wire_request=self.wires[-1])
        actions = result.action if isinstance(result.action, tuple) else (result.action,)
        if any(isinstance(action, IPythonCode) and not _admit_code(action.code) for action in actions):
            self.authority_rejections.append('experiment_authority_rejected')
            result = replace(result, action=None, provider_tool_call_id=None,
                             failure='experiment_authority_rejected')
        return result


class _Review:
    def __init__(self, transport, history, tool_schemas):
        self.transport, self.history = transport, history
        self.tool_schemas = tool_schemas
        self.projection = self.wire = self.response = None

    def generate(self, recent_context, user_message, *, system_prompt):
        if self.wire is not None:
            raise ValueError('review_call_budget_exceeded')
        self.projection = {'recent_context': list(recent_context), 'user_message': user_message,
                           'system_prompt': system_prompt}
        self.wire = {'model': MODEL, 'system': system_prompt,
            'messages': [*self.history, {'role': 'user', 'content': user_message}],
            'max_tokens': 1000, 'temperature': 0, 'thinking': {'type': 'disabled'},
            'tools': self.tool_schemas, 'tool_choice': {'type': 'none'}}
        self.response = self.transport(self.wire)
        if any(block.get('type') == 'tool_use' for block in self.response.get('content', [])):
            raise ValueError('review_tool_use_forbidden')
        return ''.join(block['text'] for block in self.response.get('content', [])
                       if block.get('type') == 'text')


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()]


def _snapshot(path):
    return {name: (Path(path) / name).read_text(encoding='utf-8')
            for name in sorted(FILES) if (Path(path) / name).is_file()}


def _high_level(output):
    if output is None or output.get('type') != 'directive':
        return True
    # A syntactic floor only; semantic abstraction is audited separately.
    return not re.search(r'`|\b(?:open|exec|eval|print|Path)\s*\(|^\s*\d+[.)]\s|\b(?:pip install|git commit|python -|spawn_child)\b',
                         output['text'], flags=re.MULTILINE)


def run_case(case, directory, *, review_transports, execution_transports, admit_review, order=('A', 'B')):
    """One immutable actual prefix, two reviews and actual continuations."""
    previous = os.environ.get('IPYTHONDIR')
    profile = Path(directory) / 'ipython-profile'
    profile.mkdir(parents=True, exist_ok=True)
    os.environ['IPYTHONDIR'] = str(profile.resolve())
    try:
        return _run_case(case, directory, review_transports=review_transports,
                         execution_transports=execution_transports, admit_review=admit_review, order=order)
    finally:
        if previous is None:
            os.environ.pop('IPYTHONDIR', None)
        else:
            os.environ['IPYTHONDIR'] = previous


def _run_case(case, directory, *, review_transports, execution_transports, admit_review, order):
    directory = Path(directory)
    goal = ('Complete the task in task.json and put exactly its requested JSON object in answer.json. '
            'A prior attempt left a draft; independently verify all requirements. '
            'When finished write verified to .lumina-complete, then claim completion. '
            'The marker is not the task evaluator. Kernel variables do not survive checkpoint restore. '
            'Use only relative task.json, answer.json and .lumina-complete; no other files, '
            'network, subprocesses or child delegation. Python json/pathlib/math/datetime are available.')
    task = FrozenTaskSpec(case['id'], 'D1 induced checkpoint', goal,
        (('task.json', canonical(case['task']).encode()),), '.lumina-complete', 'verified')
    config = E1Config(1, 6, 1, 16000, 420)
    seed_code = ("import json\nfrom pathlib import Path\n"
        "Path('answer.json').write_text(" + repr(canonical(case['draft'])) + ", encoding='utf-8')\n"
        "print(json.dumps({'task':json.loads(Path('task.json').read_text(encoding='utf-8')),"
        "'draft':json.loads(Path('answer.json').read_text(encoding='utf-8')),"
        "'assumption':" + repr(case['assumption']) + "}, ensure_ascii=False))")
    seed = ExecutionAdapter(lambda wire: {'content': [{'type': 'tool_use', 'id': 'seed',
                'name': 'ipython', 'input': {'code': seed_code}}]})
    prefix = freeze_prefix(task, model=seed, output_dir=directory / 'prefix', config=config)
    packet = json.loads(prefix.state.latest_observation.result.output)
    if packet != {key: case[key] for key in ('task', 'draft', 'assumption')}:
        raise ValueError('checkpoint_evidence_mismatch')
    branches = fork_prefix(prefix, directory / 'pair')
    # Capture the CURRENT real Execution request on a control-only copy. Wait
    # performs no task action and no provider call. The studied arms stay intact.
    preview_root = directory / 'preview'
    shutil.copytree(prefix.root, preview_root)
    preview = ExecutionAdapter(lambda wire: {'content': [{'type': 'tool_use',
        'id': 'preview', 'name': 'wait', 'input': {'event_type': 'd1-preview'}}]})
    owner = ExecutionOrgan(workspace=prefix.workspace, event_log_path=preview_root / 'execution.jsonl',
        checkpoint_path=preview_root / 'checkpoint.json', max_decisions=7,
        max_context_chars=16000, model=preview)
    try:
        owner.resume()
    finally:
        owner.shutdown()
    history = preview.wires[0]['messages']
    # The packet contains every factual task/draft value in the retained native
    # tool history. Action syntax and lifecycle metadata are the removed material.
    source_event = next(event for event in reversed(EventLog.load(prefix.event_log_path).events)
                        if event.event_type == 'IPYTHON_EXECUTION_RESULT')
    source = prefix.state.execution_id + ':' + source_event.event_id
    evidence = tuple(Evidence(source + ':' + key, canonical(packet[key]), 'execution')
                     for key in ('task', 'draft', 'assumption'))
    value = MindInput('review', 'Review the current direction and draft against the task requirements.',
                     'task', 1, goal, prefix.state.execution_id, 'suspended', evidence)
    record = {'case_id': case['id'], 'family': case['family'],
        'needs_correction': case['needs_correction'], 'expected': case['expected'],
        'initial_objective_success': score_result(prefix.workspace, case['expected']),
        'paired_start_equal': branches.equivalent, 'prefix_hashes': asdict(prefix.evidence),
        'prefix_events': _jsonl(prefix.event_log_path),
        'checkpoint': json.loads(prefix.checkpoint_path.read_text(encoding='utf-8')),
        'initial_workspace': _snapshot(prefix.workspace), 'packet': packet,
        'retained_execution_wire': preview.wires[0], 'arms': {},
        'temporary_workspaces': [str(prefix.workspace), str(branches.baseline.workspace), str(branches.candidate.workspace)]}
    for arm in order:
        branch = branches.baseline if arm == 'A' else branches.candidate
        review = _Review(review_transports[arm], history if arm == 'A' else [], preview.wires[0]['tools'])
        model = ExecutionAdapter(execution_transports[arm])
        model.workspace = branch.workspace
        base = directory / arm
        with NervousOrgan(base / 'nervous') as nervous, MindOrgan(directory=base / 'mind', model=review) as mind:
            nervous.publish(activation_event(value, source='execution'))
            receipt = run_mind_once(nervous, mind)
            output = dict(receipt.output) if receipt.output is not None else None
            admission_request = {'output': output, 'evidence': packet,
                'rubric': 'Allow only NoChange, semantic DecisionIntent, or high-level concern/direction. Reject commands, code, patches, file operations, concrete tool plans or action sequences. Judge authority/abstraction only; do not rewrite, improve or grade correctness of advice.'}
            admission = admit_review(admission_request)
            if (type(admission.get('allowed')) is not bool or not isinstance(admission.get('reason'), str)
                    or not isinstance(admission.get('reviewer'), str)):
                raise ValueError('invalid_review_admission')
            boundary_ok = _high_level(output) and admission['allowed']
            application = mind.prepare_directive('review', execution_ref=value.execution_ref,
                decision_id=branch.next_decision_id, intention_ref='task', intention_revision=1) if boundary_ok else None
            advisory = decision_advisory_for_execution(application, execution_ref=value.execution_ref,
                decision_id=branch.next_decision_id)
            message, = nervous.pending('host')
            request = Event('resume', 'host', 'execution', 'execution.resume',
                {'advisory': list(advisory) if advisory else None}, message.event_id)
            nervous.complete(message.event_id, 'host', emitted=(request,))
            owner = ExecutionOrgan(workspace=branch.workspace, event_log_path=branch.event_log_path,
                checkpoint_path=branch.checkpoint_path, max_decisions=7,
                max_context_chars=16000, model=model)
            try:
                result = owner.resume(decision_advisory=advisory)
                reality = [dict(item.payload) for item in owner.reality_evidence()]
                nervous.complete('resume', 'execution', emitted=(Event('outcome', 'execution',
                    'host', 'execution.outcome', {'facts': reality}, 'resume'),))
            finally:
                owner.shutdown()
        codes = [a.code for frame in result.decision_frames[1:]
                 for a in (frame.resulting_action if isinstance(frame.resulting_action, tuple)
                           else (frame.resulting_action,)) if isinstance(a, IPythonCode)]
        final_workspace = _snapshot(branch.workspace)
        model.snapshots.append(final_workspace)
        delivered = bool(advisory and model.wires and any(
            block.get('text') == advisory[1] for message in model.wires[0]['messages']
            for block in message['content']))
        arm_record = {'review_projection': review.projection, 'review_wire': review.wire,
            'review_response': review.response, 'review_status': receipt.status, 'review_error': receipt.error,
            'admission': admission, 'admission_request_sha256': digest(admission_request),
            'output': output, 'boundary_ok': boundary_ok, 'directive_issued': bool(output and output.get('type') == 'directive'),
            'delivered': delivered, 'advisory': list(advisory) if advisory else None,
            'execution_wires': model.wires, 'execution_responses': model.responses,
            'authority_rejections': model.authority_rejections, 'status': result.status,
            'failure': result.state.failure, 'marker_completed': result.status == 'completed',
            'objective_success': score_result(branch.workspace, case['expected']),
            'execution_calls': len(model.wires), 'actions': codes,
            'workspace_snapshots': model.snapshots,
            'repeated_exact_actions': len(codes) - len(set(codes)),
            'final_workspace': final_workspace, 'owner_outcome': reality,
            'task_unchanged': final_workspace['task.json'] == record['initial_workspace']['task.json'],
            'execution_events': _jsonl(branch.event_log_path),
            'mind_journal': json.loads((base / 'mind' / 'cognition.json').read_text(encoding='utf-8')),
            'mind_traces': {path.name: _jsonl(path) for path in (base / 'mind').rglob('*.jsonl')},
            'nervous_events': json.loads((base / 'nervous' / 'events.json').read_text(encoding='utf-8'))}
        record['arms'][arm] = arm_record
    if record['arms']['A']['review_projection'] != record['arms']['B']['review_projection']:
        raise ValueError('review_key_information_mismatch')
    return record


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    'Mind/decoupling_value.py', 'Mind/test_decoupling_value.py',
    'Mind/docs/INTEGRATED_CHAIN.md', 'Mind/docs/EXPERIMENT_HISTORY.md', 'Mind/behavioral_experiment.py',
    'Mind/organ.py', 'Mind/host.py', 'Mind/experiment_a.py', 'Mind/trace.py',
    'Mind/directive.py', 'Mind/execution_steering_experiment.py',
    'Execution/organ.py', 'Execution/execution.py', 'Execution/ipython_control.py',
    'Execution/deepseek_model.py', 'Nervous/organ.py', 'core/env_loader.py')


def _save(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    if exclusive:
        with path.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def preregister(manifest_path, directory):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    cases = manifest['cases']
    if len(cases) != 12:
        raise ValueError('twelve_cases_required')
    if (len({c['id'] for c in cases}) != 12 or sum(c['needs_correction'] for c in cases) != 8
            or any((canonical(c['draft']) != canonical(c['expected'])) != c['needs_correction'] for c in cases)
            or any(len(canonical(c[key])) > 1000 for c in cases for key in ('task', 'draft', 'assumption'))):
        raise ValueError('invalid_case_manifest')
    frozen = {'schema': 'mind-decoupling-d1-prereg-v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'baseline_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        'manifest': manifest, 'manifest_sha256': digest(manifest),
        'source_set_version': 'retained-regression-sources-v1',
        'source_hashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_PATHS},
        'provider': {'model': MODEL, 'endpoint': 'https://api.deepseek.com/anthropic/v1/messages',
                     'thinking': 'disabled', 'temperature': 0, 'timeout_seconds': 30, 'retries': 0},
        'limits': {'review_calls_per_arm': 1, 'review_output_tokens': 1000,
                   'execution_calls_per_arm': 6, 'execution_output_tokens': 1600,
                   'total_provider_calls': 168, 'context_chars': 16000},
        'arm_order': {case['id']: ['A', 'B'] if i % 2 == 0 else ['B', 'A'] for i, case in enumerate(cases)},
        'criteria': {'supported': 'valid; B>A; exact paired p<=0.05; >=2 rescued families; no B-only control loss; correction wins delivered/adopted/relevant; control wins may preserve the correct direction with NoChange',
                     'not_supported': 'valid; all 12 pairs; B<=A',
                     'otherwise': 'INCONCLUSIVE',
                     'cost_policy': 'Equal allocated call/output caps; actual token/action consumption is an outcome, never a post-treatment validity gate.'}}
    frozen['sha256'] = digest(frozen)
    _save(Path(directory) / 'preregistration.json', frozen, exclusive=True)
    return frozen


def summarize(records, trace_audit=None):
    invalid = []
    successes = {arm: 0 for arm in ('A', 'B')}
    totals = {arm: {'input_tokens': 0, 'output_tokens': 0, 'calls': 0,
                    'directives': 0, 'delivered': 0, 'repeated_exact_actions': 0} for arm in successes}
    wins = losses = 0
    rescued_families = set()
    control_losses = 0
    for index, record in enumerate(records):
        if not record['paired_start_equal']:
            invalid.append(f'{index}:unequal_checkpoint')
        a, b = (record['arms'][arm] for arm in ('A', 'B'))
        win = b['objective_success'] and not a['objective_success']
        loss = a['objective_success'] and not b['objective_success']
        wins += int(win)
        losses += int(loss)
        if win:
            rescued_families.add(record['family'])
        if loss and not record['needs_correction']:
            control_losses += 1
        for arm, outcome in record['arms'].items():
            successes[arm] += int(outcome['objective_success'])
            total = totals[arm]
            total['directives'] += int(outcome['directive_issued'])
            total['delivered'] += int(outcome['delivered'])
            total['repeated_exact_actions'] += outcome['repeated_exact_actions']
            if (outcome['review_status'] != 'accepted' or not outcome['boundary_ok']
                    or not outcome.get('admission', {}).get('allowed')
                    or outcome['authority_rejections'] or not outcome['task_unchanged']
                    or (outcome['directive_issued'] and not outcome['delivered'])
                    or (outcome['failure'] and ('provider' in outcome['failure'] or 'protocol' in outcome['failure']))):
                invalid.append(f'{index}:{arm}:protocol_authority_or_delivery')
            for response in [outcome['review_response'], *outcome['execution_responses']]:
                total['calls'] += 1
                usage = response.get('usage', {}) if response else {}
                if any(type(usage.get(key)) is not int for key in ('input_tokens', 'output_tokens')):
                    invalid.append(f'{index}:{arm}:usage_missing')
                for key in ('input_tokens', 'output_tokens'):
                    total[key] += usage.get(key, 0)
    denominator = totals['A']['input_tokens'] + totals['A']['output_tokens']
    ratio = (totals['B']['input_tokens'] + totals['B']['output_tokens']) / denominator if denominator else None
    discordant = wins + losses
    p = min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1)) / 2**discordant) if discordant else 1.0
    verdict = 'INCONCLUSIVE'
    if len(records) == 12 and not invalid:
        if successes['B'] <= successes['A']:
            verdict = 'MIND_DECOUPLING_VALUE_NOT_SUPPORTED'
        elif p <= .05 and len(rescued_families) >= 2 and not control_losses:
            rescued = [r for r in records if r['arms']['B']['objective_success'] and not r['arms']['A']['objective_success']]
            if trace_audit and all(
                    (trace_audit.get(r['case_id'], {}).get('B_rescue_adopted_relevant') and r['arms']['B']['delivered'])
                    if r['needs_correction'] else trace_audit.get(r['case_id'], {}).get('B_control_preserved')
                    for r in rescued):
                verdict = 'MIND_DECOUPLING_VALUE_SUPPORTED'
    if trace_audit and any(item.get('boundary_violation') for item in trace_audit.values()):
        invalid.append('trace_audit_boundary_violation')
        verdict = 'INCONCLUSIVE'
    return {'verdict': verdict, 'pairs': len(records), 'successes': successes,
            'B_only_wins': wins, 'A_only_wins': losses, 'exact_paired_p': p,
            'rescued_families': sorted(rescued_families), 'B_only_control_losses': control_losses,
            'cost': totals, 'total_token_ratio_B_over_A': ratio, 'invalid_reasons': invalid}


def run_campaign(directory, *, transport=None, admit_review=None):
    """Validate freeze, reserve exactly once, persist every real call before use."""
    directory = Path(directory)
    reservation = directory / 'campaign-started.json'
    if reservation.exists():
        raise FileExistsError('campaign_already_reserved')
    frozen = json.loads((directory / 'preregistration.json').read_text(encoding='utf-8'))
    unsigned = {k: v for k, v in frozen.items() if k != 'sha256'}
    if digest(unsigned) != frozen['sha256'] or digest(frozen['manifest']) != frozen['manifest_sha256']:
        raise ValueError('preregistration_integrity_changed')
    for name, expected in frozen['source_hashes'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError('registered_source_changed:' + name)
    if transport is None:
        import httpx
        from core.env_loader import load_env_file
        load_env_file(ROOT / '.env.local', override=False)
        api_key = os.environ.get('DEEPSEEK_API_KEY')
        if not api_key:
            raise ValueError('missing_deepseek_key')
        def transport(wire):
            response = httpx.post(frozen['provider']['endpoint'],
                headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01'},
                json=wire, timeout=30)
            response.raise_for_status()
            return response.json()
    control = Path(tempfile.mkdtemp(prefix='lumina-d1-control-'))
    _save(reservation, {'preregistration_sha256': frozen['sha256'], 'control_directory': str(control),
                      'started_at': datetime.now(timezone.utc).isoformat()}, exclusive=True)
    call_count = 0
    if admit_review is None:
        def admit_review(request):
            # Explicit development-review gate for this single experiment, not
            # an organ worker/scheduler. It cannot edit a proposal or give clues.
            identity = uuid.uuid4().hex
            pending = directory / 'admission' / (identity + '.pending.json')
            decision = directory / 'admission' / (identity + '.decision.json')
            _save(pending, {'request': request, 'sha256': digest(request)}, exclusive=True)
            print(canonical({'admission_pending': identity}), flush=True)
            deadline = time.monotonic() + 300
            while not decision.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError('review_admission_timeout')
                time.sleep(.2)
            value = json.loads(decision.read_text(encoding='utf-8'))
            if value.get('request_sha256') != digest(request):
                raise ValueError('admission_identity_conflict')
            return value
    def recorded(wire):
        nonlocal call_count
        if call_count >= 168:
            raise ValueError('campaign_call_budget_exhausted')
        call_count += 1
        path = directory / 'calls' / f'{call_count:03d}.json'
        call = {'wire': wire, 'started_at': datetime.now(timezone.utc).isoformat()}
        _save(path, call, exclusive=True)
        started = time.monotonic()
        try:
            response = transport(wire)
            response = {**response, 'content': [block for block in response.get('content', [])
                        if block.get('type') in {'text', 'tool_use'}]}
            call['response'] = response
            return response
        except Exception as error:
            call['error_type'] = type(error).__name__
            raise
        finally:
            call['elapsed_seconds'] = time.monotonic() - started
            _save(path, call)
    records = []
    artifact = {'schema': 'mind-decoupling-d1-artifact-v1', 'preregistration_sha256': frozen['sha256'],
                'records': records, 'control_directory': str(control)}
    try:
        for case in frozen['manifest']['cases']:
            record = run_case(case, control / case['id'], review_transports={'A': recorded, 'B': recorded},
                execution_transports={'A': recorded, 'B': recorded}, admit_review=admit_review,
                order=frozen['arm_order'][case['id']])
            records.append(record)
            artifact['summary'] = summarize(records)
            artifact['provider_calls'] = call_count
            _save(directory / 'campaign.json', artifact)
            print(canonical({'case': case['id'], 'success': {arm: data['objective_success']
                   for arm, data in record['arms'].items()}, 'calls': call_count}), flush=True)
    except Exception as error:
        artifact['aborted_error_type'] = type(error).__name__
        artifact['summary'] = {**summarize(records), 'verdict': 'INCONCLUSIVE'}
        artifact['provider_calls'] = call_count
        _save(directory / 'campaign.json', artifact)
        raise
    artifact['sha256'] = digest(artifact)
    _save(directory / 'campaign.json', artifact)
    return artifact


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['register', 'run'])
    parser.add_argument('directory')
    args = parser.parse_args()
    if args.operation == 'register':
        print(canonical(preregister(ROOT / 'Mind/fixtures/decoupling_d1/manifest.json', args.directory)))
    else:
        print(canonical(run_campaign(args.directory)['summary']))
