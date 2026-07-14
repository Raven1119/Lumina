# Conversation Memory Workspace Instructions

## Scope

This file applies only to:

```text
Lumina/Conversation_Memory/
```

All work in this directory must also comply with the repository-root `AGENTS.md`. When instructions conflict, preserve the Cold Draft durability invariant and follow the stricter rule.

The current objective is to integrate the upstream MAGMA implementation with Lumina through an isolated, opt-in boundary. The first milestone is compatibility and end-to-end validation, not redesign.

## Authorized runtime chain

Build and validate only this chain:

```text
pending Cold Draft segment
-> explicit ingestion command or API
-> Lumina-owned adapter
-> upstream MAGMA memory write
-> MAGMA graph construction and indexing
-> Lumina-owned recall facade
-> bounded MemoryContext
-> optional model-context injection
```

Ingestion and recall must remain independently switchable and disabled by default in the production chat path.

## Directory ownership

Use this layout unless a task card explicitly authorizes another structure:

```text
Conversation_Memory/
├── upstream/
│   └── MAGMA/                 # pinned upstream checkout
├── adapter/                   # Lumina-owned MAGMA adapter
├── ingestion/                 # Cold Draft conversion, checkpoints, idempotency
├── recall/                    # bounded retrieval facade and result projection
├── contracts/                 # Lumina-owned protocols and DTOs
├── tests/                     # unit, integration, failure, restart tests
├── fixtures/                  # synthetic data only
├── scripts/                   # explicit setup, ingest, recall, benchmark commands
├── docs/                      # baseline, call chains, decisions, reports
├── requirements.txt           # isolated dependencies when needed
└── README.md
```

Do not place MAGMA graph logic, extraction logic, traversal logic, or provider-specific code inside Lumina production runtime modules.

## Upstream MAGMA rules

- Clone the official MAGMA repository into `upstream/MAGMA/`.
- Record the repository URL, exact commit SHA, license, setup commands, and baseline test results.
- Treat `upstream/MAGMA/` as read-only during the first integration milestone.
- Do not silently edit, vendor, reformat, or partially copy upstream MAGMA source into Lumina-owned modules.
- Prefer adapters, wrappers, dependency injection, configuration, or a subprocess boundary.
- If an upstream patch becomes unavoidable, stop at a documented patch proposal unless the task explicitly authorizes modifying the checkout.
- Keep generated databases, model caches, embeddings, logs, credentials, and user data out of Git.

## Lumina-owned contracts

The production runtime may depend only on Lumina-owned interfaces and DTOs. It must not import MAGMA classes directly.

Use boundaries equivalent to:

```python
class MemoryIngestor(Protocol):
    def ingest(self, segment: ColdDraftSegment) -> IngestionResult: ...

class MemoryRetriever(Protocol):
    def recall(self, query: MemoryQuery) -> MemoryContext: ...
```

Required DTO properties:

```text
ColdDraftSegment:
- segment_id
- source_turn_ids
- source_timestamps
- timezone
- immutable source content

IngestionResult:
- segment_id
- ingestion_version
- status
- memory_ids
- retryable
- safe_error_code

MemoryQuery:
- query text
- conversation/session scope
- temporal reference timestamp
- recall policy

MemoryContext:
- ordered memory items
- provenance references
- bounded rendered text
- truncation metadata
```

MAGMA-specific objects must be converted into these DTOs before crossing the workspace boundary.

## Cold Draft ingestion rules

- Cold Draft is the immutable source record.
- Read only `pending_digest` segments unless a synthetic fixture explicitly declares another state.
- Never rewrite, truncate, delete, compact, summarize in place, or mutate source Draft records.
- Use at least `(segment_id, ingestion_version)` as the idempotency key.
- Persist provenance for every derived memory object, including source segment, source turns, source timestamps, timezone, and extraction version.
- Do not mark a segment consumed until all required memory writes and checkpoint writes are durably successful.
- Partial failure must remain retryable without duplicating memory nodes or edges.
- A successful retry must converge to the same logical memory state.
- Do not ingest all Cold Draft files automatically at application startup.
- Do not run ingestion implicitly on every chat request.

## Temporal normalization

Relative expressions such as `today`, `yesterday`, `last week`, `今天`, and `昨天` must be resolved against the source message timestamp and timezone, not the ingestion time or current system time.

Preserve all of:

```text
original_expression
reference_timestamp
reference_timezone
normalized_start
normalized_end
normalization_method
normalization_confidence
```

Do not replace the original text with the normalized value.

## Recall rules

- Recall must use an explicit facade owned by Lumina.
- Bound recall using configurable limits, including maximum graph depth, candidate count, returned memories, characters, and estimated tokens.
- Return deterministic ordering for deterministic fixtures.
- Every returned memory must retain provenance.
- Do not scan complete Cold Draft files during recall.
- Do not expose graph internals, provider payloads, raw embeddings, local paths, credentials, tracebacks, or source Draft content through public API responses.
- Recall failure, timeout, missing credentials, missing embedding model, or corrupt graph data must degrade to an empty `MemoryContext` or another documented safe result.
- Recall failure must never block the normal chat response path.

## Production integration boundary

Changes outside `Conversation_Memory/` must be minimal and additive.

Initially permitted production changes are limited to:

- configuration required to enable or disable ingestion and recall;
- one narrow construction or dependency-injection seam;
- one bounded memory-context injection seam;
- state transition wiring required to mark a Cold Draft segment consumed after durable success.

Do not:

- create a second `MessageRuntime` or `ModelClient` protocol;
- replace Hot Draft or Cold Draft ownership;
- embed graph traversal inside `MessageRuntime`;
- change the default memory-disabled behavior;
- make the chat path asynchronous solely for MAGMA;
- make MAGMA availability a startup requirement;
- expose MAGMA classes in Lumina public APIs;
- replace the Draft system with Conversation Memory.

A normal task may modify at most three existing production modules unless its task card explicitly authorizes more.

## First milestone implementation order

Follow this order:

1. Clone and pin upstream MAGMA.
2. Run MAGMA independently with synthetic data.
3. Document MAGMA write, graph-build, routing, traversal, and context-generation call chains with file and symbol references.
4. Define Lumina-owned contracts and DTOs.
5. Implement a synthetic Cold Draft fixture importer.
6. Implement durable idempotency and ingestion checkpoints.
7. Ingest one synthetic pending segment into unmodified MAGMA.
8. Recall the ingested information through the Lumina-owned facade.
9. Add the minimal opt-in runtime seam, disabled by default.
10. Verify chat fallback with MAGMA unavailable.

Do not begin M-flow-style multi-granularity redesign until this milestone passes.

## Not authorized yet

Do not add or implement:

- M-flow `Episode -> Facet -> FacetPoint -> Entity` redesign;
- Dream or autonomous consolidation;
- background workers, schedulers, agents, or lifecycle systems;
- automatic forgetting, deletion, decay, contradiction resolution, salience mutation, or memory rewriting;
- PostgreSQL, Neo4j, or another production database;
- autonomous model-triggered ingestion;
- unrestricted recursive graph traversal;
- ingestion of real user Draft data in committed tests;
- repository-wide refactors unrelated to the integration.

## Required tests

At minimum, add tests for:

- duplicate ingestion of the same idempotency key;
- retry after partial failure;
- no consumed transition after failed memory persistence;
- provenance preservation;
- relative-time normalization using source timestamp and timezone;
- deterministic graph construction for deterministic fixtures where supported;
- bounded recall output;
- stable recall ordering;
- safe empty recall when MAGMA is unavailable;
- timeout and missing-credential fallback;
- no credential, path, exception, graph, embedding, or Draft leakage;
- restart recovery of ingestion checkpoints;
- chat behavior with memory disabled;
- chat behavior when recall raises an exception;
- all existing Hot Draft, Cold Draft, fallback, compaction, and restart tests.

Use only synthetic fixtures in committed tests.

## Documentation requirements

Maintain these files:

```text
docs/MAGMA_BASELINE.md
docs/MAGMA_CALL_CHAIN.md
docs/INTEGRATION_DESIGN.md
docs/PROVENANCE_AND_IDEMPOTENCY.md
docs/TEMPORAL_NORMALIZATION.md
docs/DECISIONS.md
docs/INTEGRATION_STATUS.md
```

Documentation must distinguish:

- verified upstream behavior;
- Lumina adapter behavior;
- assumptions;
- planned work;
- completed and tested work.

Update the repository-level `docs/CURRENT_STATUS.md` only after an accepted milestone. Do not report imports, stubs, or unexecuted code paths as completed integration.

## Completion criteria

The first milestone is complete only when all conditions hold:

1. A synthetic `pending_digest` Cold Draft segment is ingested through a Lumina-owned interface.
2. MAGMA durably creates the expected memory representation.
3. Re-ingesting the same segment does not create logical duplicates.
4. The stored information is recalled through a Lumina-owned facade.
5. Recall output is bounded and contains provenance.
6. The segment is marked consumed only after durable success.
7. Restart recovery preserves ingestion state.
8. The normal chat path works with memory disabled.
9. The normal chat path works when MAGMA fails or is unavailable.
10. Existing repository tests continue to pass.

## Validation

Run from the Lumina repository root:

```bash
python -m pytest -q
git diff --check
```

Also run the isolated MAGMA baseline and Conversation Memory integration commands documented in this workspace.

A task is not complete merely because MAGMA imports successfully or a graph database file exists.
