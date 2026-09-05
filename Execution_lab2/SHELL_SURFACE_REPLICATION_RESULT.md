# Independent Shell-Surface Replication Result

## 1. Preregistered question

Three previously unused Nested fixtures were each run once under two arms:

- A: model-visible Read, Write, Shell, persistent IPython, Wait, SpawnChild
  and ClaimComplete;
- B: the identical surface with only Shell omitted.

The sole primary metric was REPETITIVE_LOCAL_SEARCH. It was operationalized
before the real run as at least two acquisition-only Read/Shell/IPython
decisions that exposed no newly observed task-relevant marker. The result is
REPLICATED iff A is repetitive in at least 2/3 runs and B in at most 1/3.
Completion, leaf evidence, Spawn, topology, usage, time, Context and call
shapes are secondary and cannot change that verdict.

## 2. Frozen validity

Exactly six fresh DeepSeek-V4-Pro executions ran once in this order:

`	ext
A1, B1, A2, B2, A3, B3
``n+
Every run used thinking disabled, the same Goal, Runtime, persistent-IPython
lifecycle, sequential Child driver, eight-decision bound, 2,000-character
Context bound, four-call admission bound, depth/Actor limits, ToolHost and
FileContentEquals(outcome.txt, expected) verifier. No prompt, fixture,
budget, cardinality rule or Tool implementation changed during or after the
experiment, and no replacement run was made.

Both arms used the same static hybrid DeepSeek view. Runtime already owned
both typed Tool and IPython execution; the adapter change only composes their
existing schemas. The existing native and IPython-only modes are unchanged.
The generic native system prompt was retained and does not direct the model to
use Shell, IPython or Spawn.

Validity checks passed with no reason for INVALID:

- paired fixture fingerprints were equal;
- IPython was exposed in every one of the 48 DecisionFrames;
- Shell exposure matched the arm in every frame;
- provider tool schemas matched the actual AgentProcess capability view;
- model, thinking and stream settings were fixed;
- after omitting Shell from A and normalizing only fresh execution/root IDs,
  each pair's complete initial provider request was equal;
- no provider failure, malformed protocol outcome, unrecorded cardinality
  rejection or other unrecorded admission anomaly occurred.

The artifact retains every DecisionFrame's exact model-facing capabilities,
ordered typed calls with provider call IDs and arguments, and complete actual
provider request. It contains no API headers or credential.

## 3. New fixture distribution

The fixtures do not reuse the previous 	ask.txt -> two modules -> two leaves`n+shape, filenames, values or contents. Each new 1,600-byte, six-file fixture
uses:

`	ext
mission.md
  -> one routes/... manifest
     -> three ordered records/... payload files
retired/... decoy
``n+
The three expected outputs were independently fixed as
cinder/harbor/lynx, opal/wren/sable, and moss/comet/ivory.

## 4. Primary result

| Run | Classification | No-progress local-search decisions | Terminal |
|---|---|---:|---|
| A1 | REPETITIVE_LOCAL_SEARCH | 5 | decision limit |
| B1 | REPETITIVE_LOCAL_SEARCH | 5 | decision limit |
| A2 | REPETITIVE_LOCAL_SEARCH | 5 | decision limit |
| B2 | REPETITIVE_LOCAL_SEARCH | 5 | decision limit |
| A3 | REPETITIVE_LOCAL_SEARCH | 5 | decision limit |
| B3 | REPETITIVE_LOCAL_SEARCH | 5 | decision limit |

`	ext
A repetitive = 3/3
B repetitive = 3/3
``n+
## 5. Secondary observations

All six trajectories had the same action pattern for their paired fixture:

`	ext
D1  Read mission.md                         new evidence
D2  Read routes/...                         new evidence
D3  Read all three records/...              complete leaf evidence
D4  Repeat mission.md                       no progress
D5  Repeat routes/...                       no progress
D6  Repeat all three records/...            no progress
D7  Repeat mission.md                       no progress
D8  Repeat routes/...                       no progress
``n+
No run emitted Shell, IPython, Write, ClaimComplete, SpawnChild or Wait. All
had complete leaf evidence by D3 but reached the decision limit without
writing or claiming completion. Completion was 0/3 in both arms; Spawn intent
and committed Spawn were 0/3; topology was LINEAR with one Root Actor in all
runs. No response crossed the four-call bound.

| Run | Model calls | Input / output tokens | Context sum / max chars | Wall seconds |
|---|---:|---:|---:|---:|
| A1 | 8 | 7,740 / 514 | 6,474 / 1,038 | 13.890 |
| B1 | 8 | 7,280 / 514 | 6,474 / 1,038 | 13.943 |
| A2 | 8 | 7,711 / 534 | 6,459 / 1,036 | 12.680 |
| B2 | 8 | 7,244 / 524 | 6,459 / 1,036 | 12.271 |
| A3 | 8 | 7,742 / 547 | 6,464 / 1,036 | 13.182 |
| B3 | 8 | 7,251 / 512 | 6,464 / 1,036 | 12.494 |

The higher A input-token usage is consistent with its additional Shell schema;
it is secondary and does not alter the primary result.

## 6. Verdict

**NOT_REPLICATED**

A met the repetitive threshold, but B did not meet the preregistered ceiling.
The prior SHELL_AFFORDANCE_INTERFERENCE_SUPPORTED result therefore does not
graduate to a replicated hypothesis. On these independent fixtures, removing
Shell had no observed trajectory effect: both arms selected the same direct
Read sequence and then the same completion-free reread loop.

This does not refute all possible Shell-affordance effects. It establishes
that the earlier direction was not stable across the new fixture distribution
and may have depended on properties of the first three landscapes.

## 7. Exactly one next action

Perform one read-only cross-experiment fixture-structure audit comparing the
original three Nested landscapes with these three replication landscapes to
identify which task-structure differences co-occurred with Shell selection,
before running any Wide-versus-Prime-like surface A/B.

The canonical maintained evidence is
SHELL_SURFACE_REPLICATION_ARTIFACT.json.
