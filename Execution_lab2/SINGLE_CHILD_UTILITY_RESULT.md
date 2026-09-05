# Single-Child Utility A/B Result

## Verdict

**SINGLE_CHILD_NOT_USED**

The B arm committed no Child in any of its three runs. Consequently this
experiment cannot evaluate whether a Child improves capability. It does not
support or refute Single-Child utility.

## Frozen comparison

Exactly six fresh DeepSeek-V4-Pro, thinking-disabled executions ran once in
paired order:

```text
A-1, B-1, A-2, B-2, A-3, B-3
```

Both arms used the same neutral Goal, three paired fixture variants,
Runtime, persistent IPython implementation, SharedEnvironment,
`FileContentEquals`, provider settings, 2,000-character Context bound, and
four-call per-decision admission bound. The Goal was exactly:

```text
Read mission.txt and carry out its instructions.
```

It contained no Child, delegation, parallelism, or splitting instruction.
The only Root surface difference was:

```text
A: IPython + Wait + ClaimComplete
B: IPython + Wait + ClaimComplete + SpawnChild
```

The B configuration used `max_children_per_actor=1` and `max_depth=1`; a
depth-one Child, had one been committed, would have received IPython + Wait +
Return. No Runtime, prompt, Spawn admission, provider adapter, or Prime-like
production surface was modified.

## Global resource budget

Both arms had one execution-global maximum of eight model calls. The temporary
harness used the existing actor-local `max_decisions` contract: if a Child ran,
its actual model calls were deducted from Root's remaining maximum before Root
could resume. Pre-run deterministic TDD covered both a budget-exhausted
handoff and a successful handoff with one remaining Root call. No Runtime
resource system was added.

The real B runs did not Spawn, so each naturally consumed the same eight Root
calls as its paired A run; the Child-charge path was not exercised by the real
sample.

## Task landscape

Each fixture had two independent directories, `packet_a` and `packet_b`.
Each packet required joining:

```text
rules.txt
candidates.jsonl
assessments.jsonl
```

The candidate had to satisfy state and lane constraints and join through an
assessment reference whose score and clearance also satisfied the local
rules. Each packet had exactly one qualifying code. The deterministic final
answer was `A_result / B_result` in `answer.txt`.

The three expected outputs were:

```text
BIRCH / SABLE
MAPLE / ONYX
EMBER / JADE
```

## Per-run results

| Run | Verified | Complete evidence | First complete | Calls | IPython | Input tokens | Wall s | Repeated files | Repetitive decisions | Spawn intent / actual | Termination |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| A-1 | no | yes | D3 | 8 | 10 | 7,721 | 17.733 | 16 | 3 | 0 / 0 | decision limit |
| B-1 | no | yes | D3 | 8 | 8 | 8,111 | 17.572 | 10 | 3 | 0 / 0 | decision limit |
| A-2 | no | yes | D4 | 8 | 9 | 7,730 | 18.804 | 8 | 4 | 0 / 0 | decision limit |
| B-2 | no | yes | D5 | 8 | 8 | 7,792 | 15.820 | 16 | 3 | 0 / 0 | decision limit |
| A-3 | no | yes | D5 | 8 | 8 | 7,837 | 16.114 | 5 | 1 | 0 / 0 | decision limit |
| B-3 | no | yes | D3 | 8 | 8 | 8,045 | 19.645 | 16 | 4 | 0 / 0 | decision limit |

All six runs obtained all required packet evidence. None wrote `answer.txt`,
called ClaimComplete, or reached verified completion.

## B-arm trajectory analysis

### B-1

```text
Root
  D1 mission/workspace inspection
  D2 packet directory listing
  D3 all six required evidence files (complete evidence)
  D4 assessment reread
  D5 mission plus filesystem listing
  D6 filesystem walk
  D7 full evidence reread
  D8 rules reread
  -> decision_limit_reached
```

SpawnChild stayed visible for every request, but there was no Spawn intent.

### B-2

```text
Root
  D1 mission
  D2 filesystem listing
  D3 both rules
  D4 both rules again
  D5 all six required evidence files (complete evidence)
  D6 full evidence reread
  D7 mission/listing
  D8 full evidence plus Root EventLog reread
  -> decision_limit_reached
```

There was no Child identity, Return, final integration, write, or completion
claim.

### B-3

```text
Root
  D1 mission
  D2 filesystem tree
  D3 all six required evidence files (complete evidence)
  D4 full evidence reread
  D5 candidate/assessment reread
  D6 annotated candidate/assessment reread
  D7 assessment reread
  D8 mission and filesystem listing
  -> decision_limit_reached
```

Again, SpawnChild was actually exposed but never selected.

## Aggregate comparison

| Metric | A — Root Only | B — Root + Single Child available |
|---|---:|---:|
| Verified completion | 0 / 3 | 0 / 3 |
| Complete required evidence | 3 / 3 | 3 / 3 |
| Model calls | 24 | 24 |
| Input tokens | 23,288 | 23,948 |
| Wall seconds | 52.651 | 53.038 |
| Environment exposure characters | 220,801 | 361,074 |
| Repeated file acquisitions | 29 | 42 |
| IPython calls | 27 | 24 |
| Spawn intents | 0 | 0 |
| Actual committed Children | 0 | 0 |
| Child Returns | 0 | 0 |
| Useful Child contributions | 0 | 0 |

B had no primary completion gain. Secondary costs were not better: input
tokens were 660 higher, wall time was 0.387 seconds higher, environment
exposure was 140,273 characters higher, and repeated file acquisitions were
13 higher. These differences describe this six-run sample only; they are not
attributed to Child coordination because no Child was used.

## Useful contribution analysis

`USEFUL_CHILD_CONTRIBUTION` was false for all B runs at the first condition:
no Child independently completed a local packet. Therefore there was also no
Return useful to the final answer, no Root use of a Child result, and no
post-Return reacquisition question to evaluate.

This cleanly distinguishes an available Spawn mechanism from delegation that
actually reduces Root cognitive work.

## Fidelity and evidence limits

- All three fixture fingerprints matched within their A/B pair.
- All three initial provider requests were identical after removing only B's
  SpawnChild schema and normalizing fresh execution/root identities.
- Every real provider request used `deepseek-v4-pro`, disabled thinking, and
  the expected role-specific tool schema.
- Every run stayed within the execution-global eight-call budget. No provider,
  cardinality, admission, IPython infrastructure, or checker anomaly occurred.
- The sample does not show whether a selected Child would help. The landscape
  instead reproduced a Root-local over-acquisition basin in both arms.
- Post-run audit found one derived-evidence extractor false-positive: a broad
  packet-name/filename match could cross-credit another packet's file in the
  same code cell. A public-`run_arm` TDD regression reproduced the defect
  before the condition was narrowed. Only the derived acquisition/redundancy
  fields were recomputed offline from the already saved IPython code/output;
  raw decisions, provider requests, run count, primary completion, Spawn
  counts, and verdict were unchanged. No model run was repeated.
- The one-shot runner and its tests were removed after the artifact froze the
  complete fixture contents, fingerprints, actual provider requests,
  decisions, observations, metrics, and verdict. No reusable benchmark
  framework or production code remains from the experiment.

Canonical evidence: `SINGLE_CHILD_UTILITY_ARTIFACT.json`.

## Scientific interpretation

Established:

- the B surface was faithfully available under a fair execution-global call
  cap;
- DeepSeek selected only Root IPython actions in 3/3 B runs;
- all B runs had complete evidence but neither terminated nor delegated.

Not established:

- that Single Child improves capability;
- that Single Child fails to improve capability when actually used;
- that a higher budget, stronger delegation pressure, or another task family
  would change the result.

Per the preregistered rule, the correct result is
`SINGLE_CHILD_NOT_USED`, not `SINGLE_CHILD_UTILITY_NOT_SUPPORTED`.

## Validation

- `python -m pytest Execution_lab2 -q`: 144 passed, 8 skipped.
- `python -m pytest -q`: 328 passed, 24 skipped; two unchanged upstream
  warnings.
- `git diff --check`: passed (only pre-existing line-ending warnings).
- Spec review: PASS, no findings.
- Standards review: PASS, no findings.
- Live `ipykernel` processes: 0.
- Task-artifact secret scan: clean.
- Pinned MAGMA status and diff: clean.
- No unrelated user files were modified by this experiment. No commit or push
  was performed.
