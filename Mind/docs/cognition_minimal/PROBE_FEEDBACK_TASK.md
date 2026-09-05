# Frozen probe and independent feedback correction

Authorized minimal continuation of the cognitive loop. Three existing cognition
modules are extended, with the existing cognition tests; no new runtime service.

Root cause from `model_build_smoke_2.json`: a model changed a specifically requested
action sequence, and the harness supplied new narrative evidence without returning
its independent verification report. Its incorrect visible projection survived.

`ModelProbe` holds the host's immutable initial observation/actions and a stable
ref. When present, `evaluate_model` accepts only source plus that ref. The existing
free counterfactual `run_world_model` remains usable without a fixed probe. Both
consume the same single capability slot, in the same two-model-call activation.
`ModelFeedback` holds new owner observations/outcomes, their reality source refs,
and the exact historical artifact ref. Mind recomputes the report from the stored
prediction; reports are not factual quote sources and do not certify general
model correctness. Both new inputs participate in durable event identity. The
older input shape retains its exact canonical identity when the fields are absent.

New prompt/projector v5 specifies the fixed-probe interface and use of feedback.
Earlier prompt versions and failed artifacts remain replayable. Initial selected
regression: 145 passed / 1 explicit Docker skip. The replay test groups requests
by their actual activation identity, not filesystem enumeration order.

## Frozen real correction smoke

- Restore the exact accepted journal and traces from `model_build_smoke_2.json`
  into a fresh temporary store; preserve the seed artifact digest. Do not modify
  the seed or manufacture a correct initial model.
- DeepSeek-V4-Pro on the fixed supported endpoint, non-thinking, temperature 0,
  2000 output tokens, 30 seconds. At most two activations / four provider calls.
  Two model calls and one isolated computation per activation; all existing
  source/input/output/resource bounds remain unchanged.
- First correction receives the original first artifact's independently checked
  failure against its actual action sequence, plus real-source-labeled synthetic
  observations. The current environment still requires three successful probes.
  Fixed probe: deploy v2, probe, probe, promote. Host outcome should be failed.
- Second activation receives feedback on the corrected artifact and a distinct
  fixed probe: deploy v2, three probes, promote. Host outcome should be complete.
- Each model must retain the same model item ID. Compare observable fields,
  initialization, dynamics and task outcomes outside the generated program.
  Report mismatches as such; no thresholds or fixtures are adjusted to pass.
- Preserve calls, traces, final journal, exact harness and digest in
  `model_feedback_smoke_1.json`. Stop this smoke on its first protocol failure,
  missing model, or input alignment failure. Checked mismatches remain evidence.

This is real model construction/revision on synthetic service evidence, not real
Execution benefit, an owner-attested prospective prediction, or an unbiased
generalization benchmark. Those parent-goal obligations remain outstanding.

## Recorded correction result

`model_feedback_smoke_1.json` completed with four provider calls. Both activations
were accepted, advancing the restored revision 2 -> 3 -> 4, preserving the same
model item ID and matching each fixed probe's input exactly. The revised program
predicted failure after two probes and completion after three; host checks for
initialization, every observable transition, and every outcome all matched.
Both accepted runs used the same source digest, so the second probe checks the
same corrected program on a different fixed action sequence. This remains narrow
synthetic evidence, without a prospective or generalization claim.

The original wrong-program artifacts and exact harness source/hash are retained.
The creator subsequently narrowed the remaining work to Execution validation and
requested stopping afterward; see `EXECUTION_VALIDATION.md` for the final scope
and result. Broader roadmap work is deferred by that instruction.
