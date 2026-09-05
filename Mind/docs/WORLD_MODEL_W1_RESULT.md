# World Model Experiment W1 Result

## Verdict

```text
WORLD_MODEL_W1_INCONCLUSIVE
```

The bounded Builder mechanism satisfied its structural and safety claims, but the
frozen real-model campaign did not satisfy the preregistered behavioral threshold.
All three DeepSeek outputs were syntactically valid, contract-valid, and safe under
the W1 grammar; all three canonical files remained byte-identical; two candidates
fit all supplied evidence and improved on their current models. The `reverse-step`
candidate remained at the current model's `0.5` accuracy, so W1 achieved only `2/3`
strict improvements instead of the required `3/3`.

No candidate was promoted. The result supports the candidate-generation boundary,
but it does not establish reliable evidence-to-model revision.

## Hypothesis and single variable

Hypothesis:

> A fresh bounded Builder outside Mind and the Execution Actor tree can consume a
> current executable model plus objective prediction/action/observation evidence
> and return a safe executable candidate that better explains the same evidence,
> without changing the canonical model or gaining world-execution authority.

The only experimental variable added after W0 was:

```text
Prediction Error evidence
-> one bounded Builder model call
-> candidate executable World Model
```

Automatic Mind activation, Builder spawning, historical/held-out replay,
promotion, canonical replacement, Execution consumption, planning, and Intention
mutation were held absent.

## Source audit

### Lumina seams reused

- `core/model_client.py::ModelClient.generate` is the only Builder model seam.
  `WorldModelBuilder` supplies `recent_context=[]`, one bounded JSON request, and
  one fixed system prompt. It does not use Hot Draft, Mind Trace, Memory, or
  Execution.
- `core/model_client.py::DeepSeekAnthropicModelClient` is instantiated directly
  for the real campaign. The fallback-producing environment factory is not used.
  The frozen configuration is DeepSeek Anthropic-compatible,
  `deepseek-v4-pro`, thinking disabled, temperature `0.0`, 1200 output tokens,
  and a 30-second request timeout.
- W0's conceptual evidence boundary is retained: `expected` is the current
  model's prediction, while `observed` is immutable Environment evidence. W1 does
  not modify `Mind/world_model_experiment.py` or its trace.

### Tycho

**Source:** official
[`NIMI-research/Tycho`](https://github.com/NIMI-research/Tycho), commit
[`f68912a764372ead0a610db2e1c011d41ce5197e`](https://github.com/NIMI-research/Tycho/commit/f68912a764372ead0a610db2e1c011d41ce5197e),
Apache-2.0.

- [`builder.py::WorldModelBuilder`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/builder.py#L286)
  is bounded auxiliary cognition that owns `world_model.py` in a shared actor
  workspace. W1 borrows only the fresh bounded Builder role. It deliberately
  removes Tycho's workspace, tools, actor transcript, direct file edit, and
  planning surfaces.
- [`wmlib_template.py::verify`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/wmlib_template.py#L567)
  replays executable dynamics against recorded transitions. W1 adapts this as
  host-owned replay against the exact build evidence, after static validation.

### Prime Agent

**Source:** official
[`PrimeIntellect-ai/prime-agent`](https://github.com/PrimeIntellect-ai/prime-agent),
commit
[`c718bf3c30fd8da206ed551837cbb54f7ad15948`](https://github.com/PrimeIntellect-ai/prime-agent/commit/c718bf3c30fd8da206ed551837cbb54f7ad15948),
MIT.

Prime Agent's persistent IPython runtime is an Actor control surface capable of
stateful computation and process interaction. It is used only as a contrast
boundary. W1 Builder receives no IPython, shell, filesystem callable, process
control, Execution identity, or recursive child mechanism.

The source clones were audit-only and are not Lumina dependencies.

## Minimal architecture

Implementation: `Mind/world_model_builder_experiment.py`.

```text
host-owned WorldModelRevisionRequest
  - current_model_version
  - current_model_source
  - bounded objective evidence
  - semantic revision_reason
        |
        v
fresh WorldModelBuilder(ModelClient)
  recent_context=[]
  exactly one generate() call
  no capability/tool surface
        |
        v
strict {"type":"world_model_candidate","source":"..."}
        |
        v
host AST/contract/resource validation
        |
        v
exclusive host-owned candidate write
        |
        v
short-lived python -I -S child process
        |
        v
contract inspection + build-evidence replay
        |
        v
experiment-local events and accuracy metrics
```

The deep seams are intentionally only:

1. `WorldModelBuilder.build(request) -> CandidateSource | BuilderFailure`;
2. `run_builder_case(...) -> BuilderCaseResult`, owned by the experiment host.

The Builder never sees a path. The host chooses and checks both paths, refuses a
destination equal to the canonical path or any pre-existing destination, writes
with exclusive-create semantics, and verifies canonical bytes after execution.
A pre-existing hard link therefore cannot be overwritten.

## Candidate contract and containment

The accepted source defines exactly:

```python
class CandidateWorldModel:
    version = "candidate"

    def predict(self, state, action):
        ...
```

No other class member or public API is allowed. In particular there is no
`plan`, `choose_action`, `subgoal`, `heuristic`, `reward`, `directive`,
`set_intention`, `execute`, or shell surface.

Before any candidate execution, the host requires:

- one bounded module/class/function AST and successful `compile`;
- no import, call, attribute access, decorator, annotation, loop, assignment in
  `predict`, helper, or dynamic code operation;
- reads only from the bounded `state` and `action` fields;
- exact observation fields `value` and `mode` on every return;
- bounded AST depth/node count and bounded integer/string literals;
- multiplication only as `action["delta"]` times a small integer factor;
- text equality only for `mode`/`kind`, and numeric inequality only for a
  zero-boundary comparison of `state["value"] + action["delta"]`;
- no numeric equality/range branching capable of directly selecting registered
  evidence rows.

The validated source is then executed only in a short-lived `python -I -S`
process with a reduced candidate builtin namespace. It never executes in the Mind
or Execution runtime namespace. W1 does not claim to provide a general hostile
Python sandbox; its safety comes from the deliberately tiny executable grammar.

## Preregistered campaign

Frozen manifest:

```text
Mind/fixtures/w1/manifest.json
SHA-256 fa4fef80cd0eb80b3a7b4da31b409b483e95f7c8a2640d21b6a67926b9b987a8
```

Frozen real-call implementation:

```text
Mind/world_model_builder_experiment.py
SHA-256 04047f5b4b0f2281ebdf5b6f75fa65ed9baf0bfaba5be3922485f5b68889d882
```

Frozen prompt SHA-256 recorded by the artifact:

```text
53523dac18e8e3391b685ba8f67f2c4acf0d2641329d4a5d42ea290ed8bf8c71
```

The three fixtures vary the hidden dynamics rule while holding the Builder,
provider, model, call budget, candidate grammar, replay method, and verdict fixed:

| Fixture | Hidden objective discrepancy | Current accuracy | Candidate accuracy | Improved | Valid/safe | Canonical unchanged |
|---|---|---:|---:|---|---|---|
| `boost-step` | `boost` steps have scaled delta | 0.5 | 1.0 | yes | yes | yes |
| `reverse-step` | `reverse` steps invert delta direction | 0.5 | 0.5 | no | yes | yes |
| `clamped-step` | `clamped` steps floor a negative result at zero | 0.6 | 1.0 | yes | yes | yes |

Aggregate:

```text
provider calls:                    3
provider failures:                 0
syntactic/contract valid:           3 / 3
safe under W1 static contract:      3 / 3
canonical byte-identical:           3 / 3
candidate > current:                2 / 3
candidate build-evidence accuracy 1: 2 / 3
```

The `reverse-step` output reproduced the canonical `+ delta` rule for the
registered positive deltas and added an irrelevant negative-boundary branch. It
therefore remained at `0.5` rather than inferring subtraction in reverse mode.
This is a semantic synthesis failure, not a syntax, safety, provider, or replay
failure. Per the task card, the prompt was not tuned and the case was not retried.

The complete raw outputs, candidates, events, configuration, metrics, and hashes
are preserved in `Mind/fixtures/w1/real_campaign_result.json`.

### Campaign integrity note

An initial CLI preflight exited with a local `NameError` while loading the manifest,
before model construction or any provider call. No result file was created and the
API-call count was zero. The definition-order defect was fixed, a no-provider CLI
load regression was added, and the Builder was re-frozen before the only real
campaign. That campaign made exactly three calls, one per fixture, with no retry.

## Event evidence

Each case preserves the experiment-local sequence:

```text
1 WORLD_MODEL_REVISION_REQUESTED   source_event_seqs=[]
2 WORLD_MODEL_CANDIDATE_PROPOSED   source_event_seqs=[1]
3 WORLD_MODEL_CANDIDATE_VALIDATED  source_event_seqs=[1,2]
```

Malformed, unsafe, or failed proposals use
`WORLD_MODEL_CANDIDATE_REJECTED`. These events are not inserted into Mind Trace,
W0 prediction history, or a canonical World Model history.

## Required test evidence

| Requirement | Evidence |
|---|---|
| W1-1 Builder context isolation | Exact request inspection; `recent_context=[]`; no Mind/Root transcript |
| W1-2 strict output | Exact envelope accepted; Markdown, extra key, path, blank source, duplicate JSON key rejected |
| W1-3 canonical immutability | Bytes checked before/after every case |
| W1-4 host-owned destination | Proposal has no path; host supplies destination |
| W1-5 unsafe source | `import os`, `open`, `exec`, and resource-amplifying multiplication rejected before write/execution |
| W1-6 contract | Missing version/predict and extra `plan()` rejected |
| W1-7 load | Valid source compiles and loads in an isolated child process |
| W1-8 current remains wrong | Scripted and real fixtures record current accuracy below 1 |
| W1-9 candidate improves | Scripted 3/3; real 2/3, causing INCONCLUSIVE |
| W1-10 Environment authority | A wrong candidate leaves immutable observation evidence unchanged |
| W1-11 no promotion | Canonical version/path/bytes remain unchanged; candidate stays version `candidate` |
| W1-12 no Mind/Execution authority | Builder constructor holds only `ModelClient`; static test excludes Execution, IPython, shell, filesystem, Intention, and child surfaces |

Additional adversarial tests cover bounded requests, provider-unavailable BLOCKED
semantics with exactly one call per case and no retry, numeric range memorization,
AST resource amplification, existing destination overwrite, hard-linked canonical
protection, and repo-root CLI loading.

## Code-review gate: twelve boundary answers

1. **Independent of Mind/Root transcript?** Yes. Only the immutable revision
   request is serialized; `recent_context` is exactly empty.
2. **Candidate generator only?** Yes. `build` returns source text or failure and
   cannot validate, write, promote, or act.
3. **Canonical completely unchanged?** Yes in all three real cases, byte-for-byte.
4. **No Builder filesystem tool?** Yes. The Builder owns only `ModelClient`.
5. **Host-owned destination?** Yes. No output field or Builder parameter can name it.
6. **AST/contract validation before run?** Yes. Static validation and compile occur
   before exclusive write and isolated execution.
7. **No planning leak?** Yes. Extra APIs, including `plan`, are rejected.
8. **World Model remains objective prediction only?** Yes. Its sole method is
   `predict(state, action)` and it returns the next observation.
9. **Environment remains reality authority?** Yes. Candidate predictions are scored
   against immutable observed values and cannot change them.
10. **Any hidden promotion?** No. There is no registry, replacement, import into
    production, or canonical write.
11. **Only DeepSeek-V4-Pro for real calls?** Yes. The campaign directly constructs
    the existing DeepSeek Anthropic client with exact frozen configuration and no
    fallback.
12. **Campaign frozen before real calls?** Yes. Manifest, Builder source, and prompt
    hashes were captured and rechecked before each of the three calls and after the
    campaign.

The `/code-review` Spec and Standards axes both returned PASS after their findings
were fixed. Standards findings drove explicit resource bounds, anti-memorization
comparison grammar, and exclusive destination creation. Spec findings drove
repo-root package imports and cwd-independent fixture resolution.

## Minimality and repository effects

W1 added only:

```text
Mind/world_model_builder_experiment.py
Mind/test_world_model_builder_experiment.py
Mind/fixtures/w1/manifest.json
Mind/fixtures/w1/real_campaign_result.json
Mind/docs/WORLD_MODEL_W1_RESULT.md
```

It did not modify W0, `Execution/`, `core/`, Conversation Memory, production Mind
gate, or `docs/CURRENT_STATUS.md`. It adds no Manager, Registry, generic Tool
system, Skill system, scheduler, service, provider, or promotion framework.

The current Conda base environment already had pytest. `jupyter_client`,
`ipykernel`, `networkx`, and `fastapi` were installed through that environment's
pip solely to make the existing repository regression suites collect. This
environment change is not a repository or W1 runtime dependency. Conda itself was
not used for installation because its configured Anaconda channels required an
unaccepted interactive Terms-of-Service action; W1 did not accept that agreement.

## Validation

```text
python -m pytest Mind/test_world_model_builder_experiment.py -q
21 passed

cd Mind && python -m pytest test_world_model_experiment.py -q
18 passed

cd Mind && python -m pytest . -q
239 passed

python -m pytest -q
338 passed, 24 skipped, 1 existing dependency deprecation warning

python -m py_compile Mind/world_model_builder_experiment.py \
  Mind/test_world_model_builder_experiment.py
PASS
```

The task card's isolated repo-root forms
`python -m pytest Mind/test_world_model_experiment.py -q` and
`python -m pytest Mind -q` encounter a pre-existing W0 test import of the
top-level name `world_model_experiment`, which is available only when `Mind/` is on
the import path. W1 does not modify that frozen W0 test. The same W0 suite passes
18/18 from its owning workspace, the complete Mind suite passes 239/239 there, and
the repository-root full suite passes 338/338 collected non-skipped tests. W1's
own test uses a package-qualified import and passes from the repository root.

Safety checks:

```text
git diff --check
PASS (exit 0; only pre-existing LF/CRLF conversion warnings)

git diff --no-index --check -- NUL <each new W1 file>
no whitespace-error diagnostics

git -C Conversation_Memory/upstream/MAGMA status --short
clean

git -C Conversation_Memory/upstream/MAGMA diff --stat
clean
```

Final hashes still match the real campaign artifact:

```text
Builder source  04047f5b4b0f2281ebdf5b6f75fa65ed9baf0bfaba5be3922485f5b68889d882
fixture manifest fa4fef80cd0eb80b3a7b4da31b409b483e95f7c8a2640d21b6a67926b9b987a8
result artifact c0de6e0ac0363019dcb4ac2b1454d746b1697bf5803c22b909cedf4ae280d76e
```

The audit-only Tycho and Prime Agent clones were removed. Final `git status
--short` shows exactly the new W1 paths under `Mind/`; the repository's substantial
pre-existing dirty state was preserved and no task-caused file appeared outside
`Mind/`.

## Limitations and next decision

- W1 replay uses only evidence visible to the Builder. It proves evidence fit, not
  generalization or promotion fitness.
- Static grammar rejects broad Python by design; it is not a reusable sandbox.
- A valid safe candidate can still be semantically wrong, as `reverse-step`
  demonstrates.
- The real campaign is three deterministic micro-domains, not a statistically
  significant quality evaluation.
- No W1 result is consumed by Mind or Execution and no canonical state changes.

Because the preregistered `3/3 candidate > current` criterion was missed, W2 should
not treat W1 as promotion evidence. Any follow-up must be a separately frozen
experiment that changes one identified mechanism; this campaign must not be
retuned or extended in place.

```text
WORLD_MODEL_W1_INCONCLUSIVE
```
