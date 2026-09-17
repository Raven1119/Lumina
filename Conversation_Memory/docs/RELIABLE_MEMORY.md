# Explicit reliable Memory profile

The default Formation version remains `grounded-formation-v2`. The explicit
reliable pair is the `grounded-formation-v5` writer with the `reliable-v2`
associative presentation; the historical `grounded-formation-v4` writer and
`reliable-v1` presentation remain explicitly selectable. Both reuse EVENT,
ENTITY, the Memory backend, IngestionStateStore, Cold and FirstHit.
They do not migrate or reinterpret historical checkpoints.

## Entry points

Run manual Dream with the service's writer stopped when using the separate CLI:

```powershell
Conversation_Memory/.venv/Scripts/python.exe -m Dream --reliable-memory --max-segments 1
```

`--reliable-memory` selects the v5 writer. `--ingestion-version
grounded-formation-v4` remains an explicit historical selection.

The existing `LUMINA_DREAM_COLD_DRAFT_PATH`, `LUMINA_DREAM_INGESTION_STATE_PATH`
and `LUMINA_DREAM_MAGMA_PERSIST_DIR` select the same owners as ordinary Dream.
Use isolated paths for evaluation. This flag does not schedule Dream or switch
Chat to a new reader. A caller can explicitly construct the corresponding reader:

```python
from adapter.first_hit import FirstHitPolicy
from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import RecallPolicy
from ingestion.state_store import IngestionStateStore
from core.cold_draft_store import ColdDraftStore

cold = ColdDraftStore(cold_path, source_window_segments=32,
                      source_window_bytes=1048576)
memory = MagmaMemoryAdapter.create_real(
    magma_path, IngestionStateStore(state_path), fail_if_unavailable=True,
    ingestion_version="grounded-formation-v5", formation_model=model,
    first_hit=FirstHitPolicy(), cold_store=cold,
    associative_read_profile="reliable-v2",
)
# Ingestion is normally invoked by the existing manual Dream owner.
result = memory.recall_associative(
    question, RecallPolicy(max_chars=3000, max_bytes=12000,
                           max_evidence_items=5, include_source_context=True),
    include_sources=True, source_context_turns=1,
)
```

Use the Memory environment and add `Conversation_Memory` to the import path,
as in the maintained scripts. `formation_model` is unnecessary for read-only
construction. The caller chooses budgets; the profile does not raise them.

## Four bounded batch stages

1. F1 extracts self-contained statements with actual source IDs and an explicit
   statement anchor. It does not supply entities or relations. The owner builds
   canonical `User stated:` or `Lumina stated:` text before verification.
2. F2 verifies that exact text and attribution using only each candidate's
   declared source package. The package includes the actual anchor, selected
   supports and at most one preceding turn added before verification.
3. G1 proposes optional structure for accepted text and independently discovers
   exact source mentions. Historical identity candidates carry bounded actual
   Cold source, not name-only equality permission.
4. G2 verifies identities, the entire relation pairing, its subject/object
   roles, ordinary participation and time separately.

Only F2-supported text enters EVENT. Rejected structure does not remove an
independently supported body. Missing or malformed decisions remain incomplete,
not an empty successful vote. A rejected identity cannot return through name
fallback, heuristic entity extraction, vector enrichment or metadata repair.
Identity rejection flows from a failed claim to its dependents; it does not
invalidate an independently grounded target of a negative identity assertion.
A G1 proposal that omits the identity source evidence for a mechanically
bindable kind (`current_user` or `new`) while satisfying every other syntax
rule defers to the recorded G2 identity vote; the deferral is logged as a
repaired identity issue. This deferral is v4-only: v5 declares the omission
contract-valid (see below). Any other syntax defect — an unknown identity
literal, an `existing_entity_ref` outside `named`, a dangling
`same_as`/`distinct_from` reference, or missing evidence on a `named` claim —
voids the mention regardless of the G2 vote.

The canonical body is saved unchanged. `origin_turn_id` determines provenance,
speaker and statement time. `source_refs` preserve the complete package seen by
F2; `used_source_ids` preserves its narrower cited judgment. Approved interpreted
time uses its separately authorized source turn. A speaker's report remains a
report; an assistant statement establishes neither user acceptance nor external
execution. Model judgments can still be semantically wrong.

## Persistence and recovery

Each stage reserves its exact request before sending, then saves raw response,
response digest, parsed result and summary before advancing. EVENT metadata
references request, response and parsed digests in the existing versioned
ingestion checkpoint. A deterministic empty stage is explicitly identified and
does not imply a provider request.

Restart replays successful responses without generation. A reserved stage with
no durable response returns `reliable_stage_delivery_unknown`; it cannot safely
resend automatically. Invalid/mismatched receipts fail closed. Explicit F2
rejection and G2 rejection are terminal decisions; insufficient F2 evidence,
invalid F1 entries or unfinished stages leave the window pending. There is no
automatic repair or resampling loop for v4.

Bindings and the unchanged FirstHit link plan are checkpointed before graph
mutation. v4 uses a separate `first-hit-v1:grounded-formation-v4` checkpoint key;
v2 keeps its historical key. Existing EVENT IDs and embeddings repair a missing
vector on restart, including a completed v4 checkpoint. Cold is consumed only
after all required work, graph/vector persistence and local links complete.

## Formation v5

`grounded-formation-v5` (progress schema `reliable-formation-progress-v2`,
stage binding schema unchanged, FirstHit key `first-hit-v1:grounded-formation-v5`)
keeps the four bounded stages and every v4 authorization rule, and changes the
following, v5-only:

- Speaker-attribution consistency. The F1 prompt states that the program owns
  the speaker prefix and the body must not re-attribute the statement to the
  other dialogue party. After canonical construction, a deterministic manifest
  screen isolates a body whose leading attribution names the opposite party
  ("The user"/"用户" under a `Lumina stated:` prefix, "Lumina"/"The
  assistant"/"助手" under a `User stated:` prefix) as a rejected
  `reliable_fact_attribution_conflict` issue; the window continues.
- Unified identity evidence contract. `same_as` and `named` claims require a
  non-empty, all-valid `identity_source_ids`; `new` and `current_user` claims
  may omit it (G2 remains the semantic authorizer — this declares, rather than
  repairs, the v4 deferral, which no longer triggers for v5). Any listed ID
  that names no current window turn is invalid for every kind, regardless of
  the G2 vote.
- Self-reference backstop. A closed-class first-person surface (I/me/my/we/us/
  我/我们/...) on a non-USER turn claimed as `current_user` is syntax-invalid
  (`reliable_identity_self_reference_role_conflict`); G2 cannot override it.
- Relation/role manifest consistency. A G2 `relation_supported=false` voids
  that fact's subject/object role votes.
- Body/structure decoupling at persistence. After a complete valid F2
  manifest, accepted bodies persist as projection-free EVENTs (full source
  package, F1/F2 receipts, used sources) and the checkpoint records
  `state["bodies"]` with status `bodies_persisted`. A later G-stage failure
  (delivery unknown, unparseable output, decision-manifest mismatch) no longer
  strands them: the result is failed with the stage's code, Cold stays
  pending, and the bodies remain normal readable EVENTs. On structure success
  the owner binds mentions, attaches the authorized projection attributes to
  the existing EVENTs (`update_event_projection`; deterministic, idempotent,
  conflict-failing), grows each EVENT's `formation_receipts` from the body
  phase's F1/F2 entries to the full four-stage set (existing entries must be
  preserved exactly), creates role/mention links and completes. Restart finds
  existing bodies by evidence id, replays saved stage responses without new
  provider calls, and adds only the missing structure.
- FirstHit timing. Planning happens once, at structure completion, with
  complete bindings; bodies without structure are direct seeds without
  first-hit links. The algorithm, budgets and old frozen plans are untouched.

The v5 reader pair is the `reliable-v2` presentation (canonical body always
visible, bounded source supplement), described below.

## Bounded associative presentation

The reader performs the existing seed search and FirstHit activation once.
Legal Fact seeds form the direct pool in seed order. Legal visited nonseed Facts
compete within the associated pool using the unchanged sparse projection.
The fixed starting share is 0.6 for direct item/character/optional byte budgets,
rounded upward for small item limits. Unused quota may be borrowed; associated
results cannot displace the protected direct subset. This selection phase is
identical for both profiles and does not promise to preserve every old result
or make every seed relevant.

In `reliable-v1`, complete Facts are the base representation. An optional single
bounded Cold read can replace a Fact view with its complete supporting source
package, charged against the same `max_evidence_items` cap as Facts. Shared
exact source ranges appear once; each actual turn/range counts as an item.
Unread gaps are never filled. An oversized/incomplete source upgrade falls back
to the complete selected Fact. An oversized Fact is omitted whole.

In `reliable-v2`, a selected Fact always renders its canonical body with its
speaker label; source views never replace it. Sources are a bounded supplement
with parity to the `first-hit-v1` appended source read: up to
`policy.max_evidence_items` distinct source items charged to a separate source
allowance, not a budget expansion. Characters and bytes remain shared with the
always-visible bodies — a group's supplement fits only when the combined render
stays within `max_chars`/`max_bytes`. Shared exact ranges appear once and count
once; each actual turn/range counts as one source item ("multi-turn is multiple
items" accounting unchanged). A shared package is still accepted or rejected
jointly per group, considered in selected order against the source allowance
and the remaining character/byte budget; when the joint supplement does not
fit, every Fact in the group keeps its complete body only — never a handle-only
view. An incomplete supplement marks the sources side truncated
(`cold_source_partial`) without removing a body.

The Cold transport allowance includes bounded legacy provenance-header space;
actual transport size and final visible size are distinct diagnostics.

`AssociativeMemoryContext.selections` records direct/associated origin and
`fact`/`sources` (reliable-v1) or `fact`/`fact+sources` (reliable-v2)
representation. Under reliable-v1, `facts.evidence` retains selected Fact DTOs
for traceability even when their text is replaced by sources; under reliable-v2
`facts.rendered_text` contains every selected body. Only `rendered_text`
is the actual combined presentation; do not count hidden DTO text as visible.
`facts.rendered_text` and `sources.rendered_text` describe the visible portions.
Source handles, provenance and receipt IDs remain traceable in the DTOs.
reliable-v2 diagnostics add `source_reserved_items` (the separate source item
allowance) and `visible_source_items`; `visible_items` counts bodies plus
visible source ranges and may exceed the Fact item cap.

No online generation or BGE scoring is added. FirstHit reachability is not
semantic relevance or source confidence. Unknown queries can return unrelated
history. These contracts establish bounded permission and recovery behavior;
semantic usefulness requires separate source-level evaluation.

## Known limitations

Resolved in v5 (they remain v4 limitations; frozen v4 parses are never
reinterpreted):

- `current_user` identity has no deterministic source-role backstop in v4:
  binding follows the G1 proposal plus G2 approval. A first-person mention on
  an assistant turn relies on G2 rejection and the strict decision-manifest
  check, not on a code rule. v5 adds the deterministic first-person/role
  backstop described above.
- The v4 G1 prompt asks for identity evidence only on `same_as` and `named`
  claims while the G1 parser voids every identity kind without it; the
  authorization layer defers a sole evidence omission on `current_user`/`new`
  to the recorded G2 vote (logged as a repaired identity issue). v5 declares
  the unified contract instead, so the deferral path is v4-only. Re-ingesting
  a completed v4 window whose deferred bindings change fails closed
  (`state_corrupt`) instead of silently rewriting it; repair requires an
  explicit window reset, which replays the saved stage responses without new
  provider calls.
- A non-verdict G-stage failure (delivery unknown, unparseable output, or a
  decision manifest that does not exactly match the proposal) strands a v4
  window's accepted body text: v4 has no repair or resampling path, so the
  window stays pending and Cold is never consumed. v5 persists accepted bodies
  before the G stages (`bodies_persisted`). One `insufficient` F2 verdict or
  one invalid F1 entry likewise leaves the segment permanently partial in
  every reliable version. These are deliberate terminal states, not silent
  success.
- In `reliable-v1`, a caller-requested source package replaces a Fact view:
  the model sees a short handle plus the source ranges, not the full canonical
  text, and the Fact DTO remains only for traceability. The `reliable-v2`
  presentation resolves this: the canonical body is always visible and sources
  are a bounded supplement with their own item allowance. `reliable-v1`
  remains explicitly selectable and keeps its historical replacement behavior.

Still open:

- A seed Fact pruned during FirstHit discovery is not part of the protected
  direct pool; only legal discovered seeds are.
- A shared-package source upgrade is all-or-nothing per shared group; when the
  joint upgrade does not fit, every Fact in the group keeps its complete Fact
  view even if a subset upgrade would have fit.
- The aggregate safe error code reports `cold_source_unavailable` when Cold is
  reachable but every selected citation is invalid; per-item outcomes keep the
  precise `source_refs_invalid` reason.
- `whole_turns` source expansion is available only together with a positive
  `source_context_turns`; the default remains the write-verified ranges.
