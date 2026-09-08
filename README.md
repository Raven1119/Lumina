# Lumina

The separate Mind-Nervous-Execution chain is available through `python -m Mind`.
See [current capabilities and limits](docs/CURRENT_STATUS.md),
[start/resume usage](Mind/docs/INTEGRATED_CHAIN.md), and the
[condensed experiment history](Mind/docs/EXPERIMENT_HISTORY.md).


Lumina is a local-first conversational runtime built around a Cold-first
continuity invariant: conversation evidence must be durably preserved before it
leaves the live context.

## Current Runtime

Production chat now includes bounded long-term memory Recall:

```text
Browser -> FastAPI -> MessageRuntime
-> Mind gate (LlmMindGate by default with a real model; constant in mock)
-> source-default-on Recall
   -> query target_entity_ref classification (CURRENT_USER -> E_001, or
      exact-surface lookup over persisted EntityNodes; 0/multi hit -> None)
   -> MAGMA bounded candidates
      (+ entity-conditioned FAISS subset list for entity queries)
   -> ControlledRelationResolver when the caller supplies relation surfaces
      (UNRESOLVED or absent metadata fails open)
   -> BGE rerank ([SAME_ENTITY] per-pair projection on equal entity refs)
   -> Hindsight post-rerank score
   -> final_min_score >= 0.144
   -> bounded MemoryContext
-> ModelClient
-> Hot Draft -> Cold-first logical compaction -> Cold Draft
```

Offline memory writing remains explicit:

```text
manual Dream
-> pending Cold Draft segments
-> DeepSeek-V4-Pro Grounded Formation (non-thinking, max_tokens=2000)
-> deterministic grounding validation + bounded semantic fallback
   + self-name coverage guard
-> durable GroundedMemoryUnit checkpoint before MAGMA
-> span-grounded entity mention extraction + durable mention-binding checkpoint
-> Lumina Conversation Memory adapter
-> unmodified upstream MAGMA
-> Cold segment consumed
```

Recall is read-only with respect to memory stores and fails soft: empty or
unavailable memory does not block normal conversation.

## Current Capabilities

- same-origin browser chat, `GET /api/status`, and `POST /api/chat` (`Enter`
  sends, `Shift+Enter` inserts a newline, IME-composition Enter never sends);
- mock mode plus explicit DeepSeek Anthropic-compatible real-model mode;
- safe provider fallback;
- restart-persistent Hot Draft and Cold-first compaction;
- stable per-turn provenance with aware timestamps/timezones;
- immutable Cold source records;
- manual bounded Dream;
- dedicated DeepSeek-V4-Pro, non-thinking Grounded Formation with a 2000-token
  output budget for configured real-model Dream;
- deterministic `grounded-span-v2` projection for mock/legacy ingestion;
- a deterministic, LLM-free self-name coverage guard inside Formation:
  an omitted explicit self-identification (我叫X / 我的名字是X / 你可以叫我X)
  is restored as exactly one source-grounded identity unit through the
  unchanged strict validator, never duplicated and never widening fact
  authorization;
- pinned, unmodified upstream MAGMA;
- durable idempotent memory checkpoints;
- production Recall injection through Lumina-owned DTOs;
- keyword-enriched dense + bounded lexical anchors with RRF;
- fixed depth-1 bounded graph traversal;
- fixed `BAAI/bge-reranker-v2-m3` reranking;
- deterministic Hindsight-style recency scoring;
- inclusive production `final_min_score=0.144`;
- bounded top-3 / 5000-character historical evidence injection;
- restart/idempotency/leak-safe Recall E2E coverage;
- a Mind gate on every chat message before Recall (stage 2 `LlmMindGate`
  promoted as the real-model default, `{recall: bool}`, fail-open, audited);
- generic entity binding: `CURRENT_USER` -> `E_001` plus deterministic
  exact-surface EntityRef binding for ordinary named subjects and grounded
  mentions; graph-only non-temporal EntityNodes with one
  `REFERS_TO(role=subject)` edge per event subject and generic role-less
  `REFERS_TO` edges per additional mention; an entity-conditioned FAISS
  subset candidate channel with exact-surface query-side ref lookup
  (unique hit binds, 0 or multi misses fail open); and the `[SAME_ENTITY]`
  ranking cue on equal refs.

Recall is enabled by source default. It can be explicitly disabled before
startup:

```text
LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED=false
```

## Current Development Focus

The Memory stage is complete and has no blocking todos. In production: the
Memory MVP, Grounded Write, the Mind Recall gate stage 2 (`LlmMindGate` as the
real-model default), the generic multi-entity Entity graph (`CURRENT_USER` ->
`E_001` plus ordinary persisted entities, graph-only non-temporal EntityNodes,
subject and role-less mention `REFERS_TO` edges, entity-conditioned retrieval
with exact-surface query-side ref lookup, `[SAME_ENTITY]`), and the self-name
coverage guard.

Execution V1 is preserved as isolated experimental history at tag
`execution-organ-v1-final`; it never changed the production chat or memory
path. Execution V2 has not started and requires its own approved design task.

On the authorization-aligned 36-case development subset, raw-turn Recall scored
26/36 and Grounded Write scored 29/36. Both retained all 6 currently authorized
positive cases; Grounded Write improved negative correctness from 20/30 to
23/30. The original 60-case result remains historical evidence because 24 of
its positives depended only on assistant utterances, which are not verified
fact or self-action provenance.

`ControlledRelationResolver` is an adopted fail-open capability for structured
callers. Normal Chat does not supply relation surfaces, and this consolidation
does not add a query parser. Future Mind work must cross that caller-contract
seam explicitly.

See:

- `docs/final_goal.md` for the current product objective;
- `docs/CURRENT_STATUS.md` for implementation facts;
- `docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` for the pinned MAGMA/Lumina query-path
  audit;
- `AGENTS.md` for development constraints.

## Install and Run

Install root dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the app:

```bash
python -m uvicorn core.main:app
```

Open `http://127.0.0.1:8000/`.

The supported deployment remains single-process / single-worker. Do not use
`--reload` for the production-style local runtime when Dream and memory state
are active.

## Real Model Mode

Copy the ignored `.env.example` to `.env.local`, configure `DEEPSEEK_API_KEY`,
and restart the process after changes. DeepSeek-V4-Pro is the only supported
real model; Chat, Dream, Mind, and Execution use it through their existing
authority-specific interfaces.
Process environment values take precedence over `.env.local`.

No provider request occurs merely from importing the application. Provider
failure returns a safe fallback without exposing credentials, provider bodies,
paths, or tracebacks.

## Manual Dream

From the repository root, using the prepared Conversation Memory environment
when real MAGMA dependencies are needed:

```bash
python -m Dream.runner --max-segments 10
```

Dream processes only eligible pending segments, persists/checkpoints memory
before consuming Cold state, and converges on retry.

Dream is not run by Chat, startup, Recall, a timer, or a background worker.

## Validation

Use synthetic data and temporary paths:

```bash
python -m pytest -q
python -m pytest Conversation_Memory/tests -q
python -m pytest Dream/tests -q
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

Changes affecting real MAGMA Recall should also run the existing isolated
Recall E2E harness.

## Project Boundaries

- `docs/NORTH_STAR.md` defines long-term direction only.
- `docs/final_goal.md` defines the current product objective.
- `docs/CURRENT_STATUS.md` is authoritative for built behavior.
- `docs/COLD_DRAFT.md` defines Cold-first preservation.
- `docs/DRAFT_TURN_PROVENANCE_V2.md` defines native turn provenance.
- `docs/RECALL_E2E_ACCEPTANCE.md` defines Recall E2E behavior.
- `docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` records the current source-level
  MAGMA/Lumina query-path comparison.
- `docs/MEMORY_EXPERIMENT_HISTORY.md` consolidates rejected memory experiments
  and the evidence behind adopted boundaries.

The completed Memory stage and frozen Execution V1 do not authorize autonomous Dream,
schedulers/workers, new databases, generalized memory managers, ContextBuilder,
ToolRuntime, or unrelated organ development. New Execution work starts only
from its own explicit task card.
