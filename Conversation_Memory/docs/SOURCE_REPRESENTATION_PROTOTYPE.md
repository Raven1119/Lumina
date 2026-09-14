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

These two reads return typed SourceMemoryContext/SourceExcerpt values. They identify
what was said, not whether every statement is true. Answer still interprets
acceptance, conditions, corrections, roles and later status using source text.
These interfaces introduce no selector, query rewrite, confirmation vocabulary
or topic/identity rule. No production Chat wiring or autonomous ingestion is added.

## Explicit question-directed range acquisition

Source lexical ranking now scores overlap using the same deduplicated features
that populated its postings. This repairs cases where Chinese postings matched
but whitespace-token scoring discarded the result. Only source search enables
this option; the fact Recall scorer and default v2 remain unchanged. Both
source-context and range reads share this repair.

`open_source_reader(question, SourceReadLimits(...))` creates a transient reader
over the existing source adapter. Its `search(query)` uses source dense/lexical
retrieval, fixed BGE over complete anchors and existing Hindsight composition.
It returns bounded literal snippets, original positions and known segment bounds.
Its `read(segment_id, start_turn, end_turn, start_char=0, end_char=None)` reads
inclusive original turn positions, optionally restricting the first/last turn.
A partial result reports exact character offsets and a continuation cursor.
An unknown full segment count stays unknown; indexed turn count and coordinate
extent are separate. Missing indexed ranges cannot be joined into a complete
source claim. A Cold segment is not a topic or a whole natural conversation.

The source owner resolves ranges from its existing indexes. Cached nodes avoid
repeat graph projection; identical searches avoid repeat embedding/BGE work.
The reader unions overlapping or adjacent literal ranges only when their source
metadata and overlap agree. It never bridges an unseen gap. Every exposed range
remains in its final `SourceMemoryContext`; generated queries and model prose
never enter that context. The current prototype cannot evict a poor early read,
so irrelevant snippets can consume space needed by later evidence. That is a
prototype tradeoff, not a requirement for every future acquisition design.

`core.evidence_acquisition.acquire_sources` exposes only `search_sources` and
`read_sources` to one configured model. The caller supplies current near context,
the original question and the common system background. The model can stop,
search with a natural query, read a range or continue from newly found evidence.
There is no required tool sequence or whole-parent rule. This explicit function
has no production Chat wiring, selector, verifier, fact navigation, Formation,
organ executor or persistent reader state.

The model client's `exchange_request` previews the exact native body;
`exchange` sends one request and retains native text/tool blocks and usage.
The acquisition function owns only bounded continuation. It stops on malformed
responses, repeated tool IDs, model/tool/serialization failures or exhausted
limits, without retrying. The result includes its stop reason and any literal
context acquired before failure. The caller then uses an ordinary, fresh Answer
call with the original question, near context and this literal context. Reader
strategy, draft answers and end text are not forwarded as historical evidence.
The additional generic provenance responsibilities in acquisition are reader
instructions; they do not change the ordinary final Answer prompt.

Default explicit reader limits are 3 searches, 6 range reads, 200 node projections,
10 scored candidates per search, 5 snippets of at most 120 source characters,
2000 source characters per range read and 5000 rendered final-context characters.
All headers and separators count. Acquisition separately allows 4 model calls
and 100000 cumulative native request characters; model output retains the
configured client limit. The final Answer is an additional call. These limits
belong to this opt-in prototype, not production Recall defaults. A source-owner
preflight rejection that certifies zero node projections releases only its node
reservation and still consumes a read; unknown or partially projected failures
retain their reservations.
Tool batches have no additional implicit call-count threshold; they obey the reader's cumulative operation quotas.

Diagnostic records distinguish dense/lexical work, range projections, cache
hits, BGE pairs, and exact native request character/UTF-8 byte totals. Full cost
also includes index construction/restart, embeddings, model inputs/outputs and
repeated presentation of source text in native messages. A 5000-character final
context does not imply that total model input was 5000 characters. Full traces
are local diagnostics and can contain source text; do not expose them as public
runtime responses or include them in source-only deliveries.

## Explicit cue-directed experience views

`recall_experiences(cue, policy) -> ExperienceContext` organizes the exact evidence
selected by `recall_sources`. It reuses that method's source search, fixed BGE,
Hindsight composition, caller floor and packing without changing any of them.
It adds no search, graph projection, tokenizer check or model call. Ordinary fact
Recall and all existing defaults are unchanged. There is no generated description,
query rewrite, fact navigation, new index, persistent state or consumer wiring.

The view merges overlapping or adjacent literal character ranges only when their
provenance and overlap agree, and restores original order within each segment.
Different segments, missing turns and unread character gaps keep separate views.
Names never join identities or sessions. Original role, time, source labels and
range headers remain visible through the existing source renderer. All original
selected characters are retained; merging repeated headers can reduce rendered
size, which is checked against both the original selection and max_chars. The
source policy still bounds the original selected blocks, before ranges merge.
This is presentation of observed dialogue, not an inferred episode summary.

Every `SourceExperience` contains literal `SourceExcerpt` evidence, its rendered
dialogue, and a `SourceRangeReference`. A reference identifies the full indexed
Cold segment bounds when known, otherwise only the observed turn/character range.
It does not identify a complete natural conversation. `truncated` on a view means
that the view is not a complete known original segment; the context also retains
the original read's truncation state. Source errors propagate; a view-organization
failure reports `experience_view_unavailable`. Neither removes original sources
from independent search or range access.

Expand a returned reference through the existing bounded source reader:

```python
from dataclasses import asdict
from adapter.models import RecallPolicy
from adapter.source_reader import SourceReadLimits

context = memory.recall_experiences(cue, RecallPolicy(max_chars=5000))
if context.experiences:
    reader = memory.open_source_reader(cue, SourceReadLimits())
    page = reader.read(**asdict(context.experiences[0].reference))
    # A non-null page["next_range"] is an exact continuation position.
    # Continue explicitly within reader.remaining(); no model is called here.
```

Expansion is an additional read with its own cumulative budget. The caller must
budget the initial view and any expansion it presents to a consumer. No hidden
expansion or complete-parent requirement is introduced. A valid reference proves
where the text comes from, not relevance, acceptance, correct identity or whether
an old permission still applies. Understanding remains the caller's responsibility.

The last-read diagnostic records the reused source read and final source/rendered
character counts. Source search, neighbor projection, local embedding/BGE and
index construction/restart still cost work. Zero generation is not zero cost.
These private diagnostics may contain source text and are not public API bodies.

## Validation and diagnostics

Focused mechanism tests live in `tests/test_source_memory.py`,
`tests/test_source_context.py` and `tests/test_source_backend_views.py`.
The last file exercises the real FAISS selector in the Memory environment.
`test_source_lexical.py`, `test_source_range_backend.py` and
`test_source_reader.py` cover the common lexical fix and bounded range interface.
`test_source_experiences.py` covers cue views, positional gaps, exact budgets
and expansion references without generated calls.
Root `tests/test_evidence_acquisition.py` and `tests/test_model_client.py` cover
native protocol, failure, budget and source isolation boundaries.
Existing isolated real-MAGMA Recall acceptance remains required.

Private last-read traces retain actual anchors, navigation references, projected
source blocks, BGE views and parent omissions. Backend counters distinguish
index load/update, reference checks, position lookup, projected nodes and
selector work. They are local diagnostics and must not be exposed as public
runtime bodies. Test success alone does not establish comprehension or that the
navigation's full Formation and retrieval cost is worthwhile.
