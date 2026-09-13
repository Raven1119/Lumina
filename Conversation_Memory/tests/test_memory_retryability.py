"""Executable-stage retryability through the actual Memory ingestion facade.

Frozen deterministic cases separate durable unusable responses from unfinished
transport, verification and persistence; they do not mutate real Cold or memory.
"""

from __future__ import annotations

import copy
import json

import pytest

from Conversation_Memory.adapter.grounded_formation import FORMATION_ENTITY_VERSION
from Conversation_Memory.tests.test_entity_ingestion_v2 import (
    Backend, Model, adapter, fixture, mention, repair_fixture,
)
from Conversation_Memory.tests.test_memory_admission import NoCalls


def state_of(memory, cold):
    return memory.state_store.get(memory.state_store.key(cold.segment_id, FORMATION_ENTITY_VERSION))


def retained(memory, cold, backend):
    return copy.deepcopy((state_of(memory, cold), backend.events, backend.mentions))


class RawStage:
    def __init__(self, stage, response):
        self.stage, self.response = stage, response
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        prefix = "Extract source-grounded" if self.stage == "extract" else "Repair ONLY invalid source_refs"
        assert system_prompt.startswith(prefix)
        self.calls.append(self.stage)
        return self.response


class UnavailableStage:
    def __init__(self, stage):
        self.stage = stage
        self.calls = []

    def generate(self, *args, **kwargs):
        self.calls.append(self.stage)
        raise RuntimeError("synthetic provider unavailable before response")


@pytest.mark.parametrize("response", [
    '{"mentions":',
    '{"mentions":[],"units":{}}',
    '{"error":"formation_output_too_large"}',
], ids=["malformed_json", "wrong_top_level_shape", "declared_overflow"])
def test_saved_unusable_extraction_is_not_retryable_and_never_resampled(tmp_path, response):
    cold, _output = fixture()
    backend, model = Backend(), RawStage("extract", response)
    memory = adapter(tmp_path, backend, model)
    first = memory.ingest(cold)
    assert first.status == "failed" and first.retryable is False
    state = state_of(memory, cold)
    assert state["extracted"]["response"] == response and "verified" not in state
    frozen = retained(memory, cold, backend)
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    repeated = restarted.ingest(cold)
    assert repeated.status == "failed" and repeated.retryable is False
    assert repeated.safe_error_code == first.safe_error_code
    assert model.calls == ["extract"] and no_calls.calls == []
    assert retained(restarted, cold, backend) == frozen


@pytest.mark.parametrize("defect", ["invalid_mention", "unknown_source_turn"])
def test_partial_without_eligible_source_repair_is_not_retryable(tmp_path, defect):
    cold, output = fixture()
    if defect == "invalid_mention":
        output["mentions"].append({
            "handle": "absent", "surface": "Not in source", "turn_id": cold.turns[0].turn_id,
            "occurrence": 0, "identity": "named",
        })
    else:
        bad = copy.deepcopy(output["units"][0])
        bad["source_refs"][0]["turn_id"] = "unknown-turn"
        output["units"].append(bad)
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    first = memory.ingest(cold)
    assert first.status == "failed" and first.retryable is False
    assert len(first.memory_ids) == 3 and len(backend.events) == 3
    state = state_of(memory, cold)
    assert state["status"] == "partial" and any(
        issue["status"] == "pending" for issue in state["verified"]["issues"]
    )
    frozen = retained(memory, cold, backend)
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    repeated = restarted.ingest(cold)
    assert repeated.status == "failed" and repeated.retryable is False
    assert repeated.memory_ids == first.memory_ids and no_calls.calls == []
    assert retained(restarted, cold, backend) == frozen


def test_unused_eligible_source_repair_is_retryable_and_finishes_on_explicit_retry(tmp_path):
    cold, output, repairs = repair_fixture()
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    first = memory.ingest(cold)
    assert first.status == "failed" and first.retryable is True
    initial = retained(memory, cold, backend)
    assert "repair" not in initial[0] and len(initial[1]) == 2
    next_model = Model(repairs=repairs)
    restarted = adapter(tmp_path, backend, next_model)
    result = restarted.ingest(cold)
    assert result.status == "completed" and result.memory_ids[:2] == first.memory_ids
    assert next_model.calls == ["repair", "verify"]
    assert all(state_of(restarted, cold)[name] == initial[0][name]
               for name in ("extracted", "verified", "mentions"))
    assert all(backend.events[mid] == value for mid, value in initial[1].items())


def test_completed_repair_with_remaining_pending_is_not_retryable(tmp_path):
    cold, output, repairs = repair_fixture()
    backend = Backend()
    memory = adapter(tmp_path, backend, Model(output))
    first = memory.ingest(cold)
    assert first.status == "failed" and first.retryable is True
    initial = retained(memory, cold, backend)
    repairs["repairs"][0]["source_refs"] = []
    model = Model(repairs=repairs)
    restarted = adapter(tmp_path, backend, model)
    exhausted = restarted.ingest(cold)
    assert exhausted.status == "failed" and exhausted.retryable is False
    assert exhausted.memory_ids == first.memory_ids and model.calls == ["repair"]
    state = state_of(restarted, cold)
    assert state["status"] == "partial" and "repair_verified" in state
    assert state["repair_verified"]["issues"] == initial[0]["verified"]["issues"]
    assert all(state[name] == initial[0][name] for name in ("extracted", "verified", "mentions"))
    frozen = retained(restarted, cold, backend)
    no_calls = NoCalls()
    last = adapter(tmp_path, backend, no_calls)
    repeated = last.ingest(cold)
    assert repeated.status == "failed" and repeated.retryable is False
    assert retained(last, cold, backend) == frozen and no_calls.calls == []


@pytest.mark.parametrize("response", ['{"repairs":', '{"repairs":[]}'],
                         ids=["malformed_json", "missing_selected_candidate"])
def test_saved_bad_repair_response_is_not_retryable_and_preserves_original_results(tmp_path, response):
    cold, output, _repairs = repair_fixture()
    backend = Backend()
    memory = adapter(tmp_path, backend, Model(output))
    first = memory.ingest(cold)
    assert first.retryable is True
    initial = retained(memory, cold, backend)
    model = RawStage("repair", response)
    restarted = adapter(tmp_path, backend, model)
    result = restarted.ingest(cold)
    assert result.status == "failed" and result.retryable is False
    assert result.memory_ids == first.memory_ids
    state = state_of(restarted, cold)
    assert state["repair"]["response"] == response and "repair_verified" not in state
    assert all(state[name] == initial[0][name] for name in ("extracted", "verified", "mentions"))
    assert (backend.events, backend.mentions) == initial[1:]
    frozen = retained(restarted, cold, backend)
    no_calls = NoCalls()
    last = adapter(tmp_path, backend, no_calls)
    repeated = last.ingest(cold)
    assert repeated.status == "failed" and repeated.retryable is False
    assert no_calls.calls == [] and model.calls == ["repair"]
    assert retained(last, cold, backend) == frozen


@pytest.mark.parametrize("stage", ["extract", "repair"])
def test_provider_without_a_saved_response_remains_retryable(tmp_path, stage):
    cold, output, repairs = repair_fixture()
    backend = Backend()
    if stage == "repair":
        assert adapter(tmp_path, backend, Model(output)).ingest(cold).retryable is True
    model = UnavailableStage(stage)
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(cold)
    assert result.status == "failed" and result.retryable is True
    assert ("extracted" if stage == "extract" else "repair") not in state_of(memory, cold)
    recovery_model = Model(output, repairs=repairs)
    restarted = adapter(tmp_path, backend, recovery_model)
    recovered = restarted.ingest(cold)
    if stage == "extract":
        assert recovered.status == "failed" and recovered.retryable is True
        assert recovery_model.calls == ["extract", "verify"]
        recovered = restarted.ingest(cold)
    assert recovered.status == "completed" and len(backend.events) == 3
    assert recovery_model.calls == (["extract", "verify", "repair", "verify"]
                                    if stage == "extract" else ["repair", "verify"])


@pytest.mark.parametrize("stage", ["initial_verification", "repair_verification"])
def test_unfinished_verification_retries_only_that_stage(tmp_path, stage):
    cold, output, repairs = repair_fixture()
    backend = Backend()
    if stage == "repair_verification":
        assert adapter(tmp_path, backend, Model(output)).ingest(cold).retryable is True
    model = Model(output, fail_verify=True, repairs=repairs)
    memory = adapter(tmp_path, backend, model)
    result = memory.ingest(cold)
    assert result.status == "failed" and result.retryable is True
    state = state_of(memory, cold)
    saved_stage = "extracted" if stage == "initial_verification" else "repair"
    assert saved_stage in state
    assert ("verified" if stage == "initial_verification" else "repair_verified") not in state
    recovery_model = Model(repairs=repairs)
    restarted = adapter(tmp_path, backend, recovery_model)
    recovered = restarted.ingest(cold)
    assert recovery_model.calls == ["verify"]
    assert state_of(restarted, cold)[saved_stage] == state[saved_stage]
    if stage == "initial_verification":
        assert recovered.status == "failed" and recovered.retryable is True
        recovered = restarted.ingest(cold)
    assert recovered.status == "completed" and len(backend.events) == 3


@pytest.mark.parametrize("stage", ["initial_state", "verified_state"])
def test_state_write_failure_remains_retryable_and_reuses_durable_verification(tmp_path, stage):
    cold, output = fixture()
    backend, model = Backend(), Model(output)
    memory = adapter(tmp_path, backend, model)
    original_put = memory.state_store.put

    def fail_write(key, state):
        if stage == "initial_state":
            raise OSError("synthetic failure before initial state write")
        original_put(key, state)
        if "verified" in state:
            raise OSError("synthetic failure after durable verification write")

    memory.state_store.put = fail_write
    result = memory.ingest(cold)
    assert result.status == "failed" and result.retryable is True
    recovery_model = Model(output) if stage == "initial_state" else NoCalls()
    recovered = adapter(tmp_path, backend, recovery_model).ingest(cold)
    assert recovered.status == "completed" and len(backend.events) == 3
    assert recovery_model.calls == (["extract", "verify"] if stage == "initial_state" else [])


@pytest.mark.parametrize("persist_number", [1, 2, 5], ids=["mentions", "event", "relationships"])
def test_graph_persistence_failure_remains_retryable_without_new_model_calls(tmp_path, persist_number):
    cold, output = fixture()
    backend, model = Backend(persist_number), Model(output)
    memory = adapter(tmp_path, backend, model)
    failed = memory.ingest(cold)
    assert failed.status == "failed" and failed.retryable is True
    initial = retained(memory, cold, backend)
    assert "verified" in initial[0] and model.calls == ["extract", "verify"]
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    recovered = restarted.ingest(cold)
    assert recovered.status == "completed" and len(backend.events) == 3
    assert no_calls.calls == []
    assert all(state_of(restarted, cold)[name] == initial[0][name]
               for name in ("extracted", "verified", "mentions"))
    assert all(backend.events[mid] == value for mid, value in initial[1].items())


@pytest.mark.parametrize("failure", ["read_io", "malformed_json"])
def test_state_read_io_is_recoverable_but_corrupt_json_is_not(tmp_path, monkeypatch, failure):
    cold, output = fixture()
    backend = Backend()
    memory = adapter(tmp_path, backend, Model(output))
    completed = memory.ingest(cold)
    saved = memory.state_store.path.read_bytes()
    no_calls = NoCalls()
    restarted = adapter(tmp_path, backend, no_calls)
    path_type = type(memory.state_store.path)
    original_read = path_type.read_text

    def faulty_read(path, *args, **kwargs):
        if path == memory.state_store.path:
            if failure == "read_io":
                raise OSError("synthetic transient checkpoint read failure")
            return "invalid saved JSON"
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as scope:
        scope.setattr(path_type, "read_text", faulty_read)
        failed = restarted.ingest(cold)
    assert failed.status == "failed"
    assert failed.retryable is (failure == "read_io")
    assert failed.safe_error_code == ("state_read_failed" if failure == "read_io" else "state_corrupt")
    assert memory.state_store.path.read_bytes() == saved
    recovered = restarted.ingest(cold)
    assert recovered.already_ingested and recovered.memory_ids == completed.memory_ids
    assert no_calls.calls == []
