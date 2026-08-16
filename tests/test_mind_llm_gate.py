import pytest

from core.model_client import ModelClientError
from Mind.interfaces import MindDecision
from Mind.llm_gate import LlmMindGate


class _CannedModel:
    client_kind = "model"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append(
            {
                "recent_context": recent_context,
                "user_message": user_message,
                "system_prompt": system_prompt,
            }
        )
        return self.text


class _ExplodingModel:
    client_kind = "model"

    def generate(self, recent_context, user_message, *, system_prompt):
        raise ModelClientError("Provider request failed.")


def test_true_output_decides_recall() -> None:
    gate = LlmMindGate(_CannedModel("true"))
    assert gate.decide("hello", []) == MindDecision(recall=True)


def test_false_output_with_noise_decides_no_recall() -> None:
    gate = LlmMindGate(_CannedModel("  False。\n"))
    assert gate.decide("hello", []) == MindDecision(recall=False)


def test_unparseable_output_raises() -> None:
    gate = LlmMindGate(_CannedModel("I think we should recall because..."))
    with pytest.raises(ValueError):
        gate.decide("hello", [])


def test_provider_failure_propagates_for_fail_open_handling() -> None:
    gate = LlmMindGate(_ExplodingModel())
    with pytest.raises(ModelClientError):
        gate.decide("hello", [])


def test_message_and_context_are_passed_to_model() -> None:
    model = _CannedModel("true")
    gate = LlmMindGate(model)
    context = [{"role": "user", "text": "earlier turn"}]
    gate.decide("current question", context)
    # v2 flattens context into a single classification user message instead of
    # native chat turns.
    assert model.calls[0]["recent_context"] == []
    flattened = model.calls[0]["user_message"]
    assert "current question" in flattened
    assert "earlier turn" in flattened
