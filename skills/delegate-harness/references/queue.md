<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->
## Queue drain

On a free parent turn after compact `rig_session`:

1. List pending items in priority/oldest order. Stay/advise stays local. Name concrete files; serialize shared DB/port/VM work that file checks cannot represent.
2. Skip overlapping or capped items; try the next ID. Claim **by id** when more than one item is pending, with selected `worker`, `access`, JSON `files`, and initiating `owner_session`.
3. Save the claim response `reservation_id`, `attempt_id`, `owner_token`, and owner. Same-ID reuse is never launch permission. No-worker compatibility claims reserve a slot conservatively.
4. Prepare brief TEXT (do not precreate `.rig/jobs/<id>/` for MCP). If preparing it fails, `rig_queue_unclaim` requires that unconsumed claim's exact credentials and initiating session.
5. MCP `rig_job_launch` consumes the claim (brief text + credentials + files/access); the tool creates the job dir and brief.md. Wrapper env uses `RIG_QUEUE_ID`, `RIG_RESERVATION_ID`, `RIG_ATTEMPT_ID`, `RIG_OWNER_TOKEN`, `RIG_OWNER_SESSION`, `RIG_ACCESS`, and `RIG_JOB_FILES_JSON`. Native start passes the equivalent MCP fields. Worker/files/access must match. Shell `run-worker.sh` remains human/internal fallback only (may write `.rig/jobs/<id>/brief.md` then pass that path).
6. `rig_queue_spawned` is an authenticated acknowledgement with the same queue/job/attempt credentials, not another claim. Wait once on live wrapper IDs through MCP; use host-native wait and authenticated completion for native agents. ASK: allow/deny that ID; wait the same wrapper IDs again. Do not claim during ASK.

`/queue`, hooks, and HUD refresh only park/read. Cancellation never silently returns work to pending. Legacy claims without credentials block conservatively; reconcile explicitly rather than guessing a token.

Successful queue add returns the committed item ID/text receipt; the Grok/Codex submit hook does not collect a full QUEUE/HUD block before acknowledging it. List separately when requested. Optional MCP `idempotency_key` / CLI `--idempotency-key` makes retries of one logical submission return its existing item; repeated text without a key stays distinct.

CLI queue claims use a verified durable parent owner. If automatic detection is unavailable, pass `--owner-pid` for a known live ancestor plus `--owner-session`; arbitrary live PIDs are refused. Plain claim output gives the private `.rig/queue/credentials/<queue-id>.json` artifact path (mode 0600); `--json` also returns exact credentials. Save the returned response/path privately and preserve it for launch/unclaim. Never infer ownership from the transient claim-command PID.

JSON file lists preserve spaces, Unicode and literal bracket names. `rig_queue_spawned` cannot regress done/cancelled state. Queue list shows occupied files; skip overlap and try the next item.
