# Memory Experiment History

## Purpose

This is the durable decision record for memory experiments that were rejected,
rolled back, or narrowed before the current architecture was adopted. It keeps
the evidence that affected design decisions without retaining provider response
bodies, large telemetry dumps, duplicate case artifacts, or temporary task
instructions.

Current behavior is authoritative in `docs/CURRENT_STATUS.md`. This document is
historical: nothing described as an experiment is a production capability unless
the adopted consequence explicitly says so.

## Adopted baseline

```text
Cold raw conversation
-> explicit bounded Dream
-> MiniMax-M3 Grounded Formation, non-thinking, max_tokens=2000
-> GroundedMemoryUnit with exact source provenance
-> deterministic validator
-> bounded semantic-equivalence fallback
-> value-only and hard-detail safety checks
-> durable formed-unit checkpoint before MAGMA
-> pinned MAGMA
-> bounded anchors/traversal
-> BGE rerank
-> Hindsight score composition
-> final_min_score >= 0.144
-> optional ControlledRelationResolver compatibility gate
   (resolved mismatch rejects; UNRESOLVED fails open)
-> bounded MemoryContext
```

Normal Chat currently supplies raw query text but no query relation metadata.
The controlled relation gate therefore remains a structured-caller/future-Mind
capability; it does not parse free-text queries.

## Experiment decisions

### Local cosine relevance threshold

- **Hypothesis:** a local cosine threshold could suppress irrelevant memories.
- **Experiment:** 20 positive and 20 negative synthetic Recall queries.
- **Result:** positive and negative score ranges overlapped; no threshold reached
  both 0.90 positive recall and 0.10 or lower false injection.
- **Why rejected:** there was no safe operating point.
- **Architectural lesson:** semantic similarity alone is not an admission
  contract.
- **Adopted consequence:** the temporary scorer, policy field, DTOs, fixtures,
  and calibration path were removed. Recall uses the later BGE/Hindsight path.

### Recall depth and adaptive traversal variants

- **Hypothesis:** deeper or query-adaptive graph traversal would recover missing
  temporal and cross-turn evidence.
- **Experiment:** fixed depth variants and a bounded source-faithful adaptive
  traversal were compared while keeping ranking and evidence bounds fixed.
- **Result:** deeper traversal increased candidate exposure without a stable
  correctness gain; cross-turn antecedents remained only partially recoverable.
- **Why rejected:** no variant produced a sufficiently clear net improvement.
- **Architectural lesson:** graph reachability does not establish semantic
  relevance or dialogue binding.
- **Adopted consequence:** production remains fixed at depth 1; the rejected
  optional adaptive fields and implementation were removed after the Memory
  freeze.

### GENERAL relation-weight rebalance

- **Hypothesis:** balanced graph relation weights would improve multi-hop Recall.
- **Experiment:** upstream fallback weights (ENTITY 0.60, SEMANTIC 0.30,
  TEMPORAL/CAUSAL 0.05) were compared with a balanced alternative.
- **Result:** the alternative improved one multi-hop slice but temporal Recall
  fell from 0.916667 to 0.791667 and temporal NDCG from 0.767828 to 0.693156;
  strict-gold noise did not improve.
- **Why rejected:** the tradeoff was not a net production gain.
- **Architectural lesson:** changing traversal weights cannot substitute for a
  query contract.
- **Adopted consequence:** the source-faithful fallback weights were retained.

### Upstream temporal-ranking reuse

- **Hypothesis:** upstream MAGMA contained a generic temporal ranker Lumina could
  reuse.
- **Experiment:** source-level audit of query parsing, date helpers, and the
  benchmark reranker.
- **Result:** relevant helpers depended on current-time fallback,
  benchmark/session metadata, and English-specific heuristics; there was no
  safe generic implementation to port.
- **Why rejected:** importing it would violate provenance and source-faithful
  reuse rules.
- **Architectural lesson:** inactive benchmark helpers are not an active generic
  query path.
- **Adopted consequence:** no custom temporal ranking was added.

### Free-text Formation protocol and validator audit

- **Hypothesis:** most Grounded Formation loss came from semantic omission.
- **Experiment:** frozen five-case M2.7 runs separated HTTP, JSON protocol,
  candidate grounding, and validator outcomes.
- **Result:** one audit scored 4/7 expected facts: two correction slots were lost
  to malformed JSON and one destination slot to bad grounding. A validator audit
  separately found four source-complete semantic paraphrases rejected by
  literal SRV checks and two correct insufficient-ref rejections.
- **Why the original interpretation was rejected:** protocol, Formation, and
  validation losses had been conflated.
- **Architectural lesson:** transport, proposal coverage, source sufficiency,
  and validation require separate metrics.
- **Adopted consequence:** bounded semantic-equivalence fallback was added while
  exact refs, role authorization, hard details, negation, and uncertainty stayed
  deterministic.

### MiniMax-M3 as the dedicated Formation model

- **Hypothesis:** M3 would improve Formation without changing the chat model.
- **Experiment:** M2.7 and M3 each ran the same frozen five cases once.
- **Result:** M2.7 achieved 4/7 with one protocol-invalid case; M3 achieved 6/7,
  5/5 protocol success, zero unsupported accepts, and assistant-contamination
  PASS.
- **Why accepted:** higher operational recall and protocol reliability with no
  safety loss.
- **Architectural lesson:** Formation can use a dedicated model at the existing
  model-client seam without changing Chat.
- **Adopted consequence:** real-model Dream uses non-thinking MiniMax-M3.

### Structured/tool-call Formation transport

- **Hypothesis:** forced tool arguments would eliminate malformed primary JSON.
- **Experiment:** the same frozen five cases were run through the minimal
  MiniMax tool-call schema, with exact candidate/validator/fallback telemetry.
- **Result:** protocol behavior improved in some runs, but one revalidation still
  produced only 4/7 with one missing/invalid tool result. An earlier run exposed
  an unsupported destination candidate citing only `上海。`, proving that
  structured transport did not itself guarantee grounding safety.
- **Why rejected:** the required zero protocol-failure and zero unsupported-
  acceptance gates were not jointly stable.
- **Architectural lesson:** response structure and proposition grounding are
  independent contracts.
- **Adopted consequence:** tool-call transport was rolled back; production keeps
  one strict free-text JSON Formation call and fails closed.

### Value-only semantic-fallback eligibility

- **Hypothesis:** a source span containing only the answer value was sufficient
  input for semantic fallback.
- **Experiment:** exact candidate capture/replay covered `上海。 -> 上海`, bare
  access-pass/frequency values, valid full-span paraphrases, and contamination
  controls.
- **Result:** the destination candidate followed normal reject -> semantic
  eligible -> fallback true -> final accept despite refs insufficient for its
  complete S/R/V proposition.
- **Why rejected:** value presence alone does not establish subject/relation
  binding.
- **Architectural lesson:** semantic fallback eligibility needs a deterministic
  minimum evidence contract.
- **Adopted consequence:** trivial punctuation-normalized value-only refs are
  ineligible. Full-proposition paraphrases remain eligible.

### Source-ref binding prompt

- **Hypothesis:** one narrow prompt sentence could make Formation cite complete
  S/R/V evidence.
- **Experiment:** current prompt versus the same prompt plus a collective
  source-binding instruction, one five-case run per arm.
- **Result:** A scored 2/7; B scored 0/7. B increased protocol-failed slots from
  3 to 6, did not fix multi-turn binding, and lost working correction facts.
- **Why rejected:** it regressed protocol/extraction quality without clear
  binding improvement.
- **Architectural lesson:** prompt pressure is not a reliable provenance repair
  mechanism.
- **Adopted consequence:** the Formation prompt was restored unchanged.

### Deterministic multi-turn source-ref completion

- **Hypothesis:** an immediately preceding assistant question could be appended
  to a short-answer candidate deterministically.
- **Experiment:** a narrow `[u1,u2] -> [u1,a1,u2]` completion rule with unrelated
  question/assertion controls, followed by one M3 smoke.
- **Result:** synthetic focused tests passed, but the real candidate cited only
  `u2: 上海。`; the rule's required input shape never appeared. Frozen smoke
  remained 5/7 and the implementation/tests were rolled back.
- **Why rejected:** broadening the rule would require general dialogue
  understanding or coreference.
- **Architectural lesson:** do not hide conversational interpretation inside a
  deterministic span-expander.
- **Adopted consequence:** multi-turn source binding remains a known limitation.

### Assistant self-action semantic authorization

- **Hypothesis:** assistant first-person natural-language actions could enter the
  semantic fallback using existing lexical self-action authorization.
- **Experiment:** eight exact assistant-source candidates were audited alongside
  the `5433` assistant-contamination control.
- **Result:** the propositions were linguistically supported, but truth depended
  only on assistant utterance; no verified tool/execution provenance existed.
- **Why rejected/deferred:** globally or semantically authorizing assistant text
  would weaken the contamination contract.
- **Architectural lesson:** an utterance is not proof of execution.
- **Adopted consequence:** literal existing behavior and contamination controls
  remain; broader self-action memory waits for verified execution provenance.

### AP-471 cross-field hard-detail extraction

- **Hypothesis:** a hard-detail mismatch on `f/4.8` represented unsupported data.
- **Experiment:** exact provider-free replay of the AP-471 temporal candidate.
- **Result:** the detail regex crossed the value/referenced-time field boundary
  and synthesized `4.8\nearlier`.
- **Why the old behavior was rejected:** the mismatch was validator-generated,
  not source-generated.
- **Architectural lesson:** candidate fields are independent semantic fields and
  must be scanned independently.
- **Adopted consequence:** hard details are extracted per field; the safety gates
  themselves are unchanged.

### Formation output budget

- **Hypothesis:** large 20-24 turn batches failed because 1000 output tokens
  truncated otherwise valid JSON.
- **Experiment:** three frozen corpora at 1000 versus 2000 tokens.
- **Result:** at 1000, two observable responses ended at exactly the token limit
  with truncated JSON; at 2000 all three parsed with `end_turn`, using at most
  1775 tokens. Thirty-one accepted units were independently source-supported.
- **Why accepted:** protocol failures fell to zero without changing Chat's
  1000-token budget.
- **Architectural lesson:** caller-specific budget belongs at composition, not in
  a global model default.
- **Adopted consequence:** Grounded Formation uses 2000 tokens; Chat remains 1000.

### Grounded Write Recall shadows

- **Hypothesis:** source-grounded atomic units would improve end-to-end evidence
  quality versus raw-turn memory.
- **Experiment:** the same 60-case synthetic set was replayed through Grounded
  Write, first before and then after the output-budget fix.
- **Result:** raw-turn baseline was 37/60. Grounded Write scored 28/60 before the
  fix and 29/60 after it. Negative correctness improved, but positive completeness
  fell from 17/30 to 6/30 after the fix. All accepted units were supported; the
  main loss was before Recall (Formation/validation), and every compatible
  positive with complete formed evidence was recalled.
- **Why rejected as a quality win:** the precision gain did not offset write-side
  recall loss.
- **Architectural lesson:** read-side tuning cannot recover facts never admitted
  to durable memory.
- **Adopted consequence:** Grounded Write safety/durability remains, but its
  synthetic shadow is not treated as proof of overall Recall improvement.

### Grounded Write current-contract reevaluation

- **Reason for reclassification:** the original 60-case benchmark included 24
  positive cases whose required facts depended only on assistant utterances.
  Those cases are `ASSISTANT_UTTERANCE_ONLY` and are outside the currently
  authorized Grounded Write contract.
- **Aligned subset:** 36 cases: 6 currently authorized positive cases and 30
  negative cases.
- **Raw-turn baseline:** 26/36 overall, comprising 6/6 positive completeness
  and 20/30 negative correctness; unsupported evidence was returned for 10/30
  negative cases.
- **Grounded Write:** 29/36 overall, comprising 6/6 positive completeness and
  23/30 negative correctness; unsupported evidence was returned for 7/30
  negative cases.
- **Formation accounting:** the aligned subset contains 24 currently authorized
  required facts. All 24 were contract-valid: 0 semantic omissions, 0 validator
  rejections, 0 bad-grounding failures, and 0 output-protocol failures.
- **Authorization boundary:** an assistant utterance alone is not verified fact
  or self-action provenance. Future assistant self-action memory remains
  deferred until Execution Trace or tool-result provenance can authorize it.
- **Consequence:** the historical 60-case result above remains intact, while the
  aligned 36-case reevaluation is the applicable comparison for the current
  Grounded Write authorization contract.

### Structured GroundedMemoryUnit rendering for BGE

- **Hypothesis:** rendering `subject/relation/value/text` into the existing BGE
  input would improve wrong-relation discrimination.
- **Experiment:** provider-free A/B on the current-contract subset with MAGMA,
  BGE, Hindsight, thresholds, and admission frozen.
- **Result:** structured candidate rendering did not safely remove the audited
  wrong-relation evidence while preserving all positive controls.
- **Why rejected:** candidate-side formatting cannot express what relation the
  query requests.
- **Architectural lesson:** relation compatibility is a two-sided contract.
- **Adopted consequence:** production BGE still scores raw query against
  `candidate.text`.

### Relation-surface BGE compatibility

- **Hypothesis:** multilingual BGE scores between query relation surface and
  memory relation could approximate oracle relation compatibility.
- **Experiment:** development, calibration, and untouched holdout sets included
  bilingual synonyms, paraphrases, compounds, and hard near-relations.
- **Result:** aggregate signal was useful, but FULL/PARTIAL/NO distributions
  overlapped and no safe gate threshold preserved valid cross-language controls
  while rejecting hard negatives.
- **Why rejected:** high average separability was not a safe operating point.
- **Architectural lesson:** embeddings rank similarity; they do not create a
  strict compatibility contract.
- **Adopted consequence:** no relation-only BGE threshold was added.

### Controlled relation vocabulary and resolver

- **Hypothesis:** a small explicit bilingual vocabulary could provide strict
  compatibility without an ontology or query LLM.
- **Experiment:** oracle audit, then a calibration/untouched-holdout deterministic
  resolver using 10 relation IDs, 40 aliases, and 4 context rules.
- **Result:** automatic resolution had zero false canonicalizations and reproduced
  the oracle holdout gain: 25/28 to 28/28, wrong-relation negatives 7/10 to
  10/10, with positives preserved 14/14. Uncertainty remained first-class.
- **Why accepted:** resolved mismatches were safe and UNRESOLVED could fail open.
- **Architectural lesson:** a small explicit interface is safer than thresholded
  semantic similarity, but it needs caller-supplied relation surfaces.
- **Adopted consequence:** `ControlledRelationResolver` is in production Recall
  for structured callers. No entity resolver or free-text query parser exists;
  normal Chat currently supplies no relation metadata.

### Strict answer-format evaluation

- **Hypothesis:** exact displayed-answer conformance measured memory correctness.
- **Experiment:** 26 complete-evidence cases were sent through the answer reader.
- **Result:** exact format scored 6/26 while semantic correctness was 23/26;
  17 failures were format-only.
- **Why rejected as the memory metric:** output-shell compliance obscured whether
  correct evidence was available and understood.
- **Architectural lesson:** evidence completeness/safety and answer formatting are
  separate evaluation layers.
- **Adopted consequence:** memory evaluations use semantic evidence correctness,
  not strict answer formatting.

### Mind-supplied relation surfaces (v3rel shadow)

- **Hypothesis:** the single Mind gate call could additionally emit query
  relation surfaces, letting the existing `ControlledRelationResolver` operate
  in normal Chat.
- **Experiment:** offline shadow (`docs/experiments/mind_relation_shadow/`):
  `mind-gate-v3rel` (one call, JSON `{recall, relations}` with raw surface
  text), 30-case labeled set in the 10-relation vocabulary domain, 3 runs,
  vs v2 baseline and label-oracle, with real adapter+resolver downstream on
  synthetic correct/distractor memory pairs.
- **Result (2026-08-16):** protocol_valid 97.78% (2/90 empty provider
  responses, both fail-open caught) missed the pre-registered 100% bar;
  extraction accuracy was single_zh 25% / single_en 17.6% / compound 0%;
  wrong-relation rejection 20.75% vs oracle 100%. Safety was clean:
  unsafe_failure=0, zero recall-bit regression vs v2, zero correct-evidence
  kills, OOV probes produced genuine resolver UNRESOLVED 11/12.
- **Why the mechanism is not established:** the bottleneck is query-side
  canonicalization — the model's free-text surfaces (raw canonical IDs,
  subject-prefixed phrases, combined phrases) mostly miss the exact-match
  alias table; oracle proves the resolver gate itself works once surfaces
  match. Fail-open guarantees the worst case degrades to baseline.
- **Architectural lesson:** a closed exact-match vocabulary does not absorb
  free-text model output; either the prompt must constrain output to verbatim
  aliases (moving canonicalization into prompt compliance) or the resolver
  must grow normalization — both are separate future experiments.
- **Adopted consequence:** none. Production keeps `relation_surfaces=None`
  from normal Chat; the resolver remains a structured-caller seam. No
  production wiring is suggested; the 10-relation industrial vocabulary does
  not support production-scope claims.

### Entity wiring shadow (extraction → EntityRef → EntityNode → recall)

- **Hypothesis:** the validated entity-only extraction + exact-span gate could
  drive the full write-side wiring — deterministic EntityRef binding,
  graph-only EntityNodes, REFERS_TO edges, persistence/reload, and
  entity-conditioned recall — end to end on isolated state.
- **Experiment:** TEMP shadow over an isolated `RealMagmaBackend` sandbox
  (raw results consolidated here; harness removed): 5 synthetic cases with
  real MiniMax-M3 extraction, one call per case, exact-surface EntityRef
  binding (REUSE / stable CREATE / UNRESOLVED), subject/object role edges at
  that design stage, then reload and entity-conditioned recall.
- **Result (2026-08-16):** all 5 cases schema-valid; node/edge counts
  identical before and after a retry replay (3 nodes / 7 edges → 3 / 7,
  idempotent); zero wrong merges and zero wrong news; `23` and `PRJ-204`
  correctly produced no entity. The known `Tlhey` extraction miss reproduced
  (extraction failure, not a wiring failure).
- **Adopted consequence:** the wiring seam was confirmed feasible; the
  subject/object role-edge shape was later superseded by the generic
  Event→Entity `REFERS_TO` design validated in
  `docs/experiments/multi_entity_recall_gain/` and promoted in the
  multi-entity production slices. The shadow itself was TEMP-only.

### MAGMA C.1 structured event-metadata extraction shadow

- **Hypothesis:** a MAGMA-paper-style C.1 extraction call (entities,
  relationships, semantic facts, dates, topic, summary) could supply richer
  structured metadata than current extraction.
- **Experiment:** TEMP shadow with 5 frozen synthetic fixtures, real
  MiniMax-M3 calls, per-item source-span grounding measurement; no Dream,
  Cold, MAGMA, or checkpoint access.
- **Result (2026-08-16): FAIL.** Entity recall 1.0 with zero spurious (vs
  0.4167 baseline), but relationship/fact/topic/summary outputs were largely
  paraphrases rather than source spans: exact source-span grounding 0.4762,
  relationship grounding precision 0.1250, semantic-fact grounding precision
  0.2000, field-level abstention precision 0.5333, and one unsupported
  organizer inference.
- **Why rejected:** the grounded-evidence contract requires every retained
  structured item to be traceable to frozen source text; C.1-style free
  generation cannot supply that per-item evidence trace.
- **Adopted consequence:** none. Structured-metadata enrichment was not
  pursued; the entity-only + exact-span gate direction (see
  `docs/experiments/entity_mention_extraction/`) was the viable alternative.

## Retained regression evidence

Two compact JSON fixtures remain because maintained Formation regressions load
their exact candidate/source projections directly:

- `docs/FORMATION_VALIDATOR_AUDIT_CASES.json`
- `docs/EXACT_9_VALIDATOR_REJECTION_AUDIT_CASES.json`

They are test inputs, not provider telemetry or production configuration. All
other superseded case dumps and temporary experiment harnesses were removed
after their decision-relevant conclusions were consolidated above.
