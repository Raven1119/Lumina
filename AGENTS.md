# AGENTS.md

## 1. Repository state

Lumina is a local-first conversational runtime built around a Cold-first
continuity invariant.

Production chat path:

```text
Browser -> FastAPI -> MessageRuntime
-> Mind gate (stage 2: LlmMindGate by default with a real model;
   ConstantMindGate in mock/constant mode)
-> source-default-on bounded Recall
   -> MAGMA bounded candidates
      (+ entity-conditioned FAISS subset list when the query carries a
      target_entity_ref)
   -> fixed BGE reranker ([SAME_ENTITY] per-pair projection on equal refs)
   -> Hindsight post-rerank score
   -> final_min_score >= 0.144
   -> bounded MemoryContext
-> ModelClient
-> Hot Draft -> Cold-first compaction -> Cold Draft
```

Offline memory path:

```text
manual Dream
-> pending Cold Draft segments
-> one bounded Grounded Formation call when a real model is configured
-> deterministic source-grounding validation
-> durable grounded-formation-v1 unit checkpoint
-> Lumina Conversation Memory adapter
-> unmodified upstream MAGMA
-> durable checkpoint
-> Cold Draft segment consumed
```

Recall is already injected into production chat. It is enabled by source default
and may be explicitly disabled with
`LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED=false`.

The Memory MVP, the Mind Recall gate stage 2, and the generic Entity
CURRENT_USER vertical slice are in production. Do not rebuild already working
Chat, Draft, Dream, persistence, or memory injection behavior.

Lumina's long-term form remains defined by `docs/NORTH_STAR.md`. The North Star
is a design compass, not authorization to expand the current task.

## 2. Existing capabilities

Treat these as completed behavior unless a task identifies a verified defect:

- same-origin browser chat, `/api/status`, and `/api/chat`;
- mock mode plus one explicit MiniMax Anthropic-compatible real-model adapter;
- safe provider fallback;
- restart-persistent Hot Draft and Cold-first logical compaction;
- Draft Turn Provenance V2 with stable IDs and truthful aware time provenance;
- immutable Cold source records with owner-controlled pending/consumed state;
- manual, synchronous, bounded Dream;
- configured real-model Dream uses MiniMax-M3 in non-thinking mode with a
  Formation-only 2000-token output budget;
- deterministic `grounded-span-v2` projection from eligible Cold source spans
  remains for mock/legacy ingestion;
- real-model manual Dream uses one bounded `grounded-formation-v1` call per
  Cold segment, validates atomic SRV units against exact source spans, applies
  the bounded semantic fallback and value-only guard, and checkpoints accepted
  units before MAGMA writes;
- pinned, unmodified upstream MAGMA;
- durable `(segment_id, ingestion_version)` checkpoints and retry convergence;
- Lumina-owned Recall DTOs and provenance projection;
- source-default-on production Recall injection into the Answer Model system
  context;
- bounded dense + lexical anchor retrieval with RRF fusion, plus a bounded
  entity-conditioned FAISS subset list when the query carries a
  `target_entity_ref`;
- fixed production graph traversal at `max_graph_depth=1`, `max_nodes=20`;
- fixed `BAAI/bge-reranker-v2-m3` reranking;
- deterministic Hindsight-style post-rerank scoring with wall-clock-independent
  reference time;
- inclusive production `final_min_score=0.144`;
- bounded top-3 / 5000-character `MemoryContext` rendering;
- fail-soft empty/unavailable Recall behavior that does not block normal chat;
- isolated real-MAGMA Recall E2E validation with restart/idempotency coverage;
- a Mind gate on every chat message before the Recall guard: stage 2
  `LlmMindGate` (mind-gate-v2, MiniMax-M3 non-thinking, 8 output tokens,
  temperature 0) is the real-model default; mock/explicit `constant` mode uses
  `ConstantMindGate`; decisions are `{recall: bool}`, fail-open, and
  append-only audited.

Do not duplicate these capabilities or silently replace their boundaries.

## 3. Current Recall facts

The production Recall path is currently:

```text
query
-> TRG keyword-enriched dense anchors
-> bounded lexical anchors
-> entity-conditioned FAISS subset channel when the query carries a
   target_entity_ref (EntityNode REFERS_TO adjacency -> IDSelectorBatch subset
   top-k; adds candidates only)
-> dense + lexical (+ entity subset) RRF
-> fixed graph BFS from fused anchors
-> projectable bounded candidates
-> fail-open ControlledRelationResolver gate when relation surfaces are supplied
-> BGE rerank
   (per-pair [SAME_ENTITY] scoring projection when query target and candidate
   subject carry an equal subject_entity_ref; marker never enters
   stored/evidence text; LUMINA_USER_SELF_BINDING_ENABLED=false rolls back)
-> Hindsight recency adjustment
-> final_score >= 0.144
-> stable top-3
-> MemoryContext
```

Current production policy:

```text
top_k=10
max_graph_depth=1
max_nodes=20
max_evidence_items=3
max_chars=5000
final_min_score=0.144
relation_surfaces=None
```

The current local MAGMA audit established:

```text
MAGMA_NATIVE_RECALL: PARTIAL
MAGMA_ADAPTIVE_QUERY_POLICY: NOT_USED
MAGMA_FOUR_GRAPH_TRAVERSAL: PARTIALLY_USED
```

Lumina reuses MAGMA storage, TRG dense retrieval, graph primitives, and generic
traversal, but does not call upstream `QueryEngine.query()` in production.
Upstream query classification, adaptive parameters, active semantic-gated
traversal, third scan-anchor list, benchmark reranking, and QA/session expansion
are not part of the current production path.

Do not describe upstream MAGMA as four physically separate graphs. The pinned
implementation uses one `MultiDiGraph` with temporal, semantic, causal, and
entity link types.

Do not describe upstream MAGMA's probabilistic beam helper as its active query
path. In the pinned source, that helper has no active caller.

## 4. Current Development Stage

The Memory MVP, the Mind Recall gate stage 2 (`LlmMindGate` as the real-model
production default), and the generic Entity vertical slice (`E_001`
CURRENT_USER plus ordinary persisted entities: graph-only EntityNodes,
subject and role-less mention `REFERS_TO` edges, entity-conditioned
retrieval, exact-surface query-side ref lookup with a 0/1/many rule,
`[SAME_ENTITY]` ranking cue) are in production. Any further new capability
must first be validated by an independent experiment and only then promoted
to production.
Durability, provenance, boundedness, safety, and fail-soft behavior remain
non-negotiable.

The authorization-aligned 36-case development subset reports raw-turn Recall at
26/36 and Grounded Write at 29/36, with both retaining all 6 currently
authorized positive cases. The original 60-case result remains historical: 24
positive cases depended only on assistant utterances and are outside the
current fact/self-action authorization contract.

`ControlledRelationResolver` is the existing structured seam. It rejects only
when both caller-supplied query relations and memory relations resolve and are
incompatible; either side unresolved fails open. Normal Chat supplies no
relation surfaces, so this capability remains available to structured callers
and future Mind rather than acting as a free-text query parser.

Do not add a parser, entity resolver, assistant self-action authorization, or
execution provenance without a separate explicit task. Rejected experiments
and quantitative decisions are consolidated in
`docs/MEMORY_EXPERIMENT_HISTORY.md`.

## 5. Algorithm-development rules

### Copy first

Prefer:

```text
official source code
> official package/release
> official pretrained implementation
> paper + official code
> paper-only specification
> Lumina-specific invention
```

If a suitable upstream implementation exists, port its behavior instead of
re-deriving a similar algorithm from the paper.

For each nontrivial Recall mechanism, record:

```text
SOURCE
VERSION / COMMIT
LICENSE
SOURCE SYMBOL
ORIGINAL INPUT / OUTPUT
ORIGINAL DECISION OR TRAVERSAL RULE
LUMINA ADAPTATION
```

### Diagnose before changing models

Do not model-hop. BGE is the current fixed reranker. Do not replace it merely
because a downstream case fails.

A failed experiment must first be classified as one of:

```text
anchor failure
routing failure
traversal failure
ranking failure
admission failure
graph-formation/data failure
```

Only modify the component implicated by evidence.

### One variable at a time

Do not simultaneously change query routing, traversal, BGE, Hindsight,
thresholds, graph construction, and admission. An experiment must identify
which mechanism caused the measured change.

### No benchmark patching

Forbidden:

- case IDs, fixture strings, entity-specific exceptions, or regex patches built
  from failed cases;
- threshold fishing on a final regression set;
- modifying labels/fixtures to obtain PASS;
- hidden reranking inside an admission gate;
- adding multiple magic-number features until a small benchmark passes.

## 6. Authority

Active authority:

- `docs/NORTH_STAR.md` — long-term form and direction only;
- `docs/final_goal.md` — current product direction and next objective;
- `docs/CURRENT_STATUS.md` — current implementation facts;
- `docs/COLD_DRAFT.md` — Cold-first persistence contract;
- `docs/DRAFT_TURN_PROVENANCE_V2.md` — turn provenance contract;
- `docs/RECALL_E2E_ACCEPTANCE.md` — Recall E2E contract;
- `docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` — pinned MAGMA/Lumina query-path audit;
- `Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md`;
- `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`;
- `Conversation_Memory/AGENTS.md` and `Dream/AGENTS.md` where more specific;
- this file.

Decision order:

1. explicit task card and acceptance criteria;
2. Cold-first durability and synchronous chat availability;
3. truthful provenance, idempotency, boundedness, and leak safety;
4. `docs/CURRENT_STATUS.md` for implementation facts;
5. the most specific non-stale workspace contract;
6. source-faithful algorithm reuse;
7. `docs/NORTH_STAR.md` as a tie-breaker only.

## 7. Ownership

### `core/`

Owns API validation, one `MessageRuntime`, one `ModelClient` protocol, Recall
invocation/injection, Draft creation, Hot Draft, and Cold-first compaction.

Do not put MAGMA traversal, graph internals, BGE implementation, or Dream
orchestration inside `MessageRuntime`.

### `Conversation_Memory/`

Owns the pinned MAGMA checkout, ingestion adapter, graph-backed retrieval,
Recall policy execution, BGE/Hindsight scoring, public memory DTOs, provenance,
fixtures, tests, and memory documentation.

Production code outside this workspace may depend only on Lumina-owned
interfaces/DTOs, never directly on MAGMA, NetworkX, FAISS, or model internals.

### `Dream/`

Owns manual bounded orchestration from eligible Cold segments through
the configured ingestion version and memory-complete-before-consumed
coordination. Real-model manual Dream uses `grounded-formation-v1`;
mock/legacy callers retain `grounded-span-v2`.

Recall optimization must not move Dream into chat or mutate write-side memory.

### Cold Draft owner

The existing Cold Draft owner remains the only authority for source records and
`pending_digest -> consumed` transitions.

## 8. Non-negotiable invariants

### Cold-first and provenance

- Cold persistence succeeds before logical compaction advances.
- Cold source text/provenance is immutable.
- New turns preserve `turn_id`, `role`, `text`, `created_at`,
  `source_timezone`, and `timezone_source` through all layers.
- No Recall optimization may rewrite Cold evidence.

### Dream

- Dream stays manual, synchronous, bounded, and single-writer.
- No Dream/ingestion work runs during `/api/chat`.
- Retry remains idempotent through `(segment_id, ingestion_version)`.
- Grounded Formation makes at most one provider call for a newly seen bounded
  segment, persists validated units before MAGMA, and reuses that checkpoint
  on MAGMA retry.

### Recall

- Recall remains behind a Lumina-owned facade.
- Bound anchors, depth, nodes, evidence count, and rendered size.
- Preserve stable ordering, evidence IDs, and provenance.
- Do not scan Cold Draft from Recall.
- Empty Recall is valid.
- Recall failure must not block normal chat.
- Do not expose BGE scores, embeddings, graph objects, MAGMA UUIDs, local paths,
  credentials, provider bodies, tracebacks, or raw Draft records.
- Do not modify pinned upstream MAGMA.
- BGE/Hindsight/threshold changes require explicit evidence and task scope; they
  must not be side effects of traversal work.

## 9. Minimal-change rule

Default to the smallest vertical change that tests the current hypothesis.
Unless a task explicitly authorizes more:

- modify at most three existing production modules;
- add at most one production file and one test file;
- prefer extending existing tests and E2E harnesses;
- add no generic `Manager`, `Registry`, `Factory`, framework, service, worker,
  database, or model-serving layer;
- do not refactor neighboring modules;
- do not add future-facing extension points without a current caller;
- do not update unrelated documents;
- remove TEMP experiments after extracting the maintained conclusion.

If the minimum correct experiment exceeds this budget, report the smallest
extra surface required before coding further.

## 10. Not authorized by the Recall-optimization goal alone

Do not add or redesign:

- automatic/startup/background/chat-time Dream;
- schedulers, workers, agents, or autonomous ingestion;
- new long-term memory databases;
- Conversation Graph as a separate production system;
- ContextBuilder or ToolRuntime;
- forgetting, decay, global contradiction resolution, or memory rewrite;
- new providers;
- new model-training infrastructure;
- repository-wide package/layout refactors;
- upstream MAGMA patches;
- unrelated organs or Mind work.

A Recall experiment may inspect graph formation if evidence points there, but
write-side changes require a separate explicit task.

## 11. Current known quality evidence

The current 60-case synthetic development set is diagnostic data, not a blinded
holdout and not real-user Answer quality.

At `final_min_score=0.144` it currently reports:

```text
positive required-source complete: 17 / 30
negative empty evidence:           20 / 30
combined:                          37 / 60

temporal positive:                 4 / 12
ordinary positive:                13 / 18
missing-private negative:          4 / 9
wrong-relation negative:           4 / 9
public closed-form negative:      12 / 12
```

The authorization-aligned current-contract subset is:

```text
currently authorized positive:     6 / 6
negative empty evidence:           23 / 30
combined Grounded Write:           29 / 36
raw-turn comparison:               26 / 36
```

The original strata remain useful historical diagnostics, but the 24
assistant-utterance-only positives are not required memories without verified
Execution Trace or tool-result provenance. Use all synthetic results to locate
failure modes, not to justify ad-hoc case patches; production claims still
require independent evidence.

## 12. Validation

Use synthetic data and temporary paths only.

Standard validation:

```bash
python -m pytest -q
python -m pytest Conversation_Memory/tests -q
python -m pytest Dream/tests -q
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

Run the existing real-MAGMA Recall E2E whenever a task affects Recall behavior.
Upstream MAGMA status/diff must remain empty.

For Recall optimization, report at minimum:

```text
baseline vs candidate
per-stratum results
candidate count / depth / node budget
ranking/admission components intentionally held fixed
regressions
restart/idempotency
determinism
TEMP artifacts removed
```

## 13. Change safety

- Preserve `.env.local`, `data/`, and real runtime memory.
- Do not commit, push, rebase, hard-reset, or rewrite history unless explicitly
  authorized.
- Do not silently patch upstream MAGMA.
- Do not change unrelated public behavior.
- Update `docs/CURRENT_STATUS.md` only after tests establish the implementation
  fact.
- Never describe an experiment as production behavior before promotion and
  regression validation.

## Agent skills

### Issue tracker

Issues and specs are tracked in GitHub Issues for `Raven1119/Lumina`. See
`docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-label mattpocock/skills vocabulary. See
`docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository using root `CONTEXT.md` and `docs/adr/`,
created lazily when needed. See `docs/agents/domain.md`.
