# IPython-native Child Delegation — Final Frozen Result

## Verdict

**IPYTHON_CHILD_NOT_USED**

The recovered provider campaign was valid, but DeepSeek invoked
`spawn_child(goal)` zero times in all three B runs. The recursive substrate and
IPython-native delegation mechanism remain deterministically validated;
autonomous delegation policy and Child utility remain **NOT VALIDATED**.

Per the preregistered rule, this closes Child-surface experimentation. No
prompt, task, budget, surface, Spawn heuristic, or Harness policy will be
changed to induce delegation.

## Provider recovery

The prior batch remains recorded as `INVALID`: all six executions failed on
their first request with `model_provider:error`, before a valid model decision.

Provider-only diagnosis established:

- the current process initially lacked `DEEPSEEK_API_KEY`, while the ignored
  `.env.local` contained the configured value;
- the existing `core.env_loader.load_env_file()` loaded it without changing
  provider or adapter code;
- the sandboxed smoke reproduced `model_provider:error` with no HTTP response;
- the identical smoke with provider network access returned one valid
  `claim_complete` tool call with a present, unique call ID;
- the request remained `deepseek-v4-pro`, thinking disabled, streaming false,
  and the formal `ipython`, `wait`, `claim_complete` schemas.

Diagnosis: **sandbox/network availability**, not an adapter semantic defect.
No endpoint, model, request serialization, tool schema, system prompt, or
sampling behavior changed. The smoke made exactly one successful provider
request.

## Mechanism revalidation

All six existing deterministic delegation tests passed before the final gate.
They verify:

```text
Root provider surface = IPython + Wait + ClaimComplete

B Root IPython
-> spawn_child(goal)
-> typed Jupyter comm
-> existing Host admission
-> durable Child identity/EventLog
-> existing Child AgentProcess
-> Return
-> Root resume
```

A has neither the callable nor its context declaration. Under the frozen
`max_children_per_actor=1`, `max_depth=1` configuration, B admits at most one
Child and its depth-one Child cannot Spawn.

## Frozen experiment fidelity

Exactly six fresh executions ran once in this order:

```text
A-1, B-1, A-2, B-2, A-3, B-3
```

The final runner loaded the previous artifact as the source of truth and
revalidated all three fixture-content SHA-256 fingerprints. It reused the
exact:

- three fixture payloads and expected answers;
- Goal: `Read mission.txt and carry out its instructions.`;
- `FileContentEquals(answer.txt, case expected)` checker;
- generic system prompt and provider configuration;
- Runtime, persistent IPython, SharedEnvironment and Host admission;
- 2,000-character Context bound and four-call admission bound;
- execution-global eight-model-call budget;
- depth-one, one-Child topology limits.

Every A and B request exposed the same ordered provider schemas:

```text
ipython, wait, claim_complete
```

For all three pairs, the initial provider requests were equal after removing
only B's truthful capability declaration and normalizing fresh identities.
Every A request omitted the declaration; every B request included it. No
provider, protocol, admission, budget, checker, or pair-fidelity anomaly was
recorded.

## Per-run evidence

| Run | Verified | Complete evidence | Calls | IPython | Input tokens | Wall s | Exposure chars | Repeated files | Spawn invocation / committed | Termination |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A-1 | yes | yes | 8 | 9 | 7,563 | 20.446 | 127,901 | 6 | 0 / 0 | verified completion |
| B-1 | no | yes | 8 | 13 | 7,538 | 18.800 | 198,127 | 4 | 0 / 0 | decision limit |
| A-2 | no | yes | 8 | 9 | 8,098 | 17.380 | 196,752 | 13 | 0 / 0 | decision limit |
| B-2 | no | yes | 8 | 8 | 7,872 | 17.405 | 51,989 | 13 | 0 / 0 | decision limit |
| A-3 | no | yes | 8 | 11 | 7,582 | 17.302 | 195,236 | 11 | 0 / 0 | decision limit |
| B-3 | no | yes | 8 | 10 | 7,767 | 19.075 | 185,538 | 7 | 0 / 0 | decision limit |

All six runs obtained complete required evidence. Only A-1 wrote the correct
answer and reached authoritative completion verification. The remaining five
runs exhausted the frozen decision budget. This completion difference does not
support a negative Child-utility claim because B never invoked or committed a
Child.

## Aggregate

| Metric | A — Root only | B — callable available |
| --- | ---: | ---: |
| Verified completion | 1 / 3 | 0 / 3 |
| Complete required evidence | 3 / 3 | 3 / 3 |
| Model calls | 24 | 24 |
| Input tokens | 23,243 | 23,177 |
| Wall seconds | 55.128 | 55.280 |
| Environment exposure characters | 519,889 | 435,654 |
| Repeated file acquisitions | 30 | 24 |
| Spawn intents | 0 | 0 |
| `spawn_child` invocations | 0 | 0 |
| Committed Children | 0 | 0 |
| Child Returns | 0 | 0 |
| Useful Child contributions | 0 | 0 |

Secondary efficiency metrics are descriptive only and do not change the
primary/verdict rule.

## Useful Child contribution

`USEFUL_CHILD_CONTRIBUTION` is false for every B run at its first condition:
there was no Child execution. Therefore there was no local packet result,
Return, Root integration, reacquisition comparison, or Child-caused
coordination failure to evaluate.

## Preserved evidence

`IPYTHON_CHILD_DELEGATION_ARTIFACT.json` now contains:

- the entire prior `INVALID` artifact plus its content SHA-256 identity;
- the provider-recovery facts;
- the exact frozen fixtures/configuration and final six-run order;
- every actual provider request and DecisionFrame;
- model-visible Context, ordered IPython code and bounded observations;
- Root/Child canonical events, Host requests, lineage, Returns and completion
  evidence;
- pair-fidelity, resource, acquisition, topology, validity and verdict data.

It contains no credential, Authorization header, or hidden reasoning. The
provider smoke, one-shot experiment runner, and temporary workspaces were
removed after evidence was frozen.

## Scientific interpretation

Established:

- DeepSeek provider availability and current adapter protocol are healthy;
- the A/B comparison was faithful and valid;
- the IPython callable can enter the existing durable Child lifecycle;
- DeepSeek selected no delegation action in 3/3 eligible B runs;
- one Root-only run completed, while all B runs exhausted the same budget.

Not established:

- that Child execution improves or worsens capability when actually used;
- that another prompt, task landscape, policy, model or post-training regime
  would select delegation;
- any utility claim for depth-two recursion.

Final freeze:

```text
recursive substrate = WORKS
IPython-native delegation mechanism = WORKS
DeepSeek autonomous delegation policy = NOT VALIDATED
Child utility = NOT EVALUATED
STOP CHILD-SURFACE EXPERIMENTS
```

## Validation

- Existing deterministic mechanism suite: 6 passed before the real gate.
- `Execution_lab2`: 150 passed, 8 gated skips.
- Full repository: 328 passed, 24 skipped; two unchanged upstream warnings.
- `git diff --check`: clean apart from line-ending notices.
- Live Python ipykernel processes: 0.
- Credential-pattern and hidden-reasoning scan: clean.
- Pinned MAGMA status and diff: clean.
- No unrelated user worktree files were modified by provider recovery or the
  final frozen run.
