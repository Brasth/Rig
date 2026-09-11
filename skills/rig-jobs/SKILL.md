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
Do not guess. Use MCP. The parent checks this board and MUST spawn workers for code, review, SSH, and gather unless pick `parent_writes` is true (native implement/hard).

## MCP first

Do not shell `rig` for jobs, wait, allow, deny, log, or message when MCP is listed.

- Instant: `rig_session` / `rig_jobs` / `rig_job_show` / `rig_job_log` / `rig_job_allow` / `rig_job_deny` / `rig_job_message` / `rig_queue_list` / `rig_queue_claim` / `rig_memory` / `rig_pick` / `rig_status` / `rig_job_start` / `rig_job_finish` / `rig_job_record`
- Wait: `rig_job_wait` (one blocking call, no timeout; pass `ids` for every live job, including a review+seed or queue-drain panel)
- Steer a live child: `rig_job_message` (child pulls `rig_job_inbox`; not ASK)

Launching a child is still bash `run-worker.sh` in the background. There is no spawn-from-MCP tool. Human watch: `rig tui` / `/rig` (do not launch a TUI inside this session).

`rig_job_wait`: call **once**, no timeout. Blocks until ASK or result. After allow, wait **once** more (same ids). Do not poll. If MCP wait errors or the host drops the tool, bash `rig job wait` once (no `--timeout`). Do not go back to a 30s poll loop. After implement+verify ok, wait review+seed together: MCP `ids`.

## First call

MCP `rig_session` when present. Else `rig_memory` then `rig_jobs` then `rig_status` then `rig_pick`.

Bash fallback if MCP is **missing**:

```bash
rig session --case "..." --json
rig memory
rig jobs
rig status
rig pick --case "..." --json
rig pick implement --exclude grok --case "..." --json
rig job wait [id ...]
rig job show [id]
rig job log <id>
rig job message <id> --text "steer"
rig tui
rig memory add "fact"
```

A new Grok/Codex/OpenCode/OMP/Pi/agy thread does not start a new job board. Jobs live in `.rig/jobs/`. If the user named an id, show that job. Otherwise list jobs, then show the running one. MCP `rig_jobs` also prints a QUEUE block (pending user work + occupied files). `/queue` / MCP `rig_queue_add` parks text and does not spawn. Drain claims **by id** when several items are pending; skip overlap and try the next id. While wait is blocking, enqueue with MCP `rig_queue_add` (human: `rig queue add` in another pane).

## What to report

- agent (grok / codex / claude / cursor / opencode / omp / pi / agy)
- role (implement, review, explorer, …)
- status (`running`, `ask`, `ok`, `fail`, `timeout`, `stale`)
- task (from the brief)
- doing (last decoded log line or `activity.json` after the raw log is pruned)
- elapsed (from start/end, if present)
- how to watch: MCP `rig_job_log`, or human `rig tui` in another pane
- Grok child: `open` line is `grok -r <session-id>`
- For a Rig job, use MCP `rig_job_log`. Do not read Cursor `state.vscdb`, `~/.cursor` sqlite, or other vendor session stores.

If status is `ask` or `running`, the child is still live. **You answer `ask`** with MCP `rig_job_allow` / `rig_job_deny`. Do not kill the job. Never spawn another worker because the child asked. After implement+verify ok, a second job with disjoint listed files may already be running (seed in parallel with read-only review); wait both ids; do not replace either. Spawn never started (`fail` with empty files / binary missing): one `--exclude` re-pick. Child ran and failed the patch: escalate.

Safe worker work (read/edit/test/ssh gather/git) → allow. Destructive/prod/secrets → deny or ask the user. Do not leave `ask` hanging. After allow, wait **once** more (no timeout). Do not poll.

Fallback if MCP is missing:

```bash
rig job wait <id>
rig job wait <id1> <id2>
rig job allow <id>
rig job deny <id> --reason "why"
```
