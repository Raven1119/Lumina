# D1: context separation and behavioral value

Task authority: the creator's Astra task, 2026-09-05. Baseline verified locally
and using `git ls-remote origin refs/heads/Execution_lab2`:
`9d63da7311baa7611782cc8079fb09e9d81f4e25`. Historical E/W/S verdicts are immutable.

## Source audit and selected seam

North Star requires continuous understanding and learning from reality, not a
module count. E0 establishes delivery mechanics; E1 had no equal-compute control
and its rescues had no Directive. E2 included an external-path attempt. E3/E4
evaluate guidance, not continued task completion. The cognition_minimal smoke
computed a correct answer but failed the output contract. None establishes the
requested decoupling effect.

ExecutionOrgan already supports suspended checkpoint copies and one-shot advice.
Reuse E1 `freeze_prefix`/`fork_prefix`, without changing their historical runner.
Execution currently projects bounded state and one native tool continuation,
not the entire trace. `FileContentEquals.expected_content` is model-visible:
use a completion marker only, with a separate hidden experiment evaluator for
the actual deliverable. Marker success must never stand in for task success.

Persistent Mind and Nervous can already conduct an explicit bounded activation.
Use their public interfaces, with computation and acquisition disabled. World
Model's model-writing/Builder tension is recorded and remains outside D1.

Codebase-design choice: one experiment module behind `run_case` and `run_campaign`,
one test module, one fixture manifest, and evidence/documents. No production
module changes. A protocol adapter reuses Execution's action parser and converts
its tool messages to the required official Anthropic-compatible wire format.
The existing native continuation projector drops a current decision advisory
when selecting only tool outcomes; D1's adapter explicitly carries that existing
advisory to the real wire in BOTH arms. This does not promote a production fix.

## Causal comparison

Twelve new synthetic tasks: eight seeded mistakes across four error families,
and four correct-direction controls. Host-selected seed actions create actual
Execution file/observation events; these are deliberately induced checkpoints,
not naturally occurring model mistakes. No live-model prefix selection.

Both arms receive the exact same bounded task facts, draft and working assumption
from the common Execution observation. Both use one identical cognitive prompt,
output contract and empty cognitive state. A retains the actual Execution
decision-context messages; B starts with only the bounded evidence projection.
A reuses Mind's validation/receipt implementation as experiment machinery, but
its MODEL CONTEXT retains Execution history: this is a controlled self-check,
not a second independent-context treatment. No role-prompt difference is added.

The baseline adapter's additional history is explicitly recorded in the wire
artifact alongside the ordinary Mind projection; a Mind trace alone is not
claimed to reconstruct that experimental baseline wire. Both returned judgments
use the same one-shot advisory seam before identical Execution continuation.
Both review wires include the same dormant native tool schemas with explicit
`tool_choice=none`, preserving the historical tool-message protocol in A without
granting either reviewer any tool authority. Any review tool-use output fails.

Primary estimand: effect of retaining versus excluding the current Execution
context during an otherwise identical, equally budgeted high-level review.
This is not evidence for long-term Mind identity, all-history contamination,
trigger discovery, generic self-reflection, or naturally arising failures.

## TDD seams and review

The task explicitly delegates interface/design choices. The seams selected
under that authorization are: real checkpoint/fork/continuation via `run_case`;
wire projection and data-only review via injected provider transport; external
objective scoring and single-use freeze via `run_campaign`. Tests cross these
interfaces, using scripted provider replies and actual Execution/Mind/Nervous.
Develop one RED/GREEN behavior at a time. No test reaches into an organ to mutate
state. Final code review compares this worktree against the verified baseline,
including new experiment files (there are no task commits yet).

Standards and Spec reviews independently cover causal validity, context
contamination, authority, Mind/Execution separation and Ponytail. Real calls
start only after pre-campaign review and deterministic checks.

## Frozen campaign policy (machine preregistration precedes all real calls)

- Provider: deepseek-v4-pro; official
  `https://api.deepseek.com/anthropic/v1/messages`; DEEPSEEK_API_KEY;
  thinking disabled; temperature 0; no retries; 30-second request timeout.
- Per arm: one review, max 1,000 output tokens / existing 2,000-character Mind
  contract; at most six subsequent Root decisions, 1,600 output tokens each.
  Same available Execution tools and same pre-execution authority checks.
  No Child work, scheduler, Memory or Chat. At most 168 real calls overall.
- Same checkpoint bytes, workspace content, completion marker, evidence and
  action budget. Alternate arm order by case. Cases run once; no replacement,
  prompt tuning, budget increases or resampling after campaign reservation.
- Exact task JSON equality is the primary score, computed outside the actor
  workspace after shutdown. Record marker completion separately. All required
  keys/values and JSON types must match; dictionary order/whitespace irrelevant.
- Before delivery, an independent development reviewer sees an opaque request
  ID, the original proposal and common evidence, without arm or Execution outcome.
  It approves/rejects abstraction/authority only; no advice editing, alternative
  answer, retry or correctness judgment is permitted. A five-minute bounded
  file handoff pauses this experiment until that decision is durable. Missing or
  rejected admission makes a formal value verdict INCONCLUSIVE. This is an
  experiment control, not a new Lumina agent or production scheduler. Its
  development-review cost/latency is separate from measured DeepSeek reasoning.
- Also record issue detection, Directive issuance, actual wire delivery,
  subsequent observable adoption, harmful/unnecessary intervention on controls,
  repeated exact actions, no-effect writes, and provider usage/latency.
  Textual issue/adoption coding is trace-based descriptive analysis, not the
  objective completion label or an automatic keyword classifier.
- Public-wire and source hashes, fixture bytes, paired identities, actual
  requests/results and final workspaces are retained. Credentials are never
  recorded; actual task workspaces are unrelated temporary paths, with no gold
  evaluator or sibling artifact exposed. The tool guard is a narrow experiment
  admission check, not a claim that arbitrary Python is OS-sandboxed.
- Provider/protocol failures remain results. Any unequal starting state,
  missing critical evidence, wrong/missing advisory wire, unauthorized access
  attempt, missing usage, or missing/rejected review admission makes the
  campaign INCONCLUSIVE. A detected low-level Mind plan is not delivered and
  invalidates a decoupling support claim. No hidden retries.
  Allocated review/continuation call and output-token ceilings are identical;
  actual input/output cost differences are reported as outcomes. In particular,
  earlier successful completion cannot invalidate an arm by reducing its cost.
- SUPPORTED requires a valid complete campaign, positive completion difference,
  at least two error families with B-only wins, no B-only control failure, and
  two-sided exact discordant-pair binomial p <= 0.05. B-only correction wins must
  carry a delivered, adopted high-level correction relevant to the actual error.
  B-only control wins can instead demonstrate correct NoChange preserving the
  already correct direction against A's harmful intervention.
- NOT_SUPPORTED means a valid complete campaign with B completion <= A and
  no missing-data explanation. It rejects benefit on these tasks only, not
  the universal possibility of independent cognition. Other outcomes are
  INCONCLUSIVE. No minimum baseline-failure gate that silently discards a
  ceiling result. Report ceiling effects explicitly.

Official protocol reference checked during audit:
https://api-docs.deepseek.com/guides/anthropic_api/
