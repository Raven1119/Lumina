"""Explicit profile wiring and provenance; no provider or neural execution."""
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key, name in (("LUMINA_MIND_DECISION_LOG_PATH","decisions.jsonl"),
                      ("LUMINA_DRAFT_STORE_PATH","hot.jsonl"),
                      ("LUMINA_DREAM_COLD_DRAFT_PATH","cold.jsonl"),
                      ("LUMINA_DREAM_INGESTION_STATE_PATH","state.json"),
                      ("LUMINA_DREAM_MAGMA_PERSIST_DIR","magma")):
        monkeypatch.setenv(key, str(tmp_path/name))
    monkeypatch.setenv("LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED", "0")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    import socket
    monkeypatch.setattr(socket.socket,"connect",lambda *a,**k:pytest.fail("network forbidden"))


def material(version="grounded-formation-v4", time=None):
    from Conversation_Memory.adapter.grounded_formation import GroundedMemoryUnit, SourceRef
    from Conversation_Memory.adapter.models import ColdDraftSegment, ColdDraftTurn
    start=datetime(2026,9,17,tzinfo=UTC)
    turns=(ColdDraftTurn("proposal","assistant","I suggest visiting tomorrow.",start,"UTC","client"),
           ColdDraftTurn("accepted","user","I accepted only that one visit.",start+timedelta(days=2),"UTC","client"))
    segment=ColdDraftSegment("s","c","pending_digest",turns,start,"UTC","2")
    unit=GroundedMemoryUnit("f","User stated: the user accepted the tomorrow visit.",
                            None,None,None,tuple(SourceRef(t.turn_id,t.content) for t in turns),version,time)
    return segment,unit


def metadata(segment,unit,**kwargs):
    from Conversation_Memory.adapter.magma_adapter import _formed_event_metadata
    return _formed_event_metadata(segment,unit,segment.turns[1],
                                  ingestion_version=unit.formation_version,
                                  configured_entities=("User","Lumina"),**kwargs)


def test_origin_provenance_is_not_earliest_reference_and_no_projection_fallback():
    segment,unit=material()
    result=metadata(segment,unit)
    assert result["origin_turn_id"]==result["turn_id"]=="accepted"
    assert result["provenance"]["source_role"]=="user"
    assert result["provenance"]["source_timestamp"]==segment.turns[1].timestamp.isoformat()
    assert [r["source_role"] for r in result["source_refs"]]==["assistant","user"]
    assert result["subject"] is None and result["subject_entity_ref"] is None
    assert result["entities"]==result["temporal_mentions"]==result["dates_mentioned"]==[]
    assert [r["supporting_span"] for r in result["source_refs"]]==[t.content for t in segment.turns]


def test_approved_time_uses_its_own_anchor_without_changing_origin():
    segment,unit=material(time="tomorrow")
    result=metadata(segment,unit,referenced_time_turn=segment.turns[0])
    assert result["temporal_mentions"]
    assert datetime.fromisoformat(result["temporal_mentions"][0]["reference_timestamp"])==segment.turns[0].timestamp
    assert result["provenance"]["source_timestamp"]==segment.turns[1].timestamp.isoformat()
    assert result["origin_turn_id"]=="accepted"
    without=metadata(segment,unit)
    assert without["temporal_mentions"]==[]


def test_temporal_anchor_cannot_be_forged_or_borrow_an_uncited_expression():
    segment,unit=material(time="tomorrow")
    with pytest.raises(ValueError,match="anchor mismatch"):
        metadata(segment,unit,referenced_time_turn=segment.turns[1])
    with pytest.raises(ValueError,match="anchor mismatch"):
        metadata(segment,unit,referenced_time_turn=replace(segment.turns[0],timestamp=segment.turns[1].timestamp))


def test_facade_accepts_v4_explicit_read_profile_and_default_stays_old(tmp_path,monkeypatch):
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    from Conversation_Memory.ingestion.state_store import IngestionStateStore
    from Conversation_Memory.adapter import _entity_ingestion
    segment,_=material(); result=object(); calls=[]
    monkeypatch.setattr(_entity_ingestion,"ingest_entity_formation",lambda adapter,source:(calls.append(source),result)[1])
    memory=MagmaMemoryAdapter(SimpleNamespace(),IngestionStateStore(tmp_path/"state"),
                             ingestion_version="grounded-formation-v4",formation_model=object(),
                             first_hit=FirstHitPolicy(),associative_read_profile="reliable-v1")
    assert memory.ingest(segment) is result and calls==[segment]
    default=MagmaMemoryAdapter(SimpleNamespace(),IngestionStateStore(tmp_path/"default"))
    assert default.ingestion_version=="grounded-span-v2" and default.associative_read_profile=="first-hit-v1"
    for invalid in (None,"v4",[],True):
        with pytest.raises(ValueError,match="invalid_associative_read_profile"):
            MagmaMemoryAdapter(SimpleNamespace(),IngestionStateStore(tmp_path/"bad"),associative_read_profile=invalid)
    no_model=MagmaMemoryAdapter(SimpleNamespace(),IngestionStateStore(tmp_path/"no-model"),ingestion_version="grounded-formation-v4")
    assert no_model.ingest(segment).safe_error_code=="formation_model_unavailable"


def test_first_hit_checkpoints_remain_disjoint_without_changing_algorithm(tmp_path):
    from Conversation_Memory.adapter._first_hit_ingestion import load_first_hit_stage,FirstHitIngestionError
    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    from Conversation_Memory.ingestion.state_store import IngestionStateStore
    store=IngestionStateStore(tmp_path/"state"); segment,_=material()
    old=SimpleNamespace(state_store=store,ingestion_version="grounded-formation-v2",first_hit=FirstHitPolicy())
    new=SimpleNamespace(state_store=store,ingestion_version="grounded-formation-v4",first_hit=FirstHitPolicy())
    v2=load_first_hit_stage(old,segment,"digest",new_formation=True)
    assert load_first_hit_stage(new,segment,"digest",new_formation=False) is None
    v4=load_first_hit_stage(new,segment,"digest",new_formation=True)
    assert v2["schema_version"]==v4["schema_version"]=="first-hit-v1"
    assert store.get(store.key("s","first-hit-v1"))==v2
    key=store.key("s","first-hit-v1:grounded-formation-v4")
    assert store.get(key)==v4 and v4["formation_version"]=="grounded-formation-v4"
    assert load_first_hit_stage(new,segment,"digest",new_formation=False)==v4
    store.put(key,{**v4,"formation_version":"grounded-formation-v2"})
    with pytest.raises(FirstHitIngestionError,match="first_hit_checkpoint_invalid"):
        load_first_hit_stage(new,segment,"digest",new_formation=False)


def test_backend_v4_suppresses_upstream_guessed_entity_enrichment(tmp_path,monkeypatch):
    from Conversation_Memory.adapter.backend import RealMagmaBackend
    from Conversation_Memory.adapter import _source_backend
    graph=ModuleType("memory.graph_db")
    graph.EventNode=type("EventNode",(),{})
    graph.NodeType=SimpleNamespace(EVENT="EVENT")
    graph.TraversalConstraints=object
    class Trg:
        def __init__(self,**kwargs): self.graph_db=SimpleNamespace()
        def _extract_event(self,content,metadata=None):
            return SimpleNamespace(content_narrative=content[:3],entities=["Alice"],keywords=[])
    module=ModuleType("memory.trg_memory"); module.TemporalResonanceGraphMemory=Trg
    package=ModuleType("memory"); package.__path__=[]
    monkeypatch.setitem(sys.modules,"memory",package)
    monkeypatch.setitem(sys.modules,"memory.graph_db",graph)
    monkeypatch.setitem(sys.modules,"memory.trg_memory",module)
    monkeypatch.setattr(RealMagmaBackend,"_rebuild_indexes",lambda *a:None)
    monkeypatch.setattr(RealMagmaBackend,"_rebuild_first_hit_view",lambda *a:None)
    monkeypatch.setattr(_source_backend,"rebuild",lambda *a:None)
    backend=RealMagmaBackend(tmp_path/"backend")
    old=backend.trg._extract_event("Alice mentioned a proposal.",{"formation_version":"grounded-formation-v2"})
    new=backend.trg._extract_event("Alice mentioned a proposal.",{"formation_version":"grounded-formation-v4"})
    assert old.entities==["Alice"] and new.entities==[]
    assert old.content_narrative==new.content_narrative=="Alice mentioned a proposal."


def test_dream_provider_uses_explicit_v4_and_cached_single_adapter(tmp_path,monkeypatch):
    import Dream.runner as runner
    calls=[]
    def create(*args,**kwargs):
        calls.append(kwargs); return SimpleNamespace(**kwargs)
    monkeypatch.setattr(runner.MagmaMemoryAdapter,"create_real",create)
    model=object()
    provider=runner.RealMemoryIngestorProvider(tmp_path/"magma",tmp_path/"state",model,
                                              first_hit=object(),associative_read_profile="reliable-v1")
    first=provider.get("grounded-formation-v4")
    assert provider.get("grounded-formation-v4") is first and len(calls)==1
    assert first.formation_model is model and first.associative_read_profile=="reliable-v1"
    assert first.ingestion_version=="grounded-formation-v4"


@pytest.mark.parametrize("args,version,read", [([],"grounded-formation-v2",None),
                         (["--first-hit"],"grounded-formation-v2",None),
                         (["--reliable-memory"],"grounded-formation-v4","reliable-v1")])
def test_dream_cli_profile_is_explicit(args,version,read,monkeypatch,capsys):
    import Dream.runner as runner
    calls=[]; policies=[]
    model=SimpleNamespace(client_kind="model")
    monkeypatch.setattr(runner,"build_formation_model_client",lambda:model)
    def build(injected,**kwargs):
        assert injected is model
        calls.append(kwargs)
        def run(policy):
            policies.append(policy); return runner.DreamRunReport.from_results(())
        return SimpleNamespace(run_once=run)
    monkeypatch.setattr(runner,"build_default_runner",build)
    assert runner.main(args)==0
    assert policies[0].ingestion_version==version and calls[0].get("associative_read_profile")==read
    assert bool(calls[0].get("first_hit"))==bool(args)
    capsys.readouterr()


def test_dream_reliable_cli_rejects_conflicting_explicit_version(monkeypatch,capsys):
    import Dream.runner as runner
    monkeypatch.setattr(runner,"build_formation_model_client",lambda:SimpleNamespace(client_kind="model"))
    monkeypatch.setattr(runner,"build_default_runner",lambda *a,**k:pytest.fail("conflicting profile built"))
    assert runner.main(["--reliable-memory","--ingestion-version","grounded-formation-v2"])==1
    output=capsys.readouterr().out
    assert "dream_initialization_failed" in output and "Traceback" not in output


def test_reliable_read_requires_explicit_first_hit_configuration(tmp_path):
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from Conversation_Memory.ingestion.state_store import IngestionStateStore
    with pytest.raises(ValueError,match="reliable_read_requires_first_hit"):
        MagmaMemoryAdapter(SimpleNamespace(),IngestionStateStore(tmp_path/"state"),
                           ingestion_version="grounded-formation-v4",associative_read_profile="reliable-v1")


@pytest.mark.parametrize("code",["reliable_stage_delivery_unknown","reliable_stage_checkpoint_invalid",
                                  "reliable_f2_authorization_invalid","reliable_g2_authorization_invalid",
                                  "reliable_identity_candidate_budget"])
def test_dream_exposes_safe_v4_stage_outcomes_without_private_body(code):
    from Dream.cold_draft_digest import ColdDraftDigestionTask
    assert ColdDraftDigestionTask._safe_ingestion_error(SimpleNamespace(safe_error_code=code))==code
    assert ColdDraftDigestionTask._safe_ingestion_error(SimpleNamespace(safe_error_code="secret path"))=="memory_ingestion_failed"


def test_v4_metadata_cannot_override_origin_speaker():
    from Conversation_Memory.adapter.magma_adapter import _formed_event_metadata
    segment,unit=material()
    with pytest.raises(ValueError,match="origin anchor mismatch"):
        _formed_event_metadata(segment,unit,replace(segment.turns[1],role="assistant"),
                               ingestion_version=unit.formation_version,configured_entities=())
