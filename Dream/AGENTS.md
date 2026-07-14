# AGENTS.md

## Scope

This file applies to all work under:

```text
Lumina/Dream/
```

`Dream/` is Lumina's offline digestion and maintenance orchestration layer. It is not part of the synchronous chat request path.

The first authorized milestone is limited to a manually triggered Cold Draft digestion flow:

```text
manual Dream run
-> read real pending Cold Draft segments
-> convert each segment to Conversation Memory DTOs
-> call the existing Conversation Memory ingestion interface
-> verify durable memory completion
-> mark the source Cold Draft segment consumed through the existing Cold Draft owner
-> emit a structured run report
```

Future Dream capabilities may be added only through later explicit task authorization.

## Authoritative boundaries

Also obey:

- `Lumina/AGENTS.md`
- `Lumina/Conversation_Memory/AGENTS.md`
- `docs/COLD_DRAFT.md`
- `docs/CURRENT_STATUS.md`
- `Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md`
- `Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md`

When instructions conflict:

1. preserve the Cold-first durability invariant;
2. preserve the synchronous chat path;
3. follow the most specific instruction for `Dream/`;
4. do not weaken the Conversation Memory provenance or idempotency contract.

## Authorized objective

Implement a manual, bounded, restart-safe Dream runner that consumes real Cold Draft segments only after their raw content has already been durably preserved.

Preferred public boundary:

```python
class DreamRunner:
    def run_once(self, policy: DreamRunPolicy) -> DreamRunReport:
        ...
```

The initial run should orchestrate one task:

```text
ColdDraftDigestionTask
```

The task must:

1. obtain eligible `pending_digest` segments through a Lumina-owned Cold Draft read interface;
2. process segments in deterministic order;
3. convert each segment into the existing `Conversation_Memory` DTO schema;
4. call the existing memory ingestion interface;
5. verify that the ingestion result is durably completed;
6. request the existing Cold Draft owner to transition that segment to `consumed`;
7. record success, skip, and failure results without leaking internals.

## Preferred layout

```text
Dream/
├── AGENTS.md
├── __init__.py
├── runner.py
├── cold_draft_digest.py
├── interfaces.py
├── models.py
├── state/
├── tests/
└── docs/
```

Keep orchestration code inside `Dream/`. Reuse existing Draft and Conversation Memory interfaces instead of copying their implementation.

## Required separation of responsibilities

### Dream owns

- manual run orchestration;
- selection of eligible segments;
- deterministic ordering;
- per-run limits;
- per-segment failure isolation;
- cancellation or early-stop policy;
- structured run reports;
- coordination between Cold Draft and Conversation Memory.

### Conversation Memory owns

- schema validation for memory ingestion;
- time normalization;
- entity fallback;
- provenance projection;
- MAGMA writes;
- graph and vector persistence;
- memory-side idempotency;
- bounded recall.

### Cold Draft owner owns

- reading production Cold Draft records;
- authoritative segment state;
- transition from `pending_digest` to `consumed`;
- durable persistence of that transition.

Dream must not implement a second Draft parser, graph store, vector store, or memory idempotency system when an existing owner already provides that responsibility.

## Cold Draft rules

- Read only segments in `pending_digest` state.
- Do not read or modify Hot Draft data.
- Do not rewrite, truncate, delete, summarize in place, or replace Cold Draft source content.
- Do not edit Cold Draft JSONL files directly.
- Do not construct a second writer for Cold Draft.
- Use the existing Cold Draft owner or add one narrow owner method if required.
- Mark a segment `consumed` only after memory ingestion is durably completed.
- A skipped, failed, unavailable, partial, or timed-out ingestion must leave the segment pending.
- Re-running Dream must not duplicate memory nodes or consume an incompletely written segment.
- Preserve the original segment ID, conversation ID, turn IDs, timestamps, roles, ordering, and timezone.

## Conversation Memory boundary

Dream may depend only on Lumina-owned Conversation Memory interfaces and DTOs.

Allowed shape:

```text
MemoryIngestor.ingest(ColdDraftSegment) -> IngestionResult
```

Dream must not import or expose:

- upstream MAGMA classes;
- NetworkX objects;
- FAISS objects;
- embedding model objects;
- MAGMA node UUIDs;
- MAGMA storage paths;
- provider payloads.

Do not modify `Conversation_Memory/upstream/MAGMA/`.

## Manual execution only

For this milestone, Dream may be triggered only through an explicit developer-facing command, script, or direct Python entry point.

Allowed examples:

```text
python -m Dream.runner
python -m Dream.runner --max-segments 10
```

Not authorized:

- invocation from `/api/chat`;
- automatic execution after compaction;
- startup hooks;
- background threads;
- schedulers;
- workers;
- cron integration;
- autonomous triggering;
- model-decided Dream execution;
- public frontend controls.

The normal chat path must have identical behavior and latency whether Dream succeeds, fails, or has never run.

## Run policy and bounds

Define a bounded policy, for example:

```python
@dataclass(frozen=True)
class DreamRunPolicy:
    max_segments: int
    stop_on_error: bool = False
```

The first milestone must impose at least:

- maximum segments per run;
- deterministic segment ordering;
- no unbounded file or graph scan initiated by Dream;
- no parallel writes unless later explicitly authorized;
- one active writer assumption documented.

Do not introduce concurrency in the initial implementation.

## Run result models

Return Lumina-owned structured models, for example:

```python
@dataclass(frozen=True)
class SegmentDigestResult:
    segment_id: str
    status: str
    already_ingested: bool
    consumed: bool
    error_code: str | None

@dataclass(frozen=True)
class DreamRunReport:
    attempted: int
    ingested: int
    consumed: int
    skipped: int
    failed: int
    results: tuple[SegmentDigestResult, ...]
```

Public result objects must not contain:

- local file paths;
- raw exceptions or tracebacks;
- credentials;
- provider URLs or response bodies;
- raw Cold Draft contents;
- graph internals;
- upstream MAGMA objects.

## Failure semantics

Each segment is an independent unit of work.

Required behavior:

- one segment failure does not prevent later segments unless `stop_on_error=True`;
- memory ingestion failure leaves the segment pending;
- consumed transition failure after completed ingestion is retryable;
- rerun recognizes completed memory ingestion and retries only the consumed transition;
- corrupt Draft data returns a stable error code and is not silently discarded;
- unavailable Conversation Memory returns a stable error code;
- unexpected exceptions are converted to safe structured failures;
- no failure may break or mutate the synchronous chat path.

## Idempotency and recovery

Reuse the existing Conversation Memory idempotency key:

```text
segment_id + ingestion_version
```

Dream must support these recovery windows:

```text
memory incomplete
-> retry ingestion
-> do not consume
```

```text
memory completed
-> consumed transition failed
-> next Dream run detects completed ingestion
-> retry consumed transition only
```

```text
segment already consumed
-> skip safely
```

Dream may keep run diagnostics, but must not create a competing source of truth for memory completion or Draft state.

## Initially allowed production changes

Codex may make minimal changes outside `Dream/` only when necessary to expose narrow owner interfaces, such as:

```text
ColdDraftOwner.list_pending(limit) -> list[ColdDraftSegmentRecord]
ColdDraftOwner.mark_consumed(segment_id) -> transition result
```

Any such change must:

- preserve the current Cold-first contract;
- remain synchronous and bounded;
- avoid exposing Draft internals publicly;
- keep the existing owner as the sole writer;
- be covered by focused tests;
- modify no more than three existing production modules unless a task card explicitly authorizes more.

Do not place Dream orchestration inside the Draft owner.

## Not authorized in the first milestone

Do not add:

- LLM-based Dream reasoning;
- summaries, abstraction, consolidation, or reflection;
- duplicate merging;
- contradiction detection or resolution;
- salience updates;
- forgetting or deletion;
- memory rewriting;
- multi-granularity redesign;
- M-flow features;
- task planning;
- habit or pattern extraction;
- lifecycle management;
- autonomous agents;
- schedulers or workers;
- parallel ingestion;
- chat-time memory writing;
- chat-time Dream execution;
- changes to recall behavior;
- changes to upstream MAGMA.

The current goal is scheduling separation only: conversation turns remain MAGMA memory events, but their writes occur during a manual Dream run.

## Required implementation order

1. Inspect the real Cold Draft schema and existing owner APIs.
2. Document the exact current state transition mechanism.
3. Define Dream-owned policy and result DTOs.
4. Define or reuse a read-only pending-segment interface.
5. Define or reuse the authoritative consumed-transition interface.
6. Implement one-segment digestion.
7. Implement bounded `run_once`.
8. Add recovery for completed-ingestion / failed-consume.
9. Add a manual developer entry point.
10. Verify that `/api/chat` never invokes Dream.
11. Update current-status documentation truthfully.

## Required tests

At minimum, test:

1. no pending segments returns an empty successful report;
2. one valid pending segment is ingested and consumed;
3. multiple segments are processed in deterministic order;
4. `max_segments` is enforced;
5. one failed segment does not block later segments by default;
6. `stop_on_error=True` stops after the first failure;
7. ingestion failure leaves the segment pending;
8. partial memory failure is retryable;
9. completed ingestion plus failed consumed transition is recovered on rerun;
10. duplicate Dream runs do not duplicate memory;
11. already consumed segments are skipped;
12. malformed segment data is not consumed;
13. provenance and aware timestamps survive conversion;
14. Dream never edits raw Cold Draft content;
15. Dream never imports upstream MAGMA classes;
16. Dream output does not leak paths, exceptions, credentials, graph data, or raw Draft contents;
17. restart recovery works;
18. Conversation Memory tests continue to pass;
19. existing Lumina chat, fallback, compaction, persistence, and restart tests continue to pass;
20. upstream MAGMA remains clean.

Use temporary Draft paths and fake memory backends for failure tests. Use real production Draft owner code against temporary files. Do not use committed real user conversation data.

## Documentation

Maintain:

```text
Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md
```

It must document:

- scope;
- manual trigger;
- exact data flow;
- Draft owner interface;
- Conversation Memory interface;
- state transitions;
- idempotency and retry windows;
- bounds;
- failure behavior;
- test commands;
- explicit non-features.

Update `docs/CURRENT_STATUS.md` only after implementation and tests pass. Planned behavior must not be described as complete.

## Validation

Run:

```bash
python -m pytest -q
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
```

If Dream has focused tests, also run:

```bash
python -m pytest Dream/tests -q
python -m pytest Conversation_Memory/tests -q
```

The upstream MAGMA status output must be empty.

## Completion criteria

The milestone is complete only when:

- a real pending Cold Draft segment from a temporary production-format store can be read;
- its turns are ingested through the existing Conversation Memory interface;
- memory completion is durable;
- the segment is then marked consumed through the existing Cold Draft owner;
- reruns are idempotent;
- failure windows recover correctly;
- processing is manual and bounded;
- no chat request invokes Dream;
- no upstream MAGMA source is modified;
- all existing tests pass.
