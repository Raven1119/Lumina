# Published Mind experiment evidence

The repository contains maintained code, tests, task contracts, result/review
summaries and selected immutable evidence. Full local D1-D6 campaigns are not
uploaded: per-call logs, repeated source snapshots, runtime journals, admission
records and duplicate aggregate results stay on the development machine.
`.gitignore` makes this selection explicit. No historical artifact was deleted
or rewritten during publication.

Start with [COGNITIVE_CHAIN_RESULT.md](COGNITIVE_CHAIN_RESULT.md) for the latest
conclusion. Publication does not change its **NOT_YET / INCONCLUSIVE** verdict.

## Included evidence

| File under `Mind/fixtures/` | Why it is published |
| --- | --- |
| `decoupling_d1/manifest.json` | D1 input cases; deterministic tests and experiment entry |
| `cognitive_contract_d3/acceptance/calls/004.json` | Exact archived empty return used by recovery regression |
| `protocol_recovery_d4/loop/license_change/result.json` | D4 wire compatibility and semantic regression |
| `semantic_revision_d5/acceptance/net_summary_revision/result.json` | D5 failed quotes and native request compatibility regression |
| `cognitive_chain_d6/development-1/result.json` | D6 v1 actual-wire reconstruction regression |
| `cognitive_chain_d6/acceptance/reverse_scope_summary/result.json` | Latest real workspace, submitted guidance, withheld delivery, Execution actions and returned cognition |
| `cognitive_chain_d6/registration.json`, `acceptance-cases.json` | Frozen budget, model conditions and independent sample definitions |
| `cognitive_chain_d6/analysis.json`, `engineering_reproduction.json`, `validation.json` | Compact quantitative, engineering and regression summaries |
| `semantic_revision_d5_development.json`, `semantic_revision_d5_acceptance.json` | Input fixtures, not campaign output dumps |

The selected historical records retain their original content and hashes.
Tests can run from a fresh checkout without the excluded local campaigns.
Old reports still name local artifact paths and frozen hashes for provenance;
those paths do not imply that every artifact is present on GitHub.

The complete-campaign offline analyzer and D6 source-to-baseline diff remain
local because their full input archives are intentionally not published.
Real-model campaign commands require a fresh output directory and explicit
preregistration; the published records are historical evidence, not rerun targets.

Publication verification exported only staged files, excluding local campaigns:
71 related tests passed. Two preregistration tests also passed after providing
read-only Git metadata, which an index export lacks. Imports and fixture reads
still came from the exported tree. No real-model calls were made.
