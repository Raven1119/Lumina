# Reliable Memory profile

The `grounded-formation-v6` writer with the `reliable-v2` associative
presentation is the production Memory pair: a real-model app wires Chat Recall
and app Dream to one shared v6/FirstHit/reliable-v2 adapter, and the Dream CLI
defaults to it for a configured real model. The historical
`grounded-formation-v2`, `grounded-formation-v5` and `grounded-formation-v4`
writers and the `first-hit-v1`/`reliable-v1` presentations remain
explicitly selectable. Both reuse EVENT,
ENTITY, the Memory backend, IngestionStateStore, Cold and FirstHit.
They do not migrate or reinterpret historical checkpoints.

## Entry points

Production Chat and app Dream need no flag: `core/main.py` constructs the
shared adapter with `first_hit=FirstHitPolicy()`, the app-owned Cold store and
`associative_read_profile="reliable-v2"` whenever a real formation model is
configured, and the adapter serves the ordinary `recall()` boundary through
this reliable read. Mock/legacy construction keeps `grounded-span-v2`.

Run manual Dream with the service's writer stopped when using the separate CLI:

```bash
Conversation_Memory/.venv/bin/python -m Dream.runner --max-segments 1
```

Without an explicit `--ingestion-version`, a configured real model defaults to
the v6 writer with FirstHit and reliable-v2 reading; `--reliable-memory` is a
deprecated alias for that default. `--ingestion-version
grounded-formation-v5`, `grounded-formation-v4` or `grounded-formation-v2`
remains an explicit historical selection (FirstHit only with `--first-hit`).

The existing `LUMINA_DREAM_COLD_DRAFT_PATH`, `LUMINA_DREAM_INGESTION_STATE_PATH`
and `LUMINA_DREAM_MAGMA_PERSIST_DIR` select the same owners as ordinary Dream.
Use isolated paths for evaluation. The CLI does not schedule Dream. A caller
can also explicitly construct the corresponding reader:

```python
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.ingestion.state_store import IngestionStateStore
from core.cold_draft_store import ColdDraftStore

cold = ColdDraftStore(cold_path, source_window_segments=32,
                      source_window_bytes=1048576)
memory = MagmaMemoryAdapter.create_real(
    magma_path, IngestionStateStore(state_path), fail_if_unavailable=True,
    ingestion_version="grounded-formation-v6", formation_model=model,
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

Run from the repository root with the Memory environment. Imports use the
`Conversation_Memory` package; no organ path injection is required. `formation_model` is unnecessary for read-only
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
vector on restart, including a completed v4 checkpoint and every v5/v6 body
path (body loop, `bodies_persisted` boundary and bodies recovery branch).
Cold is consumed only
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
  provider calls, and adds only the missing structure. The full body durability
  invariant is enforced at three points: an evidence-id hit in the body loop,
  the `bodies_persisted` boundary, and the bodies recovery branch each verify
  through `ensure_event_persisted` that the EVENT's vector exists and belongs
  to it (bidirectional `id_to_index`/`index_to_id` consistency, confirmed
  vector write); a missing vector is rebuilt from the persisted embedding and
  persisted before advancing, and a failed repair keeps the window
  failed/retryable without writing `bodies_persisted` or `completed`. EVENT
  ids, evidence ids and `memory_ids` stay stable and no duplicate EVENT or
  vector is created.
- FirstHit timing. Planning happens once, at structure completion, with
  complete bindings; bodies without structure are direct seeds without
  first-hit links. The algorithm, budgets and old frozen plans are untouched.

The v5 reader pair is the `reliable-v2` presentation (canonical body always
visible, bounded source supplement), described below.

## Formation v6

`grounded-formation-v6` (progress schema `reliable-formation-progress-v3`,
stage binding schema unchanged, FirstHit key `first-hit-v1:grounded-formation-v6`,
unit id prefix `grounded_memory_v6:`, mention id prefix `mention_v6:`) keeps
every v5 authorization, recovery and persistence rule — the unified identity
evidence contract, the self-reference backstop, relation/role manifest
consistency and body/structure decoupling — and changes the following,
v6-only:

- No keyword attribution screen. The v5 F1 prompt ordered the body to never
  name the other dialogue party and a deterministic manifest screen rejected
  any body whose leading words matched the opposite party's keywords
  (`reliable_fact_attribution_conflict`). That abstraction confused the source
  speaker with the person talked about inside the proposition: a user turn
  relaying Lumina's suggestion or an assistant turn reporting the user's words
  was falsely rejected. v6 removes the order and the screen entirely; the
  issue code never occurs under v6. Attribution is established only by the
  canonical prefix, which the program still derives from the actual origin
  turn role (`User stated:` / `Lumina stated:`), and by F2, which verifies
  that the actual origin speaker really expressed the complete proposition.
  A cross-reference inside the body describes the statement's content, not a
  different speaker; an assistant claim about the user still persists only as
  `Lumina stated:` and never upgrades to user confirmation or external fact.

The G1/G2 stage prompts and parsers are shared with v5; versioned checkpoints
bind each replay to its own request digests, so a v6 run never reinterprets a
frozen v5 or v4 checkpoint. The reader pair remains the `reliable-v2`
presentation; its EVENT metadata shape is unchanged.

`grounded-formation-v5` remains an explicit historical selection for replay of
its own checkpoints; it is byte-compatible and unchanged.

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

Resolved in the shared v5/v6 writer (existing checkpoints are never
reinterpreted):

- A body-phase restart between graph and vector persistence left the EVENT
  findable by evidence id while its vector was missing, and the body loop
  skipped the existing EVENT without a vector check: the window could advance
  to `bodies_persisted`/`completed` with a permanently unreadable EVENT. The
  body loop, the `bodies_persisted` boundary and the bodies recovery branch
  now re-verify every body EVENT and repair a missing vector from the
  persisted embedding before advancing; a failed repair keeps the window
  failed/retryable and never writes `bodies_persisted` or `completed`.

Resolved in v6 (it remains v5/v4 behavior; frozen v5/v4 parses are never
reinterpreted):

- The v5 keyword attribution screen conflated the source speaker with the
  person mentioned inside the proposition: legitimate bodies such as a user
  relaying Lumina's suggestion ("Lumina 建议我先做小样") or an assistant
  reporting the user's words ("用户刚才说他明天回来") were rejected as
  `reliable_fact_attribution_conflict`. v6 removes the screen; attribution
  rests on the true-role canonical prefix plus F2 verification.

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

## Calibrated FirstHit v1 explicit reader

`calibrated-first-hit-v1` is an **opt-in read candidate**, not the production
reader. A real-model Chat service selects it with
`LUMINA_MEMORY_PROFILE=calibrated-first-hit-v1` while retaining the ordinary
`LUMINA_MIND_GATE_MODE=llm` gate and the v6 writer. A direct Memory caller can
use `MagmaMemoryAdapter.create_real(..., ingestion_version="grounded-formation-v6",
first_hit=FirstHitPolicy(), associative_read_profile="calibrated-first-hit-v1")`
and then `recall(question, RecallPolicy(max_evidence_items=3, max_chars=5000,
max_bytes=20000))`. Both paths leave the stored EVENT vectors, graph links,
checkpoint keys and writer-side FirstHit connection planner unchanged.

The owner builds a separate read-only FAISS cosine index on load and rebuilds
it after adapter-owned ingestion. Only the exact generated outer `User stated: `
or `Lumina stated: ` prefix is removed from its retrieval view. The canonical
fact, negation, attribution and provenance remain unchanged in output. The
multilingual encoder is pinned to
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` revision
`e8f8c211226b894fcb81acc59f3b34ba3efd5f42`; the parameter JSON also
pins its actual weight digest. It is loaded offline from the local Hugging Face
cache, or from the exact revision directory named by
`LUMINA_CALIBRATED_MODEL_SNAPSHOT`. The 384-dimensional vectors are explicitly
unit-normalized before `IndexFlatIP`; the old English MiniLM vector index is
never searched using the new query encoding. Source version and complete
fact-ID coverage are checked; an unavailable or stale derived index yields an
empty result with a safe error code and is not rebuilt by Recall.

RRF combines at most 20 dense, indexed lexical and entity candidates **for
discovery only**. The caller can inspect each hit's raw cosine/metric,
lexical/name support and channels in private diagnostics. At most five distinct
seeds use `r=clip(cosine,0,1)` and `mu=max(r)`; their FirstHit input is
`b_i=mu*r_i/sum(r)`. The existing 5/64/256 FirstHit solver and full-row
transition denominator are unchanged. A single scorer uses absolute cosine,
`max(h-b,0)`, `mu` and lexical support for both direct and nonseed Facts. Its
five coefficients and strict threshold are in
[`calibrated_first_hit_parameters.json`](../adapter/calibrated_first_hit_parameters.json),
with story-disjoint training/validation IDs and the SHA-256 of the committed
synthetic corpus. Scores express a frozen local selection rule, not a
probability that a historical claim is true. No 0.6 direct reservation or
mandatory fill applies; 0–3 canonical Facts compete under 5000 characters
and 20000 UTF-8 bytes. The existing bounded Cold source supplement remains
optional and cannot replace a selected Fact. An ordinary empty selection has
no safe error; index, model or solver failures have one.

The synthetic fit and held-out evaluation failed the predeclared useful-memory
retention targets; the candidate is **not promoted**. Its single fit used the
committed, invented [fixture](../tests/fixtures/calibrated_associative_synthetic.json),
not AC01 or private conversation. The frozen real-graph comparison and
final rendered-text review must be read before making any semantic-benefit
claim. Mechanism tests alone establish only the amplitude, isolation, abstention
and provenance contracts.

## Semantic associative v1 explicit reader

`semantic-associative-v1` is an opt-in **read-side** candidate. Set only
`LUMINA_MEMORY_PROFILE=semantic-associative-v1` with the usual real-model
Chat configuration and isolated Hot/Cold/MAGMA/audit paths. It requires the
default `LUMINA_MIND_GATE_MODE=llm` setting but **replaces** that mode's
boolean pre-read gate with one post-read Mind choice. Combining it with
`direct`, `select`, `graph-read-v2`, an injected gate or the body profile is
rejected. Production remains v6 writer + `reliable-v2`; no writer, checkpoint,
stored EVENT vector or upstream MAGMA migration occurs.

The owner builds the same pinned, fingerprint-checked multilingual derived
index as `calibrated-first-hit-v1`, then uses the same cosine seed strengths,
`sum(b)=max(r)` and 5-seed/64-node/256-read-arc FirstHit solve. The old fitted
logistic threshold and 0.6 final direct reservation are not used for this
profile. Up to 20 complete canonical Facts, 5000 characters and 20000 UTF-8
bytes form one immutable `PreparedRecall` panel. Real seeds are offered first;
remaining indexed hits and graph visits alternate in a fixed order. A Fact
that misses the panel budget is recorded as an omission, not truncated or
silently made available to Mind. Index construction, query encodes, cache hits,
postings and graph traversal have distinct diagnostics. A stale index fails
the read with a safe code; it is not rebuilt on a query.

`MagmaMemoryAdapter.prepare_recall(question, policy)` is the direct facade
entry. The candidate app uses `RecallPolicy(max_evidence_items=20,
max_chars=5000, max_bytes=20000, include_source_context=True)` for the panel;
the owner enforces a separate maximum of 3 whole Facts, 5000 characters and
20000 bytes on `semantic_subset`. `recall()` on this profile returns
`semantic_selection_required` instead of bypassing the choice through legacy
BGE. The panel never pulls Cold text or a body payload. Source roles,
timestamps and canonical attributed Fact text stay intact.

`LlmSemanticEvidenceSelector` makes one temperature-zero, at-most-384-output-
token Mind call on the original message, current recent conversation and
bounded panel. Its versioned JSON output names only existing integer IDs and
`history` or `analogy` use; at most three, with `[]` allowed. Memory maps those
IDs to the same frozen evidence snapshot, rejects duplicates, unknown IDs,
missing dependencies and final-budget excess, and adds only a fixed use label.
An analogy is another experience, not evidence about the current event.
Malformed output, timeout, unavailable preparation and audit-write failure
cannot restore the entire candidate panel. Audit-write failure preserves a
previously legal subset; other selection failures pass empty long-term memory
to the ordinary Answer step with a diagnostic. Existing legacy `select`
fallback remains unchanged. Reliable-v1/v2 prepared reads now retain their
exact source/fact blocks and source dependencies from their one read, making
their public `subset` boundary usable without a second retrieval.

The frozen 32-question local comparison found useful sources omitted from the
20-item panel for the Night Flight recording question, three selection
protocol failures and no qualified graph-only conversation gain. It did not
meet promotion criteria; the default remains `reliable-v2`. Generic advice or
mere nonempty selection is not counted as a memory benefit. The local report
contains per-case evidence, provider receipts and exact Chat text; it is not
a runtime dependency and is not committed.

## Semantic associative v2 explicit reader

`semantic-associative-v2` is an opt-in successor to the v1 semantic reader,
selected with `LUMINA_MEMORY_PROFILE=semantic-associative-v2` and the usual
`LUMINA_MIND_GATE_MODE=llm`. It preserves the v1 reproduction path, the v6
writer, persisted graph, FirstHit equation, 5-seed/64-node/256-arc discovery,
multilingual index fingerprint and production `reliable-v2` default. A fresh
service using this profile reads first, runs **one** post-read Mind selector in
place of the boolean gate, and passes at most three selected Facts to Answer.
Conflicting gate and selector modes are rejected at app creation.

The direct facade is `MagmaMemoryAdapter.prepare_recall(question, policy)`;
the profile dispatches to `prepare_semantic_recall_v2`. Normal Chat supplies
`RecallPolicy(max_evidence_items=32, max_chars=9000, max_bytes=36000,
include_source_context=True)`. The owner collects the same bounded seed,
indexed and graph Fact pool. Its deterministic panel admission offers seeds,
source-group representatives, graph-only representatives, then source-turn
coverage before support fill. A group is a source window, **not** an event or
identity assertion. Up to 32 complete canonical Fact cards with source role,
timestamp and opaque group label enter Mind; an over-budget Fact is skipped
whole and diagnosed. Full source-backed rendered blocks remain separate in
`PreparedRecall` and never get shortened to fit the selector. The v1 20-card
ordering and budgets remain available by explicit v1 profile.

`LlmSemanticEvidenceSelectorV2` returns ranked existing IDs with a fixed
`history` or `analogy` use and legal relation. The prompt asks for at most
eight; the strict parser accepts at most twelve distinct, legal, in-panel
suggestions, so a benign 5- or 9-ID response need not erase all memory.
Malformed JSON, unknown/duplicate IDs, illegal use/relation pairs and outputs
above twelve fail closed. `PreparedRecall.ranked_semantic_subset` checks
dependencies and packs whole canonical rendered Facts in ranked order, skipping
an item that exceeds the final **3 Fact / 5000 character / 20000 byte** budget.
Each accepted block gets fixed, code-authored usage guidance. `history` is
scoped to its recorded event, speaker and time; `analogy` is explicitly a
different experience and cannot establish current identity, outcome, status
or permission. A newer explicit user decision overrides an old boundary.
The grounded historical claim guard continues to apply. No free-text model
summary or second verifier enters the Answer prompt.

`recall()` and `recall_associative()` cannot bypass the v2 selector to return
the unselected panel. Selector failure yields empty long-term memory; audit
records proposed/accepted counts, budget rejection and selected order.
Production and other explicit profiles retain their prior behavior. Isolate
Hot, Cold, MAGMA and audit paths when running a comparison, and keep the same
graph, original question, recent context, model, temperature and Answer cap.

The frozen old 32-question diagnostic confirmed that the previously omitted
AC02/SA27 prompt-card Fact entered the v2 panel and was selected; 5/9 legal
suggestions no longer fail the protocol. In a separate six-story, 24-question
fresh panel, five of six explicit cross-event analogy requests were still
labeled `history`, and one story cluster produced a qualified graph-only
Answer gain. A read-side arc cut/restoration established navigation dependence
for that Fact without rerunning a counterfactual Answer. The candidate is
**retained, not promoted**: `SEMANTIC_SELECTION_NEEDS_FIX`. Full fictional
stories, gold, frozen receipts and actual Answers remain local experiment
materials, not repository code or production memory.
