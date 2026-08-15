# Current Status

## Complete

### Chat and Draft continuity

- same-origin FastAPI browser chat with `/api/status` and `/api/chat`;
- deterministic mock mode and explicit MiniMax Anthropic-compatible real-model
  mode;
- safe provider fallback;
- append-only restart-persistent Hot Draft source storage;
- Draft Turn Provenance V2 with stable per-turn IDs, distinct aware UTC
  timestamps, validated IANA source timezone, and truthful timezone source;
- pair-aware Cold-first logical compaction;
- immutable Cold source records with owner-controlled `pending_digest ->
  consumed` state;
- restart recovery and idempotent Cold-first compaction behavior.

### Dream and memory write path

- manual, synchronous, serial, bounded Dream;
- no startup/background/chat-time Dream;
- configured real-model Dream uses one bounded `grounded-formation-v1`
  MiniMax-M3 call in non-thinking mode with a Formation-only 2000-token output
  budget per newly seen Cold segment;
- minimal atomic `GroundedMemoryUnit` values carry source-grounded
  subject/relation/value and exact source refs;
- deterministic validation plus the bounded semantic fallback and value-only
  guard rejects ungrounded details, ambiguous spans, epistemic inversion, and
  unauthorized assistant assertions;
- mock/legacy ingestion retains deterministic `grounded-span-v2` projection;
- stable grounded unit IDs derived from canonical unit content, exact source
  refs, optional referenced time, and Formation version;
- pinned, unmodified upstream MAGMA;
- durable `(segment_id, ingestion_version)` checkpoints;
- formed units are checkpointed before MAGMA writes and reused without another
  Formation call after a downstream write failure;
- memory persistence/checkpoint success before Cold consume;
- retry convergence without duplicate logical memory;
- app Dream and memory adapter reuse the same owner/backend boundaries.
- structured SRV is persisted as private MAGMA metadata; production Recall
  admission and ranking remain unchanged.

### Production Recall and Answer injection

- Recall is wired into production chat and enabled by source default;
- `LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED=false` explicitly disables it;
- current user text is the Recall query;
- MAGMA TRG keyword-enriched dense anchors plus bounded lexical anchors;
- deterministic two-list RRF fusion;
- fixed production graph traversal at `max_graph_depth=1`, `max_nodes=20`;
- fail-open controlled relation compatibility when a structured caller supplies
  relation surfaces; normal Chat supplies none and keeps existing behavior;
- fixed `BAAI/bge-reranker-v2-m3`, revision
  `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`;
- BGE loads lazily only for non-empty candidate Recall and is reused within the
  adapter;
- BGE failure or invalid output returns safe `recall_unavailable` rather than
  silently falling back to raw MAGMA order;
- Hindsight-style post-rerank scoring uses a deterministic persisted-state time
  reference rather than process wall clock;
- production `final_min_score=0.144`, inclusive `>=`;
- stable final ordering and bounded `max_evidence_items=3`, `max_chars=5000`;
- `MemoryContext.rendered_text` is injected into a fixed internal historical
  evidence block in the system prompt when non-empty;
- empty/unavailable Recall does not block normal chat;
- BGE/backend scores, embeddings, graph objects, MAGMA IDs, paths, credentials,
  provider bodies, tracebacks, and raw Draft records are not injected.

### Validation and audit

Latest reported local validation:

```text
focused memory regressions: 134 passed, 5 skipped
root tests:                 184 passed, 24 skipped
Conversation_Memory tests: 163 passed, 5 skipped
Dream tests:                36 passed, 1 skipped
real MAGMA Recall E2E:      PASS (10 / 10 queries)
production depth-1 required evidence: 11 / 11
restart Recall:             PASS
idempotency:                PASS
git diff --check:           PASS
pinned MAGMA changed by consolidation: NO
pinned MAGMA worktree:      clean at 467cb70b67ac337b22fdb42194d37c04ad701b62
```

A local source audit of pinned MAGMA established:

```text
MAGMA_NATIVE_RECALL: PARTIAL
MAGMA_ADAPTIVE_QUERY_POLICY: NOT_USED
MAGMA_FOUR_GRAPH_TRAVERSAL: PARTIALLY_USED
```

Lumina reuses MAGMA TRG/storage/vector/graph primitives and generic traversal,
but production does not call upstream `QueryEngine.query()`.

## Partial / Current Quality Gaps

### Recall quality

The current `final_min_score=0.144` is development-calibrated, not a blinded
holdout result and not a real-user Answer-quality guarantee.

On the current 60-case synthetic development set:

```text
positive required-source complete: 17 / 30
negative empty evidence:           20 / 30
combined:                          37 / 60
```

Per-stratum:

```text
ordinary positive:           13 / 18 correct
temporal positive:            4 / 12 correct
public closed-form negative: 12 / 12 correct
missing-private negative:     4 / 9 correct
wrong-relation negative:      4 / 9 correct
```

The current-contract reevaluation excludes 24 assistant-utterance-only
positive cases that lack verified fact/self-action provenance:

```text
aligned subset:                    36 cases
raw-turn baseline:                 26 / 36
Grounded Write:                    29 / 36
positive completeness:              6 / 6  (both)
negative correctness:              20 / 30 -> 23 / 30
unsupported negative evidence:     10 / 30 ->  7 / 30
authorized required facts:         24 / 24 contract-valid
Formation losses:                   0 omission / validator / grounding / protocol
```

The dominant remaining current-contract failure is insufficient evidence on
negative queries. Assistant utterance alone remains outside the authorized
memory contract until Execution Trace or tool-result provenance exists.

### MAGMA query behavior

Production currently uses fixed one-hop BFS. Upstream MAGMA query
classification and adaptive parameters are not
active in production. The pinned upstream's active semantic-gated adaptive
traversal, third scan-anchor list, query-type heuristic reranking, multi-hop,
QA/session expansion, and AnswerFormatter are also bypassed/replaced.

The upstream probabilistic beam helper exists in source but has no active caller
in the pinned QueryEngine; it must not be described as current upstream query
behavior.

### Graph coverage

Lumina's production MAGMA graph is a subset of what upstream benchmark
`MemoryBuilder` can construct. Current ingestion primarily provides:

- sequential temporal links;
- dense semantic relations;
- exact shared-entity links;
- little/no causal structure in the current synchronous no-LLM configuration.

The pinned MAGMA uses one `MultiDiGraph` with temporal, semantic, causal, and
entity link types; it is not four physically independent graph stores.

### Other implementation limits

- Hot physical storage remains append-only;
- local JSONL stores and Dream/checkpoint paths assume single-writer operation;
- deployment is single-user / single continuous session / single process-worker;
- no real conversation/thread identity isolation;
- no global application-level context token budget;
- no reliable current-state/supersession truth layer;
- no reliable evidence-sufficiency/no-answer mechanism independent of the
  current global score floor;
- Hot/Cold reads remain file scans rather than indexed stores.

## Current Development Goal

The active priority is **repository consolidation before Mind development**.
The adopted production memory path is stable. `ControlledRelationResolver`
remains a fail-open Memory-side capability for explicit structured callers;
normal Chat provides no relation surfaces. No free-text query parser, entity
resolver, assistant self-action authorization, or execution-provenance system
is implied by this boundary.

Rejected experiments and their quantitative consequences are consolidated in
`docs/MEMORY_EXPERIMENT_HISTORY.md`.

## Not Started / Not Authorized by This Goal Alone

- automatic/startup/background/chat-time Dream;
- separate Conversation Graph production system;
- PostgreSQL/Neo4j memory;
- generalized ContextBuilder or ToolRuntime;
- schedulers, workers, autonomous agents, or other organs;
- global contradiction/current-state resolution;
- forgetting/decay/consolidation;
- new model-training infrastructure.
