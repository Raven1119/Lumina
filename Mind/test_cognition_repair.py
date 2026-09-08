"""Current aggregate claims can cite their complete, actually visible basis."""
import pytest
from jsonschema import validate, ValidationError

from Mind.organ import MindOrgan, MindInput, Evidence, cognitive_step_schema, _basis
from Mind.chain import ChainMind
from Mind.test_chain import cognitive
from Mind.task_view import execution_goal


def aggregate(refs):
    return {'kind': 'belief', 'id': 'new:aggregate', 'status': 'supported',
            'claim': 'The four observed partitions together contain 10 records.',
            'basis': [{'ref': ref} for ref in refs]}


def test_four_source_basis_accepted_and_restarts_without_relabeling_old_contract(tmp_path):
    refs = [f'partition-{n}' for n in range(4)]
    sources = {ref: {'text': f'Partition {n} has {n+1} records.'} for n, ref in enumerate(refs)}
    item = aggregate(refs)
    schema = cognitive_step_schema(sources, contract='cognitive-chain-v64')
    value = {'type': 'cognitive_step', 'updates': [item], 'next': {'type': 'no_change'}}
    validate(value, schema)
    _basis(item['basis'], sources, contract='cognitive-chain-v64')
    with pytest.raises(ValidationError):
        validate(value, cognitive_step_schema(sources, contract='cognitive-chain-v62'))
    with pytest.raises(ValueError):
        _basis(item['basis'], sources, contract='cognitive-chain-v62')
    wires = []
    def respond(wire):
        wires.append(wire)
        return cognitive(updates=[item])
    task = {'business_goal': 'Understand the published observations.', 'execution_protocol': 'Internal protocol.'}
    with MindOrgan(directory=tmp_path, model=ChainMind(respond)) as mind:
        receipt = mind.activate(MindInput('event', 'Assess the combined observations.', 'goal', 1,
            execution_goal(task), 'run', 'waiting',
            evidence=tuple(Evidence(ref, sources[ref]['text'], 'execution') for ref in refs), owner_task=task))
        assert receipt.status == 'accepted'
        assert [dict(b) for b in mind.inspect().items[0]['basis']] == item['basis']
    with MindOrgan(directory=tmp_path, model=ChainMind(respond)) as mind:
        assert mind.inspect().items[0]['claim'] == item['claim']
        assert len(mind.inspect().items[0]['basis']) == 4
    assert len(wires) == 1


def test_basis_expansion_does_not_admit_unseen_sources():
    refs = [f'partition-{n}' for n in range(4)]
    with pytest.raises(ValueError, match='ungrounded_basis'):
        _basis(aggregate(refs)['basis'], {r: {'text': r} for r in refs[:-1]}, contract='cognitive-chain-v64')
