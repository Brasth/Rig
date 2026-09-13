---
name: delegate-harness
description: >
  ALWAYS activate when `.rig/harness.toml` exists. MUST run MCP `rig_session`
  (or `rig_pick`) and spawn a Rig worker for code, review, fix, SSH, remote
  debug, or gather in the codebase unless pick parent_writes is true. Parent
  checks first and writes the brief. Delegate implement, review, explore, or
  split work across Codex, Grok, Claude, Cursor, OpenCode, OMP, Pi, and agy
  via run-worker.sh.
user-invocable: true
---

# Delegate harness

## Hard gate

When `.rig/harness.toml` exists, do not write app code, review a diff, fix a bug, or SSH yourself **unless pick `parent_writes` is true** (native implement/hard). `run-worker` means you still do not write the patch. Spawn explore/mini for codebase gather only if the parent cannot name the files after a short check.

Parent checks first: read local files, name the cause, write the brief. Parent checks before an implement spawn (name files and the update). Ask / plan / advise stay with the parent. Docs/skills-only: MCP `rig_pick` with `role` mini. If the implement brief already lists files, do not also spawn explore.

Before `run-worker` / native implement: the brief MUST list files to modify, what to change, what not to change, and acceptance. Put absolute skill file paths the child must follow. Do not spawn "go find and fix". The child does only those files and changes. Do not hunt extra updates.

## MCP first

Parent orchestration is MCP. Do not shell `rig` for session, pick, wait, allow, deny, jobs, log, message, queue, or memory when those tools are listed.

- Parent verification/recovery: `rig_job_requirements` / `rig_job_check` / `rig_job_accept` / `rig_job_close` / `rig_job_reconcile`.
- Instant: `rig_session` / `rig_pick` / `rig_status` / `rig_jobs` / `rig_job_show` / `rig_job_log` / `rig_job_allow` / `rig_job_deny` / `rig_job_cancel` / `rig_job_message` / `rig_job_start` / `rig_job_finish` / `rig_job_record` / `rig_queue_add` / `rig_queue_list` / `rig_queue_claim` / `rig_queue_unclaim` / `rig_queue_spawned` / `rig_memory` / `rig_memory_add`
- Wait: one blocking `rig_job_wait` with no timeout (`ids` for every live job)
- Child (`RIG_JOB_ID` set): `rig_job_doing` / `rig_job_note` / `rig_job_ask` / `rig_job_inbox` only. Each turn, pull inbox once (empty is fine). Inbox is not ASK and does not wake wait. Do not run the `rig` CLI. Do not pick, wait, spawn, queue, or allow.

Still bash (not communication):

- Launch the child: `run-worker.sh` in the background. There is no spawn-from-MCP tool.
- Human watch: `rig tui` / `/rig`
- Fallback only if MCP is **missing**, or the host **kills** wait: one `rig job wait <id>` with no `--timeout`. Then back to MCP. Do not poll.

Parent chooses kind from **this** user's semantic request and **this** user's skills, including non-English requests. Explicit role is authoritative; omitted role uses bounded English inference. Then MCP `rig_pick` with `role` stay|explore|mini|bulk|implement|hard|review. `--case` is the task text, not a slash-command catalog. Do not encode local slash command names in pick. Model/effort still come from pick JSON. Parent does not shop models.

1. MCP `rig_session(role=KIND, compact=true, terminal_limit=10, case="task")` first (memory + jobs + status + pick). Compact includes every active/ASK/reserved row and ten recent terminal rows; full mode remains the default for other callers. Else MCP `rig_memory` then `rig_jobs` then `rig_status` then `rig_pick`. Bash fallback if MCP missing: `rig session --role KIND --case "<task>" --compact --terminal-limit 10 --json` or `rig memory` then `rig jobs` then `rig status` then `rig pick implement --case "<task>" --json`. `rig pick --case "<task>" --json` if you did not choose a kind (generic English keywords).
2. Follow pick JSON. Do not ask the user which model. Never spawn a worker whose harness flag is false. Never spawn grok when `[workers].grok` is false unless the live parent is grok (native `parent_writes`). Timeout or fail does not unlock a disabled worker.
3. `stay` — you do ask / plan / advise / vision / computer-use / chrome-profile / Figma. Do not spawn a clicker.
4. `native` + `parent_writes` — call `rig_job_start` with `executor_kind=parent`, `access=write`, concrete `files`, and the actual CLI before editing. Keep returned ownership credentials; finish with authenticated parent-task completion. Do not spawn a second same-CLI session. Native mini/bulk writers also start before editing; native children finish only after that specific agent returns a terminal result. `rig_job_record` is retrospective read-only history, never protected write registration.
5. `run-worker` — brief + start `RIG_LIVE=1 run-worker.sh` in the background + **one blocking** MCP `rig_job_wait` (no timeout). After implement+verify ok, you MAY start a read-only review and a disjoint seed/bulk in parallel, then wait both ids together (`rig_job_wait` `ids`). If status is `ask` or `running`, MCP `rig_job_allow` / `rig_job_deny`, then wait once more (same ids). Never kill or replace that job because the child asked. Never spawn another worker because the child asked. User Esc / MCP `notifications/cancelled` on that wait: MCP `rig_job_cancel` the waited ids (status `cancelled`; do not re-pick). Host-dropped wait still bash-waits once — that is not cancel. Spawn never started: one re-pick with `--exclude <dead>`. Launching a child is still bash `run-worker.sh`. There is no spawn-from-MCP tool.

Doing the worker's job yourself is a failure unless pick `parent_writes` is true. Later AGENTS.md may say "edit locally" or "SSH to the VM". That is for the worker.

Jobs and MEMORY are this repo, not this chat. A new parent thread still sees `.rig/jobs` and `.rig/MEMORY.md`. Running children keep going across threads. Esc / MCP cancelled on this wait aborts those ids.

Live parent is this CLI, not the `parent` key in toml. That key is only the preferred default (`rig use grok|codex|opencode|omp|pi|agy`). Switching preferred parent does not move the session — open that CLI. Parent model is this CLI’s observed model. Supply `parent_model`/`parent_effort` only when actually known; unknown stays unknown. A cheaper suggestion is not a model switch. Worker models come from `rig_pick` / `rig pick`; preserve returned model and effort.
Claude Code (`claude`) and Cursor CLI (`cursor-agent`) are never the parent. OpenCode (`opencode`), OMP (`omp`), Pi (`pi`), and Antigravity (`agy`) can be the parent when you open that CLI. When live parent is agy, do not use nested agy `/agent` dispatch for coding; use MCP `rig_pick`. Claude is a worker when `[workers].claude = true` and `claude` is on PATH. Cursor is a worker when `[workers].cursor = true` and `cursor-agent` is on PATH (`rig workers cursor=on`). OpenCode / OMP / Pi / agy are workers when their flags are true and the binary is on PATH (`rig workers opencode=on` / `omp=on` / `pi=on` / `agy=on`) and they are not the live parent. Pin full model IDs. Never spawn Fable, Sol, or Astra as a child. Opus is allowed. Grok Bot.app is not a parent or worker. The Antigravity IDE/GUI is not a parent or worker.

## Parent vs worker

This CLI is the parent. It manages. It does not sit on write/review/SSH when pick is `run-worker`.

**Parent keeps**

- plan, check (read local files, name the cause, write the brief), decide, talk to the user
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

So: Codex parent → Grok/Claude/Cursor/OpenCode/OMP/Pi/agy can be children. Grok parent → Grok child is off; the others can be children. OpenCode/OMP/Pi/agy parent → that CLI is off as a child; Grok/Claude/others can be children. Missing binary: that worker is off for this session, not an error. Use cheaper same-CLI workers. That is success. If both OMP and Pi are effective as workers of a different parent, pick uses OMP. Do not pick agy just because it is on PATH.

Check with MCP `rig_status` / `rig_jobs`. Those show the worker **model** and **reasoning** level. Human watch: `rig tui` in another pane. Parent log: MCP `rig_job_log`. After ok, raw `stdout.log` is pruned; `activity.json` remains. Never read Cursor `state.vscdb` or vendor sqlite for a Rig job. Fully quit OpenCode / OMP / Pi / agy once after `rig setup` so MCP `/rig` loads.

## Route

Never ask the user which model or reasoning to use. They will not know. Parent picks **kind**; pick maps kind to worker, model, and effort. MCP `rig_session` or `rig_pick` with `role`. Bash fallback if MCP is missing: `rig pick implement --case "<task>" --json` / `rig pick stay --case "<task>" --json`. `rig pick --case "<task>" --json` remains the fallback when the parent did not choose a kind.

- Ask / plan / advise / vision / computer-use / chrome-profile / Figma: parent keeps it (MCP `rig_pick` `role` stay). Docs/skills-only: MCP `rig_pick` `role` mini. Do not spawn a clicker.
- Locate / trace / codebase gather: cheap same-CLI (MCP `rig_pick` `role` explore) only if the parent cannot name the files after a short check. If the implement brief already lists files, do not also spawn explore. Include SSH/remote too. Codex explorer is `gpt-5.3-codex-mini` low. Grok explore is `grok-4.5`. Claude Code explore is `claude-haiku-4-5-20251001` low. Cursor explore is `composer-2.5-fast` (run-worker, `--mode=ask`).
- Docs/skills mini writes require an edit-capable worker. Codex mini uses `gpt-5.6-luna` low, native worker; `gpt-5.3-codex-mini` explorer is for read-only exploration.
- Mechanical bulk: MCP `rig_pick` `role` bulk. Codex `gpt-5.6-luna` low. Claude Code `claude-haiku-4-5-20251001` low. Cursor `composer-2.5-fast`.
- Write code / fix bugs / SSH / remote debug: MCP `rig_pick` `role` implement. Grok child `grok-4.6` high if Grok is effective. If Grok is the live parent: Claude Code `claude-sonnet-5` if effective, else native `parent_writes` (this parent writes; no second grok-4.6 session). Same ladder if OpenCode, OMP, Pi, or agy is live. Last-resort children: opencode, omp, pi, agy, codex, then cursor. Do not auto-spawn Cursor on fallback (MCP `rig_pick` `exclude`).
- Hard / architecture / security / multi-file: MCP `rig_pick` `role` hard. Same ladder. Native hard is `parent_writes`.
- Review: compare actual model providers, not CLI names. For post-write independent review, parent first accepts the writer snapshot with `next=review`, then calls `rig_pick(role=review, review_mode=independent, writer_job_id=...)`. Unknown actual writer provider or stale/missing acceptance refuses independent review. Standalone review may be useful but does not imply confirmed independence. Claude Code review is `claude-opus-5` high. Cursor review is `claude-opus-5-thinking-high`. If no other vendor, `spawn=none` — do not self-review.
- No extra CLIs: cheap same-CLI. Record them. That is success.
- Never spawn Sol, Astra, or Fable as a child. Never pass `gpt-5.6-sol`, `gpt-6-astra`, `gpt-5.6-sol-high`, or `claude-fable-5` to a worker. Opus is allowed.

Writer does not review its own diff.

## Stage-gated parallel

One writer owns a scope through execution and parent assessment. Default `[queue].max_running` is 3 reserved/running/ASK executions; configured global/per-worker caps remain authoritative. Stopped execution frees its slot, not its files. Writes conflict with held write/read scopes in both directions; read/read overlap is allowed. Unknown write scope is exclusive. `parent_writes` occupies this parent turn; do not drain more writers then.

After implement+verify ok means: confirmed stopped execution, all declared requirements addressed, and parent acceptance of the current snapshot. Optional review requires `next=review`; transfer the writer reservation to a fresh read-only reviewer attempt without releasing its files. Keep the returned new attempt/token. One disjoint seed/bulk may run alongside review; wait both IDs once. If seed changes reviewed scope, run it before the reviewed snapshot instead. Do not add explore/fix/QA teammates on the same write.

A failed reviewer launch still retains the original scope under the failed reviewer credentials with no execution slot. Retry uses a fresh reviewer ID, original writer/snapshot, and current holder credentials. Explicit close may conclude it; failure alone never releases the reviewed files.

## Queue drain

On a free parent turn after compact `rig_session`:

1. List pending items in priority/oldest order. Stay/advise stays local. Name concrete files; serialize shared DB/port/VM work that file checks cannot represent.
2. Skip overlapping or capped items; try the next ID. Claim **by id** when more than one item is pending, with selected `worker`, `access`, JSON `files`, and initiating `owner_session`.
3. Save the claim response `reservation_id`, `attempt_id`, `owner_token`, and owner. Same-ID reuse is never launch permission. No-worker compatibility claims reserve a slot conservatively.
4. Write the brief. If it fails, `rig_queue_unclaim` requires that unconsumed claim's exact credentials and initiating session.
5. Wrapper consumes the claim via `RIG_QUEUE_ID`, `RIG_RESERVATION_ID`, `RIG_ATTEMPT_ID`, `RIG_OWNER_TOKEN`, `RIG_OWNER_SESSION`, `RIG_ACCESS`, and `RIG_JOB_FILES_JSON`. Native start passes the equivalent MCP fields. Worker/files/access must match.
6. `rig_queue_spawned` is an authenticated acknowledgement with the same queue/job/attempt credentials, not another claim. Wait once on all live IDs. ASK: allow/deny that ID; wait the same IDs again. Do not claim during ASK.

`/queue`, hooks, and HUD refresh only park/read. Cancellation never silently returns work to pending. Legacy claims without credentials block conservatively; reconcile explicitly rather than guessing a token.

## Fail classes

- `ask` / `running` — allow/deny or wait. Never kill because the child asked. Never replace.
- User Esc / MCP wait cancelled / `rig job cancel` — abort **those waited ids**. Status `cancelled`. Do not re-pick. Do not cancel parked queue items or jobs this wait was not blocking on.
- spawn never started (binary 127, refuse, native tool error, empty argv, auth/FS before first token) — one re-pick `--exclude <dead worker>`. Same brief, new job id. Last-resort: opencode, omp, pi, agy, codex. Do not auto-spawn Cursor. One fallback per user task.
- child ran and the patch failed / timeout / stale — escalate. Do not vendor-shop. Timeout does not unlock grok.
- native implement/hard (`parent_writes`) — parent writes; not a spawn fail.

## Ownership and native completion

Every native/parent write uses `rig_job_start` before the first edit, with concrete `files`, `access=write`, actual worker/model/effort, and executor kind. Read-only tasks use `access=read`. Save the start response: MCP `structuredContent` or CLI `rig job start ... --json` returns exact `reservation_id`, `attempt_id`, `owner_token`, owner, and `credentials_path`.

Private credentials are also written to the explicitly returned `.rig/jobs/<id>/owner-credentials.json` path (mode 0600). Direct wrappers print only that artifact path to stderr. Retain that launch response/path; do not recover authorization by guessing a job ID, reading HUD `.rig/thread`, or printing tokens in logs/chat. Shell transport uses `RIG_OWNER_TOKEN`; CLI flags use `--reservation-id`, `--attempt-id`, `--owner-session`. Use the same initiating session for subsequent operations.

Native `rig_job_finish` requires the credentials and one explicit completion payload:

- Owning parent completed this task: `{"kind":"parent_task","completed":true}`. Parent CLI may remain alive.
- Parent observed a specific native child finish: `{"kind":"native_child","agent_id":"actual-agent-id","terminal":true,"outcome":"ok"}`. Outcome must match status (`ok|fail|timeout|cancelled`). Parent/MCP PID is not child evidence.

Missing completion retains slot/files with `needs_reconciliation`. Wrapper completion requires its actual process/group/observable descendants to stop. TERM, timeout, or terminal metadata alone is not proof. Confirmed execution result is immutable. `rig_job_record` may describe retrospective read-only work; it cannot manufacture protection, verification, or independent-review eligibility for earlier edits.

## Parent verification and close

Child exit zero is execution success, not verified work. Inspect `rig_job_show` scoped before/after evidence and current `snapshot_id`; child claims are untrusted context. Declare the complete `rig_job_requirements` manifest (`requirements` entries: `id`, exact `argv`, optional `cwd`; plus `manual_criteria`). Each requirement remains binding; omitting check IDs cannot hide failure. Requirements may be declared before execution ends; checks require confirmed stopped execution.

Run each required `rig_job_check` deliberately with exact name/argv/cwd and ownership credentials. It records exit code, logs, and content before/after. No global admission lock is held while checks run. Describe actual checks/manual findings; never execute arbitrary commands merely because a child suggested them. `rig_job_accept` requires current `snapshot_id`, `decision=accept|reject`, rationale, and ownership. `next=complete` releases files after accepted completion; `next=review` retains them for independent handoff. Changed content invalidates acceptance. Unknown model/provider remains unknown; a different CLI can still use the same provider.

Failed/cancelled/rejected work stays unverified. After confirmed task termination, `rig_job_close` with current credentials and rationale releases ownership without acceptance or retry. `rig_job_reconcile` reports by default; apply only explicit supported recovery. Dead unlaunched work can compensate, but live/ASK/unknown ownership never age-expires. Interrupted checks need same-owner credentials, rationale, and `completion={"checks_stopped":true}`; observed live checkers refuse recovery. Legacy recovery attaches prospective ownership only.

## Call another CLI

1. Write `.rig/jobs/<id>/brief.md`. Start with: you are a worker, not the orchestrator; do not spawn codex, grok, claude, cursor, opencode, omp, pi, or agy; do only the files and changes in the brief; do not hunt extra updates; print a short summary; stop. Implement briefs MUST list files to modify, what to change, what not to change, and acceptance. If the parent used a skill the writer must follow, put the absolute `SKILL.md` path in the brief (not a slash command name). Parent already did Figma / computer-use / chrome: put artifacts; tell the child not to use those tools. Do not spawn "go find and fix". Explore/gather briefs may say what to find; they do not need a file-edit list.
2. MCP `rig_pick` with `role` (bash fallback: `pick=$(rig pick implement --case "<task>" --json)`) then start the wrapper **in the background**. Do not block this turn on `run-worker.sh` (that deadlocks when Claude asks for permission). Do not spawn via MCP:
   `RIG_LIVE=1 RIG_ROLE=<kind> RIG_MODEL=<model> RIG_EFFORT=<effort> RIG_JOB_FILES_JSON=<JSON-array> RIG_ACCESS=<read-or-write> "${RIG_HOME:-$HOME/.rig}/scripts/run-worker.sh" <worker> <id> .rig/jobs/<id>/brief.md`
3. One blocking MCP `rig_job_wait` until ASK or `result.json` (no timeout). After implement+verify ok, one wait on the review+seed ids together (`ids`). Do not parse a TUI. If MCP wait errors or the host drops the tool, bash `rig job wait` once (no `--timeout`). Do not poll. Do not go back to a 30s poll loop.
   - ASK / status `ask`: **you** answer MCP `rig_job_allow` or `rig_job_deny`. Safe worker work (read/edit/test/ssh gather/git) → allow. Destructive/prod/secrets → deny or ask the user. Then wait **once** more (no timeout; same ids).
   - exit 0: child finished ok
   - spawn never started: **one** re-pick with `--exclude <dead worker>` (MCP `rig_pick` `exclude`, bash `rig pick --exclude`). Same brief, new job id. Last-resort: opencode, omp, pi, agy, codex. Do not auto-spawn Cursor (`spawn=none` → tell the user). One fallback per task. Do not retry as Sol, Astra, or Fable. Do not spawn a worker whose harness flag is false. Timeout does not unlock grok.
   - child ran and the patch failed / timeout / stale: escalate. Do not vendor-shop.
   - exit 124: only if you passed `--timeout` and the job was still running when the cap hit. Do not pass a timeout in the normal path.
4. NEVER kill, close, finish, or replace a job that is `ask` or `running`. The child is waiting on you. Spawning another worker because Claude asked is a failure. The same Claude job continues after you allow.

Live child: `RIG_LIVE=1`. Default wrapper is dry-run.

Codex sandbox must allow writing `$HOME/.grok` (and `$HOME/.claude` / `$HOME/.cursor` / `$HOME/.opencode` / `$HOME/.omp` / `$HOME/.pi` / `$HOME/.gemini` if used) plus outbound network, or the child fails with `FS_PERMISSION_DENIED` creating a session. `rig setup` adds those writable roots.

## After a run

Overwrite `.rig/STATE.md` with job id, worker, status, summary.
If there is one standing fact, MCP `rig_memory_add`. Do not edit MEMORY.md by hand. Bash fallback if MCP is missing: `rig memory add "one standing fact"`.

Skip if there is no fact. The command drops duplicates and caps the file at about 120 lines. No transcripts.

## Child commands

Exact argv lives in `run-worker.sh`. Pass `RIG_MODEL` and `RIG_EFFORT` from MCP `rig_pick`. Defaults if unset: Codex `gpt-5.6-luna` low, Grok `grok-4.6` high, Claude Code `claude-sonnet-5` medium, Cursor `composer-2.5`. Explore: Codex `gpt-5.3-codex-mini` low; mini writes: Codex `gpt-5.6-luna` low. Cheap Claude: `claude-haiku-4-5-20251001` low, Cursor `composer-2.5-fast`. Hard/review Claude Code: `claude-opus-5` high. Cursor hard `cursor-grok-4.6-high`; Cursor review `claude-opus-5-thinking-high`. Cursor has no `--effort` flag. Haiku cheap jobs record `low` on the board but do not pass `--effort` into Claude Code (Haiku print-mode hangs). OpenCode / OMP / Pi / agy pins are preferences; Rig lists models from that CLI and picks one that exists (cache `~/.rig/cache/model-catalogs.json`). Never Sol/Astra/Fable. OpenCode preference: cheap `openai/gpt-5.4-mini` `--variant minimal`, implement `openai/gpt-5.6-luna` `--variant high`, hard/review `openai/gpt-5.6-terra` `--variant max`. OMP/Pi preference: cheap `grok-4.5` `--thinking low`, implement/hard `grok-4.6` `--thinking high`, review `claude-opus-5` `--thinking high`. agy preference: cheap `gemini-3.8-flash-low` `--effort low`, implement `gemini-3.8-flash-high` `--effort high`, hard/review `gemini-3.1-pro-high` `--effort high`.

Cursor child is print-mode `stream-json` with `--force --trust`. Do not use `--worktree` (edits must land in this repo). Binary is `cursor-agent`, not a random `agent`. Grok Bot.app cannot be spawned.

OpenCode child is `opencode run --format json --dir $REPO --auto`. OMP child is `omp -p --mode json --cwd $REPO --approval-mode write` (not `--auto-approve`). Pi child is `pi -p --mode json --approve`. agy child is `agy -p` with `--output-format json --mode accept-edits --print-timeout ${TIMEOUT_SECS}s --disable-slash-commands`. No `--dangerously-skip-permissions`. Headless shell uses a scoped `permissions.allow` `command(*)` for the job, restored after. JSON `denied_actions` is a fail even if agy exits 0. If both OMP and Pi are effective, pick OMP.

Claude Code child is print-mode `stream-json` (not buffered `json`), `acceptEdits`, print prompt last argv, no `--bare` (that drops OAuth), no `--dangerously-skip-permissions` (org managed settings can disable bypass). Anthropic remote deny rules like `Bash(eval $(wget*))` are invalid nested parens; they print to stderr and are skipped. `rig jobs` / `rig tui` hide that noise.

A Claude child with no TTY cannot click Allow. When it needs permission, the job status becomes `ask` and MCP `rig_job_wait` returns ASK. The parent answers MCP `rig_job_allow` / `rig_job_deny` — that is the interaction. Do not ignore it. Do not close the job. Do not spawn Grok/Codex/Cursor/OpenCode/OMP/Pi/agy instead. The work timeout pauses during `ask` and restarts after allow, so a slow allow does not kill the child. There is no Claude-style allow loop for agy.

Safe → allow: read, edit, test, ssh/gather, git status/diff/add/commit. Ask the user only for force-push, prod deploy, rm -rf outside the repo, or secrets. Human TUI: `y` / `n`.

Fallback if MCP is missing or the host drops wait:

```bash
rig job wait <id>                  # one blocking wait; no --timeout; exit 2 = ASK
rig job wait <id1> <id2>           # wait-all after parent acceptance (review + seed)
rig job allow <id>                 # safe worker work — child continues
rig job deny <id> --reason "..."   # destructive / prod / secrets
rig job cancel <id>                # user aborted this wait; status cancelled; do not re-pick
```
