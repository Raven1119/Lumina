"""Explicit v3 Chat injects only selected Facts and keeps absence grounded."""
import asyncio
import json

import fastapi.routing
import httpx
import pytest

from Conversation_Memory.adapter.models import (
    MemoryContext, MemoryEvidence, PreparedRecall, SourceProvenance,
)
from Mind.evidence_selector import LlmSemanticEvidenceSelectorV3
from core.main import create_app


class Model:
    client_kind = "model"

    def __init__(self, output):
        self.output = output
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        return self.output


class Memory:
    def __init__(self):
        provenance = SourceProvenance(
            "episode", "conversation", "turn", "user",
            "2026-09-24T00:00:00+00:00", "UTC", "grounded-formation-v6")
        items = tuple(MemoryEvidence(
            f"fact-{i}", f"User stated: canonical historical Fact {i}.",
            None, provenance) for i in (1, 2))
        blocks = tuple(f"[M{i} USER]\n{item.text}" for i, item in enumerate(items, 1))
        cards = tuple(f"[C{i}]\nspeaker=USER\nspoken_at=2026-09-24T00:00:00+00:00"
                      f"\nsource_group=G1\ntext={item.text}"
                      for i, item in enumerate(items, 1))
        self.prepared = PreparedRecall(MemoryContext("question", items, "\n".join(blocks)),
                                       blocks, tuple((item.evidence_id, ()) for item in items),
                                       (3, 5000, 20000), cards)
        self.calls = 0

    def prepare_recall(self, query, policy):
        self.calls += 1
        assert policy.max_evidence_items == 40
        return self.prepared

    def recall(self, *_args):
        raise AssertionError("unselected memory bypass")


def route(tmp_path, monkeypatch, selected_output):
    import core.main as main
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "semantic-associative-v3")
    monkeypatch.delenv("LUMINA_MIND_GATE_MODE", raising=False)
    monkeypatch.setattr(main, "_default_mind_gate",
                        lambda *args: pytest.fail("pre-read gate constructed"))
    answer, selection, memory = Model("answer"), Model(selected_output), Memory()
    app = create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json",
        mind_decision_log_path=tmp_path / "mind.jsonl",
        execution_root=tmp_path / "execution",
        model_client=answer, memory_retriever=memory,
        semantic_selector=LlmSemanticEvidenceSelectorV3(selection),
        recall_enabled=True, env_file_path=None, enable_compaction=False)

    async def inline_worker(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", inline_worker)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            return await client.post("/api/chat", json={"message": "What happened?"})

    response = asyncio.run(request())
    assert response.status_code == 200
    assert len(answer.calls) == len(selection.calls) == memory.calls == 1
    audit = json.loads((tmp_path / "mind.jsonl").read_text())
    return answer.calls[0][2], selection.calls[0][2], audit


def test_v3_asgi_analogy_uses_selected_fact_and_nonexhaustive_guidance(tmp_path, monkeypatch):
    prompt, selector_prompt, audit = route(
        tmp_path, monkeypatch,
        '{"ranked":[{"id":2,"use":"analogy","relation":"similar_constraint"}]}')
    assert "canonical historical Fact 2." in prompt
    assert "canonical historical Fact 1." not in prompt
    assert "Absence from this block is NOT evidence" in prompt
    assert "different past experience" in prompt
    assert "FIRST decide whether" in selector_prompt
    assert audit["selected_evidence_ids"] == ["fact-2"]


def test_v3_selector_failure_keeps_guard_and_never_restores_panel(tmp_path, monkeypatch):
    prompt, _, audit = route(
        tmp_path, monkeypatch,
        '{"ranked":[{"id":1,"use":"history","relation":"similar_tradeoff"}]}')
    assert "canonical historical Fact" not in prompt
    assert "Absence from this block is NOT evidence" in prompt
    assert audit["selected_evidence_ids"] == []
    assert audit["rejected_by_protocol"] == 1


def test_v3_preserves_twelve_row_parser_and_three_fact_packing():
    provenance = Memory().prepared.context.evidence[0].provenance
    items = tuple(MemoryEvidence(
        f"fact-{i}", f"User stated: canonical Fact {i}.", None, provenance)
        for i in range(1, 14))
    blocks = tuple(f"[M{i} USER]\n{item.text}" for i, item in enumerate(items, 1))
    cards = tuple(f"[C{i}]\nspeaker=USER\nspoken_at={provenance.source_timestamp}"
                  f"\nsource_group=G1\ntext={item.text}"
                  for i, item in enumerate(items, 1))
    result = PreparedRecall(MemoryContext("question", items, "\n".join(blocks)),
                            blocks, tuple((item.evidence_id, ()) for item in items),
                            (3, 5000, 20000), cards)
    def ranked(count):
        return json.dumps({"ranked": [
            {"id": i, "use": "history", "relation": "same_event"}
            for i in range(1, count + 1)
        ]})
    selector = LlmSemanticEvidenceSelectorV3(Model(ranked(12)))
    proposed = selector.select_ranked("What happened?", [], result.selection_items)
    assert len(proposed) == 12
    context, diagnostics = result.ranked_semantic_subset(proposed)
    assert [item.evidence_id for item in context.evidence] == [
        "fact-1", "fact-2", "fact-3",
    ]
    assert diagnostics["proposed_ranked_count"] == 12
    assert diagnostics["accepted_count"] == 3
    with pytest.raises(ValueError, match="invalid_semantic_selection"):
        LlmSemanticEvidenceSelectorV3(Model(ranked(13))).select_ranked(
            "What happened?", [], result.selection_items,
        )
