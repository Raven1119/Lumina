"""Durable, explicitly driven organ mailboxes; no handlers, clocks or workers."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
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

    def __init__(self, directory: str | Path):
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
        if not self._path.exists():
            return {"events": [], "completed": {}}
        if self._path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("event_history_too_large")
        document = json.loads(self._path.read_text(encoding="utf-8"), object_pairs_hook=_object)
        if (type(document) is not dict or set(document) != {"version", "state", "sha256"}
                or type(document["version"]) is not int or document["version"] != 1
                or document["sha256"] != _digest(document["state"])):
            raise ValueError("invalid_event_history")
        state = document["state"]
        if (type(state) is not dict or set(state) != {"events", "completed"}
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

    def _save(self, state: dict):
        encoded = _encode({"version": 1, "state": state, "sha256": _digest(state)})
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
            # ponytail: at most 256 retained events; no index/database until the
            # experiment establishes a need for longer-lived mailbox storage.
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
            self._save(state)
            return True
