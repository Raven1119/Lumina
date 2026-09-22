"""Candidate configuration keeps the real facade/Chat/Dream owner seams."""
import asyncio
import httpx

from core import main as app_module
from Mind.constant_gate import ConstantMindGate
from Conversation_Memory.adapter.body_payload import FORMATION_BODY_VERSION
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.tests.test_body_memory import read_fixture
from Conversation_Memory.adapter._associative_recall import Activation
from Conversation_Memory.adapter._body_recall import pack_bodies
from tests.test_query_mind_chat import Answer, post_chat


def test_body_factory_is_explicit_and_preserves_default_writer(tmp_path, monkeypatch):
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    seen = []
    monkeypatch.setenv("LUMINA_DREAM_MAGMA_PERSIST_DIR", str(tmp_path / "magma"))
    monkeypatch.delenv("LUMINA_MEMORY_PROFILE", raising=False)
    monkeypatch.delenv("LUMINA_MIND_GATE_MODE", raising=False)
    monkeypatch.setattr(MagmaMemoryAdapter, "create_real", lambda *a, **kw: seen.append(kw) or kw)
    model, cold = object(), object()
    app_module._build_memory_retriever(model, cold)
    assert seen[-1]["ingestion_version"] == "grounded-formation-v6"
    assert seen[-1]["associative_read_profile"] == "reliable-v2"
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "body-recall-v1")
    app_module._build_memory_retriever(model, cold)
    assert seen[-1]["ingestion_version"] == FORMATION_BODY_VERSION
    assert seen[-1]["associative_read_profile"] == "body-recall-v1"
    assert seen[-1]["cold_store"] is cold and seen[-1]["formation_model"] is model


def test_actual_chat_renders_verified_units_and_shared_dream_version(tmp_path, monkeypatch):
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    from Conversation_Memory.ingestion.state_store import IngestionStateStore
    from Conversation_Memory.adapter import _associative_recall
    reader, body, facts, reads, _ = read_fixture(tmp_path)
    memory = MagmaMemoryAdapter(reader.backend, IngestionStateStore(tmp_path / "never-written.json"),
        ingestion_version=FORMATION_BODY_VERSION, first_hit=FirstHitPolicy(), associative_read_profile="body-recall-v1")
    activation = Activation(((facts[0], 1, 0),), seed_fact_ids=(facts[0].metadata["evidence_id"],))
    monkeypatch.setattr(_associative_recall, "activate", lambda *a, **kw: activation)
    monkeypatch.setenv("LUMINA_MIND_GATE_MODE", "constant")
    monkeypatch.setenv("LUMINA_MEMORY_PROFILE", "body-recall-v1")
    answer = Answer()
    app = app_module.create_app(draft_store_path=tmp_path / "hot.jsonl", cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "compact.json", mind_decision_log_path=tmp_path / "decisions.jsonl",
        model_client=answer, memory_retriever=memory, mind_gate=ConstantMindGate(), recall_enabled=True,
        env_file_path=None, enable_compaction=False)
    question = "What was authorized for the cracked gauge?"
    response = post_chat(app, question)
    assert response.status_code == 200
    assert len(answer.calls) == len(reads) == 1 and answer.calls[0][1] == question
    assert "已核验转述正文" in answer.calls[0][2]
    assert all(f.text in answer.calls[0][2] for f in facts)
    assert "[M2 LUMINA" in answer.calls[0][2]
    assert not (tmp_path / "never-written.json").exists()
    # The existing in-app Dream owner accepts the new explicit writer version.
    assert app.state.dream_runner is not None
    from Dream.models import DreamRunReport
    policies = []
    def run_once(policy):
        policies.append(policy)
        return DreamRunReport.from_results(())
    monkeypatch.setattr(app.state.dream_runner, "run_once", run_once)
    async def run_dream():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            return await client.post("/api/dream/run")
    assert asyncio.run(run_dream()).status_code == 200
    assert policies[0].ingestion_version == FORMATION_BODY_VERSION
