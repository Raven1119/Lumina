"""Current understanding selection and advisory-driven continuation, without semantic oracles."""
from dataclasses import replace
import json

import pytest

from Mind.chain import ChainMind
from Mind.execution_checkpoint_fixture import execution_checkpoint
from Mind.organ import MindOrgan, _apply_updates, _thaw
from Mind.test_chain import LocalTestPython, cognitive, reply
from Mind.test_event_loop import input_value


def belief(identity, claim):
    return {'kind': 'belief', 'id': identity, 'claim': claim, 'status': 'supported',
            'basis': [{'ref': 'source'}]}


def selection(updates, current, next_value=None):
    value = cognitive(next_value, updates)
    value['content'][0]['input']['current'] = current
    return value


def test_selection_retires_current_claim_preserves_correct_knowledge_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr('Mind.chain.INTEGRATION_CONTRACT_VERSION', 'cognitive-chain-v59')
    value = input_value()
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive(updates=[
            belief('new:old', 'The current scope is provisional.'),
            belief('new:stable', 'Only settled entries count.')]), thinking=False)) as mind:
        assert mind.activate(value).status == 'accepted'
        old, stable = [_thaw(i) for i in mind.inspect().items]
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: selection([
            belief('new:replacement', 'The observed scope is now established.')],
            [stable['id'], 'new:replacement']), thinking=False)) as mind:
        assert mind.activate(replace(value, event_id='second')).status == 'accepted'
        after = {i['id']: _thaw(i) for i in mind.inspect().items}
        assert old['id'] not in after and after[stable['id']] == stable and len(after) == 2
    with MindOrgan(directory=tmp_path, model=None) as mind:
        assert {i['id']: _thaw(i) for i in mind.inspect().items} == after
    history = '\n'.join(p.read_text(encoding='utf-8') for p in tmp_path.glob('*.jsonl'))
    assert old['claim'] in history


@pytest.mark.parametrize('current', [['missing'], ['item-a', 'item-a'], 'item-a'])
def test_invalid_selection_is_atomic(current):
    old = {'item-a': belief('item-a', 'A sourced condition.')}
    with pytest.raises(ValueError, match='invalid_current_selection'):
        _apply_updates(old, [], {'source': {'text': 'Evidence'}}, 'event',
                       contract='cognitive-chain-v59', current=current)
    assert list(old) == ['item-a']


def test_empty_selection_and_legacy_delta_are_distinct():
    old = {'item-a': belief('item-a', 'A sourced condition.')}
    args = (old, [], {'source': {'text': 'Evidence'}}, 'event')
    assert _apply_updates(*args, contract='cognitive-chain-v59', current=[]) == {}
    assert _apply_updates(*args, contract='cognitive-chain-v58') == old
    with pytest.raises(ValueError, match='invalid_current_selection'):
        _apply_updates(*args, contract='cognitive-chain-v58', current=[])


def test_terminal_nochange_does_not_create_or_call_execution(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    calls = []
    responses = iter([reply('ipython', {'code': 'finish'}), reply('claim_complete', {})])
    def transport(role, wire):
        calls.append(role)
        return cognitive() if role == 'mind' else next(responses)
    options = dict(directory=tmp_path/'session', workspace=workspace, transport=transport,
                   ipython=LocalTestPython(workspace))
    with execution_checkpoint(goal='Deliver the checked result.', **options) as session:
        session.run()
        assert session.execution.state.status == 'completed'
        original = session.execution.state.execution_id
    before = calls.count('execution')
    with execution_checkpoint(**options) as session:
        session.run(owner_event=('OWNER_EVIDENCE', 'The delivered material and requirements remain unchanged.'))
        assert session.execution.state.status == 'completed'
        assert session.execution.state.execution_id == original
        assert session.state.get('active_run', 0) == 0
        assert session.state['owner_inputs'][-1]['status'] == 'reviewed'
    assert calls.count('execution') == before
    count = len(calls)
    with execution_checkpoint(**options) as session:
        session.run()
    assert len(calls) == count


@pytest.mark.parametrize('cut', [None, 'identity', 'initialized', 'bound', 'action'])
@pytest.mark.parametrize('native_request', [False, True, 'with_legacy'])
def test_terminal_directive_links_once_and_recovers_at_safe_boundaries(tmp_path, monkeypatch, cut, native_request):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    if native_request == 'with_legacy':
        (workspace/'.mind-request.json').write_text(json.dumps({'question': 'Earlier request.'}), encoding='utf-8')
    directory = tmp_path/'session'
    advice = 'The new evidence requires a revised deliverable within the original scope.'
    wires = []; reviewed = False
    def transport(role, wire):
        nonlocal reviewed
        wires.append((role, wire))
        if role == 'mind':
            if 'Additional sourced evidence is now available.' in json.dumps(wire) and not reviewed:
                reviewed = True
                return cognitive({'type': 'directive', 'text': advice})
            return cognitive()
        state = json.loads(wire['messages'][0]['content'][0]['text'])['state']
        return reply('ipython', {'code': 'finish'}) if state['decision_count'] == 0 else reply('claim_complete', {})
    class ActorPython(LocalTestPython):
        sent = False
        def execute(self, code):
            result = super().execute(code)
            if native_request and not self.sent:
                self.sent = True
                return replace(result, cognitive_request=json.dumps({
                    'question': 'Assess the observed result.', 'evidence_files': ['result.json'], 'model_ref': ''}))
            return result
    options = dict(directory=directory, transport=transport, ipython=ActorPython(workspace))
    with execution_checkpoint(workspace=workspace, goal='Deliver the checked result.', **options) as session:
        session.run()
        original = session.execution.state.execution_id
    before = len(wires)
    fired = False
    with execution_checkpoint(**options) as session:
        original_save = session.save
        def save():
            nonlocal fired
            original_save()
            state = session.execution.state
            at_cut = ((cut == 'identity' and session.state.get('active_run', 0) == 1 and state.execution_id == original)
                or (cut == 'bound' and any(d['status'] == 'bound' for d in session.state['deliveries'])))
            if at_cut and not fired:
                fired = True
                raise RuntimeError('test_power_loss')
        monkeypatch.setattr(session, 'save', save)
        original_capture = session.capture
        def capture(**kwargs):
            nonlocal fired
            value = original_capture(**kwargs)
            if cut == 'initialized' and not fired and session.execution.state.execution_id != original:
                fired = True
                raise RuntimeError('test_power_loss')
            return value
        monkeypatch.setattr(session, 'capture', capture)
        original_reconcile = session.reconcile_deliveries
        def reconcile():
            nonlocal fired
            original_reconcile()
            if cut == 'action' and not fired and session.execution.state.execution_id != original:
                fired = True
                raise RuntimeError('test_power_loss')
        monkeypatch.setattr(session, 'reconcile_deliveries', reconcile)
        if cut:
            with pytest.raises(RuntimeError, match='test_power_loss'):
                session.run(owner_event=('OWNER_EVIDENCE', 'Additional sourced evidence is now available.'))
        else:
            session.run(owner_event=('OWNER_EVIDENCE', 'Additional sourced evidence is now available.'))
    with execution_checkpoint(**options) as session:
        session.run()
        assert session.execution.state.status == 'completed'
        assert session.state['active_run'] == 1 and len(session.state['prior_runs']) == 1
        assert len(session.state['deliveries']) == 1
        assert session.state['deliveries'][0]['text'] == advice
        assert session.state['deliveries'][0]['call_ref']
    assert wires[before][0] == 'mind'
    execution = [wire for role, wire in wires[before:] if role == 'execution']
    assert len(execution) == 3  # action, completion request, acknowledgment after feedback
    assert sum(json.loads(w['messages'][0]['content'][0]['text'])['state']['decision_count'] == 0 for w in execution) == 1
    assert advice in json.dumps(execution[0])
    assert sum(role == 'mind' for role, _ in wires[before:]) == 2  # judgment and result feedback


@pytest.mark.parametrize('status,predecessor,allowed', [
    ('completed', 'run', True), ('failed', 'run', True),
    ('running', 'run', False), ('completed', 'another', False)])
def test_successor_binding_is_explicit_terminal_and_same_goal(tmp_path, status, predecessor, allowed):
    value = replace(input_value(), execution_status=status)
    with MindOrgan(directory=tmp_path, model=ChainMind(lambda _: cognitive(
            {'type': 'directive', 'text': 'The changed scope requires a revised deliverable.'}), thinking=False)) as mind:
        assert mind.activate(value).status == 'accepted'
        args = dict(execution_ref='successor', decision_id='decision-000001',
                    intention_ref='task', intention_revision=1, successor_of=predecessor)
        application = mind.prepare_directive(value.event_id, **args)
        assert bool(application) == allowed
        assert mind.prepare_directive(value.event_id, **{**args, 'intention_revision': 2}) is None
        if allowed:
            assert mind.prepare_directive(value.event_id, **args) == application
            assert mind.prepare_directive(value.event_id, **{**args, 'execution_ref': 'unrelated'}) is None
