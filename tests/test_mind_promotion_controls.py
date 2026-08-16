from pathlib import Path

from Conversation_Memory.adapter.models import RecallPolicy
from core.draft_context import DraftContextProvider
from core.draft_store import JsonlDraftStore
from core.message_runtime import MessageRuntime
from Mind.decision_log import JsonlDecisionLog
from Mind.interfaces import MindDecision
from scripts.mind_promotion_controls import run_controls


_POLICY = RecallPolicy(
    top_k=10,
    max_graph_depth=1,
    max_nodes=20,
    max_evidence_items=3,
    max_chars=5000,
    final_min_score=0.144,
)


class _RecordingModel:
    client_kind = "model"

    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.system_prompts.append(system_prompt)
        return "control answer"


class _FixedGate:
    def __init__(self, recall: bool) -> None:
        self._recall = recall

    def decide(self, user_message, recent_context):
        return MindDecision(recall=self._recall)


class _FakeRetriever:
    def __init__(self, rendered_text: str) -> None:
        self._rendered_text = rendered_text
        self.calls: list[str] = []

    def recall(self, query, policy):
        self.calls.append(query)
        return type("Ctx", (), {"rendered_text": self._rendered_text})()


def _runtime(tmp_path: Path, *, gate, retriever, model) -> MessageRuntime:
    hot = JsonlDraftStore(tmp_path / "hot.jsonl")
    return MessageRuntime(
        hot_store=hot,
        draft_context_provider=DraftContextProvider(hot),
        model_client=model,
        chat_background="background",
        recall_enabled=True,
        memory_retriever=retriever,
        recall_policy=_POLICY,
        mind_gate=gate,
        mind_decision_log=JsonlDecisionLog(tmp_path / "decisions.jsonl"),
    )


_SPECS = (("control_1", "what caused the failure?", "solvent evaporated"),)


def test_controls_pass_when_gate_true_and_evidence_present(tmp_path: Path) -> None:
    model = _RecordingModel()
    runtime = _runtime(
        tmp_path,
        gate=_FixedGate(True),
        retriever=_FakeRetriever("the solvent evaporated too quickly"),
        model=model,
    )
    report = run_controls(
        runtime,
        log_path=tmp_path / "decisions.jsonl",
        recording_model=model,
        query_specs=_SPECS,
    )
    assert report["passed"] is True
    control = report["controls"][0]
    assert control["gate_recalled"] is True
    assert control["evidence_in_prompt"] is True
    assert control["no_false_decline"] is True


def test_controls_fail_on_false_decline(tmp_path: Path) -> None:
    model = _RecordingModel()
    retriever = _FakeRetriever("the solvent evaporated too quickly")
    runtime = _runtime(
        tmp_path,
        gate=_FixedGate(False),
        retriever=retriever,
        model=model,
    )
    report = run_controls(
        runtime,
        log_path=tmp_path / "decisions.jsonl",
        recording_model=model,
        query_specs=_SPECS,
    )
    assert report["passed"] is False
    control = report["controls"][0]
    assert control["gate_recalled"] is False
    assert control["no_false_decline"] is False
    assert retriever.calls == []


def test_controls_fail_when_evidence_missing(tmp_path: Path) -> None:
    model = _RecordingModel()
    runtime = _runtime(
        tmp_path,
        gate=_FixedGate(True),
        retriever=_FakeRetriever("unrelated memory content"),
        model=model,
    )
    report = run_controls(
        runtime,
        log_path=tmp_path / "decisions.jsonl",
        recording_model=model,
        query_specs=_SPECS,
    )
    assert report["passed"] is False
    assert report["controls"][0]["evidence_in_prompt"] is False
