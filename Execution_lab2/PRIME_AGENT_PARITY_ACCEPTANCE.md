# Prime Agent Parity Acceptance

## Verdict

**Prime parity = UNVERIFIABLE**

The frozen comparison cannot be run faithfully from the retained repository
state. The historical task generator, answer checker, metrics, configuration,
and raw result artifacts are recoverable, but the exact Prime Agent runner is
not. The V1 final audit explicitly records that the temporary reference runner
was removed, and the evidence archive contains only the mechanical provider
hook (`config/provider-audit.ts`), not the runner source. Reconstructing a new
wrapper from the artifacts would be a new runner and would violate this
acceptance's fidelity rule.

This verdict does **not** mean that Lumina parity was reached or missed. No
current Lumina or Prime benchmark run was made, so no current success
comparison exists. Per the closure decision, the basic-execution phase ends
with parity formally unverifiable rather than with a replacement benchmark.

## Baseline identity

The only retained formal Lumina-versus-Prime comparison is Execution V1
`EXP-025`, recorded at Lumina commit `3f07d31`:

| Item | Frozen historical identity | Recoverability |
|---|---|---|
| Tasks | `large_sparse_evidence`, `multi_file_relational_evidence`, `distractor_dynamic_investigation` | Recoverable from `Execution_Lab/tests/test_phase9a_real_model_validation.py::_tasks` at `3f07d31` |
| Fixture generator | `_document` plus `_tasks` | Recoverable at `3f07d31` |
| Answer checker | `_answer_fields`, `_evidence_ids`, `_check_answer`; exact semicolon-delimited fields and evidence IDs | Recoverable at `3f07d31` |
| Lumina runner | V1 `_run_once` / maintained P1 harness | Recoverable at `3f07d31`, but it is not the current V2 Runtime |
| Prime source | `PrimeIntellect-ai/prime-agent`, commit `e319a66d7351c75abe7f040d02d9a8d6e25028e9`, package `0.8.0`, MIT | Pin and source audit recoverable; temporary checkout removed |
| Prime runner | Temporary wrapper around the pinned native harness | **Not recoverable** from Git or the archive |
| Provider | DeepSeek Anthropic-compatible endpoint, `deepseek-v4-pro`, thinking disabled, temperature 0, max output 800 | Recoverable |
| Matrix | 3 tasks x 2 independent runs x 2 systems = 12 runs | Recoverable |
| Metrics and results | `comparison.json`, `raw_comparison_runs.json`, per-run sessions/events/requests/results | Recoverable in the V1 evidence archive |

The retained archive is
`Execution_Lab/experiment_archive/execution-experiment-evidence-20260824.zip`.
Its observed SHA-256 is
`ed699b73a6738a7aa4ac1ab8eac515b19e0eb30ec83f67000d7497c72e1394b6`,
matching its manifest. The canonical historical comparison directory inside
the archive is
`Execution_Lab/artifacts/prime-agent-reference-20260824T121904910000Z/`.

Later Execution V2 source audits use Prime Agent `v0.8.1` at `5146337...` as
an implementation reference. That is not the formal comparison pin and was
not substituted for `EXP-025`.

## Environment and configuration fidelity

The historical manifest preserves:

- model `deepseek-v4-pro` through the Anthropic-compatible DeepSeek endpoint;
- thinking disabled, temperature 0, and `max_tokens=800`;
- Prime's unchanged source, native prompt, IPython/RLM behavior, and depth-one
  recursion;
- an authorized provider-only hook forcing request fields and collecting
  telemetry;
- 600 seconds per run, a 12-request Root bound, an 80,000-token Prime bound,
  and two runs per task per system.

It also records that Prime's default Windows kernel bootstrap required the
documented Lab-local kernel-Python seam. That environment fact is preserved in
the artifacts.

Fidelity is nevertheless insufficient for a rerun:

1. `Execution_Lab/.reference/prime-agent/` was intentionally removed.
   Re-cloning the exact upstream commit would be mechanical and possible, but
   would not restore the missing experiment driver.
2. `Execution_Lab/V1_FINAL_EVIDENCE_AUDIT.md` states: "Prime Agent reference
   runner | temporary runner removed".
3. Archive enumeration finds `config/provider-audit.ts` as the only
   JavaScript/TypeScript/Python source inside the historical comparison
   artifact. It contains no orchestration runner.
4. The old task card and result artifacts describe behavior and bounds, but
   they are not executable source and cannot prove that a reconstructed
   wrapper is identical in process lifecycle, continuation injection,
   shutdown, timeout, telemetry, or final-answer collection.

Consequently neither a Prime rerun nor a current-Lumina-only comparison against
stale Prime outcomes would meet the requested same-runner acceptance.

## EXP-025 runner recovery audit

Recovery required all three identities for the original runner file:

1. a Git commit containing it;
2. its path in that commit;
3. its exact content/blob identity.

The repository and retained archive do not satisfy that threshold:

- All reachable branches, tags, and historical objects were searched by path
  and by EXP-025/Prime runner signatures. The only retained runner-related
  source record is `Execution_Lab/PRIME_AGENT_REFERENCE_SOURCE.md`, added at
  `3f07d31`; it documents the upstream pin and removed temporary checkout, not
  the experiment runner body.
- Reflogs contain no additional runner commit. `git fsck --full
  --unreachable --no-reflogs` reports no unreachable or dangling commit.
- All 2,302 unreachable blobs were inspected by content signature. The 20
  Prime-related matches are reports, result JSON, the source-audit draft, or
  the evidence ZIP itself; none is runner source. Even an isolated blob would
  still lack the required commit/path identity.
- The retained ZIP has SHA-256
  `ed699b73a6738a7aa4ac1ab8eac515b19e0eb30ec83f67000d7497c72e1394b6`.
  Its canonical Prime comparison subtree contains 315 files. The only source
  file is `config/provider-audit.ts` (834 bytes, SHA-256
  `4978b187865266680820baecb50ae98148f3db1f0e2c53ac05de20b7ef9624fd`),
  which is the provider telemetry/configuration hook, not the runner. There
  are no hidden or executable files. The sole extensionless file is a
  non-executable supervisor configuration. Files named `runner_error.json` or
  `runner_result.json` are result records, not source.
- The contemporaneous V1 final audit explicitly says: `Prime Agent reference
  runner | temporary runner removed`.

Therefore commit/path/content identity cannot be proven. Reconstructing a
wrapper from telemetry hooks, result artifacts, or current code is explicitly
excluded. The frozen 12-run suite was not executed; provider calls made by
this recovery audit: **0**.

## Current Lumina surface check

The current formal surface was checked deterministically without a provider
call:

| Actor | DecisionFrame capabilities | Actual provider tool schemas |
|---|---|---|
| Root | `ipython`, `wait`, `spawn_child`, `claim_complete` | the same four schemas (wire order: `ipython`, `wait`, `claim_complete`, `spawn_child`) |
| depth-one Child | `ipython`, `wait`, `spawn_child`, `return` | the same four schemas |
| depth-two Child | `ipython`, `wait`, `return` | the same three schemas; Spawn is mechanically absent at the depth bound |

No model-facing `read`, `write`, or `shell` schema appears in these requests.
The IPython capability check also confirms filesystem reads/writes, directory
inspection, multi-file aggregation, and subprocess execution remain available
through the shared workspace.

Evidence: four deterministic cases passed in
`test_default_root_surface_is_prime_like_in_frame_and_provider_request`,
`test_default_child_surface_is_prime_like_and_respects_depth_admission` (two
depths), and `test_ipython_retains_workspace_and_process_capabilities`.

This proves surface/configuration shape, not basic-execution parity.

## Per-task comparison

No acceptance runs were executed. The table below preserves the old `EXP-025`
results only; it must not be read as current V2 performance.

| Frozen task | Historical Lumina V1 success | Historical Prime 0.8.0 success | Current Lumina | Acceptance use |
|---|---:|---:|---:|---|
| Large sparse evidence | 0/2 | 0/2 | not run | Identity/fixture evidence only |
| Multi-file relational evidence | 0/2 | 0/2 | not run | Identity/fixture evidence only |
| Distractor-heavy dynamic investigation | 2/2 | 0/2 | not run | Identity/fixture evidence only |
| **Aggregate** | **2/6** | **0/6** | **not run** | Cannot decide current parity |

Five of six historical runs on each system recovered the non-evidence domain
facts semantically. Prime's exact checker score was
0/6 because its terminal prose never used the required semicolon
`field=value` grammar. That old completion-policy mismatch is a material
historical limitation; it is not evidence about the current Lumina
`FileContentEquals` completion transition.

## Aggregate historical metrics

These are retained context, not new acceptance measurements:

| Metric | Historical Lumina V1 | Historical Prime 0.8.0 |
|---|---:|---:|
| Verified task success / exact checker | 2/6 | 0/6 |
| Semantic fact success | 5/6 | 5/6 |
| Mean required-evidence completeness | 0.722 | 0.000 (exact-format parser result) |
| Model calls | 28 | 69 |
| Provider input tokens | 164,814 | 1,061,480 |
| Tool/program executions | 47 | 45 |
| Wall time total | 99.552 s | 353.632 s |
| Full-file exposures | 15 | 22 |
| Child / delegation use | 0 / 0 | 0 / 0 |

Environment exposure is not merged into one cross-system score: Lumina
recorded model-visible ToolResult bytes, while Prime recorded REPL stdout plus
directly injected payload. The historical comparison reports the definitions
separately. It also reports repeated Prime verification/finalization after the
facts were already known, but does not provide a single normalized redundant-
acquisition count suitable for this acceptance.

## Failure classification

There is no current Lumina failure to classify as missing capability,
execution-policy failure, Runtime/admission failure, IPython failure, or
completion-transition failure. The acceptance failed before sampling because
the comparison apparatus identity is incomplete.

The historical Prime exact-checker failures were primarily an execution-policy
and termination-contract mismatch: the agent found the facts, continued under
the autonomous continuation policy, and returned prose instead of the frozen
machine grammar. That observation belongs to `EXP-025`; it is not promoted to
a present-system diagnosis.

## Evidence limits

- Historical artifacts prove what ran on 2026-08-24, not how the current V2
  surface performs on those fixtures.
- Current deterministic tests prove actual exposed schemas and IPython
  capability, not real-provider task success, efficiency, or stopping quality.
- Reusing historical Prime results against fresh Lumina samples would combine
  different sampling times and a non-reproducible Prime wrapper.
- Rewriting the old tasks into new `CompletionSpec` fixtures or recreating the
  Prime wrapper would be new acceptance apparatus, which this task forbids.
- No provider request, benchmark task, prompt, surface override, decision
  budget, cardinality, or Runtime behavior was changed or exercised.

## Next-stage gate

The basic-execution phase is **CLOSED**. Prime parity remains
**UNVERIFIABLE**, while the promoted Prime-like execution surface retains its
direct A/B support and established implementation evidence. No replacement
parity benchmark will be designed.

The next research stage is **Child / recursive AgentProcess utility**. Its
question is whether dynamic Child topology produces actual capability gains
over the established single-Root execution baseline; this audit does not begin
that experiment.

## Validation and repository state

- Current-surface checks: `4 passed` (Root wire request, depth-one/depth-two
  Child wire requests, and shared-workspace IPython capabilities).
- `python -m pytest Execution_lab2 -q`: `144 passed, 8 skipped`.
- `python -m pytest -q`: `328 passed, 24 skipped, 2 upstream warnings`.
- `git diff --check`: PASS; existing LF-to-CRLF notices only.
- Live Python processes with an `ipykernel` command line: 0.
- Secret scan for token-shaped `sk-...` and `Bearer ...` values under
  `Execution_lab2`: no matches.
- `Conversation_Memory/upstream/MAGMA` status and diff: clean. Its status check
  emitted only an inaccessible global-ignore warning.
- Real provider / benchmark calls made by this acceptance: 0.
- `CURRENT_STATUS.md` records the formal unverifiable closure and next-stage
  gate.
- This task changed only Execution audit/status documentation and made no
  commit or push. The repository was already dirty with earlier
  Execution experiment/consolidation changes and unrelated Canvas/docs user
  changes; those pre-existing files were left untouched.
