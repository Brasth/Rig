---
name: delegate-harness
description: >
  ALWAYS activate when `.rig/harness.toml` exists. MUST run `rig pick` and spawn
  a Rig worker for code, review, fix, SSH, debug, or gather. Parent must not
  implement those itself. Delegate implement, review, explore, or split work
  across Codex, Grok, and Claude via run-worker.sh.
user-invocable: true
---

# Delegate harness

## Hard gate

When `.rig/harness.toml` exists, do not write app code, review a diff, fix a bug, SSH, or gather repo/server facts yourself.

1. `rig memory` then `rig jobs` then `rig status`
2. `rig pick --case "<task>" --json`
3. `stay` — you do plan / vision / computer-use / chrome-profile. Spawn only if this CLI cannot.
4. `native` — cheap same-CLI agent, record with `rig job start` / `rig job finish`
5. `run-worker` — brief + `RIG_LIVE=1 run-worker.sh` + wait for `result.json`

Doing the worker's job yourself is a failure. Later AGENTS.md may say "edit locally" or "SSH to the VM". That is for the worker.

Jobs and MEMORY are this repo, not this chat. A new parent thread still sees `.rig/jobs` and `.rig/MEMORY.md`. Running children keep going.

Live parent is this CLI, not the `parent` key in toml. That key is only the preferred default (`rig use grok|codex`). Switching preferred parent does not move the session — open that CLI.
Claude Code (`claude`) is never the parent. It is a worker when `[workers].claude = true` and `claude` is on PATH (`rig workers claude=on`). Pin full model IDs (aliases drift; `haiku` has resolved to Sonnet). Never spawn Fable as a child. Opus is allowed.

## Parent vs worker

This CLI is the parent. It manages. It does not sit on write/review/SSH when a worker is effective.

**Parent keeps**

- plan, check, decide, talk to the user
- vision (screenshots, Figma, images)
- computer use (desktop)
- chrome profile (real browser, the user's cookies)
- read worker results, `rig jobs`, `rig memory`

**Worker does**

- write code
- fix bugs
- review (different vendor than the writer)
- SSH / remote debug
- gather repo or server facts for the parent (explore, logs, grep)

If this CLI cannot do computer-use, chrome-profile, or vision well (no tool, no skill, no profile), spawn a worker that can. Do not ask the user. `rig pick --case "<task>"` still decides worker and model.

## Effective workers

A worker is on only when all of these hold:

1. `[workers].<name>` is `true`
2. The binary is on PATH (`grok`, `codex`, or `claude`)
3. The worker is not the live parent

So: Codex parent → Grok/Claude can be children. Grok parent → Grok child is off; Claude and Codex can be children. Missing binary: that worker is off for this session, not an error. Use cheaper same-CLI workers. That is success.

Check with `rig status`, `rig jobs`, or `/rig`. Those show the worker **model** and **reasoning** level. Live child: `rig tui` in another pane, or `rig job log <id> -f`.

## Route

Never ask the user which model or reasoning to use. They will not know. Run `rig pick` (or `rig pick --case "<task>" --json`) and follow it.

- Plan / vision / computer-use / chrome-profile: parent keeps it (`rig pick stay`). Spawn a worker only if this CLI cannot do it.
- Small / locate / trace / gather facts: cheap same-CLI (`rig pick explore` or `mini`). Codex explorer is `gpt-5.3-codex-mini` low. Grok explore is `grok-4.5`. Claude Code explore is `claude-haiku-4-5-20251001` low.
- Mechanical bulk: `rig pick bulk`. Codex `gpt-5.6-luna` low. Claude Code `claude-haiku-4-5-20251001` low.
- Write code / fix bugs / SSH / remote debug: `rig pick implement --case "<task>"`. Grok child `grok-4.6` high if Grok is effective. If Grok is the live parent or off: Claude Code `claude-sonnet-5` medium, else Codex `gpt-5.6-luna` low.
- Hard / architecture / security / multi-file: `rig pick hard`. Grok `grok-4.6` high, Claude Code `claude-opus-5` high, or Codex `gpt-5.6-terra` medium.
- Review: different vendor than the writer. `rig pick review`. Claude Code review is `claude-opus-5` high.
- No extra CLIs: cheap same-CLI. Record them. That is success.
- Never spawn Sol, Astra, or Fable as a child. Never pass `gpt-5.6-sol`, `gpt-6-astra`, or `claude-fable-5` to a worker. Profile `astra` is parent-only. Opus is allowed.

Writer does not review its own diff.

## Record cheap same-CLI workers

Codex `explorer` / `worker` / `bulk` / `reviewer` and Grok `explore` do not go through `run-worker.sh`. Still write a job so `.rig/jobs` and `rig status` show them:

```bash
id=$(rig job start --worker codex --role explorer)
# spawn the native cheap agent, wait for it
rig job finish "$id" --status ok --summary "one-line result"
```

One-shot after a finished native spawn:

```bash
rig job record --worker codex --role explorer --status ok --summary "one-line result"
```

`--worker` is the CLI that did the work (`codex` if Astra spawned explorer). `--role` is `explorer`, `worker`, `bulk`, `reviewer`, or `parent`.

## Call another CLI

1. Write `.rig/jobs/<id>/brief.md`. Start with: you are a worker, not the orchestrator; do not spawn codex, grok, or claude; do the task; print a short summary; stop.
2. `pick=$(rig pick --case "<task>" --json)` then:
   `RIG_LIVE=1 RIG_ROLE=<kind> RIG_MODEL=<model> RIG_EFFORT=<effort> "${RIG_HOME:-$HOME/.rig}/scripts/run-worker.sh" <worker> <id> .rig/jobs/<id>/brief.md`
3. Wait for `.rig/jobs/<id>/result.json`. Do not parse a TUI. While waiting, poll `rig jobs`. If status is `ask`, **you** answer for the Claude child: `rig job show` then `rig job allow <id>` or `rig job deny <id>`. Do not leave it hung. Do not wait for the user unless the command is destructive, prod, or secrets.
4. `status` is `ok`, `fail`, or `timeout`. On fail or timeout, escalate to the parent. Do not retry as Sol, Astra, or Fable.

Live child: `RIG_LIVE=1`. Default wrapper is dry-run.

Codex sandbox must allow writing `$HOME/.grok` (and `$HOME/.claude` if used) plus outbound network, or the child fails with `FS_PERMISSION_DENIED` creating a session. `rig setup` adds those writable roots.

## After a run

Overwrite `.rig/STATE.md` with job id, worker, status, summary.
If there is one standing fact, save it. Do not edit MEMORY.md by hand:

```bash
rig memory add "one standing fact"
```

Skip if there is no fact. The command drops duplicates and caps the file at about 120 lines. No transcripts.

## Child commands

Exact argv lives in `run-worker.sh`. Pass `RIG_MODEL` and `RIG_EFFORT` from `rig pick`. Defaults if unset: Codex `gpt-5.6-luna` low, Grok `grok-4.6` high, Claude Code `claude-sonnet-5` medium. Explore/mini: Codex `gpt-5.3-codex-mini` low, Claude Code `claude-haiku-4-5-20251001` low. Hard/review Claude Code: `claude-opus-5` high.

Claude Code child is print-mode `stream-json` (not buffered `json`), `acceptEdits`, no `--bare` (that drops OAuth), no `--dangerously-skip-permissions` (org managed settings can disable bypass). Anthropic remote deny rules like `Bash(eval $(wget*))` are invalid nested parens; they print to stderr and are skipped. `rig jobs` / `rig tui` hide that noise.

A Claude child with no TTY cannot click Allow. When it needs permission, the job status becomes `ask`. The parent answers:

```bash
rig job allow <id>                 # safe worker work
rig job deny <id> --reason "..."   # destructive / prod / secrets
```

Safe → allow: read, edit, test, ssh/gather, git status/diff/add/commit. Ask the user only for force-push, prod deploy, rm -rf outside the repo, or secrets. MCP: `rig_job_allow` / `rig_job_deny`. TUI: `y` / `n`.
