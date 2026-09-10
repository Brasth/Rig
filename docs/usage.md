# Rig usage

Landing page: [README](../README.md).

## What Rig is

The point is to stop you being the tired reviewer of one agent. You talk to a **parent** (intended: Codex on Astra). The parent assigns work to a **child**, then checks and sends feedback — allow/deny, another prompt — the loop you used to do yourself.

You stay in **one parent**: Codex, Grok, OpenCode, OMP, Pi, or agy. You talk to that parent. The parent decides (via `rig pick`) whether to do the work itself or spawn a **worker**. Never spawn Astra, Sol, or Fable as a child.

- **Parent** (you open this): Codex, Grok, OpenCode, OMP, Pi, or agy. It plans, checks, talks to you, does vision / computer-use / chrome-profile, and watches jobs. It does **not** sit on write/review/SSH when a worker is effective.
- **Workers** (the parent may spawn these): Grok, Claude Code, Cursor CLI, OpenCode, OMP, Pi, agy, Codex. They write code, fix bugs, review, SSH/debug, and gather facts.
- **Never the parent:** Claude Code and Cursor. Opening those CLIs does not make them the Rig parent. The Antigravity IDE/GUI is not a parent or worker; the CLI is `agy`.
- **Live parent** is whichever Codex, Grok, OpenCode, OMP, Pi, or agy you actually opened (`rig status`). The `parent =` key in `.rig/harness.toml` is only the preferred default (`rig use grok|codex|opencode|omp|pi|agy`). Opening the CLI is what makes it live.
- **Missing binary is not a failure.** That worker is off. The parent uses a cheaper same-CLI worker. That is success.

**You need** one parent CLI: Codex, Grok, OpenCode, OMP, Pi, or agy. Optional worker binaries: `grok`, `claude`, `cursor-agent`, `codex`, `opencode`, `omp`, `pi`, `agy`.

Grok Bot.app and Cursor.app are GUIs, **not** spawnable workers. `rig doctor` may list them under **Apps (not spawnable)** as a hint. The Cursor worker binary is `cursor-agent`, not the GUI.

Codex CLI and Grok CLI are installed from those products (this guide does not pin their installer URLs). Optional worker CLIs:

```bash
curl https://cursor.com/install -fsS | bash
curl -fsSL https://opencode.ai/install | bash
curl -fsSL https://omp.sh/install | sh
npm install -g @earendil-works/pi-coding-agent
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Brasth/Rig/main/install.sh | bash
```

That one line is the install. `| bash` is required. No GitHub login.

What that does:

- No GitHub login. `curl` pipes `install.sh` into bash.
- `install.sh` clones `https://github.com/Brasth/Rig.git` over HTTPS into a **temp** dir (needs `git`; if HTTPS clone fails and `gh` is logged in, it tries `gh repo clone`).
- Copies bin, scripts, skills, adapters, and templates into `~/.rig`.
- Symlinks `~/.local/bin/rig` → `~/.rig/bin/rig`.
- Runs `rig setup`.
- Deletes the temp clone. There is **no local clone to keep**.

From a checkout you already have: `./install.sh` (same copy + `rig setup`, no clone). That is the **dev** path; it copies the local tree, not GitHub `main`.

Update an existing machine: `rig update`. That fetches GitHub `main` through the same `install.sh` (not your checkout). The same `curl | bash` still works (idempotent). An older `rig` without `update` still needs the curl once. It updates the skill and scripts. It does **not** overwrite project `.rig/harness.toml` or `.rig/MEMORY.md`. It already runs `rig setup`.

**`rig setup` writes:**

- `~/.rig` (bin, scripts, skills, adapters, templates)
- Skill links in `~/.agents/skills`, `~/.grok/skills`, `~/.codex/skills`, `~/.config/opencode/skill`, `~/.omp/agent/skills`, `~/.pi/agent/skills`, `~/.gemini/antigravity-cli/skills` (`delegate-harness` and `rig-jobs`)
- Codex agent files under `~/.codex/agents` when they are Rig agents
- Grok bottom status line (`[ui.status_line]` → `rig-statusline`; restart Grok once)
- `[mcp_servers.rig]` in `~/.grok/config.toml` and `~/.codex/config.toml` **even if those files did not exist**
- `mcp.rig` in `~/.config/opencode/opencode.json` (or `mcp.servers.rig` if that map already exists)
- `mcpServers.rig` in `~/.omp/mcp.json`, `~/.pi/agent/mcp.json`, and `~/.gemini/config/mcp_config.json`
- Codex sandbox writable roots so Grok/Claude/Cursor/OpenCode/OMP/Pi/agy children can write sessions (`[sandbox_workspace_write]`)

Setup does **not** write project `mcp.json` / `opencode.json`. Setup does **not** add `pi-mcp-adapter` to Pi `settings.json`.

Success print:

```text
install ok  rig -> $HOME/.local/bin/rig
next: cd your-repo && rig init && rig doctor
```

If PATH is missing `~/.local/bin`, the installer also prints:

```text
add to PATH: export PATH="$HOME/.local/bin:$PATH"
```

Then persist it for zsh:

```bash
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Confirm the binary:

```bash
which rig
```

That must print `$HOME/.local/bin/rig` (for example `/Users/you/.local/bin/rig`). If it is empty, PATH is still wrong.

## First-time machine

Install already ran `rig setup`. Re-run `rig setup` after you update Rig (`rig update` or the curl install does this for you).

1. Fully quit Grok, Codex, OpenCode, OMP, Pi, and/or agy **once** so MCP tools load (quit the apps, then reopen).
2. Run `rig doctor`. MCP lines should show `[mcp_servers.rig]` for grok and/or codex, plus OpenCode/OMP/Pi/agy JSON MCP when those files exist.
3. After setup, Grok gets a **bottom status line**. Restart Grok once if you do not see it.
4. Pi `/rig` also needs `pi install npm:pi-mcp-adapter` (setup writes `mcp.json` but does not install the package).

### What `rig doctor` should look like

`rig doctor` is the health check. Walk it top to bottom. “Good” looks like this (paths will be yours):

```text
Rig doctor
  RIG_HOME: /Users/you/.rig
  repo:     /Users/you/your-repo
  harness:  /Users/you/your-repo/.rig/harness.toml
  version:  v1 abc1234
  update:   current

Parent
  live:      grok
  preferred: codex

Workers
  grok    flag=true  bin=/usr/local/bin/grok          effective=off (is live parent)
  claude  flag=true  bin=/usr/local/bin/claude        effective=on
  codex   flag=false bin=/usr/local/bin/codex         effective=off (flag)
  cursor  flag=false bin=(missing)                    effective=off (flag)

Apps (not spawnable)
  grok-bot /Applications/Grok Bot.app
  cursor   /Applications/Cursor.app

Skill
  project: /Users/you/your-repo/.agents/skills/delegate-harness/SKILL.md
  ~/.agents/skills/delegate-harness -> /Users/you/.rig/skills/delegate-harness
  ...

Scripts
  run-worker: /Users/you/.rig/scripts/run-worker.sh (ok)

MCP
  grok:     /Users/you/.grok/config.toml [mcp_servers.rig]  (fully quit grok once to load tools)
  codex:    /Users/you/.codex/config.toml [mcp_servers.rig]  (fully quit codex once to load tools)
  opencode: /Users/you/.config/opencode/opencode.json mcp.rig  (fully quit opencode once to load tools)
  omp:      /Users/you/.omp/mcp.json mcpServers.rig  (fully quit omp once to load tools)
  pi:       /Users/you/.pi/agent/mcp.json mcpServers.rig  (fully quit pi once to load tools)
  agy:      /Users/you/.gemini/config/mcp_config.json mcpServers.rig  (fully quit agy once to load tools)
```

How to read each section:

| Section | Good | Bad |
| --- | --- | --- |
| **RIG_HOME** | `$HOME/.rig` | empty / missing — `rig update` or re-run the curl install or `rig setup` |
| **repo** | the git repo you `cd`’d into | wrong directory |
| **harness** | `.rig/harness.toml` exists | `(missing — run: rig init)` |
| **version** | `v1 <sha>` from `~/.rig/VERSION` | `(unknown — run: rig update)` |
| **update** | `current` | `behind main — run: rig update` (omitted if offline or `RIG_SKIP_UPDATE_CHECK`) |
| **Parent live** | `codex`, `grok`, `opencode`, `omp`, `pi`, or `agy` when you are inside that CLI; `(none)` in a plain terminal is normal | you expected a parent but opened Claude/Cursor |
| **Parent preferred** | `codex`, `grok`, `opencode`, `omp`, `pi`, or `agy` from `rig use` | — |
| **Workers** | the ones you want show `effective=on` | see reasons below |
| **Apps** | GUIs listed or `(missing)` | do **not** treat these as workers |
| **Skill** | project `SKILL.md` plus symlinks under `~/.agents`, `~/.grok`, `~/.codex`, `~/.config/opencode/skill`, `~/.omp/agent/skills`, `~/.pi/agent/skills`, `~/.gemini/antigravity-cli/skills` | `(missing — run: rig init)` or `(missing — run: rig setup)` |
| **Scripts** | `run-worker: … (ok)` | missing — `rig setup` again; Rig itself is broken |
| **Model catalogs** | `opencode: N models` on a fresh cache hit (`~/.rig/cache/model-catalogs.json`) | omitted when cache is missing or stale — doctor does not wait on the four CLIs |
| **MCP** | `[mcp_servers.rig]` on grok/codex plus JSON MCP on OpenCode/OMP/Pi/agy | `missing — run: rig setup`, then fully quit the app; Pi also needs `pi-mcp-adapter` |
| **Watch** | reminder of `rig tui` / `rig jobs` / `/rig` in Grok, Codex, OpenCode, OMP, Pi, or agy | — |

**`effective=off` reasons** (printed in parentheses):

- `flag` — `[workers].<name>` is `false`. Turn on with `rig workers <name>=on`.
- `no binary` — flag is true but the CLI is not on PATH (`grok`, `claude`, `codex`, `cursor-agent`, `agy`).
- `is live parent` — you opened that CLI as the parent, so it cannot also be a child this session (typical: Grok parent → Grok child off; OpenCode parent → OpenCode child off).

A worker is **effective** only when: flag true **and** binary on PATH **and** not the live parent.

## Per-project setup

```bash
cd your-repo
rig init
rig doctor
```

`rig init` is per repo. Do this in every project you want Rig to manage.

**Files:**

| Path | What happens |
| --- | --- |
| `.rig/harness.toml` | Agent config. **Kept** if it already exists. |
| `.rig/MEMORY.md` | Durable standing facts. **Kept** if it exists. |
| `.rig/STATE.md` | Last job snapshot (overwritten each run). **Kept** if it exists on first write; later runs overwrite contents. |
| `.agents/skills/delegate-harness/SKILL.md` | **Refreshed every init.** |
| `.agents/skills/rig-jobs/SKILL.md` | **Refreshed every init.** |
| `AGENTS.md` | Inserts a **MUST use Rig** block at the **top** (markers `<!-- rig:start -->` / `<!-- rig:end -->`). Never replaces the rest of the file. `--no-patch-agents` skips. |
| `CLAUDE.md` | Only with `--patch-claude`, and only if the file is **missing**. |
| `.gitignore` | If the file exists, appends `.rig/jobs/` and `.rig/thread` when those lines are not already there. |

**New harness only:** Grok / Claude / Cursor / OpenCode / OMP / Pi / agy are turned **on** if that CLI is on PATH. Codex stays **off** (preferred parent). **Existing harness flags are never flipped.** Missing worker keys are appended as `false` → enable later with `rig workers <name>=on`.

Open a **new** parent thread after init. An old Grok/Codex/OpenCode/OMP/Pi/agy session will not pick up `AGENTS.md` or skills.

Do **not** use `rig run` for normal work. Just prompt in the parent CLI.

## Configure agents

Edit `.rig/harness.toml` or use the `rig` commands below. Real template shape:

```toml
parent = "codex"
# parent = "grok"
# parent = "opencode"
# parent = "omp"
# parent = "pi"
# parent = "agy"

[workers]
codex = false
grok = true
claude = true
cursor = false
opencode = false
omp = false
pi = false
agy = false
```

**Each key:**

- **`parent`** — preferred default only (`"codex"`, `"grok"`, `"opencode"`, `"omp"`, `"pi"`, or `"agy"`). Live parent is whichever CLI you opened (`rig status`). `rig use grok` / `codex` / `opencode` / `omp` / `pi` / `agy` writes this key; you still have to **open** that CLI. Switching the key does not move an already-open session. Claude and Cursor are never `rig use` targets. Parent **model** is the CLI’s model; worker models come from `rig pick`. Never spawn Sol, Astra, or Fable as a child.
- **`[workers].*`** — allow-list, not “install for me”. `true` means “this CLI may be spawned **if** its binary is on PATH and it is not the live parent”. Commands:

  ```bash
  rig workers grok=on|off claude=on|off codex=on|off cursor=on|off opencode=on|off omp=on|off pi=on|off agy=on|off
  ```

Effective worker = flag `true` **and** binary on PATH **and** not live parent. Check with `rig doctor` / `rig status`. `grok = false` turns off grok as a child. Open Grok and you still get native Grok. Open Pi with grok off and pick must stay Pi.

**Binaries:**

| Worker | Binary | Not a worker |
| --- | --- | --- |
| Grok | `grok` | Grok Bot.app |
| Claude Code | `claude` | — |
| Codex | `codex` | — |
| Cursor | `cursor-agent` | Cursor.app GUI |
| OpenCode | `opencode` | — |
| OMP | `omp` | — |
| Pi | `pi` | another unrelated `pi` on PATH |
| Antigravity | `agy` | Antigravity IDE/GUI |

### Examples

**Enable Cursor as a worker**

1. Install the Cursor CLI (GUI is not enough):

   ```bash
   curl https://cursor.com/install -fsS | bash
   ```

2. Allow it in this repo:

   ```bash
   rig workers cursor=on
   ```

3. `rig doctor` until `cursor` shows `effective=on`. Cursor is never the parent, so "is live parent" will not apply to it.

**Enable Claude as a worker**

1. Install the `claude` binary (no URL here — use Anthropic’s CLI install).
2. `rig workers claude=on`
3. `rig doctor` until `claude` is `effective=on` (unless you opened Claude as… you cannot; Claude is never the parent).

**Enable OpenCode, OMP, Pi, or agy as a worker**

Existing project flags stay off until you turn them on:

```bash
rig workers opencode=on
# or: rig workers omp=on
# or: rig workers pi=on
# or: rig workers agy=on
rig doctor
```

If both OMP and Pi are on as workers of a *different* parent, pick uses **OMP** (same family; OMP is the Pi fork). They can be the parent when you open that CLI (`rig use omp` / `rig use pi` / `rig use agy`, then open it). OpenCode `--auto` is required for headless spawn (no TTY). OMP uses `--approval-mode write`, not `--auto-approve`. agy uses print-mode JSON with `--mode accept-edits`; it does **not** use `--dangerously-skip-permissions`.

Do **not** enable Claude on every project. You choose. Existing project flags stay until you run `rig workers`.

**Prefer Grok as parent**

```bash
rig use grok
```

Then **open Grok** in the repo. Grok-as-parent means the Grok **child** is off for that session (`effective=off (is live parent)`). Implement work then goes to Claude if effective, else cheap same-CLI Grok.

**Prefer OpenCode, OMP, Pi, or agy as parent**

```bash
rig use opencode
# or: rig use omp
# or: rig use pi
# or: rig use agy
```

Then **open that CLI** in the repo. That CLI is off as a child. Implement: Grok child if effective, else Claude, else cheap same-CLI with that CLI's pins. Fully quit once after setup so MCP `/rig` loads. Pi also needs `pi install npm:pi-mcp-adapter`. When live parent is agy, do not use nested agy `/agent` dispatch for coding; use `rig pick`.

**Prefer Codex as parent**

```bash
rig use codex
```

Then open Codex. Parent model is the CLI’s model. Worker models come from `rig pick`. Never spawn Sol, Astra, or Fable as a child.

## Daily use

Numbered path for a human:

1. `cd` to the repo. Confirm `which rig` and that `.rig/harness.toml` exists (`rig init` if not).
2. Open **Codex, Grok, OpenCode, OMP, Pi, or agy** in that repo. After init or setup, use a **new** thread.
3. In a new thread, the parent’s first commands are `rig memory` then `rig jobs` then `rig status`. (The parent does this; you can run them in a terminal too.)
4. Type a normal prompt. Do not pick a model. Do not run `rig run`.

   Examples:

   - `Implement pagination on the jobs list.`
   - `tests/test_cli.py is failing — fix it.`
   - `Review the diff I just staged.`
   - `SSH to the box and collect the app logs from the last deploy.`

5. The parent runs `rig pick --case "<task>"` and spawns if needed. It does **not** ask you which model.
6. Watch the child: another terminal `rig tui` or `rig jobs`, or type `/rig` in Grok, Codex, OpenCode, OMP, Pi, or agy. Grok also gets a bottom status line after setup (restart Grok once).
7. `rig jobs` is a table. Columns: **STATUS AGENT ROLE JOB TASK**. Example:

   ```text
   STATUS    AGENT    ROLE       JOB                              TASK
   running   grok     worker     20260909T032405Z-82424          Implement pagination
             model  grok-4.6   reasoning high
             log    rig job log 20260909T032405Z-82424 -f
   ```

8. If a Claude child is `ask`: the **parent** answers `rig job allow <id>` or `rig job deny <id>` (TUI `y` / `n`). Never kill that job. Never spawn another worker because Claude asked. The same child continues after you allow.
9. Jobs and MEMORY are **this repo**, not the chat. A new thread still sees `.rig/jobs`. Running children keep going.

**Parent keeps:** plan, check, vision, computer-use, chrome-profile, talk to you.

**Workers:** write code, fix, review, SSH/debug, gather facts.

If **this** CLI cannot do computer-use or chrome-profile, the parent spawns a worker. It does **not** ask you.

### Routing

| Case | Who |
| --- | --- |
| Plan / vision / computer-use / chrome-profile | parent (`rig pick stay`) unless this CLI cannot do it, then spawn |
| Small locate / trace / gather | cheap same-CLI |
| Implement / SSH / fix | Grok child if Grok is **effective**; if Grok/OpenCode/OMP/Pi/agy is the live parent (or Grok off) → Claude Code if effective, else cheap same-CLI. Cursor/OpenCode/OMP/Pi/agy/Codex children last resort (not just because the CLI is on PATH) |
| Review | different vendor than the writer |
| No extra CLIs | cheap same-CLI. Record it. That is success |

Pin **full** model IDs (aliases drift). Codex / Grok / Claude / Cursor stay static pins. OpenCode / OMP / Pi / agy pins are **preferences**: `rig pick` and `run-worker.sh` list models from that CLI and pick one that exists. Catalog cache: `~/.rig/cache/model-catalogs.json` (TTL ~1 hour). `RIG_REFRESH_MODELS=1` refreshes. `RIG_SKIP_MODEL_CATALOG=1` keeps the static pin. Never Sol / Astra / Fable, even if the catalog lists them.

- Claude: `claude-haiku-4-5-20251001` cheap, `claude-sonnet-5` implement, `claude-opus-5` hard/review
- Cursor: `composer-2.5-fast` cheap, `composer-2.5` implement, `cursor-grok-4.6-high` hard, `claude-opus-5-thinking-high` review
- Grok: implement `grok-4.6` high; explore `grok-4.5`
- Codex: cheap `gpt-5.6-luna` low; explore `gpt-5.3-codex-mini`. Hard Codex work can use `gpt-5.6-terra` medium
- OpenCode: cheap `openai/gpt-5.4-mini` `--variant minimal`; implement `openai/gpt-5.6-luna` `--variant high`; hard/review `openai/gpt-5.6-terra` `--variant max`
- OMP / Pi: cheap `grok-4.5` `--thinking low`; implement/hard `grok-4.6` `--thinking high`; review `claude-opus-5` `--thinking high`
- agy: cheap `gemini-3.8-flash-low` `--effort low`; implement `gemini-3.8-flash-high` `--effort high`; hard/review `gemini-3.1-pro-high` `--effort high`

Never Fable / Sol / Astra as a child. Opus is allowed.

## Watch, jobs, memory

A Grok child is **headless**. Codex will not show its TUI. While it runs, both you and the parent can see **which agent, which task, status, and the log**.

Prefer MCP when present. Instant tools stay MCP: `rig_jobs`, `rig_job_show`, `rig_job_log`, `rig_job_allow`, `rig_job_deny`, `rig_memory`, `rig_memory_add`, `rig_pick`, `rig_status`, `rig_job_start`, `rig_job_finish`, `rig_job_record`. Launching a child is still bash `run-worker.sh` in the background; there is no spawn-from-MCP tool. Wait is one blocking `rig_job_wait` with **no timeout** (until ASK or result). If the parent host supports MCP progress, `rig_job_wait` may stream the child `doing` line while that wait is in flight. That is not a new wait API. If a parent host **kills** the MCP tool or returns early with an error, fall back to **one** bash `rig job wait <id>` with **no** `--timeout`. Do not poll 30s. Do not loop MCP wait with a short timeout. Bash is also fallback if MCP is missing.

```bash
rig tui                 # jobs board (agent / task / status / live log)
rig jobs                # same data as a table (agents use this)
rig jobs --json
rig jobs --thread       # this parent thread
rig job wait <id>       # one blocking wait until ASK (exit 2) or result; no --timeout
rig job show            # running job, or latest
rig job log <id> -f     # decoded activity (tools + text)
```

`--timeout SECS` is an optional cap, not the default. Omit timeout to block. `0` snapshots once. Exit 124 only if still running when a cap hits.

In Grok, Codex, OpenCode, OMP, Pi, or agy type `/rig`. Grok also gets a bottom status line after `rig setup` (restart Grok once).

MCP tools load after `rig setup` + fully quit the parent CLI once. Instant tools stay MCP (pick, status, start, finish, record, list/show/log, allow/deny, memory). Launch is still bash. Wait is still one blocking `rig_job_wait` with no timeout; one bash `rig job wait` if the host drops the tool.

Open the Grok child TUI yourself: `grok -r <session-id>` or `grok dashboard`. The job folder has `WATCH.md`.

Claude has no TTY as a child. When it needs permission, the job status becomes `ask` and `rig job wait` exits 2. The **parent agent** answers. Do not ignore it, kill the job, or spawn another worker — allow/deny so the same Claude child can continue:

```bash
rig job wait <id>
rig job allow <id>
rig job deny <id> --reason "prod deploy"
```

Safe worker work → allow. Destructive / prod / secrets → deny or ask the user. TUI keys: `y` / `n`. MCP: `rig_job_wait` / `rig_job_allow` / `rig_job_deny`. The child work timeout pauses while status is `ask` and restarts after allow, so a slow parent answer does not immediately timeout the job.

Jobs are this repo, not this chat. A new parent thread still sees `.rig/jobs`. Running children keep going. First commands in a new thread: MCP `rig_memory` then `rig_jobs` then `rig_status` then `rig_pick` when present; else `rig memory` then `rig jobs` then `rig status` then `rig pick --json`.

Memory is local only. Save with the command, not by editing the file:

```bash
rig memory                 # show
rig memory add "Codex sandbox must write ~/.grok"
```

- `.rig/MEMORY.md` — durable bullets, about 120 lines. No transcripts. `add` drops duplicates and caps the file.
- `.rig/STATE.md` — overwritten each run (last job / worker / status / summary).
- `.rig/jobs/` — gitignored. Each job records the parent `thread` when known. `rig prune` drops jobs older than 7 days and keeps the last 20. Successful jobs delete `stdout.log` after `result.json` is written; fail/timeout logs stay for debug.
- `.rig/thread` — gitignored (parent thread id).

## Troubleshooting

**`rig: command not found`**

`~/.local/bin` is not on PATH. In zsh:

```bash
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
which rig
```

Re-run the install if `~/.local/bin/rig` itself is missing:

```bash
curl -fsSL https://raw.githubusercontent.com/Brasth/Rig/main/install.sh | bash
```

If `rig` is already on PATH: `rig update`.

**MCP missing in Grok, Codex, OpenCode, OMP, Pi, or agy** (`rig doctor` MCP lines do not show `[mcp_servers.rig]` / `mcp.rig` / `mcpServers.rig`, or `/rig` / tools are absent)

1. `rig setup`
2. Fully quit the parent **app**, then reopen
3. `rig doctor` — MCP lines should show the server
4. Pi only: `pi install npm:pi-mcp-adapter` if doctor prints that hint, then fully quit Pi again

**Worker `effective=off`**

Read the reason in `rig doctor`:

- `flag` → `rig workers <name>=on`
- `no binary` → install that CLI so `grok` / `claude` / `codex` / `cursor-agent` is on PATH
- `is live parent` → expected (that CLI cannot spawn itself). Use another worker or cheap same-CLI

**Claude child `timeout` right after you allow**

The work clock used to keep counting the minutes spent waiting for allow. It now pauses during `ask` and restarts after allow. Update Rig (`rig update`, or `./install.sh` from a checkout, or the same curl install) so `~/.rig/scripts/run-worker.sh` has that restart. Do not kill an asking job; allow/deny and wait.

**Parent not spawning / ignoring Rig**

Usually an **old thread**. Run `rig init`, then open a **new** parent thread in the repo. Confirm `AGENTS.md` has the `<!-- rig:start -->` block at the top and `.agents/skills/delegate-harness/SKILL.md` exists.

**Cursor not spawning**

Need the `cursor-agent` binary **and** `rig workers cursor=on`. Cursor.app GUI is not enough. `rig doctor` prints the CLI install if `cursor-agent` is missing:

```bash
curl https://cursor.com/install -fsS | bash
rig workers cursor=on
rig doctor
```

**Install failed to clone**

Need `git` (or a logged-in `gh`). The installer clones `https://github.com/Brasth/Rig.git`. Error looks like: `install: could not clone … (need git, or gh auth)`.

**Child fails with `FS_PERMISSION_DENIED` creating a session**

Codex sandbox must allow writing `$HOME/.grok` (and `$HOME/.claude` / `$HOME/.cursor` / `$HOME/.opencode` / `$HOME/.omp` / `$HOME/.pi` / `$HOME/.gemini` if used) plus outbound network. `rig setup` adds those writable roots. Re-run `rig setup`.

**OpenCode / OMP / Pi / agy not spawning**

Need the binary **and** `rig workers opencode=on` (or `omp=on` / `pi=on` / `agy=on`). Existing harness flags stay off. `rig doctor` prints the install hint if the CLI is missing. As workers they are last resort: a Grok parent with no Claude still uses cheap same-CLI Grok, not OpenCode, just because `opencode` is on PATH. As parents, open that CLI (`rig use opencode|omp|pi|agy`).

## Parent agents

Parent agents: load `.agents/skills/delegate-harness/SKILL.md`. Live wrapper is `RIG_LIVE=1` + `run-worker.sh` in the background, then one blocking `rig job wait` (MCP `rig_job_wait` if present; no `--timeout`). Default wrapper is dry-run. Claude `ask` → `rig job allow` / `rig job deny`. Never kill an asking job.

Cheap same-CLI spawns (Codex explorer/worker/bulk/reviewer, Grok explore, OpenCode/OMP/Pi/agy explore/worker/bulk) often do not use `run-worker.sh`. Record them so they still show under `.rig/jobs/`:

```bash
rig job record --worker codex --role explorer --status ok --summary "traced remaining gates"
```

A Claude Code child uses print-mode `stream-json` so the TUI can show tools while it runs. Print prompt is last argv. It does **not** use `--bare` (that drops OAuth) or `--dangerously-skip-permissions` (org policy can forbid bypass). Anthropic remote settings may print `Bash(eval $(wget*))` mismatched-parentheses warnings; those rules are skipped by Claude and hidden by `rig jobs` / `rig tui`. Haiku cheap jobs still record `low` on the board but do not pass `--effort` into Claude Code (Haiku print-mode hangs).

A Cursor child is `cursor-agent -p` with `stream-json`, `--force`, `--trust`, and `--workspace` set to the repo. It does **not** use `--worktree` (edits would leave the repo). `rig doctor` mentions Grok Bot.app and Cursor.app when they exist; those GUIs cannot be spawned.

An OpenCode child is `opencode run --format json --dir <repo> --auto`. An OMP child is `omp -p --mode json --approval-mode write`. A Pi child is `pi -p --mode json --approve`. An agy child is `agy -p` with `--output-format json --mode accept-edits --print-timeout <RIG_TIMEOUT>s --disable-slash-commands`. No `--dangerously-skip-permissions`. `run-worker.sh` fills `RIG_MODEL` / `RIG_EFFORT` from `rig pick` when unset (OpenCode `--variant`, OMP/Pi `--thinking`, agy `--effort`). OpenCode / OMP / Pi / agy models are resolved against that CLI’s live catalog (cached). If both OMP and Pi are effective, pick uses OMP. agy `denied_actions` in JSON is a fail even when the process exits 0.

The parent agent picks worker **and** model/reasoning from the case. Do not ask the user. `rig pick --case "<task>"` is the lookup. Plan/vision/computer-use/chrome-profile: `rig pick stay` unless this CLI cannot do it.

## Commands

```text
usage: rig <command> [args]

  setup
  update
  init [--patch-agents|--no-patch-agents] [--patch-claude]
  doctor
  status
  use codex|grok|opencode|omp|pi|agy
  workers grok=on|off claude=on|off codex=on|off cursor=on|off opencode=on|off omp=on|off pi=on|off agy=on|off
  prune
  run "prompt"
  jobs [--json] [--thread [ID]]
  tui
  memory [show]
  memory add "standing fact"
  job start [--worker NAME] [--role ROLE] [id]
  job finish <id> [--status ok|fail] [--summary TEXT]
  job record [--worker NAME] [--role ROLE] [--status ok|fail] [--summary TEXT] [id]
  job show [id]
  job log [id] [-f] [-n N]
  job allow [id]
  job deny [id] [--reason TEXT]
  job wait [id] [--timeout SECS]
  pick [explore|mini|bulk|implement|hard|review|stay] [--case TEXT] [--json]
```

Stay in Codex, Grok, OpenCode, OMP, Pi, or agy. They can invoke Claude, Cursor, OpenCode, OMP, Pi, agy, or each other.

Parent model is the CLI’s model. Worker models come from `rig pick`. Never spawn Sol, Astra, or Fable as a child.

`rig run "prompt"` exists but is **not** the daily path — type the prompt in the parent CLI instead.
