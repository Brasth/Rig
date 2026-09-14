# Smart routing

Smart is the default for `rig pick`, `rig session`, and their MCP equivalents, including existing harnesses without a routing section. The parent supplies a semantic role and a short assessment; Rig selects a declared model+effort profile. This is deterministic policy, not a second model call or automatic learning system.

## Assessment and selection

| Role | Default complexity / risk / uncertainty | Minimum tier |
| --- | --- | --- |
| explore, mini, bulk | low / low / low | fast |
| implement | medium / medium / medium | standard |
| hard | high / medium / high | strong |
| review | high / medium / medium | strong |
| stay | not assessed | live parent; no catalog discovery |

Any high dimension requires strong; otherwise any medium requires standard; otherwise fast. Hard and review cannot be downgraded below strong. Explicit role wins over task-text inference. Missing dimensions use role defaults and are listed in `assessment.defaulted`. High risk recommends independent review but does not add a new completion gate.

Profiles are filtered by worker eligibility, exclusion, role, model bans, catalog confirmation, and review-provider rules. Among remaining profiles, choose the minimum sufficient tier, configured preference, then stable ID. Default fast/standard worker order is Grok, Claude, OpenCode, OMP, Pi, agy, Codex. Strong/review order is Claude, Grok, OpenCode, OMP, Pi, agy, Codex. Cheap implementation can select a fast model; a high-risk mini task can select strong. There is no unconditional Grok-first ladder in smart mode.

Worker flags, binary availability, scoped MCP readiness and live-parent exclusion remain mandatory. Cursor remains excluded. Explore/review are read-only. A parent fallback preserves the actual observed model/effort or reports unknown: picking a cheaper suggestion never changes the live parent model.

## CLI and MCP

```bash
rig pick implement --case "Update a label" --complexity low --risk low --uncertainty low --assessment-reason "One isolated string" --explain
rig session --role implement --case "Change admission checks" --risk high --compact --explain
rig pick mini --case "Small but unfamiliar configuration change" --uncertainty high --json
rig routing report --days 30 --json
```

MCP `rig_pick` and `rig_session` accept `complexity`, `risk`, `uncertainty`, `assessment_reason`, `policy_mode`, and boolean `explain`. They also accept an `assessment` object with `complexity`, `risk`, `uncertainty`, and `reason`; conflicting object/scalar values fail. Unknown keys and invalid types/levels fail. `rig_routing_report` accepts `repo` and positive integer `days` (default 30).

JSON keeps existing pick fields and adds `routing`: policy version/mode, configuration fingerprint, assessment/defaults, required tier, selected profile, candidate decisions, catalog provenance, review recommendation, and parent-fit limitations. Text `--explain` expands that trace. JSON always includes the trace.

Pass the returned `routing` object with the selected worker/model/effort to `rig_job_launch` or `rig_job_start`. Auto-selection during launch also retains routing metadata. A smart launch revalidates the current policy and tuple before consuming a queue claim or starting execution. Configuration drift, incompatible role/effort/tier, or missing required catalog confirmation requires a new pick. The fingerprint is not an authorization token; existing reservation/attempt/owner credentials are still required.

Each launch writes `.rig/jobs/<id>/routing.json`, bound to the exact attempt ID before execution. Explicit model launches without routing metadata retain compatibility but are marked manual/unknown, never presented as assessed smart picks. Historical jobs without sidecars remain readable. Parent execution provenance is separate from suggested child profiles.

## Profile configuration

Built-in selectors derive from `scripts/route.py` model pins. Optional `.rig/routing.json` has `schema_version: 1`, `profiles` keyed by stable ID, and `preferences` keyed by `fast`, `standard`, `strong`, or `review`.

Example: prefer Claude for standard tasks, without enabling its worker:

```json
{
  "schema_version": 1,
  "profiles": {},
  "preferences": {
    "standard": ["claude-sonnet-5-medium", "grok-4.6-high"]
  }
}
```

Unmentioned preferences append in default order. Existing profile IDs can override `worker`, `selector`, `aliases`, `roles`, `tiers`, `effort`, `supported_efforts`, `provider`, and `catalog_required`. New IDs require worker, selector, roles, tiers, effort and provider. supported_efforts defaults to the selected effort, catalog_required defaults by worker, and aliases are optional. Effort must appear in supported_efforts. Roles are explore/mini/bulk/implement/hard/review; tiers fast/standard/strong. Providers are openai/anthropic/xai/google/cursor and must agree with recognizable model families. Config never enables workers or permits banned models. Invalid config fails smart picks and `rig doctor` instead of silently using legacy.

## Catalog confirmation

OpenCode, OMP, Pi and agy profiles require a successful CLI catalog. Only exact selectors or explicitly declared aliases match; no substring or arbitrary-first-model fallback. Catalog enumeration order cannot change the chosen profile.

Cache is fresh for one hour. Successful results up to 24 hours old may be used while refreshing; failed refreshes preserve that bounded cache. Beyond 24 hours, a successful refresh is required. Successful-empty and unavailable are distinct. Probes are bounded and lazy in ranked-candidate order; lower-ranked catalogs are not needed after a sufficient candidate is selected. `RIG_SKIP_MODEL_CATALOG=1` is not confirmation. Static Grok/Claude/Codex pins remain explicitly unverified catalog provenance.

## Reporting

`rig routing report` is read-only. It groups recorded attempts by policy version, tier, profile, actual model and effort, while showing manual, legacy and missing provenance separately. It reports execution failures, cancellation, timeouts, duration, and acceptance/freshness without equating exit zero with verification. Acceptance rate uses assessed accepted/rejected work as its denominator, excluding pending/unverified work. Changed content makes old acceptance stale. Unknown actual models remain unknown; no inferred costs, correction counts, or adaptive ranking are invented.

## Rollout and rollback

```toml
[routing]
mode = "legacy"
```

Legacy restores the previous ladder and catalog resolver; `--policy-mode legacy` is a per-pick diagnostic override. Launching smart metadata against changed current policy is rejected; re-pick after configuration changes.

For runtime upgrades or rollback: stop new admissions, finish or explicitly cancel existing work, confirm termination, and close/reconcile held scopes. Preserve pending queue text, credentials, worker flags, caps, memory, and custom overrides. Update all launchers and managed protocols, then fully restart parent/MCP sessions before admitting work. Mixed-version admission writers are unsupported. A tested checkout is not an installed or live-runtime-accepted upgrade.
