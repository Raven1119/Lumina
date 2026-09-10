"""Foreground composition only: create organs, submit input, run Nervous."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import signal
from pathlib import Path

from Execution.model import EXECUTION_PROTOCOL
from Execution.runtime import Execution
from Mind.organ import MindOrgan
from Mind.task_view import execution_goal
from Nervous.organ import NervousOrgan


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run one bounded Lumina goal through its organs.')
    parser.add_argument('action', choices=('start', 'resume', 'status'))
    parser.add_argument('--state', type=Path, required=True, help='Private durable organ state, outside the workspace.')
    parser.add_argument('--workspace', type=Path)
    goal = parser.add_mutually_exclusive_group()
    goal.add_argument('--goal')
    goal.add_argument('--goal-file', type=Path)
    goal.add_argument('--pursuit', help='Opt-in continuing Mind: original authorized scope for choosing serial bounded Tasks.')
    event = parser.add_mutually_exclusive_group()
    event.add_argument('--message')
    event.add_argument('--event', help='The actual type of a new owner event; matching waits may resume.')
    event.add_argument('--retry-review', action='store_true', help='Explicitly reassess a failed cognitive activity with current evidence.')
    event.add_argument('--retry-context', action='store_true', help='Explicitly retry a rejected historical summary, preserving the failed attempt and cost.')
    event.add_argument('--review-at', help='Pursuit mode: one foreground review opportunity at an absolute timezone-aware time.')
    parser.add_argument('--review-reason', default='One authorized bounded review of the continuing pursuit.')
    parser.add_argument('--data', default='')
    parser.add_argument('--submission-id', help='Reuse only when retrying the same message/event submission.')
    parser.add_argument('--max-calls', type=int, default=40)
    parser.add_argument('--max-output-tokens', type=int)
    parser.add_argument('--max-request-bytes', type=int)
    parser.add_argument('--context-mode', choices=('baseline', 'mask', 'summary'),
                        help='Experimental working projection, fixed at start; default baseline.')
    parser.add_argument('--add-calls', type=int)
    parser.add_argument('--add-output-tokens', type=int)
    parser.add_argument('--add-request-bytes', type=int)
    args = parser.parse_args(argv)
    directory = args.state.resolve()
    if (directory / 'session.json').exists():
        parser.error('Retired experimental state is not supported. Start with a new state directory.')
    if args.action == 'start':
        if args.workspace is None or not (args.goal or args.goal_file or args.pursuit):
            parser.error('start requires --workspace and --goal, --goal-file or --pursuit')
        workspace = args.workspace.resolve(strict=True)
        if not workspace.is_dir() or directory.is_relative_to(workspace) or workspace.is_relative_to(directory):
            parser.error('state and workspace must be disjoint directories')
    elif not (directory / 'nervous' / 'settings.json').exists():
        parser.error('state does not exist')
    if args.action != 'start' and (args.workspace or args.goal or args.goal_file or args.pursuit or args.context_mode):
        parser.error('workspace and goal are fixed at start')
    if args.action != 'resume' and (args.message is not None or args.event or args.retry_review or args.retry_context or args.review_at or args.add_calls is not None):
        parser.error('new events and budget extensions require resume')
    if args.data and not args.event:
        parser.error('--data requires --event')
    if args.submission_id is not None and args.message is None and not args.event and not args.review_at:
        parser.error('--submission-id requires --message, --event or --review-at')
    if (args.add_output_tokens is not None or args.add_request_bytes is not None) and args.add_calls is None:
        parser.error('added allocations require --add-calls')
    limits = {'calls': args.max_calls,
              'output_tokens': args.max_output_tokens or args.max_calls * 5000,
              'request_bytes': args.max_request_bytes or args.max_calls * 70000}
    if not (1 <= limits['calls'] <= 200 and 1 <= limits['output_tokens'] <= 2000000
            and 1 <= limits['request_bytes'] <= 20000000):
        parser.error('allocation exceeds the supported bounded range')
    goal_text = args.goal_file.read_text(encoding='utf-8-sig') if args.goal_file else args.goal
    if args.action == 'start' and not args.pursuit:
        execution_goal({'business_goal': goal_text, 'execution_protocol': EXECUTION_PROTOCOL})
    if args.action == 'status':
        from Nervous.provider import ProviderCalls
        from Nervous.storage import inspect_safely
        result = {'mind': inspect_safely(MindOrgan.inspect_directory, directory / 'mind'),
                  'execution': inspect_safely(Execution.inspect_directory, directory / 'execution'),
                  'pending': inspect_safely(NervousOrgan.inspect_directory, directory / 'nervous'),
                  'cost': inspect_safely(ProviderCalls.inspect_directory, directory / 'nervous' / 'calls')}
        settings = inspect_safely(NervousOrgan.read_settings, directory / 'nervous' / 'settings.json')
        result['stop_reason'] = settings.get('foreground_stop_reason')
        if settings.get('diagnostic'):
            result['settings_diagnostic'] = settings['diagnostic']
        if any(result[owner].get('status') == 'not_initialized' for owner in ('mind', 'execution')):
            result['initialization'] = 'pending'
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    from core.env_loader import load_env_file
    load_env_file()
    with ExitStack() as stack:
        nervous = NervousOrgan(directory / 'nervous', limits=limits)
        stack.callback(nervous.close)
        previous_handler = signal.signal(signal.SIGINT, lambda *_: nervous.calls.request_pause())
        stack.callback(signal.signal, signal.SIGINT, previous_handler)
        initial = nervous.initialize(goal=goal_text, workspace=args.workspace, context_mode=args.context_mode,
                                     pursuit=args.pursuit)
        mode = initial.get('context_mode', 'baseline')
        from Nervous.storage import fingerprint
        authority = ({'mind_id': initial['mind_id'], 'text': initial['pursuit'],
                      'ref': 'owner-scope:' + fingerprint([initial['mind_id'], initial['pursuit']])[:24]}
                     if 'pursuit' in initial else None)
        execution = Execution(directory / 'execution', nervous.calls, workspace=Path(initial['workspace']),
                              context_mode=mode, **({'stage1_authority': authority} if authority else {}))
        stack.callback(execution.close)
        mind = MindOrgan(directory / 'mind', nervous.calls, goal=initial.get('goal'),
                         execution_protocol=EXECUTION_PROTOCOL, context_mode=mode,
                         **({'stage1_authority': authority} if authority else {}))
        stack.callback(mind.close)
        if args.add_calls is not None:
            nervous.extend_budget(args.add_calls, output_tokens=args.add_output_tokens,
                                  request_bytes=args.add_request_bytes)
        if args.message is not None or args.event:
            nervous.submit(args.message if args.message is not None else args.data,
                           event_type=args.event or 'USER_MESSAGE', submission_id=args.submission_id)
        if args.review_at:
            nervous.schedule_review(args.review_at, args.review_reason, submission_id=args.submission_id)
        if args.retry_review:
            nervous.retry_cognition(mind.retry_activity())
        if args.retry_context:
            from Nervous.provider import BudgetPause
            try:
                mind.retry_context()
                execution.retry_context()
            except (BudgetPause, ValueError, OSError):
                parser.error('context_retry_not_admitted; use status to inspect the retained recovery state')
        result = nervous.run(mind, execution)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
