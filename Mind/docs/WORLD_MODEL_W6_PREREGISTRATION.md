# World Model Experiment W6 Preregistration

## Frozen question and single variable

W6 asks whether the W1 executable representation, rather than the W5 Builder
interaction, is now the remaining bottleneck for the simple shared boost and
clamped mechanics.

The only changed mechanism is:

```text
W5: W1 tiny executable grammar
W6: bounded pure-computation Python grammar
```

W6 retains the complete W5 provider-native, bounded multi-read, stateful-
within-episode baseline. It does not change the model, provider, tools, tool
cardinality, budgets, context projection, public evidence, hidden holdouts,
initial models, verifier decision rule, atomic apply, episode trigger, retry,
or fallback behavior.

W5 remains immutable and is not rerun or overwritten.

## Codebase-design audit

### W1 source contract

The production-independent W1 validator is
`Mind/world_model_builder_experiment.py::_validate_model_source` (line 818).
Its model body admits only `Return` and `If` statements. Its expressions admit:

- bounded integer and string constants;
- direct reads of `state[value|mode]` and `action[kind|delta]`;
- `+`, `-`, and a special multiplication shape limited to a direct
  `action["delta"]` multiplied by a small integer literal;
- unary `+` / `-`, boolean `and` / `or`, one comparison, and a conditional
  expression;
- an exact `{value, mode}` return dictionary.

It admits no local assignment, local-name read, function call, attribute,
import, loop, comprehension, helper, or extra class member.

The W5 artifact shows two boost proposals and two clamped proposals:

- boost proposal 1 is rejected at `_validate_statement` because `factor = ...`
  is an `Assign`, while only `Return` and `If` are permitted;
- boost proposal 2 has no assignment, but
  `action["delta"] * (2 if ... else 1)` fails `_safe_multiplication` because the
  multiplier is an `IfExp`, not a small integer literal;
- both clamped proposals begin with `value = ...` and fail the same forbidden
  `Assign` rule; their later reads of `value` would also be outside W1;
- reverse passes because it uses only mode/kind text comparisons, `If`, exact
  observation dictionaries, and direct `state["value"] +/- action["delta"]`.

These verdicts are reproduced in
`Mind/test_world_model_representation_experiment.py`, without changing W1.

### Existing evaluator and verifier

`Mind/world_model_builder_experiment.py::_evaluate_in_isolated_process`
(line 1116) executes a previously validated class in a fresh
`python -I -S -c` child. The candidate namespace contains only
`__build_class__` and `__name__`. Inputs are copied plain dictionaries derived
from bounded evidence. The host applies a five-second timeout, rejects stderr,
caps stdout at 10,000 characters, parses strict JSON, and validates the exact
`{value: int, mode: str}` output contract.

`Mind/world_model_revision_experiment.py::_verify_source` (line 898) creates a
temporary source file, invokes that evaluator, and compares every prediction
with immutable public Reality Evidence. `run_revision_episode` writes a valid
candidate only to working state, emits verifier feedback, and atomically
changes current only at exact public accuracy 1.0.

The model-facing `run_python` analysis tool is a different seam. Its AST,
builtins, two-second timeout, input, and output are bounded by
`_run_bounded_analysis` (line 1019). W6 does not change it.

The evaluator is not a hostile general-Python sandbox: it has no container,
OS memory limit, CPU quota, PID limit, mount namespace, or network namespace.
Its safety depends on static language restriction plus a reduced builtin
namespace. W6 therefore cannot admit arbitrary Python.

### W5 interaction seam

`Mind/world_model_read_batch_experiment.py::BoundedReadBatchBuilder` (line 69)
owns one episode-local native transcript. It admits either one W4 action or an
atomic envelope of two or three independently legal reads, preserves tool IDs,
executes reads in provider order, charges each call, and sends separate results
on the next provider request.

`run_read_batch_revision_episode` (line 227) retains W2 reads, restricted
analysis, verifier observations, working/current transaction, bounded context,
failure semantics, and events. `run_registered_campaign` (line 742) owns fresh
temporary state per record, hidden post-episode replay, and result capture.

W5 imports the W1 validator directly and W2 verifier calls the W1 evaluator.
Changing either frozen module would invalidate the W5 implementation hash and
artifact. W6 therefore adds one experiment-only module. It subclasses only the
W5 transcript adapter to substitute the mechanical prompt paragraph and keeps
a W6-local episode path for the new validator/evaluator. This is intentional
locality around the single variable, not a reusable runner framework.

### Deep module and test seam

The W6 source-contract module presents two small interfaces:

```text
validate_model_source(source, class_name, expected_version) -> None
evaluate_model_source(source, evidence, class_name, expected_version)
    -> bounded predictions
```

Validation owns the complete grammar, typing, static magnitude bound, and
compile check. Evaluation always validates first and owns the temporary file,
isolated child, reduced builtins, timeout, parsing, and output contract. Callers
cannot opt out of validation.

The experiment interface is:

```text
run_representation_revision_episode(...) -> RevisionEpisodeResult
run_registered_campaign(...) -> W6 artifact
```

Tests exercise these interfaces and observable current/working files, events,
provider messages, verifier results, hidden isolation, and verdict metrics.
There is no generic validator registry, runner factory, plugin seam, or source
backend abstraction.

## W6 pure-computation contract

The accepted module remains exactly one undecorated, base-less
`CanonicalWorldModel` class with only:

```python
version = "wm-v1"
def predict(self, state, action): ...
```

Inside `predict`, W6 permits:

- local `Name` assignment only; input parameters and pure builtins cannot be
  rebound, and underscore-prefixed locals are rejected;
- `if` / `elif` / `else` and exact returns;
- integer/string constants under the W1 literal bounds;
- the four frozen input fields only;
- integer `+`, `-`, `*`, unary `+` / `-`, boolean `not`, `and` / `or`, one
  comparison, and conditional expressions;
- the frozen direct-call allowlist `min`, `max`, and `abs` only;
- an exact `{value: int, mode: str}` result.

W6 does not admit division, floor division, modulo, exponentiation, list/tuple
construction, slices, loops, comprehensions, generators, lambdas, decorators,
annotations, extra functions, or arbitrary calls because the frozen fixtures
do not require them.

The validator performs a small static type/range projection. Arithmetic is
integer-only, boolean operators are boolean-only, conditional branches must
have one type, and every intermediate integer has a conservative maximum
absolute magnitude no greater than `MAX_ABS_DYNAMICS_VALUE * 128`. This prevents
a bounded AST from becoming an unbounded big-integer resource amplifier.

It rejects, fail-closed:

```text
Import / ImportFrom
open / exec / eval / compile / __import__
getattr / setattr / delattr / globals / locals / vars
all Attribute and dunder access
input, attribute, subscript, or global mutation
with / try / raise / assert
global / nonlocal
async / await / yield
classes, helpers, decorators, defaults, annotations
loops / comprehensions / generators
filesystem, environment, network, process, and reflection surfaces
```

Static validation answers only whether source is inside this authority and
representation contract. It does not decide whether a proposed world rule is
true. Exact Reality Evidence replay remains the sole semantic verifier.

## Tycho re-audit and adaptation boundary

Official source: [`NIMI-research/Tycho`](https://github.com/NIMI-research/Tycho),
commit [`f68912a764372ead0a610db2e1c011d41ce5197e`](https://github.com/NIMI-research/Tycho/commit/f68912a764372ead0a610db2e1c011d41ce5197e),
Apache-2.0.

- [`tycho/workspace/templates/seed_world_model.py.tmpl`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/templates/seed_world_model.py.tmpl)
  seeds a freely editable executable hypothesis with state, transition, render,
  outcome, actions, subgoals, and heuristic surfaces.
- [`tycho/workspace/workspace.py`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/workspace.py)
  owns agent-authored model/workspace files separately from harness-authored
  observation evidence.
- [`tycho/workspace/agent_tools.py`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/agent_tools.py)
  exposes broad workspace reads/writes/edits and fresh general Python, then
  automatically invokes deterministic World Model feedback after a semantic
  edit.
- [`tycho/workspace/sandbox.py`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/sandbox.py)
  makes that generality tolerable with a container: network none, read-only
  root, one workspace bind mount, all capabilities dropped, no-new-privileges,
  PID/memory/CPU/open-file/file-size limits, bounded tmpfs, non-host HOME, and
  timeout cleanup. Its host mode is explicitly trusted development only.

W6 borrows only the causal pattern:

```text
agent-authored executable hypothesis
-> structural validation
-> isolated execution
-> deterministic evidence verification
-> bounded revision feedback
```

It does not copy Tycho's arbitrary Python, filesystem workspace, imports,
editor, planner, actor tools, or container assumption. The audit supports
continuing without a new container only because W6 remains a statically closed
pure language. If future representation needs arbitrary Python, attributes,
imports, user-defined helpers, loops, or external packages, a separate sandbox
experiment is required before widening authority.

## Frozen interaction, data, and provider

The W5 interaction baseline remains:

```text
provider          deepseek-anthropic
endpoint          https://api.deepseek.com/anthropic
model             deepseek-v4-pro
thinking          disabled
temperature       0
max tokens        1600
timeout           45 seconds
retry / fallback  none / none

model turns / tool calls       8 / 8
maximum accepted read batch    3
visible episode context        16,000 characters
one evidence read              2 observations
one file read                  4,000 characters
model source / notes           4,000 / 4,000 characters
restricted analysis runtime    2 seconds
```

The model-visible tools remain byte-identical:

```text
read_file
run_python
write_file
unresolved
```

The prompt is the W5 prompt with only the source-contract sentence replaced.
It names no fixture, mechanic, factor, clamping function, or solution. The
campaign order, public observations, hidden holdouts, and initial source remain:

```text
boost-step
reverse-step
clamped-step
insufficient-evidence
```

Hidden evidence cannot enter prompts, tool results, verifier, or apply. It is
scored only after each episode.

## Deterministic pre-campaign evidence

The required tests demonstrate:

- local assignment, `if`, conditional expressions, boolean expressions, and
  direct `min` / `max` / `abs` calls are admitted;
- both W5 boost/clamped proposal shapes are rejected by W1 and admitted by W6;
- reverse remains valid in both grammars;
- boost and clamped general rules execute in the isolated child;
- imports, `open`, `__import__`, input mutation, attributes, `getattr`, `exec`,
  arbitrary calls, loops, comprehensions, extra classes/functions, and static
  resource amplification are rejected;
- invalid source never reaches working/current;
- safe but false source reaches the verifier but not current;
- valid and verified source applies atomically;
- unresolved leaves current unchanged;
- hidden holdouts remain isolated in the full registered campaign.

The W1-W6 focused suite currently reports 164 passing tests; the W6 file itself
reports 38 passing tests. The required two-axis review is complete: both the
task-card/spec axis and repository/safety axis report PASS with no remaining
blockers. Review-driven hardening added full bounded evaluator-input validation,
explicit rejection of Python 3.14 class/function type parameters, and a verdict
distinction between independent pairing violations (`W6_FAIL`) and
provider-caused incomplete pairing (`W6_INCONCLUSIVE`).

## Preregistered verdict

`W6_PASS` requires all of:

- unchanged Reality Evidence, hidden isolation, coherent current sources,
  authority isolation, episode freshness, bounded context, intact pairing,
  and no provider retry/fallback;
- the deterministic source-contract adversarial self-check passes;
- boost and clamped each reach at least one host-valid, W6-source-valid semantic
  revision;
- boost, reverse, and clamped all enter deterministic verification;
- all three resolvable records finish at public accuracy 1.0 and hidden accuracy
  1.0;
- insufficient-evidence emits native `unresolved`, terminates `UNRESOLVED`, and
  leaves current unchanged.

`W6_INCONCLUSIVE` is a safe run that misses any expressivity, verifier,
generalization, Builder-progression, ambiguity, or provider threshold. Grammar,
prompt, budget, and provider must not be widened or rerun in response.

`W6_FAIL` is any evidence mutation, hidden leak, current corruption, authority
escape, context/freshness/pairing violation, dangerous-source acceptance,
possible filesystem/network/process side effect under the frozen threat model,
or unsupported ambiguity rewrite.

The artifact separately records Builder interaction, W5/W6 source-contract
verdicts for every proposed source, source safety, verifier entry, public score,
hidden score, and epistemic restraint.

## Frozen artifacts

```text
W5 implementation  a331bec2feaf90b06279db77647d4af07439f3a23ff15fd961aadbc2fa443dd7
W5 manifest        c517590ca6fd90f39076f3c7f368de86b7543c5ebeb53114e041eda5697845ea
W5 prompt          647afbe0c9b40ef6bba3fc696793fe9116ca7602251487923febcc28f5f1185a
W5 tools           31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
W5 result          20fed04470f8a3f45153b7440f21e433802cdc3e2cec092a784727bab6c1b0fb
fixture            e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f

W6 implementation  46ac449dd797e14997bd9dbf338e861751e8a43cf822b7cd2b0c3d55266292e2
W6 prompt          69f9abbff92b9ac39883a6d1f0d88182755eb61d8092ddd7772ef5889e425652
W6 tools           31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
W6 manifest        519f8ffd98df53aa17cf02079ec4d990c273c888c20fbcff0a8004748f211ef4
```

No production file is changed. This preregistration does not authorize a real
provider call. One separate explicit approval is required before exporting the
frozen W6 prompt, public evidence, current models, notes, tool schemas, and
episode-local provider history to DeepSeek for the one campaign.
