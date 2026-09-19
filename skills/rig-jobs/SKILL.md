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
- Instant: `rig_session` / `rig_jobs` / `rig_job_show` / `rig_job_log` / `rig_job_launch` / `rig_job_allow` / `rig_job_deny` / `rig_job_cancel` / `rig_job_message` / `rig_queue_list` / `rig_queue_claim` / `rig_queue_spawned` / `rig_memory` / `rig_pick` / `rig_status` / `rig_job_start` / `rig_job_finish` / `rig_job_record`
- Wait: `rig_job_wait` (one blocking call, no timeout for observable wrappers; pass `ids` for live wrappers in a review+seed or queue-drain panel)
- Steer a live child: `rig_job_message` (child pulls `rig_job_inbox`; not ASK)

REQUIRED parent launch is MCP `rig_job_launch` (pass brief TEXT with files/access/worker/model/effort + owner credentials; the tool creates `.rig/jobs/<id>/` and brief.md — do not precreate that path). Shell `run-worker.sh` is internal/human fallback only (that path may write the brief file first). Human watch: `rig tui` / `/rig` (do not launch a TUI inside this session).

`rig_job_wait`: call **once**, no timeout for normal observable wrapper work. Returns on ASK, result, cancellation, or an explicit reconciliation outcome. After allow, wait **once** more (same ids). Native agents use the owning host's native wait/interrupt and authenticated completion. If MCP wait errors or the transport drops the tool, take one bounded `rig job wait ID --timeout 0` snapshot, then inspect/reconcile; do not blindly resume an indefinite wait. Explicit cancellation never enters this fallback. After implement+verify ok, wait wrapper review+seed together: MCP `ids`.

## Child handshake

Children MUST call `rig_job_inbox` first. Handshake records connected/time/protocol 1. No success without it; fail exact `child MCP handshake missing` while preserving evidence and ownership. Permission bootstrap does not count as handshake. Restricted child tools: inbox/doing/note/ask/own show/project memory. Parent steering uses `rig_job_message`. Legacy/unknown jobs are not retroactively failed. Cursor remains excluded (no safe scoped MCP). Durable `.rig/jobs/<id>/` files: `launcher.log` (prechild), `stdout.log`, `activity.json`, `meta.json`, `result.json`, `inbox.json`, ask/reply, evidence. Detached wrapper survives parent/MCP shutdown. `stdout.log` prunes only after successful decoded activity; failures retain it.

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
- cancellation state separately: `stop-requested` / `stop-unconfirmed` / `native-cancel-required` are not `stopped`
- parent verification: current snapshot, checks/manual method, missing/failed requirements, and independent-review status separately
- task (from the brief)
- doing (last decoded log line or `activity.json` after the raw log is pruned)
- elapsed (from start/end, if present)
- how to watch: MCP `rig_job_log`, or human `rig tui` in another pane
- Grok child: `open` line is `grok -r <session-id>`
- For a Rig job, use MCP `rig_job_log`. Do not read Cursor `state.vscdb`, `~/.cursor` sqlite, or other vendor session stores.

**You answer `ask`** with MCP `rig_job_allow` / `rig_job_deny`. Do not kill or replace a job because the child asked. A `running` label without trustworthy execution observation needs reconciliation; it does not justify waiting forever. User Esc / cancelled wait records durable cancellation for attached attempts and returns promptly; `rig_job_cancel` is the explicit equivalent. Never re-wait, re-pick, or drain queued work automatically after explicit cancellation. Pending queue items and unattached jobs stay. After implement+verify ok, a second job with disjoint listed files may already be running (seed in parallel with read-only review); wait observable wrapper IDs together and use host-native wait for native agents. Only confirmed spawn-never-started failure permits one `--exclude` re-pick with a fresh ID; empty changed files alone do not prove that. Child ran and failed the patch: escalate.

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

Native finish authenticates the same owner and explicit `parent_task` completion or the specific native agent ID/terminal outcome. Obtain that result through host-native wait/interrupt; Rig MCP cannot invoke host tools or signal the parent instead. Parent/MCP PID is not native completion proof. `native-cancel-required` and missing completion keep protection. A wrapper stop request may still be unconfirmed; files and execution slots remain held until the isolated worker and in-tree descendants stop. Reparented leftovers are orphans; they do not block stop or hold the slot. Do not kill orphans on success. Unconfirmed stop is not running; workflow wait returns on attention so the parent can inspect next_parent_action. After confirmed termination the slot can be free, while close still requires exact credentials and rationale to release files without acceptance or retry. Reconcile is report-only unless explicitly applied; live/ASK/unknown ownership never expires by age.

If a user explicitly cancels a native parent write, confirms it stopped, then cannot authenticate normal completion because the saved token or job artifacts are gone, the same initiating parent session can call `rig_job_recover_parent_write` with the exact job ID, `confirmed_stopped=true`, and a rationale. This records cancelled/unverified and releases the held scope without acceptance. It is parent-only and rejects wrappers, native-child jobs, and a different parent session. Do not use it before confirmed termination. Repeated allow/approval does not complete a job, and deleting `.rig/jobs/<id>` does not clear the reservation. If the original session is unavailable, recover from that host/session or close only with exact credentials after confirmed termination; never delete reservation files.

Human TUI: Tab switches Jobs/Queue; `e` opens a nonblocking Unicode editor, Enter saves, Esc cancels the draft, and failed saves retain it. Paste is capped at 2,000 characters. `x` cancels the selected target, `l` toggles in-board activity, and `q` closes the board without stopping jobs. Snapshot age/errors remain visible during slow refreshes.

Parent acceptance with `next=review` retains scope for a fresh independent reviewer attempt, gated by the original writer's current accepted snapshot and actual provider. Use the new holder credentials after transfer. Failed review launch retains protected scope without a slot until a fresh retry or explicit close.

The small HUD selects ASK first, then stopping/reconciliation, active execution/checks, and a recent terminal result. Terminal display expires after 60 seconds; ownership does not. Check progress belongs to that check request, never an earlier wait token. Use the full board for every active ID. See `delegate-harness/SKILL.md` for the full parent workflow.
