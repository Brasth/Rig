---
name: delegate-harness
description: >
  Delegate implement, review, explore, or split work across Codex, Grok, and Claude via Rig
  run-worker.sh. Use when implementing, reviewing, exploring, splitting work across CLIs,
  or when .rig/harness.toml exists and another CLI should do the work.
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

- Small local change: parent or cheap same-CLI worker (Codex `explorer` / `worker` / `bulk`; Grok `explore`). Record it (below).
- Implement, and Grok is effective: Grok child via `run-worker.sh`.
- Review: a different vendor than the writer. If none is effective, cheap same-CLI reviewer. Record that too.
- No extra CLIs installed: cheap same-CLI workers. Record them. That is success.
- Never spawn Sol, Astra, or Fable as a child. Profile `astra` is parent-only.

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
2. Run `"${RIG_HOME:-$HOME/.rig}/scripts/run-worker.sh" <grok|codex|claude> <id> .rig/jobs/<id>/brief.md`
3. Wait for `.rig/jobs/<id>/result.json`. Do not parse a TUI.
4. `status` is `ok`, `fail`, or `timeout`. On fail or timeout, escalate to the parent. Do not retry as Sol, Astra, or Fable.

Live child: `RIG_LIVE=1`. Default wrapper is dry-run.

Codex sandbox must allow writing `$HOME/.grok` (and `$HOME/.claude` if used) plus outbound network, or the child fails with `FS_PERMISSION_DENIED` creating a session. `rig setup` adds those writable roots.

## After a run

Overwrite `.rig/STATE.md` with job id, worker, status, summary.
If there is one standing fact, append a bullet to `.rig/MEMORY.md`. Cap about 120 lines. No transcripts.

## Child commands

Exact argv lives in `run-worker.sh`. Grok uses `--prompt-file` (headless). Codex child model is `gpt-5.6-terra`. Claude child model is `haiku`.
