# GENERAL Relation-Weight Ablation

## Scope

This is a read-only GENERAL relation-weight ablation on a fixed synthetic Conversation Memory corpus. It evaluates retrieved evidence, not final answers or an LLM judge. It is not LoCoMo or LongMemEval, uses no real user data, and does not change production configuration or behavior.

## Variants

| Variant | ENTITY | SEMANTIC | TEMPORAL | CAUSAL |
| --- | ---: | ---: | ---: | ---: |
| A — upstream fallback | 0.60 | 0.30 | 0.05 | 0.05 |
| B — balanced non-causal | 1/3 | 1/3 | 1/3 | 0 |

Both use the production transition formula `0.6 * relation_weight + 0.4 * cosine_similarity`. Candidate B is an experimental control, not a new default.

## Fixed configuration

- Dataset: `recall-depth-eval-v1`; 48 turns and 20 questions.
- Categories: `{"causal_control": 4, "direct_fact": 4, "knowledge_update": 4, "multi_hop": 4, "temporal": 4}`.
- Recall policy: `{"beam_width": 10, "drop_threshold": 0.15, "intent": "GENERAL", "max_chars": 6000, "max_evidence_items": 6, "max_graph_depth": 2, "max_nodes": 200, "temporal_window": null, "top_k": 1}`.
- Anchors: the same production dense + lexical RRF path; `top_k=1`.
- Traversal and rendering: production Adaptive Traversal and GENERAL Context Linearization through `MemoryRetriever.recall(...)`.
- Runs: one unmeasured warm-up per variant, then 3 complete timed repetitions per variant.
- Environment: Python 3.12.13 on `Windows-11-10.0.26200-SP0`; latency is local-only.
- The sole A/B variable was the private GENERAL relation-weight mapping.

## Results

### Overall

| Metric | A | B | B - A |
| --- | ---: | ---: | ---: |
| Evidence Recall | 0.763889 | 0.736111 | -0.027778 |
| Complete-hit rate | 0.555556 | 0.611111 | +0.055555 |
| NDCG | 0.594515 | 0.582722 | -0.011793 |
| Strict-gold noise ratio | 0.700833 | 0.709167 | +0.008334 |
| Mean evidence count | 5.150000 | 5.150000 | +0.000000 |
| Mean rendered characters | 487.100000 | 483.500000 | -3.600000 |
| Mean Recall latency (ms) | 27.364367 | 28.701867 | +1.337500 |

### By category

| Category | Variant | Recall | Complete hit | NDCG | Noise | Evidence | Chars | Latency ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| direct_fact | A | 1.000000 | 1.000000 | 0.640402 | 0.762500 | 4.500000 | 408.250000 | 25.174417 |
| direct_fact | B | 1.000000 | 1.000000 | 0.640402 | 0.762500 | 4.500000 | 408.250000 | 25.545500 |
| multi_hop | A | 0.645833 | 0.250000 | 0.705922 | 0.458334 | 4.750000 | 456.500000 | 26.463167 |
| multi_hop | B | 0.645833 | 0.500000 | 0.738152 | 0.458333 | 4.750000 | 449.750000 | 28.565083 |
| temporal | A | 0.916667 | 0.750000 | 0.767828 | 0.633333 | 5.500000 | 526.250000 | 29.086333 |
| temporal | B | 0.791667 | 0.500000 | 0.693156 | 0.675000 | 5.500000 | 526.750000 | 27.947417 |
| knowledge_update | A | 0.750000 | 0.500000 | 0.464450 | 0.733333 | 5.750000 | 544.000000 | 29.520833 |
| knowledge_update | B | 0.750000 | 0.750000 | 0.453824 | 0.733333 | 5.750000 | 535.750000 | 30.274750 |
| causal_control | A | 0.250000 | 0.000000 | 0.193427 | 0.916667 | 5.250000 | 500.500000 | 26.577083 |
| causal_control | B | 0.250000 | 0.000000 | 0.193427 | 0.916667 | 5.250000 | 497.000000 | 31.176583 |

### No-answer controls

| Variant | Controls | Nonempty rate | Mean evidence count |
| --- | ---: | ---: | ---: |
| A | 2 | 1.000000 | 6.000000 |
| B | 2 | 1.000000 | 6.000000 |

### Determinism and fairness

- Fused anchor IDs matched before Adaptive Traversal in `60/60` paired calls.
- A fingerprint: `dcb4d9f6fe05dc5d6f6bde1ba28b3ab7d5ba32732adf578642da16263ef5ded1`.
- B fingerprint: `2ebd0602161247177e74b678f675b2686e39f72f7a30386a9bf9d7156fdf7996`.
- All three non-latency fingerprints and metric summaries matched within each variant; latency is the mean across the three timed repetitions.
- The marker-owned temporary MAGMA store was removed after evaluation.

## Interpretation

The ENTITY-biased fallback retained higher overall Evidence Recall and NDCG, and it was clearly stronger on temporal questions, although B had a higher overall complete-hit rate. Balanced weights did not reduce strict-gold noise: the B-minus-A change was `+0.008334`. Multi-hop improved in complete hit from `0.250000` to `0.500000` and in NDCG from `0.705922` to `0.738152`. Temporal clearly regressed: Recall changed from `0.916667` to `0.791667` and NDCG from `0.767828` to `0.693156`. Knowledge-update had a mixed result: complete hit changed from `0.500000` to `0.750000`, while NDCG changed from `0.464450` to `0.453824`. Direct-fact and causal-control answerable quality metrics were unchanged. The balanced candidate is not worth a separate production GENERAL weight change on this evidence. No causal specialization, abstention, or answer-quality conclusion is implied.

## Limitations

- The fixture has only 20 questions and 48 synthetic turns.
- It evaluates evidence retrieval, not an answer model or an LLM judge.
- Current causal edges are not trusted; candidate B therefore assigns CAUSAL a weight of zero.
- Local latency is machine-specific and is not a production SLA.
- The result cannot be directly generalized to real, large memory graphs, LoCoMo, or LongMemEval.

## Recommendation

**A** — The balanced candidate lost overall retrieval quality or category quality, failed to lower strict-gold noise, or worsened a no-answer control; retain the upstream fallback.

Production GENERAL weights remain unchanged. Any later production change requires a separate authorized task.
