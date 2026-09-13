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

- Ownership/assessment: `rig_job_requirements` / `rig_job_check` / `rig_job_accept` / `rig_job_close` / `rig_job_reconcile`.
- Instant: `rig_session` / `rig_jobs` / `rig_job_show` / `rig_job_log` / `rig_job_allow` / `rig_job_deny` / `rig_job_cancel` / `rig_job_message` / `rig_queue_list` / `rig_queue_claim` / `rig_memory` / `rig_pick` / `rig_status` / `rig_job_start` / `rig_job_finish` / `rig_job_record`
- Wait: `rig_job_wait` (one blocking call, no timeout; pass `ids` for every live job, including a review+seed or queue-drain panel)
- Steer a live child: `rig_job_message` (child pulls `rig_job_inbox`; not ASK)

Launching a child is still bash `run-worker.sh` in the background. There is no spawn-from-MCP tool. Human watch: `rig tui` / `/rig` (do not launch a TUI inside this session).

`rig_job_wait`: call **once**, no timeout. Blocks until ASK or result. After allow, wait **once** more (same ids). Do not poll. If MCP wait errors or the host drops the tool, bash `rig job wait` once (no `--timeout`). Do not go back to a 30s poll loop. After implement+verify ok, wait review+seed together: MCP `ids`.

## First call

MCP `rig_session(role=KIND, compact=true, terminal_limit=10, case="task")` when present. Parent chooses semantic role explicitly; omitted role has bounded English inference. All active/ASK/reserved rows remain visible; ten recent terminal rows are included. Full session mode remains the API/CLI default. Else `rig_memory` then `rig_jobs` then `rig_status` then `rig_pick`.

Bash fallback if MCP is **missing**:

```bash
rig session --role stay --case "show job status" --compact --terminal-limit 10 --json
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
- display state and its reason: reserved, working, needs-input, verifying, completed-unverified, verified, failed, cancelled; preserve underlying execution `status`/`effective` separately
- actual model/effort and provenance; unknown means unknown, not the preferred model
- held reservation scope and whether an execution slot is still held; never owner tokens
- parent verification: current snapshot, checks/manual method, missing/failed requirements, and independent-review status separately
- task (from the brief)
- doing (last decoded log line or `activity.json` after the raw log is pruned)
- elapsed (from start/end, if present)
- how to watch: MCP `rig_job_log`, or human `rig tui` in another pane
- Grok child: `open` line is `grok -r <session-id>`
- For a Rig job, use MCP `rig_job_log`. Do not read Cursor `state.vscdb`, `~/.cursor` sqlite, or other vendor session stores.

If status is `ask` or `running`, the child is still live. **You answer `ask`** with MCP `rig_job_allow` / `rig_job_deny`. Do not kill the job because the child asked. Never spawn another worker because the child asked. User Esc / cancelled wait: MCP `rig_job_cancel` those ids (status `cancelled`; do not re-pick). After implement+verify ok, a second job with disjoint listed files may already be running (seed in parallel with read-only review); wait both ids; do not replace either. Only confirmed spawn-never-started failure permits one `--exclude` re-pick with a fresh ID; empty changed files alone do not prove that. Child ran and failed the patch: escalate.

Safe worker work (read/edit/test/ssh gather/git) → allow. Destructive/prod/secrets → deny or ask the user. Do not leave `ask` hanging. After allow, wait **once** more (no timeout). Do not poll.

Fallback if MCP is missing:

```bash
rig job wait <id>
rig job wait <id1> <id2>
rig job allow <id>
rig job deny <id> --reason "why"
rig job cancel <id>
```

## Interpreting completion

Execution `ok` means the process/task ended successfully. It is completed-unverified until the parent inspects evidence, declares all requirements, deliberately runs required checks or addresses manual criteria, and accepts the current snapshot. A later content change invalidates acceptance. Only active checks/review justify “verifying”; held files alone do not. A different CLI is not proof of a different model provider.

Register native/parent writes with `rig_job_start` before editing. Preserve `structuredContent` credentials (`reservation_id`, `attempt_id`, `owner_token`, initiating owner/session) and the returned private `credentials_path`. CLI `--json` gives the same explicit launch response; shell token transport is `RIG_OWNER_TOKEN`. Never load credentials just from a guessed job ID or HUD thread cache, and never show tokens to the user.

Native finish authenticates the same owner and explicit `parent_task` completion or the specific native agent ID/terminal outcome. Missing completion keeps protection. A cancelled wrapper may still be stopping; files remain held until its process/group/observed descendants stop. Close requires confirmed termination, exact credentials, and rationale; it releases ownership without accepting or retrying work. Reconcile is report-only unless explicitly applied; live/ASK/unknown ownership never expires by age.

Parent acceptance with `next=review` retains scope for a fresh independent reviewer attempt, gated by the original writer's current accepted snapshot and actual provider. Use the new holder credentials after transfer. Failed review launch retains protected scope without a slot until a fresh retry or explicit close.

The small HUD selects ASK first, then stopping/reconciliation, active execution/checks, and a recent terminal result. Terminal display expires after 60 seconds; ownership does not. Check progress belongs to that check request, never an earlier wait token. Use the full board for every active ID. See `delegate-harness/SKILL.md` for the full parent workflow.
