Stay in Codex or Grok. They can invoke Claude or each other.

# Rig

Type a normal prompt in one parent CLI. The parent may call the other CLIs as workers. Claude is never the parent. If a worker binary is missing, use cheaper same-CLI workers. That is success.

## Install

The repo is private. Log in with `gh auth login`, then:

```bash
gh api -H 'Accept: application/vnd.github.raw' repos/Brasth/Rig/contents/install.sh | bash
```

That pipes `install.sh` and clones the rest with your GitHub login. No local clone to keep. Anonymous `curl` will 404.

From a checkout you already have: `./install.sh`

`install.sh` is idempotent. It updates the skill and scripts. It does not overwrite project `.rig/harness.toml` or `.rig/MEMORY.md`.

If `~/.local/bin` is not on your PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Per project

```bash
cd your-repo
rig init --patch-agents
rig doctor
```

`rig init` writes `.rig/` and copies the `delegate-harness` skill into `.agents/skills/` so Codex can load it.
`--patch-agents` inserts or refreshes a marked block in `AGENTS.md` that names that skill and the `run-worker.sh` command. It never replaces the whole file.
`--patch-claude` writes `CLAUDE.md` only if that file is missing.

Then open a **new** Codex or Grok thread in that repo and type normally. An already-open session will not pick up the new block.

## Commands

```text
rig setup
rig init [--patch-agents] [--patch-claude]
rig doctor
rig status
rig use codex|grok
rig parent sol|astra
rig workers grok=on|off claude=on|off codex=on|off
rig prune
rig run "prompt"
rig jobs [--json]
rig tui
rig job start|finish|record|show|log
rig pick [explore|mini|bulk|implement|hard|review] [--case TEXT]
```

`rig parent astra` records a parent-only profile. Never spawn Astra as a child.

Default project workers: Grok on, Claude on, Codex off. Live parent is whichever CLI you opened (`rig status`), not the `parent` key. `rig use grok` only records the preferred default — open Grok to make it live. A worker that equals the live parent is off for that session (Grok parent → no Grok child; Claude/Codex can still run if their flags are on). Claude Code is never the parent; `rig workers claude=on` turns it on as a worker. Pin full model IDs (aliases drift): `claude-haiku-4-5-20251001` low for explore/bulk, `claude-sonnet-5` medium for implement, `claude-opus-5` high for hard/review. Never Fable as a child.

The parent agent picks worker **and** model/reasoning from the case. Do not ask the user. `rig pick --case "<task>"` is the lookup. Codex cheap default is `gpt-5.6-luna` with low thinking; tiny explore uses `gpt-5.3-codex-mini`. Hard Codex work can use `gpt-5.6-terra` medium. Grok implement is `grok-4.6` high; Grok explore is `grok-4.5`. Never spawn Sol, Astra, or Fable as children. Opus is allowed.

## Workers

Cross-CLI jobs go through `~/.rig/scripts/run-worker.sh`. The parent waits on `.rig/jobs/<id>/result.json`.

A Grok child is **headless**. Codex will not show its TUI. While it runs, both you and the parent agent can see **which agent, which task, status, and the log**:

```bash
rig tui                 # jobs board (agent / task / status / live log)
rig jobs                # same data as a table (agents use this)
rig job show            # running job, or latest
rig job log <id> -f     # decoded activity (tools + text)
```

In Grok or Codex type `/rig`. Grok also gets a bottom status line after `rig setup` (restart Grok once). MCP tools: `rig_jobs`, `rig_job_show`, `rig_job_log`.

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

## Memory

Local only.

- `.rig/MEMORY.md` — durable bullets, about 120 lines. No transcripts.
- `.rig/STATE.md` — overwritten each run.
- `.rig/jobs/` — gitignored. `rig prune` drops jobs older than 7 days and keeps the last 20. Successful jobs delete `stdout.log` after `result.json` is written; fail/timeout logs stay for debug.
