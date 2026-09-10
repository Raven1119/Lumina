"""Opt-in persistent pursuit uses the real event owners, with scripted model output."""
import json
from contextlib import ExitStack

from Mind.organ import MindOrgan
from Mind.model import MindModel
from Mind.test_organ_events import Script, final, EmptyExecution
from Nervous.organ import NervousOrgan


def test_registered_cognition_read_routes_to_real_owner(tmp_path):
    from Nervous.views import read_views
    mind = MindOrgan(tmp_path / 'mind', None, stage1_authority={
        'mind_id': 'mind-1', 'ref': 'scope-1', 'text': 'Review without action.'},
        model=MindModel(Script()))
    try:
        alias = 'view:mind.cognition?item_ref=not-created'
        result = read_views([alias], mind=mind, execution=EmptyExecution(), attention_view=lambda: {})
        source, = json.loads(result['observation']['text'])['sources']
        assert json.loads(source['text'])['missing'] is True
        assert result['aliases'] == {alias: source['ref']}
        catalogue = read_views(['view:mind.cognition'], mind=mind, execution=EmptyExecution(),
                               attention_view=lambda: {})
        record, = json.loads(catalogue['observation']['text'])['sources']
        assert json.loads(record['text'])['content'] == mind.cognition.attention_catalogue()
    finally:
        mind.close()


def test_attention_selects_accepted_task_instead_of_superseded_proposal(tmp_path, monkeypatch):
    from Nervous.organ import Event
    mind = MindOrgan(tmp_path / 'mind', None, stage1_authority={
        'mind_id': 'mind-1', 'ref': 'scope-1', 'text': 'Review without action.'},
        model=MindModel(Script()))
    try:
        monkeypatch.setattr(mind.cognition, 'pursuit_state', lambda: {
            'task': {'id': 'task-a', 'revision': 2}, 'intentions': {}})
        event = Event('reassess', 'execution', 'mind', 'execution.changed', {
            'snapshot': {'task_contract': {'id': 'task-a', 'revision': 1}}})
        assert mind.attention_view(event)['content']['task'] == {'id': 'task-a', 'revision': 1}
    finally:
        mind.close()


def test_taskless_authorized_review_is_frozen_and_restart_is_quiet(tmp_path):
    authority = {'mind_id': 'mind-stage1', 'ref': 'scope-source',
                 'text': 'Within this workspace assess possible improvements; you may choose no task.'}
    script = Script(final())
    body = EmptyExecution()
    with ExitStack() as stack:
        nervous = stack.enter_context(NervousOrgan(tmp_path / 'nervous'))
        mind = MindOrgan(tmp_path / 'mind', nervous.calls, stage1_authority=authority,
                         model=MindModel(script))
        stack.callback(mind.close)
        original = nervous.submit(authority['text'], event_type='USER_SCOPE')
        result = nervous.run(mind, body)
        assert result['mind']['pursuit']['task'] is None
        assert result['mind']['pursuit']['intentions'] == {}
        assert len(script.wires) == 1
        frames = nervous._load()['attention']['frames']
        frame = next(iter(frames.values()))
        assert frame['views']['mind']['scope'] == authority['mind_id']
        assert original.event_id in json.dumps(frame)
        assert 'attention' in json.dumps(script.wires[0])
    with ExitStack() as stack:
        nervous = stack.enter_context(NervousOrgan(tmp_path / 'nervous'))
        mind = MindOrgan(tmp_path / 'mind', nervous.calls, model=MindModel(script))
        stack.callback(mind.close)
        nervous.run(mind, body)
        assert len(script.wires) == 1 and mind.cognition.inspect().revision == 1
