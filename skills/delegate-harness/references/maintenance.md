<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->
## After a run

Overwrite `.rig/STATE.md` with job id, worker, status, summary.
If there is one standing fact, MCP `rig_memory_add`. Do not edit MEMORY.md by hand. Bash fallback if MCP is missing: `rig memory add "one standing fact"`.

For an outdated fact, parent reads `rig_memory` structured sha256/facts, then uses `rig_memory_replace` or `rig_memory_remove` with that expected_sha256 and the exact selected fact. Stale hashes require rereading; never silently retry against changed memory. These two tools are parent-only.

Skip if there is no fact. The command drops duplicates and caps the file at about 120 lines. No transcripts.

Upgrade/rollback: stop new admissions, finish or cancel existing work, confirm stopped, accept or close scopes, preserve data (queue text, credentials, workflow spec/state/events, reservations). Update every launcher and managed source skill/protocol, then fully restart all parent/MCP sessions before admitting work. Never run mixed-version admission writers. Combined rollout with wait-cancel follows that sequence. Compatible controller rollback preserves `[orchestration] mode` and all data; incompatible versions require a separate reviewed migration.

Preserve worker/cap values, memory, custom agent overrides and unrelated AGENTS content. Prior-revision jobdata remains readable after rollback. Runtime rollback never deletes data.

Safe runtime updates are explicit: `rig update --revision FULL_COMMIT_SHA --dry-run`, then the same revision without `--dry-run`. Bare `rig update` is usage only. The controller checks versioned compatibility and owned-file hashes, never runs setup/optional installers, and never migrates data or changes configuration. `rig update --rollback` restores only the previous successful controller transaction. Legacy/unversioned installs require an explicit manual-backup/bootstrap transition, not fabricated provenance. A pending update blocks new admissions and leases; use `rig update --status`, then `rig update --recover`. Never delete the marker or kill processes to bypass checks; fully restart parent/MCP sessions afterward.
