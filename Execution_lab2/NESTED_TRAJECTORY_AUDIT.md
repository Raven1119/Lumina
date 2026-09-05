# Nested Decision-Limit Trajectory Audit

## 1. Scope and evidence boundary

This audit answers why the three evidence-preserving Nested executions reached
the frozen eight-decision limit without Spawn or completion. It reads only the
surviving N1/N2/N3 workspaces and durable Root EventLogs from the authorized
six-run replication. No provider request was sent and no mechanism, prompt,
fixture, limit, schema or workspace was changed.

The reconstruction uses each durable `DecisionFrame.actual_request`,
`actual_tools_exposed`, provider-wire tool schema, redacted provider call
names, typed Action, Tool start/result events and final workspace. It does not
read or retain hidden reasoning.

## 2. Common capability and Context facts

- Every one of the 24 requests exposed `spawn_child(goal)` in both the
  Lumina Action contracts and actual provider-wire schemas.
- Root remained running at depth 0 with `max_depth=2`, one Actor, no accepted
  Child and therefore available Child/total-Actor capacity at every decision.
- Every request also exposed `read`, `write`, `shell`, `wait` and
  `claim_complete`. The provider emitted only `read` and `shell`.
- The caller-owned `FileContentEquals` expected content (42, 45 or 67) was
  model-visible together with the task Goal. Even so, no Write or
  ClaimComplete intent was emitted.
- No response contained Spawn, Write, ClaimComplete, Wait or IPython intent.
  No completion protocol was exercised and no `answer.txt` was created.
- All emitted calls passed the existing provider-id, JSON, argument-schema and
  cardinality admission checks; there was no model-protocol failure.
- Lumina request Context ranged from 275 to 1,816 characters. Actual
  provider-visible dynamic user/tool content ranged from 366 to 1,996
  characters, within the frozen 2,000-character bound. Relevant rendered
  outputs were marked `truncated=false`.
- The task text directly named the two module paths. Directory enumeration was
  not required to discover those first dependency edges.

In the tables, cumulative objects count the first successful observation of a
task dependency file: `task.txt`, two modules and two leaves. Directory
listings, error messages and the experiment's `root.jsonl` are not dependency
objects. `Y/no` under Spawn means the schema and capacity were available but
the provider emitted no Spawn call. Completion intent means Write or
ClaimComplete. Context cells show
`Lumina actual_request.context / provider-visible dynamic user+tool chars`;
the latter excludes fixed system text and tool schemas.

## 3. N1 trajectory

| Decision | Action / target | Observation and information value | Repeat? | Context / wire chars | Cumulative dependency objects | Spawn | Completion intent | Terminal |
|---:|---|---|---|---:|---:|---|---|---|
| 1 | `read task.txt` | New required task: names `amber.module`, `cobalt.module` | no | 609 / 609 | 1 | Y/no | no | continue |
| 2 | `shell ls -la`; `find . -maxdepth 3` | Both fail; first evidence of Unix-command incompatibility | no, but low value | 971 / 991 | 1 | Y/no | no | continue |
| 3 | `cmd /c dir /a /s /b` | New workspace listing, including source files and `root.jsonl`; not needed for the named modules | no, low value | 347 / 665 | 1 | Y/no | no | continue |
| 4 | absolute-path reads of `task.txt` and `root.jsonl` | Both rejected by workspace boundary; task target is already known and `root.jsonl` is irrelevant | yes / irrelevant | 1,816 / 1,573 | 1 | Y/no | no | continue |
| 5 | `read task.txt` | Same task content | yes | 327 / 645 | 1 | Y/no | no | continue |
| 6 | `find ... *.module`; `ls -la` | Repeats the two known shell failure modes | yes | 972 / 709 | 1 | Y/no | no | continue |
| 7 | `read task.txt`; `cmd /c dir` | Repeats task content and directory listing | yes | 347 / 665 | 1 | Y/no | no | continue |
| 8 | reads both modules and both leaves | Four new required contents; obtains values 17 and 25 | no, productive | 1,056 / 1,087 | 5 | Y/no | no | `decision_limit_reached` |

N1 had the complete dependency evidence only after its final decision. It
would still require a separate Write and ClaimComplete because control calls
cannot be mixed with an ordinary sibling batch. Six of its 15 observations
were repeats, and six Tool calls failed.

Classification: **D. LOCAL_SEARCH_STALL**. The late productive batch does not
make the run productively budget-bound: D6-D7 added no critical information.

## 4. N2 trajectory

| Decision | Action / target | Observation and information value | Repeat? | Context / wire chars | Cumulative dependency objects | Spawn | Completion intent | Terminal |
|---:|---|---|---|---:|---:|---|---|---|
| 1 | `read task.txt` | New required task: names `lunar.module`, `solar.module` | no | 609 / 609 | 1 | Y/no | no | continue |
| 2 | Unix `find` for both named modules | Fails with Windows `find` syntax error | no, low value | 970 / 990 | 1 | Y/no | no | continue |
| 3 | more complex Unix `find`; `ls -la` | Repeats `find` failure; first `ls` not-found result | partly | 1,038 / 1,419 | 1 | Y/no | no | continue |
| 4 | `cat task.txt`; bare `dir` | Both executables are unavailable through direct argv; same local enumeration problem | yes / low value | 347 / 665 | 1 | Y/no | no | continue |
| 5 | bare `ls` | Repeats known `ls` failure | yes | 335 / 366 | 1 | Y/no | no | continue |
| 6 | `cmd /c dir` | First successful workspace listing, but the task already named both modules | no, low value | 972 / 717 | 1 | Y/no | no | continue |
| 7 | `read task.txt` | Same task content | yes | 1,612 / 1,994 | 1 | Y/no | no | continue |
| 8 | reads `lunar.module`, `solar.module` | Two new required module contents; reveals both leaf paths | no, productive | 971 / 1,993 | 3 | Y/no | no | `decision_limit_reached` |

N2 ended before reading either leaf, writing, or claiming completion. Five of
11 observations repeated earlier targets/failure modes, and six Tool calls
failed.

Classification: **D. LOCAL_SEARCH_STALL**. Only the final decision among the
last three acquired critical dependency information.

## 5. N3 trajectory

| Decision | Action / target | Observation and information value | Repeat? | Context / wire chars | Cumulative dependency objects | Spawn | Completion intent | Terminal |
|---:|---|---|---|---:|---:|---|---|---|
| 1 | `read task.txt` | New required task: names `north.module`, `south.module` | no | 609 / 609 | 1 | Y/no | no | continue |
| 2 | `ls -la`; Unix `find` | Both fail; first evidence of command incompatibility | no, low value | 970 / 990 | 1 | Y/no | no | continue |
| 3 | bare `dir` | Another failed attempt at the same directory-enumeration goal | yes | 347 / 665 | 1 | Y/no | no | continue |
| 4 | `cmd /c dir` | First successful listing, but both module paths were already known | no, low value | 973 / 730 | 1 | Y/no | no | continue |
| 5 | `read task.txt` | Same task content | yes | 1,613 / 1,996 | 1 | Y/no | no | continue |
| 6 | reads `north.module`, `south.module` | Two new required module contents; reveals both leaf paths | no, productive | 971 / 1,994 | 3 | Y/no | no | continue |
| 7 | `read task.txt` | Repeats task despite both module outputs being model-visible | yes | 275 / 593 | 3 | Y/no | no | continue |
| 8 | `ls -la` | Repeats the known unavailable command; no new information | yes | 971 / 656 | 3 | Y/no | no | `decision_limit_reached` |

N3's final two decisions added no critical information. It never read either
leaf, wrote, or claimed completion. Four of 10 observations were repeats and
four Tool calls failed.

Classification: **D. LOCAL_SEARCH_STALL**.

## 6. Observation accounting

The three requested categories partition Tool observations by their first
task-relevant success, first low-value/unused result, or semantic repetition.
The failure column is diagnostic and overlaps the latter two columns.

| Run | Tool observations | Unique relevant | Unique irrelevant / unused | Repeated | Failed local-search calls | IPython |
|---|---:|---:|---:|---:|---:|---:|
| N1 | 15 | 5 | 4 | 6 | 6 | 0 |
| N2 | 11 | 3 | 3 | 5 | 6 | 0 |
| N3 | 10 | 3 | 3 | 4 | 4 | 0 |

This is not the V1 form of active over-acquisition. No run read `noise-a.txt`
or successfully consumed a broad set of unrelated file contents. The dominant
cost was repeated local discovery and command-compatibility failure. N1 alone
attempted the irrelevant `root.jsonl`, and ToolHost rejected it before any
content was returned.

## 7. The three special questions

### A. Is the decision budget the demonstrated bottleneck?

No. N1 and N2 obtained important new information only on D8 after low-value or
repeated D6-D7 behavior. N3's D7-D8 added no new information. More budget might
permit eventual completion, but these trajectories do not satisfy the required
criterion that the last two or three decisions continually gained necessary
information. Raising the budget is therefore not the next evidence-backed
experiment.

### B. Did V1 active over-acquisition return?

Not as the primary pattern. Each run acquired at most one successful workspace
listing outside the dependency contents and never read the noise file. The
larger loss came from repeated `ls`/`find`/`cat`/bare-`dir` attempts,
repeated task reads and, in N1, an apparatus-induced `root.jsonl` target.

### C. Was Spawn actually usable?

Yes. All 24 DecisionFrames and provider-wire requests included the
`spawn_child` schema. Root was active at depth 0, the frozen maximum depth was
2, actor count remained 1, no Child slot had been consumed, and no lifecycle
state blocked admission. The model emitted no Spawn call. This establishes
delegation non-selection within these trajectories, but the budget accounting
shows what replaced it: direct local search that repeatedly failed or revisited
known observations.

### Context boundary

No relevant output was text-truncated, but the bounded native continuation did
not carry the full Goal/CompletionSpec in every provider wire: Goal was present
in N1 5/8, N2 6/8 and N3 6/8 requests. This does not cleanly explain the
stall: N1 D8 was productive without the Goal, while repeated actions also
occurred in requests that did contain it. Context causality therefore remains
unestablished rather than ruled out.

## 8. Verdict

**REPETITIVE_LOCAL_SEARCH**

The common causal shape is:

`named dependency paths available -> direct local enumeration -> OS-incompatible
or redundant search -> late/partial direct reads -> decision limit`.

The evidence does not support a pure decision-budget bottleneck, generalized
active over-acquisition, unavailable Spawn, or completion protocol failure.
Completion was never attempted. Context projection may be a secondary factor,
but the observed primary pattern is still repeated local search.

## 9. Exactly one recommended next experiment

Run one fixed-task, one-variable Shell-surface A/B: keep the Nested fixtures,
prompt, decision/Context/cardinality/depth limits and provider settings fixed;
compare the current tool surface against the same surface with `shell`
withheld. Measure whether named-path `read` actions replace repetitive local
enumeration and whether completion or Spawn behavior changes.
