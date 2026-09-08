"""Test-only pre-cognition V62 checkpoint for downstream execution regressions.

New user entry is covered by test_user_event_chain. These fixtures start from
an existing run; they do not claim that Mind has approved a new user goal.
"""
from pathlib import Path

from Execution import FileContentEquals
from Mind.chain import PROTOCOL, Session, write_json
from Mind.task_view import execution_goal, fingerprint


def execution_checkpoint(directory, *, workspace=None, goal=None, limits=None, **options):
    path = Path(directory) / 'session.json'
    fresh = not path.exists()
    if fresh:
        # Exact initial state shape from the task-start V62 Session constructor.
        state = {'version': 'cognitive-chain-v62', 'workspace': str(Path(workspace).resolve(strict=True)),
            'task': {'business_goal': goal, 'execution_protocol': PROTOCOL},
            'limits': limits or {'calls': 40, 'output_tokens': 200000, 'request_bytes': 2800000},
            'handled': [], 'obligation': None, 'predictions': [], 'deliveries': [], 'sources': {}}
        execution_goal(state['task'])
        write_json(path, {'state': state, 'sha256': fingerprint(state)})
    session = Session(directory, workspace=workspace, goal=goal, limits=limits, **options)
    if fresh:
        try:
            session.execution.run_goal(execution_goal(session.state['task']),
                FileContentEquals('.lumina-complete', 'done'), defer_actions=True)
        except BaseException:
            session.close()
            raise
    return session
