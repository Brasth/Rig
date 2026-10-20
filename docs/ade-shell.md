# The Rig shell, workspace and environment

An optional terminal layout for working with a parent agent while Rig runs workers. It adds no new way to run work: workers still start through the parent and the same admission, ownership and acceptance rules apply.

## What you get

| Command | What it does |
| --- | --- |
| `rig shell` | One tmux window: the parent CLI unchanged in its own pane, the board (`rig-shell`) beside it, and a log pane following the most relevant job |
| `rig shell print`, `rig shell kill` | Show the tmux commands it would run, or remove this repository's session |
| `rig shell build` | Build `rig-shell` from a Rig clone with Go 1.24+ (no project needed) |
| `rig init --sandbox` | Detect the workspace and, for a folder with no devcontainer, store dc-cli's generated config under `.rig/sandbox/override.json`; `--write-devcontainer` turns it into `.devcontainer/devcontainer.json` |
| `rig env up`, `down`, `status`, `ports [--json]` | Start or stop the workspace container, show what dc-cli reports, and list the ports it forwards |
| `rig env recover [--apply ID]` | Show the one next step `dc recover` proposes; apply it only by naming the plan id, and only if Rig allows that action (see [Environment recovery](#environment-recovery)) |
| `rig worktrees plan, create, commit, remove --job ID` | Per-job worktrees, below |
| `rig doctor [--workspace]` | Adds `workspace`, `dc-cli` and `rig-shell` checks. `--workspace` also runs dc-cli's own diagnosis |
| `rig api serve`, `ensure`, `call`, `methods` | The local API behind all of this ([reference](local-api.md)) |

`rig shell` needs tmux 3.3+, the parent CLI on `PATH` (`codex`, `grok`, `claude`, `opencode`, `omp`, `pi` or `agy`), and a built `rig-shell`. It uses its own tmux server unless you are already inside tmux, never binds the F8/F9 companion popups, and reuses the session if one exists for the repository.

## The board

Tabs: Jobs, Queue, Workflows, Workspace, Fleet. Every state shows a glyph and a word, and labels, ordering and counts come from the same code as `rig tui`, so the two never disagree.

| Key | Action |
| --- | --- |
| `Tab`, `Shift-Tab` | Next or previous tab |
| `j`, `k` | Move, or scroll on the Workspace and Fleet tabs and in the evidence drawer |
| `Enter`, `Esc` | Open or close details on narrow terminals |
| `a`, `d` | Allow or deny the selected worker prompt |
| `v` | Evidence drawer for the selected job (`Esc` closes it) |
| `x` | Stop the selected job or queue item, after a confirmation |
| `R` | Workspace tab: apply the proposed environment fix, after a confirmation, when Rig allows it |
| `e` | Park a task in the queue (`Enter` saves, `Esc` discards; a failed save keeps the draft) |
| `r`, `q` | Refresh, quit |

Pasted text is never run as commands. `RIG_TUI_ASCII=1`, `RIG_TUI_CJK=1` or a non-UTF locale selects ASCII glyphs. If the API server is not running the board starts one and shows "refresh failed" with the snapshot age if it cannot reach it, keeping the last data on screen.

## Evidence drawer, Fleet and recovery

**Evidence drawer** (`v` on a job): where the job ran (host, container with name and state, worktree with branch, base and merge state), what changed (file list with added and removed lines from git for worktree jobs, the recorded changed paths otherwise, files outside the declared scope flagged), the acceptance contract with each criterion's outcome, the checks with their exit codes and where they ran, and the forwarded ports observed now. Everything is read from records Rig wrote or from git and dc-cli as they are at that moment.

**Fleet tab**: every container dc-cli can see on the engine (`dc ls --all`), this repository's first, then other workspaces with their folder. Running ones show CPU and memory as `dc stats` reports them, and the engine's own cpus and memory. These are observed values only: Rig sums nothing, estimates nothing and never turns them into a cost ([cost accounting](cost-accounting.md)).

### Environment recovery

When `dc recover` has a next step, the Workspace tab shows it with the command to run. Rig applies a step itself only when the owner confirms that exact plan id and its action is one of `colima_start`, `create_nets` or `take_ports`, which covers an engine that is down and a port held by one of dc-cli's own containers. Everything else dc-cli offers (installing or launching Docker Desktop, anything needing `sudo`, switching Docker contexts, stopping another engine, pruning, starting a sandbox for a folder with no config) is shown as text for you to run; Rig never prunes. Each attempt, applied, failed or refused, adds one line to `.rig/recovery/environment.jsonl` with who asked, the plan, the action and the outcome. A plan that changed between your confirmation and the apply is refused.

## Parallel writers in worktrees

Off by default. `[worktrees] mode = "per-job"` in `.rig/harness.toml` runs each write job in its own git worktree (`.rig/worktrees/<job>`, branch `rig/<job>`, made from the base checkout's current commit) while all job state stays in the main `.rig`. Two writers may then be admitted on the same files at the same time, because neither edits the base checkout. The overlap is decided when the parent accepts a job:

1. the job's branch must change only the files it declared;
2. it must merge onto the base branch as it is now without conflict (`git merge-tree`, git 2.38+; nothing is touched to check);
3. the base checkout must have no staged changes and no uncommitted change in a file the merge touches.

When all three hold, `rig_job_accept` with `next = complete` merges the branch (`--no-ff`), records the merge commit, removes the worktree and deletes the branch. A refused gate names the reason and the paths (`merge_gate:conflict`, `out_of_scope`, `base_dirty`, `base_moved`) and leaves the job, its worktree and its reservation as they were so you can resolve it and accept again. A job that changed nothing is accepted without a merge. Rejecting merges nothing; the worktree is kept until `rig worktrees remove --job ID` (it refuses while a process is running there and keeps an unmerged branch unless `--discard`).

Admission still refuses writers that clash on a resource (a port, a URL), exceed the slot limits, or run outside worktree mode against a worktree writer's files. A reviewer of a worktree job reads that job's worktree until it is merged. With `[worktrees] container = "per-job"` and `[sandbox] provider = "devcontainer"`, the launcher runs `dc up <worktree>` for each job and the worker runs in that container; the project's devcontainer config must mount the main repository (its `.rig` state and its git directory) at its own path as well as the worktree, and the image needs `git` (the probe checks that `git` works there). dc-cli leaves a stopped container behind when a job ends; Rig never removes containers, so clear them with `dc prune`. `rig doctor` reports the mode and checks the git version.

## Workspace and dc-cli

Rig never calls docker. dc-cli owns the container and Rig runs it as a subprocess. Rig never edits your own `.devcontainer`. `dc up` refuses a folder with no devcontainer or compose file, so for such a folder `rig env up` stops and tells you to run `rig env init --write-devcontainer`, which writes a new `.devcontainer/devcontainer.json` (never over an existing one) with the repository and runtime mounted at their own paths. Worker execution inside the container is a separate opt-in, described below.

**dc-cli contract status.** Checked against dc-cli 0.23.0 with devcontainer CLI 0.89.0 on a real Docker engine (recorded outputs in `tests/fixtures/dc/`): `exec --no-start [workspace] -- CMD` (exit code and arguments pass through), `ls --json` (`id`, `name`, `status`, `local_folder`, `kind`; no image or digest, so evidence leaves them empty), `doctor --json` (checks with `id`, `status` ok, warning or blocker, `remediation`), `forward --status` (text only, no `--json`), and `try --print` (prints the path of the config it generated). Fields are still read tolerantly. `templates/dc-cli.json` holds the version pin; it is empty, which means any dc-cli is accepted and `rig doctor` says "unpinned". Setting it makes a different version fail `rig doctor --workspace`.

## Building and versions

`rig setup` builds `rig-shell` when Go is installed (`--shell-binary` insists, `--no-shell-binary` skips) and stamps it with the source commit. `rig update` does not carry shell sources, so after updating, `rig doctor` reports a mismatch between the binary and the installed core and says to run `rig shell build` from a clone (`RIG_SRC=/path/to/Rig rig shell build`).

Developing the shell: see `shell/README.md`.

## Running workers in the container

Off by default. Opt in per repository, then check it before relying on it:

```toml
[sandbox]
provider = "devcontainer"

[sandbox.workers]      # an explicit choice for every worker that should run there
claude = "mount-ro"    # its login directory is mounted read-only into the container
codex = "none"         # it needs no mounted login
```

```sh
rig init --sandbox     # generate the container config if the project has none
rig env up
rig doctor --workspace # per enabled worker: can it run in the container?
```

**Which workers.** Only `claude` and `codex`, because their MCP wiring is scoped to the job directory. Every other worker is refused with the reason (for example, it edits global host configuration a container cannot share). A worker with no `[sandbox.workers]` entry is refused and told what to add.

**One rule: same paths.** Every absolute path Rig puts in a command (repository, job directory, the Rig runtime, your home) must exist at the same path inside the container. Rig does not translate paths. `rig env init --write-devcontainer` writes a config that mounts the repository and the Rig runtime that way (runtime read-only), plus the login directories of workers set to `mount-ro`. If you use your own devcontainer config Rig does not change it, so it must mount those paths itself. dc-cli reuses an existing container, so changed mounts only apply to a recreated container. Before each live launch Rig probes the container and refuses with one specific reason when something is missing: repository or runtime not visible, no `python3`, no `setsid`, the agent CLI not installed, or no credentials mounted. Nothing here installs the agent CLIs into your image.

**Never the host.** A worker runs only through `dc exec --no-start`, so a job cannot start a container. If the container is not reachable the launch is refused; the worker is never run on the host as a fallback. The container sees only `HOME` and the job's own `RIG_*` variables. Owner tokens and the rest of your environment are not forwarded, and API keys are not forwarded either.

**Stopping.** Killing the `dc exec` client on the host does not stop a process inside the container, so the worker runs under a small supervisor that kills its whole process group when a stop file appears or when the host's heartbeat stops changing for 15 seconds. A stop waits for that, so files are not released while the agent still runs. If the engine hangs and the supervisor cannot act, the heartbeat lease still ends the agent, so it can outlive the host process for roughly 15 seconds at most; Rig cannot do better from the host.

**Checks.** Acceptance checks run through the same container with the exact argv, so a job is verified where it ran, and under the same stop lease. A missing executable fails the check with exit 127 instead of raising. Check records and `sandbox.json` in the job directory record where each ran; the board's detail pane shows the container, image and digest when dc-cli reports them, and says plainly when it does not.

**Ports.** In container mode a localhost URL on a port dc-cli does not forward would reach the host, not the app in the container, so the browser lane (`rig_bsk_navigate`) and named Chrome profile captures refuse it and list the forwarded ports. If dc-cli cannot list them the URL is refused. `rig env ports` shows them.

**Limits.** Not verified against a real dc-cli, Docker engine, or the real `claude` and `codex` CLIs inside a container. The tests run the real supervisor, probe and launcher against a fake `dc` whose `exec` runs locally, which proves Rig's behavior, not dc-cli's. Read-only credential mounts may break an agent that must write to its own configuration directory. Rig does not use `dc actions`, so no trust hash joins the acceptance contract.

## Verifying against a real engine

`tests/test_live_dc.py` runs a job, a cancel, a check and the port guard against a real dc-cli and Docker engine with a stand-in `claude` (it does the real MCP handshake; it is not a model). It is skipped unless `RIG_TEST_LIVE_DC=1`. Prepare a devcontainer workspace whose config mounts the repository, the Rig checkout (read-only), a home directory with `.claude` (read-only) at their own paths and the stand-in at `/usr/local/bin/claude`, start it with `dc up`, then set `RIG_DC_BIN`, `RIG_TEST_LIVE_REPO` and `RIG_TEST_LIVE_HOME`. A run with the real `claude` or `codex` needs your credentials and is not covered.
