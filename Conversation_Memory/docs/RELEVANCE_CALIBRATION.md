# Relevance Threshold Calibration

## Method

The developer command uses a marker-owned sandbox, the existing synthetic
membrane fixture, the unmodified real MAGMA backend, and the local cosine gate:

```powershell
.\Conversation_Memory\.venv\Scripts\python.exe -m scripts.relevance_calibration
.\Conversation_Memory\.venv\Scripts\python.exe -m scripts.relevance_calibration --keep-data
```

The dataset `fixtures/relevance_calibration_v1.json` has 20 relevant and 20
irrelevant queries. Positives cover exact overlap, paraphrase, causality, time,
entity, synonym, short, and long forms. Negatives cover unrelated topics,
absent same-domain facts, wrong attributes for a known entity, wrong causes,
keyword-neighbor events, absent people/times, and a close technical neighbor.

For a relevant query, its observation is the maximum cosine score among the
labelled expected turns. For an irrelevant query, it is the maximum score among
all returned evidence. Thresholds from `-1.00` through `1.00` are scanned in
`0.05` increments. Equality passes the gate.

The recommendation rule is:

```text
positive recall >= 0.90
and false injection rate <= 0.10
then minimize false injection rate,
then maximize F1,
then choose the highest threshold
```

If no point satisfies both constraints, the recommendation is `NONE`.

## Measured result

The real-MAGMA run on 2026-07-14 produced:

| Measure | Result |
|---|---:|
| Positive queries | 20 |
| Negative queries | 20 |
| Positive score range | 0.3866–0.7872 |
| Positive median | 0.6539 |
| Negative score range | 0.0724–0.7591 |
| Negative median | 0.4855 |
| Overlap interval | 0.3866–0.7591 |
| Recommended threshold | NONE |

Representative scan points show the tradeoff:

| Threshold | Positive recall | False injection rate | Empty accuracy | F1 |
|---:|---:|---:|---:|---:|
| 0.40 | 0.95 | 0.65 | 0.35 | 0.7308 |
| 0.45 | 0.85 | 0.60 | 0.40 | 0.6939 |
| 0.60 | 0.65 | 0.25 | 0.75 | 0.6842 |
| 0.65 | 0.50 | 0.10 | 0.90 | 0.6250 |
| 0.70 | 0.30 | 0.05 | 0.95 | 0.4444 |

At the last scan point that preserves at least 0.90 positive recall (`0.40`),
65% of irrelevant queries still inject evidence. At the first point reaching a
10% false injection rate (`0.65`), positive recall falls to 50%. The current
MiniLM cosine signal therefore does not reliably separate this labelled set.
No formal threshold is recommended or installed as a default.

## Performance

Seven warmed real-backend recalls measured the median:

| Measure | Result |
|---|---:|
| Gate disabled | 9.152 ms |
| Gate enabled | 10.003 ms |
| Added median latency | 0.852 ms |
| Query embedding calls per recall | 1 |
| Additional relevance embedding calls | 0 |
| Candidates scored | 4 |
| Model reloads | 0 |
| Additional LLM calls/tokens | 0 / 0 |

These timings describe one local synthetic run, not a general latency promise.
The JSON report records every threshold metric and the measured medians without
queries, vectors, paths, node IDs, credentials, or provider data.

## Result and limitation

Calibration result is `PARTIAL` with limitation code
`threshold_not_recommended`. The optional mechanism is implemented and tested,
but this dataset does not justify enabling a threshold. A future threshold
requires broader labelled data or a separately authorized relevance method;
the present result must not be described as a universal production threshold.
