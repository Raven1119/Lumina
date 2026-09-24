# Chat Memory read contract

Production Chat is separate from the persistent Mind/Nervous/Execution loop.
MessageRuntime loads the current conversation once, retains the original user
question for Answer and Hot, and performs at most one bounded Memory read.
There is no query editor, second memory store or read-time ingestion.

## Modes

`LUMINA_MIND_GATE_MODE` is read when the app is created; restart to change it.

| Mode | Read path |
| --- | --- |
| `llm` (default) | Original v2 boolean gate, then existing bounded Recall if allowed. |
| `constant` | Constant allow decision, then the same bounded Recall. |
| `direct` (explicit experiment) | No pre-read gate; original question reads whole bounded candidates for Answer. |
| `select` (explicit experiment) | One original-question read, then one semantic selection of existing source evidence, then Answer. |
| `graph-read-v2` (explicit candidate) | One structured Mind gate, then matching query-driven graph Memory, then Answer with original question and conversation. |

`LUMINA_MEMORY_PROFILE=semantic-associative-v1` is a separate explicit
read-side candidate. With the default gate-mode setting it substitutes one
post-read semantic `history`/`analogy`/empty choice for the boolean call;
there is no stacked gate. It requires Memory's complete candidate-level
`PreparedRecall` and rejects conflicting gate/selector configurations.
Selection failures pass empty long-term memory to Answer and are audited;
unlike legacy `select`, they never restore the unselected panel. Its one
384-output-token choice usually adds much more input than the default
eight-token gate. Source Facts and fixed use labels alone enter Answer.

`LUMINA_MEMORY_PROFILE=semantic-associative-v2` is a second explicit choice
at the same Chat seam. It preserves the v1 profile and replaces its 20-item
panel with up to 32 complete deterministic Fact cards. One
`LlmSemanticEvidenceSelectorV2` call returns ranked, typed suggestions:
`history` requires `same_event`, `same_entity_background` or
`historical_boundary`; `analogy` requires one of five specific similarity
relations. Mind is asked for at most eight; the parser tolerates twelve legal
distinct IDs. Memory validates and packs at most three complete rendered Facts,
then adds fixed relation/use guidance to Answer. Invalid output or unavailable
preparation passes empty memory, never the full panel. The Answer template
explicitly permits a labeled analogy as a comparison while forbidding transfer
of its people, event, outcome or permission to the current case. The same
grounded historical claim guard applies. Actual fresh evaluation found that
the model still mislabeled five of six cross-event analogies as history, so
this profile remains experimental and is not the default.

`LUMINA_MEMORY_PROFILE=semantic-associative-v3` keeps the same one-call
read-after-selection seam and strict v2 parser/three-Fact packing. Memory
first builds a graph-independent BasePanel from seeds and multilingual index
hits; full graph read can append at most eight graph-exclusive cards without
changing any base ID, order or card bytes. Mind's single v3 selector decides
same-event history before treating a different past event as an analogy.
Answer receives an internal rule that a bounded selected block is not an
exhaustive proof of what was ever recorded; empty or failed selection cannot
justify a global absence claim. Selection failure still injects no candidate.
Production and v1/v2 behavior are unchanged.

The default v2 prompt, boolean parser, temperature 0 and eight-token output
budget are unchanged. Mock models use the constant gate unless a read-first or
structured candidate mode was explicitly selected. Original gate-client construction
failure falls back to the constant gate. Other mode strings use the existing default selection.

V3 free-query generation and v4 character replacement are retired from maintained
execution. Their frozen source and outcomes remain local experimental evidence;
`contextual` no longer enables a query-generation protocol. They are not runtime
or CI dependencies. Neither read-first mode is promoted to the default by these changes.

## Default gate and audit

`MindGate.decide(original_message, recent_context) -> MindDecision(recall: bool)`
runs before the enabled/available/policy guards. Invalid decisions or provider
failure fail open with the original question. The gate does not edit text.

The existing append-only decision log retains `turn_id`, `recall` and
`decided_at`. Runtime also records `prompt_version`, `original_message`,
`effective_query` and `fallback_reason`. Old lines are never rewritten. An audit
failure restores allow and attempts one fallback append; if both writes fail,
Chat remains available with a log-failure event. Direct `record` callers without
an audit still use the original three-field format. Injected boolean gates may
omit a logger as before.

## Structured query candidate

`LUMINA_MIND_GATE_MODE=graph-read-v2` explicitly pairs `LlmQueryMindGate`
(`mind-query-gate-v1`) with the Memory adapter's `graph-read-v2` reader. Configured
real-model apps retain the same v6 writer, FirstHit policy and Cold owner. Neither
the default boolean gate nor `reliable-v2` is switched. The candidate keeps Chat's
three-Fact / 5000-character output budget and the existing 32-segment Cold window.
An additional evidence selector cannot be combined with this mode.

The one gate client uses DeepSeek-V4-Pro, temperature 0 and a fixed 768-token
output cap. It replaces the boolean call; no second parser/selector call follows.
The input contains the unchanged current message and up to the last 12 complete
near turns within 6000 characters. Over-budget turns are omitted with a count,
never silently truncated or rewritten; Answer still receives its existing full
context. A current message above 8000 characters bypasses the gate call and
degrades to one open read of the original text.

The strict JSON response has six fields:

```json
{"recall":true,"mode":"precise","clues":[{"id":"c1","text":"我","kind":"current_user","sources":[{"index":-1,"quote":"我","occurrence":0}]}],"relations":[{"subject":"c1","predicate":"用","object":"?entity","sources":[{"index":-1,"quote":"我用","occurrence":0}]},{"subject":"?entity","predicate":"重","object":"?value","sources":[{"index":-1,"quote":"多重","occurrence":0}]}],"unresolved":[]}
```

For the example input `我用的录音机多重？`, this requests two relations without
guessing a device or value. The schema permits at most three clues and two
relations. Clue kinds are `name`, `current_user`, `topic`, `literal`; endpoints
are existing clue IDs, one shared `?entity`, or terminal `?value` in object
position. Each clue/relation cites one or two exact quotes (at most 256 characters).
Index -1 is the current user message; other indices refer to the actual bounded
near input. Occurrence is zero-based. Code computes and checks true offsets,
checks clue text against its quote, and refuses assistant-only evidence for a
`current_user` clue. Models cannot supply EntityRefs or offsets.

The Memory-owned DTO and `validate_query_intent()` recheck bounds and citations
at the read boundary. A real quote proves its location, not a correct semantic
interpretation: reference resolution, direction and meaningful predicates still
need independent evaluation. Unsupported predicates remain unresolved in Memory;
the example does not grant a new universal relation vocabulary.

Valid `recall=false` has empty open conditions and performs no Memory read.
Malformed output, unavailable clients and provider failures fail open with the
original question, a stable audit reason and no retry. A valid precise intent
with unresolved negation, permission, temporal scope or other limitations keeps
its accepted relations and unresolved fields; Memory must not report a complete
result by deleting them. An uncertain direction may be explicitly open with no
relations and a preserved explanation. `mode=open` is not a precise success.

`MindDecision` retains `recall` and optionally carries a `GraphReadQuery` plus
audit data. `/api/chat` passes that request through public `Memory.recall()` once;
Answer and Hot Draft keep the original user text. The append-only log adds the
validated structured request, exact bounded near context, bounded raw gate
receipt, output cap, attempted call count, latency and degradation reason. These
private fields never enter historical evidence. `generate()` does not expose
native provider usage; evaluation must meter actual input/output/cache usage at
the provider boundary, and must not treat missing usage as zero tokens or cost.
For this structured mode, an audit-write failure preserves an already valid
decline or precise request and emits a log-failure event; it never substitutes an
open query for accepted conditions. The legacy boolean gate's historical
fail-open audit behavior remains unchanged.

Tests `test_query_mind_gate.py` and `test_query_mind_chat.py` use injected models
and synthetic evidence to check validation and actual Chat wiring. They do not
establish natural-language parsing quality or net recall benefit.

## Direct bounded candidates

Explicit `direct` skips gate construction and execution, including any injected
pre-read gate. It calls the same public `recall(query, policy)` with the original
question. The default experiment policy is `top_k=10`, `max_graph_depth=1`,
`max_nodes=20`, `max_evidence_items=20`, `max_chars=5000`,
`final_min_score=None`, `include_source_context=True`. Programmatic explicit
policy injection remains supported. The existing Recall enabled/available guards
still apply. Direct mode produces no fictitious Mind gate decision or audit.

BGE, candidate discovery, Hindsight, provenance projection and whole dependency
packing remain Memory-owned. Removing the final floor does not bypass source or
chain checks. Original facts are kept whole; required bridge facts must also fit.
Source headers and binding annotations consume the same character budget.
Neither the entire database nor private backend objects are exposed.

With source context enabled, each fact displays its original USER/LUMINA role,
source speaking time and timezone. Anonymous subject/object labels preserve
existing stored role bindings across returned facts. They expose no persistent
EntityRefs and create no identity attributes. Identical labels share a stored
binding; different labels alone do not prove distinct real-world objects.
Occupation or other identifying information must be present in original facts.
Mention proximity or equal name strings cannot invent a role or resolve namesakes.

The source-context Answer guidance uses the original question and visible
conversation for corrections, referents, direction, history, negation and
conditions. A timestamp is when the source spoke, not proof of when a fact became
true. Unverified assistant guesses are not established USER facts. Historical
memory does not automatically override an explicit current user correction.
Missing identity or requested facts permit supported partial information or an
honest clarification; empty evidence does not establish nonexistence.

## Selection after the read

Explicit `select` uses the same policy and candidate order as `direct`, with no
pre-read gate or query rewrite. `Memory.prepare_recall(query, policy)` produces
one immutable request-local `PreparedRecall`. Its `context` is the original
bounded result; `selection_items` exposes existing evidence IDs and exact
successful rendered blocks, not backend objects or persistent entity refs.

`LlmEvidenceSelector` receives the original question, original near conversation
and those same source blocks under local integer handles. It uses the configured
DeepSeek-V4-Pro client with temperature 0 and 1024 output tokens. A complete JSON
array of unique in-range integer handles maps back to existing evidence IDs.
A complete JSON fence is tolerated. A malformed response is a failure, not an
empty selection. Input evidence is bounded to 20 items and 5000 rendered
characters; prompt/JSON overhead and the original conversation are separate
provider input costs. Programmatic policy overrides retain their ordinary
bounds; an oversized selection input falls back without another model call.

Memory's `PreparedRecall.subset(ids)` retains the selected whole source blocks
and their actual validated association dependencies, in original order and with
unchanged anonymous labels. It performs no second retrieval, scoring, source
projection or write. Identity-limiting facts, such as an occupation, are not
invented dependencies: the selector must include the actual supporting facts.
Their omission remains a semantic failure even if an answer value is correct.

An intentional empty selection leaves Answer with the original background and
conversation. Additional Answer guidance instructs the model not to treat
unresolved reference or old source age as evidence that a proposition is false or an
attribute currently absent; it does not assert that every old fact remains true.
This is a prompt instruction, and semantic acceptance must check actual answers.

Selector/provider/subset failure restores the same complete prepared context.
Unavailable prepared metadata preserves that original context and fails
selection visibly. A legacy facade without preparation falls back to its one
ordinary read. A failed preparation cannot trigger another read. Empty, disabled
or failed Memory makes no selector call. Mock/unavailable selector clients fail
softly through the same audited path. Gate and selector cannot both run in a
single Runtime; app read-first modes ignore injected pre-read gates.

The existing Mind decision log records the original query, prompt version,
proposed evidence IDs, effective IDs after dependency closure and any fallback
reason. Here `recall=true` records the completed read, even when the selected
subset is empty. Audit failure restores the original context and attempts one
fallback append. No model output or audit record is elevated to a stored fact.

## Grounded historical claims in Answer

The production Chat base prompt applies the same rule to default and explicit
read modes, including no-memory replies: a reference such as “last time” is
not evidence of what happened. Concrete prior events need support in the
current message, visible near conversation or relevant returned Memory. If
that support is absent, Answer may address the present request and express
uncertainty, but must not invent a shared episode. A returned unrelated story
is still a retrieval error; the prompt instructs Answer not to transpose it.
Old unverified LUMINA guesses do not become user facts, and a current user
correction takes precedence. Supported relevant history remains usable. This
is one prompt contract, not a second LLM verifier or a guarantee of model
compliance; deterministic prompt tests and real Answer evaluation are separate.

## Failure and validation

Disabled, unavailable, failed or empty Recall leaves Answer with the original
background and recent context. Raw errors, paths and credentials are not injected.
No failure triggers another retrieval or semantic call. Answer and Hot continue
to use the original question. Recall is read-only; restart does not rewrite facts.

Maintained tests cover original inputs, source rendering, whole-group budgets,
default compatibility, restart and failure behavior. They establish mechanics,
not general semantic ability. Real comparisons must separately measure necessary
facts in candidates and Answer, source-supported task completion and ancillary
claims, unknown/ambiguous cases, actual new HTTP versus exact replay, input/output
tokens and local retrieval/BGE cost. More candidates or fewer model calls alone
do not establish a net benefit. Experimental reports stay local and are not
required by runtime or CI.
