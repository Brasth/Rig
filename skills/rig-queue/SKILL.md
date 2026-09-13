---
name: queue
description: >
  Park a Rig work item in .rig/queue/. Use when the user types /queue, wants to
  enqueue work while a child is running, or asks to list/cancel the work queue.
  Does not spawn a child. Does not wait. Does not allow/deny.
user-invocable: true
disable-model-invocation: true
argument-hint: "[text|cancel <id>]"
---

# Rig queue

Park user work in `.rig/queue/`. This is **not** a spawn. This is **not** ASK. This is **not** child inbox.

Works in Grok, Codex, OpenCode, OMP, Pi, and agy. Same files for every parent.

## Do

- If the user passed text (and it is not `cancel …`): add it.
- If they passed `cancel <id>`: cancel that pending item.
- If they passed nothing: list pending + `live/max`.
- MCP: `rig_queue_add` / `rig_queue_list` / `rig_queue_cancel`. Do not shell `rig queue` when those tools are listed.
- Bash fallback if MCP is missing: `rig queue add "…"`, `rig queue list`, `rig queue cancel <id>`.
- After add, print the committed item ID/text receipt and stop. Do not collect a full QUEUE/HUD block before acknowledging it. Print the full queue only for an explicit list request.
- Optional MCP `idempotency_key` or CLI `--idempotency-key KEY` returns the existing item when the same logical submission is retried. Without a key, repeated text remains separate work.

## Do not

- Do not spawn a worker.
- Do not call `rig_job_wait`, allow, or deny.
- Do not encode `/queue` into `rig pick --case`.
- Do not spawn from `/queue` itself. Parking is this command. Drain is the parent on a **free** turn (claim → brief → spawn → observe each executor).
- On Grok and Codex, a UserPromptSubmit hook parks `/queue text` (Codex also `$queue park …`), returns the committed receipt, and **blocks** the prompt. HUD refresh failure does not undo an acknowledged item. Codex: `rig setup`, then `/plugins` **Rig Queue** and `/hooks` trust, fully quit once. Codex 0.154 has no `/prompts:queue` slash. Bare `/queue` still needs a free turn to list.
- OpenCode plugin parks `/queue` / `$queue` (fully quit once after setup). `/queue` throws `__RIG_QUEUE_HANDLED__` after park so OpenCode 1.17 skips `prompt()` (TUI may show that error on 1.17.5+). TUI HUD is `tui.json` → `rig-hud.tsx` (file path). OMP/Pi `/queue` is an extension command (HUD under the editor) and runs even while streaming. agy: setup probes the binary for UserPromptSubmit; 1.2.0 does not have it — `rig tui` `e` or `rig queue add`. agy/Grok statusline shows QUEUE. Codex: `/plugins` Rig Queue then `/hooks` (same hook; no custom panel).

MCP drain (not this `/queue` command): compact `rig_session` with explicit role → `rig_queue_list` → `rig_queue_claim` with `id`, selected `worker`, `access`, JSON `files`, and initiating `owner_session` → preserve returned credentials → brief → authenticated wrapper/native launch → matching `rig_queue_spawned` acknowledgement → one `rig_job_wait` on observable wrapper IDs, or host-native wait and authenticated completion for native agents. Explicit cancellation never triggers re-wait, re-pick, or automatic queue drain; parked items stay.

List shows occupied files. Claim **by id** when more than one item is pending (omit id only if exactly one pending). Drain is **not** this command — see `skills/delegate-harness/SKILL.md` Queue drain: skip overlap, try the next id, never FIFO-fill conflicting writers.

On a subsequent free orchestration turn, if live jobs < cap and the item is implement-like: parent checks, claims **that id** with listed files, briefs, spawns with JSON scope and returned ownership credentials, and acknowledges `rig_queue_spawned` with those exact credentials. Wait on wrappers through MCP and native agents through the owning host. The parking command itself stops after its receipt. Stay/advise: answer here, do not spawn. `parent_writes`: do not drain more writers this turn.

Bash fallback if MCP is missing:

```bash
rig queue add "fix pagination on the jobs list"
rig queue list
rig queue cancel <id>
umask 077
rig queue claim <id> --worker grok --access write --files-json '["src/a.py","src/b.py"]' --owner-session "$RIG_OWNER_SESSION" --json > .rig/claim-response.json
```

Claims reserve execution slots and file scope atomically. Count reserved/running/ASK once against the existing global/per-worker caps. Stopped work frees its slot while file protection remains through verification/review. Read/read overlap is allowed; a writer conflicts with held readers/writers; unknown write scope is exclusive. Preserve JSON names with spaces, Unicode, and literal brackets. Legacy `--files` comma input cannot represent every filename.

A claim response returns `reservation_id`, `attempt_id`, `owner_token`, and owner. CLI claims bind to a verified durable parent process, not the transient claim command. If automatic detection is unavailable, provide `--owner-pid` for a known live ancestor and `--owner-session`; arbitrary live PIDs are refused. CLI `credentials_path` identifies the private `.rig/queue/credentials/<queue-id>.json` artifact (mode 0600); plain output shows its path, and `--json` includes the exact credentials. MCP returns them in `structuredContent`. Save that response/path privately and never print tokens in summaries. Wrapper consumes them using `RIG_QUEUE_ID`, `RIG_RESERVATION_ID`, `RIG_ATTEMPT_ID`, `RIG_OWNER_TOKEN`, `RIG_OWNER_SESSION`, `RIG_ACCESS`, and `RIG_JOB_FILES_JSON`. Native start takes equivalent fields. Job ID alone never authorizes a launch or overwrite.

`rig_queue_unclaim` requires the exact unconsumed attempt credentials. `rig_queue_spawned` requires the matching activated queue/job/attempt and cannot regress done/cancelled work. CLI acknowledgements use `--reservation-id`, `--attempt-id`, `--owner-session`; token comes from `RIG_OWNER_TOKEN`. If the brief or launch fails before execution, compensation may return that same claim to pending; cancellation never silently retries it.

Legacy claimed items without credentials remain held. Parent-only `rig_job_reconcile(queue_id=..., action=report)` inspects them; explicit `apply=true` with adopt/release, owner, rationale, selected worker/access/files, and stopped-work attestation resolves only supported cases. Observed live work cannot be overridden. This `/queue` parking skill never performs recovery, checks, or acceptance on its own.
