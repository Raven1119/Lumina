# Working on Conversation Memory

This scope implements the Lumina-owned Memory facade over pinned, unmodified
MAGMA. Follow root AGENTS, the current task, `docs/COLD_DRAFT.md`,
`docs/CURRENT_STATUS.md`, and the local provenance/temporal contracts. The
current task authorizes the entity/relationship/attribute enhancement; older
stage descriptions are historical evidence, not gates.

## Current boundary

Production callers use only Lumina DTOs and the adapter:

- `ingest(ColdDraftSegment) -> IngestionResult`;
- `recall(query, RecallPolicy) -> MemoryContext`;
- `recall_mentions(query, limit=20) -> EntityMentionContext`.

The last method reports source occurrences, including unresolved references;
it does not assert that the mentioned proposition is true. Graph objects,
EntityRefs, MAGMA UUIDs, vectors, backend scores and candidate paths remain
private. No UI or automatic ingestion is introduced.

## Current write path

Configured DeepSeek-V4-Pro manual Dream uses `grounded-formation-v2`.
`grounded-span-v2` remains the deterministic mock/legacy path. Formation v1
functions/checkpoints remain for explicit historical compatibility; default
Dream never selects them and never automatically reprocesses consumed Cold.

`grounded_formation.py` extracts facts and mentions over the complete bounded
source window, then verifies every structurally eligible proposition and its
subject/object roles in one batch. Mention surface plus occurrence locates
exact immutable source offsets. Invalid positions or references isolate the
candidate and its dependencies; independent valid results still pass the
mandatory verifier and persist. Processing issues keep the segment partial
and Cold pending; explicit semantic rejection is a normal result. Malformed
top-level output, provider failure and overflow cannot become empty success.
A single complete JSON fence is tolerated;
arbitrary surrounding prose is not.

The source-grounded `GroundedMemoryUnit` remains the fact representation.
`_entity_ingestion.py` uses the existing `IngestionStateStore` to checkpoint
the bounded raw extraction response before parsing, then verification and
bindings before graph writes. Retry reuses each saved stage. A later partial
retry may repair only invalid fact source refs once, using a separately saved
response and original verification of the new subset. Original facts, roles,
mentions and stage history stay unchanged. See the provenance contract for
the internal progress schema and repair eligibility. A completed manifest
includes both mentions and facts; a zero-fact window must still persist its
mentions. Source fingerprints and
stage/identity invariants are rechecked on restart.

One shared batch view binds subject/object/other mentions. Name lookup happens
before candidate limits. Explicit aliases/coreference require source evidence;
explicit new same-name identities separate; ambiguity and unresolved references
remain occurrences with bounded candidates. Verified local `same_as` reuses
the target's bound ref even when name lookup also finds other identities;
actual `distinct_from` conflicts still forbid the binding. Existing completed
bindings are not automatically rewritten. Current-user identity is `E_001`
only when source roles support it. Assistant assertions do not by themselves
authorize facts about the world, user or successful actions.

Facts are EVENT nodes with vectors; identities and occurrence metadata use
existing graph-only ENTITY nodes. One graph-only carrier holds unresolved
occurrences without an EntityRef. Explicit `REFERS_TO(role=subject|object)`
links differ from ordinary mention links. Attribute values stay literal; a
measurement is not an entity. Entity nodes never enter temporal event chains.

MAGMA writes converge on stable evidence IDs. Retry repairs an existing graph
node whose vector write was interrupted. Relationships and graph/vectors must
be durable before the checkpoint becomes completed and Cold's owner consumes.

## Current read path

Dense MiniLM, indexed lexical candidates and bounded entity-conditioned FAISS
candidates use existing RRF(k=60), fixed BGE and pinned Hindsight scoring.
BGE raw logits receive one stable sigmoid regardless of range or batching;
Hindsight weights and the source-snapshot reference time remain unchanged.
Name and lexical indexes are rebuildable views over the existing graph, updated
on writes; query limits apply after indexed lookup, not to a history prefix.
Multiple matching refs keep the entity channel; ambiguous refs do not receive
a shared SAME_ENTITY certainty marker.

The backend's derived entity membership covers all available EVENT vectors
linked by actual subject/object/ordinary REFERS_TO edges. Load and owner writes
maintain cached selectors over the same FAISS index; queries do not truncate
entity history to an adjacency prefix. Candidate output and graph expansion
remain bounded by `max_nodes`. Membership build/update, selector union and
native vector search have separately measured, nonconstant costs; stale or
unavailable membership fails softly for the entity channel only.

The adapter reads lazy graph adjacency within `max_nodes`, bypassing upstream's
unbounded neighbor materialization and first-ten-path truncation. Positive
`max_graph_depth` also allows one fixed, two-fact projection through explicit
subject/object roles; depth zero stays anchor-only. This does not change global
semantic/temporal depth or write an inferred relationship back to memory.

Single facts are rendered whole or omitted with `truncated=true`; source roles
and negation are preserved. A valid two-fact role path supplies both original
facts to the same BGE scorer; selecting its endpoint requires the complete
source chain to fit. A complete marked query/pair must also fit BGE's fixed
token window; otherwise scoring uses the original single fact. This changes
scoring input, not stored facts, the model,
Hindsight or the score floor. Budgets include items, rendered characters, queried
identities and actual adjacency reads. Recall remains read-only and fail-soft.
See `docs/COLD_DRAFT_ADAPTER_DESIGN.md` for selection, evidence packing and
capability limits. Experiment reports and captured provider outputs are local
artifacts, not runtime dependencies.

## Preserved boundaries

- Immutable Cold source text, turn/segment IDs, order, roles, times and timezone.
- Single writer; no competing checkpoint, database, manager or query planner.
- No automatic Dream, chat-time ingestion, backfill, fact rewriting,
  supersession, forgetting, contradiction resolution or task-memory expansion.
- No changes to Mind, Nervous, Execution, runtime provider policy or MAGMA.
- Keep original fact text, uncertainty, conditions, reported speech and precise
  values. Valid provenance proves source support, not external world truth.
- Preserve BGE model/revision, RRF constants, Hindsight formula and caller floor
  unless independently authorized and supported by evidence.

## Validation

Use isolated synthetic state. The root prepared Python runs maintained tests;
real MAGMA uses `Conversation_Memory/.venv/Scripts/python.exe`.

Run affected Memory/Dream/Chat regressions. Recall changes additionally require
`scripts.recall_e2e_test` under `docs/RECALL_E2E_ACCEPTANCE.md` isolation rules.
Run `git diff --check` and verify pinned upstream HEAD/worktree unchanged.
Report real and deterministic results separately; test counts do not establish
universal entity coverage or model reliability.
