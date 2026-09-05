# World Model Experiment W4 Preregistration

## Frozen question and single variable

W4 asks whether replacing W3's plain-text JSON action envelope with the real
DeepSeek Anthropic-compatible native tool protocol reduces action-affordance
friction while preserving the same bounded cognition and execution semantics.

The only changed mechanism is:

```text
W3: assistant text -> JSON action parser -> canonical action
W4: provider tools -> assistant tool_use -> canonical action
    -> host observation -> next-request tool_result
```

W4 does not change the prompt doctrine, fixture order or content, hidden
holdouts, current models, model identity/settings, eight-turn/eight-tool budget,
16,000-character visible-context cap, read bounds, restricted `run_python`,
source grammar, verifier, atomic apply, or episode freshness. It adds no tool,
planning behavior, environment authority, production routing, retry, or
fallback.

## Frozen W3 baseline

W3 is preserved as `W3_INCONCLUSIVE` and is not rerun or modified:

```text
boost-step              inspection loop; 0 semantic revisions; BUDGET_EXHAUSTED
reverse-step            turn 8 correct revision; public/hidden 1.0; verifier/apply PASS
clamped-step            turn 8 correct zero-floor idea; native-equivalent write;
                        rejected by the frozen tiny AST because it used max(...)
insufficient-evidence   uncertainty reasoned correctly; prose + JSON caused
                        invalid_model_action; current unchanged
```

W3 continuity itself passed: previous action visible, previous observation
visible, episode freshness, and maximum visible history 11,662 / 16,000.

## Source audit

### Lumina provider seams

1. `core/model_client.py::ModelClient` and
   `DeepSeekAnthropicModelClient.generate(...)` are text-only: input is projected
   string messages and output is one string. Their request/response DTOs do not
   represent `tools`, `tool_use`, or `tool_result`.
2. The production client can remain unchanged. W4 uses the experiment-only
   `Mind/world_model_native_tool_experiment.py::DeepSeekAnthropicNativeToolClient`.
3. W4 represents protocol-native history explicitly as Anthropic content blocks:
   request `tools`, assistant `text` / `tool_use`, then user `tool_result` paired
   by `tool_use_id`. No JSON action string is converted into a fake tool call.
4. `Execution/deepseek_model.py::DeepSeekModel` already proves native function
   calling in Lumina, but it targets DeepSeek's OpenAI-compatible
   `/chat/completions` form (`tool_calls`, then role `tool`) and imports
   Execution authority DTOs. Reusing those DTOs would couple Mind to Actor
   authority and the wrong wire protocol. There is no existing provider-neutral
   DTO that can be reused without that coupling.
5. No change to production `core/model_client.py` or `Execution/` is required.

DeepSeek's official Anthropic API documentation states that its compatibility
endpoint accepts Anthropic `tools`, returns `tool_use` blocks with `id`, `name`,
and `input`, and accepts a following `tool_result` with the matching
`tool_use_id`: <https://api-docs.deepseek.com/guides/anthropic_api/>. The current
model page identifies `deepseek-v4-pro` on this API and lists tool calls:
<https://api-docs.deepseek.com/quick_start/pricing/>. These pages were inspected
on 2026-09-01.

### Frozen Builder seams reused

- `Mind/world_model_stateful_revision_experiment.py::StatefulWorldModelRevisionBuilder`
  supplies the W3 continuity behavior being migrated from text messages.
- `Mind/world_model_revision_experiment.py::run_revision_episode` remains the
  canonical action executor and retains all W2/W3 dispatch, failure, verifier,
  and atomic-apply behavior.
- `Mind/world_model_builder_experiment.py::_validate_model_source` remains the
  exact frozen tiny AST acceptance contract.
- `Mind/fixtures/w3/manifest.json` supplies the exact W3 cases, current sources,
  public evidence, ambiguity construction, hidden holdouts, bounds, and model
  settings.

### Tycho mechanism re-audited

Official source: `NIMI-research/Tycho`, commit
`f68912a764372ead0a610db2e1c011d41ce5197e`, Apache-2.0.

Inspected symbols:

- `tycho/agent/builder.py::WorldModelBuilder.build`
- `tycho/workspace/agent_tools.py`
- `tycho/serving/llm_client.py`
- the called Anthropic mapping in
  `tycho/serving/public_backends.py::anthropic_tools` and
  `::_anthropic_history`

Tycho creates fresh episode-local history, appends assistant text/tool calls,
executes one canonical internal action, appends the structured result, and maps
the next Anthropic request to `tool_result`. Its model-visible tool flavor may
vary while executor semantics remain unified.

W4 ports only structured tool specs, canonical dispatch, explicit tool-result
feedback, and fresh episode-local history. It keeps Lumina's names and does not
copy provider-specific tool flavors, game actions, `edit_function`, 40-step
budgets, broad Python, planner/outcome concepts, workspace ontology, or
provider `response_id` continuation.

## Native tool surface and authority

The entire model-visible capability surface is:

```text
read_file(path, start?, count?)
run_python(code)
write_file(path, content)
unresolved(notes)
```

`read_file` exposes only `world_model.py`, `notes/world_model.md`, and
`evidence.json`; its schema exposes `count <= 2`, and the host additionally
requires both `start` and `count` for evidence. `run_python` delegates to the
unchanged restricted, fresh W2 analysis surface. `write_file` targets only the
working World Model or notes. `unresolved` is a first-class terminal action.

There is no shell, general filesystem, import, network, process, browser,
IPython, Execution, recursive-agent, Intention, or environment-action
capability. The native client has no generic registry and no production caller.

## Native protocol and causal metrics

Every successful provider turn records the exact request and response plus:

```text
assistant_text_present
native_tool_use_present
native_tool_call_count
tool_call_id
tool_name
tool_arguments
schema_valid
host_valid
tool_result
tool_result_visible_next_turn
tool_call_id_paired_next_turn
```

`schema_valid` answers only whether the provider input matches the published
tool JSON Schema. `host_valid` independently answers whether it satisfies the
stricter canonical executor contract. For example, an evidence read containing
only `{path: "evidence.json"}` is schema-valid but host-invalid because its
bounded range is missing.

Aggregate protocol metrics are:

```text
native_tool_call_count
schema_invalid_tool_calls
host_rejected_tool_calls
plain_text_without_tool_count
mixed_text_plus_tool_count
invalid_action_envelope_count
```

The artifact also records first write-attempt turn, first source-contract-
accepted semantic revision turn, verifier count, public/hidden accuracy,
current change, termination, and source-contract acceptance.

Protocol validity, proposal meaning, and source-contract acceptance remain
separate:

- `source_proposal_predict_body_changed` is a narrow mechanical observation;
  it does not claim correctness.
- `source_proposal_semantic_review_required` marks each World Model proposal for
  post-campaign review.
- `source_contract_accepted` reports only the unchanged frozen AST/verifier
  path.
- after the one frozen campaign, the result report manually classifies
  `source proposal semantically meaningful?` from the proposal and public
  Reality Evidence. The rubric is: the proposal must express a general
  dynamics mechanism causally responsive to an observed public divergence,
  rather than formatting, a no-op, arbitrary output, or row memorization.
  This review cannot enter provider history, alter execution, affect the
  preregistered verdict, or authorize a rerun. Hidden evidence is scored only
  after episode termination and is not used for this semantic classification.

Thus a native `write_file` containing a meaningful zero-floor proposal can be
reported as protocol-valid and semantically meaningful while still being
source-contract-rejected by the frozen AST.

## Frozen artifacts and hashes

```text
W3 manifest       b5824f9609f58a30d72ce7022714804af5928167e069dfc7396bc8ffd5a9251b
W3 implementation 809d8e1ad7ea398cb187b791cd9670feffab6fb5236c977a1122695d864948e7
W3 prompt         44e6f8bb01201d86100b9ce9622db648a1e2463ff74e46f4fe122d2dfd45c5a9
W3 fixture        e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f

W4 implementation 044f3da4cffc3decb48724d6040d07cbd73468babbdf594c631d8dd82d047992
W4 prompt         6289a41df94da1bfcc15585be329ec692d7805f91b20b9fc9fa847e8f78a54a9
W4 tools          31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
W4 manifest       debb82ad9ebb3eb6f522cda47376d4a61889aafc15e6f14c699a473a08e495eb
```

The W4 prompt differs from W3 only by replacing the plain-text JSON protocol
paragraph with the native-tool instruction. The doctrine remains unchanged.
The manifest pins the W3 and W4 artifacts and is checked before calls, records,
and result persistence.

## Frozen bounds and provider

| Boundary | Value |
|---|---:|
| model turns / tool calls | 8 / 8 |
| explicit visible episode context | 16,000 chars |
| one evidence read | 2 observations |
| one file read | 4,000 chars |
| verifier observation | 2,400 chars |
| model output / model source | 6,000 / 4,000 chars |
| notes | 4,000 chars |
| restricted Python runtime | 2 seconds |
| Python source / output | 2,000 / 2,000 chars |

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

Campaign order is fixed:

```text
boost-step
reverse-step
clamped-step
insufficient-evidence
```

The registered output destination must not exist. The real campaign is run
exactly once; no record may be selectively retried or rerun.

## Preregistered verdicts

`W4_PASS` requires all of:

- Reality Evidence unchanged, hidden isolation, coherent current sources,
  authority isolation, context bound, episode freshness, and no provider
  retry/fallback;
- actual provider requests carry `tools`, responses carry structured
  `tool_use`, and next requests carry matching `tool_result` where another turn
  exists;
- zero `invalid_model_action` caused by prose/JSON parsing and zero malformed
  action-envelope failures;
- ambiguity uses native `unresolved`, ends `UNRESOLVED`, and leaves current
  unchanged;
- at least two of three resolvable records produce host-valid native
  `write_file(world_model.py)` calls, regardless of source-grammar acceptance;
- reverse-step reaches the automatic post-edit verifier.

`W4_INCONCLUSIVE` is a safe run that misses any native-protocol, ambiguity,
write-attempt, or reverse-verifier threshold, including provider failure.

`W4_FAIL` is any evidence mutation, hidden leakage, authority escape, current
corruption, context-bound bypass, prior-episode leakage, or unsupported
ambiguity rewrite. A clamped proposal rejected solely by the unchanged tiny AST
is not by itself a W4 failure.

## Pre-campaign evidence and review gate

TDD began with a missing-module import failure, then exercised the required
native dispatch, unresolved, mixed prose/tool, next-turn visibility, call-ID
pairing, schema bound, fail-closed arguments, real hidden holdout isolation,
automatic verification, unchanged AST, and episode freshness cases. The final
focused W4 suite reports 17 passing tests.

The fixed-point review was the W4-only surface (the files did not exist before
this task). Standards review returned PASS. Spec review first found and caused
tests for separate schema/host metrics, first accepted revision turn, a real
hidden-marker holdout, and honest separation of mechanical source change from
manual semantic meaning; its final review returned PASS.

No production module is changed. This preregistration does not authorize AST
expansion, budget or tool-name tuning, `edit_function`, response-ID continuity,
automatic surprise triggers, World Model production schema, Mind/Execution
integration, or Evolution.
