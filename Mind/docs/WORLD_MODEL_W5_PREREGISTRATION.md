# World Model Experiment W5 Preregistration

## Frozen question and single variable

W5 asks whether W4's real campaign stopped only because the host rejected a
provider response containing several independently legal read-only calls.

The only changed mechanism is:

```text
W4: one assistant response -> exactly one tool_use is admissible
W5: one assistant response -> one W4 action, or 2..3 legal read_file calls
```

For a multi-call response, the host validates the complete envelope before any
call runs. It admits the envelope only when every member is an independently
legal `read_file`. It then executes the reads sequentially in provider order
and sends one `tool_result` block per original `tool_use.id` in the next user
message. Duplicate reads are not deduplicated.

W5 does not change the fixtures, holdouts, initial models, tool definitions,
W1 source grammar, W2 verifier or atomic apply, W3 within-episode continuity,
provider, prompt doctrine, read bounds, eight-model-turn/eight-tool-call budget,
16,000-character visible-context cap, retry policy, or fallback policy.

## Frozen W4 baseline

W4 remains `W4_INCONCLUSIVE`; it is not rerun or overwritten. Its one real
campaign established:

```text
real DeepSeek Anthropic-compatible request tools       PASS
real structured tool_use responses                     PASS
12 / 12 individual read calls schema- and host-legal   PASS
four first-turn envelopes containing three reads       OBSERVED
host continuation after those responses                NOT REACHED
```

The frozen W4 host required `len(tool_use) == 1`. It rejected all four
three-read envelopes before executing a read, so the campaign never tested a
real `tool_use -> tool_result -> next model call` loop.

## Source and interface audit

### DeepSeek protocol facts

DeepSeek's official Anthropic compatibility documentation lists `tools`,
`tool_use`, and `tool_result` as supported. It also says
`tool_choice.disable_parallel_tool_use` is ignored:
<https://api-docs.deepseek.com/guides/anthropic_api/>. The page was rechecked
on 2026-09-01.

Consequently W5 does not set or test `disable_parallel_tool_use`; doing so
would introduce a second mechanism without an enforceable provider guarantee.

### Tycho mechanism reference

Official source: `NIMI-research/Tycho`, commit
`f68912a764372ead0a610db2e1c011d41ce5197e`, Apache-2.0.

The inspected implementation in
[`tycho/agent/builder.py`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/builder.py)
reads `reply.get("tool_calls") or []`, retains the assistant calls, executes
each call in list order, and carries the corresponding IDs into the result
history. Tool definitions and execution are in
[`tycho/workspace/agent_tools.py`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/agent_tools.py).

W5 borrows only multi-call collection, deterministic order, and ID-preserving
results. It does not copy Tycho's general multi-tool authority or create a
parallel executor.

### Lumina seam decision

`Mind/world_model_native_tool_experiment.py::NativeWorldModelRevisionBuilder`
projects one provider response into one canonical W2 action.
`Mind/world_model_revision_experiment.py::run_revision_episode` likewise
assumes one model turn produces one action. Queuing W5 reads through that seam
would either spend extra model turns on host-internal queued actions or hide
tool-call budget/event accounting.

W5 therefore adds one experiment-only module:

```text
BoundedReadBatchBuilder
  -> NativeActionEnvelope
  -> run_read_batch_revision_episode
```

The runner reuses the unchanged W2 path and symbols for context projection,
bounded resource reads, restricted Python, source validation, deterministic
verification, append-only events, working writes, and atomic current apply.
It is not a reusable executor, registry, framework, or production integration
seam. W2 and W4 remain byte-for-byte frozen.

## Frozen cardinality and atomicity contract

```text
one call
  -> execute with unchanged W4 semantics

two or three calls
  -> admit only when all are host-legal read_file calls
  -> preflight the entire envelope and remaining tool budget
  -> execute sequentially in provider response order
  -> count every read as one tool call

more than three calls
  -> reject the complete envelope

any multi-call envelope containing write_file, run_python, unresolved,
an unknown tool, an invalid path/range, or duplicate tool IDs
  -> reject the complete envelope before any call executes
```

Duplicate resources with distinct IDs remain distinct calls and consume
distinct budget. W5 does not merge ranges, coalesce observations, cache
semantically, or infer concurrency.

The next user message contains separate results:

```text
tool_result(tool_use_id=A, observation=A)
tool_result(tool_use_id=B, observation=B)
tool_result(tool_use_id=C, observation=C)
text(current bounded W2 context and remaining budgets)
```

The trailing bounded context preserves W2's verifier state, latest bounded
observations, and exact remaining budgets. The assistant's preceding text and
all native `tool_use` blocks remain in episode-local history.

## Authority and safety

The model-visible tools remain exactly:

```text
read_file(path, start?, count?)
run_python(code)
write_file(path, content)
unresolved(notes)
```

Multi-call authority is narrower: it exists only for `read_file`. A single
`run_python`, `write_file`, or `unresolved` retains W4/W2 behavior, but none is
admissible in a batch. Reads remain limited to the working World Model, notes,
and bounded public `evidence.json`. Hidden holdouts are scored only after an
episode and never enter provider-visible requests or results.

The artifact checks hidden isolation against every compound hidden-evidence
surface (whole evidence item, state, action, expected observation, and observed
observation) after subtracting structurally identical surfaces already present
in public evidence. Provider requests and JSON text blocks are recursively
normalized before comparison, so a partial compound leak cannot pass merely by
using different JSON formatting.

There is no shell, general filesystem, network tool, browser, IPython,
Execution Actor, Intention mutation, recursive agent, or production caller.
The provider HTTP adapter can call only the frozen DeepSeek endpoint; it is not
a Mind capability exposed to the model.

## Prompt and provider freeze

The W5 prompt is the W4 prompt with only its mechanical cardinality paragraph
replaced. It does not say “use three tools”, “read in parallel”, “must write”,
“do not reread”, or “make progress”. The native tool schemas are byte-equivalent
to W4.

```text
provider        deepseek-anthropic
endpoint        https://api.deepseek.com/anthropic
model           deepseek-v4-pro
thinking        disabled
temperature     0
max tokens      1600
timeout         45 seconds
retry           none
fallback        none
environment     DEEPSEEK_API_KEY
```

## Frozen bounds and fixtures

| Boundary | Value |
|---|---:|
| model turns / tool calls | 8 / 8 |
| maximum reads in one accepted batch | 3 |
| explicit visible episode context | 16,000 chars |
| one evidence read | 2 observations |
| one file read | 4,000 chars |
| verifier observation | 2,400 chars |
| model output / model source | 6,000 / 4,000 chars |
| notes | 4,000 chars |
| restricted Python runtime | 2 seconds |
| Python source / output | 2,000 / 2,000 chars |

Campaign order and all W3/W4 data remain:

```text
boost-step
reverse-step
clamped-step
insufficient-evidence
```

`clamped-step` may still express a meaningful `max(0, ...)` rule that the
frozen tiny W1 grammar rejects. Such an outcome is classified as source-grammar
failure, not provider/tool-protocol failure. AST expressivity is outside W5.

## Metrics and causal layers

W5 records exact provider requests/responses, every native call, per-call
schema and host validity, envelope validity, execution IDs, next-turn result
IDs, and pairing integrity. Aggregate metrics include:

```text
multi_tool_envelopes
multi_read_envelopes_accepted
multi_read_calls_executed
max_calls_in_one_response
mixed_or_mutating_multi_call_rejections
legal_multi_read_cardinality_failures
tool_use_count / tool_result_count
first_real_tool_result_turn
```

If a provider call fails, the artifact still records the exact model-visible
messages, system prompt, tools, and pending `tool_result` IDs handed to the
provider adapter. Such a continuation is pairing-incomplete and cannot satisfy
the causal milestone; provider failure remains `W5_INCONCLUSIVE` unless an
independent safety violation occurred.

For an admitted batch, all `BUILDER_ACTION_REQUESTED` events share the same
pre-observation model-turn parent. Each observation points only to its
corresponding request event. This preserves the fact that all calls were chosen
before any batch result existed.

It continues to record write attempts, accepted semantic revisions,
post-edit verifier events, public/hidden accuracy, termination, source-contract
acceptance, and the full bounded event trajectory.

Results must distinguish:

```text
A. provider/tool protocol
B. cardinality handling
C. model action progression
D. source grammar
E. verifier
F. semantic generalization
```

## Frozen artifacts and hashes

```text
W4 manifest       debb82ad9ebb3eb6f522cda47376d4a61889aafc15e6f14c699a473a08e495eb
W4 implementation 044f3da4cffc3decb48724d6040d07cbd73468babbdf594c631d8dd82d047992
W4 prompt         6289a41df94da1bfcc15585be329ec692d7805f91b20b9fc9fa847e8f78a54a9
W4 tools          31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
W4 result         e4d134ae7a4f2c1f2f5c442cbc1c43841422f7f927d5f351d429ee22d82cf8de
fixture           e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f

W5 implementation a331bec2feaf90b06279db77647d4af07439f3a23ff15fd961aadbc2fa443dd7
W5 prompt         647afbe0c9b40ef6bba3fc696793fe9116ca7602251487923febcc28f5f1185a
W5 tools          31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
W5 manifest       c517590ca6fd90f39076f3c7f368de86b7543c5ebeb53114e041eda5697845ea
```

The loader pins all listed W4 artifacts, fixtures, W5 implementation, prompt,
tools, bounds, provider configuration, and verdict criteria. The campaign
reloads and compares the complete nested W4/W3/W2 campaign before every
provider call and before/after artifact write. The real runner accepts only
`Mind/fixtures/w5/real_campaign_result.json`; that destination must not already
exist. Scripted test campaigns retain temporary output paths but cannot access
the real-model environment through that seam.

## Preregistered verdicts

`W5_PASS` requires all of:

- unchanged Reality Evidence, hidden isolation, coherent current sources,
  authority isolation, episode freshness, context bound, and no provider
  retry/fallback;
- at least one actual three-read response is accepted, all three reads execute,
  all three IDs pair to separate results, DeepSeek receives those results, and
  produces a subsequent model turn;
- all four episodes reach a real `tool_use -> tool_result -> next model call`
  continuation, unless the model itself chooses a terminal first action;
- zero failures caused solely by cardinality for legal 2..3 read batches;
- at least two of three resolvable records emit a legal native
  `write_file(world_model.py)`, whether or not the frozen AST accepts it;
- reverse-step reaches the deterministic post-edit verifier;
- insufficient-evidence emits native `unresolved`, terminates `UNRESOLVED`, and
  leaves current unchanged.

`W5_INCONCLUSIVE` is a safe run that demonstrates some or all read-batch
continuation but misses progression, reverse-verifier, ambiguity, or other PASS
thresholds, including provider failure or another unsupported provider
cardinality. It must not be tuned or rerun.

`W5_FAIL` is any evidence mutation, hidden leakage, authority escape, current
corruption, cross-episode history leak, context-cap bypass, nondeterministic
mutation caused by multi-call handling, or unsupported ambiguity rewrite.

## TDD and review gate

The test seam is the public W5 episode runner plus the actual provider-message
adapter: observable outcomes are reads, events/budgets, next-request
`tool_result` blocks and IDs, verifier/apply results, terminal status, and
campaign artifact metrics. Tests do not inspect a hidden action queue.

TDD began with a missing W5 module, then added the required acceptance cases.
It explicitly proves 2- and 3-read admission, ID preservation, deterministic
order, three-budget consumption, atomic rejection when remaining budget is too
small, frozen read bounds, hidden isolation, >3 rejection, mutating/mixed/
unknown rejection, no duplicate-read optimization, single-action W4 behavior,
next-turn visibility, bounded context, episode freshness, frozen prompt/tool
surface, and the registered four-case causal milestone. Three W2 failure-
semantic tests were observed red before aligning W5 with
`current_model_unavailable`, `current_model_mismatch`, and
`context_projection_failed`.

The first two-axis review found and caused regressions for nested-campaign
substitution, the canonical exactly-once result path, first-call and
continuation provider failures, incomplete pairing, batch event ancestry,
authority-escape verdicts, duplicate IDs, and the single-read/single-analysis
W4 paths. The follow-up standards review additionally caused final-model-turn
read pairing and terminal applied-write regressions. Those tests were observed
red before the fixes. The focused suite now reports 38 passing tests. A clean
follow-up review against the task card and repository standards is required
before the real campaign. Any further
implementation fix changes the implementation hash and requires updating this
preregistration and manifest before execution.

No production file is changed. This preregistration does not authorize a
parallel-tool framework, mutating batch, AST expansion, prompt strengthening,
budget increase, provider change, new tool, Mind/Execution integration, World
Model production schema, or more than one real campaign.
