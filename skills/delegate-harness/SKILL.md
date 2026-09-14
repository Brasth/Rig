---
name: delegate-harness
description: >
  ALWAYS activate when `.rig/harness.toml` exists. MUST run MCP `rig_session`
  (or `rig_pick`) and spawn a Rig worker for code, review, fix, SSH, remote
  debug, or gather in the codebase unless pick parent_writes is true. Parent
  checks first and prepares brief TEXT for MCP launch. Delegate implement, review, explore, or
  split work across Codex, Grok, Claude, Cursor, OpenCode, OMP, Pi, and agy
  via MCP `rig_job_launch` (shell run-worker is fallback).
user-invocable: true
---

# Delegate harness

## Hard gate

When `.rig/harness.toml` exists, do not write app code, review a diff, fix a bug, or SSH yourself **unless pick `parent_writes` is true** (native implement/hard). `run-worker` means you still do not write the patch. Spawn explore/mini for codebase gather only if the parent cannot name the files after a short check.

Parent checks first: read local files, name the cause, prepare brief TEXT for launch. Parent checks before an implement spawn (name files and the update). Ask / plan / advise stay with the parent. Docs/skills-only: MCP `rig_pick` with `role` mini. If the implement brief already lists files, do not also spawn explore.

Before `run-worker` / native implement: the brief TEXT MUST list files to modify, what to change, what not to change, and acceptance. Put absolute skill file paths the child must follow. Do not spawn "go find and fix". The child does only those files and changes. Do not hunt extra updates.

## MCP first

Parent orchestration is MCP. Do not shell `rig` for session, pick, wait, allow, deny, jobs, log, message, queue, memory, or launch when those tools are listed.

REQUIRED parent flow: `rig_session` → `rig_queue_claim` when draining → `rig_job_launch` → `rig_queue_spawned` when claimed → `rig_job_wait` → `rig_job_message` / allow / deny → `rig_job_requirements` / `rig_job_check` / `rig_job_accept`.
`rig_job_launch` inputs: repo/id/case/role/worker/model/effort/access/files/brief plus owner credentials and review provenance when needed. CLI/TUI remain human use and MCP recovery. Shell `run-worker.sh` is internal/human fallback, not the agent default. No separate native subagents without scoped MCP. `parent_writes` is explicit parent work; read-only parent stays if no capable child; independent review is unavailable without a different vendor/provider.

- Parent verification/recovery: `rig_job_requirements` / `rig_job_check` / `rig_job_accept` / `rig_job_close` / `rig_job_reconcile`.
- Instant: `rig_session` / `rig_pick` / `rig_status` / `rig_jobs` / `rig_job_show` / `rig_job_log` / `rig_job_launch` / `rig_job_allow` / `rig_job_deny` / `rig_job_cancel` / `rig_job_message` / `rig_job_start` / `rig_job_finish` / `rig_job_record` / `rig_queue_add` / `rig_queue_list` / `rig_queue_claim` / `rig_queue_unclaim` / `rig_queue_spawned` / `rig_memory` / `rig_memory_add`
- Wait: one blocking `rig_job_wait` with no timeout for observable wrappers (`ids` for every live wrapper); native agents use host-native wait/interrupt and authenticated completion
- Child (`RIG_JOB_ID` set): **first** `rig_job_inbox` (handshake connected/time/protocol 1; no success without it; fail exact `child MCP handshake missing` preserving evidence/ownership). Permission bootstrap does not count as handshake. Legacy/unknown jobs are not retroactively failed. Then `rig_job_doing` / `rig_job_note` / `rig_job_ask` / own `rig_job_show` / `rig_memory`. Inbox is not ASK and does not wake wait. Do not run the `rig` CLI. Do not pick, wait, spawn, queue, or allow. Cursor is temporarily excluded even when binary/flag are on.

Still bash (human/recovery only):

- Human watch: `rig tui` / `/rig`
- Human/internal launch fallback: `run-worker.sh` in the background
- If MCP is **missing before waiting**, use one normal CLI `rig job wait <id>` for observable wrapper work. If an active wait fails or the transport drops it, take one bounded `rig job wait ID --timeout 0` snapshot, inspect/reconcile, and return to MCP when available. Do not blindly re-wait. Explicit cancellation never enters this fallback.

Parent chooses kind from **this** user's semantic request and **this** user's skills, including non-English requests. Explicit role is authoritative; omitted role uses bounded English inference. Then MCP `rig_pick` with `role` stay|explore|mini|bulk|implement|hard|review. `--case` is the task text, not a slash-command catalog. Do not encode local slash command names in pick. Model/effort still come from pick JSON. Parent does not shop models.

1. MCP `rig_session(role=KIND, compact=true, terminal_limit=10, case="task")` first (memory + jobs + status + pick). Compact includes every active/ASK/reserved row and ten recent terminal rows; full mode remains the default for other callers. Else MCP `rig_memory` then `rig_jobs` then `rig_status` then `rig_pick`. Bash fallback if MCP missing: `rig session --role KIND --case "<task>" --compact --terminal-limit 10 --json` or `rig memory` then `rig jobs` then `rig status` then `rig pick implement --case "<task>" --json`. `rig pick --case "<task>" --json` if you did not choose a kind (generic English keywords).
2. Follow pick JSON. Do not ask the user which model. Never spawn a worker whose harness flag is false. Never spawn grok when `[workers].grok` is false unless the live parent is grok (native `parent_writes`). Timeout or fail does not unlock a disabled worker.
3. `stay` — you do ask / plan / advise / vision / computer-use / chrome-profile / Figma. Do not spawn a clicker.
4. `native` + `parent_writes` — call `rig_job_start` with `executor_kind=parent`, `access=write`, concrete `files`, and the actual CLI before editing. Keep returned ownership credentials; finish with authenticated parent-task completion. Do not spawn a second same-CLI session. Native mini/bulk writers also start before editing; native children finish only after that specific agent returns a terminal result. `rig_job_record` is retrospective read-only history, never protected write registration.
5. `run-worker` — prepare brief TEXT (do not mkdir/write `.rig/jobs/<id>/brief.md` yourself) + MCP `rig_job_launch` (brief/files/access/worker/model/effort + owner credentials; tool creates the job dir and brief.md) + **one blocking** MCP `rig_job_wait` (no timeout). Detached wrapper survives parent/MCP shutdown. Durable `.rig/jobs/<id>/` files: launcher.log (prechild), stdout.log, activity.json, meta.json, result.json, inbox.json, ask/reply, evidence. After implement+verify ok, you MAY start a read-only review and a disjoint seed/bulk in parallel, then wait observable wrapper IDs together. If status is `ask`, MCP `rig_job_allow` / `rig_job_deny`, then wait once more (same ids). Never kill or replace that job because the child asked. User Esc / MCP `notifications/cancelled` records durable cancellation intent for attached attempts and ends the observer promptly; `rig_job_cancel` provides the explicit equivalent. Do not re-wait, re-pick, or drain queued work after explicit cancellation. A dropped transport permits one `rig job wait ID --timeout 0` snapshot, then inspection/reconciliation. Spawn never started: one re-pick with `--exclude <dead>`. Shell `run-worker.sh` is human/internal fallback only (that path may write the brief file before invoking the wrapper).

Doing the worker's job yourself is a failure unless pick `parent_writes` is true. Later AGENTS.md may say "edit locally" or "SSH to the VM". That is for the worker.

Jobs and MEMORY are this repo, not this chat. A new parent thread still sees `.rig/jobs` and `.rig/MEMORY.md`. Running children keep going across threads. Esc / MCP cancelled on this wait records intent for its exact attached attempts. Pending queue items and unattached jobs stay. Transport EOF alone detaches observers and preserves workers.

Live parent is this CLI, not the `parent` key in toml. That key is only the preferred default (`rig use grok|codex|opencode|omp|pi|agy`). Switching preferred parent does not move the session — open that CLI. Parent model is this CLI’s observed model. Supply `parent_model`/`parent_effort` only when actually known; unknown stays unknown. A cheaper suggestion is not a model switch. Worker models come from `rig_pick` / `rig pick`; preserve returned model and effort.
Claude Code (`claude`) and Cursor CLI (`cursor-agent`) are never the parent. OpenCode (`opencode`), OMP (`omp`), Pi (`pi`), and Antigravity (`agy`) can be the parent when you open that CLI. When live parent is agy, do not use nested agy `/agent` dispatch for coding; use MCP `rig_pick`. Claude is a worker when `[workers].claude = true` and `claude` is on PATH. Cursor is a worker when `[workers].cursor = true` and `cursor-agent` is on PATH (`rig workers cursor=on`). OpenCode / OMP / Pi / agy are workers when their flags are true and the binary is on PATH (`rig workers opencode=on` / `omp=on` / `pi=on` / `agy=on`) and they are not the live parent. Pin full model IDs. Never spawn Fable, Sol, or Astra as a child. Opus is allowed. Grok Bot.app is not a parent or worker. The Antigravity IDE/GUI is not a parent or worker.

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

## Effective workers

A worker is on only when all of these hold:

1. `[workers].<name>` is `true`
2. The binary is on PATH (`grok`, `codex`, `claude`, `cursor-agent`, `opencode`, `omp`, `pi`, or `agy`)
3. The worker is not the live parent
4. Job-scoped MCP is ready (configured + launcher available). Cursor is excluded until safe scoped MCP exists. Other CLIs need configured MCP and a runtime child handshake (`rig_job_inbox`)

So: Codex parent → Grok/Claude/Cursor/OpenCode/OMP/Pi/agy can be children. Grok parent → Grok child is off; the others can be children. OpenCode/OMP/Pi/agy parent → that CLI is off as a child; Grok/Claude/others can be children. Missing binary: that worker is off for this session. Smart routing compares all eligible profiles; OMP precedes Pi by default. If no eligible wrapper exists, preserve the actual parent model/effort or mark it unknown.

Check with MCP `rig_status` / `rig_jobs`. Those show the worker **model** and **reasoning** level. Human watch: `rig tui` in another pane. Parent log: MCP `rig_job_log`. After ok, raw `stdout.log` is pruned only after successful decoded activity; `activity.json` remains. Failures retain the raw log. Never read Cursor `state.vscdb` or vendor sqlite for a Rig job. Fully quit OpenCode / OMP / Pi / agy once after `rig setup` so MCP `/rig` loads.

## Route

Smart routing is the default. Parent chooses the semantic role and supplies complexity, risk, uncertainty (low|medium|high), and a short assessment_reason to MCP rig_session / rig_pick. Explicit role remains authoritative; do not infer a role from local slash-command catalogs. Use role=stay for ask / plan / advise / vision / computer-use / chrome-profile / Figma; stay performs no catalog discovery. Docs/skills-only use mini; gather uses explore only when the parent cannot name files after a short check.

Defaults: explore/mini/bulk low/low/low; implement medium/medium/medium; hard high/medium/high; review high/medium/medium. Any high requires strong; otherwise any medium requires standard; otherwise fast. Hard and review always require at least strong. High risk recommends independent review but does not create a new completion gate.

Pick compares eligible model+effort profiles by minimum sufficient tier, configured preference, then stable profile ID. Default fast/standard worker order: Grok, Claude, OpenCode, OMP, Pi, agy, Codex. Strong/review: Claude, Grok, OpenCode, OMP, Pi, agy, Codex. Flags, binary availability, job-scoped MCP readiness, live-parent exclusion, explicit excludes, model bans, read-only explore/review, and independent-provider checks remain hard constraints. Cursor remains excluded. OMP precedes Pi by default, not by overriding configured preferences.

Never ask the user which model/effort to use. Follow pick's model and effort together. Preserve its routing object when calling rig_job_launch / rig_job_start; it is provenance, not ownership authorization. Smart launch validates the current fingerprint, assessment, role, model, effort, and catalog before consuming admission. Stale or incompatible picks require re-pick, not a manual bypass. Explicit manual launches without routing remain marked manual/unknown; do not claim assessed provenance for them.

Built-in profiles derive selectors from route.MODELS. Optional .rig/routing.json schema_version=1 overrides profiles keyed by stable ID and preferences (fast, standard, strong, review). Configuration never enables a worker. Smart catalog-required profiles accept only exact selectors/declared aliases, with a successful catalog <=24 hours old; skipped/unavailable/empty are not confirmation. Fresh cache is <=1 hour; bounded stale cache can refresh in background. No substring or arbitrary-first-model fallback.

If no eligible wrapper exists for writing, parent_writes means this parent registers and writes, without a second same-CLI session. Actual parent model/effort remains observed or unknown; no suggested model is a switch. Explore stays read-only. Never spawn Sol, Astra, or Fable as a child; Opus is allowed.

For independent post-write review, first accept the writer snapshot with next=review, then rig_pick(role=review, review_mode=independent, writer_job_id=...). Require a different known actual model provider, not merely a different CLI. Unknown/unavailable independence stays explicit. Writer does not review its own diff.

Use --explain for candidate decisions, defaults, fingerprint, and review recommendation. rig routing report / rig_routing_report reports recorded attempts and acceptance separately from exit status; it never trains or changes ranking. Rollback: [routing] mode="legacy" restores the old ladder. Coordinate launcher/protocol updates and a full parent/MCP restart before new admissions; do not mix runtime versions.

## Stage-gated parallel

One writer owns a scope through execution and parent assessment. Default `[queue].max_running` is 3 reserved/running/ASK executions; configured global/per-worker caps remain authoritative. Stopped execution frees its slot, not its files. Writes conflict with held write/read scopes in both directions; read/read overlap is allowed. Unknown write scope is exclusive. `parent_writes` occupies this parent turn; do not drain more writers then.

After implement+verify ok means: confirmed stopped execution, all declared requirements addressed, and parent acceptance of the current snapshot. Optional review requires `next=review`; transfer the writer reservation to a fresh read-only reviewer attempt without releasing its files. Keep the returned new attempt/token. One disjoint seed/bulk may run alongside review; wait wrapper IDs through MCP and native agents through their owning host. If seed changes reviewed scope, run it before the reviewed snapshot instead. Do not add explore/fix/QA teammates on the same write.

A failed reviewer launch still retains the original scope under the failed reviewer credentials with no execution slot. Retry uses a fresh reviewer ID, original writer/snapshot, and current holder credentials. Explicit close may conclude it; failure alone never releases the reviewed files.

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

## Fail classes

- `ask` / `running` — allow/deny or wait. Never kill because the child asked. Never replace.
- User Esc / MCP wait cancelled / `rig job cancel` — record durable cancellation for **those attached attempts**, then return promptly. `stop-requested`, `stop-unconfirmed`, and `native-cancel-required` do not prove termination. Never re-wait, re-pick, or drain automatically after explicit cancellation. Preserve parked queue items and unattached jobs.
- spawn never started (binary 127, refuse, native tool error, empty argv, auth/FS before first token) — one re-pick `--exclude <dead worker>`. Same brief, new job id. Last-resort: opencode, omp, pi, agy, codex. Do not auto-spawn Cursor. One fallback per user task.
- child ran and the patch failed / timeout / stale — escalate. Do not vendor-shop. Timeout does not unlock grok.
- native implement/hard (`parent_writes`) — parent writes; not a spawn fail.

## Ownership and native completion

Every native/parent write uses `rig_job_start` before the first edit, with concrete `files`, `access=write`, actual worker/model/effort, and executor kind. Read-only tasks use `access=read`. Save the start response: MCP `structuredContent` or CLI `rig job start ... --json` returns exact `reservation_id`, `attempt_id`, `owner_token`, owner, and `credentials_path`.

Private credentials are also written to the explicitly returned `.rig/jobs/<id>/owner-credentials.json` path (mode 0600). Direct wrappers print only that artifact path to stderr. Retain that launch response/path; do not recover authorization by guessing a job ID, reading HUD `.rig/thread`, or printing tokens in logs/chat. Shell transport uses `RIG_OWNER_TOKEN`; CLI flags use `--reservation-id`, `--attempt-id`, `--owner-session`. Use the same initiating session for subsequent operations.

Native `rig_job_finish` requires the credentials and one explicit completion payload:

- Owning parent completed this task: `{"kind":"parent_task","completed":true}`. Parent CLI may remain alive.
- Parent observed a specific native child finish: `{"kind":"native_child","agent_id":"actual-agent-id","terminal":true,"outcome":"ok"}`. Outcome must match status (`ok|fail|timeout|cancelled`). Parent/MCP PID is not child evidence.

Use the owning host's native wait/interrupt tools to observe or stop that specific agent, then submit the matching authenticated completion. Rig MCP cannot call host interruption tools or signal the parent as a substitute. `native-cancel-required` remains unconfirmed; never manufacture a terminal result. Missing or unknown observation returns `NEEDS_RECONCILIATION` rather than an indefinite Rig wait. Wrapper stop attempts are bounded and identity-checked; `stopped` alone confirms termination. Keep the slot and files when stop is unconfirmed; confirmed cancelled execution frees its slot while files stay held until explicit close.

If the parent explicitly cancels its own native write and then loses its saved credentials or job artifacts, first stop the work and confirm it has terminated. From that same initiating parent session, use `rig_job_recover_parent_write` with the exact job ID, `confirmed_stopped=true`, and a rationale. This records the attempt as cancelled/unverified and releases its held scope; it never marks work successful or accepted. Recovery is parent-only, rejects wrapper/native-child jobs and another session, and is not a substitute for stopping work. Deleting `.rig/jobs/<id>` or approving again does not finish or release the reservation. If the original parent session cannot authenticate, resolve ownership from that session or use `rig_job_close` only after confirmed termination and with its exact credentials; never delete reservation files.

Missing completion retains slot/files with `needs_reconciliation`. Wrapper completion requires its actual process/group/observable descendants to stop. TERM, timeout, or terminal metadata alone is not proof. Confirmed execution result is immutable. `rig_job_record` may describe retrospective read-only work; it cannot manufacture protection, verification, or independent-review eligibility for earlier edits.

## Parent verification and close

Child exit zero is execution success, not verified work. Inspect `rig_job_show` scoped before/after evidence and current `snapshot_id`; child claims are untrusted context. Declare the complete `rig_job_requirements` manifest (`requirements` entries: `id`, exact `argv`, optional `cwd`; plus `manual_criteria`). Each requirement remains binding; omitting check IDs cannot hide failure. Requirements may be declared before execution ends; checks require confirmed stopped execution.

Run each required `rig_job_check` deliberately with exact name/argv/cwd and ownership credentials. It records exit code, logs, and content before/after. No global admission lock is held while checks run. Describe actual checks/manual findings; never execute arbitrary commands merely because a child suggested them. `rig_job_accept` requires current `snapshot_id`, `decision=accept|reject`, rationale, and ownership. `next=complete` releases files after accepted completion; `next=review` retains them for independent handoff. Changed content invalidates acceptance. Unknown model/provider remains unknown; a different CLI can still use the same provider.

Failed/cancelled/rejected work stays unverified. After confirmed task termination, `rig_job_close` with current credentials and rationale releases ownership without acceptance or retry. `rig_job_reconcile` reports by default; apply only explicit supported recovery. Dead unlaunched work can compensate, but live/ASK/unknown ownership never age-expires. Interrupted checks need same-owner credentials, rationale, and `completion={"checks_stopped":true}`; observed live checkers refuse recovery. Legacy recovery attaches prospective ownership only.

## Call another CLI

1. Prepare brief TEXT (do not mkdir or write `.rig/jobs/<id>/brief.md` before MCP launch; the tool creates that path). Start with: you are a worker, not the orchestrator; first Rig operation is `rig_job_inbox`; do not spawn codex, grok, claude, cursor, opencode, omp, pi, or agy; do only the files and changes in the brief; do not hunt extra updates; print a short summary; stop. Implement briefs MUST list files to modify, what to change, what not to change, and acceptance. If the parent used a skill the writer must follow, put the absolute `SKILL.md` path in the brief (not a slash command name). Parent already did Figma / computer-use / chrome: put artifacts; tell the child not to use those tools. Do not spawn "go find and fix". Explore/gather briefs may say what to find; they do not need a file-edit list.
2. MCP `rig_pick` with `role` (bash fallback: `pick=$(rig pick implement --case "<task>" --json)`) then MCP `rig_job_launch` with that brief TEXT plus files/access/worker/model/effort and owner credentials. Do not block this turn on the wrapper. Shell `run-worker.sh` is human/internal fallback only — that path may write `.rig/jobs/<id>/brief.md` first, then:
   `RIG_LIVE=1 RIG_ROLE=<kind> RIG_MODEL=<model> RIG_EFFORT=<effort> RIG_JOB_FILES_JSON=<JSON-array> RIG_ACCESS=<read-or-write> "${RIG_HOME:-$HOME/.rig}/scripts/run-worker.sh" <worker> <id> .rig/jobs/<id>/brief.md`
3. One normal blocking MCP `rig_job_wait` for observable wrapper work (no timeout), until ASK, result, cancellation, or an explicit reconciliation outcome. After implement+verify ok, one wait on wrapper review+seed IDs together (`ids`). Native agents use their host's wait/interrupt and authenticated completion. Do not parse a TUI. If MCP wait errors or the transport drops the tool, take one bounded `rig job wait ID --timeout 0` snapshot and inspect/reconcile. Never blindly re-wait, and never use this fallback after explicit cancellation.
   - ASK / status `ask`: **you** answer MCP `rig_job_allow` or `rig_job_deny`. Safe worker work (read/edit/test/ssh gather/git) → allow. Destructive/prod/secrets → deny or ask the user. Then wait **once** more (no timeout; same ids).
   - exit 0: child finished ok
   - spawn never started: **one** re-pick with `--exclude <dead worker>` (MCP `rig_pick` `exclude`, bash `rig pick --exclude`). Same brief, new job id. Last-resort: opencode, omp, pi, agy, codex. Do not auto-spawn Cursor (`spawn=none` → tell the user). One fallback per task. Do not retry as Sol, Astra, or Fable. Do not spawn a worker whose harness flag is false. Timeout does not unlock grok.
   - child ran and the patch failed / timeout / stale: escalate. Do not vendor-shop.
   - exit 124: only if you passed `--timeout` and the job was still running when the cap hit. Do not pass a timeout in the normal path.
4. Never kill, close, finish, or replace a job because it is asking for permission. The same Claude job continues after you allow. Explicit user cancellation follows the cancellation protocol above; only confirmed termination permits close.

Live child: `RIG_LIVE=1`. Default wrapper is dry-run.

Codex sandbox must allow writing `$HOME/.grok` (and `$HOME/.claude` / `$HOME/.cursor` / `$HOME/.opencode` / `$HOME/.omp` / `$HOME/.pi` / `$HOME/.gemini` if used) plus outbound network, or the child fails with `FS_PERMISSION_DENIED` creating a session. `rig setup` adds those writable roots.

## After a run

Overwrite `.rig/STATE.md` with job id, worker, status, summary.
If there is one standing fact, MCP `rig_memory_add`. Do not edit MEMORY.md by hand. Bash fallback if MCP is missing: `rig memory add "one standing fact"`.

Skip if there is no fact. The command drops duplicates and caps the file at about 120 lines. No transcripts.

Upgrade/rollback: stop new admissions, finish or explicitly cancel existing work, confirm termination, and close/reconcile held scopes. Preserve pending queue text and credentials. Update every launcher and managed source skill/protocol, then fully restart all parent/MCP sessions before admitting work. Never run mixed-version admission writers.

## Child commands

Exact argv lives in `run-worker.sh`. Pass `RIG_MODEL` and `RIG_EFFORT` from MCP `rig_pick`. Defaults if unset: Codex `gpt-5.6-luna` low, Grok `grok-4.6` high, Claude Code `claude-sonnet-5` medium, Cursor `composer-2.5`. Explore: Codex `gpt-5.3-codex-mini` low; mini writes: Codex `gpt-5.6-luna` low. Cheap Claude: `claude-haiku-4-5-20251001` low, Cursor `composer-2.5-fast`. Hard/review Claude Code: `claude-opus-5` high. Cursor hard `cursor-grok-4.6-high`; Cursor review `claude-opus-5-thinking-high`. Cursor has no `--effort` flag. Haiku cheap jobs record `low` on the board but do not pass `--effort` into Claude Code (Haiku print-mode hangs). OpenCode / OMP / Pi / agy smart profiles require exact selectors or declared aliases confirmed by the CLI catalog (cache `~/.rig/cache/model-catalogs.json`); legacy mode retains its older resolver. Never Sol/Astra/Fable. OpenCode preference: cheap `openai/gpt-5.4-mini` `--variant minimal`, implement `openai/gpt-5.6-luna` `--variant high`, hard/review `openai/gpt-5.6-terra` `--variant max`. OMP/Pi preference: cheap `grok-4.5` `--thinking low`, implement/hard `grok-4.6` `--thinking high`, review `claude-opus-5` `--thinking high`. agy preference: cheap `gemini-3.8-flash-low` `--effort low`, implement `gemini-3.8-flash-high` `--effort high`, hard/review `gemini-3.1-pro-high` `--effort high`.

Cursor child is print-mode `stream-json` with `--force --trust`. Do not use `--worktree` (edits must land in this repo). Binary is `cursor-agent`, not a random `agent`. Grok Bot.app cannot be spawned.

OpenCode child is `opencode run --format json --dir $REPO --auto`. OMP child is `omp -p --mode json --cwd $REPO --approval-mode write` (not `--auto-approve`). Pi child is `pi -p --mode json --approve`. agy child is `agy -p` with `--output-format json --mode accept-edits --print-timeout ${TIMEOUT_SECS}s --disable-slash-commands`. No `--dangerously-skip-permissions`. Headless shell uses a scoped `permissions.allow` `command(*)` for the job, restored after. JSON `denied_actions` is a fail even if agy exits 0. Default preferences put OMP before Pi.

Claude Code child is print-mode `stream-json` (not buffered `json`), `acceptEdits`, print prompt last argv, no `--bare` (that drops OAuth), no `--dangerously-skip-permissions` (org managed settings can disable bypass). Anthropic remote deny rules like `Bash(eval $(wget*))` are invalid nested parens; they print to stderr and are skipped. `rig jobs` / `rig tui` hide that noise.

A Claude child with no TTY cannot click Allow. When it needs permission, the job status becomes `ask` and MCP `rig_job_wait` returns ASK. The parent answers MCP `rig_job_allow` / `rig_job_deny` — that is the interaction. Do not ignore it. Do not close the job. Do not spawn Grok/Codex/Cursor/OpenCode/OMP/Pi/agy instead. The work timeout pauses during `ask` and restarts after allow, so a slow allow does not kill the child. There is no Claude-style allow loop for agy.

Safe → allow: read, edit, test, ssh/gather, git status/diff/add/commit. Ask the user only for force-push, prod deploy, rm -rf outside the repo, or secrets. Human TUI: `y` / `n`.

Normal CLI wait when MCP is missing before waiting:

```bash
rig job wait <id>                  # one normal wrapper wait; exit 2 = ASK
rig job wait <id1> <id2>           # wait-all after parent acceptance (review + seed)
```

Transport recovery after a dropped/failed wait: `rig job wait ID --timeout 0` once, then inspect/reconcile. Explicit cancellation never re-waits, re-picks, or drains queued work.

```bash
rig job allow <id>                 # safe worker work — child continues
rig job deny <id> --reason "..."   # destructive / prod / secrets
rig job cancel <id>                # durable intent; stop confirmation remains separate
```
