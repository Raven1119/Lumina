import json
from pathlib import Path

from Conversation_Memory.adapter.models import RecallPolicy
from core.contracts import ChatRequest
from core.draft_context import DraftContextProvider
from core.draft_store import JsonlDraftStore
from core.message_runtime import MessageRuntime
from core.model_client import MockModelClient
from Mind.constant_gate import ConstantMindGate
from Mind.decision_log import JsonlDecisionLog
from Mind.interfaces import MindDecision


_CHAT_BACKGROUND = "stable chat background marker"
_POLICY = RecallPolicy(
    top_k=10,
    max_graph_depth=1,
    max_nodes=20,
    max_evidence_items=3,
    max_chars=5000,
    final_min_score=0.144,
)


class _MemoryContext:
    rendered_text = "safe bounded memory"


class _RecordingRetriever:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def recall(self, query, policy):
        self.calls.append(query)
        return _MemoryContext()


class _FixedGate:
    def __init__(self, recall: bool) -> None:
        self._recall = recall
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def decide(self, user_message, recent_context):
        self.calls.append((user_message, recent_context))
        return MindDecision(recall=self._recall)


class _FailingGate:
    def decide(self, user_message, recent_context):
        raise RuntimeError("mind exploded")


def _runtime(
    tmp_path: Path,
    *,
    retriever: _RecordingRetriever | None = None,
    recall_enabled: bool = True,
    **runtime_kwargs,
) -> MessageRuntime:
    hot = JsonlDraftStore(tmp_path / "hot.jsonl")
    return MessageRuntime(
        hot_store=hot,
        draft_context_provider=DraftContextProvider(hot),
        model_client=MockModelClient(),
        chat_background=_CHAT_BACKGROUND,
        recall_enabled=recall_enabled,
        memory_retriever=retriever,
        recall_policy=_POLICY if retriever is not None else None,
        **runtime_kwargs,
    )


def _read_log(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_constant_gate_preserves_chat_behavior_and_audits(tmp_path: Path) -> None:
    retriever = _RecordingRetriever()
    log_path = tmp_path / "mind" / "decisions.jsonl"
    runtime = _runtime(
        tmp_path,
        retriever=retriever,
        mind_gate=ConstantMindGate(),
        mind_decision_log=JsonlDecisionLog(log_path),
    )

    first = runtime.handle_chat(ChatRequest(message="first"))
    second = runtime.handle_chat(ChatRequest(message="second"))

    assert first.response.status == "ok"
    assert second.response.status == "ok"
    assert retriever.calls == ["first", "second"]
    assert "mind_recall_decided" in first.events

    records = _read_log(log_path)
    assert len(records) == 2
    for record in records:
        assert record["recall"] is True
        assert record["decided_at"].endswith("Z")
        assert record["turn_id"]


def test_declined_recall_skips_retriever(tmp_path: Path) -> None:
    retriever = _RecordingRetriever()
    runtime = _runtime(
        tmp_path,
        retriever=retriever,
        mind_gate=_FixedGate(recall=False),
        mind_decision_log=JsonlDecisionLog(tmp_path / "decisions.jsonl"),
    )

    result = runtime.handle_chat(ChatRequest(message="hello"))

    assert result.response.status == "ok"
    assert retriever.calls == []
    assert "mind_recall_declined" in result.events


def test_gate_failure_fails_open_to_recall(tmp_path: Path) -> None:
    retriever = _RecordingRetriever()
    runtime = _runtime(
        tmp_path,
        retriever=retriever,
        mind_gate=_FailingGate(),
        mind_decision_log=JsonlDecisionLog(tmp_path / "decisions.jsonl"),
    )

    result = runtime.handle_chat(ChatRequest(message="hello"))

    assert result.response.status == "ok"
    assert retriever.calls == ["hello"]
    assert "mind_gate_failed" in result.events


def test_log_failure_fails_open_and_reverts_unaudited_rejection(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    retriever = _RecordingRetriever()
    runtime = _runtime(
        tmp_path,
        retriever=retriever,
        mind_gate=_FixedGate(recall=False),
        mind_decision_log=JsonlDecisionLog(blocker / "decisions.jsonl"),
    )

    result = runtime.handle_chat(ChatRequest(message="hello"))

    assert result.response.status == "ok"
    assert retriever.calls == ["hello"]
    assert "mind_decision_log_failed" in result.events


def test_gate_runs_before_recall_guard(tmp_path: Path) -> None:
    gate = _FixedGate(recall=True)
    log_path = tmp_path / "decisions.jsonl"
    runtime = _runtime(
        tmp_path,
        retriever=None,
        recall_enabled=False,
        mind_gate=gate,
        mind_decision_log=JsonlDecisionLog(log_path),
    )

    result = runtime.handle_chat(ChatRequest(message="hello"))

    assert result.response.status == "ok"
    assert gate.calls[0][0] == "hello"
    assert len(_read_log(log_path)) == 1


def test_absent_gate_preserves_existing_behavior(tmp_path: Path) -> None:
    retriever = _RecordingRetriever()
    runtime = _runtime(tmp_path, retriever=retriever)

    result = runtime.handle_chat(ChatRequest(message="hello"))

    assert result.response.status == "ok"
    assert retriever.calls == ["hello"]
    assert not any(event.startswith("mind_") for event in result.events)
