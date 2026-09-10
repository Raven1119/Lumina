"""Current native Mind model protocol and bounded, provenance-preserving input.

Only schema, source and atomic-state errors receive protocol feedback. Semantic
judgment stays with the model; cognition owns the final commit.
"""
from __future__ import annotations

import copy
import json
from jsonschema import Draft202012Validator
from Nervous.provider import MODEL, ProviderCalls, canonical
from Nervous.storage import fingerprint, plain
from Mind.cognition import cognitive_step_schema, observation_sources, _apply_updates
from Mind.task_view import COGNITIVE_CONTRACT_VERSION, evidence_read_limits
from Mind.trace import (NATIVE_PROTOCOL_VERSION, cognitive_phase, native_call_limit, output_limit,
                        project_activity_context)

OUTPUT_TOKENS = 16384
REQUEST_BYTES = 240000
EVIDENCE_READ_CHARS, _ = evidence_read_limits(COGNITIVE_CONTRACT_VERSION)
READ_SOURCE_METADATA_FIELDS = ('source_kind', 'label', 'offset', 'limit', 'total_chars', 'truncated')

MIND_PROMPT = "You are Lumina's persistent Mind, the single high-level decision owner for the authorized goal. User messages and important Execution events reach you through Nervous. You understand, investigate and decide; Execution chooses the implementation. Address the current event in the continuing goal, including the possibility that your earlier conclusion was mistaken.\n\nExtract the information needed for this decision:\n- The owner's requested outcome, acceptance conditions, authorization and any real deadline.\n- What each original source actually observes or states, about which object, population, time and conditions. Distinguish that from an actor's inference or a prior Mind/Builder judgment.\n- The prerequisites of the proposed conclusion or action. For each prerequisite that could change the decision, determine whether the visible evidence establishes it, rules it out, or leaves it unresolved. An observed value must match the relevant source, identity, scope and condition; a similar-looking record is not enough.\nUse this to choose the useful question. A request may be framed around arithmetic or file completion while the owner's decision depends on whether the interpretation is justified. Correct calculations support those calculations; they do not establish unobserved outcomes or the premises chosen for them.\n\nRead original evidence when a specific missing fact could change the decision. Use analyze_world_model for a useful comparison, conditional consequence or independent analysis of selected sources; state the open question and supply its needed evidence. Builder cannot fetch sources. Its output distinguishes assumptions from computation and observation, and returns to you in this activity. If no available observation can resolve a prerequisite, preserve it as unknown and select a proportionate action: inquiry, waiting, partial delivery or ending work under the actual goal. Closing collection or stopping work does not settle an unobserved event. No tool or analysis is mandatory.\n\nYour cognitive_step communicates the accepted result, not a reasoning transcript:\n1. updates: only changed or added understanding and questions. Express factual claims literally, with material conditions, scope and uncertainty. Include the source refs that supply both the rule and observation when an inference needs both. Unsubmitted items stay unchanged. Reuse affected IDs to replace their complete claim, basis, status and optional discriminator; revise downstream conclusions and questions that relied on a changed premise. Explicitly archive or use current to retire obsolete records. Preserve correct knowledge.\n2. Within each belief update, status evaluates the exact NEW sentence you submitted. supported means that sentence is warranted; contradicted means its negation is warranted; open means neither is established. A sentence saying an earlier statement was wrong is itself supported when that assessment is established. Do not carry a previous claim's status onto a rewritten opposite claim. Missing support for a positive claim is not proof of its negation. Prefer storing the currently warranted scoped fact or unresolved question over a narrative about your correction.\n3. next: decide whether Execution needs a CHANGE of business direction. NoChange accepts the current direction and ends this cognitive activity, including a successful result review; it neither prevents Execution from completing nor requires another report. You can repair cognition and acknowledge satisfactory work with NoChange. A Directive initiates further execution and another result review: use it for actual remaining business work or changed conditions, not to acknowledge, repeat fulfilled advice, or authorize runtime completion. Convey the new decision, its material conditions, decisive evidence or gap, and acceptance or priority implication. Execution handles its own completion protocol. Do not provide finished artifact bodies, code, commands or implementation procedures; filenames, fields and concrete business requirements can identify the target.\n\nBefore the final commit, read the affected old beliefs as propositions with prior_truth: true means the complete claim was judged true, false means it was judged false, and null means its truth was unresolved. This records your earlier judgment, not a verified fact. Re-evaluate each relevant complete claim against the sources; a correct statement ABOUT an error or missing evidence is true. Repair an incorrect prior_truth by updating the same record with the appropriate literal claim and status. Do not rewrite unaffected correct knowledge. Compare actual results with the goal and prior direction. If the work already meets its requirements and has reported its result, accept it and end this review; if a specific unmet requirement remains, give that direction. A report repeating your judgment or a runtime marker alone does not establish business success. This reconciliation is your own reasoning within this activity, not another agent or an external approval.\n\nWith execution_status null no execution run exists: a Directive starts authorized work; NoChange records understanding without starting work. Do not change the formal goal. You have read-only/analysis tools, not execution authority. Use one declared tool per response and consider its result. Only final cognitive_step commits cognition and guidance. The whole submission is bounded at 6000 characters and 16 updates; current knowledge at 16000 characters. Use concise results and only the calls needed."

def read_capacity(text):
    """Recognize owner-produced capacity metadata, never source-file assertions."""
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) and value.get('read_result') == 'capacity-v1' else None

def citation_sources(user_message):
    """Exact same text/ref map as MindOrgan._sources, no inferred replacements."""
    payload = json.loads(user_message)
    sources = {e['ref']: e['text'] for e in payload['cognition']['evidence']}
    observations = payload.get('observations', [{'ref': 'activation:observation', 'observation': payload['observation']}] if 'observation' in payload else [])
    for item in observations:
        observation = item['observation']
        if observation['capability'] in {'inspect_execution', 'read_evidence', 'analyze_world_model'}:
            sources.update({ref: record['text'] for ref, record in observation_sources(observation, item['ref'], payload['cognition'].get('contract_version')).items()})
    return sources

def parameter_errors(value, schema):
    """Select tagged variants so feedback names actual fields, not other branches."""
    base = copy.deepcopy(schema)
    checks = [((), value, base)]
    if 'updates' in base['properties']:
        base['properties']['next'] = {'type': 'object'}
        base['properties']['updates']['items'] = {'type': 'object'}
        if isinstance(value, dict):
            entries = [(('next',), value.get('next'), schema['properties']['next']['oneOf'], 'type')]
            if isinstance(value.get('updates'), list):
                entries += [(('updates', i), item, schema['properties']['updates']['items']['oneOf'], 'kind') for i, item in enumerate(value['updates'])]
            for path, item, variants, tag in entries:
                if isinstance(item, dict):
                    if tag not in item:
                        checks.append((path, item, {'required': [tag]}))
                        continue
                    candidates = [v for v in variants if item.get(tag) in v['properties'][tag]['enum']]
                    if len(candidates) > 1 and 'capability' in item:
                        candidates = [v for v in candidates if item['capability'] in v['properties'].get('capability', {}).get('enum', ())]
                    selected = candidates[0] if len(candidates) == 1 else None
                    checks.append((path, item, selected or {'oneOf': variants}))
    errors = []
    for path, item, selected in checks:
        for error in Draft202012Validator(selected).iter_errors(item):
            missing = error.validator_value.get('properties', {}).get('id', {}).get('const') if error.validator == 'contains' and isinstance(error.validator_value, dict) else None
            message = 'Final checkpoint is missing item ' + missing if missing else error.message
            if error.validator == 'maxLength':
                message = f'String has {len(error.instance)} characters; maximum allowed is {error.validator_value}.'
            errors.append({'path': list(path) + list(error.absolute_path), 'validator': error.validator, 'message': message})
    return errors

def _decode_native_response(response, wire):
    """Validate one declared tool; adapt read-only consultation to the reducer DTO."""
    tools = {tool['name']: tool for tool in wire['tools']}
    blocks = [block for block in response.get('content', []) if block.get('type') == 'tool_use']
    complete = (len(blocks) == 1 and isinstance(blocks[0].get('id'), str)
                and bool(blocks[0]['id'].strip()) and response.get('stop_reason') == 'tool_use')
    if complete and isinstance(blocks[0].get('name'), str) and blocks[0]['name'] in tools:
        block = blocks[0]
        value = block.get('input')
        errors = parameter_errors(value, tools[block['name']]['input_schema'])
        if not errors and block['name'] != 'cognitive_step':
            value = {'type': 'cognitive_step', 'updates': [],
                     'next': {**value, 'type': 'capability_request', 'capability': block['name']}}
        return value, errors, True
    if (complete and isinstance(blocks[0].get('name'), str) and blocks[0]['name'].strip()
            and isinstance(blocks[0].get('input'), dict)):
        return None, [{'validator': 'native_tool_name', 'path': ['name'], 'message':
            'Use a native tool declared in this request: ' + ', '.join(tools) +
            '. Return its complete input using the supplied schema. No input was interpreted or committed.'}], True
    return None, [{'validator': 'native_envelope', 'path': [],
                   'message': 'Expected one complete declared native tool return.'}], False


class MindModel:

    def __init__(self, transport, preflight=None, source_info=None, readable_sources=None, *, thinking=True, effort='low'):
        if type(thinking) is not bool or effort not in {'low', 'high'}:
            raise ValueError('invalid_mind_configuration')
        self.transport, self.preflight = (transport, preflight)
        self.source_info, self.readable_sources = (source_info, readable_sources)
        self.thinking, self.effort, self.calls = (thinking, effort, [])
        self.contract = self.cognitive_contract_version = COGNITIVE_CONTRACT_VERSION
        self.native_protocol_version = NATIVE_PROTOCOL_VERSION

    def preflight_context(self, activation, context, capabilities, execution_observation=None):
        projection = project_activity_context(activation, context, capabilities,
            initial_observation={'capability': 'inspect_execution', **execution_observation}
                                if execution_observation else None)
        wire = self._prepare_call(**projection.as_model_call())['wire']
        if self.preflight is not None:
            self.preflight(wire)

    def _prepare_call(self, recent_context, user_message, *, system_prompt):
        payload = json.loads(user_message)
        if payload['cognition'].get('contract_version') != self.contract:
            raise ValueError('cognitive_contract_context_mismatch')
        sources = citation_sources(user_message)
        background = payload['cognition'].pop('derived_history_background', None)
        if background is not None:
            payload['derived_history_background'] = background
        wire = {'model': MODEL, 'system': MIND_PROMPT, 'messages': [{'role': 'user', 'content': user_message}], 'tools': [{'name': 'cognitive_step'}], 'tool_choice': {'type': 'auto'}, 'max_tokens': OUTPUT_TOKENS, 'thinking': {'type': 'enabled' if self.thinking else 'disabled'}}
        if not self.thinking:
            wire['temperature'] = 0
        record = {'projection': {'recent_context': list(recent_context), 'user_message': user_message, 'system_prompt': system_prompt}, 'wire': wire, 'contract_version': self.contract}
        prior_items = payload['cognition'].pop('items')
        payload['cognition']['prior_model_judgments'] = [{'prior_status' if key == 'status' else key: [{'ref': b['ref']} for b in value] if key == 'basis' else value for key, value in item.items()} for item in prior_items]
        for item in payload['cognition']['prior_model_judgments']:
            if item['kind'] == 'belief':
                item['prior_truth'] = {'supported': True, 'contradicted': False, 'open': None}[item.pop('prior_status')]
        for evidence in payload['cognition']['evidence']:
            evidence.pop('text', None)
        if 'observation' in payload:
            payload['observation'] = {'capability': payload['observation']['capability'], 'source_ref': payload.get('observations', [{'ref': 'activation:observation'}])[-1]['ref']}
        observations = payload.get('observations', [])
        if observations:
            payload['observations'] = [{'ref': item['ref'], 'capability': item['observation']['capability']} for item in observations]
        annotations = {}
        origins = {e['ref']: e['origin'] for e in payload['cognition']['evidence']}
        for item in observations:
            origins.update({ref: source['origin'] for ref, source in observation_sources(item['observation'], item['ref'], self.contract).items()})
        for ref in sources:
            if ref.startswith('activation:observation'):
                capability = next((item['observation']['capability'] for item in observations if item['ref'] == ref), json.loads(user_message).get('observation', {}).get('capability'))
                annotations[ref] = {'kind': 'capability_result', 'capability': capability}
                capacity = read_capacity(sources[ref]) if capability == 'read_evidence' else None
                if capacity:
                    annotations[ref].update(kind='capability_response_capacity', requested_sources=[item['ref'] for item in capacity['sources']])
            elif self.source_info is not None:
                annotations[ref] = {**self.source_info(ref), 'origin': origins[ref]}
        for item in observations:
            observation = item['observation']
            if observation['capability'] != 'read_evidence':
                continue
            receipt = json.loads(observation['text'])
            if receipt.get('read_result') != 'sources-v1':
                continue
            metadata = receipt.get('source_metadata', {})
            if not isinstance(metadata, dict):
                raise ValueError('invalid_read_source_metadata')
            for source in receipt['sources']:
                ref = source['ref']
                if not ref.startswith('history:') or ref not in metadata:
                    continue
                if not isinstance(metadata[ref], dict):
                    raise ValueError('invalid_read_source_metadata')
                info = {key: metadata[ref][key] for key in READ_SOURCE_METADATA_FIELDS if key in metadata[ref]}
                kind = info.pop('source_kind', 'historical_activity_record')
                if kind not in {'historical_model_judgment', 'historical_activity_record'}:
                    raise ValueError('invalid_read_source_metadata')
                info.update(kind=kind, scope='Role-projected historical trace range, not a complete input '
                            'or a new reality observation. Model output records a past judgment.')
                annotations.setdefault(ref, {}).update(info)
        activity = payload['activation'].pop('trigger')
        goal = {'text': payload['activation'].pop('execution_goal_snapshot')}
        task_view = payload['cognition'].get('task_view')
        if task_view:
            goal_ref = 'owner-task:' + task_view['owner_task_sha256']
            goal = {'ref': goal_ref, 'text': sources[goal_ref]}
            task_view.pop('goal')
            task_view['goal_ref'] = goal_ref
        payload['cognition'].pop('evidence')
        payload = {'current_activity': activity, 'goal': goal, 'source_records': [{'ref': ref, **annotations.get(ref, {}), 'origin': origins[ref], 'text': text} for ref, text in sources.items() if ref != goal.get('ref')], **payload}
        content = json.dumps(payload, ensure_ascii=False)
        wire = record['wire']
        wire.update(system=MIND_PROMPT, max_tokens=OUTPUT_TOKENS, tool_choice={'type': 'auto'}, messages=[{'role': 'user', 'content': content}])
        if self.thinking:
            wire['output_config'] = {'effort': self.effort}
        wire['tools'][0]['input_schema'] = cognitive_step_schema(sources, prior_items, payload['available_capabilities'], contract=self.contract)
        if self.readable_sources is not None:
            readable = sorted(set(sources) | set(self.readable_sources()))
            if background is not None:
                readable = sorted(set(readable) | {item['ref'] for item in background['catalogue']})
            for option in wire['tools'][0]['input_schema']['properties']['next']['oneOf']:
                if option['properties'].get('capability', {}).get('enum') == ['analyze_world_model']:
                    option['properties']['model_ref']['description'] = 'Use the empty string for a new analysis. To reuse or revise a saved model, copy its existing model: reference from a prior Builder result exactly. This field is not a title or a name to invent for the analysis.'
                    option['description'] = 'Mind requests or revises hosted model analysis through this capability. Execution can change the authorized business workspace but cannot access or edit these hosted model artifacts. Use Builder here for model work; a Directive sends the resulting direction and conditions to Execution, not your unfinished analysis work. Request conclusions and decisive conditions, not a copy of the program. Computed programs remain in the artifact store. An incomplete analysis reports saved run refs and the actual failure so you can decide whether to continue, retain uncertainty or wait.'
                refs = option['properties'].get('refs')
                if refs is not None:
                    refs['items']['examples'] = readable
                    refs['description'] = 'Readable source references, including catalogued files whose contents are not yet visible. Reading does not require a basis citation. Cite returned text only after it appears in the exact source catalogue.'
                    if option['properties']['capability']['enum'] == ['read_evidence']:
                        refs['description'] += f' The combined returned text is limited to {EVIDENCE_READ_CHARS} characters including record labels. The file catalogue gives source character counts; choose only the evidence needed for this judgment, not every related file. Oversize returns capacity metadata without contents and consumes one consultation; use remaining consultations to narrow the request or obtain analysis.'
                        if background is not None:
                            refs['description'] += ' Historical trace pieces are readable by their history: ref; append :offset:limit to read an exact character range, with limit 1..6000. These are historical observations or model judgments, not new owner facts. A derived summary is not an evidence source.'
        commit = wire['tools'][0]
        options = commit['input_schema']['properties']['next']['oneOf']
        consultations = []
        for option in options:
            properties = option['properties']
            if properties['type']['enum'] == ['capability_request']:
                name = properties['capability']['enum'][0]
                schema = {**option, 'properties': {k: v for k, v in properties.items() if k not in {'type', 'capability'}}, 'required': [k for k in option['required'] if k not in {'type', 'capability'}]}
                consultations.append({'name': name, 'description': option.get('description', 'Read selected source evidence. This request commits no cognition or direction.'), 'input_schema': schema})
        options[:] = [o for o in options if o['properties']['type']['enum'] != ['capability_request']]
        for option in options:
            kind = option['properties']['type']['enum'][0]
            if kind == 'no_change':
                option['description'] = 'Commit understanding without new direction. An accepted result review settles its feedback obligation; Execution can finish under its existing goal. No new report is required.'
            elif kind == 'directive':
                option['description'] = 'New business direction for remaining work or changed conditions. Delivery creates further execution and feedback; not a completion acknowledgment.'
        commit['description'] = 'Commit affected cognitive revisions and their action consequence. Unsubmitted knowledge stays unchanged; current can explicitly select retained IDs. This function executes no action.'
        wire['tools'] = [commit, *consultations]
        return record

    def generate_from_trace(self, trace, projection):
        """Continue the durable activity, including known no-commit corrections."""
        record = self._prepare_call(**projection.as_model_call())
        base_wire = record['wire']
        phase = cognitive_phase(trace.events)
        if phase > 1:
            prior = trace.native_records()
            pair = next(i for i in range(len(prior) - 1, 0, -1)
                        if prior[i]['kind'] == 'result' and prior[i]['accepted']
                        and prior[i - 1]['phase'] == phase - 1)
            response, previous_wire = prior[pair]['response'], prior[pair - 1]['wire']
            block = next(b for b in response['content'] if b['type'] == 'tool_use')
            base_wire = {**base_wire, 'messages': [*previous_wire['messages'],
                {'role': 'assistant', 'content': response['content']},
                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': block['id'],
                    'content': self.continuation_content(trace, record)}]}]}
        while True:
            history = trace.native_records()
            phase_calls = [r for r in history if r['kind'] == 'call' and r['phase'] == phase]
            if phase_calls:
                base_wire = phase_calls[0]['wire']
            wire, repair = base_wire, False
            pending = bool(phase_calls and history[-1]['kind'] == 'call')
            if phase_calls and not pending:
                result = history[-1]
                if result['accepted']:
                    submission, errors, _ = _decode_native_response(result['response'], history[-2]['wire'])
                    if errors:
                        raise ValueError('native_accepted_response_invalid')
                    value = canonical(submission)
                    self.calls.append({**record, 'response': result['response'],
                                       'serialized_return': value, 'replayed': True})
                    return value
                if not result['recoverable']:
                    raise ValueError('native_repair_unavailable')
                response = result['response']
                block = next(b for b in response['content'] if b['type'] == 'tool_use')
                feedback = {'submission_status': 'rejected_before_commit', 'field_errors': result['errors'],
                    'contract': 'Correct the specified structural or source error. No cognition or guidance was committed. '
                                'This consumes the same activity call budget; substantive judgment remains yours.'}
                wire = {**base_wire, 'messages': [*history[-2]['wire']['messages'],
                    {'role': 'assistant', 'content': response['content']},
                    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': block['id'],
                        'is_error': True, 'content': canonical(feedback)}]}]}
                repair = True
            remaining = native_call_limit(trace.events) - sum(r['kind'] == 'call' for r in history)
            if remaining <= 0 and not pending:
                raise ValueError('native_call_budget')
            if remaining == 1 and not pending:
                # Preserve a final judgment slot instead of opening an unreturnable consultation.
                wire = json.loads(canonical(wire))
                wire['tools'] = [tool for tool in wire['tools'] if tool['name'] == 'cognitive_step']
                options = wire['tools'][0]['input_schema']['properties']['next']['oneOf']
                options[:] = [o for o in options if o['properties']['type']['enum'] != ['capability_request']]
                note = '\nThis is the last Mind call in this activity. Submit a final bounded judgment; unresolved questions may remain open.'
                if not wire['system'].endswith(note):
                    wire['system'] += note
            if pending:
                wire, repair = history[-1]['wire'], history[-1]['repair']
            else:
                if isinstance(self.transport, ProviderCalls):
                    self.transport.check_pause()
                if self.preflight is not None:
                    self.preflight(wire)
                if repair:
                    trace.reserve_native_repair()
                operation = fingerprint([trace.events[0].activation_id, plain(trace.events[0].payload),
                                         1 + sum(r['kind'] == 'call' for r in trace.native_records())])
                admission = {'operation': operation} if isinstance(self.transport, ProviderCalls) else {}
                trace.append_native(kind='call', phase=phase, repair=repair, wire=wire, **admission)
            operation = fingerprint([trace.events[0].activation_id, plain(trace.events[0].payload),
                                     sum(r['kind'] == 'call' for r in trace.native_records())])
            actual = {**record, 'wire': wire, 'phase': phase, 'repair': repair}
            self.calls.append(actual)
            try:
                if pending:
                    saved = (self.transport.recover('mind', operation)
                             if isinstance(self.transport, ProviderCalls) else None)
                    if saved is None and not isinstance(self.transport, ProviderCalls):
                        raise ValueError('native_result_unknown')
                    if saved is None and history[-1].get('operation') != operation:
                        from Nervous.provider import BudgetPause
                        raise BudgetPause('provider_call_outcome_unknown')
                    if saved is not None and saved['wire'] != wire:
                        raise ValueError('native_provider_identity_conflict')
                    # The provider ledger reserves durably before dispatch. No
                    # record proves this frozen local call was never dispatched.
                    response = (saved['response'] if saved is not None else
                                self.transport.call('mind', wire, operation=operation))
                elif isinstance(self.transport, ProviderCalls):
                    response = self.transport.call('mind', wire, operation=operation)
                else:
                    response = self.transport(wire)
            except Exception as error:
                trace.append_native(kind='result', response=None, errors=[{'transport': type(error).__name__}],
                                    accepted=False, recoverable=False)
                raise
            actual['response'] = response
            submission, errors, repairable_envelope = _decode_native_response(response, wire)
            value = canonical(submission) if submission is not None else ''
            limit = output_limit(trace.events[0].payload.get('cognitive_context', {}))
            if not errors and len(value) > limit:
                errors = [{'validator': 'serialized_limit', 'path': [],
                           'message': f'Cognitive step exceeds {limit} characters.'}]
            if not errors and submission['next']['type'] != 'capability_request':
                sources = {ref: {'text': text} for ref, text in citation_sources(projection.user_message).items()}
                current = json.loads(projection.user_message)['cognition']['items']
                try:
                    _apply_updates({item['id']: item for item in current}, submission['updates'],
                        sources, trace.events[0].activation_id, contract=self.contract,
                        current=submission.get('current'))
                except ValueError as error:
                    errors.append({'validator': 'effective_state', 'path': ['updates'],
                                   'message': 'Atomic state update rejected: ' + str(error)})
            trace.append_native(kind='result', response=response, errors=errors,
                                accepted=not errors, recoverable=bool(errors and repairable_envelope))
            actual['errors'] = errors
            if not errors:
                actual['serialized_return'] = value
                return value

    def continuation_content(self, trace, record):
        from Mind.trace import MODEL_OUTPUT_RECORDED, project_model_request
        previous = next((e for e in reversed(trace.events) if e.event_type == MODEL_OUTPUT_RECORDED))
        seen = citation_sources(project_model_request(trace.events[:previous.seq]).user_message)
        current = json.loads(record['wire']['messages'][-1]['content'])
        delta = {key: current[key] for key in ('observation', 'activity_budget', 'available_capabilities') if key in current}
        delta['source_records'] = [source for source in current['source_records'] if seen.get(source['ref']) != source['text']]
        prefix = 'Consultation result; current cognition is unchanged until cognitive_step.\n'
        return prefix + json.dumps(delta, ensure_ascii=False)
