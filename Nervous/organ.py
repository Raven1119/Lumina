"""Durable organ mailboxes and bounded foreground mechanical continuation."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

MAX_EVENTS = 256
MAX_EVENT_BYTES = 32 * 1024
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_PENDING_READ = 32


def _text(value: object) -> None:
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        raise ValueError("invalid_event_text")


def _freeze(value: object, depth: int = 0):
    if depth > 8:
        raise ValueError("event_data_too_deep")
    if type(value) is dict or type(value) is MappingProxyType:
        if len(value) > 64 or any(type(key) is not str for key in value):
            raise ValueError("invalid_event_data")
        return MappingProxyType({key: _freeze(item, depth + 1) for key, item in value.items()})
    if type(value) in (list, tuple):
        if len(value) > 64:
            raise ValueError("invalid_event_data")
        return tuple(_freeze(item, depth + 1) for item in value)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("invalid_event_data")


def _plain(value):
    if type(value) is MappingProxyType:
        return {key: _plain(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_plain(item) for item in value]
    return value


def _encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_encode(value)).hexdigest()


def _object(pairs):
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate_json_key")
    return dict(pairs)


@dataclass(frozen=True)
class Event:
    event_id: str
    source: str
    target: str
    kind: str
    data: Mapping[str, object]
    causation_id: str | None = None

    def __post_init__(self):
        for value in (self.event_id, self.source, self.target, self.kind):
            _text(value)
        if self.causation_id is not None:
            _text(self.causation_id)
            if self.causation_id == self.event_id:
                raise ValueError("self_caused_event")
        if type(self.data) not in (dict, MappingProxyType):
            raise ValueError("invalid_event_data")
        object.__setattr__(self, "data", _freeze(self.data))
        if len(_encode(self.document())) > MAX_EVENT_BYTES:
            raise ValueError("event_too_large")

    def document(self) -> dict:
        return {"event_id": self.event_id, "source": self.source, "target": self.target,
                "kind": self.kind, "data": _plain(self.data), "causation_id": self.causation_id}


class NervousOrgan:
    """One bounded mailbox owner. Consumers acknowledge only after durable work."""

    def __init__(self, directory: str | Path, *, limits=None, transport=None, registry=None, clock=None):
        from datetime import datetime, timezone
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        from Nervous.triggers import Registry, DEFAULT_TRIGGERS
        self.registry = registry or Registry(DEFAULT_TRIGGERS)
        self._directory = Path(directory).resolve()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._path = self._directory / "events.json"
        self._lock = threading.Lock()
        # Same native single-writer ownership as Mind, released by process exit.
        self._writer = (self._directory / "writer.lock").open("a+b")
        self._writer.seek(0, os.SEEK_END)
        if self._writer.tell() == 0:
            self._writer.write(b"0")
            self._writer.flush()
        self._writer.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._writer.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._load()
            from Nervous.provider import ProviderCalls
            from Nervous.storage import read_json, write_json
            self._settings_path = self._directory / "settings.json"
            if self._settings_path.exists():
                self.settings = self.read_settings(self._settings_path)
            else:
                self.settings = {"format": "nervous-runtime-1", "limits": limits or {
                    "calls": 40, "output_tokens": 200000, "request_bytes": 2800000}}
                write_json(self._settings_path, self.settings)
            self.calls = ProviderCalls(self._directory / "calls", self.settings["limits"], transport)
            self.stop_reason = None
        except Exception:
            self._writer.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._lock:
            self._writer.close()

    def _load(self) -> dict:
        if self._writer.closed:
            raise ValueError("nervous_is_closed")
        return self.read_state(self._path)

    @staticmethod
    def read_settings(path):
        from Nervous.storage import read_json
        settings = read_json(path)
        if not isinstance(settings, dict) or settings.get('format') != 'nervous-runtime-1':
            raise ValueError('unsupported_nervous_format')
        return settings

    @staticmethod
    def read_state(path) -> dict:
        path = Path(path)
        if not path.exists():
            return {"events": [], "completed": {}}
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("event_history_too_large")
        document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object)
        if (type(document) is not dict or set(document) != {"version", "state", "sha256"}
                or type(document["version"]) is not int or document["version"] not in (1, 2)
                or document["sha256"] != _digest(document["state"])):
            raise ValueError("invalid_event_history")
        state = document["state"]
        if (type(state) is not dict or set(state) - {'attention'} != {"events", "completed"}
                or type(state["events"]) is not list or len(state["events"]) > MAX_EVENTS
                or type(state["completed"]) is not dict):
            raise ValueError("invalid_event_history")
        events = {}
        for item in state["events"]:
            if type(item) is not dict or set(item) != {"event_id", "source", "target", "kind", "data", "causation_id"}:
                raise ValueError("invalid_event_history")
            event = Event(**item)
            if event.event_id in events or (event.causation_id is not None and event.causation_id not in events):
                raise ValueError("invalid_event_history")
            events[event.event_id] = event
        for event_id, emitted_ids in state["completed"].items():
            if (event_id not in events or type(emitted_ids) is not list
                    or len(emitted_ids) > MAX_PENDING_READ
                    or any(type(identity) is not str for identity in emitted_ids)
                    or len(emitted_ids) != len(set(emitted_ids))):
                raise ValueError("invalid_event_history")
            for identity in emitted_ids:
                emitted = events.get(identity)
                if (emitted is None or emitted.causation_id != event_id
                        or emitted.source != events[event_id].target):
                    raise ValueError("invalid_event_history")
        return state

    @classmethod
    def inspect_directory(cls, directory):
        state = cls.read_state(Path(directory) / 'events.json')
        return {t: [e['event_id'] for e in state['events']
                    if e['target'] == t and e['event_id'] not in state['completed']]
                for t in ('mind', 'mind.results', 'mind.analysis', 'execution', 'nervous')}

    def _save(self, state: dict):
        encoded = _encode({"version": 2 if 'attention' in state else 1, "state": state, "sha256": _digest(state)})
        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError("event_history_too_large")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self._directory, prefix=".events-", suffix=".tmp", delete=False) as stream:
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

    @staticmethod
    def _add(state: dict, event: Event) -> bool:
        if type(event) is not Event:
            raise ValueError("invalid_event")
        document = event.document()
        existing = {item["event_id"]: item for item in state["events"]}
        if event.event_id in existing:
            if _encode(existing[event.event_id]) != _encode(document):
                raise ValueError("event_identity_conflict")
            return False
        if event.causation_id is not None and event.causation_id not in existing:
            raise ValueError("unknown_event_cause")
        if len(existing) >= MAX_EVENTS:
            raise ValueError("event_history_full")
        state["events"].append(document)
        return True

    def publish(self, event: Event) -> bool:
        with self._lock:
            state = self._load()
            added = self._add(state, event)
            if added:
                self._save(state)
            return added

    def pending(self, target: str, limit: int = MAX_PENDING_READ) -> tuple[Event, ...]:
        _text(target)
        if type(limit) is not int or not 1 <= limit <= MAX_PENDING_READ:
            raise ValueError("invalid_pending_limit")
        with self._lock:
            state = self._load()
            # ponytail: bounded retained history; archive/compaction needs a
            # separate identity-retention design before this capacity is raised.
            return tuple(Event(**item) for item in state["events"]
                         if item["target"] == target and item["event_id"] not in state["completed"])[:limit]

    def complete(self, event_id: str, target: str, *, emitted: tuple[Event, ...] = ()) -> bool:
        _text(event_id)
        _text(target)
        if (type(emitted) is not tuple or len(emitted) > MAX_PENDING_READ
                or any(type(event) is not Event for event in emitted)):
            raise ValueError("invalid_emitted_events")
        emitted_ids = [event.event_id for event in emitted]
        if len(set(emitted_ids)) != len(emitted_ids):
            raise ValueError("duplicate_emitted_event")
        with self._lock:
            state = self._load()
            original = next((item for item in state["events"] if item["event_id"] == event_id), None)
            if original is None or original["target"] != target:
                raise ValueError("wrong_event_recipient")
            for event in emitted:
                if event.source != target or event.causation_id != event_id:
                    raise ValueError("invalid_emitted_cause")
                self._add(state, event)
            if event_id in state["completed"]:
                if state["completed"][event_id] != emitted_ids:
                    raise ValueError("completion_identity_conflict")
                return False
            state["completed"][event_id] = emitted_ids
            attention = state.get('attention', {})
            related = attention.get('frames', {}).get(event_id, {}).get('views', {}).get('related_signals', [])
            for signal in related:
                child = signal['event_id']
                if attention.get('coalesced', {}).get(child) == event_id:
                    # The receiving Mind has durably accepted this exact frozen
                    # bundle. Original signal history stays intact.
                    state['completed'].setdefault(child, [])
            self._save(state)
            return True

    def submit(self, text: str, event_type: str = "USER_MESSAGE", *,
               submission_id: str | None = None) -> Event:
        """New identity by default; callers reuse submission_id for transport retries."""
        if type(text) is not str or not text.strip() or len(text) > 4000:
            raise ValueError("invalid_user_input")
        _text(event_type)
        identity = uuid.uuid4().hex if submission_id is None else submission_id
        _text(identity)
        control = ('pursuit' in self.settings.get('initial_input', {})
                   and event_type in {'STOP', 'RESUME', 'REVOKE'})
        event = Event('user-submission-' + _digest(identity), 'user',
                      'nervous' if control else 'mind', 'user.control' if control else 'user.input',
                      {'event_type': event_type, 'text': text})
        self.publish(event)
        return event

    def schedule_review(self, due_at, reason, *, submission_id=None):
        from datetime import datetime
        if 'pursuit' not in self.settings.get('initial_input', {}):
            raise ValueError('pursuit_mode_required')
        due = datetime.fromisoformat(due_at.replace('Z', '+00:00'))
        if due.tzinfo is None or type(reason) is not str or not reason.strip() or len(reason) > 1000:
            raise ValueError('invalid_review_schedule')
        identity = uuid.uuid4().hex if submission_id is None else submission_id
        _text(identity)
        event = Event('review-schedule-' + _digest(identity)[:24], 'user', 'nervous',
                      'review.schedule', {'due_at': due_at, 'reason': reason})
        self.publish(event)
        return event

    def ready(self):
        """Continuation first, FIFO within a priority; unknown signals stay pending."""
        with self._lock:
            state = self._load()
            result = []
            for position, document in enumerate(state['events']):
                if document['event_id'] in state['completed']:
                    continue
                if document['event_id'] in state.get('attention', {}).get('coalesced', {}):
                    continue  # Responsibility is carried by its pending parent.
                event = Event(**document)
                specs = self.registry.match(event)
                if specs:
                    result.append((min(s.priority for s in specs), position, event))
            return tuple(row[2] for row in sorted(result, key=lambda row: row[:2]))

    def freeze_attention(self, event, views):
        from Nervous.attention import frame
        with self._lock:
            state = self._load()
            frames = state.setdefault('attention', {}).setdefault('frames', {})
            if event.event_id not in frames:
                if not any(e['event_id'] == event.event_id for e in state['events']):
                    raise ValueError('unknown_attention_event')
                from Nervous.watch import valid_matches
                related = []
                coalesced = state['attention'].get('coalesced', {})
                for child, parent in list(coalesced.items()):
                    if parent != event.event_id or child in state['completed']:
                        continue
                    original = next(item for item in state['events'] if item['event_id'] == child)
                    matches = valid_matches(state['attention'], original['data'])
                    if matches:
                        related.append({**original, 'valid_matches': matches})
                    else:
                        # Paused or obsolete Watch versions follow their own
                        # normal disposition, never the parent's acknowledgement.
                        del coalesced[child]
                if related:
                    views = {**views, 'related_signals': related}
                frames[event.event_id] = frame(event, views, self.registry.match(event))
                self._save(state)
            import copy
            return copy.deepcopy(frames[event.event_id])

    def attention_view(self, *, offset=0, limit=8, event_ref=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError('invalid_attention_view_range')
        state = self._load()
        if event_ref is not None:
            event = next((e for e in state['events'] if e['event_id'] == event_ref), None)
            value = {'owner': 'nervous', 'revision': event_ref, 'scope': 'original attributed event',
                     'content': event, 'missing': event is None, 'truncated': False,
                     'disposition': 'completed' if event_ref in state['completed'] else 'pending'}
            value['ref'] = 'nervous-event-view:' + _digest(value)[:24]
            return value
        pending = [e for e in state['events'] if e['event_id'] not in state['completed']]
        content = {'focus': state.get('attention', {}).get('focus'),
                   'pending': [{'event_id': e['event_id'], 'kind': e['kind'], 'source': e['source'],
                                'view_ref': 'view:nervous.attention?event_ref=' + e['event_id']}
                               for i, e in enumerate(pending) if offset <= i < offset + limit],
                   'resources': self.resources()}
        view = {'owner': 'nervous', 'revision': len(state['completed']), 'scope': 'saved attention and resources',
                'content': content, 'missing': False, 'truncated': offset + limit < len(pending),
                'next_offset': offset + limit if offset + limit < len(pending) else None}
        view['ref'] = 'nervous-view:' + _digest(view)[:24]
        return view

    def resources(self):
        used = self.calls.summary()
        return {'used': used, 'limits': dict(self.calls.limits),
                'remaining': {'calls': self.calls.limits['calls'] - used['calls'],
                    'output_tokens': self.calls.limits['output_tokens'] - used['allocated_output_tokens'],
                    'request_bytes': self.calls.limits['request_bytes'] - used['request_bytes']}}

    def handle(self, event, mind, execution):
        """Apply accepted effects before action dispatch; route bounded owner queries."""
        if event.source == 'user' and event.target == 'nervous' and event.kind in {'review.schedule', 'user.control'}:
            if 'pursuit' not in self.settings.get('initial_input', {}):
                raise ValueError('pursuit_mode_required')
            data = event.document()['data']
            if event.kind == 'user.control':
                action = {'STOP': 'stop', 'RESUME': 'resume', 'REVOKE': 'revoke'}[data['event_type']]
                return (Event('control-' + _digest(event.event_id)[:24], 'nervous', 'execution',
                              'execution.control', {'action': action, 'control_ref': event.event_id}, event.event_id),
                        Event('control-notice-' + _digest(event.event_id)[:24], 'nervous', 'mind',
                              'control.notice', {'owner_event': event.document()}, event.event_id))
            from Nervous.watch import apply_watches
            with self._lock:
                state = self._load()
                attention = state.setdefault('attention', {})
                watch = {'id': 'owner-watch-' + _digest(event.event_id)[:24], 'revision': 1,
                    'spec_id': 'review.due', 'spec_version': 1, 'params': {'due_at': data['due_at']},
                    'intention_id': None, 'status': 'active', 'reason': data['reason'], 'origin_refs': [event.event_id]}
                apply_watches(attention, [watch], attention.get('intentions', {}),
                              lambda name: execution.source_observation(name), self.clock())
                self._save(state)
            return ()
        if event.source not in {'mind', 'mind.results'} or event.target != 'nervous':
            raise ValueError('nervous_effect_authority_conflict')
        data = event.document()['data']
        if event.kind == 'view.read':
            from Nervous.views import read_views
            try:
                result = read_views(data['refs'], mind=mind, execution=execution,
                    attention_view=self.attention_view,
                    normal_read=lambda ref: execution.read_source(ref))
            except ValueError as error:
                if str(error) not in {'unsupported_view_query', 'unknown_execution_source', 'invalid_evidence_refs'}:
                    raise
                result = {'error': 'model_failed'}
            return (Event('view-result-' + _digest(event.event_id)[:24], 'nervous', 'mind.results',
                          'view.result', {'activity_id': data['activity_id'],
                                          'request_ref': data['request_ref'], **result}, event.event_id),)
        if event.kind != 'mind.effects':
            raise ValueError('unsupported_nervous_event')
        from Nervous.watch import apply_watches
        with self._lock:
            state = self._load()
            attention = state.setdefault('attention', {})
            # Derived owner references, not a second store of cognitive claims.
            intents = attention.setdefault('intentions', {})
            for item in data['effects']['intentions']:
                old = intents.get(item['id'])
                entry = {key: item[key] for key in ('id', 'revision', 'commitment')}
                if old and old['revision'] == item['revision'] and old != entry:
                    raise ValueError('intention_effect_identity_conflict')
                if old is None or old['revision'] < item['revision']:
                    intents[item['id']] = entry
            apply_watches(attention, data['effects']['watches'], intents,
                          lambda name: execution.source_observation(name), self.clock())
            attention['focus'] = next(({'id': i['id'], 'revision': i['revision']}
                                       for i in intents.values() if i['commitment'] == 'committed'), None)
            # Save before returning the deterministic outbox. A cut before the
            # mailbox ack re-applies identical revisions without re-sampling.
            self._save(state)
        return (Event('decision-' + _digest(event.event_id)[:24], 'nervous', 'execution',
                      'mind.decision', data['decision'], event.event_id),)

    def begin_observation_cycle(self):
        """Retain a foreground sampling pass until its attention transaction settles."""
        with self._lock:
            state = self._load()
            attention = state.setdefault('attention', {})
            if 'observation_cycle' not in attention:
                attention['observation_cycle'] = uuid.uuid4().hex
                self._save(state)
            return attention['observation_cycle']

    def poll_attention(self, execution, *, observation_cycle=None):
        from Nervous.watch import poll_watches
        with self._lock:
            state = self._load()
            attention = state.get('attention')
            if not attention:
                return
            if observation_cycle is not None and attention.get('observation_cycle') != observation_cycle:
                raise ValueError('observation_cycle_identity_conflict')
            signals = (poll_watches(attention, execution.source_observation, self.clock())
                       if attention.get('watches') else [])
            for signal in signals:
                self._add(state, Event(signal['occurrence_id'], 'nervous', 'mind', 'attention.signal', signal))
                if observation_cycle is None or signal['kind'] != 'source.changed':
                    continue
                # Only the same foreground sampling pass may share a judgment.
                # Equal historical contents or an old pending outbox are not an
                # occurrence identity; later A -> B -> A still creates new work.
                for candidate in state['events']:
                    identity, data = candidate['event_id'], candidate['data']
                    if (candidate['source'] != 'execution' or candidate['kind'] != 'execution.changed'
                            or identity in state['completed'] or identity in attention.get('frames', {})
                            or data.get('observation_cycle') != observation_cycle):
                        continue
                    matched = any(change['file'] == signal['file']
                                  and change['before']['ref'] == signal['before']['ref']
                                  and change['after']['ref'] == signal['after']['ref']
                                  for change in data.get('source_changes', []))
                    if matched:
                        attention.setdefault('coalesced', {})[signal['occurrence_id']] = identity
                        break
            if observation_cycle is not None:
                del attention['observation_cycle']
            if signals or observation_cycle is not None:
                self._save(state)

    def prepare_attention(self, event, mind, execution):
        view = mind.attention_view(event) if hasattr(mind, 'attention_view') else None
        if view is None or event.kind not in {'execution.changed', 'execution.snapshot'}:
            return None
        if not mind.attention_ready(event):
            return None
        snapshot = event.document()['data']['snapshot']
        # Only owner DTOs cross this selection boundary. Source bodies and full
        # cognition remain available through their owners' bounded query seams.
        work = mind.attention_origin(event)
        views = {'event': work, 'mind': view,
                 'execution': {k: snapshot.get(k) for k in ('execution_ref', 'decision', 'state_version',
                     'status', 'files', 'stop_reason', 'unknown_action', 'request_event')},
                 'resources': self.resources(), 'peripheral': self.attention_view()['content']['pending'],
                 'observed_at': self.clock().isoformat()}
        if hasattr(execution, 'view'):
            views['execution_state'] = execution.view('execution.state')
        return self.freeze_attention(event, views)

    def initialize(self, *, goal=None, workspace=None, context_mode=None, pursuit=None, repetition_mode=None):
        """Retain immutable launch input before constructing its receiving organs.

        These are original routing/authorization arguments, never mutable task
        progress. A start/resume after any construction cut republishes the same
        original input and reconstructs each organ from its own durable state.
        """
        from Nervous.storage import write_json
        previous = self.settings.get('initial_input')
        if context_mode not in (None, 'baseline', 'mask', 'summary'):
            raise ValueError('unsupported_context_mode')
        if repetition_mode not in (None, 'off', 'execution', 'mind'):
            raise ValueError('unsupported_repetition_mode')
        if (goal is not None or workspace is not None or context_mode is not None or pursuit is not None
                or repetition_mode is not None):
            text = pursuit if pursuit is not None else goal
            if (type(text) is not str or not text.strip() or len(text) > 4000 or workspace is None
                    or (goal is not None and pursuit is not None)):
                raise ValueError('invalid_initial_input')
            initial = {'workspace': str(Path(workspace).resolve(strict=True))}
            if pursuit is None:
                initial['goal'] = goal
            else:
                initial.update(pursuit=pursuit, mind_id=(previous or {}).get('mind_id') or 'mind-' + uuid.uuid4().hex)
            mode = context_mode or (previous or {}).get('context_mode', 'baseline')
            if mode != 'baseline':
                initial['context_mode'] = mode
            repetition = repetition_mode or (previous or {}).get('repetition_mode', 'off')
            if repetition != 'off':
                initial['repetition_mode'] = repetition
            if previous is not None and previous != initial:
                raise ValueError('initial_input_identity_conflict')
            if previous is None:
                self.settings['initial_input'] = initial
                write_json(self._settings_path, self.settings)
        if 'initial_input' not in self.settings:
            raise ValueError('initial_input_missing')
        initial = self.settings['initial_input']
        self.publish(Event('user-000001', 'user', 'mind', 'user.input',
                           {'event_type': 'USER_SCOPE' if 'pursuit' in initial else 'USER_GOAL',
                            'text': initial.get('pursuit', initial.get('goal'))}))
        return dict(initial)

    def retry_cognition(self, activity_id):
        """Explicit user control, not new reality evidence or automatic sampling."""
        _text(activity_id)
        event = Event('retry-' + _digest(activity_id)[:24], 'user', 'mind',
                      'mind.retry', {'activity_id': activity_id})
        self.publish(event)
        return event

    def extend_budget(self, calls: int, *, output_tokens=None, request_bytes=None):
        from Nervous.storage import write_json
        amounts = {"calls": calls, "output_tokens": output_tokens if output_tokens is not None else calls*5000,
                   "request_bytes": request_bytes if request_bytes is not None else calls*70000}
        bounds = {"calls": 200, "output_tokens": 2000000, "request_bytes": 20000000}
        if any(type(v) is not int or not 1 <= v <= bounds[k] for k, v in amounts.items()):
            raise ValueError("invalid_budget_extension")
        for key, amount in amounts.items():
            self.settings["limits"][key] += amount
        write_json(self._settings_path, self.settings)

    def run(self, mind, execution, *, max_steps=240):
        """Deliver fixed organ messages, then return control at a safe action boundary.

        This loop neither reads cognition nor chooses direction. Each recipient
        owns its durable receipt and each publisher owns its durable outbox.
        """
        from Nervous.provider import BudgetPause
        if type(max_steps) is not int or not 1 <= max_steps <= 240:
            raise ValueError("invalid_foreground_step_bound")
        self.stop_reason = None
        try:
            for _ in range(max_steps):
                self.calls.check_pause()
                observation_cycle = (self.begin_observation_cycle()
                                     if 'pursuit' in self.settings.get('initial_input', {}) else None)
                outcomes = (execution.poll(observation_cycle=observation_cycle)
                            if observation_cycle is not None else execution.poll())
                for event in outcomes:
                    self.publish(event)
                    execution.published(event.event_id)
                self.poll_attention(execution, observation_cycle=observation_cycle)
                delivered = False
                for event in self.ready():
                    target = event.target
                    if event.kind == 'attention.signal':
                        from Nervous.watch import signal_disposition
                        disposition = signal_disposition(self._load().get('attention', {}), event.document()['data'])
                        if disposition == 'deferred':
                            continue
                        if disposition == 'obsolete':
                            self.complete(event.event_id, target)
                            delivered = True
                            break
                    if target == 'nervous':
                        emitted = self.handle(event, mind, execution)
                    elif target == 'execution':
                        emitted = execution.handle(event)
                    else:
                        attention = self.prepare_attention(event, mind, execution)
                        emitted = mind.handle(event, attention=attention) if attention is not None else mind.handle(event)
                    if emitted is not None:
                        self.complete(event.event_id, target, emitted=tuple(emitted))
                        delivered = True
                        break
                if delivered:
                    continue
                from Nervous.watch import signal_disposition
                waiting = [e for e in self.ready() if e.kind != 'attention.signal'
                           or signal_disposition(self._load().get('attention', {}), e.document()['data']) != 'deferred']
                if waiting:
                    self.stop_reason = "pending_organ_work"
                    break
                if not execution.advance():
                    if self.pending('mind'):
                        self.stop_reason = 'deferred_or_unrouted_attention'
                    break
            else:
                self.stop_reason = "foreground_step_bound"
        except BudgetPause as pause:
            self.stop_reason = str(pause)
        from Nervous.storage import write_json
        self.settings['foreground_stop_reason'] = self.stop_reason
        write_json(self._settings_path, self.settings)
        return self.status(mind, execution)

    def status(self, mind, execution):
        """Read-only projection of organ-owned status and outstanding transport."""
        return {"mind": mind.status(), "execution": execution.status(),
                "pending": {t: [e.event_id for e in self.pending(t)]
                            for t in ("mind", "mind.results", "mind.analysis", "execution", "nervous")},
                "stop_reason": self.stop_reason, "cost": self.calls.summary()}
