# D2 P0 code-review

Fixed point: `9d63da7311baa7611782cc8079fb09e9d81f4e25`; WIP, no new commits.
Tracked diff: `git diff 9d63da7 -- Execution/organ.py Mind/behavioral_experiment.py`.
Also reviewed: new `Mind/event_loop.py`, `Mind/test_event_loop.py`, task document.
Old untracked D1 files/artifacts are preserved baseline, not D2 additions.
Review uses the creator's `/goal` and incremental-reuse instruction as spec;
root/Mind AGENTS and Ponytail as standards. Two independent review agents.

## Standards

Two concrete findings were fixed before preregistration:

- OS stdout could masquerade as the IPython control response. Cell execution
  now redirects OS stdout/stderr to a bounded container temporary file and
  correlates responses with request nonces. Tests cover a JSON-looking os.write,
  subprocess output and the next request. Documentation does not claim hostile
  Python self-introspection is independently attested by kernel output.
- sorted(rglob) could enumerate an unbounded task tree before applying limits.
  Streaming os.scandir now counts both directories and files, rejecting at 256
  entries. Existing 64-file / 100KB-per-file bounds remain.

Directed follow-up confirmed both fixes; no remaining finding. The small
control injection preserves the default production path. No judgement-level
smell justified more abstraction, new dependencies or neighboring refactors.

## Spec

Three concrete findings were fixed before preregistration:

- Gate previously omitted some admission rejections and terminal feedback.
  All eight episodes now have boundary results included in the gate; a test
  proves terminal boundary failure prevents PASS.
- Invalid answer.json could abort the host. It now counts as an objective task
  failure and preserves the raw file, independent of protocol acceptance.
- Feedback was emitted with source=execution while host consumed the outcome.
  It now uses source=host, satisfying Nervous.complete source/causation rules;
  execution-origin evidence is unchanged. The full test accepts both feedbacks.

Directed follow-up confirmed all fixes; no remaining code finding. P0's
deliberately suspended checkpoint is explicitly feasibility-only; subsequent
non-pausing multi-event behavior was conditional on passing the gate.

## Campaign interpretation audit

P0 subsequently failed its frozen gate. The report keeps SPEC, CAUSAL VALIDITY,
MIND/EXECUTION BOUNDARY, CONTEXT CONTAMINATION, AUTHORITY and PONYTAIL separate:
no submitted guidance crossed the experiment admission boundary, no belief was
silently translated to a Directive, and zero delivery cannot establish adoption,
harm rates or behavioral benefit. The stricter adapter length constraint,
nested-JSON grounding failure, checkpoint NameErrors and feedback code excerpt
are reported as limitations, not attributed to independent Mind cognition alone.
No post-result prompt/schema changes or formal campaign were made.

Final independent report/artifact review confirmed 6/8 acceptance, zero delivery,
the 381 > 320 adapter rejection (whole return 888), exact-quote failure and all
frozen source hashes. One causal wording issue was corrected: the control's
budget exhaustion occurred on a trajectory containing NameError and extra
inspection; no counterfactual claim that removing the error guarantees completion.

Closed findings: Standards 2/2; Spec 3/3. Behavioral gate: FAIL; value: INCONCLUSIVE.
