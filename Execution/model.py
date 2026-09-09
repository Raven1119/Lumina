"""Current Execution model dialogue, native actions and owner-authenticated recovery."""
from __future__ import annotations

import json
from dataclasses import replace

from Execution.deepseek_model import DeepSeekModel
from Nervous.provider import MODEL, BudgetPause, anthropic_messages, native_response
from Nervous.storage import canonical, fingerprint, plain

EXECUTION_PROTOCOL = 'Use ordinary Python in IPython to do the work. workspace files persist but Python variables do not survive restart. When direction needs judgment or hosted model analysis, call request_mind(question, evidence_files=(), model_ref="") inside a normal IPython cell. This publishes an advisory request after the cell commits; it does not require Wait. Mind provides high-level judgment and can consult an isolated Builder. You retain implementation decisions. Return results of guidance/model use through a concise request_mind so the persistent Mind can update its understanding. Ordinary tool results need no Mind review. Wait is for a real outside dependency. After business acceptance is satisfied write .lumina-complete with exact content done, then ClaimComplete. The marker is a runtime acknowledgment, not business acceptance. No network or child delegation is available.'
EXECUTION_ROLE = 'You are Lumina\'s Execution, implementing one owner-authorized task in its workspace. Mind owns high-level interpretation and direction. Use the owner\'s goal and received guidance to choose code, tools and local checks; do not repeat Mind\'s full strategic analysis.\n\nExtract the concrete deliverables and acceptance requirements, the applicable Mind conclusion and its conditions, and what the current workspace actually contains. Guidance is an attributed judgment, not a new observation. Preserve its material limits and unknowns in business outputs. If a necessary fact is unavailable, do not fill it with an assumption to make the artifact look complete. If local evidence conflicts with guidance or leaves a material prerequisite unresolved, report that exact discrepancy to Mind and request a decision; ordinary implementation fixes remain yours.\n\nUse normal IPython for implementation. For a significant question or result, call request_mind(question, evidence_files=(), model_ref="") from a normal cell. Include the decision-relevant issue, what action actually completed and under which parameters/conditions, what was observed and where, and what remains unknown. Name the relevant actual evidence files. Report an attempted action separately from a successful outcome. File existence, your statement, a calculation and a real observation establish different things; do not present the former as independent proof of the latter. A concise report is enough; do not copy the whole execution transcript or repeat Mind\'s reasoning.\n\nThis call queues an event after the cell commits and does not require Wait. Ordinary results need no Mind approval. Hosted model artifacts belong to Builder through Mind; do not edit them as business files. Wait is for a real outside dependency and is quiet/recoverable. Partial delivery, waiting and completion follow the owner\'s business requirements. Write the runtime marker and ClaimComplete only when those requirements are satisfied; the marker is not their evidence. New owner events can reopen the continuing goal.'
OWNER_TASK_GOAL_CHARS = 4000


def execution_goal(task):
    """Render one authorized task, keeping business acceptance verbatim."""
    if (type(task) is not dict or set(task) != {"business_goal", "execution_protocol"}
            or any(type(v) is not str or not v.strip() for v in task.values())):
        raise ValueError("invalid_owner_task")
    goal = task["business_goal"] + "\n\nExecution protocol:\n" + task["execution_protocol"]
    if len(goal) > OWNER_TASK_GOAL_CHARS:
        raise ValueError("owner_task_too_large")
    return goal


def restore_execution_goal_projection(document, owner_task):
    """Restore a verified owner goal after the bounded execution field projection.

    This changes the actual wire only for callers supplying the immutable owner
    task. No summary or inferred goal is authorized.
    """
    goal = execution_goal(owner_task)
    projected = document.get("goal")
    if (not isinstance(projected, dict) or projected.get("original_chars") != len(goal)
            or not isinstance(projected.get("text"), str)
            or not goal.startswith(projected["text"])):
        raise ValueError("execution_goal_projection_identity_conflict")
    return {**document, "goal": {"text": goal, "original_chars": len(goal), "truncated": False}}


def no_tool_response(response):
    """A received native response proving that no client tool was requested."""
    return (isinstance(response, dict) and response.get('stop_reason') in {'max_tokens', 'end_turn'}
        and isinstance(response.get('content'), list) and bool(response['content'])
        and all(isinstance(block, dict) and block.get('type') in {'text', 'thinking'}
                for block in response['content']))


def correction_wire(wire, response):
    return {**wire, 'messages': [*wire['messages'],
        {'role': 'assistant', 'content': response['content']},
        {'role': 'user', 'content': 'Protocol rejection: the received response contained no tool_use blocks, '
            'so no action was dispatched. Return one valid batch using the exposed tools; Wait remains valid. '
            'This is the only protocol correction for this owner decision. Preserve the current owner task '
            'and guidance. Do not repeat the prose analysis.'}]}


class ExecutionModel:
    """Reuse the supported action parser; actual Anthropic wire is retained."""
    identifier = MODEL
    tool_contracts = ('ipython(code: str)', 'wait(event_type: str)', 'claim_complete()')

    def __init__(self, transport, *, owner_task=None, execution_context=None, role_prompt="",
                 incoming_event_pending=None,
                 max_output_tokens=8192, thinking=False):
        self.transport, self.calls = transport, []
        self.owner_task, self.execution_context = owner_task, execution_context
        self.role_prompt = role_prompt
        self.incoming_event_pending = incoming_event_pending
        self.max_output_tokens = max_output_tokens
        self.thinking = thinking

    def decide(self, request):
        record = {'context': request.context}
        self.calls.append(record)
        def send(payload):
            messages = anthropic_messages(payload['messages'])
            continuation = request.native_tool_continuation
            if self.thinking and continuation is not None:
                envelope = continuation.raw_provider_response.get('anthropic_response')
                if envelope is not None:
                    content = plain(envelope['content'])
                    previous = next(m for m in messages if m['role'] == 'assistant')
                    if ([b for b in content if b.get('type') == 'tool_use']
                            != [b for b in previous['content'] if b.get('type') == 'tool_use']):
                        raise ValueError('execution_native_envelope_mismatch')
                if envelope is not None and any(b.get('type') == 'thinking' for b in content):
                    previous['content'] = content
                else:
                    # Start a legal provider turn from this same owner checkpoint.
                    # Earlier nonthinking tools cannot acquire fabricated thinking blocks.
                    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': request.context}]}]
                    record['provider_turn_boundary'] = 'prior_response_without_thinking'
            if self.owner_task is not None:
                # The native pair acknowledges the prior tool, but decision state
                # and incoming events must come from this request, not its predecessor.
                first = messages[0]['content'][0]
                document = restore_execution_goal_projection(json.loads(request.context), self.owner_task)
                if self.incoming_event_pending is not None and not self.incoming_event_pending():
                    document['incoming_event'] = None  # Consumed wake remains in the owner history.
                for message in messages[1:]:
                    for result in message['content'] if isinstance(message['content'], list) else ():
                        if result.get('type') == 'tool_result':
                            value = json.loads(result['content'])
                            value['incoming_event'] = document.get('incoming_event')
                            result['content'] = canonical(value)
                if self.execution_context is not None:
                    decision_context = self.execution_context(document['state']['decision_count'])
                    history = decision_context['rounds']
                    document['owner_inputs'] = decision_context['owner_inputs']
                    document['received_guidance'] = decision_context['received_guidance']
                    document['guidance_scope'] = decision_context['guidance_scope']
                    document['decision_context_version'] = decision_context['version']
                    document['cognitive_feedback'] = decision_context.get('cognitive_feedback')
                    current = messages[1:3] if len(messages) >= 3 else None
                    rounds = [(entry, entry['native_messages']) for entry in history
                              if entry.get('native_messages') and entry['decision'] + 1 < document['state']['decision_count']]
                    if current:
                        latest = next((entry for entry in history
                            if entry.get('native_messages', False) is not False
                            and entry['decision'] + 1 == document['state']['decision_count']), {})
                        rounds.append((latest, current))
                    elif record.get('provider_turn_boundary'):
                        rounds = []  # A nonthinking boundary cannot inherit invented thinking.
                    selected, size = [], 0
                    for entry, pair in reversed(rounds):
                        has_thinking = any(b.get('type') == 'thinking' for b in pair[0]['content'])
                        if has_thinking != self.thinking:
                            break
                        length = len(canonical(pair))
                        if len(selected) == 6 or size + length > 60000:
                            if not selected:
                                raise ValueError('execution_native_context_bound')
                            break  # Omit whole older rounds, never cut a signature or a tool batch.
                        selected.append((entry, pair)); size += length
                    selected.reverse()
                    document['recent_execution_history'] = [
                        {key: value for key, value in entry.items() if key != 'native_messages'}
                        for entry, _ in selected if entry]
                    document['execution_history_scope'] = 'Completed owner rounds; the current checkpoint does not rewrite their historical events or results.'
                    messages = [messages[0], *(message for _, pair in selected for message in pair)]
                first['text'] = canonical({k: v for k, v in document.items()
                    if k not in {'mind_supervisor_directive', 'received_guidance', 'guidance_scope'}})
                if document.get('received_guidance'):
                    messages.append({'role': 'user', 'content': canonical({
                        'received_guidance': document['received_guidance'], 'scope': document['guidance_scope']})})
            context = json.loads(request.context)
            # Carry the existing one-shot advisory through native continuation.
            if context.get('mind_supervisor_directive'):
                messages.append({'role': 'user', 'content': context['mind_supervisor_directive']})
            wire = {'model': MODEL, 'system': payload['messages'][0]['content'] + ('\n\n' + self.role_prompt if self.role_prompt else ''), 'messages': messages,
                'tools': [{'name': t['function']['name'], 'description': t['function']['description'],
                           'input_schema': t['function']['parameters']} for t in payload['tools']],
                'max_tokens': self.max_output_tokens, 'temperature': 0, 'thinking': {'type': 'disabled'},
                'tool_choice': {'type': 'any'}}
            if self.thinking:
                wire['thinking'] = {'type': 'enabled'}
                wire['output_config'] = {'effort': 'low'}
                wire.pop('temperature', None)
            record['wire'] = wire
            response = self.transport(wire)
            if no_tool_response(response):
                record['rejected_attempt'] = {'wire': wire, 'response': response}
                correction_of = fingerprint(wire)
                wire = correction_wire(wire, response)
                record['wire'] = wire
                response = self.transport(wire, correction_of=correction_of)
            record['response'] = response
            if response.get('stop_reason') != 'tool_use':
                return response  # Retain the incomplete envelope; never normalize partial tools.
            blocks = [b for b in response.get('content', []) if b.get('type') == 'tool_use']
            if any(block.get('name') not in {tool['name'] for tool in wire['tools']} for block in blocks):
                raise ValueError('execution_tool_not_available')
            # Execution owns bounded batch parsing and validates every action before dispatch.
            native = native_response(response)
            if self.thinking:
                native['anthropic_response'] = response
            return native
        decision = DeepSeekModel(transport=send).decide(request)
        if 'response' in record and record['response'].get('stop_reason') != 'tool_use':
            decision = replace(decision, action=None, raw_provider_response=record['response'],
                               failure='model_native:incomplete_response')
        if 'wire' in record:
            decision = replace(decision, provider_wire_request=record['wire'])
        return decision


class ExecutionHistory:
    """Interpret provider records using Execution's committed decision identities.

    The provider ledger owns dispatch and cost. Only Execution decides whether a
    response is a known no-action attempt or belongs to a committed action.
    """
    def __init__(self, calls):
        self.calls = calls

    def attempt(self, wire, correction_of=None):
        """Reuse only known no-action attempts under one durable owner decision."""
        current = json.loads(wire['messages'][0]['content'][0]['text'])['state']
        prior = []
        for _, record in self.calls.records(role='execution'):
            if 'metadata' not in record:
                continue
            if (record['status'] == 'failed' and 'response' not in record and 'provider_rejection' not in record
                    and record.get('error') in {'ConnectError', 'ConnectTimeout', 'PoolTimeout'}):
                continue  # Connection setup failed; the reserved session cost remains charged.
            state = json.loads(record['wire']['messages'][0]['content'][0]['text'])['state']
            if (state.get('execution_id'), state['decision_count']) == (current.get('execution_id'), current['decision_count']):
                prior.append(record)
        metadata = {'base_wire_sha256': correction_of or fingerprint(wire), 'repair': correction_of is not None}
        if not prior:
            if correction_of is not None:
                raise ValueError('execution_correction_without_original')
            return None, metadata
        first = prior[0]
        base = fingerprint(first['wire'])
        if (len(prior) > 2 or first.get('metadata') != {'base_wire_sha256': base, 'repair': False}
                or any(record['status'] != 'received' or 'response' not in record for record in prior)):
            raise BudgetPause('execution_model_outcome_unknown')
        if not no_tool_response(first['response']):
            raise RuntimeError('execution_response_pending_owner_commit')
        if correction_of is None:
            if len(prior) == 2:
                repaired_base = {**prior[1]['wire'], 'messages': prior[1]['wire']['messages'][:-2]}
                if wire != repaired_base:
                    raise ValueError('execution_correction_identity_conflict')
            return first, metadata
        current_base = {**wire, 'messages': wire['messages'][:-2]}
        if (correction_of != fingerprint(current_base)
                or wire != correction_wire(current_base, first['response'])):
            raise ValueError('execution_correction_identity_conflict')
        if correction_of != base:
            # A known no-action response is still the original failed attempt.
            # Its one correction uses current owner context after a host review
            # without granting another first attempt.
            metadata.update(original_wire_sha256=base)
        if len(prior) == 2:
            second = prior[1]
            if second.get('metadata') != metadata or second['wire'] != wire:
                raise ValueError('execution_correction_identity_conflict')
            return second, metadata
        return None, metadata

    def history(self, committed_count=None, execution_ref=None, committed_calls=()):
        """Recent complete native rounds authenticated by the owning Execution log."""
        committed = dict(committed_calls)
        records, outcomes = [], {}

        def batch_fingerprint(blocks):
            return fingerprint([(b['id'], b['name'], json.dumps(b['input'], ensure_ascii=False,
                sort_keys=True, separators=(',', ':'), allow_nan=False)) for b in blocks])

        for path, record in reversed(self.calls.records(role='execution')):
            messages = record['wire']['messages']
            document = json.loads(messages[0]['content'][0]['text'])
            run = document['state'].get('execution_id')
            if execution_ref is not None and run != execution_ref:
                continue
            records.append((path, record, document))
            if (record.get('metadata') and committed_count is not None
                    and document['state']['decision_count'] >= committed_count):
                continue  # A pending correction cannot change its own reconstructed history.
            # Only the final pair belongs to this owner decision count. Earlier
            # pairs may now be replayed history, including repeated tool IDs.
            pairs = [(a, b) for a, b in zip(messages, messages[1:])
                     if a['role'] == 'assistant' and b['role'] == 'user'
                     and isinstance(a['content'], list) and isinstance(b['content'], list)
                     and any(block.get('type') == 'tool_result' for block in b['content'])]
            if not pairs:
                continue
            assistant, result = pairs[-1]
            blocks = [b for b in assistant['content'] if b.get('type') == 'tool_use']
            results = result['content']
            count = document['state']['decision_count']
            if (not blocks or batch_fingerprint(blocks) != committed.get(f'decision-{count:06d}')
                    or len(results) != len(blocks)
                    or any(b.get('type') != 'tool_result' for b in results)
                    or len({b['tool_use_id'] for b in results}) != len(blocks)
                    or {b['tool_use_id'] for b in results} != {b['id'] for b in blocks}):
                continue
            outcomes.setdefault((run, count), ([assistant, result], 'execution-call:' + path.stem))

        entries, size, seen = [], 0, set()
        for path, record, document in records:
            if 'response' not in record:
                continue
            count = document['state']['decision_count']
            if committed_count is not None and count >= committed_count:
                continue  # A provider answer alone is not a committed owner action.
            decision_id = f'decision-{count + 1:06d}'
            blocks = [b for b in record['response'].get('content', []) if b.get('type') == 'tool_use']
            if batch_fingerprint(blocks) != committed.get(decision_id):
                continue
            if decision_id in seen:
                continue
            seen.add(decision_id)
            pair, result_ref = outcomes.get((document['state'].get('execution_id'), count + 1), (None, None))
            entry = {'call_ref': 'execution-call:' + path.stem, 'decision': count,
                     'native_messages': pair, 'result_call_ref': result_ref}
            if any(b['name'] in ('wait', 'claim_complete') for b in blocks):
                entry['result_scope'] = 'control_continuation_not_action_observation'
            length = len(json.dumps(entry, ensure_ascii=False))
            if len(entries) == 6 or size + length > 60000:
                break  # Drop whole older rounds; never cut thinking or a batch.
            entries.append(entry)
            size += length
            if pair is None and (committed_count is None or count + 1 != committed_count):
                break  # Do not fill an unavailable round with older actions.
        return list(reversed(entries))
