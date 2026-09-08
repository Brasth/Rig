---
name: delegate-harness
description: >
  Delegate implement, review, explore, or split work across Codex, Grok, and Claude CLIs
  via Rig. Use when implementing, reviewing, exploring, or splitting work across CLIs.
---

# Delegate harness

Read `.rig/harness.toml` and `.rig/MEMORY.md` first.
Live parent is this CLI, not the `parent` key in toml. That key is only the preferred default.
Claude is never the parent.

## Effective workers

A worker is on only when all of these hold:

1. `[workers].<name>` is `true`
2. The binary is on PATH (`grok`, `codex`, or `claude`)
3. The worker is not the live parent

Missing binary: that worker is off for this session, not an error. Use cheaper same-CLI workers. That is success.

Check with `rig status` or `rig doctor`.

## Route

- Small local change: parent or cheap same-CLI worker (Codex `explorer` / `worker` / `bulk`; Grok `explore`).
- Implement, and Grok is effective: Grok child via `run-worker.sh`.
- Review: a different vendor than the writer. If none is effective, cheap same-CLI reviewer.
- No extra CLIs installed: cheap same-CLI workers. Stop there.
- Never spawn Sol, Astra, or Fable as a child. Profile `astra` is parent-only.

Writer does not review its own diff.

## Call another CLI

1. Write `.rig/jobs/<id>/brief.md`. Start with: you are a worker, not the orchestrator; do not spawn codex, grok, or claude; do the task; print a short summary; stop.
2. Run `"${RIG_HOME:-$HOME/.rig}/scripts/run-worker.sh" <grok|codex|claude> <id> .rig/jobs/<id>/brief.md`
3. Wait for `.rig/jobs/<id>/result.json`. Do not parse a TUI.
4. `status` is `ok`, `fail`, or `timeout`. On fail or timeout, escalate to the parent. Do not retry as Sol, Astra, or Fable.

Live child: `RIG_LIVE=1`. Default wrapper is dry-run.

## After a run

Overwrite `.rig/STATE.md` with job id, worker, status, summary.
If there is one standing fact, append a bullet to `.rig/MEMORY.md`. Cap about 120 lines. No transcripts.

## Child commands

Exact argv lives in `run-worker.sh`. Grok uses `--prompt-file` (headless). Codex child model is `gpt-5.6-terra`. Claude child model is `haiku`.
