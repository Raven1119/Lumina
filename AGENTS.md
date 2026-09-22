# Working on Lumina

Lumina aims at a continuous mind, independent judgment and long-term symbiosis
with its creator. [NORTH_STAR](docs/NORTH_STAR.md) supplies direction;
[CURRENT_STATUS](docs/CURRENT_STATUS.md) supplies current facts. The current
task supplies authorization. Historical milestones are evidence, not new gates.

For runtime platform, Codex process placement, or development-environment work,
follow the Noespire platform decision in `docs/final_goal.md#target-platform`.

## Understand, then finish the authorized work

Inspect HEAD, the working tree and applicable local instructions before editing.
Follow affected entry points, contracts, callers and representative tests.
Distinguish intended architecture, current implementation and experimental
evidence; resolve disagreements using source and the owner's latest task.
Use judgment and relevant skills, including design/review when helpful.
Independent investigations may use parallel agents when useful.

Complete investigation, implementation, validation and delivery for an
implementation request. Resolve ordinary reversible choices yourself.
Ask only when missing information affects correctness or scope, or an
irreversible action lacks authorization. Explain exact blockers and continue
unaffected work. Existing authorization persists.

Choose the smallest coherent change, without an arbitrary file-count limit.
Reuse owners and existing seams; avoid new managers, duplicate stores and
speculative infrastructure. Ordinary refactors and bug fixes need focused
regressions, not another research campaign. A new algorithm or architectural
capability needs targeted evidence before promotion: freeze relevant conditions,
retain failures and distinguish mechanism from behavioral benefit.

Start with the [code map](README.md) and [scheme catalog](docs/EXPERIMENTS.md).
Detailed contracts below support these two navigation entries.

## Current map and ownership

| Area | Current entry and detailed facts |
| --- | --- |
| Cognitive chain | `python -m Mind`; [chain contract](Mind/docs/INTEGRATED_CHAIN.md), [Mind instructions](Mind/AGENTS.md). Mind owns judgment and persistent cognition, Nervous owns durable transport and mechanical continuation, Execution owns action lifecycle and environment evidence. No central Session/Host owns the loop. |
| Mind | `Mind/organ.py` handles events; `cognition.py` owns atomic cognitive commits and `trace.py` their history. `analysis.py` owns optional isolated analysis. [Cognitive architecture](docs/MIND_COGNITIVE_ARCHITECTURE.md). |
| Nervous | `Nervous/organ.py` owns mailboxes, acknowledgements and foreground delivery; `provider.py` accounts shared bounded provider calls. [Event contract](docs/NERVOUS_EVENT_FOUNDATION.md). |
| Execution | `Execution/runtime.py` is the event-facing execution owner; `Execution/organ.py` remains the supported single-run facade. `core/main.py` separately exposes `POST /api/execution`. [Execution architecture](docs/LUMINA_EXECUTION_FINAL_ARCHITECTURE.md). |
| Production Chat | `core/main.py`, `message_runtime.py`, `model_client.py`, `edge/`. MessageRuntime runs the Recall gate, bounded Recall, answer generation and Draft capture. It does not run the cognitive chain. |
| Draft | `draft_store.py`, `hot_draft_compactor.py`, `cold_draft_store.py`, `turn_provenance.py`. [Cold contract](docs/COLD_DRAFT.md), [turn provenance](docs/DRAFT_TURN_PROVENANCE_V2.md). |
| Memory | Lumina-owned DTOs in `Conversation_Memory/adapter/`; pinned upstream MAGMA. Read [local instructions](Conversation_Memory/AGENTS.md), [provenance](Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md) and affected algorithm contracts. |
| Dream | Explicit serial runner `Dream/runner.py`; [local instructions](Dream/AGENTS.md), [digestion contract](Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md). |

For domain changes consult [CONTEXT](CONTEXT.md) and [domain guidance](docs/agents/domain.md).
For opt-in pursuit, serial Task versions, attention selection, owner queries or
Watch changes, read the [Stage1 runtime contract](Mind/docs/INTENTION_STAGE1.md).
Historical results are condensed in [Mind history](Mind/docs/EXPERIMENT_HISTORY.md)
and [Memory history](docs/MEMORY_EXPERIMENT_HISTORY.md). Retired campaigns and
version compatibility are not runtime dependencies or tests to resurrect.

## Preserve the boundaries

- Mind receives bounded read-only data and analysis results; no business
  filesystem, shell, IPython, mutable Execution handles or direct action authority.
  Execution chooses implementation. Nervous transports; it does not reason.
  Trace, accepted State and projected Context remain distinct.
  Recovery and working-context changes follow the [working-context design](docs/RECOVERY_AND_WORKING_CONTEXT_DESIGN.md).
  `working_context.py` derives owner-local summaries; current authority and original
  history stay with each organ. Baseline is default; summary/masking are explicit options.
- Original statements, model judgments, calculations and reality observations
  retain their provenance. A valid citation, delivered Directive or completion
  marker establishes only what was actually checked. Failures remain failures.
  Unknown action outcomes cannot be blindly replayed.
- Cold preserves each departing raw Hot turn before logical compaction advances.
  Source text, IDs, order, roles and times remain immutable. Only Cold's owner
  changes pending/consumed state; summaries do not replace source evidence.
- Memory writes converge on `(segment_id, ingestion_version)`. Checkpoint
  grounded units and entity bindings before MAGMA writes; consume Cold only
  after durable ingestion. Assistant text does not authorize facts/self-actions.
  Keep upstream MAGMA pinned and unchanged.
- Recall is bounded, read-only and fail-soft. Preserve stable provenance and
  the configured algorithm unless that component is explicitly in scope.
  Dream remains explicit, synchronous and serial, sharing the service writer
  mutex; external Dream CLI runs with the single-worker service stopped.
- DeepSeek-V4-Pro remains the runtime model/provider policy. Deterministic mocks
  and truthful historical provenance remain supported. Astra's development
  role does not authorize a runtime model migration.
- Preserve credentials, `.env.local`, `data/`, real memory and unrelated changes.
  Use isolated test state. Public outputs must not leak private runtime bodies,
  paths, credentials or tracebacks. Clean only authorized/task-owned artifacts.
  Commit, push, rebase, reset and history rewriting require explicit authorization.

New organ wiring, autonomous capabilities or authority changes must be in the
task. An old stage label does not block ordinary repairs or already authorized
integration. Do not silently connect the separate cognitive chain to Chat.

## Validate and report

Development runs in Linux using Codex. Use the prepared Linux virtual environment
(e.g. `Conversation_Memory/.venv/bin/python` for Memory dependencies); Windows
application checks use that environment's `Scripts/python.exe`. Do not download
legacy BGE weights merely to repeat already recorded experiments.

| Affected behavior | Validation |
| --- | --- |
| Cognitive loop | `python -m pytest Mind Nervous Execution -q` |
| Chat/API/Draft | `python -m pytest tests -q`, narrowed for local changes |
| Manual Execution API | Include `tests/test_execution_api.py` |
| Memory / Dream | Their local instructions and dedicated suites |
| Whole maintained tree | `python -m pytest -q`; see `pyproject.toml` collection |

Recall changes additionally require the isolated real-MAGMA
[acceptance](docs/RECALL_E2E_ACCEPTANCE.md) in the Memory environment and an
unchanged upstream check. Markdown-only changes need claim/link/command review
and `git diff --check`, not business experiments.

Review the complete task diff and run relevant checks. Expand testing only for
a new change, failure or unresolved concern. Report what changed, why, actual
validation and remaining limitations; test counts do not prove semantic ability.
For authorized issue work use [tracker guidance](docs/agents/issue-tracker.md).
