<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->
## After a run

Overwrite `.rig/STATE.md` with job id, worker, status, summary.
If there is one standing fact, MCP `rig_memory_add`. Do not edit MEMORY.md by hand. Bash fallback if MCP is missing: `rig memory add "one standing fact"`.

Skip if there is no fact. The command drops duplicates and caps the file at about 120 lines. No transcripts.

Upgrade/rollback: stop new admissions, finish or cancel existing work, confirm stopped, accept or close scopes, preserve data (queue text, credentials, workflow spec/state/events, reservations). Update every launcher and managed source skill/protocol, then fully restart all parent/MCP sessions before admitting work. Never run mixed-version admission writers. Combined rollout with wait-cancel follows that sequence. Rollback sets `[orchestration] mode = "single"` and never deletes data.

Preserve worker/cap values, memory, custom agent overrides and unrelated AGENTS content. Prior-revision jobdata remains readable after rollback.
