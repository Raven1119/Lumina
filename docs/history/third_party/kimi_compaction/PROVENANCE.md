# Kimi CLI compaction core

`working_context.py` adapts source from MoonshotAI/kimi-cli at fixed commit
`86f136422a0aae6b217ea49e7ea1d2e8a1defcd2`. This directory retains the complete,
unmodified upstream [LICENSE](LICENSE) (Apache-2.0) and [NOTICE](NOTICE).
The upstream runtime and dependencies are not included or imported.

| Upstream file | Git blob SHA-1 | Adaptation |
| --- | --- | --- |
| [soul/compaction.py](https://github.com/MoonshotAI/kimi-cli/blob/86f136422a0aae6b217ea49e7ea1d2e8a1defcd2/src/kimi_cli/soul/compaction.py) | `177c5edce899ccd31d111aeb7e0409dde64f3a07` | `should_auto_compact` retains the ratio/reserved-space formula. `SimpleCompaction.prepare/compact` supplies the prefix/tail split, empty-prefix no-op, single no-tool summary and unchanged recent tail. |
| [prompts/compact.md](https://github.com/MoonshotAI/kimi-cli/blob/86f136422a0aae6b217ea49e7ea1d2e8a1defcd2/src/kimi_cli/prompts/compact.md) | `ddf071d5c573ee9930bb508358b3ae4c13e853a7` | Adapted to concise owner-local historical context; still-relevant failed conditions remain, rather than removing failed attempts wholesale. |
| [test_simple_compaction.py](https://github.com/MoonshotAI/kimi-cli/blob/86f136422a0aae6b217ea49e7ea1d2e8a1defcd2/tests/core/test_simple_compaction.py) | `b2fa80dbb342b1e8b10eb542525cefef837c61bd` | Threshold examples, short/empty prefix and unchanged-tail cases are adapted in `Execution/test_working_context.py`. |
| [LICENSE](https://github.com/MoonshotAI/kimi-cli/blob/86f136422a0aae6b217ea49e7ea1d2e8a1defcd2/LICENSE) | `7a4a3ea2424c09fbe48d455aed1eaa94d9124835` | Unmodified copy. |
| [NOTICE](https://github.com/MoonshotAI/kimi-cli/blob/86f136422a0aae6b217ea49e7ea1d2e8a1defcd2/NOTICE) | `53154614344bfa7439fc30dc0e48d8338cda57e0` | Unmodified copy, including the upstream third-party acknowledgment. |

All five files were retrieved from the pinned official GitHub source on
2026-09-10 and their Git blob hashes verified before adaptation.

Lumina changes the following interfaces and behavior:

- The owner passes complete immutable JSON rounds. A sibling batch and its
  results stay together; native thinking/signatures survive in the unchanged
  tail and serialized historical input. There is no text-only filtering or
  user/assistant-message counting.
- Whole serialized request bytes provide a conservative input-token estimate,
  with a separate output reservation and the existing provider byte/total
  budgets. It is an estimate, not measured usage; no `chars / 4` conversion.
- A high-water batch threshold falls back to the retained tail. A forced
  compaction still needs a nonempty complete prefix; capacity failure never
  silently cuts a round or loops through repeated summaries at one boundary.
- `ProviderCalls` owns dispatch and cost under the original organ role with
  `purpose=compaction`. No Kimi/Kosong runtime, `Compaction` protocol, extra
  reviewer, or business tools are introduced.
- An owner-local atomic JSON projection freezes scope, prior summary revision,
  source prefix and original request before dispatch. Received responses are
  claimed once after restart; unknown outcomes stop without replay. Invalid
  summaries retain the old accepted projection and original failed response.
- The model returns bounded JSON containing summary text and original source
  references. Code checks shape, length and source range, not semantic truth.
  Current accepted cognition and Execution state remain authoritative and are
  supplied separately by their owners.

The additional crash, repeated-compaction, scope, integrity and budget tests
are Lumina-specific. Scripted summaries test these mechanics, not semantic
quality or a benefit over observation masking.

## Lumina protocol and recovery differences

New compactions use `working-context-v4`. An explicit citable-reference directory
comes from exactly the covered original history, including validated typed Mind
read_evidence receipts. Arbitrary source prose and future receipts are excluded.
The model's cited refs, unchanged summary, covered segment refs and source digest
remain distinct. V4 accepts a complete JSON document with an optional exact outer
JSON code fence; structural/source validation does not establish semantic truth.

The 6000-character value is a drafting target for v4. The existing 8192-token
provider output allocation, complete request/context capacity and cumulative task
budgets remain hard bounds. Old v1-v3 pending responses retain their original
citation, envelope and hard character-limit interpretation. Protocol versions
share the owner-local atomic persistence envelope; no old failure is relabelled.

Normal owner projections retain six complete segments. Under whole-request
pressure they can retain one, permitting short-history compaction without
splitting native rounds. An explicit rejected-summary retry archives the failure,
keeps the same covered prefix and base revision, and freezes a new operation. The
actual received text precedes correction feedback; it is never rewritten by the
host. Unknown dispatches stop, while valid saved responses use ordinary recovery.

These adapters are Lumina changes, not Kimi defaults. The bounded live outcomes
and retained failures are summarized in [validation history](../../Mind/docs/EXPERIMENT_HISTORY.md).
Raw requests, per-call reports and local recovery copies are not vendored.
