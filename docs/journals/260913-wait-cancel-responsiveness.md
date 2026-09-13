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

## Next steps

Implementation and automated validation are complete. Rig has not been installed from this checkout, and the real installed Codex end-to-end wait/Stop/next-input workflow remains untested. Deployment remains separate: stop admissions, finish/cancel and reconcile held work, update every launcher and managed protocol, then restart all parent/MCP sessions together. Mixed-version admission writers remain unsupported. No production installation or Git publication was performed for this entry.

Unresolved questions: none. Installation and installed Codex smoke testing remain pending.
