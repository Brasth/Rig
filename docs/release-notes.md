# Release Notes

## Grok 4.7 CLI pin (2026-09-21)

The Grok CLI strong pin is `grok-4.7` at effort `high` on profile `grok-4.7-high`. That covers implement, hard, verify, and review. Explore, mini, and bulk stay on `grok-4.5` low (`grok-4.5-low`).

OMP, Pi, and Cursor keep their current Grok 4.6 selectors. Pi and the local OMP catalog do not list `grok-4.7`. `grok-4.7-build-fast` stays off the cheap rung: it is the same model at twice the token rate.

A `.rig/routing.json` preference that names `grok-4.6-high` no longer matches a built-in profile. Point it at `grok-4.7-high`.

### Rollout / restart

Install the checkout into `~/.rig`, then fully restart parent and MCP sessions before the next admission. Mixed-version admission writers are unsupported.

### Rollback

Restore the Grok CLI rows in `scripts/route.py` to `grok-4.6` / `high` and the profile id `grok-4.6-high`.

## Live TUI and Codex Explorer (2026-09-16)

Accepted release for live TUI activity, Codex JSON streaming, and explorer defaults.

Rig TUI now shows normalized live Pi and Codex assistant replies, concise tool status, errors, and final output. Reasoning, raw arguments, and raw tool output remain hidden. Raw logs remain available.

The Codex worker uses JSON streaming.

The shipped explorer default is `gpt-5.6-luna` on profile `codex-explorer-low`. Unsupported `gpt-5.3-codex-mini` is no longer the shipped explorer selector.

`gpt-5.3-codex-spark` is an opt-in `codex-explorer-low` selector override. It is explore-only and cannot be assigned to write roles:

```json
{
  "schema_version": 1,
  "profiles": {
    "codex-explorer-low": { "selector": "gpt-5.3-codex-spark" }
  }
}
```

### Rollout / restart

Finish or cancel existing work, confirm stopped, accept or close scopes, then fully restart all parent/MCP sessions before admitting new work. Mixed-version admission writers are unsupported.

Spark smoke-test: after the override, explore-only routing may select Spark; write roles must still refuse it. Do not make Spark a baseline.

### Rollback

Remove the `codex-explorer-low` Spark selector override to restore shipped `gpt-5.6-luna`.

## Adaptive Workflows (combined wait-cancel rollout)

Parent-owned DAG orchestration is locally parent-verified. Documented contracts:

- Durable `.rig/workflows/<id>/` holds `spec.json`, `state.json`, `events/`, and `owner-credentials.json` (mode 0600).
- `[orchestration]` mode is `adaptive` (default) or `single`; `max_nodes` is 12. Queue and worker caps remain authoritative.
- Parent MCP: `rig_workflow_create`, `rig_workflows`, `rig_workflow_show`, `rig_workflow_advance`, `rig_workflow_wait`, `rig_workflow_extend`, `rig_workflow_resolve`, `rig_workflow_approve`, `rig_workflow_cancel`, `rig_workflow_report`, `rig_job_coordination_reply`. Child after handshake may `rig_job_coordination_request`. CLI: `rig workflows`; `rig workflow create|show|advance|wait|extend|resolve|approve|cancel|report`.
- `verify` is parent/final integration; `review` is independent post-write review. Independent review unavailable stays explicit. Children never spawn or message children. Parent owns the graph, briefs, and acceptance and uses workflow advance/wait.
- Review+seed and parallel writers require file AND resource disjointness. Overlapping writer scopes are rejected, not sequenced.
- UI fields: workflow id, status, accepted/required, running, ASK, blocker, next parent action, title. No estimated progress, savings, or ETA.

### Combined rollout with wait-cancel

Stop new admissions, finish or cancel existing work, confirm stopped, accept or close scopes, preserve data (queue text, credentials, workflow spec/state/events, reservations), update every launcher and managed protocol, then fully restart all parent/MCP sessions. Mixed-version admission writers are unsupported.

### Rollback

Set `[orchestration] mode = "single"`. Rollback never deletes data.

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
