# Historical Relevance Gate Experiment

## Result

A local cosine threshold gate was evaluated against 20 positive and 20
negative synthetic Recall queries on 2026-07-14. Positive and negative score
ranges overlapped; no threshold simultaneously achieved at least 0.90 positive
recall and at most 0.10 false injection rate. The result was
`threshold_not_recommended`.

## Current status

The experiment is not a current system capability. Post-experiment tightening
removed its production and acceptance surface:

- no `RecallPolicy.min_relevance` or candidate-scoring bound;
- no encoder interception or query/evidence vector fields;
- no relevance scorer Protocol or DTOs;
- no calibration CLI, fixture, gate tests, or E2E branch;
- no `relevance_score` in public evidence.

Recall continues to use bounded MAGMA retrieval followed by deterministic
Lumina projection, item-count bounds, and character bounds. The required
negative Recall query remains in the authoritative E2E, but it is a
source-bounded/non-fabrication check rather than a claim that irrelevant MAGMA
results are filtered.

Any future relevance mechanism requires separate authorization, new evidence,
and a fresh design. This historical result does not justify restoring the
removed cosine threshold path.
