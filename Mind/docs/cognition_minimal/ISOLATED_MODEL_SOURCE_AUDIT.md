# Isolated world-model computation — P3 source audit

Status: component validation passed; no Mind capability is exposed yet.
The independent component did not choose the cognitive protocol branch. The
creator subsequently selected unified steps for the separate cognition protocol.

## Frozen source and adaptation

SOURCE: https://github.com/NIMI-research/Tycho

VERSION / COMMIT: `f68912a764372ead0a610db2e1c011d41ce5197e`.

LICENSE: Apache-2.0, copyright 2026 Jens Lehmann, Andrei Aioanei, and
Sahar Vahdati; see [the copied license](TYCHO_LICENSE.txt). No root NOTICE
file was present at this commit. Modified portions carry attribution.

| Source symbol | Original input/output and rule | Lumina adaptation |
| --- | --- | --- |
| `tycho/workspace/sandbox.py:PythonSandbox.command` | Workspace script to Docker/Finch command; read-only root, no network, dropped capabilities, resource limits; workspace writable | Copy Docker restriction flags. Use existing immutable Python image, `--pull never`, non-root UID, no bind mounts, input through stdin. No host/Finch fallback. |
| `PythonSandbox.run_script` and nested `drain` | Run a workspace script, continuously drain bounded stdout/stderr, remove named container on timeout | Port bounded binary draining and timeout removal. Treat truncation, cleanup failure and malformed output as errors. Remove owned container on interrupted execution too; never expose raw stderr to Mind. |
| `wmlib_template.py:clone`, `verify` | Deep-copy states; initialize once, thread predicted state through all actions, compare renders to observations outside transitions | Reuse deepcopy behavior and threaded rollout rule. JSON observations replace pixel grids; no future observations or outcome labels enter the container. Initialization and every action are reported separately, including joint no-ops. |
| `_cell_stats`, `_coverage_status` | Unknown cells excluded from known-cell accuracy and separately counted in coverage; vacuous is not a correct model | Host-owned field comparison, JSON null denotes unknown. Missing/extra fields are mismatches. Unknowns never count as correct; full coverage required for a match. |
| `outcome`, `verify_outcome` | Strict ongoing/level_complete/game_over status, checked against observed terminal/nonterminal events separately from render quality | Use ongoing/complete/failed/unknown, with separate host-supplied labels. No observed outcome means unverified; no self-certification or budget-as-completion. |

The ARC grid loaders, NumPy, planning/search, observation variants, agents,
runtime discovery, image building, and writable workspaces are not copied.
The JSON bridge and domain-neutral verification DTO are Lumina adaptations,
not claims of byte-for-byte Tycho reproduction or MetaWorld source reuse.

## Frozen first validation

One new production module (`Mind/world_model.py`) and one test file; documentation
and this license are supporting artifacts. Existing runtime protocols remain
unchanged in this subtask.

- Installed image: `python@sha256:3b3706a90cb23f04fabb0d255824f9a70ceb46177041898133dd5a35f3a50f0a`.
- Python stdlib in an isolated container; no host interpreter fallback, network,
  bind mounts, credentials, Memory/Execution handles, or installed host packages.
- CPU: 1; memory plus swap cap: 256 MiB; processes: 32; read-only root;
  ephemeral `/tmp`: 32 MiB; non-root UID/GID 65534; no added capabilities.
- Model source <= 16,000 characters; request <= 64 KiB; <= 16 actions;
  <= 16 named scalar fields per observation/action; strings <= 256 characters.
  This first bridge is for small local task models, not an arbitrary simulator.
- Per invocation <= 10 seconds plus bounded cleanup. Stdout <= 64 KiB and stderr
  <= 8 KiB, continuously drained. No provider calls in this validation.
- Deterministic stateful service model: deployment, readiness checks, promotion;
  latent readiness counter must survive visually unchanged observations.
- Report initialization, dynamics, outcome, field coverage and first divergence
  separately. Changing later gold labels must leave actual compute input unchanged.
- Actual container checks: no host files/credentials/network, root/workspace
  writes rejected, non-root/capabilities/no-new-privileges/cgroup limits observed;
  output flood and hung child cleaned up; process errors remain computation errors.
- This validates computation and checking only. Real-model construction, model
  revision, prospective prediction registration and Execution benefit remain
  required by the parent task before it is complete.

Any failed validation is preserved and reported for the creator's choice before
altering the frozen mechanism or retrying a failed real-model experiment.

## Component evidence

`Mind/test_world_model.py`: 3 passed / 4 explicitly skipped without Docker;
7 passed in 17.63 seconds with `LUMINA_TEST_WORLD_MODEL_DOCKER=1` on the pinned
image. Real Docker checks covered retained latent state across a visible no-op,
an independently falsified premature-readiness model, UID/capabilities/cgroups,
host-file/env/network isolation, read-only paths, output overflow, memory kill,
and timeout cleanup of both the model process and a spawned child. No real-model
or real-Execution benefit claim follows from these component tests.
