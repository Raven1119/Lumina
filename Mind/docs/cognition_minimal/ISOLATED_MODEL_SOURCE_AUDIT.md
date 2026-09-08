# Isolated computation: source and adaptation

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

Current integration: [INTEGRATED_CHAIN](../INTEGRATED_CHAIN.md).
Historical outcomes: [EXPERIMENT_HISTORY](../EXPERIMENT_HISTORY.md).
