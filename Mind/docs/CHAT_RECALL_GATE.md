# Chat Recall decision contract

The production Chat path owns one `MindGate.decide(original_message, recent_context)` call before the Recall guard. This is separate from the persistent Mind/Nervous/Execution cognitive loop. No new model owner, memory store or query planner is introduced.

## Selection

The default `LUMINA_MIND_GATE_MODE=llm` retains `mind-gate-v2`, its original boolean prompt and eight-token budget. Set the existing variable to `contextual` to opt into `mind-gate-v3` and the bounded query protocol below. Direct injected callers use `LlmMindGate(model_client, contextual=True)`. This capability is implemented but is not promoted as the default; query faithfulness remains a limitation of the explicit mode. Mock model mode and `constant` retain the original-query Constant gate, and client construction failure still falls back to it.

## Decision and source boundary

`MindDecision(recall: bool, query: str | None = None, context_refs: tuple[MindContextRef, ...] = ())` preserves old boolean callers. `MindContextRef(index, span)` identifies a position in this call's existing context view, not a persistent turn ID or EntityRef. The view is loaded once; a rolling summary can occupy a position but cannot authorize a query rewrite. Source spans must come from original user/assistant messages and occur exactly in both the cited message and the proposed query. This is a literal provenance check, not proof of semantic coreference.

The contextual protocol is one complete JSON object with exactly `recall`, `query` and `context_refs`. Its bounds are 256 query characters, two references, 96 characters per span and 2048 output characters. Query completion must preserve the requested attribute, direction, time, negation, conditions and corrections. Self-contained questions, missing antecedents and unresolved ambiguity use `query=null`; `recall=false` cannot carry an executable query. Assistant guesses must not become fact assertions or answers inside a query. Legacy bare boolean responses remain accepted.

The contextual gate budget is 1024 output tokens with the existing temperature 0 and DeepSeek provider. Oversized or incomplete output fails validation; no partial query is executed. The existing view is not replaced by a whole-history read or truncated inside a message.

## Execution and audit

Only the effective query is passed to the existing `MemoryRetriever.recall(query, policy)`. The original message, original recent context, Answer prompt, Answer parameters and Hot user turn stay unchanged. The query is never evidence and is never ingested as a new fact. Memory policy, candidates, ranking, scoring and evidence packing remain owned by the existing Memory implementation.

The existing append-only decision log retains `turn_id`, `recall` and `decided_at`. Runtime records also contain `original_message`, `candidate_query`, `effective_query`, `context_refs` (index/role/span), `prompt_version` and `fallback_reason`. Direct legacy `record(decision, turn_id=...)` calls still write the original three-field format; old lines are not rewritten.

A valid proposal takes effect only after its audit append returns successfully. Provider/JSON/schema/source/length failures use `recall=true` with the original query and a logged fallback reason. Logging failure likewise restores the original query and allow decision; a single best-effort fallback append records that outcome if the writer becomes available. A proposal line left by a write-then-raise is not proof that the proposal executed; the failure event and any subsequent fallback row must be considered. If both audit writes fail, Chat remains available with original-query fallback and a log-failure event, without a durable decision guarantee.

A missing logger prevents a new query from taking effect. Legacy boolean-only callers without a logger retain their previous behavior. The gate still runs before the Recall enabled/available/policy guards, and those guards still suppress Recall. There is at most one gate call and at most one Recall call per Chat request; failures do not trigger another query-generation call.

## Evidence standard

Deterministic tests establish the plumbing, source checks, original-message preservation and failure behavior. Promotion additionally requires real contextual decisions and supported answers on frozen independent cases. Forced-allow or scripted-query comparisons are mechanism controls, not autonomous gate results. Ambiguous historical names remain outside this change; incomplete or hallucinated answers must remain visible in the local experimental report.
