# Smart routing

Smart is the default for `rig pick`, `rig session`, and their MCP equivalents, including existing harnesses without a routing section. The parent supplies a semantic role, a task domain, and a short assessment; Rig selects a declared model+effort profile. Role describes the job stage, domain describes the work, and the assessment sets the required capability tier. Local routing uses the scored picker by default; Jev is an optional external choice engine with local fallback.

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

In smart mode, an optional cost-aware lane can skip wrapper/catalog lookup. It is off by default. When `.rig/routing.json` schema 2 sets `execution.direct_parent_low_risk: true`, pick returns native parent writes only for **mini** or **implement** with complexity, risk, and uncertainty all **low**, and only when the live parent is an eligible tracked native parent (codex, grok, opencode, omp, pi, or agy) that is not excluded, the domain permits delegated implementation, and there is no configured policy for that domain. That pick is additive provenance `execution_strategy=direct-parent` with `spawn=native`, `parent_writes=true`, `executor_kind=parent`. Catalog confirmation is not consulted on that lane.

Parent-only domains return before wrapper selection. Other roles and assessments use the wrapper catalog path unless the configured domain fallback skips it. If no eligible wrapper remains and parent fallback is permitted, parent writes use a distinct `execution_strategy=parent-fallback`. Wrapper selections are `wrapper`. Stay is `stay`. Unavailable spawn is `none`. Legacy mode never takes the direct-parent lane.

Profiles are filtered by worker eligibility, exclusion, role, model bans, catalog confirmation, and review-provider rules. The local picker then scores every eligible canonical model/effort group for task traits, tier fit, configured preference, transport health, and the selected objective (`quality`, `balanced`, `speed`, or `cost`). Duplicate transports for one provider/model/effort group are collapsed before scoring. Default fast/standard worker order is Codex, Grok, Claude, Devin, MiMo, OpenCode, OMP, Pi, agy. Strong/review order is Devin, Claude, Codex, Grok, OpenCode, OMP, Pi, agy. MiMo is fast/standard only. Devin is a conditional default candidate. Unavailable, disabled, or unverified Devin and MiMo are skipped automatically. Cheap implementation can select a fast model; a high-risk mini task can select strong. There is no unconditional Grok-first ladder in smart mode.

## Task domains

Smart routing accepts `task_domain` independently of `role` and the fast/standard/strong tier:

| Domain | Meaning and boundary |
| --- | --- |
| `general` | Work without a more specific domain |
| `ui-design` | Parent-only design, vision, Figma, and visual inspection |
| `frontend` | Implementation of UI code from the supplied brief and artifacts |
| `ui-verification` | Parent-only visual checks and browser/computer-use verification |
| `research` | Parent source acquisition; read-only worker synthesis only from verified local source files |
| `backend` | Server, API, database, and other backend implementation |
| `debugging` | Diagnosis and fixes; the role still determines read/write scope |
| `review` | Review-stage work; does not replace role or independent-provider checks |

An explicit domain is authoritative, including `general`. When absent, bounded English keyword rules infer a domain; role `review` defaults to domain `review`, and unmatched text defaults to `general`. Inference is a convenience, not multilingual understanding. Pass both semantic role and domain for ambiguous or non-English requests. Domain inference never changes an explicit role. For example, frontend review is `role=review, task_domain=frontend`; choosing domain `review` with another delegated role fails rather than upgrading that role into a reviewer (`stay` is allowed).

Domain labels and profile capability scores are routing heuristics, not benchmark results or claims that one model is best at a task. A frontend preference cannot lower a high-risk task below strong, turn an explore-only model into a writer, enable a worker, or make the writer's provider eligible as its independent reviewer.

### Parent-only capabilities

`ui-design` and `ui-verification` stay with the parent. A configured policy for either must have empty `preferred_profiles` and `fallback: "parent"`; other values fail validation. A parent-only domain requested as an independent worker review reports review unavailable. Domain configuration cannot give workers vision, Figma, browser, BrowserSkill, or computer-use access.

A `stay` result is a routing boundary, not proof the parent has the necessary tools or authorization. Verify the parent's actual capability and existing project/backend opt-ins before acting. Missing tools do not justify enabling a backend or delegating clicks. For a design-to-code task, the parent obtains the design evidence, passes concrete artifacts to a `frontend` worker, then performs `ui-verification` itself.

### Research source contract

A research child requires all three: `role=explore`, `access=read`, and a nonempty `research_sources` list. Sources must already exist as readable regular files inside the repository. Use repository-relative file paths, at most 100 entries. URLs, absolute paths, `..` paths, directories, missing/unreadable files, and symlinks resolving outside the repository are rejected. Source paths are normalized and deduplicated; nonempty research sources are invalid with another domain.

With no sources, or with a role other than explore, research stays with the parent for source acquisition unless the domain policy sets `fallback: "none"`, which reports unavailable. A review request reports independent worker review unavailable. Explicit `role=stay` remains parent planning/inspection even with `fallback: "none"`; no worker fallback is attempted, and registering that stay requires explicit `access=read`. Invalid supplied sources fail validation rather than being ignored. This does not grant the parent browsing access either: check its real tools and permissions separately. Once the parent has made the source material available locally, a read-only research worker can compare or synthesize it. The research contract restricts workers to the supplied local sources; missing or remote material must be requested from the parent. This adds no browser/tool permission and is not a new shell/network sandbox.

Sources are checked again at launch and included in the job's read admission scope. Pass the same normalized source list when claiming queued work; a claim must cover the admitted sources. Workflow explore nodes also include sources in their read scope and cannot declare side effects or write resources. Source deletion, loss of read access, or a symlink moved outside the repository requires correcting the inputs and re-picking before launch.

## Picker engine and Jev

Project settings live under `[routing]` in `.rig/harness.toml`:

```toml
[routing]
mode = "smart"
engine = "local" # local | jev
local_policy = "scored-v1" # scored-v1 | ordered-v1
objective = "balanced" # quality | balanced | speed | cost
```

Use `rig routing engine jev` to enable Jev for one project and `rig routing engine local` to return to the local picker. `rig routing objective quality|balanced|speed|cost` changes only that project. The TUI Settings tab exposes the same project controls.

Set the global Jev key once with `rig provider jev setup`; it is held in the macOS Keychain as service `rig`, account `jev-api-key`. `RIG_API_JEV_KEY` takes precedence for CI. `rig provider jev status --json` and `rig provider jev remove` inspect or remove the global credential. Keys never enter a project file, route sidecar, command argv, or routing report.

For domains without a configured policy, Jev only receives a bounded task summary, role, assessment, trait labels, and the already hard-filtered canonical candidate IDs. It cannot enable a disabled worker, choose a banned model, bypass a catalog, or evade independent-review provider checks. Missing credentials, timeouts, transport/API errors, invalid answers, or more than 255 candidates fall back to the scored local picker. Routing evidence records the engine, selected ID, traits, scores, and fallback code without preserving the task text.

Worker flags, binary availability, scoped MCP readiness and live-parent exclusion remain mandatory. Cursor runs with a job-scoped `--plugin-dir` Rig MCP and `--force`; other plugin MCPs stay visible, so the wrapper fails the job on any non-Rig MCP call (tripwire). Cursor is last resort in pick. Explore/review are read-only. A parent fallback preserves the actual observed model/effort or reports unknown: picking a cheaper suggestion never changes the live parent model. Direct-parent jobs use the same `rig_job_start` / authenticated `rig_job_finish` / parent acceptance lifecycle as other parent writes.

## CLI and MCP

```bash
rig pick implement --case "Update a label" --complexity low --risk low --uncertainty low --assessment-reason "One isolated string" --explain
rig session --role implement --case "Change admission checks" --risk high --compact --explain
rig pick mini --case "Small but unfamiliar configuration change" --uncertainty high --json
rig pick implement --task-domain frontend --case "Build the supplied component" --explain
rig session --role stay --task-domain ui-verification --case "Check the rendered layout" --compact --explain
# These source files must already exist and be readable inside this repository:
rig pick explore --task-domain research --research-source docs/source-a.md --research-source docs/source-b.md --case "Compare the two sources" --json
rig routing report --days 30 --json
```

CLI `rig pick`, `rig session`, and `rig job start` accept `--task-domain`; repeat `--research-source` for each source file. MCP `rig_pick`, `rig_session`, `rig_job_launch`, and `rig_job_start` accept string `task_domain` and array `research_sources`. Workflow node specifications accept the same fields.

MCP `rig_pick` and `rig_session` accept `complexity`, `risk`, `uncertainty`, `assessment_reason`, `policy_mode`, and boolean `explain`. They also accept an `assessment` object with `complexity`, `risk`, `uncertainty`, and `reason`; conflicting object/scalar values fail. Unknown keys and invalid types/levels fail. `rig_routing_report` accepts `repo` and positive integer `days` (default 30).

JSON includes the selected top-level `task_domain` and a detailed `routing.task_domain` object: name, source (`explicit`, `inferred`, or `default`), rule, policy, ordered preferred profiles, fallback, capability requirements, normalized research sources, parent-only boundary, selection, and reason. `--explain` includes the domain and selection reason.

JSON keeps existing pick fields and adds `routing`: policy version/mode, configuration fingerprint, assessment/defaults, required tier, selected profile, candidate decisions, catalog provenance, review recommendation, parent-fit limitations, and additive `execution_strategy` (`direct-parent`, `wrapper`, `parent-fallback`, `stay`, `none`). Text `--explain` expands that trace. JSON always includes the trace. Existing pick/sidecar consumers that ignore unknown fields stay compatible.

Pass the returned `routing` object with the selected worker/model/effort to `rig_job_launch` or `rig_job_start`. Auto-selection during launch also retains routing metadata. A smart launch revalidates the current policy and tuple before consuming a queue claim or starting execution. Configuration drift, incompatible role/effort/tier, or missing required catalog confirmation requires a new pick. Domain policy, capability boundaries, and source readability/containment are also revalidated at admission. Launch can inherit domain and source inputs from the returned routing object; conflicting scalar inputs fail rather than replacing it. The fingerprint is not an authorization token; existing reservation/attempt/owner credentials are still required.

Each launch writes `.rig/jobs/<id>/routing.json`, bound to the exact attempt ID before execution. Explicit model launches without routing metadata and without an explicit domain/source contract retain compatibility but are marked manual/unknown, never presented as assessed smart picks. An explicit domain/source contract requires smart routing metadata; manual or legacy launch cannot silently discard it. Auto-selection can create that metadata before admission. Historical jobs without sidecars remain readable. Policy version 2 requires domain evidence on every new smart launch; re-pick older policy-v1 selections. Historical sidecars remain readable. Parent execution provenance is separate from suggested child profiles.

## Profile configuration

Built-in selectors derive from `scripts/route.py` model pins. Optional `.rig/routing.json` accepts `schema_version` **1**, **2**, **3**, or **4**. Schema 1 has profiles/preferences; schema 2 adds optional `execution.direct_parent_low_risk`; schema 3 adds `picker` (`engine`, `local_policy`, `objective`) as a project-specific override; schema 4 adds `domains`. Schemas 1–3 remain valid and need no migration when domain preferences are not required. Unknown keys and invalid picker/execution values fail smart picks and `rig doctor`; legacy mode still falls back to builtin config.

Example: prefer Claude for standard tasks, without enabling its worker:

```json
{
  "schema_version": 1,
  "profiles": {},
  "preferences": {
    "standard": ["claude-sonnet-5-medium", "grok-4.7-high"]
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

Unmentioned preferences append in default order. Existing profile IDs can override `worker`, `selector`, `aliases`, `roles`, `tiers`, `effort`, `supported_efforts`, `provider`, `catalog_required`, and `capability`. Capability values are configurable heuristic scores, not measured guarantees. New IDs require worker, selector, roles, tiers, effort and provider. supported_efforts defaults to the selected effort, catalog_required defaults by worker, and aliases are optional. Effort must appear in supported_efforts. Roles are explore/mini/bulk/implement/hard/review/verify; tiers fast/standard/strong. Providers are openai/anthropic/xai/google/cursor/cognition/xiaomi and must agree with recognizable model families. Config never enables workers or permits banned models. Invalid config fails smart picks and `rig doctor` instead of silently using legacy.

### Schema 4 domain preferences

`domains` is an object keyed by the domain names above. Each entry accepts only `preferred_profiles` (ordered stable profile IDs, default `[]`) and `fallback` (`scored`, `parent`, or `none`, default `scored`). Unknown domains, unknown/duplicate profile IDs, and invalid keys or fallbacks fail validation. Unmentioned domains retain built-in routing. Declaring a domain policy suppresses the low-risk direct-parent shortcut for that domain so its preferences are evaluated first.

The first **eligible** configured profile wins. All worker flags, binary/MCP availability, live-parent exclusions, model bans, role support, required tier, catalog confirmation, and review-provider gates still apply. If no preferred profile qualifies:

- `scored`: run the local scored picker over all eligible profiles; if none qualifies, retain the existing terminal parent/unavailable fallback
- `parent`: skip other wrapper profiles and permit only the existing parent fallback; tracked parent writes still require start/finish/check/accept, read-only roles stay read-only, and unavailable independent review remains unavailable
- `none`: report `spawn=none`; do not substitute a different worker or parent. Use this for a worker-only preference allowlist

A configured domain policy takes precedence over `ordered-v1` and Jev selection. Its scored fallback is local; Jev cannot replace the configured preference. Domains without a configured policy keep the existing picker engine and objective.

Example: a project can prefer the existing Sonnet profile, then Luna, for frontend implementation:

```json
{
  "schema_version": 4,
  "domains": {
    "frontend": {
      "preferred_profiles": ["claude-sonnet-5-medium", "codex-luna-low"],
      "fallback": "scored"
    }
  }
}
```

This is a customization example, not a built-in winner or a promise either profile will run. The stable Sonnet profile currently selects `claude-sonnet-5-5`; the Luna write profile selects `gpt-6-luna`. Both are skipped when the role/tier or worker gates do not fit. In particular, a strong frontend task cannot use these standard-tier preferences; any selected worker must use an eligible strong profile instead.

See [the complete example configuration](examples/task-domain-routing.json) for frontend, backend, debugging, local-source research, review, and parent-only domains. Its Grok preferences are also project choices, not measured superiority claims. `codex-explorer-low` is used only as a research preference because it is explore-only; it does not gain write access. The separate `codex-luna-low` write profile keeps its existing role limits. Merge the example's `domains` into your existing `.rig/routing.json` with `schema_version: 4`; do not overwrite unrelated profile, execution, or picker settings. Run `rig doctor` and inspect `rig pick ... --explain` before launching.

## TUI domain settings and preview

In `rig tui`, open **Settings**, select **Domain routing**, and press `e`.
The form edits existing profile IDs in preference order and cycles the domain's
`scored` / `parent` / `none` fallback. Use Tab/Shift-Tab or up/down to select a
field, Enter/left/right to cycle a choice, and ordinary text editing for the
comma-separated profile IDs. The available IDs appear below the form; PgUp/PgDn
scroll the details. This editor does not create profiles, enable workers, change
provider credentials, or change popup controls. UI design/verification remain
parent-only. Ctrl-R removes the selected domain's override in the draft.

- **F5** previews the current task/domain/role, optional complexity/risk/uncertainty,
  excluded workers, local research sources, and independent-review writer job
- **F2** saves domain preferences only; task and assessment fields are never saved
- **Esc** closes and discards unsaved edits; a submitted save finishes before closing

Opening or cancelling the form does not change project files. Save validates the
entire candidate with the same parser as normal routing, compares the exact file
content fingerprint captured when the form opened, then atomically replaces
`.rig/routing.json`. If another editor changed the file, cancel and reopen rather
than overwriting it. Invalid input, stale saves, and failed writes leave the
original file intact. Unrelated profiles/preferences/execution/picker settings
are preserved. A domain edit upgrades schema 1–3 to schema 4; a no-change save
leaves the original bytes/schema untouched. Disabled or uninitialized projects
are not enabled implicitly. Legacy-mode settings can be edited, but previews
require the project to use smart routing; the editor never switches its mode.

Preview uses the normal `route.pick` / smart policy against the unsaved candidate
and a detached, read-only local catalog snapshot. It shows the actual selected
worker/model/effort/tier for that snapshot, parent-only/unavailable outcomes,
configuration fingerprint, and candidate rejection reasons. Worker flags, binary
and MCP readiness, live parent, bans, role/tier, independent-review provenance,
parent-only domains, and research-source gates remain authoritative. Unknown
parent model/effort stay unknown. The preview does not launch jobs, refresh
catalogs, invoke provider CLIs, or call a routing provider. Missing, skipped,
empty, and expired catalog entries are not promoted to fresh. Bounded stale
catalogs retain their existing policy eligibility. Because no refresh occurs,
a later live pick can change after its catalog refresh. If Jev would be used,
the preview labels the local fallback explicitly; it cannot predict Jev's reply.

Preview evidence is marked `preview_only` and is rejected by launch validation.
Run a fresh normal pick before starting work; neither the preview nor its
fingerprint grants admission authority. Existing CLI/MCP contracts are unchanged.

## Catalog confirmation

OpenCode, OMP, Pi, agy, Devin, and MiMo profiles require a successful CLI catalog. Only exact selectors or explicitly declared aliases match; no substring or arbitrary-first-model fallback. Devin uses `devin models list --format json` only and never falls back to a table parse, keyword hit, or first remaining model. Catalog enumeration order cannot change the chosen profile.

MiMo Code is an opt-in child (`mimo = false` in new repos). Use `rig setup --mimo` to install the official CLI if needed and enable it in the current initialized repo, or `rig workers mimo=on` for an already installed CLI. Each job receives Rig MCP from a private `MIMOCODE_CONFIG_DIR`; global MiMo settings remain untouched. The launcher uses `mimo run --format json` and never passes MiMo's `--yolo` flag. MiMo must complete the normal inbox handshake before the job counts as started. Its fast and standard pins are the exact catalog selectors `xiaomi/mimo-v2.6-flash` and `xiaomi/mimo-v2.6-pro`; Rig refreshes MiMo's Xiaomi catalog before confirming either selector. MiMo's binary, worker flag, and exact catalog match are required. When those gates pass, MiMo is a fast/standard default candidate after Devin and before OpenCode. It has no hard or review profile. Picker explanations and `rig doctor` identify disabled workers, missing CLIs or MCP setup, unsupported roles, and catalog failures with next steps.

Devin is child-only and stays disabled unless the user enables it. Built-in profiles are role-strict: `devin-swe-2-medium` (`swe-2-medium`, explore/mini/bulk, fast), `devin-swe-2-high` (`swe-2-high`, implement, standard), `devin-swe-2-max` (`swe-2-max`, hard/review, strong). Provider is `cognition`. Those IDs are conditional default candidates: fast/standard places Codex first, then Grok and Claude, then Devin before MiMo; strong/review places Devin first. Selection still requires the worker flag, the `devin` binary, job-scoped MCP readiness, and an exact confirmed catalog model. Unavailable, disabled, or unverified Devin is skipped automatically. Manual/direct wrapper launches still reject swe aliases, SWE-1.x, Fusion, empty/default, and role-mismatched SWE-2 selectors.

Cache is fresh for one hour. Successful results up to 24 hours old may be used while refreshing; failed refreshes preserve that bounded cache. Beyond 24 hours, a successful refresh is required. Successful-empty and unavailable are distinct. Probes are bounded and lazy in ranked-candidate order; lower-ranked catalogs are not needed after a sufficient candidate is selected. `RIG_SKIP_MODEL_CATALOG=1` is not confirmation. Static Grok/Claude/Codex pins remain explicitly unverified catalog provenance.

## Reporting

`rig routing report` is read-only. It groups recorded attempts by policy version, tier, profile, actual model, effort, and `execution_strategy`, while showing manual, legacy and missing provenance separately. Direct-parent and wrapper attempts are counted separately. It reports execution failures, cancellation, timeouts, duration, acceptance/freshness, and token coverage without equating exit zero with verification. Acceptance rate uses assessed accepted/rejected work as its denominator, excluding pending/unverified work. Changed content makes old acceptance stale.

Optional `token_usage` is persisted only from an unambiguous final structured worker event: Claude-style `type=result`, or Grok `type=end` with a `stopReason` string. The usage object may include any subset of non-negative integer `input`, `output`, `reasoning`, `cached_input`, and `total`; only fields actually reported are kept. Totals and costs are never estimated or synthesized (`total_cost_usd` is ignored; a missing `total` stays unknown). Negative, malformed, non-final, or ambiguous payloads are omitted. Direct-parent jobs stay usage-unknown unless a real parent implementation supplies the same structured object. Overall known/unknown usage coverage is explicit. Per-component `n`/`sum`/`median` use only records that include that component; a missing field is unknown, not zero. Unknown actual models remain unknown; no inferred costs, correction counts, or adaptive ranking are invented.

## Rollout and rollback

```toml
[routing]
mode = "legacy"
```

Legacy restores the previous ladder and catalog resolver; `--policy-mode legacy` is a per-pick diagnostic override. Explicit `task_domain` or `research_sources` inputs are rejected in legacy mode; it is not a fallback that silently accepts a domain contract. To keep smart routing but disable the cost-aware lane, set `execution.direct_parent_low_risk` to `false` or omit it (schema 1 remains valid). To remove domain preferences while keeping smart routing, remove the `domains` object; schemas 1–3 are still supported if you retain only the fields that schema allows. Built-in domain inference and parent-only boundaries remain active in smart mode. Launching smart metadata against changed current policy is rejected; re-pick after configuration changes.

Orchestration is separate from routing: `[orchestration] mode = "adaptive"` (default) or `"single"`; `max_nodes` is 12. Queue and worker caps remain authoritative. Adaptive decomposes eligible work into a DAG; `single` keeps one-job behavior. Children never spawn or message children. No estimated progress, savings, or ETA.

For runtime upgrades or rollback: stop new admissions, finish or cancel existing work, confirm stopped, accept or close scopes, preserve data, update all launchers and managed protocols, then fully restart parent/MCP sessions before admitting work. Mixed-version admission writers are unsupported. Compatible controller rollback preserves `[orchestration] mode` and all data; incompatible versions require a separate reviewed migration. A tested checkout is not an installed or live-runtime-accepted upgrade.

Runtime reports add domain slices, explicit execution-latency and token coverage, and forward-only workflow blocked-time evidence. See [local runtime metrics](runtime-metrics.md) for definitions, unknown/partial coverage and the opt-in offline performance benchmark.
