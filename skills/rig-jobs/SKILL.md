---
name: rig
description: >
  Check Rig worker jobs: which agent is running, what task it is doing, status, and logs.
  Use when the user asks about child agents, job status, what a worker is doing, live logs,
  or types /rig.
user-invocable: true
argument-hint: "[job-id]"
---

# Rig jobs

Show the user which Rig worker is running, the task, status, and a readable log.
Do not guess. Run the commands. The parent checks this board and MUST spawn workers for code, review, SSH, and gather — it does not do that work itself.

## Commands

```bash
rig memory            # standing facts; run this on a new thread
rig jobs              # every job in this repo (survives a new parent thread)
rig job wait [id]     # poll until ask (exit 2) or result
rig job show          # running job, or latest
rig job show <id>
rig job log <id>      # decoded activity
rig tui               # interactive board (user terminal; do not launch inside this TUI)
rig memory add "fact" # one standing bullet after a useful run
```

A new Grok/Codex/OpenCode/OMP/Pi/agy thread does not start a new job board. Jobs live in `.rig/jobs/`. If the user named an id, show that job. Otherwise list jobs, then show the running one.

## What to report

- agent (grok / codex / claude / cursor / opencode / omp / pi / agy)
- role (implement, review, explorer, …)
- status (`running`, `ask`, `ok`, `fail`, `timeout`, `stale`)
- task (from the brief)
- doing (last decoded log line: tool + path, or waiting for json)
- how to watch: `rig job log <id> -f` or `rig tui` in another pane
- Grok child: `open` line is `grok -r <session-id>`

If status is `ask`, the Claude child is waiting on a permission prompt. **You answer it** — that is the interaction. Do not kill the job. Do not spawn another worker.

```bash
rig job wait <id>                  # exit 2 = ASK
rig job allow <id>
rig job deny <id> --reason "why"
```

Safe worker work (read/edit/test/ssh gather/git) → allow. Destructive/prod/secrets → deny or ask the user. Do not leave `ask` hanging. After allow, loop `rig job wait` so the same child can continue.

Headless children are not a native Codex/Grok/OpenCode/OMP/Pi/agy agent row. The board, `/rig`, statusline, and these commands are the UI. `/rig` works in OpenCode, OMP, Pi, and agy after `rig setup` (Pi also needs `pi install npm:pi-mcp-adapter`).

MCP tools if present: `rig_jobs`, `rig_job_show`, `rig_job_log`, `rig_job_wait`, `rig_job_allow`, `rig_job_deny`. Same data.
