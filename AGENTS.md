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

The immediate Conversation Memory goal is to integrate the upstream MAGMA implementation with Lumina through a minimal, opt-in boundary, using pending Cold Draft segments as the ingestion source.

This repository also explicitly authorizes an isolated Dream orchestration workspace at:

```text
Lumina/Dream/
```

The initial Dream milestone is limited to manually triggered Cold Draft digestion. Dream may read real `pending_digest` Cold Draft segments through the existing Cold Draft owner, invoke the Lumina-owned Conversation Memory ingestion interface, and request the owner to mark a segment `consumed` only after durable memory completion.

This authorization changes only the scheduling time of MAGMA writes. Conversation turns remain the memory-event granularity, while writes occur during an explicit Dream run rather than during `/api/chat`.

## Authoritative documents

Treat the following files as active authority:

- `docs/final_goal.md`: product direction;
- `docs/COLD_DRAFT.md`: Cold-first preservation contract;
- `docs/CURRENT_STATUS.md`: current implementation facts;
- `Conversation_Memory/AGENTS.md`: Conversation Memory workspace rules;
- `Dream/AGENTS.md`: Dream workspace rules;
- this `AGENTS.md`: repository-wide development permissions and boundaries.

When documents conflict:

1. preserve the Cold Draft durability invariant;
2. preserve the synchronous chat path;
3. follow the most specific applicable `AGENTS.md`;
4. do not weaken Conversation Memory provenance or idempotency guarantees.

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


## Authorized Dream objective

Build and validate this manual offline chain:

```text
explicit developer command
-> DreamRunner.run_once()
-> bounded read of real pending Cold Draft segments
-> conversion to existing Conversation Memory DTOs
-> existing MemoryIngestor.ingest(...)
-> durable ingestion completion
-> Cold Draft owner marks the segment consumed
-> structured Dream run report
```

The purpose is scheduling separation only. Conversation turns remain MAGMA memory events, while MAGMA writes occur during an explicit Dream run rather than during `/api/chat`.

`Dream/` is an orchestration layer. It must not directly depend on upstream MAGMA, NetworkX, FAISS, embedding implementations, or MAGMA-specific DTOs.

The initial Dream milestone is manual only. It must not run from `/api/chat`, application startup, compaction hooks, background workers, schedulers, or autonomous model decisions.

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

Dream-specific implementation should live under:

```text
Dream/
├── AGENTS.md
├── runner.py
├── cold_draft_digest.py
├── interfaces.py
├── models.py
├── tests/
└── docs/
```

Keep orchestration in `Dream/`. Reuse the existing Cold Draft owner and Conversation Memory interfaces instead of copying their implementation.

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
- Keep Dream execution independently switchable and manually triggered.
- Do not create a second Cold Draft writer or a competing memory-completion source of truth.

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


## Initially allowed Dream work

Within `Dream/`, Codex may:

- define `DreamRunPolicy`, `SegmentDigestResult`, and `DreamRunReport`;
- implement `DreamRunner.run_once(...)`;
- implement a `ColdDraftDigestionTask`;
- inspect the production Cold Draft schema and existing owner APIs;
- read real `pending_digest` segments through a bounded Lumina-owned interface;
- convert production-format segments into existing Conversation Memory DTOs;
- call the existing `MemoryIngestor`;
- verify durable memory completion;
- request the existing Cold Draft owner to mark a successfully ingested segment consumed;
- isolate failures per segment;
- add a bounded manual CLI or direct Python entry point;
- add focused tests using temporary production-format Draft stores;
- document state transitions, retry windows, and explicit non-features;
- minimally modify existing production modules only when required to expose narrow Cold Draft owner methods.

A normal Dream task may modify at most three existing production modules unless its task card explicitly authorizes more.

## Not authorized in the first integration milestone

Do not:

- redesign MAGMA into the M-flow multi-granularity model yet;
- add automatic Dream scheduling, autonomous consolidation, lifecycle management, background schedulers, agents, or workers;
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


## Dream boundaries

Work under `Dream/` is authorized only for the manual Cold Draft digestion milestone defined above and in `Dream/AGENTS.md`.

Do not:

- invoke Dream or MAGMA ingestion from `/api/chat`;
- make ingestion implicit after each chat turn;
- invoke Dream automatically after compaction;
- add startup hooks, background threads, schedulers, workers, cron integration, or autonomous triggers;
- add LLM-based reflection, summarization, abstraction, or consolidation;
- add duplicate merging, contradiction handling, salience mutation, forgetting, deletion, or memory rewriting;
- redesign MAGMA into the M-flow multi-granularity model;
- change the current per-turn MAGMA event granularity;
- place Dream orchestration inside `MessageRuntime`;
- directly import upstream MAGMA, NetworkX, FAISS, or embedding implementations from `Dream/`.

The normal chat path must remain independent of Dream availability, failures, and execution time.

## Change discipline

- Prefer additive changes.
- Keep production edits outside `Conversation_Memory/` minimal and localized.
- A normal integration task may modify at most three existing production modules unless its task card explicitly authorizes more.
- Add no root-level dependency without documenting why isolation inside `Conversation_Memory/` is insufficient.
- Preserve local `.env.local` and `data/` contents.
- Do not automatically commit, push, rebase, hard reset, delete branches, or rewrite history.
- Do not silently patch the upstream MAGMA checkout. Record any required patch as a separate diff or adapter decision.

## Required Conversation Memory implementation order

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


## Required Dream implementation order

1. Inspect the real Cold Draft schema and owner implementation.
2. Document the current `pending_digest` to `consumed` transition mechanism.
3. Define Dream-owned policy and report DTOs.
4. Reuse or expose a bounded pending-segment read interface.
5. Reuse or expose the authoritative consumed-transition interface.
6. Implement one-segment digestion.
7. Implement bounded deterministic `run_once`.
8. Add recovery for completed-ingestion / failed-consume.
9. Add a manual developer entry point.
10. Verify that `/api/chat` never invokes Dream.
11. Update documentation and current status truthfully.

Do not add further Dream capabilities before this chain passes.

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


For Dream, also test:

- no pending segments returns an empty successful report;
- one valid pending segment is ingested and consumed;
- multiple segments are processed in deterministic order;
- `max_segments` is enforced;
- one failed segment does not block later segments by default;
- `stop_on_error=True` stops after the first failure;
- ingestion failure leaves the segment pending;
- completed ingestion plus failed consumed transition recovers on rerun;
- duplicate Dream runs do not duplicate memory;
- malformed source data is not consumed;
- provenance and aware timestamps survive conversion;
- raw Cold Draft content remains unchanged;
- Dream does not import upstream MAGMA classes;
- outputs do not leak paths, exceptions, credentials, graph data, or raw Draft contents;
- restart recovery works;
- existing Conversation Memory and Lumina tests continue to pass.

Use temporary Draft paths and the real production Cold Draft owner against temporary files. Never commit real user Draft data.

## Documentation requirements

Maintain under `Conversation_Memory/docs/`:

- `MAGMA_BASELINE.md`: upstream revision, environment, commands, baseline behavior;
- `MAGMA_CALL_CHAIN.md`: write, graph construction, routing, traversal, and context-generation call chains;
- `INTEGRATION_DESIGN.md`: interfaces, data flow, configuration, failure behavior, and bounded recall policy;
- `PROVENANCE_AND_IDEMPOTENCY.md`: identifiers, checkpoints, retries, and consumed-state rules;
- `DECISIONS.md`: decisions, rejected alternatives, and any upstream patches required.


Maintain under `Dream/docs/`:

- `DREAM_COLD_DRAFT_DIGESTION.md`: manual trigger, exact data flow, owner interfaces, state transitions, idempotency, recovery windows, bounds, failure behavior, tests, and explicit non-features.

Update `docs/CURRENT_STATUS.md` truthfully after each accepted milestone. Do not describe planned work as complete.

## Validation

Run the existing repository validation plus focused workspace tests:

```bash
python -m pytest -q
python -m pytest Conversation_Memory/tests -q
python -m pytest Dream/tests -q
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
```

The upstream MAGMA status output must be empty.

If the workspace uses an isolated environment, also run its documented MAGMA baseline and integration test commands.

A Conversation Memory task is not complete when only imports succeed. Completion requires a synthetic Cold Draft segment to be ingested, recalled through a Lumina-owned interface, and verified without breaking the memory-disabled chat path.

A Dream task is not complete when only imports succeed. Completion requires a real production-format pending Cold Draft segment in a temporary store to be ingested through the existing Conversation Memory interface, durably completed, marked consumed through the existing Cold Draft owner, and verified without changing the synchronous chat path.
