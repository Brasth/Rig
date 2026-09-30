<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->
## Adaptive workflows

`[orchestration]` mode is `adaptive` (default) or `single`; `max_nodes` is 12. Queue and worker caps remain authoritative. Adaptive decomposes eligible work into a DAG of at most `max_nodes`. Automatic safe decomposition: writers must be file AND resource disjoint; overlapping writer scopes are rejected, not sequenced; unknown write scope is exclusive; read overlap with a writer is only after that writer is accepted; multiple writers or any side effects require a final `verify` node (automatic `final-verify` if omitted). `single` keeps one-job behavior. Children never spawn or message children. Parent owns the graph, briefs, and acceptance and uses `rig_workflow_advance` / `rig_workflow_wait`.

Durable files under `.rig/workflows/<id>/`: `spec.json`, `state.json`, `events/`, `owner-credentials.json` (mode 0600). Create returns `credentials_path`; never print owner tokens. Workflow statuses: `planned`, `running`, `attention`, `blocked`, `completed-unverified`, `verified`, `failed`, `cancel-requested`, `cancelled`. Workflow `verified` only after required nodes (and final verify when present) have current parent acceptance. `completed-unverified` means execution finished without that acceptance.

**Verify vs review.** `verify` is parent/final integration (role `verify`, including automatic `final-verify`). `review` is independent post-write review. Independent review unavailable stays explicit.

Parent-only MCP: `rig_workflow_create` (spec object; optional `queue_id` / `owner_session`), `rig_workflows` (accepted/required, running/ASK, blocker, next parent action; no ETA), `rig_workflow_show`, `rig_workflow_advance` (refresh then launch ready nodes up to capacity; refuses any repo ASK; stops on cancel, unresolved failure, or coordination; `parent_writes` returns one registered parent action and launches no siblings that turn), `rig_workflow_wait` (wakes COORDINATION and ASK; shows sibling node status; do not pass timeout unless you must cap the wait), `rig_workflow_extend` (append-only; cannot alter launched nodes or contracts; no extension after final verify launches), `rig_workflow_resolve` (`retry` identical stopped/released, `skip` with rationale, or `fail`; required nodes cannot be silently waived; accepted nodes cannot retry), `rig_workflow_approve` (gated `external` | `production` | `destructive`; bound to workflow+node+owner session+current spec hash; invalidated by spec change), `rig_workflow_cancel` (freeze advancement; cancel active attempts and unstarted nodes; unrelated jobs and queues stay; confirmed-stop semantics unchanged), `rig_workflow_report` (observed wall time, node time, max concurrency, outcomes, acceptance; no estimated progress, savings, or ETA), `rig_job_coordination_reply` (`reply` | `stop`; coordination never expands files, resources, effects, or frozen contracts).

Child after handshake may `rig_job_coordination_request` (`dependency` | `contract` | `scope`). Resource claims are opaque `{name, access}` with `read|write`; never secrets.

CLI: `rig workflows [--json]`; `rig workflow create [--file PATH] [--json]`; `rig workflow show|advance|wait|extend|resolve|approve|cancel|report <id>`.

Queue lifecycle when bound to a workflow: claimed on create from a parked pending item, spawned after the first node, done only after verification, cancelled on cancel. A workflow-bound queue is never pending.

UI (`rig tui` Tab Jobs/Queue/Workflows): workflow id, status, accepted/required, running, ASK, blocker, next parent action, title. No estimated progress, savings, or ETA.

Combined rollout with wait-cancel: stop new admissions, finish or cancel existing work, confirm stopped, accept or close scopes, preserve data, update every launcher and managed protocol, then fully restart all parent/MCP sessions. Rollback sets `[orchestration] mode = "single"` and never deletes data.
