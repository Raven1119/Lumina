"""Mind-owned bounded analysis: isolated calculation, never workspace action.

The model sees only its question, selected source records and an optional prior
artifact. Provider calls and computations are durable and never blindly replayed.
"""
from __future__ import annotations

import json
from pathlib import Path
from jsonschema import validate, ValidationError
from Nervous.storage import fingerprint, write_json, read_json
from Nervous.provider import MODEL, BudgetPause
from Mind.world_model import (run_model, verify_run, run_static_model, verify_static_run,
    observation_contract_schema, compare_observation_contract, STATIC_PROTOCOL)

OUTPUT_TOKENS = 16384
MAX_TURNS = 6
ANALYSIS_VERSION = 'mind-analysis-v1'

ANALYSIS_PROMPT = 'You are the temporary analysis role consulted by Mind, in an independent bounded context. Only the supplied question, selected evidence and optional prior model are available. You cannot read additional files or act in the business workspace. Answer the question that matters to the decision, without assuming the proposed explanation is correct.\n\nExtract the observation object and scope, the relevant sourced values/rules, the candidate actions or conditions being compared, and missing inputs. Keep observed facts, assumptions and derived results separate. An identity, authorization, unobserved event or future availability cannot be inferred merely because a numerical model runs. If a missing prerequisite matters, report what is missing and which conclusions remain conditional.\n\nAnswer directly when calculation adds nothing. Otherwise use compute and define predict(inputs, action) returning named scalar quantities. Derive constants and units from supplied evidence; report what the actual computation used and obtained. For ordinary calculations provide source, inputs and action. Only a useful prospective reality check also needs observation_file and check_spec: candidate action/conditions, observed object, observation time, quantities and units. Do not invent future observations. Reuse a relevant prior program with source="" or revise it; keep unrelated history out. Temporal simulation remains available when state transitions are needed.\n\nReturn one concise report using the existing fields: answer gives the result and its decision-relevant scope/conditions; assumptions contains premises not established by the observations; unknowns states missing evidence and its consequence; run_ref identifies the actual computation if used, otherwise stays empty. Keep code and debugging here. A successful run establishes the reported calculation, not its real-world assumptions; matching already supplied data is not prospective verification. Source absence is not an observed negative fact. At most six calls and six computations; if unresolved, report the bounded result honestly.'

def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}

class Analysis:

    def __init__(self, directory, calls, read_source, compute=run_model, *, owner_task=None, thinking=True, static_compute=run_static_model):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.calls, self.read_source, self.compute = (calls, read_source, compute)
        self.owner_task, self.thinking, self.static_compute = (owner_task, thinking, static_compute)

    def analyze(self, request_ref, request, *, owner_task=None):
        # A continuing Mind freezes the relevant Task per request; old analysis
        # resumes under that original identity after later Task changes.
        owner_task = self.owner_task if owner_task is None else owner_task
        destination = self.directory / (fingerprint(request_ref) + '.json')
        if destination.exists():
            saved = read_json(destination)
            if saved['request_ref'] != request_ref or saved['request'] != request:
                raise ValueError('analysis_request_identity_conflict')
            return saved['observation']
        first_turn = self.directory / (fingerprint(request_ref) + '.turn-0.json')
        if first_turn.exists():
            frozen = read_json(first_turn)
            context = json.loads(frozen['wire']['messages'][0]['content'])
            if (frozen.get('request_sha256', fingerprint(request)) != fingerprint(request)
                    or context['question'] != request['question']
                    or [e['ref'] for e in context['evidence']] != request['refs']
                    or context['owner_task'] != owner_task
                    or (context['prior_model']['ref'] if context['prior_model'] else '') != request['model_ref']
                    or context.get('observation_file') != request.get('observation_file')):
                raise ValueError('analysis_request_identity_conflict')
            evidence, previous = context['evidence'], context['prior_model']
        else:
            evidence = [{'ref': ref, 'text': self.read_source(ref)} for ref in request['refs']]
            previous = self.model(request['model_ref']) if request['model_ref'] else None
            context = {'question': request['question'], 'evidence': evidence, 'prior_model': previous, 'owner_task': owner_task}
            if 'observation_file' in request:
                context['observation_file'] = request['observation_file']
        if sum((len(e['text']) for e in evidence)) > 24000:
            raise ValueError('builder_evidence_bound')
        messages = [{'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}]
        scalar = {'type': ['string', 'number', 'boolean', 'null']}
        fields = {'type': 'object', 'minProperties': 1, 'maxProperties': 16, 'additionalProperties': scalar}
        text = {'type': 'string', 'maxLength': 900}
        tools = [{'name': 'compute', 'description': 'Compute a conditional model; no workspace action.', 'input_schema': object_schema({'source': {'type': 'string', 'maxLength': 16000}, 'initial_observation': fields, 'actions': {'type': 'array', 'maxItems': 16, 'minItems': 1, 'items': fields}, 'observation_file': {'type': 'string', 'minLength': 1, 'maxLength': 128}})}, {'name': 'report', 'description': 'End analysis with a computed run or explicit unknown.', 'input_schema': object_schema({'run_ref': {'type': 'string', 'maxLength': 128}, 'answer': dict(text), 'assumptions': dict(text), 'unknowns': dict(text)})}]
        if 'observation_file' in request:
            tools[0]['input_schema']['properties']['observation_file']['const'] = request['observation_file']
        tools[0]['input_schema']['properties']['check_spec'] = observation_contract_schema()
        tools[0]['input_schema']['required'].append('check_spec')
        static_schema = object_schema({'source': {'type': 'string', 'maxLength': 16000, 'description': 'Complete Python program defining predict(inputs, action) -> scalar quantities. Empty reuses the supplied prior source.'}, 'inputs': fields, 'action': fields, 'observation_file': {'type': 'string', 'minLength': 1, 'maxLength': 128}, 'check_spec': observation_contract_schema(static=True)})
        if 'observation_file' in request:
            static_schema['properties']['observation_file']['const'] = request['observation_file']
        static_schema['required'] = ['source', 'inputs', 'action']
        static_schema['dependentRequired'] = {'observation_file': ['check_spec'], 'check_spec': ['observation_file']}
        tools[0]['input_schema'] = {'type': 'object', 'oneOf': [static_schema, tools[0]['input_schema']]}
        tools[1]['description'] = 'End with a structured analysis or one selected computation.'
        tools[1]['input_schema']['properties']['answer']['description'] = 'Concise conclusion about the selected computed run or sourced understanding. The program is already durable at run_ref; do not paste or describe a different program here.'
        tools[1]['input_schema']['properties']['answer']['maxLength'] = 2000
        runs = {previous['ref']: previous} if previous else {}
        turns, computations = ([], 0)
        for index in range(MAX_TURNS):
            tools[1]['input_schema']['properties']['run_ref'] = {'type': 'string', 'enum': ['', *runs]}
            turn_path = self.directory / (fingerprint(request_ref) + f'.turn-{index}.json')
            operation = fingerprint([owner_task, request_ref, index])
            metadata = {'analysis_request_sha256': fingerprint(request)}
            prompt = ANALYSIS_PROMPT
            wire = {'model': MODEL, 'system': prompt, 'messages': messages, 'tools': tools, 'tool_choice': {'type': 'auto'}, 'thinking': {'type': 'enabled'}, 'output_config': {'effort': 'low'}, 'max_tokens': OUTPUT_TOKENS}
            if not self.thinking:
                wire.update(thinking={'type': 'disabled'}, temperature=0, tool_choice={'type': 'any'})
                wire.pop('output_config')
            if turn_path.exists():
                turn = read_json(turn_path)
                if (turn.get('request_sha256', fingerprint(request) if turn['wire'] == wire else None)
                        != fingerprint(request)):
                    raise ValueError('analysis_request_identity_conflict')
                if 'response' not in turn:
                    try:
                        saved = self.calls.recover('builder', operation)
                    except BudgetPause as error:
                        raise RuntimeError('builder_call_outcome_unknown') from error
                    if saved is None and turn.get('operation') != operation:
                        raise RuntimeError('builder_call_outcome_unknown')
                    if saved is not None and (saved['wire'] != turn['wire'] or saved.get('metadata') != metadata):
                        raise ValueError('analysis_call_identity_conflict')
                    turn['response'] = (saved['response'] if saved is not None else
                        self.calls.call('builder', turn['wire'], operation=operation, metadata=metadata))
                    write_json(turn_path, turn)
                wire = turn['wire']
                response = turn['response']
            else:
                self.calls.ensure(wire, role='builder')
                turn = {'wire': wire, 'request_sha256': fingerprint(request), 'operation': operation}
                write_json(turn_path, turn)
                response = self.calls.call('builder', wire, operation=operation, metadata=metadata)
                write_json(turn_path, {**turn, 'response': response})
            messages, tools = wire['messages'], wire['tools']
            turns.append(str(turn_path.name))
            blocks = [b for b in response.get('content', []) if b.get('type') == 'tool_use']
            if not 1 <= len(blocks) <= 3 or response.get('stop_reason') != 'tool_use' or len({b['id'] for b in blocks}) != len(blocks) or (len(blocks) > 1 and any((b['name'] != 'compute' for b in blocks))):
                raise ValueError('builder_terminal_or_cardinality_failure')
            block = blocks[0]
            tool = next((tool for tool in tools if tool['name'] == block['name']), None)
            if tool is None:
                raise ValueError('builder_unknown_tool')
            try:
                for candidate in blocks:
                    validate(candidate['input'], tool['input_schema'])
            except ValidationError as error:
                errors = [{'path': list(issue.absolute_path), 'validator': issue.validator, 'message': f'String has {len(issue.instance)} characters; maximum allowed is {issue.validator_value}.' if issue.validator == 'maxLength' else issue.message[:600]} for issue in error.context or [error]]
                messages = [*messages, {'role': 'assistant', 'content': response['content']}, {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': b['id'], 'is_error': True, 'content': json.dumps({'status': 'rejected_before_commit', 'field_errors': errors}, ensure_ascii=False)} for b in blocks]}]
                continue
            value = block['input']
            if block['name'] == 'report':
                if value['run_ref'] and value['run_ref'] not in runs:
                    raise ValueError('builder_unknown_run')
                run = runs.get(value['run_ref'])
                static = bool(run and run['run']['protocol'] == STATIC_PROTOCOL)
                if run and (not static) and (verify_run(run['run'], [None] * len(run['run']['request']['actions']))['initialization']['status'] != 'matched'):
                    messages = [*messages, {'role': 'assistant', 'content': response['content']}, {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': block['id'], 'content': 'Report rejected: model initial rendering differs from supplied initial observation. Recompute a consistent model, or report unknown with empty run_ref.'}]}]
                    continue
                report = {'kind': 'COMPUTED_CONDITIONAL' if run else 'MODEL_ANALYSIS', 'model_ref': value['run_ref'], 'answer': value['answer'], 'assumptions': value['assumptions'], 'unknowns': value['unknowns']}
                report['evidence_refs'] = request['refs']
                if run:
                    report['prediction'] = run['run']['prediction'] if static else run['run']['prediction']['steps'][-1]
                    if static:
                        report['action'] = run['run']['request']['action']
                    if 'observation_file' in run:
                        report['observation_file'] = run['observation_file']
                    else:
                        report['kind'] = 'CALCULATION'
                        report['result'] = report.pop('prediction')
                    if 'check_spec' in run:
                        report['check_spec'] = run['check_spec']
                encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                if len(encoded) > 2700:
                    messages = [*messages, {'role': 'assistant', 'content': response['content']}, {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': block['id'], 'is_error': True, 'content': 'Report exceeds 2700 characters including the actual prediction and check_spec. Shorten your report fields; preserve material conditions and uncertainty.'}]}]
                    continue
                observation = {'capability': 'analyze_world_model', 'text': encoded, 'origin': 'computation'}
                write_json(destination, {'request_ref': request_ref, 'request': request, 'observation': observation, 'turns': turns})
                return observation
            if computations + len(blocks) > 6:
                messages = [*messages, {'role': 'assistant', 'content': response['content']}, {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': b['id'], 'is_error': True, 'content': f'Computation budget: {6 - computations} remain. Report an available run or unknown.'} for b in blocks]}]
                continue
            computations += len(blocks)
            results = []
            for block in blocks:
                value = block['input']
                compute_path = self.directory / (fingerprint([request_ref, index, block['id']]) + '.compute.json')
                if compute_path.exists():
                    saved = read_json(compute_path)
                    if saved['input'] != value:
                        raise ValueError('builder_computation_identity_conflict')
                    if saved['status'] == 'mechanism_stop':
                        raise RuntimeError(saved['error'])
                    if saved['status'] != 'received':
                        raise RuntimeError('builder_computation_outcome_unknown')
                    result = saved['result']
                    if result['kind'] == 'COMPUTED':
                        runs[result['run_ref']] = self.model(result['run_ref'])
                else:
                    self.calls.check_pause()
                    saved = {'input': value, 'status': 'reserved'}
                    write_json(compute_path, saved)
                    try:
                        source = value['source'] or (previous['run']['request']['source'] if previous else '')
                        static = 'inputs' in value
                        run = self.static_compute(source, value['inputs'], value['action']) if static else self.compute(source, value['initial_observation'], value['actions'])
                        if static:
                            verify_static_run(run)
                        else:
                            verify_run(run, [None] * len(value['actions']))
                        ref = 'model:' + fingerprint([request_ref, index, block['id'], run])[:32]
                        artifact = {'ref': ref, 'run': run, 'evidence_refs': request['refs'], 'request_ref': request_ref}
                        if 'observation_file' in value:
                            artifact['observation_file'] = value['observation_file']
                        if 'check_spec' in value:
                            compare_observation_contract(run, value['check_spec'], None)
                            predicted = run['prediction']['quantities'] if static else run['prediction']['steps'][-1]['observation']
                            missing = set(value['check_spec']['quantities']) - set(predicted)
                            if missing:
                                raise ValueError('predicted_quantity_fields_missing: ' + ', '.join(sorted(missing)))
                            artifact.update(check_spec=value['check_spec'], contract_version='prediction-observation-static-v1' if static else 'prediction-observation-v1')
                        artifact['artifact_sha256'] = fingerprint(artifact)
                        write_json(self.directory / (ref.replace(':', '-') + '.json'), artifact)
                        runs[ref] = artifact
                        result = {'kind': 'COMPUTED', 'run_ref': ref, 'prediction': run['prediction']}
                        if static:
                            result['action'] = run['request']['action']
                        else:
                            result['initialization_check'] = verify_run(run, [None] * len(value['actions']))['initialization']
                    except (ValueError, RuntimeError) as error:
                        if str(error) in {'container_cleanup_failed', 'container_output_incomplete'}:
                            write_json(compute_path, {**saved, 'status': 'mechanism_stop', 'error': str(error)})
                            raise
                        result = {'kind': 'COMPUTATION_FAILED', 'error': str(error)[:600]}
                    write_json(compute_path, {**saved, 'status': 'received', 'result': result})
                results.append({'type': 'tool_result', 'tool_use_id': block['id'], 'content': json.dumps(result, sort_keys=True)})
            messages = [*messages, {'role': 'assistant', 'content': response['content']}, {'role': 'user', 'content': results}]
        report = {'kind': 'ANALYSIS_INCOMPLETE', 'model_ref': '', 'reason': 'builder_activity_budget_exhausted', 'available_runs': list(runs), 'evidence_refs': request['refs'], 'analysis_version': ANALYSIS_VERSION}
        observation = {'capability': 'analyze_world_model', 'text': json.dumps(report, separators=(',', ':')), 'origin': 'computation'}
        write_json(destination, {'request_ref': request_ref, 'request': request, 'observation': observation, 'turns': turns, 'analysis_version': ANALYSIS_VERSION})
        return observation

    def model(self, ref):
        if not isinstance(ref, str) or not ref.startswith('model:') or len(ref) != 38 or any((c not in '0123456789abcdef' for c in ref[6:])):
            raise ValueError('invalid_model_reference')
        artifact = read_json(self.directory / (ref.replace(':', '-') + '.json'))
        if artifact['ref'] != ref:
            raise ValueError('model_identity_conflict')
        if artifact.get('artifact_sha256') != fingerprint({k: v for k, v in artifact.items() if k != 'artifact_sha256'}):
            raise ValueError('model_artifact_integrity_failure')
        if artifact['run']['protocol'] == STATIC_PROTOCOL:
            verify_static_run(artifact['run'])
        else:
            verify_run(artifact['run'], [None] * len(artifact['run']['request']['actions']))
        return artifact
