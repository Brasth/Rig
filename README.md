Stay in Codex or Grok. They MUST invoke Claude, Cursor, or each other as workers for code, review, fix, SSH, and gather. The parent keeps plan, vision, computer-use, and chrome-profile.

# Rig

Type a normal prompt in one parent CLI. The parent may call the other CLIs as workers. Claude is never the parent. If a worker binary is missing, use cheaper same-CLI workers. That is success.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Brasth/Rig/main/install.sh | bash
```

No GitHub login. That pipes `install.sh` and clones the rest over HTTPS. No local clone to keep.

From a checkout you already have: `./install.sh`

`install.sh` is idempotent. It updates the skill and scripts. It does not overwrite project `.rig/harness.toml` or `.rig/MEMORY.md`.
`rig setup` writes `[mcp_servers.rig]` into `~/.grok/config.toml` and `~/.codex/config.toml` even if those files did not exist yet. Fully quit Grok and Codex once so they load the tools.

If `~/.local/bin` is not on your PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Per project

```bash
cd your-repo
rig init
rig doctor
```

`rig init` writes `.rig/` and copies the `delegate-harness` skill into `.agents/skills/` so Codex can load it.
It puts a **MUST use Rig** block at the **top** of `AGENTS.md` (never replaces the rest). `--no-patch-agents` skips that. `--patch-claude` writes `CLAUDE.md` only if that file is missing.

The parent must spawn workers for code, review, fix, SSH, and gather. It keeps plan, vision, computer-use, and chrome-profile. Open a **new** Codex or Grok thread after init. An already-open session will not pick up the block.

## Commands

```text
rig setup
rig init [--patch-agents] [--patch-claude]
rig doctor
rig status
rig use codex|grok
rig parent sol|astra
rig workers grok=on|off claude=on|off codex=on|off cursor=on|off
rig prune
rig run "prompt"
rig jobs [--json] [--thread [ID]]
rig tui
rig memory [show]
rig memory add "standing fact"
rig job start|finish|record|show|log|allow|deny|wait
rig pick [explore|mini|bulk|implement|hard|review|stay] [--case TEXT]
```

`rig parent astra` records a parent-only profile. Never spawn Astra as a child.

New `rig init` turns a worker on only if its CLI is on PATH (Grok, Claude, Cursor). Codex stays off as the preferred parent. Existing `.rig/harness.toml` flags are never flipped; missing `cursor` is appended as off (`rig workers cursor=on` to enable). Live parent is whichever CLI you opened (`rig status`), not the `parent` key. `rig use grok` only records the preferred default — open Grok to make it live. A worker that equals the live parent is off for that session. Claude Code and Cursor CLI are never the parent. Cursor binary is `cursor-agent` (`rig workers cursor=on`). Pin full model IDs (aliases drift): Claude `claude-haiku-4-5-20251001` cheap, `claude-sonnet-5` implement, `claude-opus-5` hard/review. Cursor `composer-2.5-fast` cheap, `composer-2.5` implement, `cursor-grok-4.6-high` hard, `claude-opus-5-thinking-high` review. Never Fable/Sol/Astra as a child. Grok Bot.app is not a worker.

The parent manages. It plans, checks, does vision, computer-use, and chrome-profile. Workers write code, fix bugs, review, SSH/debug, and gather facts for the parent. If this CLI cannot do computer-use or chrome-profile, spawn a worker. Do not ask the user.

The parent agent picks worker **and** model/reasoning from the case. Do not ask the user. `rig pick --case "<task>"` is the lookup. Plan/vision/computer-use/chrome-profile: `rig pick stay` (parent keeps it). Implement: Grok child if Grok is effective; if Grok is the live parent, Claude if effective, else cheap same-CLI. Cursor and Codex children are last resort — not the default just because their CLI is on PATH. Codex cheap default is `gpt-5.6-luna` with low thinking; tiny explore uses `gpt-5.3-codex-mini`. Hard Codex work can use `gpt-5.6-terra` medium. Grok implement is `grok-4.6` high; Grok explore is `grok-4.5`. Never spawn Sol, Astra, or Fable as children. Opus is allowed.

## Workers

Cross-CLI jobs go through `~/.rig/scripts/run-worker.sh`. Start the wrapper in the background. The parent loops `rig job wait <id>` until `.rig/jobs/<id>/result.json`. Do not block the parent turn on `run-worker.sh`.

A Grok child is **headless**. Codex will not show its TUI. While it runs, both you and the parent agent can see **which agent, which task, status, and the log**:

```bash
rig tui                 # jobs board (agent / task / status / live log)
rig jobs                # same data as a table (agents use this)
rig job wait <id>       # poll until ask (exit 2) or result
rig job show            # running job, or latest
rig job log <id> -f     # decoded activity (tools + text)
```

In Grok or Codex type `/rig`. Grok also gets a bottom status line after `rig setup` (restart Grok once). MCP tools: `rig_jobs`, `rig_job_show`, `rig_job_log`, `rig_job_wait`, `rig_job_allow`, `rig_job_deny`, `rig_memory`, `rig_memory_add`.

Open the Grok child TUI: `grok -r <session-id>` or `grok dashboard`. The job folder has `WATCH.md`.

Cheap same-CLI spawns (Codex explorer/worker, Grok explore) do not use that wrapper. Record them so they still show under `.rig/jobs/`:

```bash
rig job record --worker codex --role explorer --status ok --summary "traced remaining gates"
```

Default `run-worker.sh` is dry-run (prints the command, writes `result.json`). Live child:

```bash
RIG_LIVE=1 ~/.rig/scripts/run-worker.sh grok <job-id> <brief-file>
```

If the binary is missing, the wrapper prints the command it would have run and exits non-zero.

A Claude Code child uses print-mode `stream-json` so the TUI can show tools while it runs. It does **not** use `--bare` (that drops OAuth) or `--dangerously-skip-permissions` (org policy can forbid bypass). Anthropic remote settings may print `Bash(eval $(wget*))` mismatched-parentheses warnings; those rules are skipped by Claude and hidden by `rig jobs` / `rig tui`.

A Cursor child is `cursor-agent -p` with `stream-json`, `--force`, `--trust`, and `--workspace` set to the repo. It does **not** use `--worktree` (edits would leave the repo). `rig doctor` mentions Grok Bot.app and Cursor.app when they exist; those GUIs cannot be spawned.

Claude has no TTY as a child. When it needs permission, the job status becomes `ask` and `rig job wait` exits 2. The **parent agent** answers, same as an interaction. Do not ignore it, kill the job, or spawn another worker — allow/deny so the same Claude child can continue:

```bash
rig job wait <id>
rig job allow <id>
rig job deny <id> --reason "prod deploy"
```

Safe worker work → allow. Destructive / prod / secrets → deny or ask the user. TUI keys: `y` / `n`. MCP: `rig_job_wait` / `rig_job_allow` / `rig_job_deny`.

Jobs are this repo, not this chat. A new Grok or Codex thread still sees `.rig/jobs`. Running children keep going. First commands in a new thread: `rig memory` then `rig jobs`.

## Memory

Local only. Save with the command, not by editing the file:

```bash
rig memory                 # show
rig memory add "Codex sandbox must write ~/.grok"
```

- `.rig/MEMORY.md` — durable bullets, about 120 lines. No transcripts. `add` drops duplicates and caps the file.
- `.rig/STATE.md` — overwritten each run (last job / worker / status / summary).
- `.rig/jobs/` — gitignored. Each job records the parent `thread` when known. `rig prune` drops jobs older than 7 days and keeps the last 20. Successful jobs delete `stdout.log` after `result.json` is written; fail/timeout logs stay for debug.
