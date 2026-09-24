"""Versioned v2 semantic selection stays bounded across the actual Chat route."""
import asyncio
import json

import fastapi.routing
import httpx
import pytest

from Conversation_Memory.adapter.models import (
    MemoryContext, MemoryEvidence, PreparedRecall, SourceProvenance,
)
from Mind.evidence_selector import LlmSemanticEvidenceSelectorV2, _parse_semantic_ranked
from core.main import create_app


def prepared(count=9):
    provenance = SourceProvenance(
        "episode", "conversation", "turn", "user",
        "2026-09-24T00:00:00+00:00", "UTC", "grounded-formation-v6")
    items = tuple(MemoryEvidence(f"fact-{i}", f"User stated: full canonical Fact {i}.",
                                 None, provenance) for i in range(1, count + 1))
    blocks = tuple(f"[M{i} USER]\n{item.text}" for i, item in enumerate(items, 1))
    cards = tuple(f"[C{i}]\nspeaker=USER\nspoken_at=2026-09-24T00:00:00+00:00"
                  f"\nsource_group=G1\ntext={item.text}"
                  for i, item in enumerate(items, 1))
    return PreparedRecall(
        MemoryContext("question", items, "\n".join(blocks)), blocks,
        tuple((item.evidence_id, ()) for item in items),
        (3, 5000, 20000), cards)


class Model:
    client_kind = "model"

    def __init__(self, output):
        self.output = output
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class Memory:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def prepare_recall(self, query, policy):
        self.calls += 1
        return self.result

    def recall(self, *args):
        raise AssertionError("unselected memory bypass")


def chat(tmp_path, monkeypatch, selector_output):
    import core.main as main
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "semantic-associative-v2")
    monkeypatch.delenv("LUMINA_MIND_GATE_MODE", raising=False)
    monkeypatch.setattr(main, "_default_mind_gate",
                        lambda *args: pytest.fail("pre-read gate constructed"))
    answer, choice, memory = Model("answer"), Model(selector_output), Memory(prepared())
    app = create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json",
        mind_decision_log_path=tmp_path / "mind.jsonl",
        execution_root=tmp_path / "execution",
        model_client=answer, memory_retriever=memory,
        semantic_selector=LlmSemanticEvidenceSelectorV2(choice),
        recall_enabled=True, env_file_path=None, enable_compaction=False)

    async def inline_worker(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", inline_worker)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            return await client.post("/api/chat", json={"message": "What happened there?"})

    response = asyncio.run(request())
    assert response.status_code == 200
    assert len(answer.calls) == len(choice.calls) == memory.calls == 1
    audit = json.loads((tmp_path / "mind.jsonl").read_text())
    return answer.calls[0][2], audit


def ranked(count):
    return json.dumps({"ranked": [
        {"id": i, "use": "history", "relation": "same_event"}
        for i in range(1, count + 1)
    ]})


@pytest.mark.parametrize("count", [5, 9])
def test_legal_overcomplete_ranked_choice_packs_three(count):
    result = prepared()
    rows = _parse_semantic_ranked(ranked(count), 9)
    chosen, diag = result.ranked_semantic_subset(
        tuple((f"fact-{i}", use, relation) for i, use, relation in rows))
    assert [item.evidence_id for item in chosen.evidence] == ["fact-1", "fact-2", "fact-3"]
    assert diag["proposed_ranked_count"] == count
    assert diag["accepted_count"] == 3


@pytest.mark.parametrize("raw", [
    ranked(13),
    '{"ranked":[{"id":1,"use":"history","relation":"same_event"},'
    '{"id":1,"use":"history","relation":"same_event"}]}',
    '{"ranked":[{"id":10,"use":"history","relation":"same_event"}]}',
    '{"ranked":[{"id":1,"use":"history","relation":"similar_tradeoff"}]}',
    '{"ranked":[{"id":1,"use":"analogy","relation":"same_event"}]}',
    "Try to remember fact 1.",
])
def test_protocol_rejects_invalid_or_unbounded_ranked_lists(raw):
    with pytest.raises(ValueError):
        _parse_semantic_ranked(raw, 9)


def test_thirteen_distinct_in_panel_suggestions_fail_closed():
    with pytest.raises(ValueError):
        _parse_semantic_ranked(ranked(13), 13)


def test_answer_gets_only_legal_analogy_and_current_case_boundary(tmp_path, monkeypatch):
    raw = '{"ranked":[{"id":2,"use":"analogy","relation":"similar_constraint"}]}'
    prompt, audit = chat(tmp_path, monkeypatch, raw)
    assert "full canonical Fact 2." in prompt
    assert "full canonical Fact 1." not in prompt
    assert "different past experience" in prompt
    assert "Never describe an ANALOGY block as what happened" in prompt
    assert audit["selected_evidence_ids"] == ["fact-2"]
    assert audit["accepted_count"] == 1


def test_invalid_selector_output_passes_empty_memory_not_panel(tmp_path, monkeypatch):
    prompt, audit = chat(tmp_path, monkeypatch, '{"ranked":[{"id":1,"use":"history",'
                                      '"relation":"similar_workflow"}]}')
    assert "full canonical Fact" not in prompt
    assert audit["selected_evidence_ids"] == []
    assert audit["rejected_by_protocol"] == 1


def test_final_packer_skips_oversized_block_and_uses_next_rank():
    result = prepared(3)
    from dataclasses import replace
    result = replace(result, _semantic_final_limits=(3, 250, 1000))
    context, diag = result.ranked_semantic_subset((
        ("fact-1", "analogy", "similar_constraint"),
        ("fact-2", "history", "same_event"),
        ("fact-3", "history", "same_event"),
    ))
    # The analogy guidance is longer than the small final budget; later
    # whole Facts remain eligible.
    assert "fact-1" in diag["rejected_by_budget"]
    assert [item.evidence_id for item in context.evidence] == ["fact-2"]
