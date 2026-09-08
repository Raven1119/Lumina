"""Native submission and read/cite admission regressions from the real chain."""
import json

import pytest
from jsonschema import validate

from Mind.chain import ChainMind
from Mind.organ import MindOrgan, MindInput, MindResultEvent, Evidence
from Mind.test_chain import cognitive


@pytest.fixture(autouse=True)
def archived_submission_contract(monkeypatch):
    """These regressions exercise V51–V57 quoting/observation aliases.

    V58 removes those output fields; its source identity and recovery are tested
    at the actual current entry point in test_integration_redesign.
    """
    monkeypatch.setattr('Mind.chain.INTEGRATION_CONTRACT_VERSION', 'cognitive-chain-v57')


@pytest.mark.parametrize('escaped', [False, True])
def test_oversized_read_returns_capacity_and_can_narrow_after_restart(tmp_path, escaped):
    from Mind.chain import Session, read_json
    from Mind.host import activation_event, run_mind_once
    from Mind.test_chain import LocalTestPython
    from Mind.trace import _thaw

    workspace = tmp_path/'workspace'
    workspace.mkdir()
    directory = tmp_path/'session'
    refs, wires = [], []
    marker = 'Direct observation: measured value is 12.'
    text = marker + ('"\n'*750 if escaped else ' evidence'*350)

    def transport(role, wire):
        assert role == 'mind'
        wires.append(wire)
        if len(wires) == 1:
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': refs})
        if len(wires) == 2:
            assert marker not in json.dumps(wire)
            assert 'response_capacity' in json.dumps(wire)
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': [refs[0]]})
        available = wire['tools'][0]['input_schema']['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum']
        observed = [ref for ref in available if ref.startswith('activation:observation')][-1]
        return cognitive(updates=[{'kind':'belief','id':'new:measurement','claim':marker,
            'status':'supported','basis':[{'ref':observed,'quote':marker}]}])

    def reopen(**kwargs):
        return Session(directory, transport=transport, ipython=LocalTestPython(workspace), **kwargs)

    with reopen(workspace=workspace, goal='Assess the observations.') as session:
        refs.extend(session.source(text, 'observation-'+str(n)) for n in range(3))
        session.save()
        value = MindInput('read-capacity', 'Assess the observations.', 'goal', 1, 'Assess the observations.', 'run', 'waiting',
            (Evidence('catalogue', 'Available refs: '+','.join(refs), 'execution'),))
        session.nervous.publish(activation_event(value))
        receipt = run_mind_once(session.nervous, session.mind)
        assert receipt.status == 'waiting'
        request, = session.nervous.pending('mind.requests', 1)
        session.consult(request)
        result, = session.nervous.pending('mind.results', 1)
        observation = _thaw(result.data)['observation']
        capacity = json.loads(observation['text'])
        assert capacity['status'] == 'not_read'
        if escaped:
            assert capacity['required_text_chars'] < capacity['max_text_chars']
            assert capacity['required_observation_chars'] > capacity['max_observation_chars']
        else:
            assert capacity['required_text_chars'] > capacity['max_text_chars']
        assert [row['ref'] for row in capacity['sources']] == refs
        assert marker not in observation['text']
        assert session.mind.inspect().revision == 0

    with reopen() as session:
        receipt = run_mind_once(session.nervous, session.mind)
        assert receipt.status == 'waiting' and len(wires) == 2
        request, = session.nervous.pending('mind.requests', 1)
        assert list(request.data['payload']['refs']) == [refs[0]]
        session.consult(request)
        assert session.mind.inspect().revision == 0

    with reopen() as session:
        receipt = run_mind_once(session.nervous, session.mind)
        assert receipt.status == 'accepted' and len(wires) == 3
        assert session.mind.inspect().revision == 1
        item, = session.mind.inspect().items
        assert item['claim'] == marker and item['status'] == 'supported'
        assert marker in session.mind.read_source(item['basis'][0]['ref'])['text']
        records = read_json(directory/'mind/cognition.json')['records']
        activation = next(r['activation_id'] for r in records if r['kind'] == 'started')
        lineage = session.source_info(activation+':observation')
        assert lineage['kind'] == 'capability_response_capacity'
        assert lineage['requested_sources'] == refs
        assert 'retrieved_sources' not in lineage


def test_catalogued_source_is_readable_before_its_contents_are_citable(tmp_path):
    wires = []

    def transport(wire):
        wires.append(wire)
        assert wire['tool_choice'] == {'type': 'auto'}
        assert wire['thinking'] == {'type': 'enabled'}
        schema = wire['tools'][0]['input_schema']
        if len(wires) == 1:
            request = {'type': 'cognitive_step', 'updates': [], 'next': {
                'type': 'capability_request', 'capability': 'read_evidence', 'refs': ['file-version']}}
            validate(request, schema)
            read_schema = next(option['properties']['refs'] for option in schema['properties']['next']['oneOf']
                               if option['properties'].get('capability', {}).get('enum') == ['read_evidence'])
            assert 'file-version' in read_schema['items']['examples']
            assert '8000' in read_schema['description']
            basis_refs = schema['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum']
            assert 'file-version' not in basis_refs
            return cognitive({'type': 'capability_request', 'capability': 'read_evidence', 'refs': ['file-version']})
        assert 'The measured value is 12.' in json.dumps(wire)
        refs = schema['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum']
        observation = next(ref for ref in refs if ref.startswith('activation:observation'))
        return cognitive(updates=[{'kind': 'belief', 'id': 'new:measurement', 'claim': 'The measured value is 12.',
            'status': 'supported', 'basis': [{'ref': observation, 'quote': 'The measured value is 12.'}]}])

    with MindOrgan(directory=tmp_path/'mind', model=ChainMind(transport, readable_sources=lambda: ['file-version']),
                   available_capabilities=('read_evidence',)) as mind:
        receipt = mind.activate(MindInput('event', 'Review the measurement.', 'goal', 1, 'Deliver accurately.', 'run', 'waiting',
            evidence=(Evidence('catalogue', 'File reference: file-version', 'execution'),)))
        assert receipt.status == 'waiting'
        receipt = mind.accept_result(MindResultEvent(receipt.request.request_ref, {
            'capability': 'read_evidence', 'text': 'The measured value is 12.', 'origin': 'execution'}))
        assert receipt.status == 'accepted'
        assert len(wires) == 2


@pytest.mark.parametrize('thinking', [False, True])
def test_mind_reasoning_mode_preserves_full_checkpoint_and_read_continuation_after_restart(tmp_path, thinking):
    from dataclasses import replace
    from Mind.trace import _thaw
    wires, previous = [], []
    def transport(wire):
        wires.append(wire)
        assert wire['thinking'] == {'type': 'enabled' if thinking else 'disabled'}
        if thinking:
            assert wire['output_config'] == {'effort': 'low'} and 'temperature' not in wire
        else:
            assert wire['temperature'] == 0 and 'output_config' not in wire
        assert 'SOURCE owner-scope\nOnly the current reporting window is authorized.' in wire['messages'][0]['content']
        if len(wires) == 1:
            return cognitive(updates=[{'kind':'belief', 'id':'new:scope',
                'claim':'Only the current reporting window is authorized.', 'status':'supported',
                'basis':[{'ref':'owner-scope', 'quote':'Only the current reporting window is authorized.'}]}])
        assert previous[0]['id'] in json.dumps(wire)
        if len(wires) == 2:
            return cognitive({'type':'capability_request', 'capability':'read_evidence', 'refs':['measurement-file']})
        assert 'The observed value is 12.' in json.dumps(wire)
        refs = wire['tools'][0]['input_schema']['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum']
        observed = next(ref for ref in refs if ref.startswith('activation:observation'))
        return cognitive(updates=[*previous, {'kind':'belief', 'id':'new:measurement',
            'claim':'The observed value is 12.', 'status':'supported',
            'basis':[{'ref':observed, 'quote':'The observed value is 12.'}]}])
    def model():
        return ChainMind(transport, readable_sources=lambda: ['measurement-file'], thinking=thinking)
    value = MindInput('seed', 'Record the authorization.', 'goal', 1, 'Report the current window accurately.',
        'run', 'waiting', (Evidence('owner-scope', 'Only the current reporting window is authorized.', 'execution'),))
    with MindOrgan(directory=tmp_path, model=model(), available_capabilities=('read_evidence',)) as mind:
        assert mind.activate(value).status == 'accepted'
        previous.extend(_thaw(item) for item in mind.inspect().items)
        waiting = mind.activate(replace(value, event_id='measure', trigger='A measurement is available.'))
        assert waiting.status == 'waiting'
        assert [_thaw(item) for item in mind.inspect().items] == previous
    with MindOrgan(directory=tmp_path, model=model(), available_capabilities=('read_evidence',)) as mind:
        assert len(wires) == 2
        accepted = mind.accept_result(MindResultEvent(waiting.request.request_ref,
            {'capability':'read_evidence', 'text':'The observed value is 12.', 'origin':'execution'}))
        assert accepted.status == 'accepted' and len(wires) == 3
        current = {_thaw(item)['id']: _thaw(item) for item in mind.inspect().items}
        assert current[previous[0]['id']] == previous[0]
        observed = next(item for item in current.values() if item['id'] != previous[0]['id'])
        assert mind.read_source(observed['basis'][0]['ref'])['text'] == 'The observed value is 12.'


@pytest.mark.parametrize('contract,accepted', [('cognitive-chain-v49', False), ('cognitive-chain-v51', True), ('cognitive-chain-v53', True)])
def test_complete_document_reads_are_versioned_and_continue_twice_across_restart(tmp_path, contract, accepted):
    from Mind.event_loop import CognitiveModel
    from Mind.trace import CONTINUED_NATIVE_PROTOCOL_VERSION
    texts = ['Analysis of the current artifact.\n'*150 + 'Final condition: preserve the signed input.',
             'Recorded inspection of the result.\n'*150 + 'Final observation: the signed input is unchanged.']
    assert all(4000 < len(text) < 8000 for text in texts)
    wires = []
    def transport(wire):
        wires.append(wire)
        if len(wires) <= 2:
            return cognitive({'type':'capability_request', 'capability':'read_evidence',
                              'refs':['document-'+str(len(wires))]})
        assert all(text.splitlines()[-1] in json.dumps(wire) for text in texts)
        refs = wire['tools'][0]['input_schema']['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum']
        observed = [ref for ref in refs if ref.startswith('activation:observation')]
        assert len(observed) == 2
        return cognitive(updates=[{'kind':'belief', 'id':'new:result',
            'claim':'The inspected result preserves the signed input.', 'status':'supported',
            'basis':[{'ref':ref, 'quote':text.splitlines()[-1]} for ref,text in zip(observed,texts)]}])
    def model():
        value = CognitiveModel(transport, contract=contract, thinking=False)
        value.native_protocol_version = CONTINUED_NATIVE_PROTOCOL_VERSION
        return value
    event = MindInput('review', 'Review both full documents.', 'goal', 1, 'Preserve the signed input.',
        'run', 'waiting', (Evidence('catalogue', 'Available: document-1 and document-2.', 'execution'),))
    with MindOrgan(directory=tmp_path, model=model(), available_capabilities=('read_evidence',)) as mind:
        receipt = mind.activate(event)
        assert receipt.status == 'waiting'
    for index,text in enumerate(texts):
        with MindOrgan(directory=tmp_path, model=model(), available_capabilities=('read_evidence',)) as mind:
            assert len(wires) == index + 1 and mind.inspect().revision == 0
            result = MindResultEvent(receipt.request.request_ref,
                {'capability':'read_evidence', 'text':text, 'origin':'execution'})
            if not accepted:
                with pytest.raises(ValueError, match='result_too_large'):
                    mind.accept_result(result)
                assert len(wires) == 1 and mind.inspect().revision == 0
                return
            receipt = mind.accept_result(result)
            assert receipt.status == ('waiting' if index == 0 else 'accepted')
    with MindOrgan(directory=tmp_path, model=None) as mind:
        item, = mind.inspect().items
        assert item['status'] == 'supported' and len(wires) == 3
        assert [mind.read_source(basis['ref'])['text'] for basis in item['basis']] == texts


@pytest.mark.parametrize('contract,capability,text,accepted', [
    ('cognitive-chain-v49', 'read_evidence', 'x'*2701, False),
    ('cognitive-chain-v51', 'read_evidence', 'x'*8000, True),
    ('cognitive-chain-v53', 'read_evidence', 'x'*8000, True),
    ('cognitive-chain-v51', 'read_evidence', 'x'*8001, False),
    ('cognitive-chain-v51', 'read_evidence', '\n'*4500, False),
    ('cognitive-chain-v51', 'analyze_world_model', 'x'*2701, False),
])
def test_larger_read_capacity_does_not_expand_old_or_builder_observations(contract, capability, text, accepted):
    from Mind.trace import TraceError, _validate_capability_observation
    observation = {'capability':capability, 'text':text,
                   'origin':'execution' if capability == 'read_evidence' else 'computation'}
    payload = {'capability':capability, 'observation':observation}
    if accepted:
        _validate_capability_observation(payload, requested_capability=capability, contract=contract)
    else:
        with pytest.raises(TraceError):
            _validate_capability_observation(payload, requested_capability=capability, contract=contract)


@pytest.mark.parametrize('contract', ['cognitive-chain-v49', 'cognitive-chain-v51', 'cognitive-chain-v53'])
def test_host_reads_full_single_file_or_preserves_historical_capacity_after_restart(tmp_path, contract):
    from Mind.chain import Session
    from Mind.event_loop import CognitiveModel
    from Mind.host import activation_event, run_mind_once
    from Mind.test_chain import LocalTestPython
    from Mind.trace import _thaw
    workspace = tmp_path/'workspace'; workspace.mkdir()
    text = 'Documented inspection details.\n'*170 + 'EOF: the signed input is preserved.'
    assert 4000 < len(text) < 8000
    path = workspace/'decision.md'; path.write_text(text, encoding='utf-8')
    directory = tmp_path/'session'
    refs, calls = [], []
    def transport(role, wire):
        assert role == 'mind'
        calls.append(wire)
        if len(calls) == 1:
            return cognitive({'type':'capability_request', 'capability':'read_evidence', 'refs':refs})
        basis = wire['tools'][0]['input_schema']['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum']
        observed = next(ref for ref in basis if ref.startswith('activation:observation'))
        return cognitive(updates=[{'kind':'belief', 'id':'new:result', 'claim':'The signed input is preserved.',
            'status':'supported', 'basis':[{'ref':observed, 'quote':text.splitlines()[-1]}]}])
    with Session(directory, workspace=workspace, goal='Assess the complete document.',
                 transport=transport, ipython=LocalTestPython(workspace)) as session:
        if contract == 'cognitive-chain-v49':
            # Build a genuine pending old-contract activity before the host restarts.
            session.mind.close()
            session.mind = MindOrgan(directory=directory/'mind', model=CognitiveModel(
                lambda wire: transport('mind', wire), contract=contract, thinking=False),
                available_capabilities=('read_evidence',))
        refs.append(session.source(path.read_text(encoding='utf-8'), 'file:decision.md'))
        session.save()
        session.nervous.publish(activation_event(MindInput('read', 'Read the complete document.', 'goal', 1,
            'Assess the complete document.', 'run', 'waiting', (Evidence('catalogue', refs[0], 'execution'),))))
        assert run_mind_once(session.nervous, session.mind).status == 'waiting'
    with Session(directory, transport=transport, ipython=LocalTestPython(workspace)) as session:
        request, = session.nervous.pending('mind.requests', 1)
        session.consult(request)
        event, = session.nervous.pending('mind.results', 1)
        observed = _thaw(event.data)['observation']
        if contract == 'cognitive-chain-v49':
            capacity = json.loads(observed['text'])
            assert capacity['status'] == 'not_read'
            assert (capacity['max_text_chars'], capacity['max_observation_chars']) == (2700, 3000)
            assert text.splitlines()[-1] not in observed['text'] and len(calls) == 1
        else:
            assert text in observed['text']
            assert run_mind_once(session.nervous, session.mind).status == 'accepted'
            item, = session.mind.inspect().items
            assert text.splitlines()[-1] in session.mind.read_source(item['basis'][0]['ref'])['text']
            assert len(calls) == 2


@pytest.mark.parametrize('new_protocol,correct_ref,true_quote', [
    (False, True, True), (True, True, True), (True, False, True), (True, True, False),
])
def test_reference_enum_correction_is_one_versioned_model_retry_not_a_host_alias(tmp_path, new_protocol, correct_ref, true_quote):
    from Mind.trace import MindTrace, CONTINUED_NATIVE_PROTOCOL_VERSION, REFERENCE_REPAIR_NATIVE_PROTOCOL_VERSION
    protocol = REFERENCE_REPAIR_NATIVE_PROTOCOL_VERSION if new_protocol else CONTINUED_NATIVE_PROTOCOL_VERSION
    quote = 'Final section: the signed input is preserved.'
    wires = []
    def transport(wire):
        wires.append(wire)
        if len(wires) <= 2:
            return cognitive({'type':'capability_request', 'capability':'read_evidence',
                'refs':['document-current' if len(wires) == 1 else 'additional-evidence']})
        reference = 'document-current'
        if len(wires) == 4:
            feedback = json.loads(wire['messages'][-1]['content'][0]['content'])
            error, = feedback['field_errors']
            assert error['path'] == ['updates', 0, 'basis', 1, 'ref'] and error['validator'] == 'enum'
            assert 'document-current' in error['message']
            if correct_ref:
                refs = wire['tools'][0]['input_schema']['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum']
                reference = next(ref for ref in refs if ref.startswith('activation:observation'))
        return cognitive(updates=[{'kind':'belief', 'id':'new:result',
            'claim':'The signed input is preserved.', 'status':'supported', 'basis':[
                {'ref':'catalogue', 'quote':'document-current'},
                {'ref':reference, 'quote':quote if true_quote else 'A claim absent from the actual source.'}]}])
    def model(version=protocol):
        result = ChainMind(transport, readable_sources=lambda: ['document-current', 'additional-evidence'], thinking=False)
        result.native_protocol_version = version
        return result
    event = MindInput('reference-check', 'Review the current document.', 'goal', 1, 'Preserve the signed input.',
        'run', 'waiting', (Evidence('catalogue', 'Available: document-current and additional-evidence.', 'execution'),))
    with MindOrgan(directory=tmp_path, model=model(), available_capabilities=('read_evidence',)) as mind:
        receipt = mind.activate(event)
        assert receipt.status == 'waiting'
    for text in ['document-current\n'+quote, 'The inspection was recorded.']:
        with MindOrgan(directory=tmp_path, model=model(), available_capabilities=('read_evidence',)) as mind:
            assert mind.inspect().revision == 0
            receipt = mind.accept_result(MindResultEvent(receipt.request.request_ref,
                {'capability':'read_evidence', 'text':text, 'origin':'execution'}))
    accepted = new_protocol and correct_ref and true_quote
    assert receipt.status == ('accepted' if accepted else 'failed')
    assert len(wires) == (4 if new_protocol else 3)

    path, = [p for p in tmp_path.glob('activation-*.jsonl') if '.native.' not in p.name]
    trace = MindTrace.reopen(path)
    records = trace.native_records()
    assert records[5]['recoverable'] is new_protocol and records[5]['accepted'] is False
    assert sum(bool(record.get('repair')) for record in records) == int(new_protocol)
    assert records[5]['response']['content'][-1]['input']['updates'][0]['basis'][1]['ref'] == 'document-current'
    original = {p.name:p.read_bytes() for p in tmp_path.glob('activation-*.jsonl')}
    with MindOrgan(directory=tmp_path, model=model(REFERENCE_REPAIR_NATIVE_PROTOCOL_VERSION)) as mind:
        assert mind.inspect().revision == int(accepted)
        assert mind.activate(event).status == ('duplicate' if accepted else 'failed')
        if accepted:
            item, = mind.inspect().items
            assert item['basis'][1]['ref'].startswith('activation-')
            assert quote in mind.read_source(item['basis'][1]['ref'])['text']
    assert {p.name:p.read_bytes() for p in tmp_path.glob('activation-*.jsonl')} == original
    assert len(wires) == (4 if new_protocol else 3)


@pytest.mark.parametrize('new_protocol,correct_quote,restart', [
    (False, True, False), (True, True, False), (True, False, False), (True, True, True),
])
def test_final_exact_source_quote_uses_one_versioned_repair_and_preserves_provisional_updates(
        tmp_path, new_protocol, correct_quote, restart):
    from Mind.trace import MindTrace, REFERENCE_REPAIR_NATIVE_PROTOCOL_VERSION, GROUNDED_REPAIR_NATIVE_PROTOCOL_VERSION
    protocol = GROUNDED_REPAIR_NATIVE_PROTOCOL_VERSION if new_protocol else REFERENCE_REPAIR_NATIVE_PROTOCOL_VERSION
    catalog = '[{"file":"input.json","chars":42}]'
    measured = 'The observed decision ended with incomplete_response.'
    compact = '"deflate_bytes":{"1":2126,"9":2044}'
    wires = []
    def submission(reference='catalogue', quote='failure'):
        return [{'kind':'belief', 'id':'new:measured', 'claim':'The run needs another decision.', 'status':'supported',
                 'basis':[{'ref':'input', 'quote':compact}, {'ref':reference, 'quote':quote}]}]
    def transport(wire):
        wires.append(wire)
        if len(wires) <= 2:
            # An uncommitted candidate is not forced to pass final grounding.
            return cognitive({'type':'capability_request', 'capability':'read_evidence', 'refs':['current']},
                             updates=submission())
        if len(wires) == 3:
            return cognitive({'type':'directive', 'text':'Prioritize the remaining authorized delivery.'}, updates=submission())
        assert len(wires) == 4
        feedback = json.loads(wire['messages'][-1]['content'][0]['content'])
        error, = feedback['field_errors']
        assert error['validator'] == 'exact_source_quote'
        assert error['path'] == ['updates', 0, 'basis', 1, 'quote']
        assert 'exact substring' in error['message']
        assert measured not in error['message'] and compact not in error['message']
        refs = wire['tools'][0]['input_schema']['properties']['updates']['items']['oneOf'][0]['properties']['basis']['items']['properties']['ref']['enum']
        observed = next(ref for ref in refs if ref.startswith('activation:observation'))
        return cognitive({'type':'directive', 'text':'Prioritize the remaining authorized delivery.'},
                         updates=submission(observed, measured) if correct_quote else submission())
    def model(interrupt=False):
        model = ChainMind(transport, readable_sources=lambda: ['current'], thinking=False)
        model.native_protocol_version = protocol
        if interrupt:
            def preflight(wire):
                content = wire['messages'][-1]['content']
                if isinstance(content, list) and content[0].get('is_error'):
                    raise SystemExit('test interruption before the reserved repair')
            model.preflight = preflight
        return model
    event = MindInput('quote-check', 'Review the run.', 'goal', 1, 'Deliver the authorized receipt.', 'run', 'waiting',
        (Evidence('catalogue', catalog, 'execution'), Evidence('input', '{'+compact+'}', 'execution')))
    with MindOrgan(directory=tmp_path, model=model(), available_capabilities=('read_evidence',)) as mind:
        receipt = mind.activate(event)
        assert receipt.status == 'waiting' and mind.inspect().revision == 0
    for index in range(2):
        with MindOrgan(directory=tmp_path, model=model(interrupt=restart and index == 1), available_capabilities=('read_evidence',)) as mind:
            result = MindResultEvent(receipt.request.request_ref,
                {'capability':'read_evidence', 'text':measured, 'origin':'execution'})
            if restart and index == 1:
                with pytest.raises(SystemExit, match='test interruption'):
                    mind.accept_result(result)
                assert len(wires) == 3 and mind.inspect().revision == 0
            else:
                receipt = mind.accept_result(result)
    if restart:
        with MindOrgan(directory=tmp_path, model=model(), available_capabilities=('read_evidence',)) as mind:
            assert len(wires) == 3
            receipt = mind.accept_result(result)
    accepted = new_protocol and correct_quote
    assert receipt.status == ('accepted' if accepted else 'failed')
    assert len(wires) == (4 if new_protocol else 3)
    path, = [p for p in tmp_path.glob('activation-*.jsonl') if '.native.' not in p.name]
    trace = MindTrace.reopen(path)
    records = trace.native_records()
    assert records[1]['accepted'] and records[3]['accepted']
    assert records[5]['accepted'] is not new_protocol and records[5]['recoverable'] is new_protocol
    assert records[5]['response']['content'][-1]['input']['updates'][0]['basis'][1] == {'ref':'catalogue','quote':'failure'}
    assert sum(bool(record.get('repair')) for record in records) == int(new_protocol)
    if new_protocol:
        assert sum(event.event_type == 'MIND_DIRECTIVE_ISSUED' for event in trace.events) == int(accepted)
    with MindOrgan(directory=tmp_path, model=None) as mind:
        assert mind.inspect().revision == int(accepted)
        if accepted:
            item, = mind.inspect().items
            assert item['basis'][1]['quote'] == measured
            assert mind.read_source(item['basis'][1]['ref'])['text'] == measured
