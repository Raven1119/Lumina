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

The default v2 prompt, boolean parser, temperature 0 and eight-token output
budget are unchanged. Mock models use the constant gate unless `direct` was
explicitly selected. Gate-client construction failure falls back to the constant
gate. Other mode strings use the existing default selection.

V3 free-query generation and v4 character replacement are retired from maintained
execution. Their frozen source and outcomes remain local experimental evidence;
`contextual` no longer enables a query-generation protocol. They are not runtime
or CI dependencies. The default is not promoted to `direct` by these changes.

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
