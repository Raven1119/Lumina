"""Persistent single-intention cognition; event dispatch and action stay with their organs."""
from __future__ import annotations
import copy
import json
import os
import re
import tempfile
import threading
from urllib.parse import parse_qsl
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from jsonschema import Draft202012Validator
from Nervous.storage import canonical as _canonical_json, fingerprint as _digest
from Nervous.provider import BudgetPause
from Mind.contracts import (ActivationInput, ExecutionObservation, ActivationFailure,
    CapabilityRequest, parse_output, request_payload)
from Mind.activity import start_activity, continue_activity, resume_native_activity, record_terminal
from Mind.task_view import (COGNITIVE_CONTRACT_VERSION, context_limit, mind_task_view,
    project_observation, evidence_read_limits, execution_view_limits, directive_limit, analysis_question_limit)
from Mind.trace import (MindTrace, TraceError, NATIVE_PROTOCOL_VERSION, CAPABILITY_OBSERVED,
    CAPABILITY_REQUESTED, _freeze, _thaw, _strict_json_object, _reject_json_constant,
    replay_activation, observation_ref, consultation_allowed, ACTIVITY_CALLS, cognitive_phase,
    ACTIVATION_STARTED, MODEL_OUTPUT_RECORDED)
from Mind.intention import (COGNITIVE_VERSION as PURSUIT_CONTRACT, bind_pursuit,
    effects_schema, apply_effects, source_refs as pursuit_source_refs, validate_task_projection)

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
        if name == 'read_evidence' and contract == PURSUIT_CONTRACT:
            fields['refs']['items'] = {**_schema_text(256), 'anyOf': [
                {'maxLength': 128}, {'pattern': '^view:'}]}
        if name=='analyze_world_model':fields.update(question=_schema_text(analysis_question_limit()),model_ref={'type':'string','maxLength':128},observation_file=_schema_text(128))
        option=_schema_object(fields)
        if name=='analyze_world_model':option['required'].remove('observation_file')
        options.append(option)
    result=_schema_object({'type':{'enum':['cognitive_step']},'updates':{'type':'array','maxItems':16,'items':{'oneOf':variants}},'next':{'oneOf':options}})
    result['properties']['updates']['description']='Only added or changed knowledge. Unsubmitted records stay unchanged. Reuse an ID to replace the complete record; retire obsolete knowledge explicitly.'
    result['properties']['current']={'type':'array','uniqueItems':True,'items':_schema_text(64),
        'description':'Optional complete selection after updates. Omitted IDs leave current understanding; history remains. Omit this field to retain unchanged records.'}
    if contract == PURSUIT_CONTRACT:
        result['properties']['effects'] = effects_schema(sources)
        result['properties']['effects']['description'] = ('Optional versioned pursuit, Task and Watch changes committed with cognition. '
            'Use an empty list or null for unchanged effects. A new Task requires an explicit high-level directive.')
        result['properties']['current']['description'] = ('Optional selection within this activity\'s visible cognition. '
            'Unseen knowledge remains accepted. Leaving attention never retires a pursuit.')
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
    pursuit: dict | None=None
    attention: dict | None=None


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
    effects: Mapping[str, object] | None = None

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
        authority = value.pursuit.get('authorization') if isinstance(value.pursuit, dict) else None
        original_authority = (isinstance(authority, dict) and item.ref == authority.get('ref')
                              and item.text == authority.get('text'))
        limit = 4000 if original_authority else MAX_EVIDENCE_CHARS
        if not isinstance(item.text,str) or len(item.text)>limit:raise ValueError('invalid_evidence')
        if item.ref in seen or item.ref.startswith('activation:observation'):raise ValueError('evidence_identity_conflict')
        seen.add(item.ref)
    if value.execution_observation is not None:
        if type(value.execution_observation) is not ExecutionObservation or value.execution_observation.goal!=value.goal or value.execution_observation.status!=value.execution_status:raise ValueError('execution_snapshot_conflict')
    if value.owner_task is not None:mind_task_view(value.owner_task,value.goal)
    document = asdict(value)
    if value.pursuit is None:
        document.pop('pursuit')  # Preserve old input digests and frozen activity replay.
        if value.attention is not None:
            raise ValueError('attention_requires_pursuit')
    else:
        # Identity relative to accepted state is checked before activity admission.
        if type(value.pursuit) is not dict:
            raise ValueError('invalid_pursuit_input')
        validate_task_projection(value.pursuit, value.owner_task)
    if value.attention is None:
        document.pop('attention')
    elif type(value.attention) is not dict or len(_canonical_json(value.attention)) > 16000:
        raise ValueError('attention_frame_bound')
    return _json(_canonical_json(document))


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


def apply_cognitive_commit(items, submitted, sources, event_id, context):
    """The model adapter and durable owner share exactly the same structural check."""
    current = submitted.get('current')
    pursuit = context.get('pursuit')
    if pursuit is not None:
        visible = {item['id'] for item in context['items']}
        if any(not update['id'].startswith('new:') and update['id'] not in visible
               for update in submitted['updates']):
            raise ValueError('unread_cognitive_item')
        if current is not None:
            current = [*current, *(identity for identity in items if identity not in visible)]
    elif 'effects' in submitted:
        raise ValueError('pursuit_effects_not_enabled')
    candidate = _apply_updates(items, submitted['updates'], sources, event_id, current=current)
    revised, effects = apply_effects(pursuit, submitted, sources, candidate, event_id,
                                   task_status=context.get('task_status'), activation_id=context.get('activation_id'))
    return candidate, revised, effects


def _requested_cognitive_items(payload):
    if payload.get('capability') != 'read_evidence':
        return ()
    result = []
    for ref in payload.get('refs', ()):
        prefix = 'view:mind.cognition?'
        if not ref.startswith(prefix):
            continue
        pairs = parse_qsl(ref[len(prefix):], strict_parsing=True)
        if len(pairs) == 1 and pairs[0][0] == 'item_ref':
            result.append(pairs[0][1])
    return tuple(result)


def context_with_cognitive_reads(context, events):
    """Extend only this activity's visible set from correlated current-owner reads."""
    if context.get('pursuit') is None:
        return copy.deepcopy(context)
    result = copy.deepcopy(context)
    items = {item['id']: item for item in result['items']}
    sources = {source['ref']: source for source in result['evidence']}
    requested = ()
    for event in events:
        if event.event_type == CAPABILITY_REQUESTED:
            requested = _requested_cognitive_items(event.payload)
        elif event.event_type == CAPABILITY_OBSERVED and requested:
            observation = event.payload['observation']
            if observation['capability'] != 'read_evidence':
                continue
            body = _json(observation['text'])
            if body.get('read_result') != 'sources-v1':
                continue
            for source in body['sources']:
                if not source['ref'].startswith('view-result:'):
                    continue
                view = _json(source['text'])
                scope = view.get('scope', {})
                if (view.get('owner') != 'mind' or not isinstance(scope, dict)
                        or scope.get('kind') != 'cognitive_item' or scope.get('item_ref') not in requested):
                    continue
                if (source['ref'] != 'view-result:' + _digest(view)[:24]
                        or view['revision'] != context['revision'] or view['truncated']):
                    raise ValueError('cognitive_view_identity_conflict')
                if view['missing']:
                    continue
                item = view['content']['item']
                if item['id'] != scope['item_ref']:
                    raise ValueError('cognitive_view_identity_conflict')
                for revealed in [item, *view['content'].get('dependencies', [])]:
                    items[revealed['id']] = revealed
                for basis in view['content']['basis_sources']:
                    if basis['ref'] in sources and basis != sources[basis['ref']]:
                        raise ValueError('evidence_identity_conflict')
                    sources[basis['ref']] = basis
    result['items'] = list(items.values())
    result['evidence'] = list(sources.values())
    return result

class Cognition:

    def __init__(self, *, directory, model, available_capabilities=(), prepare_background=None):
        if type(available_capabilities) is not tuple or any((n not in {'read_evidence', 'analyze_world_model', 'inspect_execution'} for n in available_capabilities)) or len(set(available_capabilities)) != len(available_capabilities):
            raise ValueError('invalid_capability_inventory')
        self._directory = Path(directory).resolve()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._path = self._directory / 'cognition.json'
        self._model = model
        self._capabilities = available_capabilities
        self._prepare_background = prepare_background
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
        return self.read_records(self._path)

    @staticmethod
    def read_records(path) -> list[dict]:
        path = Path(path)
        if not path.exists():
            return []
        if path.stat().st_size > MAX_JOURNAL_BYTES:
            raise ValueError('cognition_history_too_large')
        document = _json(path.read_text(encoding='utf-8'))
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
            from Nervous.storage import sync_directory
            sync_directory(self._path.parent)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def inspect(self) -> MindView:
        with self._lock:
            state, _, _ = self._fold(self._load())
            intention = state['intention']
            return MindView(state['revision'], intention[0] if intention else None, intention[1] if intention else None, tuple((_freeze(item) for item in state['items'].values())))

    def pursuit_state(self):
        """Accepted pursuit and proposed immutable Task versions, never execution progress."""
        with self._lock:
            state, _, _ = self._fold(self._load())
            return copy.deepcopy(state.get('pursuit'))

    @staticmethod
    def _item_headers(state, starts, ends):
        metadata = {}
        for event_id, start in starts.items():
            end = ends.get(event_id, {})
            if end.get('kind') != 'accepted':
                continue
            task = start['context'].get('execution_task')
            task_ref = {key: task[key] for key in ('id', 'revision')} if task else None
            for update in end['updates']:
                identity = ('item-' + _digest([event_id, update['id']])[:20]
                            if update['id'].startswith('new:') else update['id'])
                metadata[identity] = {'task_ref': task_ref, 'last_revision': end['revision']}
        return [{'id': item['id'], 'kind': item['kind'], 'status': item['status'],
                 'basis_refs': [basis['ref'] for basis in item.get('basis', [])], **metadata[item['id']],
                 **({'dependencies': list(item['assumptions'])} if item['kind'] == 'scenario' else {})}
                for item in state['items'].values()]

    def attention_catalogue(self):
        """Bounded headers derived from accepted knowledge; no extra authority store."""
        with self._lock:
            state, starts, ends = self._fold(self._load())
            return {'revision': state['revision'], 'items': self._item_headers(state, starts, ends)}

    @classmethod
    def _item_view(cls, state, starts, ends, item_ref):
        item = state['items'].get(item_ref)
        header = next((entry for entry in cls._item_headers(state, starts, ends) if entry['id'] == item_ref), None)
        dependencies = ([state['items'][ref] for ref in item['assumptions']]
                        if item and item['kind'] == 'scenario' else [])
        # A scenario's accepted assumptions are part of its complete read, just
        # as they are part of the initial AttentionFrame's structural closure.
        basis_refs = dict.fromkeys(basis['ref'] for record in ([item, *dependencies] if item else [])
                                   for basis in record.get('basis', []))
        view = {'owner': 'mind', 'revision': state['revision'],
                'scope': {'kind': 'cognitive_item', 'item_ref': item_ref},
                'content': {'item': copy.deepcopy(item), 'last_revision': header['last_revision'],
                            **({'dependencies': copy.deepcopy(dependencies)} if dependencies else {}),
                            'basis_sources': [copy.deepcopy(state['sources'][ref])
                                              for ref in basis_refs]} if item else None,
                'missing': item is None, 'truncated': False}
        view['ref'] = 'mind-cognition:' + _digest(view)[:24]
        return view

    def cognitive_item_view(self, item_ref):
        """Read the current complete record and its original basis, not a new truth assessment."""
        _text(item_ref, 64)
        with self._lock:
            state, starts, ends = self._fold(self._load())
            return self._item_view(state, starts, ends, item_ref)

    @classmethod
    def _validate_cognitive_read(cls, request, observation, state, starts, ends):
        wanted = _requested_cognitive_items(request.payload)
        if not wanted or observation is None or observation.get('capability') != 'read_evidence':
            return
        body = _json(observation['text'])
        if body.get('read_result') != 'sources-v1':
            return  # Capacity or failed reads never reveal another editable item.
        sources = {source['ref']: source['text'] for source in body['sources']}
        expected_records = {}
        for item_ref in wanted:
            expected = cls._item_view(state, starts, ends, item_ref)
            expected_ref, expected_text = 'view-result:' + _digest(expected)[:24], _canonical_json(expected)
            expected_records[item_ref] = (expected_ref, expected_text)
            if sources.get(expected_ref) != expected_text:
                raise ValueError('cognitive_view_identity_conflict')
        for source in body['sources']:
            if not source['ref'].startswith('view-result:'):
                continue
            view = _json(source['text'])
            scope = view.get('scope', {})
            if (view.get('owner') == 'mind' and isinstance(scope, dict)
                    and scope.get('kind') == 'cognitive_item' and scope.get('item_ref') in expected_records
                    and (source['ref'], source['text']) != expected_records[scope['item_ref']]):
                raise ValueError('cognitive_view_identity_conflict')

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
            if ref.startswith('history:'):
                match = re.fullmatch(r'history:(activation-[0-9a-f]{24}):(\d+)(?::(\d+):(\d+))?', ref)
                if match is None:
                    raise ValueError('invalid_history_reference')
                activation_id, seq, offset, limit = match.groups()
                offset, limit = int(offset or 0), int(limit or 6000)
                if not 1 <= limit <= 6000:
                    raise ValueError('invalid_history_range')
                start = next((s for event_id, s in starts.items()
                              if s['activation_id'] == activation_id and event_id in ends), None)
                if start is None:
                    raise ValueError('unknown_owner_source')
                trace = MindTrace.reopen(self._trace_path(start))
                event = next((event for event in trace.events if event.seq == int(seq)), None)
                if event is None:
                    raise ValueError('unknown_owner_source')
                prior_refs = set()
                for prior in starts.values():
                    if prior['event_id'] == start['event_id']:
                        break
                    prior_refs.update(item['ref'] for item in prior['input']['evidence'])
                piece = self._history_piece(start, event, prior_refs)
                text = _canonical_json(piece['content'])
                if offset > len(text):
                    raise ValueError('invalid_history_range')
                return _freeze({'ref': ref, 'text': text[offset:offset + limit], 'origin': 'computation',
                    'source_kind': 'historical_model_judgment' if event.event_type == MODEL_OUTPUT_RECORDED
                                   else 'historical_activity_record',
                    'label': event.event_type, 'offset': offset, 'limit': limit,
                    'total_chars': len(text), 'truncated': offset + limit < len(text)})
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

    @staticmethod
    def _history_piece(start, event, prior_refs=()):
        # Initial context embeds old cognition and background; the input and
        # activation are the original facts for this activity, without recursion.
        inputs = {**start['input'], 'evidence': [item for item in start['input']['evidence']
                                               if item['ref'] not in prior_refs]}
        task = start['context'].get('task_view')
        payload = _thaw(event.payload)
        if event.event_type == ACTIVATION_STARTED:
            activation = _thaw(event.payload['activation'])
            activation['execution_goal_snapshot'] = project_observation(
                {'capability': 'inspect_execution', 'goal': activation['execution_goal_snapshot']}, task)['goal']
            inputs['goal'] = project_observation(
                {'capability': 'inspect_execution', 'goal': inputs['goal']}, task)['goal']
            if task:
                inputs['owner_task'] = {'business_goal': task['goal'],
                                        'owner_task_sha256': task['owner_task_sha256']}
            if inputs['execution_observation'] is not None:
                inputs['execution_observation'] = project_observation(
                    {'capability': 'inspect_execution', **inputs['execution_observation']}, task)
                inputs['execution_observation'].pop('capability')
            payload = {'activation': activation, 'input': inputs}
        elif event.event_type == 'INITIAL_EXECUTION_OBSERVED':
            payload = project_observation({'capability': 'inspect_execution', **payload}, task)
            payload.pop('capability')
        elif event.event_type == CAPABILITY_OBSERVED:
            payload['observation'] = project_observation(payload['observation'], task)
        return {'ref': f"history:{start['activation_id']}:{event.seq}",
                'event_type': event.event_type,
                'content': {'payload': payload, 'timestamp': event.timestamp,
                            'projection': 'Mind role view of the saved trace; execution protocol omitted.',
                            'source_event_seqs': list(event.source_event_seqs)}}

    def _history_segments(self, starts, ends):
        segments, prior_refs = [], set()
        for event_id, start in starts.items():
            if event_id not in ends:
                continue
            segments.append({'ref': 'activity:' + start['activation_id'],
                'content': {'event_id': event_id, 'status': ends[event_id]['kind'],
                    'pieces': [self._history_piece(start, event, prior_refs)
                               for event in MindTrace.reopen(self._trace_path(start)).events]}})
            if start['context'].get('pursuit') is not None:
                task = start['context'].get('execution_task')
                segments[-1]['content']['task_ref'] = {key: task[key] for key in ('id', 'revision')} if task else None
            # Source text remains at its first historical occurrence. Repeated
            # initial evidence is a projection of that same immutable source.
            prior_refs.update(item['ref'] for item in start['input']['evidence'])
        return segments

    def history_segments(self):
        """Complete ended activities, with no copied requests or old context."""
        with self._lock:
            _, starts, ends = self._fold(self._load())
            return self._history_segments(starts, ends)

    def _receipt(self, start: dict, end: dict, *, duplicate: bool=False) -> MindReceipt:
        if end['kind'] == 'failed':
            return MindReceipt(start['event_id'], 'failed', start['base_revision'], error=end['error'])
        replay = replay_activation(MindTrace.reopen(self._trace_path(start)).events)
        return MindReceipt(start['event_id'], 'duplicate' if duplicate else 'accepted', end['revision'],
                           replay.final_result, effects=copy.deepcopy(end.get('effects')))

    @staticmethod
    def _request(start: dict, trace: MindTrace, request_ref=None) -> MindRequest:
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

    @staticmethod
    def _sources(start: dict, trace: MindTrace | None = None, *, retained_sources=None) -> dict:
        context = context_with_cognitive_reads(start['context'], trace.events) if trace else start['context']
        sources = {item["ref"]: item for item in context["evidence"]}
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
        if start['context'].get('pursuit') is not None:
            for ref, source in list(sources.items()):
                if ref.startswith('activation:observation'):
                    durable = ref.replace('activation:', start['activation_id'] + ':', 1)
                    sources[durable] = {**source, 'ref': durable}
        for ref, source in sources.items():
            if ref in (retained_sources or {}) and retained_sources[ref] != source:
                raise ValueError('evidence_identity_conflict')
        return sources

    def _fold(self, records: list[dict]):
        return self.fold_records(records, self._directory)

    @classmethod
    def inspect_directory(cls, directory):
        directory = Path(directory)
        records = cls.read_records(directory / 'cognition.json')
        diagnostic = {'valid_records': len(records), 'issue': None}
        try:
            state, starts, ends = cls.fold_records(records, directory)
        except (ValueError, TypeError, KeyError, IndexError, OSError):
            # ponytail: bounded journal; only damaged diagnostics refold prefixes.
            state, starts, ends = cls.fold_records([], directory)
            for index in range(len(records)):
                try:
                    state, starts, ends = cls.fold_records(records[:index + 1], directory)
                except (ValueError, TypeError, KeyError, IndexError, OSError) as error:
                    diagnostic = {'valid_records': index, 'issue': {'record': index + 1, 'kind': type(error).__name__}}
                    break
        traces = {record['activation_id']: MindTrace.inspect_path(directory / (record['activation_id'] + '.jsonl'))
                  for record in records if isinstance(record, dict) and record.get('kind') == 'started'
                  and isinstance(record.get('activation_id'), str)
                  and record['activation_id'].startswith('activation-')
                  and record['activation_id'][11:].isalnum()}
        result = {'revision': state['revision'], 'items': list(state['items'].values()),
                  'diagnostic': diagnostic, 'traces': traces}
        if state.get('pursuit') is not None:
            result['pursuit'] = copy.deepcopy(state['pursuit'])
        return result

    @classmethod
    def fold_records(cls, records: list[dict], directory):
        state = {"revision": 0, "intention": None, "items": {}, "sources": {}, "execution_ref": None,
                 "results": {}, "owner_task": None, "pursuit": None}
        starts: dict[str, dict] = {}
        ends: dict[str, dict] = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError('invalid_cognition_record')
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
                        or context.get("contract_version") != (PURSUIT_CONTRACT if value.get('pursuit') is not None else COGNITIVE_CONTRACT_VERSION)
                        or any(prior not in ends for prior in starts)):
                    raise ValueError("invalid_cognition_start")
                trace_path = Path(directory) / (record['activation_id'] + '.jsonl')
                if trace_path.exists() and trace_path.stat().st_size:
                    trace = MindTrace.reopen(trace_path)
                    if (_canonical_json(_thaw(trace.events[0].payload["cognitive_context"]))
                            != _canonical_json(context)):
                        raise ValueError("cognition_context_mismatch")
                intention = [value["intention_ref"], value["intention_revision"], value["goal"]]
                if value.get('pursuit') is not None:
                    if state['intention'] is not None and state['pursuit'] is None:
                        raise ValueError('pursuit_mode_conflict')
                    state['pursuit'] = bind_pursuit(value['pursuit'], state['pursuit'])
                    validate_task_projection(value['pursuit'], value.get('owner_task'))
                    if context.get('pursuit') != state['pursuit']:
                        raise ValueError('pursuit_context_mismatch')
                    if context.get('attention') != value.get('attention'):
                        raise ValueError('attention_context_mismatch')
                    if (context.get('task_status') != value['pursuit']['task_status']
                            or context.get('execution_task') != value['pursuit'].get('execution_task', value['pursuit']['task'])
                            or context.get('activation_id') != record['activation_id']):
                        raise ValueError('pursuit_context_mismatch')
                elif state['pursuit'] is not None:
                    raise ValueError('pursuit_mode_conflict')
                elif state["intention"] is not None and intention != state["intention"]:
                    raise ValueError("intention_conflict")
                task = value.get("owner_task")
                if state['pursuit'] is None and state["owner_task"] is not None and task != state["owner_task"]:
                    raise ValueError("owner_task_identity_conflict")
                if task is not None:
                    expected = mind_task_view(task, value["goal"])
                    if record["context"].get("task_view") != expected:
                        raise ValueError("task_view_context_mismatch")
                elif "task_view" in record["context"]:
                    raise ValueError("task_view_without_owner")
                state["owner_task"] = task
                state["intention"] = intention if state['pursuit'] is None else None
                starts[event_id] = record
            elif kind == "result_received":
                if (set(record) != {"kind", "event_id", "result", "result_digest"}
                        or event_id not in starts or event_id in ends or record["result"]["request_ref"] in state["results"]
                        or record["result_digest"] != _digest(record["result"])):
                    raise ValueError("invalid_result_receipt")
                trace = MindTrace.reopen(Path(directory) / (starts[event_id]['activation_id'] + '.jsonl'))
                request = cls._request(starts[event_id], trace, record["result"]["request_ref"])
                result = record["result"]
                if set(result) != {"request_ref", "observation", "error"} or result["request_ref"] != request.request_ref:
                    raise ValueError("result_request_conflict")
                cls._validate_cognitive_read(request, result['observation'], state, starts, ends)
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
                    if (set(record) - {'current', 'effects'} != {"kind", "event_id", "revision", "updates"}
                            or ('effects' in record and state['pursuit'] is None)):
                        raise ValueError("invalid_cognition_commit")
                    start = starts[event_id]
                    if record["revision"] != state["revision"] + 1 or start["base_revision"] != state["revision"]:
                        raise ValueError("stale_cognition_commit")
                    trace = MindTrace.reopen(Path(directory) / (start['activation_id'] + '.jsonl'))
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
                    sources = cls._sources(start, trace, retained_sources=state['sources'])
                    visible_context = context_with_cognitive_reads(start['context'], trace.events)
                    items, pursuit, effects = apply_cognitive_commit(state['items'], raw, sources,
                                                                     event_id, visible_context)
                    if effects != record.get('effects'):
                        raise ValueError('pursuit_effect_trace_mismatch')
                    # Local observation aliases must not collide across activations.
                    aliases = {ref: ref.replace("activation:", start["activation_id"] + ":", 1)
                               for ref in sources if ref.startswith("activation:observation")}
                    for item in items.values():
                        for basis in item.get("basis", []):
                            if basis["ref"] in aliases:
                                basis["ref"] = aliases[basis["ref"]]
                    for local_ref, durable_ref in aliases.items():
                        sources[durable_ref] = {**sources[local_ref], "ref": durable_ref}
                    used = {basis["ref"] for item in items.values() for basis in item.get("basis", [])} | pursuit_source_refs(pursuit)
                    retained_sources = {**state['sources'], **sources}
                    state.update(revision=record["revision"], items=items,
                                 sources={ref: retained_sources[ref] for ref in sorted(used)}, execution_ref=start["input"]["execution_ref"],
                                 pursuit=pursuit)
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
            if value.pursuit is not None:
                if state['intention'] is not None and state['pursuit'] is None:
                    raise ValueError('pursuit_mode_conflict')
                pursuit = bind_pursuit(value.pursuit, state['pursuit'])
            elif state['pursuit'] is not None:
                raise ValueError('pursuit_mode_conflict')
            else:
                pursuit = None
            if pursuit is None and state["intention"] is not None and intention != state["intention"]:
                raise ValueError("intention_conflict")
            if pursuit is None and state["owner_task"] is not None and document["owner_task"] != state["owner_task"]:
                raise ValueError("owner_task_identity_conflict")
            if len(starts) >= MAX_ACTIVATIONS:
                return MindReceipt(value.event_id, "failed", state["revision"], error="activation_budget_exceeded")
            sources = dict(state["sources"])
            if pursuit is not None and value.pursuit.get('visible_item_ids') is not None:
                selected = value.pursuit['visible_item_ids']
                if any(ref not in state['items'] for ref in selected):
                    raise ValueError('unknown_visible_cognition')
                retained = {basis['ref'] for identity in selected for basis in state['items'][identity].get('basis', [])}
                retained.add(pursuit['authorization']['ref'])
                for item in pursuit['intentions'].values():
                    if item['commitment'] == 'committed':
                        retained.update(item['origin_refs'])
                        retained.update(item['influence_refs'])
                sources = {ref: source for ref, source in sources.items() if ref in retained}
            for evidence in document["evidence"]:
                if (evidence['ref'] in state['sources']
                        and state['sources'][evidence['ref']] != evidence):
                    raise ValueError('evidence_identity_conflict')
                for previous in starts.values():
                    for prior in previous["input"]["evidence"]:
                        if prior["ref"] == evidence["ref"] and prior != evidence:
                            raise ValueError("evidence_identity_conflict")
                if evidence["ref"] in sources and sources[evidence["ref"]] != evidence:
                    raise ValueError("evidence_identity_conflict")
                sources[evidence["ref"]] = evidence
            used_chars = len(_canonical_json(state["items"]))
            context = {"revision": state["revision"], "event_id": value.event_id,
                       "intention_ref": value.intention_ref, "intention_revision": value.intention_revision,
                       "execution_ref": value.execution_ref,
                       "items": list(state["items"].values()), "evidence": list(sources.values()),
                       "capacity": {"used_chars": used_chars, "max_chars": MAX_COGNITIVE_STATE_CHARS,
                                    "remaining_chars": MAX_COGNITIVE_STATE_CHARS - used_chars},
                       "contract_version": COGNITIVE_CONTRACT_VERSION}
            if pursuit is not None:
                context.update(pursuit=pursuit, task_status=value.pursuit['task_status'],
                               contract_version=PURSUIT_CONTRACT)
                context['activation_id'] = 'activation-' + _digest([value.event_id, document])[:24]
                context['execution_task'] = value.pursuit.get('execution_task', value.pursuit['task'])
                if value.attention is not None:
                    context['attention'] = document['attention']
                selected = value.pursuit.get('visible_item_ids')
                if selected is not None:
                    if any(ref not in state['items'] for ref in selected):
                        raise ValueError('unknown_visible_cognition')
                    context['items'] = [state['items'][ref] for ref in selected]
                authority = pursuit['authorization']
                if authority['ref'] not in sources:
                    raise ValueError('unread_pursuit_authority')
            if document["owner_task"] is not None:
                context["task_view"] = mind_task_view(document["owner_task"], value.goal)
                task_source = {"ref": "owner-task:" + context["task_view"]["owner_task_sha256"],
                               "text": document["owner_task"]["business_goal"], "origin": "execution"}
                if task_source["ref"] not in sources:
                    context["evidence"].append(task_source)
            activation = ActivationInput(value.trigger, value.goal, value.execution_status)
            capabilities = tuple(name for name in self._capabilities
                                 if value.execution_ref is not None or name != 'inspect_execution')
            if self._prepare_background is not None:
                segments = self._history_segments(starts, ends)
                for force in (False, True):
                    background = self._prepare_background(segments, force=force)
                    context['derived_history_background'] = background
                    try:
                        if len(_canonical_json(context)) > context_limit():
                            raise BudgetPause('mind_context_capacity_exhausted')
                        preflight = getattr(self._model, 'preflight_context', None)
                        if preflight is not None:
                            preflight(asdict(activation), context, capabilities,
                                      asdict(value.execution_observation) if value.execution_observation else None)
                        break
                    except BudgetPause as error:
                        if (str(error) != 'mind_context_capacity_exhausted'
                                or force or background['mode'] != 'summary'):
                            raise
            elif len(_canonical_json(context)) > context_limit():
                return MindReceipt(value.event_id, "failed", state["revision"], error="context_budget_exceeded")
            start = {"kind": "started", "event_id": value.event_id, "input": document,
                     "request_digest": _digest(document), "base_revision": state["revision"],
                     "activation_id": "activation-" + _digest([value.event_id, document])[:24],
                     "context": context, "budget_version": ACTIVITY_BUDGET_VERSION}
            self._append(start)
            trace = MindTrace.create(self._trace_path(start), activation_id=start["activation_id"])
            result = start_activity(activation,
                model=self._model, trace=trace, cognitive_context=context,
                execution_observation=value.execution_observation,
                capabilities=capabilities)
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
            self._validate_cognitive_read(request, document['observation'], state, starts, ends)
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
                trace = MindTrace.reopen_for_native(self._trace_path(start), allow_pending=True)
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
            sources = self._sources(start, trace, retained_sources=state['sources'])
            visible_context = context_with_cognitive_reads(start['context'], trace.events)
            items, pursuit, effects = apply_cognitive_commit(state['items'], submitted, sources,
                                                             start['event_id'], visible_context)
            visible_ids = {item['id'] for item in visible_context['items']} | {
                'item-' + _digest([start['event_id'], update['id']])[:20]
                for update in submitted['updates'] if update['id'].startswith('new:')}
            visible_items = {ref: item for ref, item in items.items()
                             if pursuit is None or ref in visible_ids}
            used = {basis["ref"] for item in visible_items.values() for basis in item.get("basis", [])}
            next_context = {**visible_context, "items": list(visible_items.values()),
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
            if effects is not None:
                end['effects'] = effects
        self._append(end)
        return self._receipt(start, end)
