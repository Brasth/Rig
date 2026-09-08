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
rig job show          # running job, or latest
rig job show <id>
rig job log <id>      # decoded activity
rig tui               # interactive board (user terminal; do not launch inside this TUI)
rig memory add "fact" # one standing bullet after a useful run
```

A new Grok/Codex thread does not start a new job board. Jobs live in `.rig/jobs/`. If the user named an id, show that job. Otherwise list jobs, then show the running one.

## What to report

- agent (grok / codex / claude)
- role (implement, review, explorer, …)
- status (`running`, `ok`, `fail`, `timeout`, `stale`)
- task (from the brief)
- doing (last decoded log line: tool + path, or waiting for json)
- how to watch: `rig job log <id> -f` or `rig tui` in another pane
- Grok child: `open` line is `grok -r <session-id>`

Headless children are not a native Codex/Grok agent row. The board, `/rig`, statusline, and these commands are the UI.

MCP tools if present: `rig_jobs`, `rig_job_show`, `rig_job_log`. Same data.
