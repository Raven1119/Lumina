"""Immutable current cognitive activity and bounded native-call recovery."""
from __future__ import annotations
import json
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence
from Mind.task_view import (COGNITIVE_CONTRACT_VERSION, context_limit, output_limit, project_observation,
    fingerprint, evidence_read_limits, execution_view_limits, analysis_question_limit, directive_limit)
TRACE_FORMAT_VERSION = 1
NATIVE_PROTOCOL_VERSION = "mind-native-v1:6-calls"
ACTIVITY_CALLS = 6
MAX_EVENT_BYTES = 272384
MAX_ACTIVATION_ID_CHARS = 128
MAX_DECISION_ID_CHARS = 128
MAX_TRIGGER_CHARS = 1000
MAX_GOAL_CHARS = 4000
MAX_STATUS_CHARS = 200
MAX_MODEL_OUTPUT_CHARS = 6000
MAX_OBSERVATION_CHARS = 3000
MAX_DIRECTIVE_CHARS = 6000
MAX_DECISION_INTENT_CHARS = 1000
MAX_OUTCOME_CHARS = 2000
MAX_FAILURE_CHARS = 2000
ACTIVATION_STARTED = "ACTIVATION_STARTED"
MODEL_OUTPUT_RECORDED = "MODEL_OUTPUT_RECORDED"
NATIVE_REPAIR_RESERVED = "NATIVE_REPAIR_RESERVED"
CAPABILITY_REQUESTED = "CAPABILITY_REQUESTED"
CAPABILITY_OBSERVED = "CAPABILITY_OBSERVED"
INITIAL_EXECUTION_OBSERVED = "INITIAL_EXECUTION_OBSERVED"
ACTIVATION_FINISHED = "ACTIVATION_FINISHED"
ACTIVATION_FAILED = "ACTIVATION_FAILED"
MIND_DIRECTIVE_ISSUED = "MIND_DIRECTIVE_ISSUED"
_EVENT_TYPES = {ACTIVATION_STARTED, MODEL_OUTPUT_RECORDED, NATIVE_REPAIR_RESERVED,
    CAPABILITY_REQUESTED, CAPABILITY_OBSERVED, INITIAL_EXECUTION_OBSERVED,
    ACTIVATION_FINISHED, ACTIVATION_FAILED, MIND_DIRECTIVE_ISSUED}
_ENVELOPE_KEYS = {"activation_id", "event_type", "payload", "seq", "source_event_seqs", "timestamp", "trace_format_version"}
_SAFE_FAILURE_CODES = {"capability_limit_exceeded", "capability_not_available", "invalid_activation_input",
    "invalid_execution_observation", "invalid_model_output", "invalid_trace", "model_failed",
    "observation_too_large", "trace_failed"}


def activity_steps(events):
    return ACTIVITY_CALLS


def native_call_limit(events):
    return ACTIVITY_CALLS


def cognitive_phase(events):
    return 1 + sum(e.event_type == CAPABILITY_OBSERVED for e in events)


def consultation_allowed(events):
    return cognitive_phase(events) + sum(e.event_type == NATIVE_REPAIR_RESERVED for e in events) < ACTIVITY_CALLS


def model_dependencies(events):
    return (0, *(e.seq for e in events if e.event_type in {INITIAL_EXECUTION_OBSERVED, CAPABILITY_REQUESTED, CAPABILITY_OBSERVED}))


def trace_event_limit(events):
    return 3 * ACTIVITY_CALLS + 3

def observation_ref(events, event):
    first = next((e for e in events if e.event_type == CAPABILITY_OBSERVED))
    return 'activation:observation' + (f':{event.seq}' if event.seq != first.seq else '')

class TraceError(ValueError):
    """A safe, detail-free trace validation or persistence failure."""

@dataclass(frozen=True)
class MindEvent:
    trace_format_version: int
    seq: int
    activation_id: str
    event_type: str
    timestamp: str
    payload: Mapping[str, object]
    source_event_seqs: tuple[int, ...]

@dataclass(frozen=True)
class ModelRequestProjection:
    recent_context: tuple[Mapping[str, str], ...]
    system_prompt: str
    user_message: str

    def as_model_call(self) -> dict[str, object]:
        """Return the exact mutable container shape required by ModelClient."""
        return {'recent_context': [dict(message) for message in self.recent_context], 'system_prompt': self.system_prompt, 'user_message': self.user_message}

@dataclass(frozen=True)
class ActivationReplay:
    activation_id: str
    model_requests: tuple[ModelRequestProjection, ...]
    model_outputs: tuple[str, ...]
    capability_request: Mapping[str, object] | None
    capability_observation: Mapping[str, object] | None
    final_result: Mapping[str, object] | None
    failure_code: str | None
    causal_chain: tuple[tuple[int, tuple[int, ...]], ...]

class MindTrace:
    """One current activity journal owned by cognition."""

    def __init__(self, path: Path, activation_id: str, *, fixed_timestamp: str | None, events: tuple[MindEvent, ...], read_only: bool) -> None:
        self._path = path
        self._activation_id = activation_id
        self._fixed_timestamp = fixed_timestamp
        self._events = events
        self._read_only = read_only

    @classmethod
    def create(cls, path: str | Path, *, activation_id: str, fixed_timestamp: str | None=None) -> MindTrace:
        target = Path(path)
        _validate_activation_id(activation_id)
        if fixed_timestamp is not None:
            _validate_timestamp(fixed_timestamp)
        if not target.parent.is_dir():
            raise TraceError('trace_parent_unavailable')
        try:
            if target.exists() and target.stat().st_size:
                raise TraceError('trace_already_exists')
        except OSError:
            raise TraceError('trace_unavailable') from None
        return cls(target, activation_id, fixed_timestamp=fixed_timestamp, events=(), read_only=False)

    @classmethod
    def reopen(cls, path: str | Path) -> MindTrace:
        target = Path(path)
        events = _read_events(target)
        _validate_sequence(events, require_terminal=False)
        return cls(target, events[0].activation_id, fixed_timestamp=None, events=events, read_only=True)

    @classmethod
    def inspect_path(cls, path):
        from Nervous.storage import inspect_jsonl
        events = []
        def accept(document):
            event = _event_from_document(document)
            candidate = tuple([*events, event])
            _validate_sequence(candidate, require_terminal=False)
            events.append(event)
        diagnostic = inspect_jsonl(path, accept, max_line_bytes=MAX_EVENT_BYTES)
        result = {**diagnostic, 'last_event_type': events[-1].event_type if events else None}
        if events and Path(path).with_suffix('.native.jsonl').exists():
            trace = cls(Path(path), events[0].activation_id, fixed_timestamp=None, events=tuple(events), read_only=True)
            records = []
            def accept_native(record):
                trace._validate_native([*records, record])
                records.append(record)
            result['native'] = inspect_jsonl(Path(path).with_suffix('.native.jsonl'), accept_native,
                                              max_line_bytes=trace._native_record_limit())
        return result

    @classmethod
    def reopen_for_finalization(cls, path: str | Path) -> MindTrace:
        target = Path(path)
        events = _read_events(target)
        if _validate_sequence(events, require_terminal=False) != 'after_model':
            raise TraceError('trace_not_finalizable')
        return cls(target, events[0].activation_id, fixed_timestamp=None, events=events, read_only=False)

    @classmethod
    def reopen_for_result(cls, path: str | Path) -> MindTrace:
        """Reopen only a cognitive request waiting for its host result event."""
        target = Path(path)
        events = _read_events(target)
        state = _validate_sequence(events, require_terminal=False)
        if state != 'waiting_result' or 'cognitive_context' not in events[0].payload or events[-1].payload['capability'] not in events[0].payload['available_capabilities']:
            raise TraceError('trace_not_waiting_for_result')
        return cls(target, events[0].activation_id, fixed_timestamp=None, events=events, read_only=False)

    @classmethod
    def reopen_for_native(cls, path: str | Path, *, allow_pending=False) -> MindTrace:
        """Open a native continuation; pending calls require provider ownership proof."""
        trace = cls.reopen(path)
        if trace.events[0].payload.get('native_protocol') != NATIVE_PROTOCOL_VERSION:
            raise TraceError('native_protocol_not_enabled')
        project_model_request(trace.events)
        records = trace.native_records()
        phase = cognitive_phase(trace.events)
        if records and records[-1]['kind'] == 'call':
            if not allow_pending or records[-1]['phase'] != phase:
                raise TraceError('native_result_unknown')
        elif records:
            recorded_phase = records[-2]['phase']
            if recorded_phase != phase and not (
                    recorded_phase == phase - 1 and records[-1]['accepted']
                    and trace.events[-1].event_type == CAPABILITY_OBSERVED):
                raise TraceError('native_result_unknown')
        elif phase != 1:
            raise TraceError('native_result_unknown')
        # No record for this phase means no dispatch: append_native(call) always
        # precedes transport. A completed consultation can safely start its next call.
        trace._read_only = False
        return trace

    def _native_record_limit(self):
        return 262144

    def native_records(self) -> list[dict]:
        """Actual provider requests and responses for this activity."""
        path = self._path.with_suffix('.native.jsonl')
        try:
            if path.stat().st_size > 2 * native_call_limit(self.events) * self._native_record_limit():
                raise TraceError('native_trace_too_large')
            records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self._validate_native(records)
        except FileNotFoundError:
            records = []
        except OSError as exc:
            raise TraceError('native_persistence_failed') from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise TraceError('invalid_native_trace') from exc
        for fence in (e for e in self.events if e.event_type == NATIVE_REPAIR_RESERVED):
            size = fence.payload['record_count']
            if len(records) < size or hashlib.sha256(_canonical_json(records[:size]).encode()).hexdigest() != fence.payload['prefix_sha256']:
                raise TraceError('native_prefix_lost')
        return records

    def reserve_native_repair(self):
        records = self.native_records()
        if any((e.event_type == NATIVE_REPAIR_RESERVED and e.payload['record_count'] == len(records) for e in self.events)):
            return
        self.append(NATIVE_REPAIR_RESERVED, {'record_count': len(records), 'prefix_sha256': hashlib.sha256(_canonical_json(records).encode()).hexdigest()}, source_event_seqs=(self.events[-1].seq,))

    def _validate_native(self, records):
        if len(records) > 2 * native_call_limit(self.events):
            raise TraceError('native_call_budget')
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise TraceError('invalid_native_record')
            if record.get('activation_id') != self._activation_id or record.get('seq') != index or record.get('version') != self.events[0].payload.get('native_protocol'):
                raise TraceError('native_identity_conflict')
            if index % 2 == 0:
                if (set(record) - {'operation'} != {'activation_id', 'seq', 'version', 'kind', 'phase', 'repair', 'wire'}
                        or 'operation' in record and (not isinstance(record['operation'], str)
                            or len(record['operation']) != 64)):
                    raise TraceError('invalid_native_call')
                if record['kind'] != 'call' or type(record['repair']) is not bool or type(record['phase']) is not int or (record['phase'] not in range(1, activity_steps(self.events) + 1)):
                    raise TraceError('invalid_native_call')
                previous = records[index - 1] if index else None
                same_phase = bool(index and record['phase'] == records[index - 2]['phase'])
                if record['repair'] != same_phase or (same_phase and (not previous['recoverable'])):
                    raise TraceError('invalid_native_repair')
                if not same_phase and (record['phase'] != (records[index - 2]['phase'] + 1 if index else 1) or (index and (not previous['accepted']))):
                    raise TraceError('invalid_native_phase')
            else:
                if set(record) != {'activation_id', 'seq', 'version', 'kind', 'response', 'errors', 'accepted', 'recoverable'}:
                    raise TraceError('invalid_native_result')
                if record['kind'] != 'result' or type(record['accepted']) is not bool or type(record['recoverable']) is not bool or (record['accepted'] and (record['recoverable'] or record['errors'])):
                    raise TraceError('invalid_native_result')

    def append_native(self, **payload) -> None:
        if self._read_only or self.events[0].payload.get('native_protocol') != NATIVE_PROTOCOL_VERSION:
            raise TraceError('native_trace_not_writable')
        records = self.native_records()
        record = {'activation_id': self._activation_id, 'seq': len(records), 'version': self.events[0].payload['native_protocol'], **payload}
        self._validate_native([*records, record])
        encoded = (_canonical_json(record) + '\n').encode('utf-8')
        if len(encoded) > self._native_record_limit():
            raise TraceError('native_record_too_large')
        try:
            new_file = not self._path.with_suffix('.native.jsonl').exists()
            with self._path.with_suffix('.native.jsonl').open('ab') as stream:
                if stream.write(encoded) != len(encoded):
                    raise OSError('partial native append')
                stream.flush()
                os.fsync(stream.fileno())
            if new_file:
                from Nervous.storage import sync_directory
                sync_directory(self._path.parent)
        except OSError as exc:
            raise TraceError('native_persistence_failed') from exc

    @property
    def events(self) -> tuple[MindEvent, ...]:
        return self._events

    def append(self, event_type: str, payload: Mapping[str, object], *, source_event_seqs: Sequence[int]) -> MindEvent:
        if self._read_only:
            raise TraceError('reopened_trace_is_read_only')
        event = self.preview_append(event_type, payload, source_event_seqs=source_event_seqs)
        candidate = self._events + (event,)
        encoded = (_canonical_json(_event_document(event)) + '\n').encode('utf-8')
        try:
            new_file = not self._path.exists()
            with self._path.open('ab') as handle:
                written = handle.write(encoded)
                if written != len(encoded):
                    raise OSError('partial append')
                handle.flush()
                os.fsync(handle.fileno())
            if new_file:
                from Nervous.storage import sync_directory
                sync_directory(self._path.parent)
        except OSError:
            raise TraceError('trace_append_failed') from None
        self._events = candidate
        return event

    def preview_append(self, event_type: str, payload: Mapping[str, object], *, source_event_seqs: Sequence[int]) -> MindEvent:
        """Validate a result before its owner records receipt; perform no I/O."""
        if type(event_type) is not str or event_type not in _EVENT_TYPES:
            raise TraceError('unknown_event_type')
        if not isinstance(payload, Mapping):
            raise TraceError('invalid_event_payload')
        if not isinstance(source_event_seqs, (list, tuple)):
            raise TraceError('invalid_source_event_seqs')
        snapshotted_payload = _snapshot_payload(payload)
        refs = tuple(source_event_seqs)
        event = MindEvent(trace_format_version=TRACE_FORMAT_VERSION, seq=len(self._events), activation_id=self._activation_id, event_type=event_type, timestamp=self._fixed_timestamp or _utc_timestamp(), payload=snapshotted_payload, source_event_seqs=refs)
        candidate = self._events + (event,)
        _validate_sequence(candidate, require_terminal=False)
        encoded = (_canonical_json(_event_document(event)) + '\n').encode('utf-8')
        if len(encoded) - 1 > _event_byte_limit(candidate[0]):
            raise TraceError('event_too_large')
        return event

    def preview_capability_result(self, observation, error: str | None=None, *, request_seq=None) -> MindEvent:
        """Validate an external result against its actual request and snapshot."""
        request = next((event for event in reversed(self._events) if event.event_type == CAPABILITY_REQUESTED and (request_seq is None or event.seq == request_seq)), None)
        if request is None or (error is not None and observation is not None):
            raise TraceError('invalid_capability_result')
        initial = next((event for event in self._events if event.event_type == INITIAL_EXECUTION_OBSERVED), None)
        prefix = MindTrace(self._path, self._activation_id, fixed_timestamp=None, events=self._events[:request.seq + 1], read_only=True)
        if error is not None:
            return prefix.preview_append(ACTIVATION_FAILED, {'code': error}, source_event_seqs=(request.seq,))
        dependencies = (initial.seq, request.seq) if initial is not None and request.payload['capability'] == 'inspect_execution' else (request.seq,)
        return prefix.preview_append(CAPABILITY_OBSERVED, {'capability': request.payload['capability'], 'observation': observation}, source_event_seqs=dependencies)

def project_model_request(events):
    prefix=tuple(events)
    if _validate_sequence(prefix,require_terminal=False)!='awaiting_model':raise TraceError('event_prefix_has_no_model_request')
    start=prefix[0];context=_thaw(start.payload['cognitive_context']);activation=_thaw(start.payload['activation'])
    observed=[e for e in prefix if e.event_type==CAPABILITY_OBSERVED]
    spent=cognitive_phase(prefix)-1+sum(e.event_type==NATIVE_REPAIR_RESERVED for e in prefix)
    initial=next((e for e in prefix if e.event_type==INITIAL_EXECUTION_OBSERVED),None)
    return project_activity_context(activation, context,
        _thaw(start.payload['available_capabilities']) if consultation_allowed(prefix) else [],
        observations=[{'ref':observation_ref(prefix,e),'observation':_thaw(e.payload['observation'])} for e in observed],
        remaining_calls=ACTIVITY_CALLS-spent,
        initial_observation=_initial_execution_projection(initial.payload) if initial else None)


def project_activity_context(activation, context, capabilities, *, observations=(),
                             remaining_calls=ACTIVITY_CALLS, initial_observation=None):
    """The same pure projection supports pre-admission budgeting and saved traces."""
    context=_thaw(context);activation=_thaw(activation)
    task=context.get('task_view')
    if task:
        if fingerprint(activation['execution_goal_snapshot'])!=task['execution_goal_sha256']:raise TraceError('execution_task_view_conflict')
        activation['execution_goal_snapshot']=task['goal']
    payload={'activation':activation,'cognition':context,'information_acquisition_allowed':True,
        'available_capabilities':list(capabilities),
        'observations':[{'ref':item['ref'],'observation':project_observation(item['observation'],task)} for item in observations],
        'activity_budget':{'mind_calls_total':ACTIVITY_CALLS,'mind_calls_remaining':remaining_calls,'consultations_and_corrections_share_this_budget':True}}
    if initial_observation:payload['initial_execution_observation']=project_observation(initial_observation,task)
    if observations:payload['observation']=payload['observations'][-1]['observation']
    return ModelRequestProjection((),'',_canonical_json(payload))


def replay_activation(events):
    history=tuple(events);_validate_sequence(history,require_terminal=True)
    requests=[];outputs=[];capability=None;observation=None;final=None;failure=None
    for index,event in enumerate(history):
        if event.event_type==MODEL_OUTPUT_RECORDED:
            requests.append(project_model_request(history[:index]));outputs.append(event.payload['text'])
        elif event.event_type==CAPABILITY_REQUESTED:capability=event.payload
        elif event.event_type==CAPABILITY_OBSERVED:observation=event.payload['observation']
        elif event.event_type==ACTIVATION_FINISHED:final=event.payload
        elif event.event_type==MIND_DIRECTIVE_ISSUED:final=_freeze({'type':'directive','text':event.payload['text']})
        elif event.event_type==ACTIVATION_FAILED:failure=event.payload['code']
    return ActivationReplay(history[0].activation_id,tuple(requests),tuple(outputs),capability,observation,final,failure,
                            tuple((e.seq,e.source_event_seqs) for e in history))


def _validate_sequence(events, *, require_terminal):
    if not events or len(events)>trace_event_limit(events):raise TraceError('invalid_event_count')
    state='start';request=None;initial=None;last=None
    for index,event in enumerate(events):
        _validate_common_event(event,expected_seq=index,activation_id=events[0].activation_id)
        if index==0:
            if event.event_type!=ACTIVATION_STARTED:raise TraceError('invalid_event_order')
            _validate_started(event.payload);_require_refs(event,());state='awaiting_model';continue
        if event.event_type==NATIVE_REPAIR_RESERVED:
            if state!='awaiting_model':raise TraceError('invalid_event_order')
            _require_keys(event.payload,{'record_count','prefix_sha256'})
            count=event.payload['record_count'];digest=event.payload['prefix_sha256']
            if type(count) is not int or count<2 or count%2 or count>2*ACTIVITY_CALLS or not isinstance(digest,str) or len(digest)!=64:
                raise TraceError('invalid_native_repair_reservation')
            if any(e.payload.get('record_count')==count for e in events[:index] if e.event_type==NATIVE_REPAIR_RESERVED):raise TraceError('duplicate_native_repair_reservation')
            _require_refs(event,(events[index-1].seq,));continue
        if state=='awaiting_model':
            if event.event_type==INITIAL_EXECUTION_OBSERVED and index==1:
                _validate_initial_execution_observed(event.payload,events[0].payload);_require_refs(event,(0,));initial=event;continue
            if event.event_type==MODEL_OUTPUT_RECORDED:
                _validate_model_output(event.payload,expected_call_index=cognitive_phase(events[:index]),limit=output_limit())
                _require_refs(event,model_dependencies(events[:index]));last=event;state='after_model';continue
            if event.event_type==ACTIVATION_FAILED:
                _validate_failed(event.payload);_require_refs(event,model_dependencies(events[:index]));state='terminal';continue
        elif state=='after_model':
            _validate_output_transition(last, event)
            if event.event_type==CAPABILITY_REQUESTED:
                _validate_capability_request(event.payload)
                if not consultation_allowed(events[:index]) or event.payload['capability'] not in events[0].payload['available_capabilities']:raise TraceError('capability_limit_exceeded')
                _require_refs(event,(last.seq,));request=event;state='waiting_result';continue
            if event.event_type in {ACTIVATION_FINISHED,MIND_DIRECTIVE_ISSUED,ACTIVATION_FAILED}:
                if event.event_type==ACTIVATION_FINISHED:_validate_finished(event.payload)
                elif event.event_type==MIND_DIRECTIVE_ISSUED:_validate_directive_issued(event,events=events[:index])
                else:_validate_failed(event.payload)
                _require_refs(event,(last.seq,));state='terminal';continue
        elif state=='waiting_result':
            if event.event_type==CAPABILITY_OBSERVED:
                _validate_capability_observation(event.payload,requested_capability=request.payload['capability'])
                dependencies=(initial.seq,request.seq) if initial and request.payload['capability']=='inspect_execution' else (request.seq,)
                _require_refs(event,dependencies);state='awaiting_model';continue
            if event.event_type==ACTIVATION_FAILED:
                _validate_failed(event.payload);_require_refs(event,(request.seq,));state='terminal';continue
        raise TraceError('invalid_event_order')
    if require_terminal and state!='terminal':raise TraceError('activation_incomplete')
    return state


def _validate_output_transition(output, event):
    """The host records the submitted decision verbatim; it does not author one."""
    from Mind.contracts import (parse_output, CapabilityRequest, Directive,
                                DecisionIntent, NoChange, request_payload)
    if event.event_type == ACTIVATION_FAILED:
        return
    parsed = parse_output(output.payload['text'])
    if event.event_type == CAPABILITY_REQUESTED:
        valid = isinstance(parsed, CapabilityRequest) and request_payload(parsed) == _thaw(event.payload)
    elif event.event_type == MIND_DIRECTIVE_ISSUED:
        valid = isinstance(parsed, Directive) and parsed.text == event.payload.get('text')
    elif event.event_type == ACTIVATION_FINISHED:
        valid = (isinstance(parsed, NoChange) and event.payload == {'type': 'no_change'} or
                 isinstance(parsed, DecisionIntent) and event.payload == {'type': 'decision_intent', 'intent': parsed.intent})
    else:
        valid = False
    if not valid:
        raise TraceError('model_result_mismatch')


def _validate_started(payload):
    _require_keys(payload,{'activation','cognitive_context','available_capabilities','native_protocol'})
    if payload['native_protocol']!=NATIVE_PROTOCOL_VERSION:raise TraceError('unsupported_native_protocol')
    context=payload['cognitive_context'];activation=payload['activation']
    if not isinstance(context,Mapping) or context.get('contract_version')!=COGNITIVE_CONTRACT_VERSION:raise TraceError('unsupported_cognitive_contract')
    if len(_canonical_json(_thaw(context)))>context_limit():raise TraceError('context_too_large')
    _require_keys(activation,{'trigger','execution_goal_snapshot','execution_status'})
    _validate_required_text(activation['trigger'],MAX_TRIGGER_CHARS)
    _validate_required_text(activation['execution_goal_snapshot'],MAX_GOAL_CHARS)
    if context.get('execution_ref') is None:
        if activation['execution_status'] is not None:raise TraceError('execution_context_mismatch')
    else:_validate_required_text(activation['execution_status'],MAX_STATUS_CHARS)
    allowed=payload['available_capabilities']
    if not isinstance(allowed,(list,tuple)) or len(set(allowed))!=len(allowed) or any(n not in {'read_evidence','analyze_world_model','inspect_execution'} for n in allowed):raise TraceError('invalid_capability_inventory')


def _validate_capability_request(payload, *, contract=None):
    name=payload.get('capability')
    if name=='inspect_execution':_require_keys(payload,{'capability'});return
    if name not in {'read_evidence','analyze_world_model'}:raise TraceError('invalid_capability_request')
    fields={'capability','refs'}
    if name=='analyze_world_model':fields|={'question','model_ref'}
    if name=='analyze_world_model' and 'observation_file' in payload:fields.add('observation_file')
    _require_keys(payload,fields);refs=payload['refs']
    if not isinstance(refs,(list,tuple)) or not 1<=len(refs)<=3:raise TraceError('invalid_source_refs')
    for ref in refs:_validate_required_text(ref,128,normalized=True)
    if name=='analyze_world_model':
        _validate_required_text(payload['question'],analysis_question_limit())
        if not isinstance(payload['model_ref'],str) or len(payload['model_ref'])>128:raise TraceError('invalid_model_reference')
        if 'observation_file' in payload:_validate_required_text(payload['observation_file'],128,normalized=True)


def _validate_capability_observation(payload, *, requested_capability, **_):
    _require_keys(payload,{'capability','observation'})
    if payload['capability']!=requested_capability:raise TraceError('observation_capability_mismatch')
    observation=payload['observation']
    if not isinstance(observation,Mapping) or observation.get('capability')!=requested_capability:raise TraceError('invalid_capability_observation')
    limit=evidence_read_limits()[1] if requested_capability=='read_evidence' else execution_view_limits()[1] if requested_capability=='inspect_execution' else MAX_OBSERVATION_CHARS
    if len(_canonical_json(_thaw(observation)))>limit:raise TraceError('observation_too_large')
    if requested_capability=='inspect_execution':
        _require_keys(observation,{'capability','goal','status','recent_outcome','failure'})
        _validate_required_text(observation['goal'],MAX_GOAL_CHARS);_validate_required_text(observation['status'],MAX_STATUS_CHARS)
        _validate_optional_text(observation['recent_outcome'],MAX_OUTCOME_CHARS);_validate_optional_text(observation['failure'],MAX_FAILURE_CHARS)
    else:
        _require_keys(observation,{'capability','text','origin'})
        if not isinstance(observation['text'],str):raise TraceError('invalid_analysis_observation')
        if observation['origin'] not in {'execution','computation'}:raise TraceError('invalid_analysis_origin')
        if requested_capability=='analyze_world_model' and observation['origin']!='computation':raise TraceError('invalid_analysis_origin')


def _validate_initial_execution_observed(payload, started):
    _require_keys(payload,{'goal','status','recent_outcome','failure'})
    _validate_capability_observation({'capability':'inspect_execution','observation':{'capability':'inspect_execution',**payload}},requested_capability='inspect_execution')
    activation=started['activation']
    if payload['goal']!=activation['execution_goal_snapshot'] or payload['status']!=activation['execution_status']:raise TraceError('execution_observation_mismatch')


def _event_byte_limit(start):
    return MAX_EVENT_BYTES

def _validate_common_event(event: MindEvent, *, expected_seq: int, activation_id: str) -> None:
    if type(event.trace_format_version) is not int or event.trace_format_version != TRACE_FORMAT_VERSION:
        raise TraceError('unsupported_trace_format_version')
    if type(event.seq) is not int or event.seq != expected_seq:
        raise TraceError('non_contiguous_sequence')
    _validate_activation_id(event.activation_id)
    if event.activation_id != activation_id:
        raise TraceError('mixed_activation_ids')
    if type(event.event_type) is not str or event.event_type not in _EVENT_TYPES:
        raise TraceError('unknown_event_type')
    _validate_timestamp(event.timestamp)
    if not isinstance(event.payload, Mapping):
        raise TraceError('invalid_event_payload')
    if type(event.source_event_seqs) is not tuple:
        raise TraceError('invalid_source_event_seqs')
    if any((type(ref) is not int for ref in event.source_event_seqs)):
        raise TraceError('invalid_source_event_seq')
    if len(set(event.source_event_seqs)) != len(event.source_event_seqs):
        raise TraceError('duplicate_source_event_seq')
    if any((ref < 0 or ref >= event.seq for ref in event.source_event_seqs)):
        raise TraceError('invalid_source_event_seq')

def _validate_model_output(payload: Mapping[str, object], *, expected_call_index: int, limit: int=MAX_MODEL_OUTPUT_CHARS) -> None:
    _require_keys(payload, {'call_index', 'text'})
    if type(payload['call_index']) is not int or payload['call_index'] != expected_call_index:
        raise TraceError('invalid_model_call_index')
    text = payload['text']
    if type(text) is not str or len(text) > limit:
        raise TraceError('invalid_model_output_event')

def _initial_execution_projection(payload: Mapping[str, object]) -> dict[str, object]:
    return {'capability': 'inspect_execution', 'failure': _thaw(payload['failure']), 'goal': _thaw(payload['goal']), 'recent_outcome': _thaw(payload['recent_outcome']), 'status': _thaw(payload['status'])}

def _validate_finished(payload: Mapping[str, object]) -> None:
    result_type = payload.get('type')
    if result_type == 'no_change':
        _require_keys(payload, {'type'})
        return
    if result_type == 'decision_intent':
        _require_keys(payload, {'intent', 'type'})
        _validate_required_text(payload['intent'], MAX_DECISION_INTENT_CHARS)
        return
    raise TraceError('invalid_activation_result')

def directive_id_for(activation_id: object, issuing_seq: object, *, events=None) -> str:
    """Derive the inspectable Directive ID from already durable trace facts."""
    _validate_activation_id(activation_id)
    if type(issuing_seq) is not int or issuing_seq < 0 or issuing_seq >= (trace_event_limit(events) if events is not None else 3 * ACTIVITY_CALLS + 3):
        raise TraceError('invalid_directive_sequence')
    return f'{activation_id}:directive:{issuing_seq}'

def _validate_directive_issued(event: MindEvent, *, events=None) -> None:
    _require_keys(event.payload, {'directive_id', 'text'})
    expected_id = directive_id_for(event.activation_id, event.seq, events=events)
    if type(event.payload['directive_id']) is not str or event.payload['directive_id'] != expected_id:
        raise TraceError('invalid_directive_id')
    _validate_required_text(event.payload['text'], directive_limit())

def _validate_decision_id(value: object) -> None:
    if type(value) is not str or not value.strip() or value != value.strip() or (len(value) > MAX_DECISION_ID_CHARS):
        raise TraceError('invalid_decision_id')

def _validate_failed(payload: Mapping[str, object]) -> None:
    _require_keys(payload, {'code'})
    code = payload['code']
    if type(code) is not str or code not in _SAFE_FAILURE_CODES:
        raise TraceError('unsafe_failure_code')

def _require_refs(event: MindEvent, expected: tuple[int, ...]) -> None:
    if event.source_event_seqs != expected:
        raise TraceError('invalid_causal_provenance')

def _require_keys(payload: Mapping[str, object], expected: set[str]) -> None:
    if set(payload) != expected:
        raise TraceError('unexpected_payload_fields')

def _validate_required_text(value: object, limit: int, *, normalized: bool=False) -> None:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise TraceError('invalid_bounded_text')
    if normalized and value != value.strip():
        raise TraceError('non_normalized_text')

def _validate_optional_text(value: object, limit: int) -> None:
    if value is None:
        return
    _validate_required_text(value, limit)

def _validate_activation_id(activation_id: object) -> None:
    if type(activation_id) is not str or not activation_id.strip() or len(activation_id) > MAX_ACTIVATION_ID_CHARS:
        raise TraceError('invalid_activation_id')

def _validate_timestamp(value: object) -> None:
    if type(value) is not str:
        raise TraceError('invalid_timestamp')
    try:
        datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ')
    except ValueError:
        raise TraceError('invalid_timestamp') from None

def _snapshot_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    try:
        encoded = _canonical_json(dict(payload))
        decoded = json.loads(encoded, object_pairs_hook=_strict_json_object, parse_constant=_reject_json_constant)
    except (RecursionError, TypeError, ValueError):
        raise TraceError('invalid_event_payload') from None
    if type(decoded) is not dict:
        raise TraceError('invalid_event_payload')
    return _freeze(decoded)

def _freeze(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple((_freeze(item) for item in value))
    return value

def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value

def _event_document(event: MindEvent) -> dict[str, object]:
    return {'activation_id': event.activation_id, 'event_type': event.event_type, 'payload': _thaw(event.payload), 'seq': event.seq, 'source_event_seqs': list(event.source_event_seqs), 'timestamp': event.timestamp, 'trace_format_version': event.trace_format_version}

def _read_events(path: Path) -> tuple[MindEvent, ...]:
    events: list[MindEvent] = []
    try:
        with path.open('rb') as handle:
            while True:
                raw = handle.readline(MAX_EVENT_BYTES + 2)
                if not raw:
                    break
                if len(raw) > MAX_EVENT_BYTES + 1 or not raw.endswith(b'\n'):
                    raise TraceError('invalid_jsonl_record')
                if raw == b'\n':
                    raise TraceError('empty_jsonl_record')
                try:
                    document = json.loads(raw[:-1].decode('utf-8'), object_pairs_hook=_strict_json_object, parse_constant=_reject_json_constant)
                except (RecursionError, TypeError, UnicodeDecodeError, ValueError):
                    raise TraceError('invalid_jsonl_record') from None
                events.append(_event_from_document(document))
                if len(raw) > _event_byte_limit(events[0]) + 1:
                    raise TraceError('invalid_jsonl_record')
                if len(events) > trace_event_limit(events):
                    raise TraceError('too_many_events')
    except TraceError:
        raise
    except OSError:
        raise TraceError('trace_unavailable') from None
    if not events:
        raise TraceError('empty_trace')
    return tuple(events)

def _event_from_document(document: object) -> MindEvent:
    if type(document) is not dict or set(document) != _ENVELOPE_KEYS:
        raise TraceError('invalid_event_envelope')
    payload = document['payload']
    refs = document['source_event_seqs']
    if type(payload) is not dict or type(refs) is not list:
        raise TraceError('invalid_event_envelope')
    return MindEvent(trace_format_version=document['trace_format_version'], seq=document['seq'], activation_id=document['activation_id'], event_type=document['event_type'], timestamp=document['timestamp'], payload=_freeze(payload), source_event_seqs=tuple(refs))

def _canonical_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(',', ':'), sort_keys=True)

def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError('duplicate JSON key')
    return dict(pairs)

def _reject_json_constant(_: str) -> object:
    raise ValueError('non-standard JSON constant')

def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
