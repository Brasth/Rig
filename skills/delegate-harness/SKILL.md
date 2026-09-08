---
name: delegate-harness
description: >
  Delegate implement, review, explore, or split work across Codex, Grok, and Claude via Rig
  run-worker.sh. Use when implementing, reviewing, exploring, splitting work across CLIs,
  or when .rig/harness.toml exists and another CLI should do the work.
---

# Delegate harness

Read `.rig/harness.toml` and `.rig/MEMORY.md` first.
Live parent is this CLI, not the `parent` key in toml. That key is only the preferred default (`rig use grok|codex`). Switching preferred parent does not move the session — open that CLI.
Claude Code (`claude`) is never the parent. It is a worker when `[workers].claude = true` and `claude` is on PATH (`rig workers claude=on`). Pin full model IDs (aliases drift; `haiku` has resolved to Sonnet). Never spawn Fable as a child. Opus is allowed.

## Effective workers

A worker is on only when all of these hold:

1. `[workers].<name>` is `true`
2. The binary is on PATH (`grok`, `codex`, or `claude`)
3. The worker is not the live parent

So: Codex parent → Grok/Claude can be children. Grok parent → Grok child is off; Claude and Codex can be children. Missing binary: that worker is off for this session, not an error. Use cheaper same-CLI workers. That is success.

Check with `rig status`, `rig jobs`, or `/rig`. Live child: `rig tui` in another pane, or `rig job log <id> -f`.

## Route

Never ask the user which model or reasoning to use. They will not know. Run `rig pick` (or `rig pick --case "<task>" --json`) and follow it.

- Small / locate / trace: cheap same-CLI (`rig pick explore` or `mini`). Codex explorer is `gpt-5.3-codex-mini` low. Grok explore is `grok-4.5`. Claude Code explore is `claude-haiku-4-5-20251001` low.
- Mechanical bulk: `rig pick bulk`. Codex `gpt-5.6-luna` low. Claude Code `claude-haiku-4-5-20251001` low.
- Implement: `rig pick implement --case "<task>"`. Grok child `grok-4.6` high if Grok is effective. If Grok is the live parent or off: Claude Code `claude-sonnet-5` medium, else Codex `gpt-5.6-luna` low.
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
3. Wait for `.rig/jobs/<id>/result.json`. Do not parse a TUI.
4. `status` is `ok`, `fail`, or `timeout`. On fail or timeout, escalate to the parent. Do not retry as Sol, Astra, or Fable.

Live child: `RIG_LIVE=1`. Default wrapper is dry-run.

Codex sandbox must allow writing `$HOME/.grok` (and `$HOME/.claude` if used) plus outbound network, or the child fails with `FS_PERMISSION_DENIED` creating a session. `rig setup` adds those writable roots.

## After a run

Overwrite `.rig/STATE.md` with job id, worker, status, summary.
If there is one standing fact, append a bullet to `.rig/MEMORY.md`. Cap about 120 lines. No transcripts.

## Child commands

Exact argv lives in `run-worker.sh`. Pass `RIG_MODEL` and `RIG_EFFORT` from `rig pick`. Defaults if unset: Codex `gpt-5.6-luna` low, Grok `grok-4.6` high, Claude Code `claude-sonnet-5` medium. Explore/mini: Codex `gpt-5.3-codex-mini` low, Claude Code `claude-haiku-4-5-20251001` low. Hard/review Claude Code: `claude-opus-5` high.
