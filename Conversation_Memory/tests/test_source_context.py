from dataclasses import asdict, replace
from types import SimpleNamespace
import pytest
from Conversation_Memory.adapter.models import BackendCandidate, EntityMentionContext, RecallPolicy
from Conversation_Memory.adapter.source_memory import _source_rows
from Conversation_Memory.adapter.source_context import render_context
from Conversation_Memory.tests.test_source_memory import SourceBackend, adapter, segment

class ContextBackend(SourceBackend):
    def source_parent(self, anchor, *, limit, known_candidates, max_nodes):
        session = anchor.metadata["provenance"]["conversation_id"]
        group = [c for c in self.rows.values() if c.metadata["provenance"]["conversation_id"] == session]
        extra = [c for c in group if c.metadata["evidence_id"] not in known_candidates]
        if len(group) > limit or len(known_candidates) + len(extra) > max_nodes:
            return []
        known_candidates.update((c.metadata["evidence_id"], c) for c in extra)
        return group

    def source_locate(self, refs, *, limit, known_candidates, max_nodes, max_refs=None):
        result = []
        for ref in refs:
            for c in self.rows.values():
                p = c.metadata["provenance"]
                if p["turn_id"] == ref["turn_id"] and p["segment_id"] == ref["segment_id"]:
                    if c not in result and len(result) < limit and (
                        c.metadata["evidence_id"] in known_candidates or len(known_candidates) < max_nodes):
                        result.append(c); known_candidates[c.metadata["evidence_id"]] = c
        return result

def memory(tmp_path, rows, *, window=1000):
    backend = ContextBackend(window=window)
    obj = adapter(tmp_path, backend)
    for sid, texts in rows:
        assert obj.ingest_sources(segment(texts, sid)).status == "completed"
    backend.anchor_ids = [next(iter(backend.rows))]
    return obj

def policy(**kwargs):
    return replace(RecallPolicy(top_k=10, max_nodes=80, max_evidence_items=80,
        max_chars=5000, max_graph_depth=1), **kwargs)

def test_parent_restores_later_user_response_without_fact_formation(tmp_path):
    m = memory(tmp_path, [("a", [("assistant","The output might be aligned."),
        ("user","I need another check."), ("assistant","We can wait."),
        ("user","I checked the guide; the output is still offset.")])])
    before = dict(m.backend.rows)
    ctx = m.recall_source_context("Is the output aligned?", policy())
    assert ctx.safe_error_code is None
    assert len(ctx.evidence) == 4
    assert "still offset" in ctx.rendered_text
    assert "[USER " in ctx.rendered_text and "[LUMINA " in ctx.rendered_text
    assert m.backend.rows == before and m.backend.fact_reads == 0

def test_navigation_locates_original_not_wrong_binding_or_derived_fact(tmp_path):
    m = memory(tmp_path, [("a",[("user","The desk is narrow.")]),
                         ("b",[("user","The blue sample remains on hold.")])])
    source = list(m.backend.rows.values())[1]
    p = source.metadata["provenance"]
    fact = BackendCandidate("Invented state: released by Professor Ren",None,None,
        {"provenance":p,"source_refs":[{"turn_id":p["turn_id"],"supporting_span":source.text,
         "source_start":0,"source_end":len(source.text)}],"subject_entity_ref":"wrong-binding"})
    backend = SimpleNamespace(resolve_target_entity_refs=lambda q,limit:("wrong-binding",),
                              recall=lambda *a,**k:[fact])
    nav = SimpleNamespace(backend=backend,recall_mentions=lambda *a,**k:EntityMentionContext("q"))
    ctx = m.recall_source_context("What is the sample state?",policy(),fact_memory=nav)
    assert "remains on hold" in ctx.rendered_text
    assert "Professor" not in ctx.rendered_text and "wrong-binding" not in ctx.rendered_text
    off = m.recall_source_context("What is the sample state?",policy())
    assert "remains on hold" not in off.rendered_text

def test_broken_navigation_keeps_independent_source(tmp_path):
    m = memory(tmp_path,[("a",[("user","The cover is open.")])])
    broken = SimpleNamespace(backend=object())
    ctx = m.recall_source_context("cover?",policy(),fact_memory=broken)
    assert ctx.safe_error_code is None and "cover is open" in ctx.rendered_text
    assert len(m.last_source_context_read["navigation"]["errors"]) == 2

def test_packing_omits_whole_parent_and_counts_headers(tmp_path):
    m = memory(tmp_path,[("a",[("user","One."),("assistant","Two.")])])
    complete = m.recall_source_context("q",policy())
    cut = m.recall_source_context("q",policy(max_chars=len(complete.rendered_text)-1))
    assert cut.truncated and cut.evidence == () and cut.rendered_text == ""

def test_dereference_budget_never_returns_partial_parent(tmp_path):
    m = memory(tmp_path,[("a",[("user","One."),("assistant","Two."),("user","Three.")])])
    ctx = m.recall_source_context("q",policy(max_nodes=2))
    assert ctx.truncated and not ctx.evidence
    assert len(m.last_source_context_read["candidates"]) == 1

def test_lossless_turn_merge_and_incomplete_tail_rejected():
    raw = segment([("user","a  b\n c  d")])
    rows = _source_rows(raw,lambda text:len(text)<=3)
    candidates = [BackendCandidate(r["text"],r["timestamp"].isoformat(),1,r["metadata"]) for r in rows]
    rendered = render_context(candidates)
    assert rendered.count("[USER ") == 1
    assert "a  b\n c  d" in rendered
    with pytest.raises(ValueError,match="incomplete_turn"):
        render_context(candidates[:-1])

def test_duplicate_local_turn_numbers_do_not_merge_distinct_segments():
    a = _source_rows(segment([("user","First.")],"a"),lambda t:True)[0]
    b = _source_rows(segment([("assistant","Second.")],"b"),lambda t:True)[0]
    b["metadata"]["provenance"]["conversation_id"] = a["metadata"]["provenance"]["conversation_id"]
    rows = [BackendCandidate(r["text"],r["timestamp"].isoformat(),1,r["metadata"]) for r in (a,b)]
    text = render_context(rows)
    assert "[USER " in text and "[LUMINA " in text and "First." in text and "Second." in text

def test_trace_cleared_on_invalid_query(tmp_path):
    m = memory(tmp_path,[("a",[("user","A fact.")])])
    assert m.recall_source_context("q",policy()).evidence
    assert m.recall_source_context(" ",policy()).safe_error_code == "invalid_query"
    assert m.last_source_context_read == {}
