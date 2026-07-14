# MAGMA Write and Recall Call Chains

This document describes verified source behavior at commit
`467cb70b67ac337b22fdb42194d37c04ad701b62`. Paths are relative to the upstream
MAGMA root. “Four graphs” below means four logical edge views in one physical
`NetworkXGraphDB`.

## Write chain

| Stage | File and symbol | Input → output | Caller / callee | LLM | Embedding | Disk | Failure behavior |
|---|---|---|---|---:|---:|---:|---|
| Dataset load | `load_dataset.py:97 load_locomo_dataset` | JSON path → `list[LoCoMoSample]` | baseline/evaluation entry → `parse_conversation`, `parse_session` | No | No | Read | Missing/invalid input raises after diagnostic print |
| Session/turn parse | `load_dataset.py:59 parse_session`; `:79 parse_conversation` | dict/list → `Session`, `Turn`, `Conversation` | loader → dataclass constructors | No | No | No | Missing required keys raise |
| Build orchestration | `memory/memory_builder.py:947 MemoryBuilder.build_memory` | `LoCoMoSample` → statistics dict | evaluation/wrapper → methods below | Optional | Yes | No | Per-event add exceptions are logged and loop continues |
| Session time | `memory/temporal_parser.py:45 TemporalParser.parse_session_timestamp` | date string → naive `datetime` | `build_memory:984` | No | No | No | Invalid/missing input falls back to `datetime.now()` |
| Turn event extraction | `memory/memory_builder.py:135 MemoryBuilder.extract_event` | `Turn`, session id, base time, neighbors → `{content, metadata, timestamp}` | `build_memory:992` → LLM controller or regex helper | Optional OpenAI | No | No | LLM retries three times, then regex fallback; with no controller entities remain empty |
| Relative time | `memory/temporal_parser.py:90 extract_temporal_reference`; `:206 extract_all_dates` | text + base `datetime` → normalized `datetime`/pairs | `extract_event:268-275` only for LLM-returned `dates_mentioned` | No | No | No | No match returns `None`; raw no-LLM turn text is not scanned |
| Event write | `memory/trg_memory.py:152 TemporalResonanceGraphMemory.add_event` | interaction text, timestamp, metadata → UUID node id | `build_memory:995` → `_extract_event`, encoder, graph/vector DB | Optional | Yes | No | LLM extraction catches and uses `_simple_extract_event`; embedding/DB errors propagate |
| Basic fallback extraction | `memory/trg_memory.py:368 _simple_extract_event` | text → `EventExtractionResult` | `add_event` → regex/capitalization heuristics | No | No | No | Truncates narrative to 500 chars |
| Embedding | `memory/vector_db.py:533 VectorEncoder`; `:570 encode` | text(s) → float32 NumPy array | `add_event`, builder indexing, query paths → SentenceTransformer/OpenAI encoder | No | Yes | Model cache | Missing `.openai_encoder` logs and falls back to MiniLM; missing local model requires HF download |
| Node/vector insertion | `memory/graph_db.py:368 NetworkXGraphDB`; `memory/vector_db.py:133 FAISSVectorDB` | `EventNode` / vector tuple → IDs/count | `add_event` → `add_node`, `add_vector` | No | Already made | In-memory | Shape/duplicate/backend errors generally propagate |
| Temporal view | `memory/memory_builder.py:539 create_temporal_links`; `:765 create_temporal_proximity_links` | node IDs → edge counts | `batch_create_links` → `NetworkXGraphDB.add_link` | No | No | No | Missing nodes skipped |
| Semantic view | `memory/memory_builder.py:623 create_semantic_links` | node IDs → edge count | `batch_create_links` → encoder/vector search/add link | No | Yes | No | Missing target nodes skipped; encoder failures propagate |
| Entity view | `memory/memory_builder.py:722 create_entity_links` | node IDs → edge count | `batch_create_links` → metadata grouping/add link | No | No | No | Empty entity metadata produces zero; represented as `SEMANTIC/SAME_ENTITY`, not a separate graph |
| Causal view | `memory/memory_builder.py:670 create_causal_links`; `:802 detect_qa_links` | ordered node IDs → edge counts | `batch_create_links` → speaker adjacency/QA rules | No for these rules | No | No | Missing speakers/nodes skipped; these links encode dialogue flow rather than extracted real-world causation |
| Four-view batch | `memory/memory_builder.py:908 batch_create_links` | event IDs → per-view counts | `build_memory:1050` → all link builders | No except upstream extraction already done | Yes for semantic | No | Individual method exceptions are not centrally caught |
| Graph persistence | `memory/memory_builder.py:1079 save`; `memory/graph_db.py:666 export_to_json` | graph objects → `graph.json` | wrapper/evaluation → graph DB | No | No | Yes | Filesystem/serialization errors propagate |
| Vector/index persistence | `memory/memory_builder.py:1079 save`; `memory/vector_db.py:340 FAISSVectorDB.save` | FAISS index and entries → `index.faiss`, `metadata.json`, `keyword_index.json` | builder → vector DB/JSON | No | Yes | Yes | Missing path/filesystem errors propagate |

`MemoryBuilder` temporarily disables `TemporalResonanceGraphMemory`'s immediate
temporal/semantic linking (`build_memory:974-977`), creates all events, restores
those methods (`:1046-1047`), then builds relations in batch. Node identifiers
are UUID4 and are therefore not stable across rebuilds.

## Physical graph finding

`memory/graph_db.py:26 LinkType` declares `TEMPORAL`, `SEMANTIC`, `CAUSAL`, and
`ENTITY`. `NetworkXGraphDB.__init__` owns one NetworkX multi-graph plus unified
`nodes` and `links` dictionaries. Persistence writes one `graph.json`.
Furthermore, `MemoryBuilder.create_entity_links` writes entity co-mentions as
`LinkType.SEMANTIC` with subtype `SAME_ENTITY`, while
`TemporalResonanceGraphMemory._create_entity_edges` can write `LinkType.ENTITY`.
There are therefore multiple inconsistent entity representations, not four
physically isolated graph stores.

## Recall chain

| Stage | File and symbol | Input → output | Implementation and limits | Failure/no-result behavior |
|---|---|---|---|---|
| Entry | `memory/query_engine.py:682 QueryEngine.query` | question + `top_k` → (`QueryContext`, rendered string) | Orchestrates all stages; final nodes trimmed to `top_k` | Empty retrieval produces empty node list/context |
| Classification/routing | `query_engine.py:247 detect_query_type`; `:367 get_adaptive_params` | question → rule label/config | Regex/keyword rules, not LLM; labels include temporal, causal, entity, activity, multi-hop; depths 4–12 depending on type | Defaults to `general` |
| Temporal query constraints | `query_engine.py:472 extract_date_from_question`; `:504 find_nodes_by_date_range`; `:541 resolve_relative_temporal_reference` | question/date → dates/nodes | Rule/date parsing helpers; the main `query` primarily routes by query type | Parsing miss returns no date/match |
| Embedding anchors | `memory/trg_memory.py:234 query` → `VectorEncoder.encode`, `FAISSVectorDB.search` | enriched question → anchor nodes | `max_results` is 15 temporal, 30 multi-hop, otherwise 20 in `QueryEngine`; FAISS provides entry ranking | Encoder errors propagate; empty index returns no anchors |
| Keyword candidates | `query_engine.py:878 _keyword_search` | question → ranked nodes | Keyword index and enriched query; sliced to 15/30/20 | Empty list |
| Scan candidates | `query_engine.py:970 _scan_all_nodes` | question → ranked nodes | Scans every graph node; sliced to 20/40/25 | Empty list |
| Rank fusion | `query_engine.py:55 _rrf_fusion` | ranked lists → `(node, score)` list | Reciprocal rank fusion with `k=60`; sequential, not four parallel graph searches | No ranked lists yields warning and empty candidates |
| Graph selection | `query_engine.py:367 get_adaptive_params`; `:805 _adaptive_graph_traversal` | query type/candidates → traversed nodes | Chooses preferred edge types in the one graph; temporal/causal/entity queries do not select separate graph DBs | Falls back to available candidates |
| Traversal | `query_engine.py:1099 _adaptive_graph_traversal` | anchors/query/budgets → scored nodes | Adaptive BFS; type depth 4–12, query passes `max_nodes=800`; per-node neighbor limit 8/10 and encoding cap 400 | Similarity/drop filters prune; empty queue returns empty |
| Alternate traversal | `query_engine.py:1002 _probabilistic_beam_search`; `graph_db.py:512 traverse` | anchors/constraints → nodes/paths | Beam width 10, visited budget 50 for probabilistic helper; graph DB BFS defaults are supplied by caller. The main query uses adaptive BFS after initial `trg.query` BFS | Bounded empty result; `find_path` returns `None` for no path |
| Reranking | `query_engine.py:1282 _rerank_and_filter`; `:1632 _retrieve_multi_hop_evidence` | candidates/question → top nodes | Heuristic entity, temporal, phrase, keyword, and vector signals; embedding is used both at entry and during traversal/reranking | Empty candidates → empty list |
| Evidence expansion | `query_engine.py:586 _expand_qa_context`; `:638 _expand_session_context` | top nodes → event/session nodes | Adds QA/session neighbors, then trims again to `top_k` | Missing links simply add nothing |
| Context generation | `memory/answer_formatter.py:821 format_context_for_qa` | nodes/question/sessions → text | Renders ranked node narratives, timestamps, speaker, and often `original_text`; not graph paths or a summary-only view | Empty nodes yield formatter fallback text |
| Answer formatting | `answer_formatter.py:531 build_qa_prompt`; `:53 extract_answer` | context/question/LLM response → final answer | Evaluation harness may call OpenAI; baseline stops at returned context | No-key final LLM answer path unavailable; parser has malformed-JSON and not-found heuristics |

## Answers to required recall questions

1. Routing is rule-based in `detect_query_type`; it is not LLM or hybrid.
2. A query selects preferred relation types inside one graph, not one of four
   physical graphs.
3. Four graph stores are not searched in parallel. Vector, keyword, and node
   scan retrieval are executed sequentially and fused.
4. The active path uses vector-anchor BFS plus adaptive similarity-filtered BFS;
   a probabilistic beam-search helper also exists.
5. Depth is query-type-specific (4–12). Initial candidates are 15–40 per
   retrieval source, traversal passes max 800 nodes, and final output is
   `top_k`; lower graph APIs have their own default budgets.
6. Embeddings support entry search, traversal similarity, semantic edge
   construction, and a reranking signal; they are not entry-only.
7. Context is ranked node narrative/original text with timestamp and speaker,
   not traversal paths. Session summaries can also be included.
8. Some upstream provenance survives (`session_id`, `dia_id`, timestamp,
   speaker, original text), but no immutable source-segment provenance contract
   exists.
9. Node count is bounded, but no token or character maximum is enforced on the
   rendered context.
10. No results generally return empty lists/context and warnings. Many low-level
    exceptions propagate; LLM extraction catches exceptions and falls back,
    while import-time missing credentials do not fall back.
