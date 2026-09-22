# Recall End-to-End Acceptance

## Purpose

`scripts.recall_e2e_test` is a developer-only acceptance harness for the retained `grounded-span-v2` / BGE profile and the
Cold/Dream persistence boundaries:

```text
isolated production-format Hot Draft
-> real Cold-first compaction
-> pending Cold Draft segment
-> manual DreamRunner
-> real MAGMA graph/vector persistence
-> Lumina-owned MemoryRetriever.recall
-> restart and idempotency checks
```

It verifies evidence and provenance only. It does not ask an LLM to generate an
answer, alter recall ranking, inject recall into `/api/chat`, or schedule Dream.

The default real-model v6/reliable-v2 path is validated separately in
`test_reliable_profile_integration.py`, `test_reliable_v6_ingestion.py`, and
`test_reliable_recall_v2.py`. This legacy harness does not validate that algorithm.
Do not download BGE merely to repeat its recorded results.

## Commands

Run from the repository root with the isolated Conversation Memory environment:

With already prepared legacy dependencies/cache, the Linux command is:

```bash
Conversation_Memory/.venv/bin/python -m scripts.recall_e2e_test --work-dir /tmp/lumina-legacy-e2e
```

Windows equivalent:

```powershell
.\Conversation_Memory\.venv\Scripts\python.exe -m scripts.recall_e2e_test
.\Conversation_Memory\.venv\Scripts\python.exe -m scripts.recall_e2e_test --keep-data
.\Conversation_Memory\.venv\Scripts\python.exe -m scripts.recall_e2e_test --work-dir data\recall_e2e_test --verbose
```

The script rejects execution through another Python environment so a missing
real MAGMA dependency cannot be mistaken for an acceptance pass.

## Isolation and deletion safety

The default sandbox is `data/recall_e2e_test/`. Every Draft, compaction,
ingestion-state, MAGMA, vector, report, and log path is constructed explicitly
under that root. Shell environment variables used by production Dream are not
read.

Before creating or deleting a sandbox, the script:

- rejects a filesystem root, the user home, repository root, and `data/` itself;
- rejects repository code directories;
- rejects `data/draft/`, `data/conversation_memory/`, `.git/`, and the upstream
  MAGMA checkout, including their descendants;
- rejects an existing symlink or non-directory;
- refuses to reset an existing directory unless it contains the exact marker
  `.recall_e2e_sandbox` with the expected versioned content;
- deletes only that validated sandbox, never its parent.

The script never reads or mutates default production Draft or Conversation
Memory data.

## Sandbox layout

```text
data/recall_e2e_test/
├── .recall_e2e_sandbox
├── draft/
│   ├── hot_drafts.jsonl
│   ├── cold_drafts.jsonl
│   └── hot_draft_compaction_state.json
├── conversation_memory/
│   ├── magma/
│   └── ingestion_state.json
├── reports/
│   └── recall_e2e_result.json
└── logs/
```

## Fixed conversation and compaction

The six target turns are deterministic synthetic text:

```text
2026-07-14T10:00:00+08:00 user
I completed the membrane experiment yesterday. 我昨天完成了膜实验。

2026-07-14T10:05:00+08:00 assistant
The experiment was recorded as completed.

2026-07-14T11:00:00+08:00 user
The first experiment failed because the solvent evaporated too quickly.

2026-07-14T11:05:00+08:00 assistant
The rapid solvent evaporation caused the failure.

2026-07-15T09:00:00+08:00 user
I changed the solvent today and repeated the experiment.
我上周一更换了溶剂，下周一准备复查。

2026-07-15T09:05:00+08:00 assistant
The repeated experiment used the new solvent.
```

One additional synthetic user/assistant pair is appended as the retained Hot
tail. The production `JsonlDraftStore` writes all eight turns, then the
production `HotDraftCompactor` runs with a six-turn threshold and a two-turn
tail. This creates one real `pending_digest` segment containing exactly the six
target turns while leaving the physical Hot Draft append-only.

The script verifies chronological role/text order before and after compaction
and writes the fixed schedule as native V2 turn provenance with IANA timezone
`Asia/Shanghai`. It verifies unique stable IDs, expected UTC instants, Hot
restart fidelity, exact Cold turn-object equality, and unchanged source text
after the owner marks the segment consumed. It never writes a consumed JSONL
record directly.

## Grounded span provenance

The fixed times are converted to UTC and persisted by the production Draft
stores before compaction. Dream rebuilds deterministic eligible conversation
spans from the six-turn Cold segment. This fixture produces six grounded events
and preserves the three user and three assistant source roles.

Acceptance verifies every event's stable span ID, exact source substring and
offsets, source role, source turn ID, timestamp, timezone, and timezone source.
Temporal normalization still uses the originating turn rather than segment,
Dream, or server time. Mentions split across grounded events from the same turn
are aggregated for the existing English/Chinese temporal assertions.

The base Recall suite passes `10/10` with restart and idempotency intact. The
earlier role-preserving top-2 candidate-distribution baseline was `10/11`:
Xiaolin requires two USER spans at BGE ranks 1 and 3. A later source-backed
raw-score floor of 0.0 retained only `7/11`, so it was rejected rather than
tuned.

The 2026-08-10 production-parameter sweep used isolated TEMP real MAGMA and
the fixed BGE revision. It collected candidates and raw scores once for all 36
`depth x top_k x max_nodes` retrieval configurations, then evaluated 23,430
distinct observed-score-breakpoint/K selections offline. Depth 0 was a
baseline only; production candidates were depths 1, 2, and 3. Fifteen
retrieval configurations formed a stable hard-gate plateau.

The selected production policy is:

```text
top_k = 10
max_graph_depth = 1
max_nodes = 20
max_evidence_items = 3
max_chars = 5000
final_min_score = 0.144
```

Before applying the composed-score floor, this retrieval configuration records
required evidence `11/11`, meeting-role PASS,
assistant-self-memory PASS, USER stress PASS, and ASSISTANT stress PASS.
Xiaolin's two required USER spans both survive. All six no-answer and all three
near-miss queries remain non-empty, so this is a coverage improvement, not an
admission or abstention result. Required and irrelevant raw logits overlap:
the minimum required score is `-4.517147064208984`, while the maximum
irrelevant score is `1.9342246055603027`. There is no globally safe absolute
raw-logit floor; raw scores remain private. Production uses the separately
calibrated composed-score floor below, not a raw BGE-logit floor.

### Hindsight post-rerank score and final-floor probe

Lumina borrows one concrete scoring operation from the MIT-licensed Hindsight
repository at pinned commit
[`f1c825d88471d069aec0480446d071c589ab10bd`](https://github.com/vectorize-io/hindsight/tree/f1c825d88471d069aec0480446d071c589ab10bd).
The exact upstream implementation is in
[`fusion.py`](https://github.com/vectorize-io/hindsight/blob/f1c825d88471d069aec0480446d071c589ab10bd/hindsight-api-slim/hindsight_api/engine/search/fusion.py)
and
[`reranking.py`](https://github.com/vectorize-io/hindsight/blob/f1c825d88471d069aec0480446d071c589ab10bd/hindsight-api-slim/hindsight_api/engine/search/reranking.py).
The upstream license is
[`MIT`](https://github.com/vectorize-io/hindsight/blob/f1c825d88471d069aec0480446d071c589ab10bd/LICENSE).

The imported weighting behavior is deliberately narrow. Lumina's BGE boundary
always returns raw logits, so normalization explicitly applies one sigmoid per
score, independently of its numeric range or batch. This replaces the previous
upstream-style range heuristic; the historical measurements below retain their
original results and do not establish calibration for the corrected contract.

```text
normCE = sigmoid(raw BGE logit), exactly once for every candidate
recency = linear over 365 days with floor 0.1; missing = 0.5; future = 1.0
temporal = 0.5
proof = 0.5
final = normCE
        * (1 + 0.2 * (recency - 0.5))
        * (1 + 0.2 * (temporal - 0.5))
        * (1 + 0.1 * (proof - 0.5))
```

The fixed upstream commit treats an explicit `question_date` as the recency
reference time and falls back to server `utcnow()` only when it is absent; see
[`_recall_scoring_now`](https://github.com/vectorize-io/hindsight/blob/f1c825d88471d069aec0480446d071c589ab10bd/hindsight-api-slim/hindsight_api/engine/memory_engine.py#L832-L838).
Lumina's Recall facade has no persisted query timestamp. To keep identical
persisted state and query restart-reproducible, it uses the latest aware
`source_timestamp` in the deterministic bounded candidate snapshot as its
logical as-of watermark. A snapshot without any truthful aware source time
fails softly as `recall_unavailable`; no wall-clock or fabricated date is
used.

The production E2E runs every fixed positive, no-answer, and near-miss case
twice under simulated 1990 and 2090 process clocks. All 20 top-3
`MemoryContext` values are identical, and required positive evidence remains
`11/11`. This changes only reference-time semantics; the Hindsight formula,
BGE, RRF, MAGMA traversal, K, and admission behavior are unchanged.

Lumina has no compatible truthful temporal-proximity or proof-count signal, so
both remain neutral. Stable ties retain existing candidate order. The optional
`final_min_score` is inclusive (`final >= floor`) and is applied only after
this composition. Scores remain private.

The fixed 20-case provider-free probe reused candidate generation
`top_k=10`, depth `1`, `max_nodes=20`, K=`3`, one candidate/BGE evaluation per
query, and then evaluated three offline arms:

| Arm | Positive required evidence | Empty no-answer | Empty near-miss | Evidence p50 / max | Rendered chars p50 / max |
| --- | ---: | ---: | ---: | ---: | ---: |
| A: current raw BGE | 11/11 | 0/6 | 0/3 | 3 / 3 | 63 / 76 |
| B: Hindsight, no final floor | 11/11 | 0/6 | 0/3 | 3 / 3 | 63 / 76 |
| C: Hindsight, final floor 0.5 | 7/11 | 6/6 | 3/3 | 0 / 2 | 0 / 33 |

Arm C dropped `tomorrow_correction`, `current_choice`, `historical_choice`, and
`xiaolin_destination`. The minimum required final score was
`0.00023000556505023181`; the maximum irrelevant final score was
`0.9578046397979886`. Therefore the fixed suite has no global final-score
separation. That task originally selected Arm B with
`final_min_score=None`; the later 60-case BGE-only development calibration
supersedes only the production caller value with the rounded inclusive floor
`0.144`. An exact provider-free replay at the rounded value retained required
evidence for `17/30` positives and returned empty evidence for `20/30`
negatives (`37/60` combined), still the best of the three predeclared
candidates. The rejected `0.5` floor remains rejected, and neither development
suite is a blinded holdout or an Answer-quality claim.

Arm A and Arm B selected byte-identical top-3 rendered contexts for all 20
fixed cases and four additional role-critical pair queries. Those exact inputs
are already covered by the frozen seven-call MiniMax reader sanity below, so no
duplicate Answer Model run was made for a scoring change that did not change
model input.

| Depth control | Best `top_k / max_nodes / K` | Positive | Empty no-answer / near-miss | Candidate p50 / p95 | MAGMA p50 / p95 ms | BGE p50 / p95 ms | Total p50 / p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 baseline | `10 / 40 / 3` | 11/11 | 0/6 / 0/3 | 5 / 10 | 8.427 / 10.080 | 136.902 / 311.472 | 143.316 / 319.974 |
| 1 selected | `10 / 20 / 3` | 11/11 | 0/6 / 0/3 | 5 / 13 | 11.109 / 14.185 | 172.427 / 442.180 | 180.133 / 454.314 |
| 2 control | `10 / 40 / 3` | 11/11 | 0/6 / 0/3 | 5 / 13 | 11.554 / 16.497 | 172.314 / 454.032 | 180.747 / 467.895 |
| 3 control | `10 / 60 / 3` | 11/11 | 0/6 / 0/3 | 5 / 13 | 11.697 / 15.531 | 167.997 / 457.431 | 180.294 / 468.931 |

The best context has median/max evidence `3/3`, rendered characters `63/76`,
and approximate tokens `16/19`. One-hop expansion added BGE candidates in
seven fixed cases but added required evidence in zero cases. Depth 0 is faster
and equally complete on this fixture, but the task required an existing graph
depth of at least one unless it caused a hard regression. Depth 1 passes every
hard gate and is the smallest eligible graph configuration.

The leading same-quality landscape was not a single threshold cliff: depth-0
controls included `(10,40,3)`, `(8,60,3)`, and `(8,20,3)`; graph-enabled
controls included depth-1 `(10,20,3)`, depth-3 `(10,60,3)`, depth-2
`(10,40,3)`, depth-2 `(10,20,3)`, and depth-1 `(10,60,3)`. Every listed tuple
uses no floor and is `(top_k,max_nodes,K)`. Parameter stability is `STABLE`.

A single frozen seven-call reader sanity used the current production evidence
template and MiniMax-M2.7 with no retry or judge. The predeclared surface check
scored `5/7`; its two meeting misses were then locally adjudicated as false
negatives because the current-reality answer had the correct negative polarity
and the self-memory answer preserved Lumina's earlier positive statement.
Semantic reader sanity is `7/7`, with no severe role regression. No answer or
evidence text is retained in this document.

The rejected raw `reranker_min_score` experiment has been removed. The final
composed `final_min_score` remains caller-opted and defaults to None in the
reusable DTO; production Chat explicitly passes `final_min_score=0.144`. Successful
post-BGE empty selection remains a normal empty MemoryContext, not
recall_unavailable. The safe report retains the historical
`native_per_turn_v2` field name to preserve its report schema and contains no
source conversation text, private MAGMA IDs, or timestamps.
## Dream and persistence checks

Dream runs with:

```text
max_segments=10
stop_on_error=False
ingestion_version=grounded-span-v2
```

Acceptance requires at least one attempted segment, zero failures, a consumed
result for every target segment, a `completed` Conversation Memory checkpoint,
and an authoritative consumed Cold Draft record. The real MAGMA graph and
vector store must each contain one persisted event/vector per grounded span.

## Recall query set

The Lumina-owned `MagmaMemoryAdapter.recall` facade is called with:

```python
RecallPolicy(
    top_k=5,
    max_chars=1200,
    max_evidence_items=5,
    max_graph_depth=6,
    max_nodes=200,
)
```

`top_k` limits the number of vector-search anchor nodes. The separate
`max_evidence_items` limit bounds the final public evidence total, including
both anchors and eligible graph-traversal expansions.

The nine acceptance categories are:

1. Exact/overlap cause query: first experiment failure.
2. Semantic paraphrase: initial membrane test going wrong.
3. Behavior change: solvent changed before repetition.
4. Temporal query: experiment completion and `yesterday` normalization.
5. Entity/topic query: membrane experiment.
6. Chinese temporal query: `我什么时候完成了膜实验？`.
7. Chinese previous-week query: `上周一发生了什么？`.
8. Chinese next-week query: `我准备什么时候复查？`.
9. Negative query: an absent Sigma-Aldrich catalyst purchase.

Every positive query must contain its expected source evidence. Exact and
paraphrased cause queries must share the same stable evidence ID. The negative
query may be empty or contain unrelated source evidence; it passes only if
every item comes from the sandbox segment and none fabricates catalyst,
purchase, or Sigma-Aldrich evidence. No answer generation or relevance gate is
involved.

Every query is repeated to verify deterministic ordering.

## Chinese temporal checks

Chinese temporal evidence is part of this same authoritative sandbox rather
than a second E2E. The fixed turns include `昨天`, `上周一`, and `下周一`; the
three Chinese queries are:

```text
我什么时候完成了膜实验？
上周一发生了什么？
我准备什么时候复查？
```

The checks verify local Shanghai calendar intervals, `language=zh`, unchanged
event timestamps and raw Cold turns, durable completion before consumed state,
no-op duplicate Dream, stable evidence after restart, and the shared Recall
bounds. Query-side temporal constraints are not implemented; Recall remains
outside `/api/chat`.

## Provenance and leak checks

Every returned evidence item must have:

- a stable SHA-256 evidence ID;
- a sandbox-generated segment ID;
- non-empty conversation and turn IDs;
- an aware source timestamp, source timezone, and `timezone_source=client`;
- source role `user` or `assistant`;
- ingestion version `grounded-span-v2`.

Public contexts and the report are checked for local paths, tracebacks,
credentials, provider data, NetworkX/FAISS/embedding object names, MAGMA UUIDs,
and raw synthetic conversation text. Private node IDs are used only for
internal durable-state comparison and are never serialized into the report or
terminal output.

## Bound checks

The script independently verifies:

- `top_k=1` produces at most one vector anchor in the real MAGMA
  `QueryContext`; the public context may also contain eligible graph expansions;
- `top_k=10, max_evidence_items=2` returns at most two public evidence items in
  total, including anchors and graph expansions;
- `max_chars=120` never renders more than 120 characters and reports
  `truncated=true`.

The negative query must always return a valid `MemoryContext`, not an exception.

## Restart and idempotency

After the first successful recall, the script discards the active adapter and
backend, constructs a new real backend from the same persisted graph/vector
directory, and reruns all nine query categories. Event/vector counts, ordered
evidence IDs, and complete provenance signatures must be unchanged.

Dream is also run a second time after the source segment is consumed. It must
attempt zero segments. Graph node count, vector count, ingestion-state bytes,
and recall evidence IDs must remain unchanged. MAGMA UUID ordering is never an
acceptance criterion.

## Report and terminal output

With `--keep-data`, the structured report remains at
`reports/recall_e2e_result.json` inside the sandbox. It contains only counts,
boolean checks, stable limitation/error codes, and the overall result. It does
not include an absolute sandbox path, evidence text, source text, timestamps,
node IDs, graph/vector contents, provider data, or exceptions.

Default terminal output is a short PASS/FAIL summary. `--verbose` adds only
safe stage names and does not enable third-party MAGMA/model output.

## Cleanup

The default run writes the temporary report, prints the result, and deletes the
marker-owned sandbox whether the pipeline passes or fails. `--keep-data`
retains Draft, MAGMA, state, and report files for explicit developer inspection.
The next run may reset that directory only because its marker proves script
ownership. Global Hugging Face model caches are never removed.

## Automated validation

```powershell
.\Conversation_Memory\.venv\Scripts\python.exe -m pytest tests -q
.\Conversation_Memory\.venv\Scripts\python.exe -m pytest Dream/tests -q
.\Conversation_Memory\.venv\Scripts\python.exe -m pytest Conversation_Memory/tests -q
python -m pytest -q
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

Ordinary Python skips the real-MAGMA assertions but still runs sandbox safety,
marker ownership, and static chat-isolation tests. The isolated environment
must execute and pass the real end-to-end assertions.
