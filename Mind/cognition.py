"""Persistent single-intention cognition; event dispatch and action stay with their organs."""
from __future__ import annotations
import copy
import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from jsonschema import Draft202012Validator
from Nervous.storage import canonical as _canonical_json, fingerprint as _digest
from Mind.contracts import (ActivationInput, ExecutionObservation, ActivationFailure,
    CapabilityRequest, parse_output, request_payload)
from Mind.activity import start_activity, continue_activity, resume_native_activity, record_terminal
from Mind.task_view import (COGNITIVE_CONTRACT_VERSION, context_limit, mind_task_view,
    project_observation, evidence_read_limits, execution_view_limits, directive_limit, analysis_question_limit)
from Mind.trace import (MindTrace, TraceError, NATIVE_PROTOCOL_VERSION, CAPABILITY_OBSERVED,
    CAPABILITY_REQUESTED, _freeze, _thaw, _strict_json_object, _reject_json_constant,
    replay_activation, observation_ref, consultation_allowed, ACTIVITY_CALLS, cognitive_phase)

MAX_ACTIVATIONS=64
MAX_JOURNAL_RECORDS=(ACTIVITY_CALLS+1)*MAX_ACTIVATIONS
MAX_COGNITIVE_STATE_CHARS=16000
MAX_JOURNAL_BYTES=4*1024*1024
MAX_EVIDENCE_CHARS=1000
ACTIVITY_BUDGET_VERSION='mind-activity-v1:6-calls'

def _schema_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _schema_text(limit):
    return {"type": "string", "minLength": 1, "maxLength": limit, "pattern": r"\S"}


_BASIS_SCHEMA = {"type": "array", "items": _schema_object({"ref": _schema_text(128)})}
_UPDATE_SCHEMAS = {
    "belief": _schema_object({"kind": {"enum": ["belief"]}, "id": _schema_text(64),
        "claim": _schema_text(6000), "status": {"enum": ["open", "supported", "contradicted", "archived"]},
        "basis": _BASIS_SCHEMA, "discriminator": _schema_text(6000)}),
    "question": _schema_object({"kind": {"enum": ["question"]}, "id": _schema_text(64),
        "text": _schema_text(6000), "status": {"enum": ["open", "closed", "archived"]}, "basis": _BASIS_SCHEMA}),
    "scenario": _schema_object({"kind": {"enum": ["scenario"]}, "id": _schema_text(64),
        "status": {"enum": ["active", "archived"]},
        "assumptions": {"type": "array", "minItems": 1, "maxItems": 3, "items": _schema_text(64)},
        "steps": {"type": "array", "minItems": 1, "maxItems": 3, "items": _schema_object({
            name: _schema_text(200) for name in ("state", "actors", "action", "external", "outcome")})},
        "unknowns": {"type": "array", "maxItems": 3, "items": _schema_text(200)}}),
}
_UPDATE_SCHEMAS["belief"]["allOf"] = [{
    "if": {"properties": {"status": {"enum": ["supported", "contradicted"]}}},
    "then": {"properties": {"basis": {"minItems": 1}}}}]
_UPDATE_SCHEMAS["belief"]["required"].remove("discriminator")


def update_schemas(contract=None):
    return copy.deepcopy(_UPDATE_SCHEMAS)


def cognitive_step_schema(sources, items=(), capabilities=(), *, contract=None):
    variants=list(update_schemas().values())
    for variant in variants:
        props=variant['properties']
        props['id']['anyOf']=[{'pattern':r'^new:[A-Za-z0-9_-]{1,32}$'}]
        if items:props['id']['anyOf'].append({'enum':[item['id'] for item in items]})
        if 'basis' in props:
            if sources:props['basis']['items']['properties']['ref']['enum']=list(sources)
            else:props['basis']['maxItems']=0
        if 'claim' in props:
            props['claim']['description']='The literal assertion, including relevant scope and time.'
            props['status']['description']='Evaluate this complete assertion: supported means warranted; contradicted means its negation is warranted; open means undecided.'
            props['discriminator']['description']='Optional unobserved test of an unresolved claim. Omission on a revised item retires its prior test.'
    options=[_schema_object({'type':{'enum':['no_change']}}),
        _schema_object({'type':{'enum':['directive']},'text':_schema_text(directive_limit())}),
        _schema_object({'type':{'enum':['decision_intent']},'intent':_schema_text(1000)})]
    if 'inspect_execution' in capabilities:options.append(_schema_object({'type':{'enum':['capability_request']},'capability':{'enum':['inspect_execution']}}))
    for name in ('read_evidence','analyze_world_model'):
        if name not in capabilities:continue
        fields={'type':{'enum':['capability_request']},'capability':{'enum':[name]},
            'refs':{'type':'array','minItems':1,'maxItems':3,'items':_schema_text(128)}}
        if name=='analyze_world_model':fields.update(question=_schema_text(analysis_question_limit()),model_ref={'type':'string','maxLength':128},observation_file=_schema_text(128))
        option=_schema_object(fields)
        if name=='analyze_world_model':option['required'].remove('observation_file')
        options.append(option)
    result=_schema_object({'type':{'enum':['cognitive_step']},'updates':{'type':'array','maxItems':16,'items':{'oneOf':variants}},'next':{'oneOf':options}})
    result['properties']['updates']['description']='Only added or changed knowledge. Unsubmitted records stay unchanged. Reuse an ID to replace the complete record; retire obsolete knowledge explicitly.'
    result['properties']['current']={'type':'array','uniqueItems':True,'items':_schema_text(64),
        'description':'Optional complete selection after updates. Omitted IDs leave current understanding; history remains. Omit this field to retain unchanged records.'}
    return result


def observation_source_text(observation, contract=None):
    if observation['capability']=='analyze_world_model':
        report=_json(observation['text'])
        return '\n'.join(key+': '+(value if isinstance(value,str) else _canonical_json(value)) for key,value in report.items())
    if observation['capability']=='read_evidence':return observation['text']
    return '\n'.join(name+':\n'+('[absent]' if value is None else value) for name,value in observation.items())


def observation_sources(observation, ref, contract=None):
    if observation['capability']=='read_evidence':
        receipt=_json(observation['text'])
        if receipt.get('read_result')=='sources-v1':
            result={}
            for record in receipt['sources']:
                if set(record)!={'ref','text','origin'} or record['ref'] in result or record['origin'] not in {'execution','computation'}:raise ValueError('invalid_read_source')
                _text(record['ref'],128)
                if not isinstance(record['text'],str):raise ValueError('invalid_read_source')
                result[record['ref']]=record
            return result
    text=observation_source_text(observation)
    return {ref:{'ref':ref,'text':text,'origin':observation.get('origin','execution')}} if text else {}

@dataclass(frozen=True)
class Evidence:
    ref: str
    text: str
    origin: str


@dataclass(frozen=True)
class MindInput:
    event_id: str
    trigger: str
    intention_ref: str
    intention_revision: int
    goal: str
    execution_ref: str | None
    execution_status: str | None
    evidence: tuple[Evidence,...]=()
    execution_observation: ExecutionObservation | None=None
    owner_task: dict | None=None


@dataclass(frozen=True)
class MindView:
    revision: int
    intention_ref: str | None
    intention_revision: int | None
    items: tuple[Mapping[str, object], ...]

@dataclass(frozen=True)
class MindResultEvent:
    request_ref: str
    observation: Mapping[str, object] | None = None
    error: str | None = None

@dataclass(frozen=True)
class MindRequest:
    request_ref: str
    event_id: str
    payload: Mapping[str,object]


@dataclass(frozen=True)
class MindReceipt:
    event_id: str
    status: str
    revision: int
    output: Mapping[str, object] | None = None
    error: str | None = None
    request: MindRequest | None = None

def _json(text: str):
    return json.loads(text, object_pairs_hook=_strict_json_object, parse_constant=_reject_json_constant)

def _text(value: object, limit: int) -> None:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError('invalid_bounded_text')

def _input_document(value):
    if type(value) is not MindInput:raise ValueError('invalid_mind_input')
    for text,limit in ((value.event_id,128),(value.trigger,1000),(value.intention_ref,128),(value.goal,4000)):_text(text,limit)
    if value.execution_ref is None:
        if value.execution_status is not None or value.execution_observation is not None:raise ValueError('execution_snapshot_conflict')
    else:_text(value.execution_ref,128);_text(value.execution_status,200)
    if type(value.intention_revision) is not int or value.intention_revision<0:raise ValueError('invalid_intention_revision')
    if type(value.evidence) is not tuple or len(value.evidence)>42:raise ValueError('evidence_budget_exceeded')
    seen=set()
    for item in value.evidence:
        if type(item) is not Evidence or item.origin not in {'execution','computation'}:raise ValueError('invalid_evidence')
        _text(item.ref,128)
        if not isinstance(item.text,str) or len(item.text)>MAX_EVIDENCE_CHARS:raise ValueError('invalid_evidence')
        if item.ref in seen or item.ref.startswith('activation:observation'):raise ValueError('evidence_identity_conflict')
        seen.add(item.ref)
    if value.execution_observation is not None:
        if type(value.execution_observation) is not ExecutionObservation or value.execution_observation.goal!=value.goal or value.execution_observation.status!=value.execution_status:raise ValueError('execution_snapshot_conflict')
    if value.owner_task is not None:mind_task_view(value.owner_task,value.goal)
    return _json(_canonical_json(asdict(value)))


def _basis(basis,sources,*,contract=None):
    if type(basis) is not list:raise ValueError('invalid_basis')
    for item in basis:
        if type(item) is not dict or set(item)!={'ref'}:raise ValueError('invalid_basis')
        _text(item['ref'],128)
        if item['ref'] not in sources:raise ValueError('ungrounded_basis')


def _apply_updates(items,updates,sources,event_id,*,contract=None,current=None):
    if type(updates) is not list or len(updates)>16:raise ValueError('update_budget_exceeded')
    candidate=copy.deepcopy(items);labels={}
    for update in updates:
        if type(update) is not dict:raise ValueError('invalid_update')
        identity=update.get('id');_text(identity,64)
        if identity in labels:raise ValueError('duplicate_item_update')
        if identity.startswith('new:'):
            if re.fullmatch(r'new:[A-Za-z0-9_-]{1,32}',identity) is None:raise ValueError('invalid_local_label')
            labels[identity]='item-'+_digest([event_id,identity])[:20]
        elif identity in items:labels[identity]=identity
        else:raise ValueError('unknown_item')
    schemas=update_schemas()
    for update in updates:
        value=copy.deepcopy(update);identity=labels[value['id']];value['id']=identity;kind=value.get('kind')
        if identity in items and kind!=items[identity]['kind']:raise ValueError('item_kind_conflict')
        if kind not in schemas or not Draft202012Validator(schemas[kind]).is_valid(value):raise ValueError('invalid_cognitive_item')
        if kind in {'belief','question'}:
            _basis(value['basis'],sources)
            if kind=='belief' and value['status'] in {'supported','contradicted'} and not value['basis']:raise ValueError('assessment_needs_evidence')
        elif kind=='scenario':
            value['assumptions']=[labels.get(ref,ref) for ref in value['assumptions']];value['analysis_status']='QUALITATIVE'
        candidate[identity]=value
    active={key:value for key,value in candidate.items() if value['status']!='archived'}
    if current is not None:
        if type(current) is not list or any(type(ref) is not str for ref in current) or len(set(current))!=len(current):raise ValueError('invalid_current_selection')
        selected=[labels.get(ref,ref) for ref in current]
        if any(ref not in active for ref in selected):raise ValueError('invalid_current_selection')
        active={ref:active[ref] for ref in selected}
    if len(_canonical_json(active))>MAX_COGNITIVE_STATE_CHARS:raise ValueError('cognitive_state_budget_exceeded')
    for item in active.values():
        if item['kind']=='scenario' and any(ref not in active or active[ref]['kind']!='belief' for ref in item['assumptions']):raise ValueError('unknown_scenario_assumption')
    return active

class Cognition:

    def __init__(self, *, directory, model, available_capabilities=()):
        if type(available_capabilities) is not tuple or any((n not in {'read_evidence', 'analyze_world_model', 'inspect_execution'} for n in available_capabilities)) or len(set(available_capabilities)) != len(available_capabilities):
            raise ValueError('invalid_capability_inventory')
        self._directory = Path(directory).resolve()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._path = self._directory / 'cognition.json'
        self._model = model
        self._capabilities = available_capabilities
        self._lock = threading.Lock()
        self._writer = (self._directory / 'writer.lock').open('a+b')
        self._writer.seek(0, os.SEEK_END)
        if self._writer.tell() == 0:
            self._writer.write(b'0')
            self._writer.flush()
        self._writer.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._writer.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._load()
        except Exception:
            self._writer.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._writer.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _trace_path(self, start: dict) -> Path:
        return self._directory / (start['activation_id'] + '.jsonl')

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        if self._path.stat().st_size > MAX_JOURNAL_BYTES:
            raise ValueError('cognition_history_too_large')
        document = _json(self._path.read_text(encoding='utf-8'))
        if type(document) is not dict or set(document) != {'version', 'records', 'sha256'} or type(document['version']) is not int or (document['version'] != 1) or (type(document['records']) is not list) or (len(document['records']) > MAX_JOURNAL_RECORDS) or (document['sha256'] != _digest(document['records'])):
            raise ValueError('invalid_cognition_history')
        return document['records']

    def _append(self, record: dict) -> None:
        records = [*self._load(), record]
        if len(records) > MAX_JOURNAL_RECORDS:
            raise ValueError('activation_budget_exceeded')
        encoded = _canonical_json({'version': 1, 'records': records, 'sha256': _digest(records)})
        if len(encoded.encode('utf-8')) > MAX_JOURNAL_BYTES:
            raise ValueError('cognition_history_too_large')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self._directory, prefix='.cognition-', suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def inspect(self) -> MindView:
        with self._lock:
            state, _, _ = self._fold(self._load())
            intention = state['intention']
            return MindView(state['revision'], intention[0] if intention else None, intention[1] if intention else None, tuple((_freeze(item) for item in state['items'].values())))

    def has_replayable_result(self, event_id: str) -> bool:
        """Identify a durable receipt or accepted native answer requiring no call.

        This is a read-only budget hint. Normal event/result identity validation
        and commit still run through activate/accept_result.
        """
        with self._lock:
            _, starts, ends = self._fold(self._load())
            if event_id in ends:
                return True
            if event_id not in starts:
                return False
            path = self._trace_path(starts[event_id])
            try:
                replay_activation(MindTrace.reopen(path).events)
                return True
            except TraceError:
                pass
            try:
                MindTrace.reopen_for_finalization(path)
                return True
            except TraceError:
                pass
            try:
                trace = MindTrace.reopen_for_native(path)
            except TraceError:
                return False
            records = trace.native_records()
            return bool(records and records[-1]['accepted'] and records[-2]['phase'] == cognitive_phase(trace.events))

    def read_source(self, ref: str) -> Mapping[str, object]:
        """Read durable observations and accepted evidence by exact reference.

        Historical process output is not promoted into owner facts. Computation
        retains its explicit origin. No fuzzy lookup or reference repair occurs.
        """
        _text(ref, 128)
        with self._lock:
            _, starts, ends = self._fold(self._load())
            for event_id, start in starts.items():
                active_observation = ref.startswith(start['activation_id'] + ':observation')
                if not active_observation and (event_id not in ends or ends[event_id]['kind'] != 'accepted'):
                    continue
                sources = self._sources(start, MindTrace.reopen(self._trace_path(start)))
                local = ref.replace(start['activation_id'] + ':', 'activation:', 1)
                if ref.startswith(start['activation_id'] + ':observation') and local in sources:
                    return _freeze({**sources[local], 'ref': ref})
                if ref in sources and (not ref.startswith('activation:observation')):
                    return _freeze(sources[ref])
        raise ValueError('unknown_owner_source')

    def _receipt(self, start: dict, end: dict, *, duplicate: bool=False) -> MindReceipt:
        if end['kind'] == 'failed':
            return MindReceipt(start['event_id'], 'failed', start['base_revision'], error=end['error'])
        replay = replay_activation(MindTrace.reopen(self._trace_path(start)).events)
        return MindReceipt(start['event_id'], 'duplicate' if duplicate else 'accepted', end['revision'], replay.final_result)

    def _request(self, start: dict, trace: MindTrace, request_ref=None) -> MindRequest:
        event = next((e for e in reversed(trace.events) if e.event_type == CAPABILITY_REQUESTED and (request_ref is None or request_ref == f"{start['activation_id']}:request:{e.seq}")), None)
        if event is None:
            raise ValueError('unknown_capability_request')
        return MindRequest(f"{start['activation_id']}:request:{event.seq}", start['event_id'], event.payload)

    def _waiting(self, start: dict) -> MindReceipt:
        try:
            trace = MindTrace.reopen_for_result(self._trace_path(start))
        except TraceError:
            trace = MindTrace.reopen_for_finalization(self._trace_path(start))
            last = trace.events[-1]
            parsed = parse_output(last.payload['text'])
            if not consultation_allowed(trace.events) or not isinstance(parsed, CapabilityRequest) or parsed.capability not in trace.events[0].payload['available_capabilities']:
                raise TraceError('trace_not_waiting_for_result')
            trace.append(CAPABILITY_REQUESTED, request_payload(parsed), source_event_seqs=(last.seq,))
        return MindReceipt(start['event_id'], 'waiting', start['base_revision'], request=self._request(start, trace))

    def _sources(self, start: dict, trace: MindTrace | None = None) -> dict:
        sources = {item["ref"]: item for item in start["context"]["evidence"]}
        if trace is not None:
            for event in trace.events:
                if event.event_type != CAPABILITY_OBSERVED:
                    continue
                observation = project_observation(_thaw(event.payload["observation"]),
                                                  start["context"].get("task_view"))
                for ref, value in observation_sources(observation, observation_ref(trace.events, event)).items():
                    if ref in sources and sources[ref] != value:
                        raise ValueError("evidence_identity_conflict")
                    sources[ref] = value
        return sources

    def _fold(self, records: list[dict]):
        state = {"revision": 0, "intention": None, "items": {}, "sources": {}, "execution_ref": None,
                 "results": {}, "owner_task": None}
        starts: dict[str, dict] = {}
        ends: dict[str, dict] = {}
        for record in records:
            kind = record.get("kind")
            event_id = record.get("event_id")
            if kind == "started":
                if set(record) != {"kind", "event_id", "input", "request_digest", "activation_id", "base_revision", "context", "budget_version"}:
                    raise ValueError("invalid_cognition_start")
                value = record["input"]
                context = record["context"]
                if (context.get("execution_ref") != value["execution_ref"]
                        or (value["execution_ref"] is None) != (value["execution_status"] is None)):
                    raise ValueError("execution_context_mismatch")
                if (event_id in starts or event_id != value["event_id"]
                        or record["request_digest"] != _digest(value)
                        or record["activation_id"] != "activation-" + _digest([event_id, value])[:24]
                        or record["base_revision"] != state["revision"]
                        or record["budget_version"] != ACTIVITY_BUDGET_VERSION
                        or context.get("contract_version") != COGNITIVE_CONTRACT_VERSION
                        or any(prior not in ends for prior in starts)):
                    raise ValueError("invalid_cognition_start")
                trace_path = self._trace_path(record)
                if trace_path.exists() and trace_path.stat().st_size:
                    trace = MindTrace.reopen(trace_path)
                    if (_canonical_json(_thaw(trace.events[0].payload["cognitive_context"]))
                            != _canonical_json(context)):
                        raise ValueError("cognition_context_mismatch")
                intention = [value["intention_ref"], value["intention_revision"], value["goal"]]
                if state["intention"] is not None and intention != state["intention"]:
                    raise ValueError("intention_conflict")
                task = value.get("owner_task")
                if state["owner_task"] is not None and task != state["owner_task"]:
                    raise ValueError("owner_task_identity_conflict")
                if task is not None:
                    expected = mind_task_view(task, value["goal"])
                    if record["context"].get("task_view") != expected:
                        raise ValueError("task_view_context_mismatch")
                elif "task_view" in record["context"]:
                    raise ValueError("task_view_without_owner")
                state["owner_task"] = task
                state["intention"] = intention
                starts[event_id] = record
            elif kind == "result_received":
                if (set(record) != {"kind", "event_id", "result", "result_digest"}
                        or event_id not in starts or event_id in ends or record["result"]["request_ref"] in state["results"]
                        or record["result_digest"] != _digest(record["result"])):
                    raise ValueError("invalid_result_receipt")
                trace = MindTrace.reopen(self._trace_path(starts[event_id]))
                request = self._request(starts[event_id], trace, record["result"]["request_ref"])
                result = record["result"]
                if set(result) != {"request_ref", "observation", "error"} or result["request_ref"] != request.request_ref:
                    raise ValueError("result_request_conflict")
                preview = trace.preview_capability_result(result["observation"], result["error"],
                    request_seq=int(request.request_ref.rsplit(':', 1)[1]))
                if len(trace.events) > preview.seq:
                    actual = trace.events[preview.seq]
                    if (actual.event_type != preview.event_type
                            or _canonical_json(_thaw(actual.payload)) != _canonical_json(_thaw(preview.payload))):
                        raise ValueError("result_trace_conflict")
                state["results"][request.request_ref] = record
            elif kind in {"accepted", "failed"}:
                if event_id not in starts or event_id in ends:
                    raise ValueError("invalid_cognition_end")
                if kind == "accepted":
                    if set(record) not in ({"kind", "event_id", "revision", "updates"},
                                           {"kind", "event_id", "revision", "updates", "current"}):
                        raise ValueError("invalid_cognition_commit")
                    start = starts[event_id]
                    if record["revision"] != state["revision"] + 1 or start["base_revision"] != state["revision"]:
                        raise ValueError("stale_cognition_commit")
                    trace = MindTrace.reopen(self._trace_path(start))
                    if (any(f"{start['activation_id']}:request:{event.seq}" not in state["results"]
                                for event in trace.events if event.event_type == CAPABILITY_REQUESTED)):
                        raise ValueError("missing_result_receipt")
                    if (_canonical_json(_thaw(trace.events[0].payload.get("cognitive_context")))
                            != _canonical_json(start["context"])):
                        raise ValueError("cognition_context_mismatch")
                    replay = replay_activation(trace.events)
                    raw = _json(replay.model_outputs[-1])
                    if (replay.failure_code is not None or raw.get("updates") != record["updates"]
                            or ('current' in raw) != ('current' in record)
                            or raw.get('current') != record.get('current')):
                        raise ValueError("cognition_trace_mismatch")
                    sources = self._sources(start, trace)
                    items = _apply_updates(state["items"], record["updates"], sources, event_id,
                                           current=record.get('current'))
                    # Local observation aliases must not collide across activations.
                    aliases = {ref: ref.replace("activation:", start["activation_id"] + ":", 1)
                               for ref in sources if ref.startswith("activation:observation")}
                    for item in items.values():
                        for basis in item.get("basis", []):
                            if basis["ref"] in aliases:
                                basis["ref"] = aliases[basis["ref"]]
                    for local_ref, durable_ref in aliases.items():
                        sources[durable_ref] = {**sources[local_ref], "ref": durable_ref}
                    used = {basis["ref"] for item in items.values() for basis in item.get("basis", [])}
                    state.update(revision=record["revision"], items=items,
                                 sources={ref: sources[ref] for ref in sorted(used)}, execution_ref=start["input"]["execution_ref"])
                elif set(record) != {"kind", "event_id", "error"}:
                    raise ValueError("invalid_cognition_failure")
                ends[event_id] = record
            else:
                raise ValueError("invalid_cognition_record")
        return state, starts, ends


    def activate(self, value: MindInput) -> MindReceipt:
        document = _input_document(value)
        if self._writer.closed:
            raise ValueError("mind_is_closed")
        if not self._lock.acquire(blocking=False):
            return MindReceipt(value.event_id, "busy", 0, error="activation_in_progress")
        try:
            if self._writer.closed:
                raise ValueError("mind_is_closed")
            state, starts, ends = self._fold(self._load())
            if value.event_id in starts:
                start = starts[value.event_id]
                if start["request_digest"] != _digest(document):
                    raise ValueError("event_identity_conflict")
                if value.event_id in ends:
                    return self._receipt(start, ends[value.event_id], duplicate=True)
                try:
                    return self._waiting(start)
                except TraceError:
                    return self._finish(start, state, recovering=True)
            if any(event_id not in ends for event_id in starts):
                return MindReceipt(value.event_id, "busy", state["revision"], error="prior_event_pending")
            intention = [value.intention_ref, value.intention_revision, value.goal]
            if state["intention"] is not None and intention != state["intention"]:
                raise ValueError("intention_conflict")
            if state["owner_task"] is not None and document["owner_task"] != state["owner_task"]:
                raise ValueError("owner_task_identity_conflict")
            if len(starts) >= MAX_ACTIVATIONS:
                return MindReceipt(value.event_id, "failed", state["revision"], error="activation_budget_exceeded")
            sources = dict(state["sources"])
            for evidence in document["evidence"]:
                for previous in starts.values():
                    for prior in previous["input"]["evidence"]:
                        if prior["ref"] == evidence["ref"] and prior != evidence:
                            raise ValueError("evidence_identity_conflict")
                if evidence["ref"] in sources and sources[evidence["ref"]] != evidence:
                    raise ValueError("evidence_identity_conflict")
                sources[evidence["ref"]] = evidence
            context = {"revision": state["revision"], "event_id": value.event_id,
                       "intention_ref": value.intention_ref, "intention_revision": value.intention_revision,
                       "execution_ref": value.execution_ref,
                       "items": list(state["items"].values()), "evidence": list(sources.values()),
                       "contract_version": COGNITIVE_CONTRACT_VERSION}
            if document["owner_task"] is not None:
                context["task_view"] = mind_task_view(document["owner_task"], value.goal)
                task_source = {"ref": "owner-task:" + context["task_view"]["owner_task_sha256"],
                               "text": document["owner_task"]["business_goal"], "origin": "execution"}
                if task_source["ref"] not in sources:
                    context["evidence"].append(task_source)
            if len(_canonical_json(context)) > context_limit():
                return MindReceipt(value.event_id, "failed", state["revision"], error="context_budget_exceeded")
            start = {"kind": "started", "event_id": value.event_id, "input": document,
                     "request_digest": _digest(document), "base_revision": state["revision"],
                     "activation_id": "activation-" + _digest([value.event_id, document])[:24],
                     "context": context, "budget_version": ACTIVITY_BUDGET_VERSION}
            self._append(start)
            trace = MindTrace.create(self._trace_path(start), activation_id=start["activation_id"])
            result = start_activity(ActivationInput(value.trigger, value.goal, value.execution_status),
                model=self._model, trace=trace, cognitive_context=context,
                execution_observation=value.execution_observation,
                capabilities=tuple(name for name in self._capabilities
                                   if value.execution_ref is not None or name != "inspect_execution"))
            return self._handle_activity_result(start, state, result)
        finally:
            self._lock.release()

    def _handle_activity_result(self, start, state, result):
        if isinstance(result, CapabilityRequest):
            return self._waiting(start)
        if isinstance(result, ActivationFailure):
            end = {"kind": "failed", "event_id": start["event_id"], "error": result.code}
            self._append(end)
            return self._receipt(start, end)
        return self._finish(start, state)

    def accept_result(self, value: MindResultEvent) -> MindReceipt:
        """Consume a correlated immutable result; this owner invokes no external organ."""
        if type(value) is not MindResultEvent:
            raise ValueError("invalid_result_event")
        _text(value.request_ref, 128)
        document = _json(_canonical_json({"request_ref": value.request_ref,
                         "observation": _thaw(value.observation), "error": value.error}))
        if len(_canonical_json(document)) > 9500:
            raise ValueError("result_too_large")
        with self._lock:
            if self._writer.closed:
                raise ValueError("mind_is_closed")
            state, starts, ends = self._fold(self._load())
            start = next((start for start in starts.values()
                          if value.request_ref.startswith(start["activation_id"] + ":request:")), None)
            if start is None:
                raise ValueError("unknown_result_request")
            capability = document["observation"].get("capability") if isinstance(document["observation"], dict) else None
            limit = (evidence_read_limits()[1] if capability == "read_evidence" else
                     execution_view_limits()[1] if capability == "inspect_execution" else 3000)
            if len(_canonical_json(document)) > limit + 500:
                raise ValueError("result_too_large")
            event_id = start["event_id"]
            duplicate = value.request_ref in state["results"]
            if duplicate:
                if state["results"][value.request_ref]["result_digest"] != _digest(document):
                    raise ValueError("result_identity_conflict")
                if event_id in ends:
                    return self._receipt(start, ends[event_id], duplicate=True)
                try:
                    waiting = self._waiting(start)
                except TraceError:
                    return self._finish(start, state, recovering=True)
                if waiting.request.request_ref != value.request_ref:
                    return waiting
            if event_id in ends:
                raise ValueError("activation_already_ended")
            if document["observation"] is not None:
                project_observation(document["observation"], start["context"].get("task_view"))
            trace = MindTrace.reopen_for_result(self._trace_path(start))
            request = self._request(start, trace)
            if value.request_ref != request.request_ref:
                raise ValueError("result_request_conflict")
            trace.preview_capability_result(document["observation"], document["error"])
            # Bind the result before inference. An unknown response is never resampled.
            if not duplicate:
                self._append({"kind": "result_received", "event_id": event_id,
                              "result": document, "result_digest": _digest(document)})
            observation = (ActivationFailure(document["error"]) if document["error"] is not None
                           else document["observation"])
            result = continue_activity(trace=trace, model=self._model, observation=observation)
            return self._handle_activity_result(start, state, result)

    def _finish(self, start: dict, state: dict, *, recovering: bool = False) -> MindReceipt:
        if recovering:
            try:
                trace = MindTrace.reopen_for_native(self._trace_path(start))
            except TraceError as error:
                if str(error) not in {"event_prefix_has_no_model_request", "native_result_unknown"}:
                    end = {"kind": "failed", "event_id": start["event_id"], "error": "trace_failed"}
                    self._append(end)
                    return self._receipt(start, end)
            else:
                result = resume_native_activity(trace, self._model)
                if isinstance(result, (CapabilityRequest, ActivationFailure)):
                    return self._handle_activity_result(start, state, result)
        try:
            trace = MindTrace.reopen(self._trace_path(start))
            try:
                replay = replay_activation(trace.events)
            except TraceError:
                if not recovering:
                    raise
                trace = MindTrace.reopen_for_finalization(self._trace_path(start))
                result = parse_output(trace.events[-1].payload["text"])
                if isinstance(result, (CapabilityRequest, ActivationFailure)):
                    raise ValueError("invalid_terminal_result")
                record_terminal(trace, result, (trace.events[-1].seq,))
                replay = replay_activation(trace.events)
            if replay.failure_code:
                raise ValueError("activation_failed")
            submitted = _json(replay.model_outputs[-1])
            updates = submitted["updates"]
            sources = self._sources(start, trace)
            items = _apply_updates(state["items"], updates, sources, start["event_id"],
                                   current=submitted.get("current"))
            used = {basis["ref"] for item in items.values() for basis in item.get("basis", [])}
            next_context = {**start["context"], "items": list(items.values()),
                            "evidence": [sources[ref] for ref in sorted(used)]}
            if len(_canonical_json(next_context)) > context_limit():
                raise ValueError("context_budget_exceeded")
        except (KeyError, TypeError, ValueError) as exc:
            safe = {"ungrounded_basis", "unknown_item", "unknown_scenario_assumption",
                    "cognitive_state_budget_exceeded", "assessment_needs_evidence",
                    "evidence_identity_conflict", "context_budget_exceeded"}
            error = str(exc) if str(exc) in safe else ("interrupted" if recovering else "invalid_cognitive_update")
            end = {"kind": "failed", "event_id": start["event_id"], "error": error}
        else:
            end = {"kind": "accepted", "event_id": start["event_id"],
                   "revision": state["revision"] + 1, "updates": updates}
            if "current" in submitted:
                end["current"] = submitted["current"]
        self._append(end)
        return self._receipt(start, end)
