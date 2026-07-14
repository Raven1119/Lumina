# AGENTS.md

## Repository scope

Lumina is currently a restart-persistent Cold Draft conversational MVP. The existing production chat path is:

```text
Browser frontend -> FastAPI -> MessageRuntime -> ModelClient
-> Hot Draft -> Cold-first compaction -> Cold Draft
```

This repository now explicitly authorizes an isolated Conversation Memory integration workspace at:

```text
Lumina/Conversation_Memory/
```

The immediate goal is to integrate the upstream MAGMA implementation with Lumina through a minimal, opt-in boundary, using pending Cold Draft segments as the ingestion source. This authorization supersedes the previous blanket prohibition on MAGMA, recall, embeddings, graph traversal, and memory work only within the scope defined below.

## Authoritative documents

Treat the following files as active authority:

- `docs/final_goal.md`: product direction;
- `docs/COLD_DRAFT.md`: Cold-first preservation contract;
- `docs/CURRENT_STATUS.md`: current implementation facts;
- this `AGENTS.md`: development permissions and boundaries.

When documents conflict, preserve the Cold Draft durability invariant and follow the more specific instruction in this file for `Conversation_Memory/` work.

## Authorized objective

Build and validate this minimal chain:

```text
pending Cold Draft segment
-> explicit Conversation Memory ingestion
-> MAGMA-compatible memory write
-> four-graph construction/indexing
-> explicit recall request
-> bounded memory context result
-> optional injection into the existing model request
```

The first milestone is integration compatibility, not architectural redesign. Run the upstream MAGMA behavior first; modify or replace MAGMA internals only in later explicitly authorized tasks.

## Workspace layout

All new memory-specific implementation, experiments, copied fixtures, research notes, adapters, and tests should live under:

```text
Conversation_Memory/
```

Preferred layout:

```text
Conversation_Memory/
├── upstream/
│   └── MAGMA/                 # pinned upstream checkout; keep source changes isolated
├── adapter/                   # Lumina <-> MAGMA boundary
├── ingestion/                 # Cold Draft segment conversion and idempotency
├── recall/                    # bounded recall facade and result projection
├── tests/                     # integration and contract tests
├── fixtures/                  # synthetic Cold Draft data only
├── docs/                      # design notes, call-chain analysis, decisions
└── README.md
```

Do not copy MAGMA source files into Lumina production modules. Prefer a pinned upstream checkout, package boundary, subprocess boundary, or explicit adapter.

## Required boundaries

### Cold Draft preservation

- Cold Draft remains the immutable source record.
- Read only segments in `pending_digest` state unless a test fixture explicitly says otherwise.
- Never rewrite, truncate, delete, summarize in place, or reinterpret the original Cold Draft record.
- A memory write must retain provenance including at least `segment_id`, source turn identifiers when available, source timestamps, and ingestion version.
- Do not mark a Cold Draft segment consumed until the derived memory write is durably successful.
- Failed or partial ingestion must remain retryable and idempotent.
- The existing rule remains mandatory: write the Cold Draft segment before advancing logical compaction state.

### Existing chat runtime

- Keep exactly one production `MessageRuntime` and one `ModelClient` protocol.
- Preserve the existing Hot Draft and Cold Draft owners.
- Keep the default chat path synchronous, bounded, restart-persistent, and usable when Conversation Memory is disabled or broken.
- Conversation Memory must be opt-in through configuration or an explicit runtime seam.
- MAGMA failure, timeout, missing credentials, unavailable embeddings, or corrupt memory data must not prevent the normal chat response path.
- Do not expose credentials, provider bodies, provider URLs, local paths, raw exceptions, graph internals, or Cold Draft contents through public API responses.

### Integration shape

- The production runtime may depend only on a small Lumina-owned interface, not directly on MAGMA internals.
- Define narrow boundaries such as:

```text
MemoryIngestor.ingest(segment) -> IngestionResult
MemoryRetriever.recall(query, policy) -> MemoryContext
```

- Convert MAGMA-specific objects into Lumina-owned DTOs before they enter the main chat path.
- Bound recall by configurable limits such as maximum nodes, graph depth, returned memories, characters, or tokens.
- Preserve deterministic ordering and stable identifiers wherever possible.
- Keep ingestion and recall independently switchable.

## Initially allowed work

Within `Conversation_Memory/`, Codex may:

- clone or inspect the official MAGMA repository;
- pin and record the upstream commit SHA;
- install and document MAGMA-specific dependencies in an isolated environment or dedicated dependency file;
- trace MAGMA memory writing, four-graph construction, query routing, traversal, and context generation;
- implement a Cold Draft-to-MAGMA adapter;
- implement manual or explicitly invoked ingestion;
- implement a bounded recall facade;
- add synthetic fixtures, unit tests, integration tests, benchmarks, and diagnostics;
- add minimal configuration needed to enable or disable the integration;
- modify a small number of existing Lumina modules only when necessary to expose an opt-in ingestion or recall seam.

## Not authorized in the first integration milestone

Do not:

- redesign MAGMA into the M-flow multi-granularity model yet;
- add Dream, autonomous consolidation, lifecycle management, background schedulers, agents, or workers;
- add automatic deletion, forgetting, contradiction resolution, salience mutation, or memory rewriting;
- make ingestion run implicitly on every chat request;
- scan all Cold Draft files on every recall;
- replace the Draft system with MAGMA;
- bypass the Cold-first compaction contract;
- place graph logic directly inside `MessageRuntime`;
- make MAGMA classes part of Lumina's public API schema;
- introduce PostgreSQL, Neo4j, or another production database unless a later task explicitly authorizes it;
- modify upstream MAGMA code before the unmodified integration path has been documented and tested;
- commit credentials, `.env.local`, generated memory databases, model caches, embeddings, or user conversation data.

## Change discipline

- Prefer additive changes.
- Keep production edits outside `Conversation_Memory/` minimal and localized.
- A normal integration task may modify at most three existing production modules unless its task card explicitly authorizes more.
- Add no root-level dependency without documenting why isolation inside `Conversation_Memory/` is insufficient.
- Preserve local `.env.local` and `data/` contents.
- Do not automatically commit, push, rebase, hard reset, delete branches, or rewrite history.
- Do not silently patch the upstream MAGMA checkout. Record any required patch as a separate diff or adapter decision.

## Required implementation order

1. Record the MAGMA upstream URL, commit SHA, license, setup steps, and baseline tests.
2. Run MAGMA independently with synthetic data.
3. Document its complete write and recall call chains with file and symbol references.
4. Define Lumina-owned ingestion and recall interfaces.
5. Implement a read-only Cold Draft fixture importer.
6. Add durable idempotency keyed by `segment_id` and ingestion version.
7. Run manual ingestion into MAGMA.
8. Run recall through the Lumina-owned facade.
9. Add an opt-in runtime seam; keep it disabled by default.
10. Verify fallback behavior with MAGMA unavailable.

Do not proceed to multi-granularity redesign until these steps pass.

## Required tests

At minimum, add tests for:

- duplicate ingestion of the same `segment_id`;
- retry after partial failure;
- no consumed-state transition after failed memory write;
- provenance preservation;
- relative-time normalization using the source message timestamp and timezone;
- bounded recall output;
- stable recall ordering for deterministic fixtures;
- no credential, path, exception, graph, or Draft leakage;
- chat behavior with memory disabled;
- chat behavior when MAGMA raises, times out, or has no model/embedding credentials;
- restart recovery of ingestion state;
- existing Hot Draft, Cold Draft, fallback, compaction, and restart tests.

Use synthetic conversation fixtures. Never use real user Draft data in committed tests.

## Documentation requirements

Maintain under `Conversation_Memory/docs/`:

- `MAGMA_BASELINE.md`: upstream revision, environment, commands, baseline behavior;
- `MAGMA_CALL_CHAIN.md`: write, graph construction, routing, traversal, and context-generation call chains;
- `INTEGRATION_DESIGN.md`: interfaces, data flow, configuration, failure behavior, and bounded recall policy;
- `PROVENANCE_AND_IDEMPOTENCY.md`: identifiers, checkpoints, retries, and consumed-state rules;
- `DECISIONS.md`: decisions, rejected alternatives, and any upstream patches required.

Update `docs/CURRENT_STATUS.md` truthfully after each accepted milestone. Do not describe planned work as complete.

## Validation

Run the existing repository validation plus Conversation Memory tests:

```bash
python -m pytest -q
git diff --check
```

If the workspace uses an isolated environment, also run its documented MAGMA baseline and integration test commands.

A task is not complete when only imports succeed. Completion requires a synthetic Cold Draft segment to be ingested, recalled through a Lumina-owned interface, and verified without breaking the memory-disabled chat path.
