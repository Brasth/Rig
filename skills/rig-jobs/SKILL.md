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
Do not guess. Run the commands. The parent checks this board and MUST spawn workers for code, review, SSH, and gather unless pick `parent_writes` is true (native implement/hard).

## MCP first

If MCP tools are present, use them. Bash is fallback if MCP is missing.

- Instant: `rig_session` / `rig_jobs` / `rig_job_show` / `rig_job_log` / `rig_job_allow` / `rig_job_deny` / `rig_job_message` / `rig_queue_list` / `rig_queue_claim` / `rig_memory` / `rig_pick` / `rig_status` / `rig_job_start` / `rig_job_finish` / `rig_job_record`
- Wait: `rig_job_wait` (one blocking call, no timeout; pass `ids` for every live job, including a review+seed or queue-drain panel)

Launching a child is still bash `run-worker.sh` in the background. There is no spawn-from-MCP tool.

`rig_job_wait` / `rig job wait`: call **once**, no timeout. Blocks until ASK or result. After allow, wait **once** more (same ids). Do not poll. `--timeout` is an optional cap, not the default. If MCP wait errors or the host drops the tool, bash `rig job wait` once (no `--timeout`). Do not go back to a 30s poll loop. After implement+verify ok, wait review+seed together: `ids` or `rig job wait id1 id2`.

## Commands

First commands in a new thread: MCP `rig_session` when present; else `rig_memory` then `rig_jobs` then `rig_status` then `rig_pick`; else bash:

```bash
rig session --case "..." --json   # memory + jobs + status + pick
rig memory            # standing facts; run this on a new thread
rig jobs              # every job in this repo (survives a new parent thread)
rig status            # live parent, effective workers, job count
rig pick --case "..." --json
rig pick implement --exclude grok --case "..." --json  # after a dead spawn, once
rig job wait [id ...] # one blocking wait; no --timeout; exit 2 = ASK; two ids = wait-all
rig job show          # running job, or latest
rig job show <id>
rig job log <id>      # decoded activity
rig job message <id> --text "steer"  # parent inbox; child pulls rig_job_inbox; not ASK
rig tui               # interactive board (user terminal; do not launch inside this TUI)
rig memory add "fact" # one standing bullet after a useful run
```

A new Grok/Codex/OpenCode/OMP/Pi/agy thread does not start a new job board. Jobs live in `.rig/jobs/`. If the user named an id, show that job. Otherwise list jobs, then show the running one. `rig jobs` also prints a QUEUE block (pending user work). `/queue` / `rig queue add` parks text and does not spawn. While wait is blocking, enqueue from another pane with `rig queue add`.

## What to report

- agent (grok / codex / claude / cursor / opencode / omp / pi / agy)
- role (implement, review, explorer, …)
- status (`running`, `ask`, `ok`, `fail`, `timeout`, `stale`)
- task (from the brief)
- doing (last decoded log line or `activity.json` after the raw log is pruned)
- elapsed (from start/end, if present)
- how to watch: `rig job log <id> -f` or `rig tui` in another pane
- Grok child: `open` line is `grok -r <session-id>`
- For a Rig job, use `rig job log` / MCP. Do not read Cursor `state.vscdb`, `~/.cursor` sqlite, or other vendor session stores.

If status is `ask` or `running`, the child is still live. **You answer `ask`** — that is the interaction. Do not kill the job. Never spawn another worker because the child asked. After implement+verify ok, a second job with disjoint listed files may already be running (seed in parallel with read-only review); wait both ids; do not replace either. Spawn never started (`fail` with empty files / binary missing): one `--exclude` re-pick. Child ran and failed the patch: escalate.

```bash
rig job wait <id>                  # one blocking wait; exit 2 = ASK
rig job wait <id1> <id2>           # wait-all (review + seed)
rig job allow <id>
rig job deny <id> --reason "why"
```

Safe worker work (read/edit/test/ssh gather/git) → allow. Destructive/prod/secrets → deny or ask the user. Do not leave `ask` hanging. After allow, wait **once** more (no timeout). Do not poll.
