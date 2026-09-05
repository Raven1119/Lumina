# Tool Cardinality Evidence Audit

## 1. Audit scope

This update adds the previously missing redacted pre-admission evidence seam
and reports one exact six-run replication of the frozen Flat/Nested experiment.
It does not change the four-call limit, provider decision semantics, Runtime,
prompt, fixture, model/settings, Context, Spawn policy, Child limit or depth.

The original experiment's three Nested `>4` arrays remain lost and cannot be
retroactively classified. The new replication is a fresh sample, not a
reconstruction of those responses.

## 2. Evidence implementation

`DeepSeekModel` now accepts one optional research callback. Immediately before
the existing `len(tool_calls) > 4` rejection it emits only:

- `tool_call_count`;
- ordered `index` and `tool_name`;
- `call_id_present` and `call_id_unique`;
- `arguments_is_json_object` and `arguments_schema_valid`;
- `classification`: `ordinary`, `control`, or `unknown`.

It does not emit arguments, file/user content, API keys, headers or the full
provider response. The callback is outside EventLog and is fail-soft, so
evidence recording cannot change the accepted/rejected action or execute an
effect.

Four deterministic cases establish fidelity: five Reads are ordinary and
execute zero calls; five Spawns are control and create zero identities; a
mixed ordered response preserves classification plus present/unique ID flags;
and malformed JSON arguments are marked invalid without crashing. Every case
retains `model_protocol:too_many_tool_calls`.

## 3. Evidence-preserving replication

| Run | Completion | Max depth | Actors | Failure | Pre-admission shape |
|---|---|---:|---:|---|---|
| F1 | verified | 0 | 1 | none | none |
| N1 | no | 0 | 1 | `decision_limit_reached` | none |
| F2 | verified | 0 | 1 | none | none |
| N2 | no | 0 | 1 | `decision_limit_reached` | none |
| F3 | verified | 0 | 1 | none | none |
| N3 | no | 0 | 1 | `decision_limit_reached` | none |

All six `pre_admission_call_shapes` arrays in
`TOPOLOGY_EMERGENCE_ARTIFACT.json` are empty because no response exceeded
four calls. There is therefore no new over-cap response to classify as
`LEGAL_ORDINARY_OVER_CAP`, `HOMOGENEOUS_SPAWN_OVER_CAP`, `MIXED_CONTROL`
or `MALFORMED`.

## 4. Origin of current limit = 4

Git history attributes `_MAX_TOOL_CALLS_PER_DECISION = 4` to commit
`690501a0ebd262ed5c8130bd52a0e8fe0968603c`,
`feat(execution): support bounded sequential tool calls`. `SOURCE_AUDIT.md`
records the exact count as a Lumina task-specific adaptation. The preceding
real evidence had observed a three-read ordinary sibling response; maintained
tests then established four-call success and failure handling. No recorded
provider observation or upstream rule derives the number four.

- **Safety invariants:** whole-batch validation before effects, unique ids,
  valid tool names/JSON/action schemas, control-call rules and bounded durable
  projection.
- **Protocol requirements:** provider call identity, function-call shape and
  correlated `role=tool` results.
- **Experimental bound:** keeping sequential execution, Context and recovery
  surfaces finite.
- **Arbitrary local cap:** the exact numeric value `4`. It is neither a
  DeepSeek protocol requirement nor an upstream-derived algorithm.

## 5. DeepSeek / DSH / Codex comparison

- **DeepSeek official API:** the current
  [Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/)
  models response `message.tool_calls` as an array and says automatic choice
  may call one or more tools. It states a request-side maximum of 128 supplied
  functions, but states no maximum number of tool calls in one response. That
  request-schema limit is not a response-cardinality rule.
- **DSH:** pinned
  [`executeToolCalls`](https://github.com/deepseek-ai/deepseek-harness/blob/b150a551b8d465e31e418e1b2eaf5e79bbb7d28e/packages/core/agent-loop/src/tool-calls.ts)
  plans every provider-emitted `ToolCallBlock` and uses
  `maxParallelToolCalls` to bound concurrent in-flight execution. In this
  audited path, the concurrency bound is not a fixed small cap on the total
  response array.
- **Codex:** pinned
  [`turn.rs`](https://github.com/openai/codex/blob/bde9db1375667c50dcc0c2b52532a4e2672571c2/codex-rs/core/src/session/turn.rs)
  accepts multiple tool-response work items into an ordered future set, while
  [`parallel.rs`](https://github.com/openai/codex/blob/bde9db1375667c50dcc0c2b52532a4e2672571c2/codex-rs/core/src/tools/parallel.rs)
  controls parallel versus serialized dispatch per tool. No equivalent fixed
  four-call execution admission limit appears in these audited paths.

These comparisons establish only that `4` is local. They do not establish that
the three historical missing Nested arrays were legal, and the replication did
not reproduce an over-cap response.

## 6. Scientific interpretation

The replication establishes that, under the unchanged substrate, Flat
completed at depth zero in 3/3 runs while Nested committed no Child and reached
the eight-decision limit in 3/3 runs. It also establishes that cardinality
admission did not truncate any of these six fresh trajectories.

It does **not** classify the original lost responses, prove that the four-call
cap is generally irrelevant, establish autonomous recursion, or establish
whether a different decision bound would change Nested behavior. The unchanged
preregistered rule yields `NOT VALIDATED` because Nested depth two is 0/3.

## 7. Verdict

- **Topology:** `NOT VALIDATED`.
- **Current protocol conclusion:** `none`; no fresh response crossed the
  cardinality boundary.
- **Historical protocol conclusion:** `INSUFFICIENT_EVIDENCE`; the original
  three arrays remain lost.

## 8. Exactly one recommended next action

Treat the repeated Nested `decision_limit_reached` outcome as the single next
research question before changing cardinality or topology policy.
