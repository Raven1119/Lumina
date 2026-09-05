from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from Nervous.organ import Event, MAX_EVENTS, MAX_EVENT_BYTES, NervousOrgan


def _event(identity="input", *, target="mind", cause=None, source="host", data=None):
    return Event(identity, source, target, "observation", data or {"text": "new evidence"}, cause)


def _process(code, directory):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    result = subprocess.run([sys.executable, "-c", code, str(directory)],
                            env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_request_result_roundtrip_survives_process_exit_and_preserves_fifo(tmp_path):
    initial = _event()
    request = Event("recall-1", "mind", "memory", "recall_requested", {"query": "earlier failure"}, "input")
    with NervousOrgan(tmp_path) as nervous:
        assert nervous.publish(initial)
        assert nervous.pending("memory") == ()
        assert nervous.pending("mind") == (initial,)
        # No completion on a handler error: reading is not destructive.
        assert nervous.pending("mind") == (initial,)
        assert nervous.complete("input", "mind", emitted=(request,))
        assert not nervous.complete("input", "mind", emitted=(request,))
        assert nervous.pending("mind") == ()
    _process('''
import os, sys
from Nervous.organ import Event, NervousOrgan
n = NervousOrgan(sys.argv[1])
r, = n.pending("memory")
assert r.event_id == "recall-1"
n.complete(r.event_id, "memory", emitted=(Event("result-1", "memory", "mind", "recall_result", {"status":"empty"}, r.event_id),))
# No graceful close: disk commits and the OS writer lock must survive process exit.
os._exit(0)
''', tmp_path)
    with NervousOrgan(tmp_path) as nervous:
        assert nervous.pending("memory") == ()
        result, = nervous.pending("mind")
        assert result.event_id == "result-1" and result.causation_id == request.event_id
        assert not nervous.publish(initial)
        nervous.publish(_event("later"))
        assert [item.event_id for item in nervous.pending("mind", 1)] == ["result-1"]
        assert [item.event_id for item in nervous.pending("mind")] == ["result-1", "later"]
        assert nervous.complete("result-1", "mind")
        assert [item.event_id for item in nervous.pending("mind")] == ["later"]


def test_event_identity_and_recipient_validation_are_atomic(tmp_path):
    data = {"nested": [{"value": True}]}
    initial = _event(data=data)
    data["nested"][0]["value"] = False
    assert initial.data["nested"][0]["value"] is True
    with pytest.raises(TypeError):
        initial.data["nested"][0]["value"] = False
    with NervousOrgan(tmp_path) as nervous:
        nervous.publish(initial)
        before = (tmp_path / "events.json").read_bytes()
        # Python considers True == 1; identity must still distinguish their JSON.
        for value in (False, 1, 1.0):
            with pytest.raises(ValueError, match="identity_conflict"):
                nervous.publish(replace(initial, data={"nested": [{"value": value}]}))
        with pytest.raises(ValueError, match="unknown_event_cause"):
            nervous.publish(_event("orphan", cause="missing"))
        with pytest.raises(ValueError, match="wrong_event_recipient"):
            nervous.complete(initial.event_id, "execution")
        valid = _event("response", target="execution", source="mind", cause=initial.event_id)
        for invalid in (replace(valid, event_id="invalid", source="memory"),
                        replace(valid, event_id="invalid", causation_id="missing")):
            with pytest.raises(ValueError, match="invalid_emitted_cause"):
                nervous.complete(initial.event_id, "mind", emitted=(valid, invalid))
        assert (tmp_path / "events.json").read_bytes() == before
        nervous.complete(initial.event_id, "mind", emitted=(valid,))
        completed = (tmp_path / "events.json").read_bytes()
        with pytest.raises(ValueError, match="completion_identity_conflict"):
            nervous.complete(initial.event_id, "mind")
        with pytest.raises(ValueError, match="identity_conflict"):
            nervous.complete(initial.event_id, "mind", emitted=(replace(valid, data={"changed": True}),))
        assert (tmp_path / "events.json").read_bytes() == completed


def test_ack_and_emitted_event_commit_together_and_reconcile_lost_response(tmp_path, monkeypatch):
    initial = _event()
    emitted = _event("response", target="memory", source="mind", cause="input")
    with NervousOrgan(tmp_path) as nervous:
        nervous.publish(initial)
        before = (tmp_path / "events.json").read_bytes()
        real_replace = os.replace
        def fail_before(*_):
            raise OSError("simulated failed replacement")
        with monkeypatch.context() as patch:
            patch.setattr("Nervous.organ.os.replace", fail_before)
            with pytest.raises(OSError):
                nervous.complete("input", "mind", emitted=(emitted,))
        assert (tmp_path / "events.json").read_bytes() == before
        assert nervous.pending("mind") == (initial,)
        assert nervous.pending("memory") == ()
        assert not list(tmp_path.glob(".events-*.tmp"))
        def fail_after(source, target):
            real_replace(source, target)
            raise OSError("simulated lost response")
        with monkeypatch.context() as patch:
            patch.setattr("Nervous.organ.os.replace", fail_after)
            with pytest.raises(OSError):
                nervous.complete("input", "mind", emitted=(emitted,))
    with NervousOrgan(tmp_path) as nervous:
        assert nervous.pending("mind") == ()
        assert nervous.pending("memory") == (emitted,)
        assert not nervous.complete("input", "mind", emitted=(emitted,))


def test_single_owner_and_corrupted_history_fail_closed(tmp_path):
    with NervousOrgan(tmp_path) as nervous:
        nervous.publish(_event())
        output = _process('''
import sys
from Nervous.organ import NervousOrgan
try:
    NervousOrgan(sys.argv[1])
except OSError:
    print("writer-rejected")
else:
    raise RuntimeError("second writer admitted")
''', tmp_path)
        assert output == "writer-rejected"
    path = tmp_path / "events.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["state"]["events"][0]["data"]["text"] = "altered"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_event_history"):
        NervousOrgan(tmp_path)


def test_data_and_history_bounds_keep_old_delivery_receipts(tmp_path):
    for data in ({"callable": lambda: None}, {1: "not a string key"}, {"n": float("nan")},
                 {"n": float("inf")}, {"text": "x" * MAX_EVENT_BYTES}):
        with pytest.raises(ValueError):
            _event(data=data)
    with NervousOrgan(tmp_path) as nervous:
        for index in range(MAX_EVENTS):
            nervous.publish(_event(f"input-{index}"))
        assert len(nervous.pending("mind")) == 32
        with pytest.raises(ValueError, match="event_history_full"):
            nervous.publish(_event("over-budget"))
        assert nervous.complete("input-0", "mind")
        assert not nervous.publish(_event("input-0"))
        assert not nervous.complete("input-0", "mind")
        assert nervous.pending("mind", 1)[0].event_id == "input-1"
        for invalid in (0, 33, True):
            with pytest.raises(ValueError, match="invalid_pending_limit"):
                nervous.pending("mind", invalid)
