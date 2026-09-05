# E1 Blinded Primary Review

Reviewed before opening `e1-artifact.json` or the arm-label mapping.

- Blinded artifact SHA-256: `2d1d6584bcfaf9b66a97d2d72b425044688f773c58059d5673c071687bcd75f3`
- Primary metric: `VerifiedCompletion`

| Task | arm-A | arm-B |
| --- | ---: | ---: |
| e1-01-ledger-replay | FAIL | FAIL |
| e1-02-utc-window | FAIL | PASS |
| e1-03-dependency-lock | FAIL | PASS |
| e1-04-event-projection | FAIL | FAIL |
| e1-05-constrained-route | FAIL | PASS |
| e1-06-grid-transform | FAIL | FAIL |

Blinded totals:

- arm-A: 0 / 6
- arm-B: 3 / 6

No baseline/candidate mapping, Mind result, Directive, or trajectory detail was
inspected before this table was persisted.
