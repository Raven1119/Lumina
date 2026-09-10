"""One sampled source transition can match both registered attention and prediction rules."""
import pytest

from Mind.test_cognition_core import Script, response, step
from Nervous.test_attention_delivery import chain, register_source
from Nervous.organ import Event


def test_prediction_and_source_watch_share_review_but_repeated_change_is_new(tmp_path):
    script = Script(register_source, response(step()), response(step()), response(step()))
    with chain(tmp_path, script) as (nervous, mind, execution):
        nervous.run(mind, execution)
        execution.handle(Event('prediction-registration', 'mind.results', 'execution', 'prediction.watch', {
            'ref': 'model:test-registration', 'activity_id': 'test-prediction',
            'before_observation_ref': None, 'observation_file': 'observations.json',
            'check_spec': {}, 'task_contract': None}))
        (execution.workspace / 'observations.json').write_text('A', encoding='utf-8')
        nervous.run(mind, execution)
        assert len(script.wires) == 2
        state = nervous._load()
        merged = state['attention']['coalesced']
        assert len(merged) == 1
        signal, parent = next(iter(merged.items()))
        assert signal in state['completed'] and parent in state['completed']
        frame = state['attention']['frames'][parent]
        assert frame['views']['related_signals'][0]['event_id'] == signal
        (execution.workspace / 'observations.json').write_text('B', encoding='utf-8')
        nervous.run(mind, execution)
        assert len(script.wires) == 3
        assert len(nervous._load()['attention']['coalesced']) == 2
        (execution.workspace / 'observations.json').write_text('A', encoding='utf-8')
        nervous.run(mind, execution)
        assert len(script.wires) == 4
        assert len(nervous._load()['attention']['coalesced']) == 3


@pytest.mark.parametrize('cut', ['after_publish', 'before_merge', 'after_merge', 'after_frame', 'before_ack', 'after_ack'])
def test_coalesced_observation_resumes_after_durable_boundary_without_second_review(tmp_path, monkeypatch, cut):
    script = Script(register_source, response(step()), response(step()))
    with chain(tmp_path, script) as (nervous, mind, execution):
        nervous.run(mind, execution)
        execution.handle(Event('prediction-registration', 'mind.results', 'execution', 'prediction.watch', {
            'ref': 'model:test-registration', 'activity_id': 'test-prediction',
            'before_observation_ref': None, 'observation_file': 'observations.json',
            'check_spec': {}, 'task_contract': None}))
        (execution.workspace / 'observations.json').write_text('A', encoding='utf-8')
        save = nervous._save

        def interrupt(state):
            merged = state.get('attention', {}).get('coalesced', {})
            parent = next(iter(merged.values()), None)
            framed = parent in state.get('attention', {}).get('frames', {})
            completed = parent in state['completed']
            matched = bool(parent) and (
                cut in {'before_merge', 'after_merge'} and not framed
                or cut == 'after_frame' and framed and not completed
                or cut in {'before_ack', 'after_ack'} and completed)
            if matched and cut in {'before_merge', 'before_ack'}:
                raise SystemExit(cut)
            save(state)
            if matched:
                raise SystemExit(cut)

        monkeypatch.setattr(nervous, '_save', interrupt)
        if cut == 'after_publish':
            def interrupt_publication(event_id):
                raise SystemExit('after durable mailbox publication, before publisher acknowledgement')
            monkeypatch.setattr(execution, 'published', interrupt_publication)
        with pytest.raises(SystemExit):
            nervous.run(mind, execution)
    with chain(tmp_path, script) as (nervous, mind, execution):
        nervous.run(mind, execution)
        assert len(script.wires) == 2
        state = nervous._load()
        signal, parent = next(iter(state['attention']['coalesced'].items()))
        assert signal in state['completed'] and parent in state['completed']
        assert len([e for e in state['events'] if e['kind'] == 'attention.signal']) == 1
        assert state['attention']['frames'][parent]['views']['related_signals'][0]['event_id'] == signal
        nervous.run(mind, execution)
        assert len(script.wires) == 2


@pytest.mark.parametrize('frozen', [False, True])
def test_an_old_or_already_frozen_prediction_event_cannot_absorb_a_new_signal(tmp_path, frozen):
    script = Script(register_source, response(step()), response(step()))
    with chain(tmp_path, script) as (nervous, mind, execution):
        nervous.run(mind, execution)
        execution.handle(Event('prediction-registration', 'mind.results', 'execution', 'prediction.watch', {
            'ref': 'model:test-registration', 'activity_id': 'test-prediction',
            'before_observation_ref': None, 'observation_file': 'observations.json',
            'check_spec': {}, 'task_contract': None}))
        (execution.workspace / 'observations.json').write_text('A', encoding='utf-8')
        cycle = nervous.begin_observation_cycle() if frozen else 'earlier-independent-sampling-pass'
        parent, = execution.poll(observation_cycle=cycle)
        if frozen:
            nervous.publish(parent)
            execution.published(parent.event_id)
            frame = nervous.prepare_attention(parent, mind, execution)
            assert frame is not None
            nervous.poll_attention(execution, observation_cycle=cycle)
            assert nervous._load()['attention']['frames'][parent.event_id] == frame
        nervous.run(mind, execution)
        assert len(script.wires) == 3
        assert not nervous._load()['attention'].get('coalesced')
        assert not nervous.pending('mind')
