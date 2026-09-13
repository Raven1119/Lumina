"""Opt-in source display through the public adapter; controlled candidates/logits."""
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import BackendCandidate, MemoryEvidence, RecallPolicy, SourceProvenance
from ingestion.state_store import IngestionStateStore
from recall.rendering import bound_evidence_groups


def candidate(eid, text, *, subject=None, obj=None, role="user", **metadata):
    return BackendCandidate(text, "2040-01-01T00:00:00+00:00", 1.0, {
        "evidence_id": eid, "subject_entity_ref": subject, "object_entity_ref": obj,
        "provenance": asdict(SourceProvenance(
            "source-segment", "source-conversation", eid, role,
            "2026-06-03T14:15:00+02:00", "Europe/Paris", "grounded-formation-v2", "client",
        )), **metadata,
    })


class Backend:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.last_recall_stats = {"budget_exhausted": False}

    def resolve_target_entity_refs(self, query, *, limit):
        return ()

    def recall(self, query, policy):
        self.calls.append((query, policy))
        return self.rows


class Reranker:
    def __init__(self, scores=None):
        self.calls = []
        self.scores = scores or {}

    def fits_pair(self, query, text):
        return True

    def score(self, query, texts):
        self.calls.append((query, tuple(texts)))
        return tuple(self.scores.get(text, 1.0) for text in texts)


def adapter(tmp_path, rows, *, scores=None):
    backend, reranker = Backend(rows), Reranker(scores)
    memory = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "unused.json"))
    memory._bge_reranker = reranker
    memory._bge_reranker_load_attempted = True
    return memory, backend, reranker


def headers(context):
    lines = context.rendered_text.splitlines()
    return {item.evidence_id: lines[index * 2] for index, item in enumerate(context.evidence)}


@pytest.mark.parametrize("bad", [None, 0, 1, "false", []])
def test_source_context_policy_requires_actual_bool(bad):
    with pytest.raises(ValueError, match="include_source_context must be a bool"):
        RecallPolicy(include_source_context=bad)


@pytest.mark.parametrize("enabled", [False, True])
def test_source_context_policy_accepts_bool(enabled):
    assert RecallPolicy(include_source_context=enabled).include_source_context is enabled


def test_default_display_is_exact_and_rich_mode_changes_no_query_score_or_evidence(tmp_path):
    rows = [candidate("f1", "Mira was a nurse in 2020.", subject="PRIVATE_NURSE_REF"),
            candidate("f2", "I guessed Mira used copper; this was not confirmed.", role="assistant")]
    before = deepcopy(rows)
    memory, backend, scorer = adapter(tmp_path, rows)
    policy = RecallPolicy(max_chars=5000)
    plain = memory.recall("What did Mira do in 2020?", policy)
    explicit_plain = memory.recall(plain.query, replace(policy, include_source_context=False))
    rich = memory.recall(plain.query, replace(policy, include_source_context=True))
    assert plain == explicit_plain
    assert plain.rendered_text == ("[USER]\nMira was a nurse in 2020.\n"
                                   "[LUMINA]\nI guessed Mira used copper; this was not confirmed.")
    assert plain.evidence == rich.evidence
    assert len(backend.calls) == 3 and len(set(scorer.calls)) == 1
    assert all(query == plain.query for query, _ in backend.calls)
    assert rows == before
    assert not (tmp_path / "unused.json").exists()
    assert "PRIVATE_NURSE_REF" not in rich.rendered_text
    assert 'spoken_at="2026-06-03T14:15:00+02:00"' in rich.rendered_text
    assert 'timezone="Europe/Paris"' in rich.rendered_text
    assert "2040" not in rich.rendered_text
    assert rich.rendered_text.count("nurse") == 1
    assert "was not confirmed" in rich.rendered_text
    assert headers(rich)["f2"].startswith("[LUMINA |")


def test_same_name_occupations_and_subject_object_joins_use_only_existing_bindings(tmp_path):
    rows = [candidate("nurse", "Mira is a nurse.", subject="PRIVATE_ALPHA"),
            candidate("study", "Mira studies basalt.", subject="PRIVATE_ALPHA", obj="PRIVATE_BASALT"),
            candidate("pilot", "Mira is a pilot.", subject="PRIVATE_BETA"),
            candidate("sample", "Mira owns basalt.", subject="PRIVATE_BETA", obj="PRIVATE_BASALT")]
    memory, _, _ = adapter(tmp_path, rows)
    context = memory.recall("What does Mira study?", RecallPolicy(include_source_context=True))
    h = headers(context)
    assert "subject_binding=I1" in h["nurse"] and "subject_binding=I1" in h["study"]
    assert "subject_binding=I3" in h["pilot"] and "subject_binding=I3" in h["sample"]
    assert "object_binding=I2" in h["study"] and "object_binding=I2" in h["sample"]
    assert all(ref not in context.rendered_text for ref in ("PRIVATE_ALPHA", "PRIVATE_BETA", "PRIVATE_BASALT"))
    assert all(item.text == row.text for item, row in zip(context.evidence, rows))
    assert context.rendered_text.count("nurse") == context.rendered_text.count("pilot") == 1


def test_unresolved_and_ordinary_mentions_cannot_create_roles_or_occupations(tmp_path):
    row = candidate("unknown", "Mira studies basalt.", subject=None, obj=None,
                    mention_entity_refs=["PRIVATE_OTHER"], entities=["Mira the pilot"],
                    canonical_surface="Mira the nurse",
                    identity_source_refs=[{"supporting_span": "Mira is a surgeon."}])
    memory, _, _ = adapter(tmp_path, [row])
    context = memory.recall("What is Mira's occupation?", RecallPolicy(include_source_context=True))
    assert context.evidence[0].text == row.text
    assert "_binding=" not in context.rendered_text
    assert all(word not in context.rendered_text for word in ("PRIVATE_OTHER", "pilot", "nurse", "surgeon"))


@pytest.mark.parametrize("invalid_ref", ["", "   ", 2, ["PRIVATE_LIST"]])
def test_malformed_role_metadata_is_not_rendered_as_identity(tmp_path, invalid_ref):
    memory, _, _ = adapter(tmp_path, [candidate("f", "Mira studies basalt.", subject=invalid_ref)])
    context = memory.recall("Mira", RecallPolicy(include_source_context=True))
    assert context.safe_error_code is None and len(context.evidence) == 1
    assert "_binding=" not in context.rendered_text


def test_invalid_source_cannot_assign_a_role_label_to_valid_evidence(tmp_path):
    bad = candidate("bad", "Mira is an astronaut.", subject="PRIVATE_BAD")
    bad.metadata["provenance"]["source_role"] = "system"
    good = candidate("good", "Mira studies basalt.", subject="PRIVATE_GOOD")
    memory, _, _ = adapter(tmp_path, [bad, good])
    context = memory.recall("Mira", RecallPolicy(include_source_context=True))
    assert [e.evidence_id for e in context.evidence] == ["good"]
    assert "subject_binding=I1" in context.rendered_text
    assert "astronaut" not in context.rendered_text


@pytest.mark.parametrize("timestamp", [None, "", "not-a-time", "2026-06-03T14:15:00"])
def test_unknown_source_time_is_explicit_without_inventing_current_time(timestamp):
    evidence = MemoryEvidence("f", "Mira used basalt in 2020.", "2040-01-01T00:00:00+00:00",
                              SourceProvenance("s", "c", "t", "user", timestamp, "", "legacy"))
    selected, text, truncated = bound_evidence_groups([[evidence]], count=1, max_chars=5000,
                                                     source_context_roles={})
    assert selected == (evidence,) and not truncated
    assert text == '[USER | spoken_at="unknown" | timezone="unknown"]\nMira used basalt in 2020.'


def test_rich_group_budget_counts_all_headers_and_preserves_complete_chain(tmp_path):
    bridge = candidate("bridge", "Mira uses Station Seven.", subject="PRIVATE_MIRA", obj="PRIVATE_STATION")
    endpoint = candidate("endpoint", "Station Seven does not heat basalt.", subject="PRIVATE_STATION",
                         association_chain_evidence_ids=["bridge", "endpoint"],
                         association_bridge_evidence_ids=["bridge"])
    independent = candidate("independent", "Pip reads.", subject="PRIVATE_PIP")
    scores = {bridge.text: -20.0, bridge.text + "\n" + endpoint.text: 20.0, independent.text: 0.0}
    memory, _, _ = adapter(tmp_path, [bridge, endpoint, independent], scores=scores)
    policy = RecallPolicy(include_source_context=True, max_chars=5000, final_min_score=0.05)
    whole = memory.recall("What does the station do?", policy)
    assert [e.evidence_id for e in whole.evidence] == ["bridge", "endpoint", "independent"]
    pair_text = "\n".join(whole.rendered_text.splitlines()[:4])
    exact = memory.recall(whole.query, replace(policy, max_chars=len(pair_text)))
    assert [e.evidence_id for e in exact.evidence] == ["bridge", "endpoint"]
    assert exact.rendered_text == pair_text and exact.truncated
    below = memory.recall(whole.query, replace(policy, max_chars=len(pair_text) - 1))
    one_item = memory.recall(whole.query, replace(policy, max_evidence_items=1))
    for context in (below, one_item):
        assert [e.evidence_id for e in context.evidence] == ["independent"]
        assert context.truncated and endpoint.text not in context.rendered_text
        assert bridge.text not in context.rendered_text
    h = headers(whole)
    assert "object_binding=I2" in h["bridge"] and "subject_binding=I2" in h["endpoint"]
    assert "does not heat" in whole.rendered_text


def test_rich_annotation_overhead_can_omit_a_fact_without_changing_default_fit(tmp_path):
    memory, _, _ = adapter(tmp_path, [candidate("f", "Mira reads.", subject="PRIVATE_MIRA")])
    plain = memory.recall("Mira", RecallPolicy())
    budget = len(plain.rendered_text)
    same = memory.recall("Mira", RecallPolicy(max_chars=budget))
    rich = memory.recall("Mira", RecallPolicy(max_chars=budget, include_source_context=True))
    assert same == plain
    assert not rich.evidence and rich.rendered_text == "" and rich.truncated
