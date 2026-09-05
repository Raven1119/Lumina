from __future__ import annotations

import hashlib
import json

import pytest

from Execution import ExecutionOrgan, FileContentEquals
from Execution.execution import ClaimComplete, ScriptedModel, Wait


def _open(root, actions):
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "answer.txt").write_text("done", encoding="utf-8")
    return ExecutionOrgan(
        workspace=workspace, event_log_path=root / "execution.jsonl",
        max_decisions=5, model=ScriptedModel(actions),
    )


def _register(organ, pre, *, prediction_id="prediction-1", digest=None):
    return organ.register_prediction(
        prediction_id=prediction_id, pre_ref=pre.evidence_ref,
        prediction_digest=digest or hashlib.sha256(b"completion=true").hexdigest(),
    )


def test_registration_survives_outcome_restart_and_keeps_history_resolvable(tmp_path):
    organ = _open(tmp_path, [Wait("continue"), ClaimComplete()])
    try:
        organ.run_goal("Complete after wake.", FileContentEquals("answer.txt", "done"))
        pre = organ.reality_evidence()[0]
        before = (tmp_path / "execution.jsonl").read_bytes()
        receipt = _register(organ, pre)
        assert receipt is not None
        assert (tmp_path / "execution.jsonl").read_bytes() == before
        assert organ.state.status == "waiting"
        result = organ.deliver_event("continue")
        assert result.status == "completed"
        outcome = organ.reality_evidence()[-1]
        assert outcome.sequence > receipt.sequence
        assert organ.prediction_receipt("prediction-1") == receipt
        assert _register(organ, pre) == receipt
        assert _register(organ, pre, prediction_id="late") is None
    finally:
        organ.shutdown()
    restarted = _open(tmp_path, [])
    try:
        assert restarted.prediction_receipt("prediction-1") == receipt
        assert restarted.reality_evidence(include_historical_pre=True)[0] == pre
        assert all(item.kind == "OUTCOME" for item in restarted.reality_evidence())
        assert _register(restarted, pre, prediction_id="after-restart") is None
        with pytest.raises(ValueError, match="conflict"):
            _register(restarted, pre, digest="0" * 64)
    finally:
        restarted.shutdown()


def test_old_or_foreign_pre_is_not_eligible_even_when_execution_is_waiting(tmp_path):
    first = _open(tmp_path / "first", [Wait("one"), Wait("two"), ClaimComplete()])
    second = _open(tmp_path / "second", [Wait("one"), ClaimComplete()])
    try:
        for organ in (first, second):
            organ.run_goal("Complete after wake.", FileContentEquals("answer.txt", "done"))
        old_pre = first.reality_evidence()[0]
        assert _register(second, old_pre) is None
        first.deliver_event("one")
        assert first.state.status == "waiting"
        assert _register(first, old_pre) is None
        current = first.reality_evidence(after_sequence=old_pre.sequence)[0]
        assert _register(first, current) is not None
    finally:
        first.shutdown()
        second.shutdown()


def test_failed_write_retains_previous_prefix_and_does_not_block_execution(tmp_path, monkeypatch):
    organ = _open(tmp_path, [Wait("continue"), ClaimComplete()])
    try:
        organ.run_goal("Complete after wake.", FileContentEquals("answer.txt", "done"))
        pre = organ.reality_evidence()[0]
        first = _register(organ, pre)
        with monkeypatch.context() as patch:
            def fail_replace(*_):
                raise OSError("simulated interrupted write")
            patch.setattr("Execution.organ.os.replace", fail_replace)
            with pytest.raises(OSError):
                _register(organ, pre, prediction_id="failed-write")
        assert organ.prediction_receipt("prediction-1") == first
        assert organ.prediction_receipt("failed-write") is None
        assert not list(tmp_path.glob(".prediction-*.tmp"))
        assert organ.deliver_event("continue").status == "completed"
    finally:
        organ.shutdown()


def test_receipt_can_be_reconciled_when_response_was_lost(tmp_path, monkeypatch):
    organ = _open(tmp_path, [Wait("continue"), ClaimComplete()])
    try:
        organ.run_goal("Complete after wake.", FileContentEquals("answer.txt", "done"))
        pre = organ.reality_evidence()[0]
        persist = organ._persist_prediction_receipts
        def persist_then_fail(receipts):
            persist(receipts)
            raise OSError("caller lost the response")
        with monkeypatch.context() as patch:
            patch.setattr(organ, "_persist_prediction_receipts", persist_then_fail)
            with pytest.raises(OSError):
                _register(organ, pre)
        assert organ.deliver_event("continue").status == "completed"
        receipt = organ.prediction_receipt("prediction-1")
        assert receipt is not None
        assert _register(organ, pre) == receipt
    finally:
        organ.shutdown()


def test_receipts_are_bounded_and_corruption_fails_closed(tmp_path):
    organ = _open(tmp_path, [Wait("continue"), ClaimComplete()])
    try:
        organ.run_goal("Complete after wake.", FileContentEquals("answer.txt", "done"))
        pre = organ.reality_evidence()[0]
        for index in range(64):
            assert _register(organ, pre, prediction_id=f"p-{index}") is not None
        assert _register(organ, pre, prediction_id="over-budget") is None
        assert _register(organ, pre, prediction_id="p-0") is not None
        path = tmp_path / "execution.prediction-receipts.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["receipts"][0]["prediction_digest"] = "0" * 64
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ValueError, match="receipt file"):
            organ.prediction_receipt("p-0")
        # A broken cognition record never becomes an Execution dependency.
        assert organ.deliver_event("continue").status == "completed"
    finally:
        organ.shutdown()
