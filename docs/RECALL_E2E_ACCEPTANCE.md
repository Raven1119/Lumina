# Recall End-to-End Acceptance

## Purpose

`scripts.recall_e2e_test` is a developer-only acceptance harness for the current
production boundaries:

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

## Commands

Run from the repository root with the isolated Conversation Memory environment:

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
I completed the membrane experiment yesterday.

2026-07-14T10:05:00+08:00 assistant
The experiment was recorded as completed.

2026-07-14T11:00:00+08:00 user
The first experiment failed because the solvent evaporated too quickly.

2026-07-14T11:05:00+08:00 assistant
The rapid solvent evaporation caused the failure.

2026-07-15T09:00:00+08:00 user
I changed the solvent today and repeated the experiment.

2026-07-15T09:05:00+08:00 assistant
The repeated experiment used the new solvent.
```

One additional synthetic user/assistant pair is appended as the retained Hot
tail. The production `JsonlDraftStore` writes all eight turns, then the
production `HotDraftCompactor` runs with a six-turn threshold and a two-turn
tail. This creates one real `pending_digest` segment containing exactly the six
target turns while leaving the physical Hot Draft append-only.

The script verifies chronological role/text order before and after compaction
and verifies the Cold Draft source text remains unchanged after the owner marks
the segment consumed. It never writes a consumed JSONL record directly.

## Current timestamp limitation

The fixed time schedule is part of the test definition, but the current
production `JsonlDraftStore` and Cold Draft schema do not preserve per-turn
timestamps. Cold Draft contains only one aware UTC segment `created_at`.

The existing Dream converter therefore maps that segment timestamp to every
turn and derives stable conversation/turn IDs. Acceptance verifies this actual
mapping rather than claiming the fixed per-turn schedule was persisted. It also
inspects the persisted temporal metadata to prove `yesterday` was normalized
from the segment `created_at` and `UTC`, never from Dream execution time.

The safe report records only the limitation code
`segment_created_at_for_all_turns`; it does not contain source conversation
text or timestamps.

## Dream and persistence checks

Dream runs with:

```text
max_segments=10
stop_on_error=False
ingestion_version=recall-e2e-v1
```

Acceptance requires at least one attempted segment, zero failures, a consumed
result for every target segment, a `completed` Conversation Memory checkpoint,
and an authoritative consumed Cold Draft record. The real MAGMA graph and
vector store must each contain one persisted event/vector per source turn.

## Recall query set

The Lumina-owned `MagmaMemoryAdapter.recall` facade is called with:

```python
RecallPolicy(
    top_k=5,
    max_chars=1200,
    max_evidence_items=5,
    max_graph_depth=6,
    max_nodes=200,
    min_relevance=None,
)
```

The six acceptance categories are:

1. Exact/overlap cause query: first experiment failure.
2. Semantic paraphrase: initial membrane test going wrong.
3. Behavior change: solvent changed before repetition.
4. Temporal query: experiment completion and `yesterday` normalization.
5. Entity/topic query: membrane experiment.
6. Negative query: an absent Sigma-Aldrich catalyst purchase.

Every positive query must contain its expected source evidence. Exact and
paraphrased cause queries must share the same stable evidence ID. The negative
query may be empty or contain unrelated source evidence because the
compatibility run deliberately disables the optional relevance gate; it passes
only if every item comes from the sandbox segment and none fabricates catalyst,
purchase, or Sigma-Aldrich evidence. No answer generation occurs.

Every query is repeated to verify deterministic ordering.

## Optional relevance gate

The E2E first proves backward compatibility with `min_relevance=None`. It then
loads the same 20-positive/20-negative calibration definitions and applies the
documented selection rule to the current sandbox memory.

The current real-MAGMA calibration recommends no threshold because the positive
and negative cosine distributions overlap too heavily. E2E therefore records
`enabled_validation=threshold_not_recommended`; it does not invent a threshold
or claim that the absent negative query is rejected by an enabled gate. If a
future calibration produces a recommendation, E2E is already structured to
require all positive queries, an empty negative result, stable order, complete
provenance, bounds, and identical restart behavior.

## Provenance and leak checks

Every returned evidence item must have:

- a stable SHA-256 evidence ID;
- a sandbox-generated segment ID;
- non-empty conversation and turn IDs;
- an aware source timestamp and source timezone;
- ingestion version `recall-e2e-v1`.

Public contexts and the report are checked for local paths, tracebacks,
credentials, provider data, NetworkX/FAISS/embedding object names, MAGMA UUIDs,
and raw synthetic conversation text. Private node IDs are used only for
internal durable-state comparison and are never serialized into the report or
terminal output.

## Bound checks

The script independently verifies:

- `top_k=1` returns at most one evidence item;
- `top_k=10, max_evidence_items=2` returns at most two items;
- `max_chars=120` never renders more than 120 characters and reports
  `truncated=true`.

The negative query must always return a valid `MemoryContext`, not an exception.

## Restart and idempotency

After the first successful recall, the script discards the active adapter and
backend, constructs a new real backend from the same persisted graph/vector
directory, and reruns all six query categories. Event/vector counts, ordered
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
