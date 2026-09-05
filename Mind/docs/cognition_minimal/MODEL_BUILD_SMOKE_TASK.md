# Real model construction and revision smoke

Frozen before provider calls, 2026-09-05. This is a small service-state modeling
check, not a new arithmetic benchmark or an Execution-benefit claim.

- Mind uses the explicit v3 computation capability; Memory is absent.
- DeepSeek-V4-Pro through the fixed supported Anthropic endpoint, non-thinking,
  temperature 0, max 2000 output tokens, timeout 30 seconds per call.
- At most two activations / four provider calls. Each activation has at most
  one computation, two model calls, 2000 output characters per call. Stop the
  smoke on its first failed acceptance or missing required model artifact.
- The existing pinned, isolated Docker backend runs generated code; gold later
  observations/outcome labels are never supplied to the compute request.
- Visible observation schema: `version` and `phase`. Actions: `deploy` with a
  version, `probe`, `promote`. A visible no-op probe can change latent state.
- Completion for this task means v2 is active; v1 active is ongoing for this
  target. Promotion before readiness fails. No estimated real-world probability.
- Initial evidence: deploy v2 -> starting, first probe -> starting, second probe
  -> ready, promote -> active. First requested calculation uses deploy, probe,
  promote, testing an early promotion not shown as a completed historical run.
- New evidence for a later deployment: the environment now requires three
  successful probes; after the second it is still starting, after the third
  ready. The next activation must revise the retained model and compute deploy,
  probe, probe, promote. Its failure is checked by the host independently.
- Initial observation and requested action sequences are frozen in the harness.
  An altered model-chosen request is reported as not aligned, not graded against
  a silently changed fixture. Wrong predictions remain falsifications.
- Preserve actual prompts, responses, traces, acceptance journal, request/code
  digests and all verification results in `model_build_smoke_1.json`, alongside
  the exact temporary harness source and hash. Do not overwrite this artifact.

Expected evidence: actual program generation/execution, grounded model retention,
host comparison of initialization/dynamics/outcome, a retained model revised on
new evidence. Both inputs are synthetic observations. This does not establish
general model quality, calibrated causal inference, owner-attested prospective
eligibility, or actual Execution improvement.

## First outcome and formatting-only retry

`model_build_smoke_1.json`: one v3 provider call, 2,282 output characters, rejected
before computation by the unchanged 2,000-character admission bound. The response
included a program, two provisional items and indented JSON. No artifact was
accepted or executed. Do not characterize this as a verified model-quality result.

The next one-shot artifact is `model_build_smoke_2.json`. Only the runtime prompt
changes: v4 explicitly requests compact JSON and defers model-item metadata to the
final step so the compute request can spend its budget on code/inputs. The original
v3 prompt is retained for truthful replay. Provider, output/context bounds, fixtures,
verification rules and the four-call campaign cap remain unchanged.

## Formatting retry and independent falsification

`model_build_smoke_2.json` used four calls with 1346, 505, 1995 and 522 output
characters. Both activations accepted one model; the second retained its item ID.
Generated programs actually ran in the isolated container. The formatting failure
is resolved for this smoke; the following model-quality results are not PASS:

- The first artifact rendered extra latent fields (`ready`, `promoted`) as visible
  observations. It also made readiness immediate after one probe and predicted
  completion for an early promotion that should fail. Host initialization,
  dynamics and outcome comparisons falsified it. Full field coverage did not
  override mismatches; COMPUTED remained distinct from checked correctness.
- The second artifact changed the requested two-probe action sequence to three
  probes. Its program and stable model ID were retained, but request alignment was
  false, so the frozen verifier did not grade it against a different trajectory.
- Both acceptance journals and traces are preserved. Do not rewrite the earlier
  failure, silently alter gold observations, or claim these results demonstrate
  effective Execution supervision. This smoke supplied new environment evidence;
  it did not feed the first verification report into the next activation.

The next root-cause correction should distinguish a host-frozen observation/action
probe from a freely chosen hypothetical simulation. For a frozen probe, Mind
should reference immutable input data and provide the program, rather than
regenerating the supplied action sequence. Counterfactual simulation remains a
separate valid computation use. Feed the independent mismatch report into a new
bounded activation so correction is driven by actual checking, not only by new
narrative evidence. This is pending implementation/validation, not a passed gate.
