# Explicit source representation prototype

This opt-in Memory interface leaves production v2, Formation, Cold consumption
and existing checkpoints unchanged. Local experimental reports are separate
artifacts, not runtime dependencies or evidence of production readiness.

## Source ownership and persistence

Call `MagmaMemoryAdapter.ingest_sources(segment)` on a separate empty MAGMA
namespace using the existing adapter and IngestionStateStore owners. The
bounded pending ColdDraftSegment supplies immutable text, IDs, order, roles and
times. The source checkpoint uses `source-window-v1`, independently of
Formation. Its completion neither consumes Cold nor completes fact extraction.
An unchanged checkpoint can repair an interrupted source graph/vector write;
a changed source or partition fails visibly. No consumed-Cold migration occurs.

Pinned MAGMA's NARRATIVE node is only a carrier, marked
`memory_kind=source-window-v1`. Source and fact namespaces stay separate:
source methods reject EVENT stores and ordinary Recall rejects source stores.
No inferred fact, identity, causal or temporal links are created by co-occurrence.

The actual MiniLM window and the fixed BGE tokenizer's 256-token source
allowance partition each turn into exact non-overlapping ranges. Whitespace and
long-turn tails remain eligible. Stable IDs and checkpoint fingerprints preserve
this partition; there is no automatic repartitioning.

Source text, range, segment, conversation and lexical views rebuild on load and
update incrementally on owner writes. A retained FAISS bitmap grows geometrically.
These are disposable views of the same graph, not a third authority. Reads
check their owner signature and never repair persistent state.

## Explicit read interfaces

`recall_sources(query, policy)` retains the original S algorithm: dense/lexical
RRF(k=60) anchors, up to two neighboring source blocks each side within a segment,
fixed BGE, then existing Hindsight composition and whole-group packing. BGE uses
the complete neighborhood when the pair fits; otherwise the complete anchor.
A still-oversized pair fails softly. Exact source ranges are deduplicated.

`recall_source_context(query, policy, fact_memory=None)` adds a separate
experimental organization of source context:

1. Independently search source text with at most 20 search candidates and the
   caller's top_k. Failed Formation does not remove source eligibility.
2. If a fact adapter is explicitly supplied, its bounded fact/entity search and
   occurrence lookup each admit at most 20 results. Alternate their exact source
   references, checking at most 80 refs and returning at most 20 located blocks.
   The source owner verifies original range, literal text, role, time and scope.
   Derived facts and entity bindings are navigation only and are never presented
   as Answer evidence by this interface.
3. Fuse independent and navigated source ranks with RRF(k=60), retaining top_k
   anchors. Fixed BGE scores each complete original anchor, without a parent
   fallback or silent truncation; Hindsight retains its existing formula.
4. In anchor score order, read each distinct complete indexed conversation
   parent once. Parent completeness means all currently indexed blocks and full
   turns of its segments are present. It does not mean a closed topic, a complete
   lifetime history or that no unseen later source exists.
5. Merge contiguous blocks into exact complete turns, retaining segment, turn
   position, role, original time and timezone. The session header is shared.
   Pack complete parents within one total character/item budget; omit an
   oversized parent whole rather than silently clipping a confirmation.

Cold currently does not persist a real conversation ID. Its converter normally
uses `cold-draft:<segment_id>`, so a natural conversation split across Cold
segments is not automatically reunited. Where a truthful shared conversation ID
already exists in the Memory DTO, segment-first speaking time orders segments
and original block order orders turns. Neither names nor inferred identities
join sessions.

`policy.max_nodes` bounds the unique source blocks projected by independent
anchors, reference validation and parent dereferences together. Source search
and fact search are additional bounded index work; max_nodes is not their total
cost. The experimental comparison uses top_k=10, max_nodes=80 and
max_evidence_items=80 with max_chars=5000 for this interface; S uses 20/20 under
the same 5000-character ceiling. These are explicit experimental arguments,
not new default Recall parameters. All body text, headers and separators count.

Omitting fact_memory disables all Formation-derived navigation and keeps source
search, grouping, scoring and presentation unchanged. It does not receive extra
search capacity. Navigation failures retain independent source reading.

Both reads return typed SourceMemoryContext/SourceExcerpt values. They identify
what was said, not whether every statement is true. Answer still interprets
acceptance, conditions, corrections, roles and later status using source text.
No selector, query rewrite, confirmation vocabulary or topic/identity rule is
introduced. No production Chat wiring or autonomous ingestion is added.

## Validation and diagnostics

Focused mechanism tests live in `tests/test_source_memory.py`,
`tests/test_source_context.py` and `tests/test_source_backend_views.py`.
The last file exercises the real FAISS selector in the Memory environment.
Existing isolated real-MAGMA Recall acceptance remains required.

Private last-read traces retain actual anchors, navigation references, projected
source blocks, BGE views and parent omissions. Backend counters distinguish
index load/update, reference checks, position lookup, projected nodes and
selector work. They are local diagnostics and must not be exposed as public
runtime bodies. Test success alone does not establish comprehension or that the
navigation's full Formation and retrieval cost is worthwhile.
