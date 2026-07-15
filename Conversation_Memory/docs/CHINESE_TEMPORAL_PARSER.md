# Chinese Temporal Normalization

## Scope and ownership

Lumina ingestion uses one private deterministic parser for required English and
Chinese memory-write normalization. Its internal order mirrors upstream
MAGMA's MIT-licensed `TemporalParser`:

```text
relative patterns -> directed weekdays -> absolute dates
-> longest non-overlapping spans -> source order
```

The implementation is Lumina-owned. Upstream MAGMA remains unchanged. The only
cross-module entry point is
`ingestion.temporal.normalize_temporal_references(ColdDraftTurn)`; MAGMA-style
single-reference, question-detection, query-constraint, duration, and date-
formatting APIs were removed because no production caller used them.

## Persisted metadata

Each retained expression becomes the existing
`NormalizedTemporalReference` shape:

```text
original_expression
reference_timestamp
reference_timezone
normalized_start
normalized_end
normalization_method
normalization_confidence
language
```

The adapter writes canonical `temporal_mentions` and the MAGMA-style
`dates_mentioned` projection. The removed `temporal_references` alias had no
runtime consumer. Mentioned dates never replace the original text or the MAGMA
event timestamp.

## Supported Chinese expressions

- days: `今天/今日`, `昨天/昨日`, `明天/明日`, `前天`, `后天`;
- weeks: current/previous/next forms using `周` or `星期`;
- months: `本月/这个月`, `上月/上个月`, `下月/下个月`;
- years: `今年/本年`, `去年/上一年`, `明年/下一年`;
- directed weekdays: previous/next `周/星期/个星期` plus Monday through
  Sunday;
- absolute dates: `YYYY年M月D日/号`, `YYYY年M月`, `YYYY年`,
  `YYYY-MM-DD`, and `YYYY/MM/DD`;
- multiple Chinese/English mentions in one turn.

A standalone weekday is intentionally ambiguous and is not parsed. Chinese
directed weekdays use the named day in the previous or next calendar week, not
nearest-weekday arithmetic.

## Calendar and timezone semantics

The reference is each V2 turn's aware `created_at` plus its validated IANA
`source_timezone`. Day, Monday-bounded week, real month, and real year
boundaries are calculated on that local calendar and stored as aware UTC
half-open `[start, end)` intervals. This preserves cross-midnight, leap-year,
and DST behavior; a New York spring-forward local day can span 23 elapsed UTC
hours. Legacy fixed-offset source timezones remain supported.

Naive references and invalid timezones fail explicitly. Invalid or unsupported
calendar expressions do not produce nested fallback dates and never rewrite
the source.

## Validation and limits

The authoritative Recall E2E now includes the Chinese `昨天`, `上周一`, and
`下周一` cases and three Chinese evidence queries in the same Cold/Dream/MAGMA/
Recall/restart/idempotency sandbox as English Recall. Focused unit tests retain
relative units, directed weekday, absolute date, longest match, multiple
mentions, midnight, DST, leap-year, invalid input, and legacy boundaries.

A historical local run measured roughly 141 microseconds per parser call on a
small synthetic sample; the one-off benchmark script was removed. The parser
performs no network, LLM, embedding, model initialization, or graph scan.

Chinese-numeral dates, dates without a year, lunar dates, holidays, vague
phrases, time-of-day expressions, and durations remain unsupported.
