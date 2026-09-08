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
Do not guess. Run the commands.

## Commands

```bash
rig jobs
rig job show          # running job, or latest
rig job show <id>
rig job log <id>      # decoded activity
rig tui               # interactive board (user terminal; do not launch inside this TUI)
```

If the user named an id, show that job. Otherwise list jobs, then show the running one.

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
