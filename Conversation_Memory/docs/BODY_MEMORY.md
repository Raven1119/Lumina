# Explicit body-backed memory candidate

`grounded-formation-v7` + `body-recall-v1` is an implemented, unpromoted candidate.
Production stays `grounded-formation-v6` + `reliable-v2`. It changes the saved and
returned unit of context, not the original FirstHit equation or graph protocol.

## Write ownership and recovery

`_body_formation.py` supplies a separate F1 protocol: directly rewrite the bounded
Cold dialogue into topic-local groups of independently understandable units with
origin and declared source IDs. F2 sees the exact machine-attributed canonical
text and verifies every unit against its declared sources. Grouping grants no
extra evidence; rejected/insufficient units leave explicit gaps. There is no
fifth generation and no generated header or joining sentence. These are verified
paraphrases, never verbatim quotes or independent proof of external execution.

`_entity_ingestion.py` freezes the F1/F2 receipts and body manifest in the existing
ingestion checkpoint. The existing backend then atomically writes immutable
`bodies/v1/<digest-prefix>/<digest>.json`, followed by each existing EVENT and its
vector, then G1/G2 structure. Each EVENT stores only its own canonical unit plus
`body_ref`, `body_unit_ref`, `body_digest`. The payload keeps ordered units,
per-unit provenance/source hashes, receipt references and completeness. IDs bind
version, source identity and content; equal text does not merge identities.

Ingestion can repair missing/corrupt payloads from frozen receipts and restore
missing vectors from stored embeddings. A G failure leaves verified bodies
readable while Cold remains pending. Unknown provider delivery is not retried.
Old checkpoints retain their original meaning; no automatic backfill or migration.
The checkpoint owner still rewrites its existing whole JSON file: this upgrade
does not solve that storage scaling cost.

## Read ownership and budgets

`_body_recall.py` uses production whole-query seed search and original FirstHit.
It groups every legal positively activated Fact before final attention/top-k or
relation filtering. Missing roles/SRV do not bar historical context; they also do
not establish a precise relationship answer. Each body gets max(trigger h), not
a sum. Direct seeds retain the 0.6 protection starting share and budget borrowing.

`BodyRecallPolicy` bounds payload reads to 16 blocks / 262144 bytes, each payload
at most 65536 bytes; no persistent payload cache. Final output has at most 3 body
blocks, `RecallPolicy.max_evidence_items` actual units, `max_chars` characters and
min(`max_bytes` if set, 20000) UTF-8 bytes. Labels and provenance count. Whole
bodies are preferred; otherwise complete units show an explicit partial label.
A bare complete Fact remains usable when payload/header budgets do not fit.
Duplicates are removed by stable IDs, not text or names.

Bad/missing bindings fall back to legal complete Facts. Reads load generated IDs
only, never scan payload directories or ingestion checkpoints, repair storage,
call a generator/BGE, or expand Cold. Body expansion is distinct from graph gain.
`BodyMemoryEvidence` exposes unit/body/source references without changing legacy
`MemoryEvidence`. The ordinary `recall()` facade returns the exact rendered text.

## Explicit invocation

Use a separate persist/state/Cold root when comparing candidates:

```python
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.ingestion.state_store import IngestionStateStore

memory = MagmaMemoryAdapter.create_real(
    persist_dir, IngestionStateStore(state_path),
    ingestion_version="grounded-formation-v7", formation_model=formation_model,
    first_hit=FirstHitPolicy(), associative_read_profile="body-recall-v1",
    cold_store=cold_owner,
)
# Writing is explicit through the existing manual Dream owner; reads generate nothing.
context = memory.recall("What was authorized?", RecallPolicy(
    max_evidence_items=3, max_chars=5000, max_bytes=20000,
    include_source_context=True,
))
print(context.rendered_text)
```

Chat and its existing manual Dream share this adapter when explicitly configured
with `LUMINA_MEMORY_PROFILE=body-recall-v1`. It needs the configured Formation
client. Keep the ordinary boolean Mind gate; simultaneous `graph-read-v2` or
`select` gates are rejected. The original user question reaches Chat unchanged.
Manual CLI also accepts `--ingestion-version grounded-formation-v7` or the same
environment profile; it remains synchronous and serial. Keep service stopped
when invoking the external Dream writer. Explicit direct mode has its separately
larger existing policy and is not the equal-capacity evaluation configuration.

For ablation only, `BodyRecallPolicy(propagate=False/True, return_bodies=False/True)`
selects seeds/Facts, propagation/Facts, seeds/bodies, propagation/bodies. All share
the same whole-query seeds and visible unit/character/byte limits. Seed-only sets
read arcs to zero; production defaults and the numerical core remain unchanged.
Compare final text, separate sibling context from different-body graph discovery,
and never interpret activation or successful provenance checks as semantic gain.

## Validation and evidence boundary

```bash
python -m pytest Conversation_Memory/tests/test_body_memory.py Conversation_Memory/tests/test_body_memory_persistence.py tests/test_body_memory_chat.py -q
```

Use the prepared Linux Memory environment and existing MiniLM cache. The real
persistence suite injects payload/EVENT/vector/structure failures, reconstructs
MAGMA, checks stable IDs and Cold consumption, repairs payload loss without new
Formation, and reads after the bounded Cold window advances. Deterministic
Formation tests establish mechanisms only. Actual rewrite fidelity and four-arm
natural-query effects require separately frozen local evidence. Experiment
conversations, receipts, gold, payloads and scores are not repository fixtures.
F2 approval remains a model judgment: it can miss an undeclared antecedent or
leave a unit dependent on surrounding dialogue. Mechanical reference checks
cannot certify natural-language entailment or self-containment. Source-package
violations and missing qualifiers block promotion even when recall coverage
improves; preserve them as failures, without regenerating to select a winner.
Legacy BGE acceptance must not be substituted for this candidate or downloaded
merely to repeat historical measurements. No promotion is implied by this contract.
