from scripts.mind_gate_operational import (
    CountingModelClient,
    compare_runs,
    run_sequence,
)


class _FakePoster:
    """Minimal stand-in for a TestClient POST /api/chat call."""

    def __init__(self, latency_s: float = 0.0) -> None:
        self.latency_s = latency_s
        self.messages: list[str] = []

    def post(self, message: str) -> int:
        self.messages.append(message)
        return 200


def test_counting_client_counts_generate_calls() -> None:
    class _Inner:
        client_kind = "model"

        def generate(self, recent_context, user_message, *, system_prompt):
            return "answer"

    client = CountingModelClient(_Inner())
    client.generate([], "one", system_prompt="bg")
    client.generate([], "two", system_prompt="bg")
    assert client.calls == 2
    assert client.client_kind == "model"


def test_run_sequence_records_status_and_latency_per_message() -> None:
    poster = _FakePoster()
    records = run_sequence(poster.post, ["m1", "m2"])
    assert [r["message"] for r in records] == ["m1", "m2"]
    assert all(r["status"] == 200 for r in records)
    assert all(r["latency_ms"] >= 0 for r in records)


def test_compare_runs_computes_acceptance() -> None:
    baseline = {
        "records": [{"status": 200, "latency_ms": 100.0}] * 4,
        "answer_calls": 4,
        "gate_calls": 0,
    }
    candidate = {
        "records": [{"status": 200, "latency_ms": 1600.0}] * 4,
        "answer_calls": 4,
        "gate_calls": 4,
    }
    result = compare_runs(baseline, candidate)
    assert result["added_calls_per_message"] == 1.0
    assert result["added_latency_median_ms"] == 1500.0
    assert result["acceptance"]["added_calls_exactly_one"] is True
    assert result["acceptance"]["median_added_latency_within_3s"] is True
    assert result["acceptance"]["all_responses_ok"] is True


def test_compare_runs_rejects_missing_gate_calls() -> None:
    baseline = {
        "records": [{"status": 200, "latency_ms": 100.0}],
        "answer_calls": 1,
        "gate_calls": 0,
    }
    candidate = {
        "records": [{"status": 200, "latency_ms": 100.0}],
        "answer_calls": 1,
        "gate_calls": 0,
    }
    result = compare_runs(baseline, candidate)
    assert result["acceptance"]["added_calls_exactly_one"] is False
