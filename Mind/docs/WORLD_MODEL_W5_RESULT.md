# World Model Experiment W5 Result

## Verdict

```text
W5_PASS
```

The single preregistered real campaign was executed once on 2026-09-01 with
`deepseek-v4-pro`. There was no retry, fallback, selective rerun, prompt change,
parameter change, or second output destination.

W5 crossed the causal boundary that W4 did not reach. DeepSeek again emitted
three independently legal `read_file` calls on the first turn of every episode.
The W5 host admitted each complete read-only envelope, executed its members in
provider order, returned one result for every original ID, and DeepSeek produced
a subsequent model turn in all four episodes.

This establishes the bounded native multi-read loop. It does not establish that
the frozen tiny executable grammar can represent every correct World Model
revision: boost and clamped proposals remained blocked at the source-grammar
layer, while reverse was accepted and atomically applied.

## Frozen run identity

```text
provider              deepseek-anthropic
endpoint              https://api.deepseek.com/anthropic
model                 deepseek-v4-pro
thinking              disabled
temperature           0
max tokens            1600
timeout               45 seconds
retry / fallback      none / none

manifest SHA-256      c517590ca6fd90f39076f3c7f368de86b7543c5ebeb53114e041eda5697845ea
implementation SHA-256
                      a331bec2feaf90b06279db77647d4af07439f3a23ff15fd961aadbc2fa443dd7
prompt SHA-256        647afbe0c9b40ef6bba3fc696793fe9116ca7602251487923febcc28f5f1185a
tools SHA-256         31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
fixture SHA-256       e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f
W4 result SHA-256     e4d134ae7a4f2c1f2f5c442cbc1c43841422f7f927d5f351d429ee22d82cf8de
W5 result SHA-256     20fed04470f8a3f45153b7440f21e433802cdc3e2cec092a784727bab6c1b0fb
```

The canonical artifact is `Mind/fixtures/w5/real_campaign_result.json`. It
contains every actual provider request and response, per-call validation,
events, results, sources, and verdict metrics. It contains no API key, local
workspace path, `.env.local` reference, or traceback.

## W4 to W5 causal comparison

| Dimension | W4 | W5 |
|---|---:|---:|
| real provider calls | 4 | 18 |
| native `tool_use` blocks | 12 | 26 |
| accepted multi-read envelopes | 0 | 5 |
| reads executed inside accepted batches | 0 | 14 |
| executed tool steps | 0 | 24 |
| result blocks returned on later calls | 0 | 23 |
| episodes reaching a real continuation | 0 / 4 | 4 / 4 |
| legal read-batch cardinality failures | 4 | 0 |
| resolvable records with native World Model writes | 0 / 3 | 3 / 3 |
| accepted semantic revisions | 0 / 3 | 1 / 3 |
| reverse post-edit verifier | no | yes |
| ambiguity native unresolved | no | yes |

W4 and W5 used the same model, endpoint, tool schemas, fixture order, source
grammar, verifier, budgets, hidden holdouts, and prompt doctrine. W5 changed
only host admission for two or three independently legal `read_file` calls.
The observed transition from zero executed tools to four real continuations is
therefore attributable to that cardinality mechanism within this campaign.

## Native batch and pairing metrics

```text
multi_tool_envelopes                    5
multi_read_envelopes_accepted           5
multi_read_calls_executed              14
maximum calls in one response           3
mixed/mutating envelope rejections      0
legal multi-read cardinality failures   0

native tool_use blocks                 26
executed tool steps                    24
next-call tool_result blocks           23
pairing integrity                    PASS
first real tool_result turn             2 in every episode
```

The difference between 24 executed steps and 23 returned results is expected:
reverse's final verified World Model write was atomically applied and ended the
episode, so it did not require another model call. Clamped's last write call was
host-valid but not executed because all eight tool steps were already consumed.
Every executed nonterminal call received exactly one ID-matched result on the
next attempted provider request.

All four first turns reproduced W4's exact three-read shape:

```text
read_file(world_model.py)
read_file(notes/world_model.md)
read_file(evidence.json, start=0, count=2)
```

Clamped later emitted one additional two-read envelope. Duplicate merging,
range coalescing, concurrent execution, mutating batches, and budget discounts
were not used.

## Per-record trajectories

| Record | Model calls | Executed tools | Host-valid native write calls | Accepted | Termination | Public / hidden | Current |
|---|---:|---:|---:|---:|---|---:|---|
| boost-step | 7 | 8 | 2 | 0 | `STRUCTURAL_FAILURE: invalid_model_action` | 0.5 / 0.0 | unchanged |
| reverse-step | 3 | 5 | 1 | 1 | `CONSISTENT_ENOUGH` | 1.0 / 1.0 | changed |
| clamped-step | 6 | 8 | 2 | 0 | `BUDGET_EXHAUSTED: tool_budget_exhausted` | 0.6 / 0.0 | unchanged |
| insufficient-evidence | 2 | 3 | 0 | 0 | `UNRESOLVED` | 1.0 / n/a | unchanged |

### boost-step

DeepSeek read all four public evidence items, inferred the correct shared
mechanic—step deltas are doubled in boost mode—and emitted two meaningful
native source proposals. The first used a local `factor`; the second used an
inline conditional multiplier. Both were host-valid native writes and both
changed the `predict` body, but the unchanged W1 tiny AST rejected them before
the deterministic evidence verifier.

The model then spent the remaining tool budget on another source read and a
restricted analysis call. With zero tool calls remaining it returned prose
without a structured terminal action, producing `invalid_model_action`. This is
model action/budget progression after a source-grammar block, not a W5 batch or
provider-protocol failure. The current model stayed coherent and unchanged.

### reverse-step

DeepSeek read all four public items and emitted the compact mode-conditioned
subtraction rule on turn 3. The frozen source contract accepted it, the
deterministic verifier reached public accuracy 1.0, and atomic apply succeeded.
The post-campaign hidden replay also scored 1.0. This is the complete path:

```text
native multi-read
-> paired results
-> further read
-> native write
-> source validation
-> deterministic verifier
-> atomic apply
```

### clamped-step

DeepSeek read all five public observations in a three-read batch followed by a
two-read batch and inferred the correct lower-bound mechanism. Its first source
proposal used a local `value` assignment plus an `and` condition; the second
kept a local assignment and used an inline conditional. Both proposals were
semantically responsive to the public divergence. The first was executed and
rejected by the frozen tiny AST. The second was emitted after all eight tool
steps had been consumed, so the host stopped atomically before executing it.

The current model stayed coherent and unchanged. The resulting public/hidden
scores describe that unchanged model, not the unexecuted proposal. This is a
source representation/budget outcome, not a read-batch failure.

### insufficient-evidence

DeepSeek inspected the current model, notes, and both public observations. It
correctly observed that step behavior was supported while toggle behavior was
not identified, emitted native `unresolved`, wrote bounded uncertainty notes,
ended `UNRESOLVED`, and left current unchanged.

## Layered interpretation

### A. Provider/tool protocol — PASS

The real DeepSeek Anthropic-compatible endpoint received the frozen tool
schemas and returned native structured calls. There was no provider failure,
retry, fallback, or text-to-tool emulation.

### B. Cardinality handling — PASS

All five legal 2..3-read envelopes were admitted atomically. All members were
validated first, executed in provider order, charged separately, and returned
with their original IDs. No legal batch failed solely because its call count
was greater than one.

### C. Model action progression — PASS for the preregistered threshold

All three resolvable records produced at least one host-valid native
`write_file(world_model.py)` call; the threshold was 2 / 3. Reverse completed
the verifier/apply path. Boost and clamped continued reasoning after structural
feedback, although neither reached an accepted representation.

### D. Source grammar — PARTIAL / next blocker

Reverse is expressible in the tiny W1 grammar. Boost and clamped generated
meaningful mechanics that used constructs outside that grammar. W5 explicitly
froze this layer, so these rejections neither falsify the native batch mechanism
nor authorize changing the grammar in this experiment.

### E. Deterministic verifier — PASS where reached

Reverse reached the unchanged verifier, scored 1.0 on public evidence, and was
atomically applied. No invalid source was written to current. Boost/clamped
were stopped before verification or application.

### F. Semantic generalization — limited evidence

Reverse generalized to its hidden observation (1.0). The boost and clamped
proposals expressed the correct general public mechanisms, but because they
were not accepted, their unchanged current models scored 0.0 on hidden replay.
W5 therefore does not claim executable semantic generalization for those two
cases.

## Acceptance criteria

### Passed

- Reality Evidence unchanged; hidden compound surfaces remained isolated.
- All current sources remained coherent; ambiguity current stayed unchanged.
- Authority surface remained exactly the four frozen Builder tools.
- Episode freshness and the 16,000-character context cap passed; maximum
  observed history was 13,560 characters.
- No retry/fallback and no provider failure.
- At least one three-read W4-style response was executed and returned; actual:
  four first-turn examples.
- Four of four episodes reached a real next model call after tool results.
- Zero legal read-batch cardinality failures.
- Three of three resolvable cases emitted legal native World Model writes.
- Reverse reached the post-edit verifier and atomic apply.
- Ambiguity used native `unresolved`, ended `UNRESOLVED`, and preserved current.

### Non-blocking observations

- Only one of three resolvable revisions passed the frozen source contract.
- Boost exhausted its tools after two grammar-rejected proposals and ended with
  an invalid plain-text action.
- Clamped exhausted its tool budget before its second source proposal could be
  executed.
- Boost/clamped hidden accuracy remained 0.0 because current was unchanged.

None is a preregistered W5_FAIL condition, and all required W5_PASS thresholds
were met.

## Promotion and next boundary

```text
W5_NATIVE_MULTI_READ_MECHANISM_SUPPORTED
NO_PRODUCTION_PROMOTION
```

The experiment supports W5 as the bounded provider-native Builder baseline for
the next isolated World Model experiment. It does not authorize production
Mind routing, a generic or parallel tool framework, mutating batches, larger
budgets, prompt strengthening, new providers, or new authority.

The next falsifiable boundary is W6 executable representation expressivity:
whether the frozen tiny W1 grammar should move toward a still-bounded safe
ordinary-Python AST plus isolated execution and the unchanged evidence
verifier. That is a separate variable and is not implemented here.
