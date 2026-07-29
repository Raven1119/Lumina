# Conversation Memory Recall Depth Evaluation

## Scope

This is a small synthetic retrieval-layer ablation of the current Conversation
Memory implementation. It compares `max_graph_depth` values 0, 1, and 2 on the
same ingested memory store.

It evaluates evidence retrieval, not final answer quality. It does not use an
answer model, an LLM Judge, or a complete academic benchmark.

## Dataset

- Dataset: `recall-depth-eval-v1`
- Synthetic conversation turns: 48
- Evaluation questions: 20
- Categories: 4 questions each for `direct_fact`, `multi_hop`, `temporal`,
  `knowledge_update`, and `causal_control`
- No-answer controls: 2 questions within `causal_control`
- Data: entirely fictional, with no real user conversations or private data

Each source turn has a unique stable fixture evidence ID and an aware timestamp.
The turns span twelve synthetic conversations and include state changes,
cross-conversation relationships, low-keyword-overlap links, and
surface-similar distractors.

Gold evidence contains only source turns needed to support the question. Related
background and paraphrases are not marked gold. Consequently, the reported
irrelevant-evidence ratio is a strict-gold diagnostic; it does not prove that
every non-gold item is semantically useless.

## Configuration

All three conditions used one temporary marker-owned MAGMA store, the same 48
ingested memories, the same MiniLM model, the same questions, and the same
non-depth policy:

| Parameter | Value |
| --- | ---: |
| `top_k` | 1 |
| `max_nodes` | 200 |
| `max_evidence_items` | 6 |
| `max_chars` | 6000 |
| allowed traversal types | temporal, semantic, causal |

Only `max_graph_depth` changed:

| Condition | `max_graph_depth` |
| --- | ---: |
| depth 0 | 0 |
| depth 1 | 1 |
| depth 2 | 2 |

The fixed `max_nodes=200` is larger than the 48 ingested event nodes. The public
`MemoryContext` does not expose traversal visited-node statistics, so the
evaluation cannot directly prove that `max_nodes` was never reached. No
production API was added to expose that internal statistic.

One unmeasured depth-2 Recall was performed before timing. Every question then
ran in the fixed order depth 0, depth 1, depth 2.

## Metrics

- **Evidence Recall:** retrieved gold items divided by all gold items. No-answer
  controls are excluded.
- **Complete-hit rate:** fraction of answerable questions for which every gold
  item was retrieved.
- **NDCG:** binary relevance over the complete returned list, with gold items
  relevant and all other items non-relevant. Controls are excluded.
- **Irrelevant evidence ratio:** non-gold items divided by all returned items,
  using the strict gold labels described above.
- **Control nonempty rate:** fraction of no-answer controls returning any
  evidence.
- **Latency:** wall-clock Recall time in the current local environment.
- **Evidence count:** number of public `MemoryEvidence` items.
- **Rendered chars:** length of public `MemoryContext.rendered_text`.
- **Truncated rate:** fraction of results where the existing evidence-count or
  character bound reported truncation.

## Results

### Overall by depth

The following values are from the primary recorded run:

| Depth | Evidence Recall | Complete hit | NDCG | Irrelevant ratio | Control nonempty | Control evidence | Latency ms | Evidence count | Rendered chars | Truncated |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0.208 | 0.056 | 0.444 | 0.600 | 1.000 | 1.00 | 11.331 | 1.00 | 67.55 | 0.000 |
| 1 | 0.620 | 0.389 | 0.496 | 0.802 | 1.000 | 6.00 | 11.304 | 5.85 | 415.55 | 0.600 |
| 2 | 0.620 | 0.389 | 0.496 | 0.802 | 1.000 | 6.00 | 12.765 | 5.85 | 415.55 | 0.600 |

Depth 1 versus depth 0:

- Evidence Recall increased by 0.412 absolute.
- Complete-hit rate increased by 0.333 absolute.
- NDCG increased by 0.051 absolute.
- Strict-gold irrelevant ratio increased by 0.202 absolute.
- Mean evidence count increased from 1.00 to 5.85.
- Mean rendered context increased from 67.55 to 415.55 characters.
- Control questions expanded from 1 to 6 returned items on average.

Depth 2 returned exactly the same ordered evidence as depth 1 for all 20
questions. It therefore added no retrieval value in this dataset. Its primary
run latency was 1.461 ms higher than depth 1, but these small local timing
differences are not a production SLA.

The 60% truncation rate at depths 1 and 2 was caused by the existing evidence
count bound when more than six candidates were available; rendered text stayed
far below the 6000-character limit.

### Category by depth

| Category | Depth | Recall | Complete hit | NDCG | Irrelevant | Latency ms | Evidence count | Rendered chars |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| direct_fact | 0 | 0.250 | 0.250 | 0.250 | 0.750 | 10.669 | 1.00 | 63.50 |
| direct_fact | 1 | 0.750 | 0.750 | 0.465 | 0.858 | 10.371 | 5.50 | 378.50 |
| direct_fact | 2 | 0.750 | 0.750 | 0.465 | 0.858 | 12.496 | 5.50 | 378.50 |
| multi_hop | 0 | 0.229 | 0.000 | 0.750 | 0.250 | 11.370 | 1.00 | 66.25 |
| multi_hop | 1 | 0.625 | 0.250 | 0.601 | 0.650 | 11.096 | 5.75 | 411.25 |
| multi_hop | 2 | 0.625 | 0.250 | 0.601 | 0.650 | 11.835 | 5.75 | 411.25 |
| temporal | 0 | 0.333 | 0.000 | 0.750 | 0.250 | 10.758 | 1.00 | 81.25 |
| temporal | 1 | 0.792 | 0.500 | 0.731 | 0.708 | 11.139 | 6.00 | 445.50 |
| temporal | 2 | 0.792 | 0.500 | 0.731 | 0.708 | 11.992 | 6.00 | 445.50 |
| knowledge_update | 0 | 0.125 | 0.000 | 0.250 | 0.750 | 10.930 | 1.00 | 59.00 |
| knowledge_update | 1 | 0.500 | 0.250 | 0.375 | 0.833 | 10.676 | 6.00 | 421.50 |
| knowledge_update | 2 | 0.500 | 0.250 | 0.375 | 0.833 | 12.181 | 6.00 | 421.50 |
| causal_control | 0 | 0.000 | 0.000 | 0.000 | 1.000 | 12.927 | 1.00 | 67.75 |
| causal_control | 1 | 0.250 | 0.000 | 0.119 | 0.958 | 13.238 | 6.00 | 421.00 |
| causal_control | 2 | 0.250 | 0.000 | 0.119 | 0.958 | 15.319 | 6.00 | 421.00 |

Temporal questions received the largest mean Recall at depth 1 (0.792).
Direct-fact questions also improved because three of four vector anchors were
not the strict gold turn. Multi-hop and knowledge-update questions gained
substantially but remained incomplete. The two causal questions remained the
weakest answerable group.

### Per-question differences

- `direct_fact_01` was already a complete anchor-only hit. Graph expansion kept
  the gold item but added four non-gold items.
- `multi_hop_01` improved from zero gold items at depth 0 to a complete
  three-item hit at depth 1.
- `temporal_02` and `temporal_04` improved from partial to complete hits.
- `knowledge_update_01` improved from zero to a complete two-item hit.
- `multi_hop_02`, `knowledge_update_02`, and `causal_control_02` did not improve;
  expansion around the selected anchor did not reach the required later state
  within the public evidence bound.
- Both no-answer controls returned evidence at every depth. Expansion raised
  their evidence count from one to six.
- No question changed its ordered evidence list between depth 1 and depth 2.

## Interpretation

1. **Is depth 1 better than depth 0?** Yes for target-evidence coverage:
   Recall and complete-hit rate increased substantially. The improvement came
   with much larger public contexts and more strict-gold noise.
2. **Is depth 2 better than depth 1?** No. It produced no evidence, ordering, or
   metric change for any question in this dataset.
3. **Which categories benefit?** Temporal benefited most, followed by direct
   fact, multi-hop, and knowledge update. Causal questions received only a small
   gain.
4. **Which categories gained noise?** Every category returned more non-gold
   evidence. Direct fact and knowledge update had the highest depth-1
   strict-gold irrelevant ratios; controls expanded to the full evidence bound.
5. **Does this support a minimal routing design?** Yes, at design-experiment
   level only. Some questions require graph expansion for gold coverage, while
   an already-correct direct anchor and no-answer controls show clear expansion
   cost without additional target evidence.

## Limitations

- The dataset contains only 20 questions and 48 synthetic turns.
- Gold evidence was manually designed and is intentionally strict.
- There is no final-answer generation or answer-quality evaluation.
- There is no LLM Judge.
- The no-LLM MAGMA graph is dominated by current temporal, semantic, and entity
  relationship construction; results may differ with other data distributions.
- Public Recall does not expose traversal visited-node statistics.
- Latency represents this Windows local environment only and is not a
  production SLA.
- The result cannot be extrapolated directly to LoCoMo, LongMemEval, or real
  private user conversations.
- This evaluation does not determine a production routing classifier or
  threshold.

## Reproducibility

The evaluation was run twice before report generation. Excluding wall-clock
latency, both runs produced the same ordered evidence and metric fingerprint:

```text
45cc6b924ef515b3ad187eab4bd8aea8591d9149c043a6c81a158da022a7ee01
```

The script validates the fixture, category counts, gold references, complete
depth coverage, equal non-depth parameters, metric ranges, public evidence
bounds, depth-0 anchor bound, duplicate evidence, and default marker-owned
temporary-directory cleanup.

## Recommendation

**A. Support the next step of designing a minimal anchor-only / graph-enhanced
route.**

The evidence supports only a two-choice design experiment:

- anchor-only for cases where direct evidence is sufficient;
- graph-enhanced with fixed depth 1 when additional linked evidence is needed.

Depth 2 is not justified by this evaluation. This recommendation does not
authorize a production scheduler, query classifier, dynamic budget, edge-type
router, Evidence Organizer, or M-FLOW-style multi-stage system.
