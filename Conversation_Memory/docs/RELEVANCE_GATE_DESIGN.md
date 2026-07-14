# Local Relevance Gate Design

## Scope

The relevance gate is an optional Lumina-owned filter after MAGMA retrieval. It
does not change MAGMA query routing, vector search, traversal, scores, or
ordering, and it is not connected to `/api/chat`.

```text
MAGMA bounded candidate retrieval
-> private Lumina backend candidates
-> local cosine scoring
-> min_relevance filter
-> top_k / max_evidence_items
-> max_chars
-> MemoryContext
```

`RecallPolicy.min_relevance` defaults to `None`, which bypasses relevance
scoring and preserves the previous recall result behavior. No threshold is a
production default.

## Verified embedding path

`TemporalResonanceGraphMemory.query` enriches the query and calls the already
loaded `VectorEncoder` once before FAISS search. `add_event` encoded each event
once and stored the resulting vector in both the event node and the persistent
vector database. `FAISSVectorDB.metadata.json` restores `VectorEntry.vector` on
restart.

The private `RealMagmaBackend` therefore uses the highest-priority reuse path:

1. temporarily observes the existing encoder call made by `trg.query` and
   retains that query vector only for the current bounded result;
2. obtains each returned node's existing vector through
   `vector_db.get_vector(node_id)`;
3. passes numeric tuples only through private `BackendCandidate` fields;
4. restores the encoder method immediately after the query.

The scorer does not encode query or evidence text. It creates no encoder or
model instance, makes no network request, and calls no LLM. The query still has
the one embedding call MAGMA already required. The gate adds zero embedding
calls. Private vectors never enter `MemoryEvidence`, `MemoryContext`, reports,
or public adapter exports.

## Lumina-owned interfaces

`RelevanceScorer.score(query, candidates) -> RelevanceScoreResult` is a
structural protocol. `CosineEmbeddingRelevanceScorer` consumes a bounded
sequence of private backend candidates and returns `ScoredRecallCandidate`
values in the same order.

Cosine scores are finite floats clamped to `[-1.0, 1.0]`:

- equal vectors score `1.0`;
- orthogonal vectors score `0.0`;
- opposite vectors score `-1.0`;
- a zero vector scores `0.0`;
- a missing, dimension-mismatched, NaN, or infinite vector returns a structured
  scorer failure rather than allowing the requested gate to be bypassed;
- a missing query vector also returns a structured scorer failure.

The gate includes equality: an item is retained when
`relevance_score >= min_relevance`. It filters only and never reorders the
remaining candidates.

## RecallPolicy

The added fields are:

```python
min_relevance: float | None = None
max_relevance_candidates: int = 20
```

`min_relevance` accepts finite values in `[-1.0, 1.0]`. Boolean, NaN, infinity,
and out-of-range values are rejected. `max_relevance_candidates` accepts 1–20.
The real backend requests at most this many candidates when the gate is
enabled, and the adapter slices again defensively before scoring. With the gate
disabled, the backend keeps the prior `top_k` request size.

## Ordering and public DTOs

The adapter retains its existing deterministic candidate order: descending
private backend score, source timestamp, then stable evidence ID. Cosine does
not participate in sorting. Backend score and both vectors remain private.

`MemoryEvidence` contains an optional `relevance_score`; it no longer exposes
the backend retrieval score. Character truncation reconstructs the DTO without
losing the relevance score.

## Failure and empty-result behavior

- Gate disabled: scorer availability is irrelevant and the old path runs.
- Gate enabled and no backend candidates: return a valid empty context without
  an error.
- Gate enabled and all scores below threshold: return a valid empty context
  without an error.
- Gate enabled and scorer/vector input unavailable, malformed, reordered, or
  raises: return an empty context with `relevance_unavailable`; never bypass
  the requested gate.
- Backend recall failure remains `recall_unavailable`.

Raw exceptions, paths, credentials, vectors, MAGMA UUIDs, FAISS objects, and
NetworkX objects are discarded at the facade boundary.

## Non-features

There is no LLM judge, cross-encoder, network fallback, independent embedding
model, global graph scan, production threshold, chat injection, Dream change,
Cold Draft change, or upstream MAGMA patch.
