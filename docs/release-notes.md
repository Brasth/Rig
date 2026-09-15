# Release Notes

## Native-Child Model Provenance (2026-09-15)

Accepted fix for native-child model provenance on admission and the job board.

- Fresh running native-child admission now requires a selected model and fails before a live job record if selection is missing.
- Selected native children show their model/effort on the job board.
- Parent and historical jobs remain unknown only when actual provenance is unavailable.
- Rollout requires a fully restarted parent CLI/MCP session after update.

## Devin Child Worker

Devin is a child-only worker. It is never a parent and defaults off.

### Opt-in

Enable Devin via routing preferences in `.rig/routing.json`. Default preference order stays Grok, Claude, OpenCode, OMP, Pi, agy, Codex.

### Role pins

Exact selectors only:

- `swe-2-medium` — explore, mini, bulk
- `swe-2-high` — implement
- `swe-2-max` — hard, review

### Catalog confirmation

Live catalog confirmation is JSON-only (`devin models list --format json`). No aliases, SWE-1.x, Fusion, defaults, or role mismatch.

### Child wrapper

The wrapper launches with accept-edits and a temporary job-scoped MCP config, then restores the previous config. One Devin job is permitted per repository.

### Rollout

1. Run `rig update`.
2. Per repo: `rig init`, then `rig doctor`.
3. Fully restart the parent CLI / MCP session before admitting new work.

### Rollback

Disable `workers.devin` and remove Devin preference IDs.

## Cost-aware Smart Routing

Optional cost-aware lane for smart mode. Off by default.

### Opt-in

Schema 2 `.rig/routing.json` may set `execution.direct_parent_low_risk` (boolean, default `false`). Schema 1 remains valid. Example:

```json
{
  "schema_version": 2,
  "execution": {
    "direct_parent_low_risk": true
  }
}
```

### Direct-parent eligibility

When enabled, pick returns native parent writes (`execution_strategy=direct-parent`) only when **all** of these hold:

- smart mode
- role is **mini** or **implement**
- complexity, risk, and uncertainty are all **low**
- live parent is an eligible tracked native parent (codex, grok, opencode, omp, pi, or agy) and not excluded

All other roles, medium/high assessments, excluded parents, and legacy mode keep the existing wrapper / parent-fallback path. Catalog confirmation is not consulted on the direct-parent lane.

### Parent lifecycle

Direct-parent jobs still use the same write path as other parent writes: `rig_job_start`, authenticated `rig_job_finish`, deliberate checks, and parent acceptance of the current snapshot. Execution success alone is not verification.

### Token telemetry and reporting

`rig routing report` groups attempts by policy, tier, profile, actual model, effort, and `execution_strategy`, and counts direct-parent separately from wrapper. Token coverage comes only from observed structured worker usage. Totals and costs are never inferred. Unknown usage is not zero and is excluded from token aggregates.

### Rollout / upgrade

1. Finish or explicitly cancel active work; confirm termination and reconcile held scopes.
2. Run `rig update`.
3. Per repo: `rig init`, then `rig doctor`.
4. Fully restart the parent CLI / MCP session before admitting new work.

Mixed old/new admission writers are unsupported.

### Rollback

- Keep smart mode but disable the lane: set `execution.direct_parent_low_risk` to `false` (or omit it / stay on schema 1).
- Or switch to legacy: `[routing] mode = "legacy"` in harness config.

### Not included

No pricing integration, automatic model switching/ranking, or untracked bypass of the parent write lifecycle.
