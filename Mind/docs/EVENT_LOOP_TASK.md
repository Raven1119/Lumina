# D2: event-driven persistent cognition and execution

Authority: creator's `/goal`, 2026-09-05. Local branch and GitHub
`Execution_lab2` verified at `9d63da7311baa7611782cc8079fb09e9d81f4e25`.
The untracked D1 implementation, fixtures, preregistration and failure verdict
are preserved. They are part of the audit input, not a clean-tree assumption.

## Selected minimal changes

Reuse MindOrgan cognitive commit, MindTrace, Nervous durable request/result and
the qualified one-shot Directive bridge. No belief-to-Directive conversion.
One formal Intention remains immutable throughout a task. Both compared arms
receive identical evidence, event opportunities, persistent-note opportunities
and allocated model budgets. A retains current Execution message context; B
has bounded cognition/evidence. This estimates that combined context treatment,
not a separately identified pure isolation effect.

First remove D1's two practical blockers:

1. An adapter uses the existing `cognitive_step` data contract as a native
   structured return. The receiving adapter checks exactly one named block;
   `disable_parallel_tool_use` is not relied upon. Normal Mind reducers still
   validate and commit the data. NoChange and empty updates remain legal.
2. Normal Python runs in a restricted Docker IPython process, with only a
   synthetic workspace mounted. Owner logs, Mind state, evaluator and secrets
   stay on the host. There is no Python syntax whitelist. Docker absence is a
   hard environment failure; no fallback to host execution.

Codebase-design seam: one experiment entry in `Mind/event_loop.py`, one test
file, and a small optional IPython-control injection at ExecutionOrgan's
existing control seam. Default production execution remains unchanged. If
the protocol precondition passes, add only the smallest decision-boundary
advance operation needed for pending guidance to reach a still-eligible
decision while cognition may be in flight. No generic scheduler or registry.

The added execution isolation is necessary because the existing facade creates
a host kernel unconditionally; using the actual facade and ordinary Python
together requires this small cross-directory change. Model decisions and
their provider wire remain recorded by the existing Execution owner.
The existing E1 `freeze_prefix` accepts an optional control constructor so this
stage can reuse its verified fork unchanged; it does not reconstruct checkpoints.

## Validation sequence

1. Deterministic checks: normal lambda/functions/imports, persistence and errors;
   container mount/network/privilege limits; native return cardinality;
   accepted cognition across reopen; read-request/result correlation;
   actual nonempty qualified delivery; wrong/stale decision rejection.
2. Freeze a small real development protocol stage before its first call.
   Require legal outputs from both contexts and actual nonempty delivery;
   preserve all NoChange and failures, no resampling. The stage will state its
   full cases, call/token ceilings and gate in its preregistration.
3. Only if prerequisites hold, implement/freeze a multi-event workspace task
   family and a separate formal paired campaign. Triggers use observable
   changes/failures, never evaluator labels. An activation does not implicitly
   pause Execution; stale guidance is recorded instead of silently retargeted.
4. Report mechanisms, valid comparison and behavioral gains independently.
   Stop at a failed prerequisite rather than repeatedly tuning until PASS.

World Model, Builder code, emotion, endogenous goals, Chat/Memory and new Actor
topology stay outside this goal. No commit, push or production wiring.

## Sources

- Existing Lumina Mind/Execution/Nervous at the baseline above; read D1's real
  failed requests and reports. A DSML continuation and B schema/length failures
  are not NoChange; ordinary lambda rejections are experiment restrictions.
- DeepSeek's official Anthropic API compatibility table was checked:
  https://api-docs.deepseek.com/guides/anthropic_api/ . Named tool choice and
  input schemas are supported; disable_parallel_tool_use is ignored.
- Docker restrictions reuse the existing `Mind/world_model.py::_command`
  pattern only, not its World Model capability. That pattern records its
  Tycho PythonSandbox provenance at commit
  `f68912a764372ead0a610db2e1c011d41ce5197e`, Apache-2.0; existing license:
  `Mind/docs/cognition_minimal/TYCHO_LICENSE.txt`.
- Runtime base: the already installed official python image digest
  `sha256:3b3706a90cb23f04fabb0d255824f9a70ceb46177041898133dd5a35f3a50f0a`.
  IPython 9.16.1 matches this workspace's installed version (BSD-3-Clause).
  The built image ID and installed packages will be frozen with the stage.

## P0 runnable development stage

Build the runtime image once (the source digest is fixed):

```powershell
@'
FROM python@sha256:3b3706a90cb23f04fabb0d255824f9a70ceb46177041898133dd5a35f3a50f0a
RUN pip install --no-cache-dir ipython==9.16.1
ENV PYTHONDONTWRITEBYTECODE=1
'@ | docker build --tag lumina-execution-ipython:d2 -
```

The host uses the repository Python environment, including jsonschema (already
installed with the Jupyter stack). Runtime task Python runs inside Docker with
network disabled, no host credentials and only its disposable workspace mounted.
The JSON envelope is correlated per request; OS stdout/stderr are captured apart
from it. Kernel output remains task-controlled evidence, not an independent
oracle or an adversarially secure attestation. Objective files are checked by the
host. The container is closed before the host's bounded, non-link-following scan.

```powershell
./.venv/Scripts/python.exe -m Mind.event_loop register-protocol Mind/fixtures/event_loop_d2/p0
./.venv/Scripts/python.exe -m Mind.event_loop run-protocol Mind/fixtures/event_loop_d2/p0
```

Registration creates a source snapshot, hashes, immutable image ID, package list,
full cases and gate. Run refuses source changes and an already reserved stage.
P0 has two report-maintenance cases (one scope correction, one normal control),
two arms, a checkpoint review and an owner-result feedback activation per arm.
Maximum allocation: 32 provider calls / 48,000 output tokens / 30 minutes,
with at most two 1,000-token cognition calls per activity and four 2,000-token
Execution calls per arm. No retries. Actual usage is reported, not filtered.

For a proposed Directive or DecisionIntent, the development host writes an
`admission/*.pending.json` and waits up to 300 seconds. A development reviewer
reads only the proposal and abstraction rubric, then writes the matching
`.decision.json` with `request_sha256`, `allowed`, `reason`, `reviewer`.
This is an explicit experiment boundary audit, not another runtime agent or a
model correctness judge. Every episode is audited, including terminal feedback.
NoChange does not require a semantic direction check; failure remains failure.

P0 requires eight legal accepted activities, high-level output boundaries,
nonempty actual delivery in each arm type, and reopened accepted cognition in
both contexts. Objective task scores remain separate. Failure stops the next
stage without resampling. A pass authorizes designing the independent formal
multi-event campaign; it is not itself evidence of long-range behavioral value.
