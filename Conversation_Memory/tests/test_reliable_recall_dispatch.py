"""Reliable profiles serve the ordinary Recall facade; legacy stays untouched.

Synthetic activation only: no provider, neural load, app, or real MAGMA.
"""
from types import SimpleNamespace

import pytest

from Conversation_Memory.adapter._associative_recall import Activation
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import (
    BackendCandidate,
    MemoryContext,
    RecallPolicy,
    SourceExcerpt,
    SourceMemoryContext,
    SourceProvenance,
)
from Conversation_Memory.ingestion.state_store import IngestionStateStore


def candidate(eid, text, *, refs=None):
    provenance = dict(
        segment_id="s", conversation_id="cold-draft:s", turn_id=eid,
        source_role="user", source_timestamp="2026-09-17T00:00:00+00:00",
        source_timezone="UTC", ingestion_version="grounded-formation-v6",
        timezone_source="client",
    )
    metadata = dict(
        evidence_id=eid, provenance=provenance,
        source_start=0, source_end=len(text),
    )
    if refs is not None:
        metadata["source_refs"] = refs
    return BackendCandidate(text, provenance["source_timestamp"], None, metadata)


def source(eid, text, *, turn_id, index=0):
    provenance = SourceProvenance(
        **{**candidate(turn_id, text).metadata["provenance"], "turn_id": turn_id}
    )
    return SourceExcerpt(eid, text, provenance, index, 0, len(text), len(text))


def ref(item):
    provenance = item.provenance
    return dict(
        turn_id=provenance.turn_id, source_start=item.source_start,
        source_end=item.source_end, supporting_span=item.text,
        source_role=provenance.source_role,
        source_timestamp=provenance.source_timestamp,
        source_timezone=provenance.source_timezone,
        timezone_source=provenance.timezone_source,
    )


class Cold:
    def __init__(self, *items):
        self.items = items
        self.calls = []

    def read_source_refs(self, refs, **kwargs):
        self.calls.append((refs, kwargs))
        return SourceMemoryContext(
            kwargs["query"], self.items,
            "\n".join(item.text for item in self.items),
        )


def reliable_adapter(tmp_path, rows, seeds, *, cold=None, profile="reliable-v2"):
    adapter = MagmaMemoryAdapter(
        SimpleNamespace(),
        IngestionStateStore(tmp_path / "state"),
        ingestion_version="grounded-formation-v6",
        first_hit=FirstHitPolicy(),
        cold_store=cold,
        associative_read_profile=profile,
    )
    adapter._activate_first_hit = lambda cue: Activation(
        tuple(rows), diagnostics={}, seed_fact_ids=tuple(seeds),
    )

    def no_legacy_recall(*args, **kwargs):
        raise AssertionError("reliable profile must not run legacy Recall")

    adapter._recall = no_legacy_recall
    return adapter


def test_recall_dispatches_combined_render_and_fact_evidence(tmp_path):
    raw = source("s1", "I accepted only that one visit.", turn_id="t1")
    fact = candidate("f", "User accepted only that one visit.", refs=[ref(raw)])
    cold = Cold(raw)
    adapter = reliable_adapter(tmp_path, [(fact, 1.0, 1.0)], ("f",), cold=cold)

    context = adapter.recall("cue", RecallPolicy(max_chars=1000, max_evidence_items=1))

    assert type(context) is MemoryContext
    assert [item.evidence_id for item in context.evidence] == ["f"]
    assert fact.text in context.rendered_text
    assert raw.text in context.rendered_text
    assert context.safe_error_code is None
    assert cold.calls, "dispatch must request the bounded source supplement"


def test_recall_dispatch_surfaces_source_degradation_without_dropping_facts(tmp_path):
    fact = candidate("f", "User accepted only that one visit.")
    adapter = reliable_adapter(tmp_path, [(fact, 1.0, 1.0)], ("f",))

    context = adapter.recall("cue", RecallPolicy(max_chars=1000))

    assert [item.evidence_id for item in context.evidence] == ["f"]
    assert fact.text in context.rendered_text
    assert context.safe_error_code == "cold_source_unavailable"


def test_recall_dispatch_keeps_score_floor_an_explicit_conflict(tmp_path):
    adapter = reliable_adapter(tmp_path, [], ())

    context = adapter.recall("cue", RecallPolicy(final_min_score=0.144))

    assert context.evidence == ()
    assert context.rendered_text == ""
    assert context.safe_error_code == "first_hit_score_policy_conflict"


def test_prepare_recall_dispatch_preserves_selectable_fact_blocks(tmp_path):
    fact = candidate("f", "User accepted only that one visit.")
    adapter = reliable_adapter(tmp_path, [(fact, 1.0, 1.0)], ("f",))

    prepared = adapter.prepare_recall("cue", RecallPolicy(max_chars=1000))

    assert fact.text in prepared.context.rendered_text
    assert prepared.selection_items == (("f", prepared.context.rendered_text),)
    assert prepared.subset(("f",)).rendered_text == prepared.context.rendered_text
    assert prepared.subset(()).rendered_text == ""


@pytest.mark.parametrize("profile", ["reliable-v1", "reliable-v2"])
def test_prepare_recall_keeps_exact_source_supplement_in_one_block(tmp_path, profile):
    raw = source("s1", "I accepted only that one visit.", turn_id="t1")
    fact = candidate("f", "User accepted only that one visit.", refs=[ref(raw)])
    adapter = reliable_adapter(tmp_path, [(fact, 1.0, 1.0)], ("f",), cold=Cold(raw), profile=profile)

    prepared = adapter.prepare_recall("cue", RecallPolicy(max_chars=1000, max_evidence_items=1))

    assert prepared.selection_items == (("f", prepared.context.rendered_text),)
    assert prepared.subset(("f",)).rendered_text == prepared.context.rendered_text
    assert prepared.subset(()).rendered_text == ""


@pytest.mark.parametrize("configure_first_hit", [True, False])
def test_legacy_profiles_keep_the_original_recall_path(tmp_path, configure_first_hit):
    kwargs = {}
    if configure_first_hit:
        kwargs.update(
            ingestion_version="grounded-formation-v6",
            first_hit=FirstHitPolicy(),
        )
    adapter = MagmaMemoryAdapter(
        SimpleNamespace(),
        IngestionStateStore(tmp_path / "state"),
        associative_read_profile="first-hit-v1",
        **kwargs,
    )

    def no_activation(*args, **kwargs):
        raise AssertionError("legacy profile must not run first-hit activation")

    adapter._activate_first_hit = no_activation
    sentinel = MemoryContext("cue")
    calls = []

    def legacy_recall(query, policy, **_kwargs):
        calls.append((query, policy))
        return sentinel

    adapter._recall = legacy_recall
    policy = RecallPolicy()

    assert adapter.recall("cue", policy) is sentinel
    assert calls == [("cue", policy)]
    prepared = adapter.prepare_recall("cue", policy)
    assert prepared.context is sentinel
