# Release Notes

## The Rig shell, workspace panel and environment commands (2026-10-20)

New, all optional and off unless you use them. `rig shell` opens a tmux layout with your parent CLI, the new Go board `rig-shell` and a log pane. The board reads everything through the local API, shows Jobs, Queue, Workflows and a Workspace tab, and can allow, deny, stop and enqueue. `rig init --sandbox` and `rig env up | down | status` manage a workspace container through dc-cli without editing your own devcontainer files. `rig doctor` gains `workspace`, `dc-cli` and `rig-shell` checks, and `--workspace` adds dc-cli's own diagnosis. `rig setup` builds `rig-shell` when Go is installed (`--no-shell-binary` skips) and `rig shell build` rebuilds it. New API methods: `workspace.status`, and `board.snapshot` now carries a ready-to-render `view`. Workers still run on the host; `[sandbox] provider = "devcontainer"` remains refused at launch. The dc-cli commands Rig relies on are assumptions not yet verified against a release, and the version pin in `templates/dc-cli.json` is empty. See [the shell guide](ade-shell.md).

## One local API for the board and the companion (2026-10-20)

The board's enqueue, stop, answer and recovery-guidance actions now go through the local API (`queue.add`, `queue.cancel`, `job.cancel`, `job.allow`, `job.deny`, `recovery.guide`). An action falls back to the old direct call only when the API server cannot be reached at all; a request that may already have reached the server is never re-run, and an error the API reports (a disabled project, an unknown item) is shown, not bypassed. Tool results now include a `structured` field where the MCP tool returns one.

The tmux companion's separate `control.sock` is gone. The API server now hosts the same companion service: `ui.request` carries the old operations unchanged, `events.list` and `events.ack` expose notices, and every call needs the API token. `rig ui serve` starts that server. It stays up while a companion session is registered or an action is running and exits after 15 idle seconds otherwise. Companion behavior, receipts and notices are unchanged. Curses board settings panels (Jev key, picker engine, objective, routing domains) still edit configuration directly.

## Board reads through the local API (2026-10-20)

`rig tui` now fetches its jobs, queue, slot, settings and workflow data from the local API (new read-only `board.snapshot` method). If no API server answers, the board starts a detached one that exits after 60 idle seconds and, for that frame and until the server is up, falls back to the previous direct reads, labelled "direct read, API unavailable" in the status line, so the board never blanks. `rig api serve --idle SECONDS` sets the idle exit (default: never). Set `RIG_TUI_API=0` to keep fully direct reads. Board actions (enqueue, stop, answer, routing and key settings, recovery guidance) and the tmux companion are unchanged in this step.

## MCP tool profiles (2026-10-20)

`.rig/harness.toml` gains an optional `[mcp] profile = "full" | "core"` (default and template value `full`, so nothing changes). `core` stops listing eight cost-reporting, benchmark and analysis tools (billing report, import and sync; benchmark create, outcome and report; routing report; task timeline) to shrink the parent's tool list; everything on the launch, wait, check, accept, close, workflow and recovery path stays. `RIG_MCP_PROFILE` overrides the file, and unrecognised values mean `full`. Profiles only filter `tools/list`: they add no authority, hidden tools remain callable by name, and the worker child set is always the same eight tools. `rig doctor` reports the active profile as an optional check.

## Local API v1 (2026-10-20)

`rig api serve` starts a versioned JSON-RPC 2.0 server on a private Unix socket (mode 0600, in the 0700 runtime directory) bound to one repository; `rig api call METHOD [JSON]` and `rig api methods` are the clients. Every request carries the token from `api.token`. Methods map one-to-one onto existing parent MCP tools (session, doctor, status, jobs, queue, workflows, allow, deny, cancel, message), so project-enabled and ownership checks are unchanged, a client-supplied `repo` is ignored, owner tokens are redacted from every response, and a worker child cannot start the server. `docs/local-api.md` is generated from the method registry and a test keeps them in step. Nothing changes for existing workflows; the board and companion move onto this API in a later step.

## Sandbox provider seam (2026-10-20)

`.rig/harness.toml` gains an optional `[sandbox]` section with `provider = "host"` (the default and today's behavior). `scripts/sandbox_provider.py` defines the provider interface a container backend will implement; the host provider changes nothing. `provider = "devcontainer"` is reserved: `run-worker.sh` refuses to launch with it, and so does any unknown value, rather than silently running on the host. `rig doctor` lists the provider as an optional check. Existing repos need no change.

## OpenCode cheap-model policy (2026-10-15)

Every install and update now limits OpenCode children to three exact OpenCode Go selectors: `opencode-go/glm-5.3-flash` (default, profile `opencode-glm-5.3-flash`), `opencode-go/deepseek-v4.1-flash` (second, `opencode-deepseek-v4.1-flash`), and region-dependent `opencode-go/muse-spark-1.3-contributor` (optional third, `opencode-muse-spark-1.3-contributor`). OpenCode Go is required; the selectors must be listed by `opencode models` for your account and region. Rig does not set up authentication, purchase anything, enable OpenCode, update its binary, or edit your global OpenCode configuration.

Behavior changes:

- OpenCode profiles and legacy defaults cover explore, mini, bulk, implement, and verify only, with an empty effort and no `--variant`. OpenCode no longer has hard, review, or strong-tier profiles; legacy hard/review picks skip OpenCode and use the normal fallback to other workers or the parent.
- Explicit overrides are enforced on every path: smart and legacy picks, manual and MCP launches, native-child admission, and the direct `run-worker.sh` wrapper. Unsupported, look-alike, or empty models, any effort, and hard/review roles are rejected with the approved list. Nothing is silently substituted, and Rig does not retry another model after a runtime quota or model failure.
- The exact selector must appear in the OpenCode catalog before launch. A missing CLI, authentication, or catalog entry makes OpenCode unavailable for that pick while other workers keep their existing routing.
- New canonical providers `zai`, `deepseek`, and `meta` identify the model family across transport prefixes (`opencode-go` is a transport, not a provider), so review independence compares actual families. Billing scopes and provider enums include them.
- The profiles `opencode-gpt-5.4-mini-minimal`, `opencode-gpt-5.6-luna-high`, and `opencode-gpt-5.6-terra-max`, and OpenCode `openai/gpt-*` selectors, are removed. Smart mode rejects a `.rig/routing.json` that still references them with a migration message; legacy mode ignores it. Files on disk are never rewritten. Disallowed overrides of the new profiles fail validation clearly.

No performance, quality, or pricing claims are made for the approved models; their built-in capability scores are neutral. Schema versions are unchanged. See [OpenCode cheap-model policy](smart-routing.md#opencode-cheap-model-policy).

### Rollout / restart

Install or update the runtime as usual (the new `scripts/opencode_policy.py` ships with the other scripts), then fully restart parent and MCP sessions before the next admission. Replace removed profile IDs in `.rig/routing.json` and any OpenCode selector overrides before using smart mode. Confirm `opencode models` lists the approved selectors.

### Rollback

Reinstall the previous revision with the runtime rollback procedure and restart parent and MCP sessions. Rollback does not delete routing data or touch your OpenCode configuration.

## Task-domain routing v1 (2026-09-30)

Smart routing now accepts a task domain separately from role and capability tier: `general`, `ui-design`, `frontend`, `ui-verification`, `research`, `backend`, `debugging`, and `review`. CLI pick/session and job start use `--task-domain` and repeatable `--research-source`; MCP pick/session/job start/job launch accept `task_domain` and `research_sources`. Workflow nodes carry the same inputs. Explicit domain wins; omitted domains use bounded English inference without changing an explicit role.

Schema 4 `.rig/routing.json` adds per-domain ordered `preferred_profiles` and `fallback: scored|parent|none`. The first eligible preference wins. Existing schema 1–3 configurations remain valid. Domain policies never enable workers or relax role, tier, availability, catalog, or independent-review provider gates. High-risk tasks still require strong profiles. Configured domains run their preferences and fallback locally instead of the direct-parent shortcut, ordered picker, or Jev. Model preferences and capability scores are heuristics, not new benchmark claims or guaranteed model winners.

UI design and visual/browser verification remain parent-only. Research workers must be explore/read with existing readable repository-contained source files; source acquisition without those inputs stays with the parent. Workers gain no browser, vision, Figma, or computer-use access. The parent must separately verify its tools and authorization.

Picks, explanation output, and attempt-bound routing sidecars include domain evidence. Admission revalidates the current domain policy and source access, and research sources are protected read scope. Changed policy or conflicting inputs require a new pick. Manual and legacy launches cannot silently discard an explicit domain/source contract. No ownership, queue, acceptance, or cancellation rule is relaxed. Policy version 2 requires domain evidence on every new smart launch; re-pick older policy-v1 selections. Historical sidecars remain readable.

See [Smart routing](smart-routing.md#task-domains) and the [example domain configuration](examples/task-domain-routing.json). The example uses existing Sonnet, Luna, and Grok profile IDs as project customizations; it does not change default pins or enable those workers.

### Rollout / restart

Stop new admissions, finish or cancel existing work, confirm stopped, accept or close scopes, and preserve routing, queue, reservation, and workflow data. Update every launcher and managed source skill/protocol, then fully restart all parent/MCP sessions before new admissions. Validate merged schema 4 configuration with `rig doctor` and inspect domain picks with `--explain`. A tested checkout is not an installed or live-runtime-accepted upgrade; mixed-version admission writers are unsupported.

### Rollback

Remove `domains` to return to the existing smart picker preferences, retaining only fields supported by the chosen schema. Smart mode still applies built-in domain classification and parent-only boundaries. For the old ladder, set `[routing] mode = "legacy"` and stop passing domain/source inputs; legacy rejects explicit domain contracts. Re-pick after any policy change. For a runtime rollback, use the same stop/confirm/accept-or-close/preserve/restart procedure and restore a routing config compatible with the older runtime. Do not delete job or workflow data.

## Claude Sonnet 5.5 and computer-use opt-in (2026-09-30)

Claude Code implement/verify and the wrapper default now select `claude-sonnet-5-5` at effort `medium`. The stable profile ID remains `claude-sonnet-5-medium` so existing routing preferences continue to work. Haiku and the already updated Opus 5.5 selectors are unchanged. See the [official Sonnet 5.5 model ID](https://platform.claude.com/docs/en/models/sonnet-5-5/overview). Rig launches Claude Code rather than constructing Messages API requests; live model availability still depends on the installed Claude Code and account.

Globally installed computer-use and computer-test skills no longer select Rig for a generic request. Rig requires an enabled project, available parent MCP, and an opted-in available backend. Otherwise follow available host-native capabilities and their instructions. Explicit Rig requests report setup blockers without silently choosing another provider or enabling anything. Permission denials on a selected Rig path still cannot be bypassed. CU and BrowserSkill action tools are hidden while the project is disabled or uninitialized, even if backend flags remain enabled.

Refresh managed project skills and protocol text, then safely restart parent/MCP sessions using the existing rollout procedure. This change does not enable a project or backend. Historical job metadata and stable routing profile IDs are not rewritten.

## OpenAI Luna and Claude Opus pins (2026-09-23)

Codex explorer and routine Luna pins are `gpt-6-luna` at effort `low` on profiles `codex-explorer-low` and `codex-luna-low`. Role limits stay the same: the explorer profile is explore-only. Codex hard and review stay `gpt-5.6-terra` on `codex-terra-medium` and `codex-terra-high`.

OpenCode standard Luna is exact selector `openai/gpt-6-luna` with alias `gpt-6-luna` on profile `opencode-gpt-5.6-luna-high`. That profile stays `catalog_required`. OpenCode cheap and Terra pins are unchanged.

Claude Code hard/review and OMP/Pi review selectors are `claude-opus-5-5`. Cursor review is `claude-opus-5-5-thinking-high`. Profile IDs stay `claude-opus-5-high`, `omp-claude-opus-5-high`, `pi-claude-opus-5-high`, and `cursor-opus-thinking-high`.

Default fast/standard worker order is Codex, Grok, Claude, conditional Devin, conditional MiMo, OpenCode, OMP, Pi, agy. Strong/review is conditional Devin, Claude, Codex, Grok, OpenCode, OMP, Pi, agy. MiMo is fast/standard only. Devin stays disabled unless the user enables it; MiMo stays opt-in. Preference order does not enable workers and does not bypass live-parent exclusion or other gates.

This supersedes the 2026-09-16 shipped explorer selector.

### Rollout / restart

Install the checkout into `~/.rig`, then fully restart parent and MCP sessions before the next admission. Mixed-version admission writers are unsupported.

### Rollback

Restore Codex and OpenCode Luna selectors to `gpt-5.6-luna` and `openai/gpt-5.6-luna` (alias `gpt-5.6-luna`), Opus selectors to `claude-opus-5` (Cursor `claude-opus-5-thinking-high`, alias `anthropic/claude-opus-5`), and fast/standard worker order to Grok, Claude, OpenCode, OMP, Pi, agy, Codex. Fully restart parent and MCP sessions. Rollback does not delete routing data.

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

The shipped explorer default in this release was `gpt-5.6-luna` on profile `codex-explorer-low`. Unsupported `gpt-5.3-codex-mini` is no longer the shipped explorer selector. The 2026-09-23 note moves that selector to `gpt-6-luna`.

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

Remove the `codex-explorer-low` Spark selector override to restore the shipped explorer selector (`gpt-6-luna` as of 2026-09-23).

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

Enable Devin via routing preferences in `.rig/routing.json`. Default fast/standard order is Codex, Grok, Claude, OpenCode, OMP, Pi, agy. Strong/review stays Claude, Grok, OpenCode, OMP, Pi, agy, Codex.

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
