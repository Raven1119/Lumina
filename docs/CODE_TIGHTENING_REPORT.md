# Memory Code Tightening Report

## Measurement scope

Counts use physical lines and tracked workspace files, excluding the pinned
`Conversation_Memory/upstream/MAGMA`, `.venv`, caches, and generated data.
Production is `core/`, non-test `Dream/`, and non-test/non-script
`Conversation_Memory/`. Developer scripts, tests, and Markdown documentation
are reported separately. Public exports are explicit `__all__` entries.

## Before

| Area | Files | Lines |
| --- | ---: | ---: |
| `core/` production | 11 | 1,113 |
| `Dream/` production | 5 | 559 |
| `Conversation_Memory/` production | 13 | 1,576 |
| Production total | 29 | 3,248 |
| Developer scripts | 5 | 1,853 |
| Tests | 16 | 3,174 |
| Documentation | 15 | 2,194 |

- Explicit public exports: 25.
- Protocols / DTO classes: 10 / 21.
- Completely uncalled audited functions: 2
  (`_extract_weekday_reference`, `_extract_absolute_date`).
- Recent DTOs without a required production consumer: 3
  (`TimeConstraints`, `ScoredRecallCandidate`, `RelevanceScoreResult`).
- Baseline tests: Conversation Memory 143, Dream 32, isolated root 107,
  ordinary root 83 passed/24 skipped, Recall E2E 6/6 PASS.

## Caller audit

| Symbol or group | Classification | Evidence and decision |
| --- | --- | --- |
| `MemoryIngestor`, `MemoryRetriever`, `MemoryBackend` | A | Active Dream/adapter/E2E boundaries; retained. |
| Core `RecallPolicy` bounds | A | Used by backend, facade, and E2E; retained. |
| `normalize_temporal_references` | A | Called for every new MAGMA event; retained as the sole temporal entry. |
| `RelevanceScorer`, cosine scorer, scored DTOs | B | Only the rejected optional gate and its tests/calibration consumed them; removed. |
| `min_relevance`, relevance candidate bound | B | No API/config selected a threshold; removed with the backend branch. |
| `TimeConstraints`, question/constraint methods | B | Test-only and absent from Recall, Dream, and API; removed. |
| Public `TemporalParser` compatibility methods | B | Test/benchmark-only; parser made private and reduced to ingestion parsing. |
| One-off Chinese benchmark | B | Historical number retained in docs; script removed. |
| Dedicated weekday/absolute single extractors | C | Definitions had no caller; removed. |

## Removed and consolidated

- Removed encoder monkey patching, query/evidence vector capture, cosine
  scoring, relevance error codes, score fields, experimental policy fields,
  calibration CLI/fixture/tests, and the gate-specific E2E branch.
- Removed query-side temporal DTOs/methods, unused compatibility helpers,
  duplicate `temporal_references` metadata, and unused package re-exports.
- Removed the one-off benchmark and standalone Chinese real-MAGMA E2E.
- Folded three Chinese evidence queries and temporal metadata checks into the
  single authoritative Recall E2E.
- Reduced synonym enumeration while retaining relative, week/month/year,
  directed weekday, absolute date, longest match, multiple mention, midnight,
  DST, leap-year, invalid input, event metadata, restart, idempotency, and
  legacy fallback coverage.
- Consolidated two relevance documents into one short historical result and
  removed stale current-capability descriptions elsewhere.

## After

| Area | Files | Lines |
| --- | ---: | ---: |
| `core/` production | 11 | 1,113 |
| `Dream/` production | 5 | 555 |
| `Conversation_Memory/` production | 12 | 1,153 |
| Production total | 28 | 2,821 |
| Developer scripts | 3 | 1,082 |
| Tests | 13 | 2,333 |
| Documentation | 15 | 2,026 |

- Explicit public exports: 0.
- Protocols / DTO classes: 9 / 18.
- Completely uncalled audited functions: 0.
- Audited DTOs without a required production consumer: 0.

## Net change

- Production: -1 file, -427 lines.
- Developer scripts: -2 files, -771 lines.
- Tests: -3 files, -841 lines.
- Documentation: 0 files, -168 lines.
- Measured code/document files: -6; including the removed calibration fixture,
  permanent files overall: -7.
- Explicit public exports: -25; Protocols: -1; DTOs: -3.

## Preserved behavior

The tested chain remains Hot Draft -> Cold-first compaction -> Cold Draft ->
manual Dream -> durable unmodified MAGMA -> bounded Recall. V2 per-turn
provenance, legacy fallback, original text, English/Chinese write-time temporal
normalization, IANA/DST calendar behavior, durable-before-consumed ordering,
safe Recall failure, provenance projection, output bounds, restart, and
idempotency remain covered. `/api/chat` still has no Recall or Dream wiring.

## Final validation

Conversation Memory passed 48 tests, Dream passed 32, the isolated root suite
passed 90, and ordinary Python passed 68 with 22 isolated-environment skips.
The consolidated Recall E2E passed 9/9 queries plus restart and idempotency.
`git diff --check` passed and both upstream MAGMA status/diff outputs were empty.
