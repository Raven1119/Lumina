# Explicit first-hit Memory

## Status and scope

`first_hit=FirstHitPolicy()` enables a maintained, zero-generation association
path over existing EVENT facts and verified ENTITY identities. New Formation v2
windows use that same activation to plan local semantic links. Original source
expansion goes through the injected Cold owner. Historical Formation v2,
legacy `recall`,
prepared Recall and the separate source-index interfaces retain their contracts.
The original v2 profile remains explicit. Production v6 now reuses this
FirstHit mechanism with `reliable-v2` presentation; see [Reliable Memory](RELIABLE_MEMORY.md).
The historical mechanism evidence below is not an A/B result or a claim of
semantic superiority for v6.

No new persistent memory object, causal relation, identity inference, automatic
Dream, memory agent or consumer/tool loop is introduced. Pinned MAGMA and the
DeepSeek-V4-Pro Formation interface remain unchanged. Read/association logic
uses local embeddings and NumPy, with no generative call.

## Actual entries

From the repository root, use the Memory environment for real MAGMA. Construct
one backend, checkpoint store and Cold owner for the same manual writer:

```python
from pathlib import Path
from Dream.runner import RealMemoryIngestorProvider, DreamRunner
from Dream.cold_draft_digest import ColdDraftDigestionTask
from Dream.models import DreamRunPolicy
from core.cold_draft_store import ColdDraftStore
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.models import RecallPolicy

# Explicit isolated/new state; formation_model is the existing configured
# DeepSeek-V4-Pro client, or fixed native responses in a synthetic check.
root = Path("my-isolated-memory")
owner = ColdDraftStore(root / "cold.jsonl", source_window_segments=32,
                       source_window_bytes=1_048_576)
provider = RealMemoryIngestorProvider(
    root / "magma", root / "ingestion.json", formation_model,
    first_hit=FirstHitPolicy(), cold_store=owner,
)
memory = provider.get("grounded-formation-v2")
runner = DreamRunner(owner, ColdDraftDigestionTask(owner, provider))
# Append through owner.append_segment(...), then explicitly invoke Dream.
report = runner.run_once(DreamRunPolicy(ingestion_version="grounded-formation-v2"))
result = memory.recall_associative(
    "original complete cue", RecallPolicy(max_chars=3000, max_evidence_items=5),
    include_sources=True, source_context_turns=1,
)
# result.facts and result.sources retain different evidence namespaces.
# result.rendered_text is their combined, bounded material.
raw_only = memory.recall_recent_sources("cue without a formed fact")
```

A direct `MagmaMemoryAdapter.create_real(..., ingestion_version=
"grounded-formation-v2", formation_model=..., first_hit=FirstHitPolicy(),
cold_store=owner)` exposes the same entries. Adapter construction never migrates
stored windows. An omitted Cold owner leaves facts usable and reports source
expansion unavailable. The Cold window defaults to disabled unless configured.

The existing explicit CLI also accepts `python -m Dream.runner --ingestion-version grounded-formation-v2 --first-hit`.
It uses existing `LUMINA_DREAM_COLD_DRAFT_PATH`,
`LUMINA_DREAM_INGESTION_STATE_PATH` and `LUMINA_DREAM_MAGMA_PERSIST_DIR` settings,
with a 32-segment/1 MiB Cold window. This command explicitly selects Formation v2. Without the version option, a
configured real model selects the current v6/reliable-v2 default. As with the legacy
CLI, stop the single-worker application service before using it. There is no
new Chat HTTP option or change to the Chat event flow.

### Provider-free vertical correctness check

```bash
HF_HUB_OFFLINE=1 Conversation_Memory/.venv/bin/python -m scripts.first_hit_memory_check
```

This maintained entry uses a fresh isolated directory, fixed synthetic Formation
responses and real pinned MAGMA/local embeddings. It exercises Cold append,
manual Dream, actual planned links, persistence, restart, associative retrieval,
exact source expansion, expiry and recent dialogue without facts. It prints
safe aggregate checks. No credentials or real history are needed. See its
`--help` for explicit work-directory handling.

## Numerical and graph contract

### Explicit graph-read-v1 candidate

`associative_read_profile="graph-read-v1"` selects the complete read candidate
through the existing `MagmaMemoryAdapter.recall` / `recall_associative` facade.
Production remains `reliable-v2`. The candidate's owner is
[`_graph_read.py`](../adapter/_graph_read.py); request DTOs live in
[`graph_read_query.py`](../adapter/graph_read_query.py), bounded exploration in
[`_first_hit_read.py`](../adapter/_first_hit_read.py). No new state or writer
profile is introduced. `_activate_first_hit`, including the ingestion caller,
still uses the original `discover_first_hit`. `FirstHitPolicy` and serialized
link-plan/checkpoint fields are unchanged.

```python
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.graph_read_query import (
    GraphReadQuery, ReadClue, RelationConstraint,
)
from Conversation_Memory.ingestion.state_store import IngestionStateStore

# existing_magma_copy / checkpoint_copy / cold are isolated existing owners.
# No formation_model is required for reading. No BGE/provider call is made.
memory = MagmaMemoryAdapter.create_real(
    existing_magma_copy, IngestionStateStore(checkpoint_copy),
    fail_if_unavailable=True, ingestion_version="grounded-formation-v6",
    first_hit=FirstHitPolicy(), cold_store=cold,
    associative_read_profile="graph-read-v1",
)
budget = RecallPolicy(max_evidence_items=5, max_chars=3000)
natural = memory.recall(original_question, budget)
explicit = memory.recall(GraphReadQuery(
    original_question,
    clues=(ReadClue(known_name, (known_entity_ref,)),),
    relations=(RelationConstraint(known_relation, object_refs=(known_entity_ref,)),),
), budget)
# A role-only caller constraint uses relation=None. Multiple relations are
# separate obligations requiring actual Facts, not an inferred composite Fact.
print(natural.rendered_text)
print(explicit.rendered_text)
```

Only supply caller-known conditions; target Fact IDs and evaluation gold are
not runtime inputs. A string preserves the complete natural question for one
unchanged bounded seed search. Existing indexed entity surfaces supply cue
groups; same-name refs within a group are OR alternatives. Names do not imply
AND. This reader does **not** parse arbitrary relation direction or conjunction
from language: natural input records both gaps and keeps open association.
Explicit `require_all=True` enables coverage ranking for declared clues.

The shared entry set is frozen once, at most five seeds. Each declared group
masks that set by existing entity membership (ordinary mentions may support
navigation) or the owner's existing lexical features. Covered groups have unit
input mass; uncovered groups have zero mass and a visible diagnostic. No extra
per-group retrieval, graph traversal or embedding is performed. Without named
or declared clues, the whole query retains the global seed vector as one group.
The original global `b` and `h` remain available. One resolvent solve is reused
for `H[c] = (b_c @ R) / diag(R)`. For explicit conjunction, `min_c H[c,v]`
ranks within each existing direct/associated pool; it measures graph support,
not the probability or truth of a conjunction.

The read queue updates best known max-path support, including an existing seed,
and propagates strict improvements over cached arcs. Max-path values schedule
work; first-hit values still sum multi-path contributions. Physical adjacency
reads remain capped at `E=max_edges`; queue pushes at `E*(N+1)` and cached
relaxation attempts at `E*N`, where `N=max_nodes`. Stale heap entries cannot
override new priorities. Budget exhaustion returns a marked partial result.
Even pending peeks whose endpoints are local belong to its already-read matrix.
Unread adjacency keeps its complete denominator; zero-attention bridges conduct.
These finite limits do not guarantee the globally best subgraph.

The derived view keeps actual subject/object/ordinary labels from active stored
`REFERS_TO` edges. Multiple labels never increase channel mass. Final explicit
eligibility checks each original Fact's predicate, metadata bindings, actual
role edges and stored source-range shape. Unknown roles or predicates cannot
satisfy an explicit constraint. Missing Cold leaves canonical Facts visible
with the existing source status; metadata qualification is not exact original
source verification. Cold's owner still validates any requested supplement.
No eligibility filter removes navigation bridges.

Only eligible pool ordering and qualification change; the composer retains its
0.6 direct reservation and complete canonical bodies plus bounded source
supplement. Within a pool, unsatisfied relation obligations precede already
covered ones, then explicit clue coverage, then the original stable ordering.
Selected Facts collectively satisfy obligations only when all required actual
evidence is visible. Missing obligations or uncovered required groups report
`graph_read_evidence_incomplete` with partial evidence; no inferred statement is
rendered. Group support alone is not semantic sufficiency. Private diagnostic
snapshots contain groups, roles, work counts and the local matrix; none enter
public evidence text or persistent state. A failed candidate graph read returns
`graph_read_unavailable` without retrying the writer or dropping constraints.

Provider-free maintained smoke and regression:

```bash
Conversation_Memory/.venv/bin/python -m pytest \
  Conversation_Memory/tests/test_first_hit_read.py \
  Conversation_Memory/tests/test_graph_read_query.py \
  Conversation_Memory/tests/test_graph_read_facade.py -q
```

These synthetic checks prove mechanism and boundary behavior, not utility.
Real comparison must freeze the same existing graph, full query, MiniLM model,
seed/node/read-arc and rendering budgets; preserve missing questions and inspect
final `rendered_text`. Natural and caller-constrained results are separate arms.
The candidate remains explicitly unpromoted; it must not be inferred from a
writer version or enabled by an application default.

### Explicit query-driven graph-read-v2

This candidate connects natural language through the existing Chat gate and
Memory facade. With the normal configured model and memory owners, select:

```bash
LUMINA_MIND_GATE_MODE=graph-read-v2 \
  Conversation_Memory/.venv/bin/python -m uvicorn core.main:app
```

Send the original message to the ordinary `POST /api/chat`. The candidate
replaces the one boolean gate call with `LlmQueryMindGate` (temperature 0,
768 output tokens); it does not call both gates or a separate rewriter.
The original message and bounded recent conversation still reach Answer.
Valid `recall=false` makes no Memory call. Invalid interpretation keeps the
original message in a marked open fallback, with no resampling and at most
one read. See [the gate schema and audit contract](../../Mind/docs/CHAT_RECALL_GATE.md).

The DTO extends `GraphReadQuery` with optional `QueryIntent` and source context.
It permits three source-quoted clues, two directional relations, one shared
`?entity` and one terminal literal `?value`. Memory validates source positions
again. The gate supplies no database identities. Existing indexed names resolve
to up to five OR alternatives per clue; unresolved or truncated identities
remain visible. Exact quote validation proves a location, not semantic parsing.
Legacy conditions cannot be mixed with the new intent in one request; a v2
request containing only legacy conditions is rejected, not silently made open.

[`_query_graph_read.py`](../adapter/_query_graph_read.py) owns the read-only
interpretation boundary, entry allocation and local joins. A whole-question
search retains the bounded union of the same dense, lexical and entity channels,
before the final five entries. Each channel retains at most five; each search
retains at most twenty. Only a missing group may trigger a supplementary search,
at most two, so the deduplicated buffer has at most sixty entries (under the
64 ceiling). The first two whole-question anchors retain slots; remaining
slots prioritize uncovered groups, then existing RRF scores with stable ties.
One Fact can cover several groups without consuming several slots. Conditions
form at most two entry groups, followed by unused clue groups up to three;
all original clues also have separate coverage diagnostics.

`discover_query_first_hit` in [`_first_hit_read.py`](../adapter/_first_hit_read.py)
rotates active groups in fixed input order, one pending arc per group turn.
Each group updates max-path priorities; all groups share cached physical arcs,
at most 5 seeds, 64 nodes and 256 actual reads. Queue and cached relaxation work
each have a finite `G * E * (N+1)` limit (`G <= 3`). Stale entries cannot restore
weaker support. Full outgoing denominators, the global seed vector and FirstHit
equation remain unchanged. One resolvent supplies global h and all group H.
Scores measure support, never identity correctness, independent evidence count
or logical conjunction. Uncovered groups retain zero input; if every group is
empty, a single global frontier explores without declaring those groups met.

For precise intent, each candidate must independently pass the existing
`qualify_fact` role-edge and stored-source-shape checks. Cross-Fact combinations
enumerate at most `64 * 64` pairs and require identical bindings for every shared
clue/variable. Literal values need no entity edge. Predicate matching uses exact
normalization and the existing controlled vocabulary. That vocabulary has no
use/mass family, so this candidate alone adds the fixed ordinary use/使用/用
and mass/weight/重量/质量/重 aliases; v1 and the shared resolver are unchanged.
Unknown predicates remain unknown; traversal does not authorize them.

Compatible role bindings are necessary but not sufficient for complete output.
Multiple possible bindings or values remain ambiguous. Unresolved gate limits,
unaccounted extra clues, and incompatible or one-sided stored time annotations
keep the result partial. Extra clues are not silently turned into new AND
conditions: only exact existing role surfaces/predicates account for them.
Speaking timestamps alone are not treated as fact validity intervals. This
small reader cannot prove arbitrary device classes, negation, permissions or
historical scope from lexical similarity. It never fabricates a combined Fact.

The reliable-v2 composer first reserves the same 0.6 direct share. It then tests
all missing members of an evidence bundle jointly against the unchanged whole
item/character/byte budget, charging existing members once. It never displaces
protected direct evidence. Only an eligible complete bundle actually visible
in the final canonical bodies can satisfy the accepted relations; otherwise
the request returns `graph_read_evidence_incomplete` with available partial
evidence. Open association does not impose relation joins. Cold remains a
bounded supplement under the same 32-segment/1 MiB owner window and existing
output allowances. Query syntax and diagnostics are not rendered as facts.

For reproducible A/B/C comparisons, freeze the natural gate response and the
request-local `QueryEntries` once. A uses production original FirstHit plus
reliable-v2 on the raw question. B calls `activate_query_read(...,
entries=entries, seed_only=True)` and the unchanged reliable-v2 composer; it
opens no adjacency cursor. C uses the same entries with exploration enabled
through the public facade. B/C thus differ only in expansion, while A/C also
include interpretation and entry changes. `QueryEntries` is ephemeral and
valid only for that request and graph version; it is not a new state store.

Maintenance checks (synthetic, no provider or BGE) are:

```bash
Conversation_Memory/.venv/bin/python -m pytest \
  tests/test_query_mind_gate.py tests/test_query_mind_chat.py \
  Conversation_Memory/tests/test_query_first_hit.py \
  Conversation_Memory/tests/test_query_graph_read.py -q
```

These checks establish mechanisms and actual routing, not natural retrieval
benefit. Frozen real-graph comparisons must inspect complete `rendered_text`,
retain semantic parse failures and missing historical data, and distinguish
seed-selection benefit from new nonseed evidence. Experimental inputs, gold,
model receipts and results are local materials, not repository dependencies.
Production stays v6/original FirstHit/reliable-v2. `graph-read-v1`, its original
source and numerical semantics, writer activation and checkpoints remain intact.

### Original FirstHit and shared equations

Defaults are engineering starting values, not measured optima:

| Policy | Default |
| --- | --- |
| `decay` | 0.75 |
| `max_seeds / max_nodes / max_edges` | 5 / 64 / 256 |
| `attention_budget / attention_penalty` | 1 / 0 |
| `max_links` | 3 (write cap is 3) |

Seeds use existing dense, indexed lexical and entity-conditioned retrieval,
with stable RRF(k=60) fusion and normalized nonnegative input mass at most one.
No seed means no uniform whole-graph activation. Write-time entity entries come
only from the already verified bindings, including an explicit empty binding set.
Retrieval performs no old graph traversal, BGE reranking or Hindsight/floor pass.
A non-None `RecallPolicy.final_min_score` returns
`first_hit_score_policy_conflict`. The first-hit policy controls discovery and
competition; only the output policy's item/character bounds, relation filter
and optional source labels apply to the resulting facts.

The backend maintains a derived directed view on load and owner writes. Its
complete outgoing totals include only eligible channels: actual stored temporal
PRECEDES/SUCCEEDS directions, semantic RELATED_TO directions and bidirectional
navigation over actual Fact-to-ENTITY REFERS_TO edges. Repeated physical
(source,target,family) records use maximum weight, not summed duplicate votes.
Existing event-to-event entity shortcuts and all causal edges are excluded.
Entity direction reversal is navigation, not another persisted factual claim.
Temporal/entity channels have unit weights. Legacy semantic scores are the
pinned `1/(1+L2_distance)` values; new semantic weights are nonnegative endpoint
cosines. Missing/invalid semantic weights make that local row unavailable.
Raw native FAISS distances are never activation weights.

A stable owner cursor and path-strength queue visit at most the configured
nodes and actual adjacency entries. Even cursor peeks consume edge budget.
Bridge nodes may conduct before final competition. Only actually read arcs enter
the fixed local matrix; it is not completed into a free induced subgraph.
Complete denominators remain fixed:

```text
s[u] = sum of all eligible outgoing channel weights
P[u,v] = actually_read_weight[u,v] / max(1, s[u])
full_row_mass[u] = s[u] / max(1, s[u])
R = solve(I - decay * P, I)       # float64, local matrix only
y = b @ R
h = y / diag(R)
e = decay * (full_row_mass - P.sum(axis=1))
delta = y @ e
```

Checks cover shape, finite values, transition bounds, positive diagonal and
`(I-decay*P) @ R` residual. Only rounding-scale correction is permitted.
Numerical/view failure retains independently projectable seed facts with
`first_hit_unavailable`; writing cannot freeze a degraded plan. There is no
whole-graph fallback or retry/resampling inside one activation. Unavailable
seed channels retain usable read material with `first_hit_seed_channel_unavailable`;
writing keeps its responsibility pending rather than freezing a degraded plan.

Under the same full graph snapshot, seed vector and complete denominators,
local h is a lower bound on full h. Each omitted or visited node's missing
first-hit mass is at most delta. Filling unread arcs can only increase h under
those fixed conditions. Delta excludes ordinary attenuation/death, missed seed
retrieval and semantic correctness; it is neither a confidence value nor a
Recall error rate, and it does not guarantee final selection stability. A real
graph change can change denominators and invalidates that monotonic comparison.
Cycles do not repeatedly reward returning to the same target; they can still
change first arrival at other targets.

Only facts compete after solving. Attention minimizes
`0.5 * ||a-h_fact||^2 + attention_penalty * sum(a)` over
`a >= 0, sum(a) <= attention_budget`. The implementation projects onto the
nonnegative l1 ball; spare budget stays unused. This does not guarantee k nonzero
facts. Sort order is attention, h, stable evidence ID. Packing returns whole
facts or marks omission. Navigation through a bridge does not require outputting
its text, because no joint factual conclusion is asserted by this interface.
Numerical values, delta, graph paths and UUIDs remain private diagnostics.

## New-window write and recovery

Formation owns extraction, verification and bindings exactly as before.
`segment_id:first-hit-v1` is an additional version key in the same
`IngestionStateStore`, not a new state owner. A reservation is persisted before
creating the new v2 checkpoint. Its strict schema freezes the algorithm profile,
source digest, ordered batch evidence IDs, link plan and plan digest.

Before the first associated graph mutation, each verified new fact activates
old history through the same core. The complete current batch is excluded,
including any already-written members during recovery. Candidates rank by
`h_old * max(cos(new_text, old_vector), 0)`. Up to three distinct old facts are
selected; zero links is valid. Actual semantic edges store stable endpoints,
algorithm and weight-source metadata and support two navigation directions.
This configuration disables upstream automatic semantic linking for these new
events and skips the old same-name event clique. Existing verified ENTITY roles
and temporal responsibilities remain intact. A graph-before-vector interruption
also repairs the original temporal predecessor in this explicit writer, using
persisted node insertion order and timestamp order; later arrivals cannot change
that original predecessor. Default v2 recovery retains its previous behavior.

```text
first-hit reservation -> frozen plan -> nodes/vectors/roles durable
-> idempotent semantic plan durable -> first-hit completed -> v2 terminal
-> only complete combined ingestion allows Cold owner consumption
```

A graph/state write failure preserves the frozen plan. Restart reuses it,
without another activation or Formation sample. A graph-before-vector
interruption also repairs the new writer's original temporal predecessor using
persisted node insertion order and timestamp order, excluding later arrivals.
Default v2 recovery retains its previous behavior. A source-ref repair may append
new verified units under the existing repair rules; existing plans stay frozen,
and only appended units get plans excluding the entire extended batch. A
terminal v2 record requires an exactly matching completed association stage;
impossible combinations fail closed. An outstanding association stage requires
the same configured first-hit profile on resume.

Any existing v2 checkpoint without a first-hit reservation keeps its original
responsibilities, whether pending, partial or completed. Selecting this profile
never upgrades it or reprocesses consumed Cold. Old graph edges are retained.

## Cold source window

See [Cold owner contract](../../docs/COLD_DRAFT.md). The owner keeps a rebuildable
recent view bounded by segment count and immutable serialized UTF-8 source
bytes. Ordering follows actual append order; retry and consumption do not
refresh age. Initial load and existing owner writes may read the archive.
Queries only consult the cached view and a file signature. Unexpected external
file-signature changes invalidate the view rather than triggering an archive
scan. This metadata detector cannot detect a same-length in-place rewrite whose
file identity and timestamps stay unchanged (including rapid Windows writes).
Such external writers are outside the single-writer contract; reopen the owner
after external modification. Query-time archive hashing is deliberately absent.

Only selected facts' exact `(segment_id, turn_id, source_start, source_end)`
references request expansion. Supporting text and supplied role/time/timezone
are checked against Cold. Legacy V1 deterministic turn projection is retained.
Overlaps merge, unread gaps remain separate, and support ranges have priority
over optional neighboring turns. The source results preserve original roles,
times, coordinates and source text. Expiry removes expansion eligibility, never
the source archive or long-lived fact.

Facts and source supplement share one final rendered-character limit, including
the separator. The raw-only `recall_recent_sources` entry is explicit and uses
bounded lexical postings over this same window; it can return conversations
that formed no facts. It is not a mandatory graph-retrieval prerequisite.
Unavailable/partial/stale source status remains visible while facts remain
usable. Existing separate source namespace methods are never called on the fact
backend to manufacture raw records.

## Costs and limits

Only local matrix work is bounded by the first-hit discovery policy. Backend
load rebuilds derived indexes over stored nodes/edges and orders adjacency rows;
owner row insertion can cost proportional to row degree. Flat FAISS and entity
selector search still depend on the stored index; bounded lexical postings can
miss relevant candidates. Graph/vector persistence, checkpoint JSON replacement
and Cold owner archive rewriting retain their existing whole-store costs. The
source window bounds cached source material and online expansion, not archival
write complexity. No end-to-end O(1) claim is made.

The single-process writer contract remains. Arbitrary direct mutation of backend
graph internals is unsupported; all supported writes maintain the view version.
Local facts must have legal source provenance. Source support is not proof of
world truth. Original conditional language, uncertainty, attribution and
assistant/user roles are retained. Quality, sparse attention defaults and the
usefulness of semantic links remain evaluation questions for a later task.


## Maintenance validation (2026-09-16)

- New correctness/recovery tests in the prepared Memory environment:
  **115 passed**. This includes the actual MAGMA vector-interruption repair,
  snapshot/edge budgets, absorption-equation comparison, sparse projection,
  frozen-plan retries, Cold window bounds and source validation.
- The maintained real vertical entry passed: two synthetic windows and facts,
  one planned logical association persisted as two directed semantic links,
  identical restart results, read-only queries, source expiry with facts retained,
  and independent recent-source lookup. It used four fixed Formation interface
  responses and **zero provider generation requests**. Its one-seed read visited
  four nodes, ten adjacency entries and a 4x4 matrix; source excerpts used 57
  UTF-8 text bytes. These counts describe this correctness fixture only.
- The pre-existing fixed real-MAGMA acceptance passed: **10/10** Recall checks,
  **11/11** required production evidence, restart and idempotency.
- Review and regression exposed and repaired optional-backend compatibility,
  stale entity-membership signatures after semantic writes, missing original
  temporal links after interrupted vector writes, degraded seed-channel plans,
  cross-package policy compatibility and fallback attention limits.
- Two external-write tests initially relied on Windows timestamps changing on
  rapid same-length writes. They now use deterministic atomic replacement and
  separately characterize the unchanged-signature limitation documented above.
  That limitation is retained, not hidden by an archive-scan fallback.

The final combined suite (`tests Mind Nervous Execution Conversation_Memory/tests
Dream/tests`) passed **1878 tests**, with **77 explicit skips** and eight instances
of two existing upstream deprecation warnings. It ran with application paths
redirected to fresh temporary state and private default env loading disabled.
`git diff --check`, Python syntax and maintained document links passed. The six
pre-existing protected data/credential files retained their exact hashes; pinned
MAGMA remained at `467cb70b67ac337b22fdb42194d37c04ad701b62` with a clean worktree.

Validation uses synthetic state and fixed responses. No A/B, parameter sweep,
consumer evaluation or claim of semantic superiority was made. Status:
**implementation and correctness verification complete; semantic quality awaits
later evaluation**.
