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
| verify | parent/final integration | read-only; not independent review |

Any high dimension requires strong; otherwise any medium requires standard; otherwise fast. Hard and review cannot be downgraded below strong. Explicit role wins over task-text inference. Missing dimensions use role defaults and are listed in `assessment.defaulted`. High risk recommends independent review but does not add a new completion gate. Independent review unavailable stays explicit. `verify` is parent/final integration; `review` is independent post-write review. Review+seed requires file AND resource disjointness.

In smart mode, an optional cost-aware lane can skip wrapper/catalog lookup. It is off by default. When `.rig/routing.json` schema 2 sets `execution.direct_parent_low_risk: true`, pick returns native parent writes only for **mini** or **implement** with complexity, risk, and uncertainty all **low**, and only when the live parent is an eligible tracked native parent (codex, grok, opencode, omp, pi, or agy) that is not excluded. That pick is additive provenance `execution_strategy=direct-parent` with `spawn=native`, `parent_writes=true`, `executor_kind=parent`. Catalog confirmation is not consulted on that lane.

All other roles and assessments keep the wrapper catalog path. If no eligible wrapper remains, parent writes use a distinct `execution_strategy=parent-fallback`. Wrapper selections are `wrapper`. Stay is `stay`. Unavailable spawn is `none`. Legacy mode never takes the direct-parent lane.

Profiles are filtered by worker eligibility, exclusion, role, model bans, catalog confirmation, and review-provider rules. Among remaining profiles, choose the minimum sufficient tier, configured preference, then stable ID. Default fast/standard worker order is Grok, Claude, OpenCode, OMP, Pi, agy, Codex. Strong/review order is Claude, Grok, OpenCode, OMP, Pi, agy, Codex. Devin is omitted from those default lists (opt-in via `.rig/routing.json` preferences). Cheap implementation can select a fast model; a high-risk mini task can select strong. There is no unconditional Grok-first ladder in smart mode.

Worker flags, binary availability, scoped MCP readiness and live-parent exclusion remain mandatory. Cursor remains excluded. Explore/review are read-only. A parent fallback preserves the actual observed model/effort or reports unknown: picking a cheaper suggestion never changes the live parent model. Direct-parent jobs use the same `rig_job_start` / authenticated `rig_job_finish` / parent acceptance lifecycle as other parent writes.

## CLI and MCP

```bash
rig pick implement --case "Update a label" --complexity low --risk low --uncertainty low --assessment-reason "One isolated string" --explain
rig session --role implement --case "Change admission checks" --risk high --compact --explain
rig pick mini --case "Small but unfamiliar configuration change" --uncertainty high --json
rig routing report --days 30 --json
```

MCP `rig_pick` and `rig_session` accept `complexity`, `risk`, `uncertainty`, `assessment_reason`, `policy_mode`, and boolean `explain`. They also accept an `assessment` object with `complexity`, `risk`, `uncertainty`, and `reason`; conflicting object/scalar values fail. Unknown keys and invalid types/levels fail. `rig_routing_report` accepts `repo` and positive integer `days` (default 30).

JSON keeps existing pick fields and adds `routing`: policy version/mode, configuration fingerprint, assessment/defaults, required tier, selected profile, candidate decisions, catalog provenance, review recommendation, parent-fit limitations, and additive `execution_strategy` (`direct-parent`, `wrapper`, `parent-fallback`, `stay`, `none`). Text `--explain` expands that trace. JSON always includes the trace. Existing pick/sidecar consumers that ignore unknown fields stay compatible.

Pass the returned `routing` object with the selected worker/model/effort to `rig_job_launch` or `rig_job_start`. Auto-selection during launch also retains routing metadata. A smart launch revalidates the current policy and tuple before consuming a queue claim or starting execution. Configuration drift, incompatible role/effort/tier, or missing required catalog confirmation requires a new pick. The fingerprint is not an authorization token; existing reservation/attempt/owner credentials are still required.

Each launch writes `.rig/jobs/<id>/routing.json`, bound to the exact attempt ID before execution. Explicit model launches without routing metadata retain compatibility but are marked manual/unknown, never presented as assessed smart picks. Historical jobs without sidecars remain readable. Parent execution provenance is separate from suggested child profiles.

## Profile configuration

Built-in selectors derive from `scripts/route.py` model pins. Optional `.rig/routing.json` accepts `schema_version` **1** or **2**. Both may include `profiles` keyed by stable ID and `preferences` keyed by `fast`, `standard`, `strong`, or `review`. Schema 1 remains valid. Schema 2 adds optional `execution.direct_parent_low_risk` (boolean, default `false`). Unknown keys and non-boolean execution values fail smart picks and `rig doctor`; legacy mode still falls back to builtin config.

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

Opt in to low-risk parent writes before catalog lookup:

```json
{
  "schema_version": 2,
  "execution": {
    "direct_parent_low_risk": true
  }
}
```

Unmentioned preferences append in default order. Existing profile IDs can override `worker`, `selector`, `aliases`, `roles`, `tiers`, `effort`, `supported_efforts`, `provider`, and `catalog_required`. New IDs require worker, selector, roles, tiers, effort and provider. supported_efforts defaults to the selected effort, catalog_required defaults by worker, and aliases are optional. Effort must appear in supported_efforts. Roles are explore/mini/bulk/implement/hard/review/verify; tiers fast/standard/strong. Providers are openai/anthropic/xai/google/cursor/cognition and must agree with recognizable model families. Config never enables workers or permits banned models. Invalid config fails smart picks and `rig doctor` instead of silently using legacy.

## Catalog confirmation

OpenCode, OMP, Pi, agy and Devin profiles require a successful CLI catalog. Only exact selectors or explicitly declared aliases match; no substring or arbitrary-first-model fallback. Devin uses `devin models list --format json` only and never falls back to a table parse, keyword hit, or first remaining model. Catalog enumeration order cannot change the chosen profile.

Devin is child-only. Built-in profiles are role-strict: `devin-swe-2-medium` (`swe-2-medium`, explore/mini/bulk, fast), `devin-swe-2-high` (`swe-2-high`, implement, standard), `devin-swe-2-max` (`swe-2-max`, hard/review, strong). Provider is `cognition`. Those IDs are not in the default Grok/Claude/OpenCode/OMP/Pi/agy/Codex preference lists; put them first in `.rig/routing.json` `preferences` to opt in. Manual/direct wrapper launches still reject swe aliases, SWE-1.x, Fusion, empty/default, and role-mismatched SWE-2 selectors.

Cache is fresh for one hour. Successful results up to 24 hours old may be used while refreshing; failed refreshes preserve that bounded cache. Beyond 24 hours, a successful refresh is required. Successful-empty and unavailable are distinct. Probes are bounded and lazy in ranked-candidate order; lower-ranked catalogs are not needed after a sufficient candidate is selected. `RIG_SKIP_MODEL_CATALOG=1` is not confirmation. Static Grok/Claude/Codex pins remain explicitly unverified catalog provenance.

## Reporting

`rig routing report` is read-only. It groups recorded attempts by policy version, tier, profile, actual model, effort, and `execution_strategy`, while showing manual, legacy and missing provenance separately. Direct-parent and wrapper attempts are counted separately. It reports execution failures, cancellation, timeouts, duration, acceptance/freshness, and token coverage without equating exit zero with verification. Acceptance rate uses assessed accepted/rejected work as its denominator, excluding pending/unverified work. Changed content makes old acceptance stale.

Optional `token_usage` is persisted only from an unambiguous final structured worker event: Claude-style `type=result`, or Grok `type=end` with a `stopReason` string. The usage object may include any subset of non-negative integer `input`, `output`, `reasoning`, `cached_input`, and `total`; only fields actually reported are kept. Totals and costs are never estimated or synthesized (`total_cost_usd` is ignored; a missing `total` stays unknown). Negative, malformed, non-final, or ambiguous payloads are omitted. Direct-parent jobs stay usage-unknown unless a real parent implementation supplies the same structured object. Overall known/unknown usage coverage is explicit. Per-component `n`/`sum`/`median` use only records that include that component; a missing field is unknown, not zero. Unknown actual models remain unknown; no inferred costs, correction counts, or adaptive ranking are invented.

## Rollout and rollback

```toml
[routing]
mode = "legacy"
```

Legacy restores the previous ladder and catalog resolver; `--policy-mode legacy` is a per-pick diagnostic override. To keep smart routing but disable the cost-aware lane, set `execution.direct_parent_low_risk` to `false` or omit it (schema 1 remains valid). Launching smart metadata against changed current policy is rejected; re-pick after configuration changes.

Orchestration is separate from routing: `[orchestration] mode = "adaptive"` (default) or `"single"`; `max_nodes` is 12. Queue and worker caps remain authoritative. Adaptive decomposes eligible work into a DAG; `single` keeps one-job behavior. Children never spawn or message children. No estimated progress, savings, or ETA.

For runtime upgrades or rollback: stop new admissions, finish or cancel existing work, confirm stopped, accept or close scopes, preserve data, update all launchers and managed protocols, then fully restart parent/MCP sessions before admitting work. Mixed-version admission writers are unsupported. Adaptive-workflow rollback sets `[orchestration] mode = "single"` and never deletes data. A tested checkout is not an installed or live-runtime-accepted upgrade.
