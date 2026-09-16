---
date: 2026-09-13
status: implementation-validated
---

# Wait and cancellation responsiveness

## Context

Codex and `rig tui` could appear frozen after wait/cancel, with no visible worker and no responsive path to the next request. Reproduction held the real admission `flock`: synchronous cancellation on the MCP reader blocked a subsequent ping. Missing process identity and native tasks could wait indefinitely; EOF also joined wait threads without a deadline.

## Decisions and changes

The MCP reader now dispatches tool work separately. Cancellation records durable intent for the exact attached attempts and ends the observer promptly. Intent is distinct from confirmed `stopped`: unknown execution keeps its slot and files, and confirmed cancelled work requires explicit close to release file ownership. Pending queue items stay; explicit cancellation never triggers re-wait, re-pick, or automatic drain.

Rig cannot invoke host-native interruption tools. Native agents require the owning host to wait or interrupt that specific agent, then submit authenticated terminal completion. Parent/MCP PID liveness is not task completion evidence. Unknown observation reports reconciliation needs instead of an indefinite Rig wait.

Wrapper shutdown uses bounded process-tree TERM/KILL attempts, process identity checks, and zombie-aware liveness. Transport loss detaches observers without implicitly stopping workers; recovery uses one bounded status snapshot. TUI snapshots/actions run off the input thread, with a nonblocking Unicode queue editor. Queue receipts acknowledge committed storage; CLI claims preserve a verified durable owner and private attempt credentials.

## Validation and lessons

Tests cover real lock contention and bounded process shutdown, including stubborn descendants and process-identity safety. Focused TUI tests exercise stalled scans/actions, sub-100 ms key handling, Unicode paste/editing, and draft recovery; real curses PTY interaction also passed. Documentation tests execute the private-credentials native lifecycle example and check the generated protocol.

The preliminary integrated run covered 578 tests and exposed compatibility assertions needing correction. One failure identified the managed AGENTS template's no-apostrophe invariant; wording was corrected while preserving the existing guard. The [verified full suite](../../plans/260913-wait-cancel-responsiveness/reports/full-suite-verified.log) then passed **580 tests in 108.722 seconds**.

After final TUI footer and MCP schema changes, **29 TUI tests** and **33 MCP tests** passed; two TUI tests were added after the full suite began, so no 582-test full-suite run is claimed. Focused wait/process runs passed 5/7 tests. Python compilation, Bash syntax, and diff checks passed. Final review reported no remaining concrete defects.

## Sept16 installed-smoke note

Local install and user cold restart happened on 2026-09-16. Live workflow `installed-smoke-20260916` observed child handshake plus ASK approval/resume. Fan-out then failed with `job id already belongs to an attempt; use a fresh job id` because `jobs.new_job_id` used timestamp+PID only. That smoke was cancelled and the stopped reader scope closed.

Fix job `20260916-job-id-collision-fix` adds a uuid4 hex suffix while preserving timestamp/PID and explicit IDs. Parent accepted the current snapshot after 156 jobs/launch/workflow/admission/queue tests passed in 7.423s; the old-helper injection regression fails once and the fixed helper passes. Compilation and diff checks passed. No independent reviewer was eligible (only xAI worker).

Metadata writer `20260916-metadata-race-fix` timed out but left its patch on disk. Parent focused validation of that surface passed **111 tests**. A later parent `full1015` run hit a HUD timeout, stopped the interactive shell, and failed on a stale journal assertion; that full-suite rerun remains pending. No invented success is recorded for those incomplete runs.

Partial live handshake+ASK really occurred during the Sept16 installed-smoke attempt. Combined rollout with adaptive workflow remains pending successful installed smoke: corrected runtime reinstall, cold restart, and final live smoke are still outstanding. This note does not claim final installed-smoke success or combined-rollout success.

## Next steps

Sept13 implementation and automated validation remain as recorded above. Sept16 installed smoke partially exercised handshake/ASK but hit job-id collision; the collision fix is parent-accepted in-tree. Metadata-race patch is present after writer timeout; focused 111 passed; full1015 rerun after HUD/shell stop is pending. Still pending: reinstall the corrected runtime, cold restart, and rerun live installed smoke so combined rollout with adaptive workflow can finish. Mixed-version admission writers remain unsupported. Do not treat this entry as final installed-rollout success or combined-rollout success.

Unresolved questions: none.
