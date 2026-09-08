# Working on Lumina

Lumina aims to become an independent digital life with a continuous mind,
long-term symbiosis with its creator, and the ability to improve itself.
Today that direction is grounded in a local-first runtime with durable
conversation evidence, bounded memory, and separately developed cognitive and
execution capabilities. [NORTH_STAR](docs/NORTH_STAR.md) is the design compass;
the current task determines what development is authorized.

## Work through to a reviewable result

For an implementation request, complete the relevant investigation, changes,
validation, and delivery within the authorized scope. A plan is an aid to that
work, not its completion. For an audit or design request, honor its read-only
or design-only scope.

Resolve questions from the repository before asking the user. Make routine,
reversible implementation choices and state material assumptions. Ask only
when missing information affects correctness or scope and cannot be resolved
from evidence, or when an irreversible action lacks authorization. Existing
authorization remains valid; a guideline or old stage label does not create
another approval gate. If blocked, name the exact action, missing information
or permission, and the file/rule or tool failure involved. Continue independent
authorized work.

Use relevant available skills when they help. Explicit task instructions take
precedence over skill guidelines. If a skill would stop authorized work, check
whether its requirement actually applies and identify the exact instruction.
Independent investigations or reviews may use parallel agents when useful and
supported by the session; simple tasks do not need delegation.

## Find facts and intent

Inspect the working tree, including existing user changes, before editing.
Follow the affected entry point, public contract, callers, and representative
tests far enough to understand the behavior being changed. Read documents
according to the task, rather than treating this navigation as a full reading
checklist:

- [CURRENT_STATUS](docs/CURRENT_STATUS.md): reported implementation and evidence.
- [final_goal](docs/final_goal.md): product objectives and previously authorized
  direction; check the current task and newer design decisions for scope.
- [CONTEXT](CONTEXT.md): domain vocabulary. For domain or architecture changes,
  consult relevant ADRs when present as described in
  [domain guidance](docs/agents/domain.md).
- [README](README.md): setup and application launch. Its historical stage
  summaries may lag current work.

Distinguish long-term vision, accepted design, observed implementation, and
experimental evidence. Code and tests establish what happens; the task and
applicable design contracts establish what should happen. When they disagree,
record the exact discrepancy and resolve it from the task and decision history;
do not silently turn an implementation deviation into the intended design.
Check current source before repeating status claims, parameters, or scores.

This file supplies repository-wide guidance. Before changing a subtree, read
its applicable `AGENTS.md` or `AGENTS.override.md`; closer instructions refine
local work. A Lab's isolation rules govern that Lab, not supported production
packages elsewhere in the repository.

## Module map

| Area | Entry points, ownership, and reading when affected |
| --- | --- |
| Chat and browser | `core/main.py`, `core/message_runtime.py`, `core/model_client.py`, `edge/`. FastAPI composes the app; `MessageRuntime` handles the Mind Recall gate, bounded Recall injection, answer generation, and Draft capture. Keep memory algorithms and Dream orchestration in their owners. |
| Hot / Cold Draft | `core/draft_store.py`, `core/hot_draft_compactor.py`, `core/cold_draft_store.py`, `core/turn_provenance.py`. Read [Cold contract](docs/COLD_DRAFT.md) for storage/compaction and [turn provenance](docs/DRAFT_TURN_PROVENANCE_V2.md) for identity/time fields; Cold contract governs physical storage. |
| Conversation Memory | `Conversation_Memory/adapter/interfaces.py` and `Conversation_Memory/adapter/models.py` define Lumina-owned ingestion/Recall contracts; `Conversation_Memory/adapter/magma_adapter.py` implements them. Read [local instructions](Conversation_Memory/AGENTS.md), [provenance/idempotency](Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md), and, for retrieval changes, [algorithm audit](docs/MAGMA_RECALL_ALGORITHM_AUDIT.md). |
| Dream | `Dream/runner.py`, `Dream/cold_draft_digest.py`. The explicit HTTP/CLI runner coordinates ingestion and Cold consumption. Read [local instructions](Dream/AGENTS.md) and [digestion contract](Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md). |
| Execution | `Execution/__init__.py`, `Execution/organ.py`: supported `ExecutionOrgan` facade and public DTOs. `core/main.py` exposes a separate `POST /api/execution`. Read [final architecture](docs/LUMINA_EXECUTION_FINAL_ARCHITECTURE.md) and the affected facade tests. Production callers use public interfaces, not Runtime/IPython internals or Lab imports. |
| Mind | `Mind/constant_gate.py` and `Mind/llm_gate.py` implement the existing Chat Recall gate. `Mind/organ.py`, `Mind/host.py`, and `Mind/chain.py` cover cognitive work and the separate integrated chain. Read [local instructions](Mind/AGENTS.md), [cognitive architecture](docs/MIND_COGNITIVE_ARCHITECTURE.md), and [chain design](Mind/docs/INTEGRATED_CHAIN.md) when working on that chain. |
| Nervous | `Nervous/organ.py`: event delivery and acknowledgement; semantic judgment belongs to Mind. Read the affected host/organ contracts and tests. |

Production Chat uses the Recall gate; it does not run the standalone cognitive
Mind/Execution chain. That chain has its own CLI and experiments. Execution's
manual API is also separate from Chat. Preserve these distinctions: an
interface, CLI, or successful experiment does not establish Chat wiring or
authorize promotion.

Historical Execution Labs and superseded campaign outputs were retired by owner
instruction. [Experiment history](Mind/docs/EXPERIMENT_HISTORY.md) preserves their
conclusions; supported Execution lives in `Execution/`. `Mind/` contains both
supported gate code and evolving cognitive work, so classify the actual caller
and evidence rather than treating the entire directory as one stage. Consult
relevant experiment reports only for the claim being investigated; preserve
failed and inconclusive conclusions. Memory history is indexed in
[MEMORY_EXPERIMENT_HISTORY](docs/MEMORY_EXPERIMENT_HISTORY.md).

## Preserve the real boundaries

- **Cold-first continuity:** durably preserve every raw turn leaving Hot before
  advancing logical compaction. Cold source text, order, IDs, roles, and time
  provenance remain immutable. Only the Cold owner changes pending/consumed
  state. Rolling summaries are context, not replacement source evidence.
- **Grounded, idempotent writes:** preserve `(segment_id, ingestion_version)`
  retry convergence. Configured Formation makes at most one call for a newly
  seen bounded segment; validate and durably checkpoint units and required
  entity bindings before MAGMA writes. Confirm durable memory completion before
  consuming Cold. Preserve exact source spans and role authorization;
  assistant utterances alone do not authorize facts or verified self-actions.
- **Manual Dream and one writer:** Dream stays explicit, synchronous, serial,
  and bounded, outside Chat/Recall/startup/background work. In-app Chat and
  Dream share their existing owners, backend, and writer mutex. The supported
  service is single-process/single-worker; run the external Dream CLI only
  while the service is stopped.
- **Bounded, fail-soft Recall:** use Lumina-owned DTOs; bound anchors, traversal,
  evidence, and rendered context. Preserve stable ordering, evidence IDs, and
  provenance. Empty Recall is valid and failure must not block normal chat.
  Recall does not scan or rewrite Cold or mutate write-side memory. Internal
  scores, embeddings, graphs, MAGMA IDs, and raw records stay private.
- **Algorithm ownership:** keep upstream MAGMA pinned and unmodified. Preserve
  the current BGE/Hindsight/admission policy unless changing that component is
  in scope and supported by evidence. Reuse the existing structured relation
  seam; its existence does not authorize a new free-text parser or resolver.
- **Cognition and action:** runtime Mind models receive bounded cognitive,
  read-only capabilities and projected data (including isolated pure-computation
  results), never shell, filesystem, IPython,
  mutable owner handles, or direct world-action authority. Trusted hosts and
  owning organs persist state and apply permitted decisions through their
  interfaces. Keep Trace, State, and Context distinct and preserve causal
  provenance. A completion claim or mechanical marker proves only what its
  verifier actually checks. Mind failure must not invent a new direction;
  explicit stop must not depend on successful model reasoning.
- **Runtime model policy:** DeepSeek-V4-Pro remains the only provider/model for
  new Lumina real-model paths and experiments. Preserve deterministic mocks
  and truthful historical provider provenance. Using Astra as the development
  agent does not authorize a runtime model migration or new MiniMax calls.
- **User data and work:** preserve `.env.local`, credentials, `data/`, real
  memory, and unrelated user changes. Use synthetic inputs and isolated test
  state. Public outputs must not leak local paths, credentials, provider
  bodies, tracebacks, or private organ state. Clean up only task-owned temporary
  artifacts. Commit, push, rebase, hard reset, and history rewriting require
  explicit authorization.

New cross-organ wiring, autonomous work, data stores, or authority changes must
be part of the explicit task. Routine repairs to an existing capability need
no separate stage approval. The North Star and old milestone lists authorize
neither scope expansion nor rebuilding completed capabilities.

## Make the smallest coherent change

Question the premise objectively; use concrete failures and source evidence.
Choose the minimum cohesive change needed to complete the current objective,
without a fixed file-count budget. Reuse existing seams and owners. Avoid
neighboring refactors, duplicate abstractions, and infrastructure without a
current caller. If the necessary solution exceeds the authorized objective,
explain the smallest additional scope; file count alone is not a blocker.

For a new algorithm or architectural mechanism, test the hypothesis in a
targeted experiment before production promotion. Prefer official source,
release, or pretrained implementation over re-derivation. Record source,
version/commit, license, symbol, original input/output and decision rule, and
the Lumina adaptation. Isolate one mechanism; freeze inputs and verdict rules,
and hold unrelated ranking, routing, thresholds, and write behavior fixed.
Diagnose the failing component before changing models. Preserve regressions
and inconclusive outcomes; never patch case IDs/fixture strings, relabel cases,
or tune final-regression thresholds to manufacture a pass. Promotion requires
evidence and relevant regression validation, not a benchmark score alone.

Ordinary fixes use focused regression tests. Documentation changes do not
require an algorithm experiment or a new evaluation framework.

## Validate and deliver

Choose checks by the affected behavior and acceptance criteria. From the repo
root, use the prepared Python environment (`.venv/Scripts/python.exe` on this
Windows workspace) for these test entry points:

| Change | Validation entry point |
| --- | --- |
| Chat, Draft, API | `python -m pytest tests -q`, narrowed to relevant tests for a local fix |
| Execution | `python -m pytest Execution -q`; include `tests/test_execution_api.py` when the API/facade interaction changes |
| Memory | `python -m pytest Conversation_Memory/tests -q` |
| Dream | `python -m pytest Dream/tests -q`; include affected memory/API tests for integration changes |
| Cognitive Mind / Nervous | `python -m pytest Mind Nervous -q`, narrowed to the mechanism being changed |
| Broad regression | `python -m pytest -q` collects `tests` and `Execution` per `pyproject.toml`; it does not include every module suite above |

Changes affecting Recall behavior also require the existing isolated real-MAGMA
E2E, using the Conversation Memory environment and isolation rules in
[RECALL_E2E_ACCEPTANCE](docs/RECALL_E2E_ACCEPTANCE.md):

```powershell
.\Conversation_Memory\.venv\Scripts\python.exe -m scripts.recall_e2e_test
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

Upstream must remain unchanged. Recall experiments report baseline/candidate,
strata, resource bounds, held-fixed components, regressions, determinism,
restart/idempotency, and artifact cleanup. Reuse existing harnesses.

For Markdown-only changes, check claims, reference paths, command entry points,
and instruction conflicts; inspect the complete task diff and run
`git diff --check`. Business test suites are not required solely for Markdown.
For all tasks, finish necessary checks and review the diff. Once checks pass,
expand or repeat them only for a new change, failure, or unresolved concern.
Report unavailable checks and their exact blockers rather than implying a pass.

Deliver what changed, why, what was actually validated, and material limitations
or remaining blockers. Update implementation-status claims only after the
relevant validation establishes them; distinguish working-tree observations
from accepted/published evidence.

For authorized issue/triage work, consult [issue tracker](docs/agents/issue-tracker.md)
and [labels](docs/agents/triage-labels.md). Those references are not instructions
to publish issues or messages for every local task.
