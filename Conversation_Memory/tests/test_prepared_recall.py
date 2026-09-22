"""Actual Memory subset seam with controlled candidates/logits, no model I/O."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from Conversation_Memory.adapter.models import MemoryContext, PreparedRecall, RecallPolicy
from test_source_context_rendering import adapter, candidate


@pytest.fixture(autouse=True)
def isolated_mind_log(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_MIND_DECISION_LOG_PATH", str(tmp_path / "mind.jsonl"))


def evidence_ids(context):
    return tuple(item.evidence_id for item in context.evidence)


def relation_rows():
    bridge = candidate("bridge", "Nora uses Station Nine.", subject="PRIVATE_NORA", obj="PRIVATE_STATION")
    endpoint = candidate("endpoint", "Station Nine does not heat basalt.", subject="PRIVATE_STATION",
                         association_chain_evidence_ids=["bridge", "endpoint"],
                         association_bridge_evidence_ids=["bridge"])
    other = candidate("other", "Pip reads.", subject="PRIVATE_PIP")
    scores = {bridge.text: -20.0, bridge.text + "\n" + endpoint.text: 20.0, other.text: 0.0}
    return [bridge, endpoint, other], scores


@pytest.mark.parametrize("rich", [False, True])
def test_prepare_preserves_default_read_exactly_and_subsets_do_no_io(tmp_path, rich):
    rows = [candidate("fact", "Nora said:\n[USER] is a literal token.\nNot a new source.", subject="PRIVATE_NORA"),
            candidate("guess", "I guessed copper; this was not confirmed.", role="assistant")]
    before = deepcopy(rows)
    memory, backend, scorer = adapter(tmp_path, rows)
    policy = RecallPolicy(include_source_context=rich, max_chars=5000)
    ordinary = memory.recall("  What did Nora say?  ", policy)
    prepared = memory.prepare_recall("  What did Nora say?  ", policy)
    assert prepared.context == ordinary
    assert len(backend.calls) == len(scorer.calls) == 2
    assert "\n".join(block for _, block in prepared.selection_items) == ordinary.rendered_text
    assert prepared.subset(evidence_ids(ordinary)) == ordinary
    assert prepared.subset(("guess",)).evidence == (ordinary.evidence[1],)
    assert prepared.subset(("guess",)).rendered_text == prepared.selection_items[1][1]
    assert prepared.subset(()).evidence == ()
    assert prepared.subset(()).query == ordinary.query
    assert prepared.context == ordinary and rows == before
    assert len(backend.calls) == len(scorer.calls) == 2
    assert not (tmp_path / "unused.json").exists()


def test_subset_keeps_labels_source_time_roles_and_full_identity_facts_when_selected(tmp_path):
    rows = [candidate("nurse", "Nora was a nurse in 2020.", subject="PRIVATE_A"),
            candidate("nurse-fact", "Nora studies basalt.", subject="PRIVATE_A", obj="PRIVATE_BASALT"),
            candidate("pilot", "Nora is a pilot.", subject="PRIVATE_B"),
            candidate("pilot-fact", "Nora owns basalt.", subject="PRIVATE_B", obj="PRIVATE_BASALT")]
    memory, backend, scorer = adapter(tmp_path, rows)
    prepared = memory.prepare_recall("What does the pilot own?", RecallPolicy(include_source_context=True))
    blocks = dict(prepared.selection_items)
    selected = prepared.subset(("pilot-fact", "pilot"))
    assert evidence_ids(selected) == ("pilot", "pilot-fact")
    assert selected.rendered_text == blocks["pilot"] + "\n" + blocks["pilot-fact"]
    assert "subject_binding=I3" in selected.rendered_text
    assert 'spoken_at="2026-06-03T14:15:00+02:00"' in selected.rendered_text
    assert 'timezone="Europe/Paris"' in selected.rendered_text
    assert "PRIVATE_" not in selected.rendered_text
    assert selected.evidence == prepared.context.evidence[2:]
    # Role identity alone does not manufacture an occupation dependency.
    assert evidence_ids(prepared.subset(("pilot-fact",))) == ("pilot-fact",)
    assert len(backend.calls) == len(scorer.calls) == 1


def test_endpoint_selection_restores_exact_bridge_and_deduplicates_overlap(tmp_path):
    rows, scores = relation_rows()
    memory, backend, scorer = adapter(tmp_path, rows, scores=scores)
    prepared = memory.prepare_recall("What does the station do?", RecallPolicy(
        include_source_context=True, max_chars=5000, final_min_score=0.05))
    assert evidence_ids(prepared.context) == ("bridge", "endpoint", "other")
    selected = prepared.subset(("endpoint",))
    assert evidence_ids(selected) == ("bridge", "endpoint")
    assert selected == prepared.subset(("endpoint", "bridge"))
    assert selected.rendered_text == "\n".join(block for _, block in prepared.selection_items[:2])
    assert "does not heat basalt" in selected.rendered_text
    assert evidence_ids(prepared.subset(("bridge",))) == ("bridge",)
    assert len(backend.calls) == len(scorer.calls) == 1


def test_complete_packing_budget_is_preserved_before_selection(tmp_path):
    rows, scores = relation_rows()
    memory, _, _ = adapter(tmp_path, rows, scores=scores)
    policy = RecallPolicy(include_source_context=True, max_chars=5000, final_min_score=0.05)
    full = memory.prepare_recall("Station?", policy)
    pair = full.subset(("endpoint",)).rendered_text
    exact = memory.prepare_recall("Station?", replace(policy, max_chars=len(pair)))
    assert exact.context.rendered_text == pair
    assert exact.subset(("endpoint",)).rendered_text == pair
    assert exact.context.truncated and exact.subset(()).truncated
    for small in (replace(policy, max_chars=len(pair)-1), replace(policy, max_evidence_items=1)):
        result = memory.prepare_recall("Station?", small)
        assert evidence_ids(result.context) == ("other",)
        assert result.context.truncated
        assert result.selection_items == (("other", result.context.rendered_text),)
        with pytest.raises(ValueError, match="invalid_recall_selection"):
            result.subset(("endpoint",))


@pytest.mark.parametrize("invalid", [["fact"], "fact", (True,), (1,), (None,),
                                     ("missing",), ("fact", "fact"), (("fact",),)])
def test_selection_rejects_wrong_types_unknown_ids_and_duplicates(tmp_path, invalid):
    memory, backend, scorer = adapter(tmp_path, [candidate("fact", "Nora reads.")])
    prepared = memory.prepare_recall("Nora", RecallPolicy())
    before = prepared.context
    with pytest.raises(ValueError, match="invalid_recall_selection"):
        prepared.subset(invalid)
    assert prepared.context == before
    assert len(backend.calls) == len(scorer.calls) == 1


def test_prepared_value_and_source_dtos_are_immutable(tmp_path):
    memory, _, _ = adapter(tmp_path, [candidate("fact", "Nora reads.")])
    prepared = memory.prepare_recall("Nora", RecallPolicy())
    assert type(prepared.selection_items) is tuple
    assert all(type(pair) is tuple for pair in prepared.selection_items)
    for target, field, value in ((prepared, "context", None),
                                 (prepared.context, "rendered_text", "edited"),
                                 (prepared.context.evidence[0], "text", "edited"),
                                 (prepared.context.evidence[0].provenance, "source_role", "assistant")):
        with pytest.raises(FrozenInstanceError):
            setattr(target, field, value)


@pytest.mark.parametrize("invalid_chain", [False, True])
def test_prepared_only_missing_dependency_does_not_break_original_read(tmp_path, invalid_chain):
    ancestor = candidate("ancestor", "Ada owns Station Nine.", subject="PRIVATE_ADA", obj="PRIVATE_STATION")
    bridge = candidate("bridge", "Station Nine serves Nora.", subject="PRIVATE_STATION", obj="PRIVATE_NORA",
                       association_chain_evidence_ids=["missing" if invalid_chain else "ancestor", "bridge"],
                       association_bridge_evidence_ids=["missing" if invalid_chain else "ancestor"])
    endpoint = candidate("endpoint", "Nora studies basalt.", subject="PRIVATE_NORA",
                         association_chain_evidence_ids=["bridge", "endpoint"],
                         association_bridge_evidence_ids=["bridge"])
    scores = {ancestor.text: -20.0, bridge.text: -20.0,
              ancestor.text + "\n" + bridge.text: -20.0,
              bridge.text + "\n" + endpoint.text: 20.0}
    memory, backend, scorer = adapter(tmp_path, [ancestor, bridge, endpoint], scores=scores)
    policy = RecallPolicy(final_min_score=0.05)
    ordinary = memory.recall("Nora", policy)
    assert evidence_ids(ordinary) == ("bridge", "endpoint") and ordinary.safe_error_code is None
    prepared = memory.prepare_recall("Nora", policy)
    assert prepared.context == ordinary
    with pytest.raises(ValueError, match="prepared_recall_unavailable"):
        _ = prepared.selection_items
    with pytest.raises(ValueError, match="prepared_recall_unavailable"):
        prepared.subset(())
    assert len(backend.calls) == len(scorer.calls) == 2


def test_transitive_recorded_dependencies_close_without_semantic_inference(tmp_path):
    rows, scores = relation_rows()
    third = candidate("third", "Basalt is blue.", subject="PRIVATE_STATION",
                      association_chain_evidence_ids=["endpoint", "third"],
                      association_bridge_evidence_ids=["endpoint"])
    # Both bridge roles must be explicit for an actual validated two-fact path.
    rows[1].metadata["object_entity_ref"] = "PRIVATE_BASALT"
    memory, _, _ = adapter(tmp_path, rows + [third])
    prepared = memory.prepare_recall("Station?", RecallPolicy(max_chars=5000))
    assert evidence_ids(prepared.subset(("third",))) == ("bridge", "endpoint", "third")


@pytest.mark.parametrize("mode", ["invalid", "empty", "backend", "reranker"])
def test_empty_or_failed_read_keeps_safe_context_without_another_read(tmp_path, mode):
    memory, backend, scorer = adapter(tmp_path, [] if mode == "empty" else [candidate("fact", "Nora reads.")])
    if mode == "backend":
        def fail(*args, **kwargs):
            backend.calls.append((args, kwargs))
            raise RuntimeError("private exception")
        backend.recall = fail
    if mode == "reranker":
        memory._bge_reranker = None
    prepared = memory.prepare_recall("   " if mode == "invalid" else "Nora", RecallPolicy())
    assert prepared.selection_items == ()
    assert prepared.subset(()) == prepared.context
    assert not prepared.context.evidence
    assert prepared.context.safe_error_code == ("invalid_query" if mode == "invalid" else
                                                 None if mode == "empty" else "recall_unavailable")
    assert len(backend.calls) == (0 if mode == "invalid" else 1)
    assert not scorer.calls


def test_prepared_accepts_existing_dto_namespace_alias_without_reconstruction(tmp_path):
    from Conversation_Memory.adapter.models import MemoryContext as PublicContext
    memory, _, _ = adapter(tmp_path, [candidate("fact", "Nora reads.")])
    original = memory.prepare_recall("Nora", RecallPolicy())
    context = PublicContext(original.context.query, original.context.evidence,
                            original.context.rendered_text, original.context.truncated)
    prepared = PreparedRecall(context, original._rendered_blocks, original._dependencies)
    assert isinstance(prepared.subset(("fact",)), PublicContext)
    assert asdict(prepared.subset(("fact",))) == asdict(original.context)
