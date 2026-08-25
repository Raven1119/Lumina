# Current Status

## Complete

### Chat and Draft continuity

- same-origin FastAPI browser chat with `/api/status` and `/api/chat`;
- the browser chat input sends on `Enter`, inserts a newline on
  `Shift+Enter`, and never sends during IME composition
  (`edge/static/app.js`);
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
- a deterministic, LLM-free self-identity coverage guard runs inside Formation
  after model-call validation: when an explicit user self-identification
  (我叫X / 我的名字是X / 你可以叫我X) was omitted, exactly one source-grounded
  identity unit (exact-span value) is constructed from the raw source,
  admitted only through the unchanged strict validator, and never duplicates
  an equivalent accepted unit (`Conversation_Memory/adapter/identity_coverage.py`;
  shadow: `docs/experiments/identity_coverage_guard/`; regression:
  `tests/test_identity_coverage.py`);
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
- the Lumina-owned MAGMA backend enforces an EVENT-only temporal-link
  boundary: non-event graph nodes never participate in temporal ordering,
  while pinned upstream MAGMA remains unchanged
  (`Conversation_Memory/adapter/backend.py`, regression:
  `Conversation_Memory/tests/test_temporal_boundary.py`).

### Production Recall and Answer injection

- Recall is wired into production chat and enabled by source default;
- `LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED=false` explicitly disables it;
- current user text is the Recall query;
- MAGMA TRG keyword-enriched dense anchors plus bounded lexical anchors;
- deterministic RRF fusion (dense + lexical, plus the entity-conditioned
  subset list described below when the query carries a `target_entity_ref`);
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

### Current-user entity binding (promoted)

- validated GroundedMemoryUnit subjects are classified after the unchanged
  Grounded Formation validator; detection first resolves the contextual
  `CURRENT_USER` role, and when the grounded source establishes the subject is
  the current user, the unit persists generic metadata
  `subject_entity_ref="E_001"` (retrieval metadata only — it never authorizes
  a fact);
- recognized subject surfaces: first-person pronouns, self-naming binding,
  assistant naming + user acceptance, segment-level self-name binding, and
  Formation speaker-normalized `用户` / `user` / `the user` surfaces gated by
  first-person evidence in cited user-role spans;
- a current-user-bound event gets one graph-only EntityNode
  (`entity:e_001`) and a single
  `Event --ENTITY/REFERS_TO(role=subject)--> EntityNode` edge; find-or-create
  is idempotent across ingestion retry, node/edge/metadata persist across
  restart, and the EntityNode is never vector-indexed
  (`Conversation_Memory/tests/test_entity_node_write_path.py`);
- ordinary named subjects use deterministic write-side exact-surface binding:
  one bounded persisted candidate with the same canonical surface is reused,
  no match receives a stable new EntityRef, and duplicate-surface candidates
  remain unbound rather than guessed; these bindings are checkpointed before
  MAGMA writes and include the current-run overlay, without an LLM, fuzzy
  matching, aliases, or query-side entity lookup;
- grounded mention bindings (Production Slice 1): on a new formation segment,
  each unique `(turn_id, supporting_span)` of validator-accepted units gets
  one bounded entity-only extraction call through the same FormationModel
  seam (span text only — never unit.text, never the Cold store), an
  exact-span gate, and — only when a span backs more than one unit — one
  subset-enforced selector call per unit; grounded surfaces are bound by
  exact canonical-surface match against persisted candidates plus the
  same-run overlay, with unbound mentions receiving a stable EntityRef seeded
  from `(unit.id, surface)`, except that a mention surface equal to the same
  unit's already-bound subject canonical surface reuses that unit's
  `subject_entity_ref` directly (one explicit entity never gets two refs); the five-key records (`unit_id`, `entity_ref`,
  `canonical_surface`, `turn_id`, `supporting_span`) are checkpointed before
  any MAGMA write, reused with zero provider calls on retry/restart, carried
  into event metadata as `mention_entity_refs` / `mention_entity_surfaces`
  (consumed by the Slice 2 write path below); extraction/selector
  failure after one bounded retry leaves the segment pending without a
  checkpoint, and malformed persisted bindings fail as `state_corrupt`
  (`Conversation_Memory/tests/test_entity_node_write_path.py`; shadow
  evidence: `docs/experiments/grounded_mention_selection/RESULT.md`);
- event-centric multi-entity REFERS_TO (Production Slice 2): at
  `create_relationships` time, each event's `mention_entity_refs` adds one
  generic role-less `Event --ENTITY/REFERS_TO--> EntityNode` edge per
  mentioned ref — properties carry only `sub_type`, never a `role` key —
  skipping a mention ref equal to the event's `subject_entity_ref` because
  the role=subject edge already covers that pair; EntityNodes are
  find-or-created graph-only exactly as in the subject path (never
  vector-indexed, never on the temporal chain), node/edge dedup makes
  ingestion retry and reload converge, and non-entity attribute values never
  produce EntityNodes (`Conversation_Memory/tests/test_entity_node_write_path.py`;
  shadow evidence: `docs/experiments/multi_entity_recall_gain/RESULT_CROWDED.md`);
- at Recall time a self-referential query is classified to
  `target_entity_ref="E_001"` before candidate generation; MAGMA
  anchor/lexical/traversal always use the original normalized query;
- when that classification finds no self reference, the adapter asks the
  backend for a deterministic exact-surface lookup over persisted EntityNode
  `canonical_surface` attributes (subject and mention EntityNodes alike):
  exactly one distinct ref whose surface is contained in the query resolves
  to that ref, while 0 hits or hits mapping to more than one distinct ref
  (two Alexes) resolve None — no LLM, embedding, fuzzy, alias, or
  coreference matching, and no general query entity parser;
  `LUMINA_USER_SELF_BINDING_ENABLED=false` still disables the whole entity
  channel, lookup included
  (`Conversation_Memory/tests/test_entity_conditioned_recall.py`,
  `tests/test_user_self_binding.py`);
- a query carrying a `target_entity_ref` additionally runs an
  entity-conditioned candidate channel: the EntityNode's full `REFERS_TO`
  adjacency (role=subject and role-less mention edges) yields that entity's
  event IDs, FAISS
  `IDSelectorBatch` subset search ranks a bounded top-k over only those
  events, and the result joins RRF as a third list — it adds candidates only
  and leaves BGE / Hindsight / admission untouched; a query without a
  `target_entity_ref` takes the byte-identical pre-existing path, and
  `list_entity_candidates` still projects role=subject edges only
  (`Conversation_Memory/tests/test_entity_conditioned_recall.py`);
- the constant `[SAME_ENTITY] ` marker enters only the BGE scoring projection
  of a (query, candidate) pair when both sides carry the same entity ref;
  stored factual text and user-visible evidence never contain the marker;
- BGE / Hindsight / `final_min_score=0.144` are unchanged;
- `LUMINA_USER_SELF_BINDING_ENABLED=false` rolls back to pre-binding scoring
  exactly; legacy memories without the field read as unbound;
- evidence: `docs/experiments/user_self_production_wiring/RESULT.md`
  (mini-shadow + fresh-session E2E gate) and
  `docs/experiments/context_role_entityref/RESULT.md` (role/ref separation).

### Mind gate (stage 2 promoted)

- every chat message passes a Mind gate before the Recall guard
  (`User -> Mind -> Memory`);
- the production default for real model configuration is the promoted
  `LlmMindGate` (`mind-gate-v2`, MiniMax-M3 non-thinking, 8 output tokens,
  temperature 0), validated by shadow + holdout + operational + regression
  evidence (`docs/experiments/mind_stage2_promotion/RESULT.md`);
- mock mode always uses the stage-1 `ConstantMindGate`
  (`MindDecision(recall=True)`), regardless of mode setting;
- `LUMINA_MIND_GATE_MODE=constant` rolls back to the stage-1 constant-allow
  gate; gate-client construction failure also falls back to it, so chat
  availability never depends on gate provider configuration;
- Mind wiring is independent of Recall wiring: the gate still runs (and is
  audited) when Recall is disabled or unavailable;
- each decision is appended to an append-only JSONL audit log
  (`LUMINA_MIND_DECISION_LOG_PATH`, default `data/mind/decisions.jsonl`),
  recording `{turn_id, recall, decided_at}`;
- decision failure and audit-log failure fail open as separate events
  (`mind_gate_failed` / `mind_decision_log_failed`); an unauditable rejection
  never takes effect silently;
- the gate contract remains `MindDecision {recall: bool}`; further Mind
  responsibilities require separate approval
  (`docs/plan/MIND_DEFINITION_V1.md`).

### Validation and audit

Latest reported local validation:

```text
root tests:                 328 passed, 24 skipped
Conversation_Memory tests: 163 passed, 45 skipped
Dream tests:                36 passed, 1 skipped
real MAGMA Recall E2E:      PASS (10 / 10 queries)
Mind production-seam controls: PASS (9 / 9)
USER_SELF fresh-session E2E: PASS (promoted, docs/experiments/user_self_production_wiring/RESULT.md)
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

## Current Development Stage

The Memory stage is complete and currently has no blocking todos. In
production: the Memory MVP, Grounded Write, the Mind Recall gate stage 2
(`LlmMindGate` as the real-model default;
`docs/experiments/mind_stage2_promotion/`), the generic multi-entity Entity
graph (`CURRENT_USER` → `E_001` plus ordinary persisted entities, graph-only
EntityNodes, exact-surface query-side ref lookup, entity-conditioned
retrieval, `[SAME_ENTITY]` ranking cue;
`docs/experiments/entity_production_acceptance/RESULT.md`), and the self-name
coverage guard (`Conversation_Memory/adapter/identity_coverage.py`;
`tests/test_identity_coverage.py`).

Execution V1 is frozen as isolated experimental history at tag
`execution-organ-v1-final`; it was never connected to production Chat,
Memory, Dream, or Mind. Execution V2 has not started, and no production
Execution or ToolRuntime implementation exists on this baseline.

`ControlledRelationResolver` remains a fail-open Memory-side capability for
explicit structured callers; normal Chat provides no relation surfaces. No
free-text query parser, entity resolver, assistant self-action authorization,
or execution-provenance system is implied by this boundary.

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
