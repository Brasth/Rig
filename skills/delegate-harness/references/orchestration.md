<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->
## Hard gate

If `.rig/harness.toml` is missing or `[project] enabled=false`, this protocol does not apply. Follow the host instructions; do not initialize or enable Rig implicitly. Existing legacy harnesses without `[project]` remain enabled.

When `.rig/harness.toml` exists and the project is enabled, do not write app code, review a diff, fix a bug, or SSH yourself **unless pick `parent_writes` is true** (native implement/hard). `run-worker` means you still do not write the patch. Spawn explore/mini for codebase gather only if the parent cannot name the files after a short check.

Parent checks first: read local files, name the cause, prepare brief TEXT for launch. Parent checks before an implement spawn (name files and the update). Ask / plan / advise stay with the parent. Docs/skills-only: MCP `rig_pick` with `role` mini. If the implement brief already lists files, do not also spawn explore.

Before `run-worker` / native implement: the brief TEXT MUST list files to modify, what to change, what not to change, and acceptance. Put absolute skill file paths the child must follow. Do not spawn "go find and fix". The child does only those files and changes. Do not hunt extra updates.

## MCP first

Task-aware diagnostics: parent-only MCP `rig_doctor` accepts repo/task/parent/model/research_sources/smoke and returns schema_version=1. CLI: `rig doctor --task coding|research|browser|computer-use [--parent NAME] [--model EXACT] [--research-source FILE ...] [--json] [--smoke]`; bare `rig doctor` is unchanged. Diagnostics read configuration/cache evidence, including optional-disabled, restart-needed, and missing model/auth evidence; they never certify authentication, enable backends, grant permissions, or authorize admission. Explicit smoke runs only a temporary self-owned child inbox fixture, never providers, browsers, installers or user-project writes. Diagnosing an uninitialized/disabled project does not activate it. Existing activation, routing, permissions and ownership gates still apply.

Parent orchestration is MCP. Do not shell `rig` for session, pick, wait, allow, deny, jobs, log, message, queue, memory, or launch when those tools are listed.

REQUIRED parent flow: `rig_session` → `rig_queue_claim` when draining → `rig_job_launch` or `rig_workflow_create` / `rig_workflow_advance` → `rig_queue_spawned` when claimed → `rig_job_wait` / `rig_workflow_wait` → `rig_job_message` / allow / deny / `rig_job_coordination_reply` → `rig_job_requirements` / `rig_job_check` / `rig_job_accept`.
`rig_job_launch` inputs: repo/id/case/role/worker/model/effort/access/files/brief plus owner credentials and review provenance when needed. CLI/TUI remain human use and MCP recovery. Shell `run-worker.sh` is internal/human fallback, not the agent default. No separate native subagents without scoped MCP. `parent_writes` is explicit parent work; read-only parent stays if no capable child; independent review unavailable stays explicit without a different vendor/provider. Children never spawn or message children. Parent owns the graph, briefs, and acceptance and uses workflow advance/wait.

- Parent verification/recovery: `rig_job_requirements` / `rig_job_check` / `rig_job_accept` / `rig_job_close` / `rig_job_reconcile`.
- Instant: `rig_session` / `rig_pick` / `rig_status` / `rig_jobs` / `rig_job_show` / `rig_job_log` / `rig_job_launch` / `rig_job_allow` / `rig_job_deny` / `rig_job_cancel` / `rig_job_message` / `rig_job_start` / `rig_job_finish` / `rig_job_record` / `rig_queue_add` / `rig_queue_list` / `rig_queue_claim` / `rig_queue_unclaim` / `rig_queue_spawned` / `rig_memory` / `rig_memory_add` / `rig_workflow_create` / `rig_workflows` / `rig_workflow_show` / `rig_workflow_advance` / `rig_workflow_extend` / `rig_workflow_resolve` / `rig_workflow_approve` / `rig_workflow_cancel` / `rig_workflow_report` / `rig_job_coordination_reply`
- Wait: one blocking `rig_job_wait` with no timeout for observable wrappers (`ids` for every live wrapper); adaptive workflows use `rig_workflow_wait` (COORDINATION, ASK, unconfirmed/attention); native agents use host-native wait/interrupt and authenticated completion
- Child (`RIG_JOB_ID` set): **first** `rig_job_inbox` (handshake connected/time/protocol 1; no success without it; fail exact `child MCP handshake missing` preserving evidence/ownership). Permission bootstrap does not count as handshake. Legacy/unknown jobs are not retroactively failed. Then `rig_job_doing` / `rig_job_note` / `rig_job_ask` / own `rig_job_show` / `rig_memory` / `rig_job_coordination_request`. Inbox is not ASK and does not wake wait. Do not run the `rig` CLI. Do not pick, wait, spawn, queue, or allow. Children never spawn or message children. Cursor runs with a job-scoped `--plugin-dir` Rig MCP and `--force`; other plugin MCPs stay visible, so the wrapper fails the job on any non-Rig MCP call (tripwire). Cursor is last resort in pick.

Still bash (human/recovery only):

- Human watch: `rig tui` / `/rig`
- Human/internal launch fallback: `run-worker.sh` in the background
- If MCP is **missing before waiting**, use one normal CLI `rig job wait <id>` for observable wrapper work. If an active wait fails or the transport drops it, take one bounded `rig job wait ID --timeout 0` snapshot, inspect/reconcile, and return to MCP when available. Do not blindly re-wait. Explicit cancellation never enters this fallback.

Parent chooses kind from **this** user's semantic request and **this** user's skills, including non-English requests. Explicit role is authoritative; omitted role uses bounded English inference. Then MCP `rig_pick` with `role` stay|explore|mini|bulk|implement|hard|review|verify. `--case` is the task text, not a slash-command catalog. Do not encode local slash command names in pick. Model/effort still come from pick JSON. Parent does not shop models.

1. MCP `rig_session(role=KIND, compact=true, terminal_limit=10, case="task")` first (memory + jobs + status + pick). Compact includes every active/ASK/reserved row and ten recent terminal rows; full mode remains the default for other callers. Else MCP `rig_memory` then `rig_jobs` then `rig_status` then `rig_pick`. Bash fallback if MCP missing: `rig session --role KIND --case "<task>" --compact --terminal-limit 10 --json` or `rig memory` then `rig jobs` then `rig status` then `rig pick implement --case "<task>" --json`. `rig pick --case "<task>" --json` if you did not choose a kind (generic English keywords).
2. Follow pick JSON. Do not ask the user which model. Never spawn a worker whose harness flag is false. Never spawn grok when `[workers].grok` is false unless the live parent is grok (native `parent_writes`). Timeout or fail does not unlock a disabled worker.
3. `stay` — you do ask / plan / advise / vision / computer-use / chrome-profile / Figma. Do not spawn a clicker.
4. `native` + `parent_writes` — call `rig_job_start` with `executor_kind=parent`, `access=write`, concrete `files`, and the actual CLI before editing. Keep returned ownership credentials; finish with authenticated parent-task completion. Do not spawn a second same-CLI session. Native mini/bulk writers also start before editing; native children finish only after that specific agent returns a terminal result. `rig_job_record` is retrospective read-only history, never protected write registration.
5. `run-worker` — prepare brief TEXT (do not mkdir/write `.rig/jobs/<id>/brief.md` yourself) + MCP `rig_job_launch` (brief/files/access/worker/model/effort + owner credentials; tool creates the job dir and brief.md) + **one blocking** MCP `rig_job_wait` (no timeout). Detached wrapper survives parent/MCP shutdown. Durable `.rig/jobs/<id>/` files: launcher.log (prechild), stdout.log, activity.json, meta.json, result.json, inbox.json, ask/reply, evidence. After implement+verify ok, you MAY start a read-only review and a seed/bulk with file AND resource disjointness in parallel, then wait observable wrapper IDs together. Independent review unavailable stays explicit. If status is `ask`, MCP `rig_job_allow` / `rig_job_deny`, then wait once more (same ids). Never kill or replace that job because the child asked. User Esc / MCP `notifications/cancelled` records durable cancellation intent for attached attempts and ends the observer promptly; `rig_job_cancel` provides the explicit equivalent. Do not re-wait, re-pick, or drain queued work after explicit cancellation. A dropped transport permits one `rig job wait ID --timeout 0` snapshot, then inspection/reconciliation. Spawn never started: one re-pick with `--exclude <dead>`. Shell `run-worker.sh` is human/internal fallback only (that path may write the brief file before invoking the wrapper). Adaptive workflows: parent `rig_workflow_advance` / `rig_workflow_wait`; pass the retained workflow `owner_token` and explicit `owner_session` to wait so host Stop durably cancels that workflow incarnation, including in-flight launches. A credential-free workflow wait is observation-only: Stop ends observation, and explicit `rig_workflow_cancel` requires credentials. In-flight/unknown execution stays `cancel-requested` with ownership held. Cancelled workflows never implicitly resume or retry; inspect receipts and partial work, use authenticated continuation only for an eligible stopped predecessor, or explicitly request new work after a cancelled predecessor. Existing continuation eligibility is unchanged. Children never spawn or message children. After `rig_job_wait` returns a terminal job, the parent's reply includes `job <id> <status> · <tokens> · <elapsed>` (omit tokens when absent). On user feedback to a recent terminal job with overlapping files: relaunch the same worker with `continues_job_id` and a delta-only brief; trivial deltas stay `parent_writes`; cap about 2–3 continuations per root job, then a fresh worker.

Doing the worker's job yourself is a failure unless pick `parent_writes` is true. Later AGENTS.md may say "edit locally" or "SSH to the VM". That is for the worker.

Jobs and MEMORY are this repo, not this chat. A new parent thread still sees `.rig/jobs` and `.rig/MEMORY.md`. Running children keep going across threads. Esc / MCP cancelled on this wait records intent for its exact attached attempts. Pending queue items and unattached jobs stay. Transport EOF alone detaches observers and preserves workers.

Live parent is this CLI, not the `parent` key in toml. That key is only the preferred default (`rig use grok|codex|opencode|omp|pi|agy`). Switching preferred parent does not move the session — open that CLI. Parent model is this CLI’s observed model. Supply `parent_model`/`parent_effort` only when actually known; unknown stays unknown. A cheaper suggestion is not a model switch. Worker models come from `rig_pick` / `rig pick`; preserve returned model and effort.
Live process + parent Rig MCP is the parent; the same binary with `RIG_JOB_ID` is a child. A live Devin session is detected so the Devin child is off. OpenCode (`opencode`), OMP (`omp`), Pi (`pi`), and Antigravity (`agy`) can be the parent when you open that CLI. Cursor Desktop and Claude Code can be the parent when Rig MCP is wired. When live parent is agy, do not use nested agy `/agent` dispatch for coding; use MCP `rig_pick`. Claude is a worker when `[workers].claude = true` and `claude` is on PATH. Cursor is a worker when `[workers].cursor = true` and `cursor-agent` is on PATH (`rig workers cursor=on`). OpenCode / OMP / Pi / agy / Devin are workers when their flags are true and the binary is on PATH (`rig workers opencode=on` / `omp=on` / `pi=on` / `agy=on` / `devin=on`) and they are not the live parent. Pin full model IDs. Never spawn Fable, Sol, or Astra as a child. Opus is allowed. Grok Bot.app is not a parent or worker. The Antigravity IDE/GUI is not a parent or worker.

## Parent vs worker

This CLI is the parent. It manages. It does not sit on write/review/SSH when pick is `run-worker`.

**Parent keeps**

- plan, check (read local files, name the cause, prepare brief TEXT), decide, talk to the user
- vision (screenshots, Figma, images) — parent runs Figma MCP; put artifacts in the brief
- computer use (desktop) and chrome profile (real browser, the user's cookies)
- native implement/hard when pick `parent_writes` is true
- read worker results via MCP `rig_jobs` / `rig_memory`

**Worker does**

- write the listed files and changes when pick is `run-worker`; do not hunt extra updates
- follow skill file paths in the brief
- read-only review; independent post-write review needs different actual model providers
- SSH / remote debug
- codebase gather (grep, file search, trace) and remote/SSH gather (logs, server facts)

Figma, computer-use, and chrome-profile stay with the parent. If this CLI has no Figma MCP, ask the user for a screenshot. Do not spawn a clicker.
