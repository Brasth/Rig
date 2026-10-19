# Rig → ADE and TUI · codebase analysis and plan (2026-10-19)

Source: the design canvas "Rig ADE and TUI plan", Plan page (seven boards:
Verdict, ADE shell mockup, Current state, Target architecture, dc-cli
integration map, Phased roadmap, Decisions and risks). Read against
`Brasth/Rig` head `1ccae0b` (merge of PR #55, "declare Python runtime
metadata"). The canvas was written against head `4fca3b2`, one PR earlier.

Scope of this plan: changes inside this repository only. Work the canvas
assigns to dc-cli (schemaVersion on `--json`, `dc exec --env`, `dc up
--name`, a `rig-workers` devcontainer feature, a public Go package for the
board theme) is listed where Rig depends on it, with the fallback Rig uses
until it lands. The website, account, phone, marketplace and features pages
of the canvas are product surfaces outside this repo and are not planned here.

## 1. The canvas's claims, checked against the code

| Canvas claim | Status at `1ccae0b` | Evidence |
| --- | --- | --- |
| No pyproject | Partly fixed | `pyproject.toml` exists (version `"1"`, `dependencies = []`, no entry points, no package layout). Install still copies the tree into `~/.rig` via `install.sh` + `bin/rig setup`. |
| No `.github` CI | Confirmed | No `.github/` directory. `CONTRIBUTING.md` line 85: "GitHub-hosted test workflows are currently deferred." |
| ~2,000 unittest cases, 108 test files | Confirmed | 108 `tests/test_*.py`; `unittest` discovery counts 1,990 cases. `tests/run_tests.py --syntax-only` passes (225 Python, 7 shell files). |
| 42k lines stdlib Python | Confirmed | 43,716 lines across `scripts/`, `scripts/mcp_tools/`, `bin/rig`. |
| 76 parent + 8 child MCP tools | Confirmed | `rig_mcp.TOOLS` = 76, `CHILD_TOOLS` = 8 (`scripts/mcp_tools/schemas.py`). Computer-use and BrowserSkill tool groups are already filtered out of `tools/list` by config in `rig_mcp.listed_tools()`, which is a nascent profile mechanism. |
| Only listener is the tmux companion's Unix socket | Confirmed | `scripts/ui_service.py` serves `control.sock` under `/tmp/rig-ui-<uid>/<token>/` with ops `ping`, `snapshot`, `action`, `details`, `ack`, `register`, `unregister`. Unversioned, companion-shaped, no auth beyond directory mode. |
| curses board reads `.rig` files directly | Confirmed | `scripts/tui_runtime.load_snapshot()` calls `jobs.list_jobs`, `work_queue.list_items`, `harness.parse_harness` in-process. `rig_tui.py` never touches `control.sock`. |
| No OS boundary for workers | Confirmed | `scripts/run-worker.sh` (1,452 lines) builds commands with `codex exec -s workspace-write`, `claude --permission-mode acceptEdits`, `cursor --force`, `opencode --auto`, pi/agy accept-edits; line ~1100 refuses dangerous modes. Everything runs on the host checkout. |
| No worktrees for execution | Confirmed, with a seed | `scripts/worktrees.py` does discovery and identity registration only; `rig worktrees` help text says "does not enable execution". |
| Companion covers Codex and Grok only | Confirmed | `scripts/ui_launch.py` classifies `codex` and `grok` launches only. |
| Curses + tmux HUD, F8/F9 popups | Confirmed | `ui_popup.py`, `ui_launch.py`, tmux key bindings. |
| Acceptance contracts with exact argv checks | Confirmed | `scripts/acceptance_contract.py`: `fingerprint()` is sha256 of the contract JSON; evidence refs carry `sha256`. Container id and image digest are not evidence fields yet. |
| `docs/tui-design.md` scope rules | Confirmed | The current board is explicitly a read-only state view; no editor, no project selection. |

Net: the canvas's read of the repo is accurate. One Phase 0 item (pyproject)
has started since it was written; the rest is open.

## 2. Analysis

### 2.1 What exists per layer of the target architecture

- Surfaces: `bin/rig` (Bash dispatcher, 1,650 lines, `--json` on 31 commands),
  `scripts/rig_tui.py` + `tui_*.py` (curses board), `scripts/ui_*.py`
  (tmux companion). No Go code in the repo.
- Protocol: `scripts/rig_mcp.py` (stdio MCP, hand-written), `scripts/mcp_runtime.py`
  (request lifecycle), `scripts/mcp_tools/*` (schemas and handlers),
  `scripts/child_mcp.py` (per-worker MCP config written into the job dir).
  `templates/runtime-compat.json` carries protocol version numbers
  (`admission_protocol`, `child_mcp_protocol`, ...). No local API.
- Core: `admission.py`, `jobs.py` (3,173 lines), `work_queue.py`,
  `coordination.py`, `route.py` + `routing_*.py`, `acceptance_contract.py`,
  `change_evidence.py`, `memory.py`, `billing_ledger.py`, `token_usage.py`.
- Execution: `run-worker.sh` (`build_worker_cmd` at line 890; exports
  `RIG_JOB_ID`, `RIG_JOB_DIR`, `RIG_REPO`, `RIG_ROLE` at line 1180),
  `worker_launch.py`, `process_control.py`, `native_recovery.py`.
- Environment: nothing. `doctor.py` checks parent configs, binaries, models,
  optional components and a smoke run; no engine or container checks.
- Config: `harness.py.parse_harness()` is a hand-written TOML subset parser
  with known sections (`project`, `routing`, `orchestration`, `workers`,
  `queue`, `computer-use`, `browser-skill`). A new `[sandbox]` section must
  be added to that parser and `templates/harness.toml`, following the
  `[computer-use] enabled` exact-true pattern.

### 2.2 Findings the canvas does not spell out

1. **Binary name collision.** `rig` is the Bash dispatcher. A Go shell
   that is also called `rig` cannot ship beside it in Phase 1. Proposal:
   the Go binary is `rig-shell`, launched by a new `rig shell` (alias
   `rig ade`) subcommand; `bin/rig` stays the entry point until Phase 4
   decides whether Go takes over dispatch.
2. **Scope contradiction between canvas pages.** The Decisions board says
   "Rig renders state and evidence, never a code editor". The ADE screens
   page contains Code (read and edit any file), Search with replace, file
   Viewer, Terminal tab, Preview browser, Marketplace and Plans. This plan
   follows the Decisions board: Phase 1 ships workspace, parent pane, board
   and evidence only. The editor-like screens need an explicit decision
   before any of them enter the roadmap.
3. **The existing socket is the right seed, not the right API.**
   `ui_service.py` already handles concurrency, session pruning and a
   sandboxed collector thread. Its payloads are tuned for the status row and
   popup. The local API should be a new, versioned JSON-RPC surface in the
   same runtime directory (socket path length is already a solved problem
   there, see `ui_store.runtime_directory`), with the companion migrated
   onto it rather than extended in place.
4. **Stdlib-only is a constraint on the API.** `pyproject.toml` declares no
   dependencies and the tests run with the system `python3`. The JSON-RPC
   server stays `socketserver` + `json`; no MCP SDK, no third-party RPC.
5. **Ownership credentials must never cross the API.** Mutating API methods
   take the same `credentials_path` the MCP tools take (`.rig/jobs/<id>/
   owner-credentials.json`, mode 0600, validated by `admission.py`). The
   API never prints `owner_token`, same as `rig_job_launch`.
6. **Double sandbox is concrete, not hypothetical.** Codex already runs
   `-s workspace-write`; inside a container that sandbox must become
   `danger-full-access` or the container's own boundary is doubled. The
   provider has to own the per-worker permission flag, which today is
   hard-coded per CLI inside `build_worker_cmd`.
7. **Child MCP config resolves host paths.** `child_mcp.py` writes a
   config whose `command` points at the host `rig-mcp.sh`. Inside a
   container that path must resolve to the mounted `~/.rig` copy; this is
   the one place Phase 2 touches the handshake, and the handshake itself
   (`rig_job_inbox` first, protocol 1) stays unchanged.
8. **dc-cli changes are out of scope here.** Until dc-cli ships
   `schemaVersion`, Rig pins a dc-cli version and probes `dc --version`
   in `rig doctor`; JSON fixtures for tests are recorded from that pinned
   version, the same way `tests/mcp_test_support.stub_path()` already
   fakes worker binaries.
9. **`rig update` already has transactional install with rollback**
   (`scripts/runtime_update.py`). The dc-cli pin and later the Go binary
   ride that mechanism; no second updater.

## 3. Plan

Each phase ends on a named check, following the canvas. Sizes are relative
(S, M, L). Phase 0 needs no decision from anyone; it is the next work.

### Phase 0 · Foundation (size M, 5 PRs)

**0.1 CI and packaging (S).**
- Add `.github/workflows/tests.yml`: `python3 tests/run_tests.py` on Ubuntu
  and macOS for Python 3.11 and 3.12, plus `--syntax-only` as a fast job.
- Add `.github/workflows/shell.yml`: `bash -n` and `shellcheck` on
  `bin/rig`, `scripts/*.sh`, `install.sh`.
- Update `CONTRIBUTING.md` line 85 to describe the live workflows.
- `pyproject.toml`: keep `dependencies = []`; add a `[tool.rig]` block with
  the pinned dc-cli version (read by `doctor.py` and `install.sh`) so there
  is one place for the pin.
- Exit check: the test workflow is green on `main`.

**0.2 `[sandbox]` config and `SandboxProvider`, host backend only (S/M).**
- `templates/harness.toml`: `[sandbox] provider = "host"` with the
  `devcontainer` value documented but refused until Phase 2.
- `scripts/harness.py`: parse `[sandbox]`; unknown provider is an explicit
  error, not a silent host fallback.
- New package `scripts/sandbox/` with `__init__.py` (interface and
  `provider_for(repo)`), `host.py`. Interface, kept small on purpose:
  `status(repo) -> dict`, `wrap_worker_cmd(cmd, env, job) -> (cmd, env)`,
  `run_check(argv, cwd, env) -> CompletedProcess`, `evidence(job) -> dict`
  (host returns `{"provider": "host"}`), `forwarded_ports() -> list`.
- `run-worker.sh`: after `build_worker_cmd`, call
  `python3 scripts/sandbox_wrap.py` to transform `CMD` and env; host returns
  them unchanged. This is the only hook Phase 2 will need.
- `doctor.py`: a `sandbox` check reporting the configured provider.
- Tests: `tests/test_sandbox_host.py`, `tests/test_harness.py` additions,
  `tests/test_run_worker.py` dry runs asserting byte-identical commands.
- Exit check: every existing worker dry-run test passes unchanged with the
  provider in the path.

**0.3 Local API v1 (M).**
- New `scripts/local_api.py`: JSON-RPC 2.0 over a Unix socket at
  `<runtime dir>/api.sock`, newline-delimited, `{"rig_api": 1}` on every
  response. Methods mirror the parent MCP tools that the board and CLI need
  (`session`, `jobs.list`, `jobs.show`, `queue.list`, `queue.add`,
  `queue.cancel`, `workflows.list`, `workflows.show`, `job.allow`, `job.deny`,
  `job.cancel`, `job.message`, `doctor`, `events.subscribe`). Handlers call
  the same functions in `scripts/mcp_tools/*` that MCP dispatch calls, so
  behaviour cannot drift.
- `events.subscribe` streams the notices `ui_service.py` already derives
  (attention, finished, verified, stop progress, workflow states), so the
  shell never polls files.
- `rig api serve` and `rig api call METHOD [JSON]` subcommands in `bin/rig`
  for scripting and tests; `ui_service.ensure_service` starts both.
- `templates/runtime-compat.json`: add `local_api_protocol: 1`.
- Docs: `docs/local-api.md` with the method table generated by a script in
  the style of `scripts/generate_protocol.py`, with a test that the doc
  matches the registry (same pattern as `tests/test_protocol_docs.py`).
- Tests: `tests/test_local_api.py` with a fake repo, including: no method
  returns `owner_token`; mutations without `credentials_path` are refused
  with the admission error text; socket directory is mode 0700.
- Exit check: `rig api call session` and `rig session --json` return the
  same document.

**0.4 MCP tool profiles (S).**
- `scripts/mcp_tools/schemas.py`: tag every parent tool with a profile
  (`core` or `full`). `core` = session, pick, queue, job lifecycle, wait,
  check, accept, close, recovery guide, doctor (roughly 30 tools). `full`
  adds workflows, billing, benchmark, context, timeline, computer-use and
  BrowserSkill.
- `[mcp] profile = "core" | "full"` in `harness.toml` (default `full`, so
  nothing changes for existing repos); `RIG_MCP_PROFILE` env override for
  `rig-mcp.sh`.
- `listed_tools()` applies the profile after the existing CU and BSK gates.
  `CHILD_TOOLS` is untouched.
- Tests: `tests/test_mcp_modularization.py` additions asserting the child
  set is 8 regardless of profile and that every tool has exactly one
  profile.
- Exit check: `rig doctor` prints the active profile and the tool count.

**0.5 The curses board reads through the API (M).**
- `tui_runtime.load_snapshot` becomes an API client; the in-process
  loader stays as a fallback flagged `stale: true` only when the API is
  unreachable, and the board shows that state (the existing "refresh
  failed, snapshot stale" path in `docs/tui-design.md`).
- Actions in `BoardRuntime.submit` go through `job.*` and `queue.*` methods.
- Migrate `ui_popup.py` and the status row to the same client; retire the
  bespoke ops in `ui_service.py` once nothing calls them.
- Tests: existing TUI tests run against a fake API server; one test proves
  the board process never opens `.rig/jobs` directly (patch `open`/`Path`
  in `jobs.list_jobs` to raise).
- Exit check (canvas Phase 0 exit): the curses board reads state only
  through the API, and the contract tests pass.

### Phase 1 · ADE shell, Rig side (size L)

Needs decision D1 (Go shell lives in this repo) and dc-cli's public theme
package for a shared look; without the latter, Phase 1 proceeds with its
own theme and adopts the shared package later.

- `shell/` Go module (`go.mod`, Bubble Tea, Bubbles, Lip Gloss) producing
  `rig-shell`. Panes from the ADE mockup: workspace (left), parent (centre,
  a tmux pane Rig lays out), board (right, Jobs / Queue / Workflows tabs),
  evidence drawer (bottom), footer keys. Reads only the local API.
- `rig shell` subcommand: starts the API, creates the tmux layout
  (parent pane, shell pane, log pane), replaces F8/F9 popups when the shell
  is active. The companion status row stays for users without the shell.
- Workspace panel: `dc inspect --json`, `dc ls --json`, `dc forward
  --status --json`, `dc doctor --json` behind a Go `dc` client with
  recorded fixtures. Shows "no engine" cleanly when dc-cli is absent.
- `rig init --sandbox` and `rig env up | down | status` in `bin/rig`,
  calling `scripts/sandbox/devcontainer.py` for detection and lifecycle
  only (no worker execution yet). Generated config for repos with no
  devcontainer goes to `.rig/sandbox/override.json`; the repo's own
  `.devcontainer` is never written.
- `doctor.py`: engine, dc-cli version against the pin, workspace kind,
  each blocker carrying a `dc recover` diagnosis id when dc provides one.
- Release: `rig update` learns to fetch the `rig-shell` binary for the
  platform plus the pinned dc-cli; `runtime_update.py` transaction covers
  all three, with rollback.
- CI: Go build and test job; a smoke job that runs `rig shell --once`
  against a fake API and asserts the layout renders at 120×36.
- Exit check (canvas): `rig shell` in a repo opens the shell with the parent
  in its pane and the board live over the API. Jobs still run on the host.

### Phase 2 · Sandboxed workers (size L)

Needs decision D2 (credential mounting) and dc-cli's `exec --env`
passthrough (fallback: write env into a job-scoped file mounted into the
container and sourced by a wrapper).

- `scripts/sandbox/devcontainer.py` implements `wrap_worker_cmd`: `dc exec
  --no-start -- <agent cmd>` with `RIG_JOB_ID`, `RIG_JOB_DIR`, `RIG_REPO`,
  `RIG_ROLE` passed through; `--no-start` guarantees jobs never start
  containers. Per-worker permission flag moves out of `build_worker_cmd`
  into the provider: Codex gets `danger-full-access` inside a container
  (the container is the boundary), Claude stays `acceptEdits`, and the
  `adapters/codex/agents/*.toml` files gain a container variant.
- `child_mcp.py`: the written config's `command` resolves inside the
  container (mounted `~/.rig`, read-only). Handshake unchanged.
- `[sandbox]` grows `workers.<name>.credentials = "mount-ro" | "none"`,
  explicit per worker; a worker without an entry is refused in container
  mode, in line with "one explicit opt-in per worker".
- `acceptance_contract.py` / `rig_job_check`: when the provider is
  `devcontainer`, checks run through `run_check` → `dc actions run` after a
  one-time `dc actions trust` of the contract's exact argv; the trust hash
  joins the contract fingerprint. Evidence gains `container_id` and
  `image_digest`.
- `rig_job_ui_evidence`: URLs must come from `forwarded_ports()`; the
  receipt stores the forwarded port, not an arbitrary URL.
- Shell: evidence drawer v1 shows checks and container; `rig tui` job
  rows show the container short id.
- `doctor.py`: binary checks run inside the container too (`dc exec
  --no-start -- rig doctor --inside`), so a missing agent CLI in the image
  is a named blocker.
- Tests: provider tests replay recorded dc JSON; `test_run_worker.py` dry
  runs in container mode assert the exact `dc exec` argv; acceptance tests
  assert fingerprint change when the trust hash changes.
- Exit check (canvas): one job verified end to end inside a container, with
  container id and image digest in the evidence drawer.

### Phase 3 · Parallel isolation (size L)

- `worktrees.py` grows from discovery to lifecycle: `create(job)`,
  `remove(job)`, one container per worktree via `dc up --name` (fallback:
  override config with a per-job label).
- `admission.py`: reservations stay as the admission guard for host mode;
  in worktree mode overlapping writer scopes are admitted and the overlap
  check moves to the merge gate in acceptance (`rig_job_accept` refuses
  until the worktree merges cleanly onto the base). This is the largest
  core change in the plan and gets its own design note before code.
- Evidence drawer full: diff (from `change_evidence.py`), contract, ports.
- Fleet tab in the shell from `dc ls --json --all`; CPU and memory from
  `dc stats --json` shown as observed values only, in line with
  `docs/cost-accounting.md`.
- `recovery_guide.py`: environment faults get a `dc recover` action beside
  ownership recovery; never volume prune.
- Exit check (canvas): two writer nodes touching the same files run at once
  and merge after acceptance.

### Phase 4 · Native panes, platform (size L)

- Bubble Tea PTY panes for logs, `dc exec` shells and worker output; tmux
  becomes optional; decision on whether `rig-shell` takes over the `rig`
  name from `bin/rig`.
- Adapter SDK: `adapters/<cli>/adapter.json` (command template, permission
  flags per provider, MCP config writer, evidence decoder) with a
  conformance test suite, so a new worker CLI is an adapter directory
  alone. `run-worker.sh` shrinks to the launcher.
- Web board over the same local API (read-only first).
- Experiment: headless parent with Rig-rendered chat, behind a flag.
- Exit check (canvas): the shell runs without tmux, and a new worker ships
  as an adapter package alone.

## 4. Recommendations (decisions resolved)

These are the choices this plan assumes. Each can be reversed before the
phase that depends on it; none is needed for Phase 0.

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| D1 | Where the Go shell lives | `shell/` in this repo, one release tarball with the Python core | The shell is only useful against this repo's API; a separate repo would re-create the version-drift risk the canvas names. |
| D2 | Agent credentials inside containers | Read-only bind mount of the agent home, one explicit `credentials = "mount-ro"` per worker in `[sandbox]`; absent means refused in container mode | Matches the canvas and the existing per-worker opt-in style of `[workers]`. Keychain logins (Claude on macOS) are documented as host-only until a token path exists. |
| D3 | Editor-like screens on the canvas (Code, Search/replace, Viewer, Terminal, Preview, Marketplace, Plans) | Out of the roadmap | The Decisions board's own rule: Rig renders state and evidence, never an editor. Revisit only after Phase 3. |
| D4 | Does `rig-shell` take over the `rig` name | No. `bin/rig` stays the CLI; `rig shell` launches the UI | Every adapter, hook and doc calls `rig`; renaming buys nothing. Phase 4 may port the dispatcher to Go behind the same name, which is a separate decision. |
| D5 | dc-cli version policy | Exact pin in `pyproject.toml` `[tool.rig] dc_cli = "vX.Y.Z"`; `rig doctor` fails on mismatch; `rig update` moves both | A frozen contract is the only protection while dc JSON has no `schemaVersion`. |
| D6 | Adapter manifests | Pull forward from Phase 4 to Phase 2 as a minimal `adapters/<cli>/adapter.json` (command template, permission flags for `host` and `container`) | The container provider needs per-worker permission flags per mode; hard-coding them in `run-worker.sh` twice would be worse than extracting once. |
| D7 | MCP profile default | `full` for existing repos, `core` written by `rig init` for new ones | Parents lose nothing today; new users get a smaller tool list in the parent's context. |
| D8 | The curses board's future | Kept alive until Phase 4 as the no-Go, no-tmux fallback over the same API | Two thin surfaces over one API cost little; deleting the board before the shell reaches parity would regress users. |
| D9 | Local API transport | JSON-RPC 2.0, newline-delimited, over a Unix socket in the existing runtime directory, with a per-runtime 0600 token file sent on every request | Stdlib only, same path-length handling as `control.sock`, and a second user on the machine cannot drive the board. |

## 5. PR plan for the whole roadmap

Critical path: 0.1 → 0.3 → 0.5 → 1.1 → 1.3 → 2.1 → 2.3 → 3.1. Everything else
can run beside it. Each PR carries tests and a `docs/release-notes.md` entry.

### Phase 0 · Foundation (5 PRs)

| PR | Title | Depends on | Exit check |
| --- | --- | --- | --- |
| 0.1 | `ci: add test and shell workflows` | — | Workflows green on `main`. |
| 0.2 | `feat(sandbox): [sandbox] config and host SandboxProvider` | 0.1 | All worker dry-run tests pass with byte-identical argv. |
| 0.3 | `feat(api): local API v1 over a Unix socket` | 0.1 | `rig api call session` equals `rig session --json`. |
| 0.4 | `feat(mcp): tool profiles core and full` | — | `rig doctor` shows profile and tool count; child set still 8. |
| 0.5 | `refactor(tui): board and companion read through the local API` | 0.3 | Board process never opens `.rig/jobs`; companion ops retired. |

### Phase 1 · Shell and environment (6 PRs)

| PR | Title | Depends on | Exit check |
| --- | --- | --- | --- |
| 1.1 | `feat(shell): Go module with API client and board pane` | 0.5 | `rig-shell --once` renders Jobs/Queue/Workflows from a fake API at 120×36. |
| 1.2 | `feat(cli): rig shell lays out tmux with parent, shell and log panes` | 1.1 | `rig shell` in a repo shows the parent in its pane and the live board. |
| 1.3 | `feat(sandbox): devcontainer detection, rig init --sandbox, rig env up/down/status` | 0.2 | A repo with no config gets `.rig/sandbox/override.json`; `.devcontainer` untouched. |
| 1.4 | `feat(doctor): engine, dc-cli pin and workspace checks` | 1.3 | A mismatched dc-cli is a named blocker with a fix line. |
| 1.5 | `feat(shell): workspace panel from dc inspect/ls/forward/doctor` | 1.1, 1.3 | Panel shows kind, image, state, ports, doctor from recorded fixtures and live dc. |
| 1.6 | `feat(update): one tarball with rig-shell and the dc-cli pin` | 1.1 | `rig update` installs or rolls back all three together. |

### Phase 2 · Sandboxed workers (6 PRs)

| PR | Title | Depends on | Exit check |
| --- | --- | --- | --- |
| 2.1 | `refactor(adapters): adapter.json manifests drive build_worker_cmd` | 0.2 | Every CLI dry run is byte-identical to before. |
| 2.2 | `feat(sandbox): devcontainer provider wraps workers with dc exec --no-start` | 2.1, 1.3 | Container-mode dry runs assert the exact `dc exec` argv and env passthrough. |
| 2.3 | `feat(child-mcp): config resolves inside the container; credentials opt-in` | 2.2 | Child handshake succeeds from inside a container in the integration test. |
| 2.4 | `feat(acceptance): checks via dc actions; container id and image digest in evidence` | 2.2 | Fingerprint changes when the trust hash changes; evidence carries both ids. |
| 2.5 | `feat(ui-evidence): URLs only from forwarded ports` | 2.2 | A receipt citing an unforwarded URL is refused. |
| 2.6 | `feat(shell): evidence drawer v1 and container ids on job rows` | 1.5, 2.4 | One job verified end to end inside a container, visible in the drawer. |

### Phase 3 · Parallel isolation (5 PRs)

| PR | Title | Depends on | Exit check |
| --- | --- | --- | --- |
| 3.0 | `docs: design note for reservations at the merge gate` | — | Reviewed before 3.2 starts. |
| 3.1 | `feat(worktrees): create and remove per job; container per worktree` | 2.2 | A job runs in its own worktree and container; cleanup on close. |
| 3.2 | `feat(admission): worktree mode admits overlapping writers; accept merges` | 3.0, 3.1 | Two writers on the same files run at once and merge after acceptance. |
| 3.3 | `feat(shell): evidence drawer full and Fleet tab` | 2.6, 3.1 | Diff, contract, ports and cross-repo containers shown from observed data. |
| 3.4 | `feat(recovery): dc recover as an environment recovery action` | 1.4 | Engine-down and port-held faults get one audited action. |

### Phase 4 · Native panes and platform (5 PRs)

| PR | Title | Depends on | Exit check |
| --- | --- | --- | --- |
| 4.1 | `feat(shell): PTY panes for logs, dc exec shells and worker output` | 1.2 | Shell runs without tmux for plain-text streams. |
| 4.2 | `feat(adapters): conformance suite; run-worker.sh becomes the launcher` | 2.1 | A new worker ships as an adapter directory alone. |
| 4.3 | `feat(web): read-only board over the local API` | 0.3 | Same session document rendered in a browser. |
| 4.4 | `exp(shell): headless parent with Rig-rendered chat` | 4.1 | Behind a flag; no default change. |
| 4.5 | `chore: decide whether bin/rig dispatch moves to Go` | 4.1 | Decision recorded; no code required. |

## 6. Test and release strategy

- Python: `tests/run_tests.py` on 3.11 and 3.12, Ubuntu and macOS. Dry-run
  byte equality for every worker CLI in host and container mode.
- dc-cli: recorded JSON fixtures from the pinned version under
  `tests/fixtures/dc/`, replayed through `mcp_test_support.stub_path()`
  style fake binaries; one opt-in live job (`RIG_TEST_LIVE_DC=1`) for
  maintainers with Docker.
- Local API: the method registry generates `docs/local-api.md`; a test
  fails when the doc and the registry differ, as `test_protocol_docs.py`
  does for the agent protocol today.
- Go: `go test` with golden renders of each pane at 120×36 and 80×24;
  `go vet` and `staticcheck` in CI.
- Release: one tarball per platform holding `bin/`, `scripts/`, `adapters/`,
  `rig-shell` and the dc-cli pin; `rig update` keeps its transactional
  install and rollback; `rig doctor` checks that the three versions match.

## 7. What not to do

- No editor, file tree, git UI, marketplace or billing screens in the
  shell; the canvas's own Decisions board forbids them.
- No estimated progress, ETA or inferred cost anywhere new; observed
  values only, as `docs/cost-accounting.md` already requires.
- No direct `docker` calls from Rig; dc-cli owns engine state.
- No worker ever starts a container; `rig env up` is the only path.
- No change to the child rules: inbox first, never spawn children, never
  browser or computer-use tools.

## 8. Risks specific to this codebase

- The hand-written TOML parser in `harness.py` must learn `[sandbox]` and
  `[mcp]` sections and nested `workers.<name>` keys; test the parser before
  anything reads the values.
- `run-worker.sh` is Bash with per-CLI branches; the provider hook must
  wrap the final argv without re-quoting it. The dry-run tests are the
  guard; add a byte-equality assertion for every CLI.
- Two listeners (`control.sock` and `api.sock`) during 0.3 to 0.5. Keep
  both under the same runtime directory and the same `ensure_service`
  path so the companion cannot start one without the other.
- The 1,990-case suite runs in minutes on CI; split the shell and Go jobs
  from the Python job so a Go failure does not hide a Python one.
