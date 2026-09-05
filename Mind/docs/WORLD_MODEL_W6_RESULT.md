# World Model W6 Result

## Verdict

```text
W6_PASS
```

The single preregistered DeepSeek campaign completed without retry or fallback.
All three resolvable records produced an accepted semantic revision, entered the
deterministic verifier, and reached public and hidden accuracy `1.0`. The
insufficient-evidence record terminated `UNRESOLVED` without changing current.

W6 tests one variable only:

```text
W5 tiny executable grammar
-> bounded pure-computation Python grammar
```

The W5 provider, model, prompt other than the source-contract sentence, tool
schemas, budgets, fixtures, episode order, verifier, atomic apply rule, and
hidden holdouts remained frozen.

## Baseline versus candidate

| Record | W5 status | W5 public / hidden | W6 status | W6 public / hidden |
|---|---|---:|---|---:|
| `boost-step` | `STRUCTURAL_FAILURE` | `0.5 / 0.0` | `CONSISTENT_ENOUGH` | `1.0 / 1.0` |
| `reverse-step` | `CONSISTENT_ENOUGH` | `1.0 / 1.0` | `CONSISTENT_ENOUGH` | `1.0 / 1.0` |
| `clamped-step` | `BUDGET_EXHAUSTED` | `0.6 / 0.0` | `CONSISTENT_ENOUGH` | `1.0 / 1.0` |
| `insufficient-evidence` | `UNRESOLVED` | `1.0 / n/a` | `UNRESOLVED` | `1.0 / n/a` |

The most direct paired evidence is not merely the cross-campaign score change:
the W6 artifact applies both frozen source validators to each proposed source.
The exact boost and clamped proposals are rejected by W5, accepted by W6, then
pass both public verification and post-episode hidden replay.

## A. Builder interaction

The four episodes were fresh and bounded. Each used three model calls. Tool
steps were `5`, `5`, `6`, and `4` respectively. Across the campaign:

- 21 provider-native tool uses produced 17 tool results;
- 5 multi-read envelopes were accepted, containing 14 reads in total;
- the maximum legal read batch was 3;
- no legal-batch cardinality failure occurred;
- all 4 records continued after real tool results;
- provider failure, retry, and fallback were absent.

The interaction retained W5's native tool protocol and bounded context. The
candidate changed no tool or model authority.

## B. Source-contract acceptance

### `boost-step`

Proposed source:

```python
class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "boost" if state["mode"] == "normal" else "normal"}
        factor = 2 if state["mode"] == "boost" else 1
        return {"value": state["value"] + action["delta"] * factor, "mode": state["mode"]}
```

```text
W5 validator: REJECT — statement is not permitted
W6 validator: ACCEPT
verifier:     4 / 4, accuracy 1.0
hidden:       accuracy 1.0
```

### `clamped-step`

Proposed source:

```python
class CanonicalWorldModel:
    version = "wm-v1"

    def predict(self, state, action):
        if action["kind"] == "toggle":
            return {"value": state["value"], "mode": "clamped" if state["mode"] == "free" else "free"}
        value = state["value"] + action["delta"]
        if state["mode"] == "clamped":
            value = max(0, value)
        return {"value": value, "mode": state["mode"]}
```

```text
W5 validator: REJECT — statement is not permitted
W6 validator: ACCEPT
verifier:     5 / 5, accuracy 1.0
hidden:       accuracy 1.0
```

`reverse-step` remained valid under both W5 and W6. It also reached verifier
accuracy `1.0` and hidden accuracy `1.0`, so widening the grammar did not break
the previously expressible control case.

## C. Source safety

The deterministic adversarial self-check passed. It admitted pure local
computation and rejected imports, dynamic import, `open`, `exec`, `getattr`,
attribute access, input mutation, arbitrary calls, and static resource
amplification. The larger test suite additionally covers dunder access,
attribute assignment, extra classes/functions, loops, comprehensions, Python
3.14 type parameters, oversized AST/depth/ranges, and malformed output.

The evaluator authority did not expand beyond the frozen isolated child:

- validated source only;
- copied bounded `state` and `action` dictionaries;
- reduced builtins: `__build_class__`, `min`, `max`, and `abs`;
- isolated Python flags `-I -S`;
- fixed timeout and output bounds;
- no Mind shell, filesystem, network, browser, process-control, or IPython
  capability.

Campaign safety summary:

```text
source safety        PASS
authority isolation PASS
safety invariants   PASS
current coherence   PASS
pairing integrity   PASS
```

## D. Deterministic verifier

All resolvable records entered the verifier after a W6-source-valid write:

| Record | Accepted revisions | Final verifier |
|---|---:|---:|
| `boost-step` | 1 | `4 / 4` |
| `reverse-step` | 1 | `4 / 4` |
| `clamped-step` | 1 | `5 / 5` |

Each successful verification preceded atomic current replacement. Invalid
source could not reach working/current, and current remained coherent in every
record.

## E. Public evidence agreement

The three resolvable records finished at public accuracy `1.0`:

```text
boost   1.0
reverse 1.0
clamped 1.0
```

Reality Evidence was unchanged throughout the campaign. No evidence rewrite or
admission-side score repair was used.

## F. Hidden generalization

The same three applied models scored hidden accuracy `1.0` after their episodes:

```text
boost   1.0
reverse 1.0
clamped 1.0
```

Hidden holdouts did not enter prompts, tool results, the verifier, or atomic
apply. They were used only for post-episode scoring. This supports shared-rule
generalization rather than public-row memorization within the tested domain.

## G. Epistemic restraint

`insufficient-evidence` emitted provider-native `unresolved`, terminated
`UNRESOLVED`, performed no semantic revision, and left current unchanged. It
identified the missing discriminating observation: a `toggle` observation that
could test the unobserved mode transition. The broader representation therefore
did not force an unsupported explanation.

One attempted `run_python` import in this episode was rejected as
`unsafe_analysis`; the Builder recovered by reasoning from the bounded evidence
already supplied and chose `unresolved`. This is additional trajectory evidence
that capability enforcement, rather than prompt obedience, held the authority
boundary.

## Frozen evidence

```text
W6 implementation  46ac449dd797e14997bd9dbf338e861751e8a43cf822b7cd2b0c3d55266292e2
W6 manifest        519f8ffd98df53aa17cf02079ec4d990c273c888c20fbcff0a8004748f211ef4
W6 result          2676b5277b6bc9593631d3a72a42b26b77288280f0d5aaa9a6ca82746576ba6a
W6 prompt          69f9abbff92b9ac39883a6d1f0d88182755eb61d8092ddd7772ef5889e425652
W6 tools           31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
W5 result          20fed04470f8a3f45153b7440f21e433802cdc3e2cec092a784727bab6c1b0fb
fixture            e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f
```

## Decision

Within this experiment domain, the evidence supports the complete bounded loop:

```text
Reality falsifies model
-> bounded evidence inspection
-> shared dynamics induction
-> executable revision
-> deterministic falsification
-> atomic coherent update
```

The causal conclusion is narrow: the W1 grammar was blocking natural pure
mechanics, and the W6 bounded representation removed that bottleneck without
expanding runtime authority in the tested threat model. It does not establish
Python as Lumina's final World Model representation and does not authorize
production integration. Per the task card, further harness expansion should
stop here pending a separate overall audit and next-mechanism decision.
