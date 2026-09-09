# Rig

A local parent/worker kit. You stay in one parent CLI. You type a prompt. The parent hands the work to a child, then checks the result and sends feedback — the loop you used to do yourself, sitting on one agent.

Intended parent is **Codex running Astra** (the human-like assistant). Grok, OpenCode, OMP, Pi, and agy can also be the parent. Claude and Cursor are never the parent. Missing worker binary → cheaper same-CLI. That is success.

## Why

Using AI to ship a feature often means **you** become the bottleneck. You read every diff. You write every correction. You get tired. The work stops being smooth.

Sol made an agent feel like a person at the computer. Astra went further. Rig is the harness around that:

1. You talk to the **parent** (Astra in Codex, or another parent CLI you opened).
2. The parent assigns the task to a **child** (Grok, Claude, Cursor, OpenCode, OMP, Pi, agy, Codex).
3. The parent **checks, verifies, and gives the child feedback** (`rig jobs`, allow/deny, a follow-up prompt) — the same review loop you used to run by hand.

Open Codex on Astra as the parent. Pick the parent model in that CLI. Worker models come from `rig pick`. Never spawn Astra, Sol, or Fable as a child.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Brasth/Rig/main/install.sh | bash
```

No GitHub login. It clones over HTTPS, copies into `~/.rig`, puts `rig` on `~/.local/bin`, runs `rig setup`, and deletes the temp clone. Same curl later is idempotent. It does **not** overwrite a project’s `.rig/harness.toml` or `.rig/MEMORY.md`.

Already have `rig` on PATH: `rig update` (GitHub `main`, same installer). Older `rig` without that command still needs the curl once.

From a checkout you already have: `./install.sh` (copies the local tree + `rig setup`, no clone).

**PATH (only if `rig` is not found):**

```bash
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

`which rig` must print `$HOME/.local/bin/rig`.

You need one parent CLI: Codex, Grok, OpenCode, OMP, Pi, or agy. Optional worker binaries: `grok`, `claude`, `cursor-agent`, `codex`, `opencode`, `omp`, `pi`, `agy`.

## Per project

```bash
cd your-repo
rig init
rig doctor
```

`rig init` is per repo. Do this in every project you want Rig to manage. Existing `.rig/harness.toml` flags are never flipped.

Fully quit the parent CLI once after first install (not just the tab — quit the apps). MCP tools and the Grok status line load on a cold start.

Open a **new** thread in that repo. An already-open session will not pick up `AGENTS.md` or skills.

Type a normal prompt in that parent CLI. Example: `fix the failing tests in tests/test_cli.py`. Do **not** use `rig run` for normal work.

## Configure

```bash
rig use grok|codex|opencode|omp|pi|agy
rig workers grok=on|off claude=on|off codex=on|off cursor=on|off opencode=on|off omp=on|off pi=on|off agy=on|off
```

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

- **Live parent** is whichever Codex, Grok, OpenCode, OMP, Pi, or agy you actually opened (`rig status`). The `parent =` key is only the preferred default (`rig use grok|codex|opencode|omp|pi|agy`). Opening the CLI is what makes it live.
- Parent **model** is the CLI’s model. Worker models come from `rig pick`. Never spawn Sol, Astra, or Fable as a child.
- A worker is **effective** only when: flag true **and** binary on PATH **and** not the live parent.
- Claude Code and Cursor are never the parent.
- Grok Bot.app and Cursor.app are GUIs, **not** spawnable workers. The Cursor worker binary is `cursor-agent`.

## Watch

`rig tui` / `rig jobs` / `/rig` in Grok, Codex, OpenCode, OMP, Pi, or agy. Grok also gets a bottom status line after setup (restart Grok once). Pi needs `pi install npm:pi-mcp-adapter` before `/rig` loads.

Jobs and MEMORY are this repo, not the chat. A new thread still sees `.rig/jobs`. Running children keep going.

If a Claude child is `ask`, the parent answers `rig job allow <id>` or `rig job deny <id>` (TUI `y` / `n`). Never kill that job.

More: [Usage](docs/usage.md) (setup, doctor, harness, daily use, troubleshooting).
Parent spawn protocol: `.agents/skills/delegate-harness/SKILL.md` (also the `<!-- rig:start -->` block in `AGENTS.md`).
