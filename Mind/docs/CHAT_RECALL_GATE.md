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

The default v2 prompt, boolean parser, temperature 0 and eight-token output
budget are unchanged. Mock models use the constant gate unless a read-first mode was
explicitly selected. Gate-client construction failure falls back to the constant
gate. Other mode strings use the existing default selection.

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
conversation. The additional Answer guidance prevents unresolved reference or
old source age from being treated as evidence that a proposition is false or an
attribute currently absent; it does not assert that every old fact remains true.

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
