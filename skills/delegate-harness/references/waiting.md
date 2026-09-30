<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->
## Fail classes

- `ask` / `running` — allow/deny or wait. Never kill because the child asked. Never replace.
- User Esc / MCP wait cancelled / `rig job cancel` — record durable cancellation for **those attached attempts**, then return promptly. `stop-requested`, `stop-unconfirmed`, and `native-cancel-required` do not prove termination. Never re-wait, re-pick, or drain automatically after explicit cancellation. Preserve parked queue items and unattached jobs.
- spawn never started (binary 127, refuse, native tool error, empty argv, auth/FS before first token) — one re-pick `--exclude <dead worker>`. Same brief, new job id. Last-resort: opencode, omp, pi, agy, codex, cursor. One fallback per user task.
- child ran and the patch failed / timeout / stale — escalate. Do not vendor-shop. Timeout does not unlock grok.
- native implement/hard (`parent_writes`) — parent writes; not a spawn fail.

Use one blocking `rig_job_wait` for observable wrappers, no timeout normally; native agents use owning-host wait/interrupt and authenticated completion. Do not parse a TUI. If MCP is missing before waiting, one normal CLI `rig job wait <id>` is allowed. A dropped/failed wait permits one `rig job wait ID --timeout 0` snapshot, then inspect/reconcile, never a blind polling loop; explicit cancellation never enters this fallback.

ASK: answer `rig_job_allow` / `rig_job_deny`, then wait the same IDs once more. Never kill, close, finish, or replace a job because it asked. Safe read/edit/test/SSH gather/git may be allowed; destructive/prod/secrets require denial or user approval. After terminal `rig_job_wait`, report `job <id> <status> · <tokens> · <elapsed>` (omit tokens when absent).

Workflow waits pass the retained owner_token (and owner_session when explicit): host Stop durably cancels that exact workflow incarnation, including in-flight admissions. Without owner_token, rig_workflow_wait is observation-only; Stop only ends observation. rig_workflow_cancel with credentials is the explicit execution-stop path. Unknown/in-flight nodes remain cancel-requested with ownership held until actual stop. Cancelled workflows never resume implicitly; inspect retained receipts and partial work, then use authenticated continuation for an eligible stopped predecessor, or explicitly request new work after a cancelled predecessor. Existing continuation eligibility is unchanged.
