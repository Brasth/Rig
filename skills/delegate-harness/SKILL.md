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

Parent checks first: read local files, name the cause, write the brief. Parent checks before an implement spawn (name files and the update). Ask / plan / advise stay with the parent. Docs/skills-only uses `rig pick mini`. If the implement brief already lists files, do not also spawn explore.

Before `run-worker` / native implement: the brief MUST list files to modify, what to change, what not to change, and acceptance. Put absolute skill file paths the child must follow. Do not spawn "go find and fix". The child does only those files and changes. Do not hunt extra updates.

Parent chooses kind from **this** user's request and **this** user's skills. Then call `rig pick stay` / `explore` / `mini` / `implement` / `hard` / `review` (MCP: `rig_pick` with `role`). `--case` is the task text, not a slash-command catalog. Do not encode local slash command names in pick. Model/effort still come from pick JSON. Parent does not shop models.

1. MCP `rig_session` first when present (memory + jobs + status + pick). Else MCP `rig_memory` then `rig_jobs` then `rig_status` then `rig_pick`. Bash fallback if MCP missing: `rig session --role KIND --case "<task>" --json` or `rig memory` then `rig jobs` then `rig status` then `rig pick implement --case "<task>" --json`. `rig pick --case "<task>" --json` if you did not choose a kind (generic English keywords).
2. Follow pick JSON. Do not ask the user which model. Never spawn a worker whose harness flag is false. Never spawn grok when `[workers].grok` is false unless the live parent is grok (native `parent_writes`). Timeout or fail does not unlock a disabled worker.
3. `stay` — you do ask / plan / advise / vision / computer-use / chrome-profile / Figma. Do not spawn a clicker.
4. `native` + `parent_writes` (implement/hard) — **this parent writes** the listed files, then MCP `rig_job_record` (else `rig job record`). Do not spawn a second same-CLI session. Explore/mini/bulk native stay cheap same-CLI agents + `rig_job_start` / `rig_job_finish`.
5. `run-worker` — brief + start `RIG_LIVE=1 run-worker.sh` in the background + **one blocking wait** (MCP `rig_job_wait` with no timeout if present, else `rig job wait <id>` with no `--timeout`). After implement+verify ok, you MAY start a read-only review and a disjoint seed/bulk in parallel, then wait both ids together (`rig_job_wait` `ids` or `rig job wait id1 id2`). If status is `ask` or `running`, you allow/deny, then wait once more (same ids). Never kill or replace that job. Never spawn another worker because the child asked. Spawn never started: one re-pick with `--exclude <dead>`. Launching a child is still bash `run-worker.sh`. There is no spawn-from-MCP tool.

Doing the worker's job yourself is a failure unless pick `parent_writes` is true. Later AGENTS.md may say "edit locally" or "SSH to the VM". That is for the worker.

Jobs and MEMORY are this repo, not this chat. A new parent thread still sees `.rig/jobs` and `.rig/MEMORY.md`. Running children keep going.

Prefer MCP when present for session, pick, status, start, finish, record, wait, allow, deny, and memory. Instant MCP: `rig_session` / `rig_pick` / `rig_status` / `rig_job_start` / `rig_job_finish` / `rig_job_record` / `rig_jobs` / `rig_job_show` / `rig_job_log` / `rig_job_wait` / `rig_job_allow` / `rig_job_deny` / `rig_memory`. Bash fallback if MCP is missing. Do not spawn via MCP.

Live parent is this CLI, not the `parent` key in toml. That key is only the preferred default (`rig use grok|codex|opencode|omp|pi|agy`). Switching preferred parent does not move the session — open that CLI. Parent model is this CLI’s model. Worker models come from `rig_pick` / `rig pick`.
Claude Code (`claude`) and Cursor CLI (`cursor-agent`) are never the parent. OpenCode (`opencode`), OMP (`omp`), Pi (`pi`), and Antigravity (`agy`) can be the parent when you open that CLI. When live parent is agy, do not use nested agy `/agent` dispatch for coding; use `rig pick`. Claude is a worker when `[workers].claude = true` and `claude` is on PATH. Cursor is a worker when `[workers].cursor = true` and `cursor-agent` is on PATH (`rig workers cursor=on`). OpenCode / OMP / Pi / agy are workers when their flags are true and the binary is on PATH (`rig workers opencode=on` / `omp=on` / `pi=on` / `agy=on`) and they are not the live parent. Pin full model IDs. Never spawn Fable, Sol, or Astra as a child. Opus is allowed. Grok Bot.app is not a parent or worker. The Antigravity IDE/GUI is not a parent or worker.

## Parent vs worker

This CLI is the parent. It manages. It does not sit on write/review/SSH when pick is `run-worker`.

**Parent keeps**

- plan, check (read local files, name the cause, write the brief), decide, talk to the user
- vision (screenshots, Figma, images) — parent runs Figma MCP; put artifacts in the brief
- computer use (desktop) and chrome profile (real browser, the user's cookies)
- native implement/hard when pick `parent_writes` is true
- read worker results, `rig jobs`, `rig memory`

**Worker does**

- write the listed files and changes when pick is `run-worker`; do not hunt extra updates
- follow skill file paths in the brief
- review (different vendor than the writer; never self-review)
- SSH / remote debug
- codebase gather (grep, file search, trace) and remote/SSH gather (logs, server facts)

Figma, computer-use, and chrome-profile stay with the parent. If this CLI has no Figma MCP, ask the user for a screenshot. Do not spawn a clicker.

## Effective workers

A worker is on only when all of these hold:

1. `[workers].<name>` is `true`
2. The binary is on PATH (`grok`, `codex`, `claude`, `cursor-agent`, `opencode`, `omp`, `pi`, or `agy`)
3. The worker is not the live parent

So: Codex parent → Grok/Claude/Cursor/OpenCode/OMP/Pi/agy can be children. Grok parent → Grok child is off; the others can be children. OpenCode/OMP/Pi/agy parent → that CLI is off as a child; Grok/Claude/others can be children. Missing binary: that worker is off for this session, not an error. Use cheaper same-CLI workers. That is success. If both OMP and Pi are effective as workers of a different parent, pick uses OMP. Do not pick agy just because it is on PATH.

Check with MCP `rig_status` / `rig_jobs` (bash: `rig status`, `rig jobs`) or `/rig`. Those show the worker **model** and **reasoning** level. Live child: `rig tui` in another pane, or `rig job log <id> -f`. Fully quit OpenCode / OMP / Pi / agy once after `rig setup` so MCP `/rig` loads.

## Route

Never ask the user which model or reasoning to use. They will not know. Parent picks **kind**; pick maps kind to worker, model, and effort. Run MCP `rig_session` or `rig_pick` with `role` (or bash `rig pick implement --case "<task>" --json` / `rig pick stay --case "<task>" --json` if MCP is missing) and follow it. `rig pick --case "<task>" --json` remains the fallback when the parent did not choose a kind.

- Ask / plan / advise / vision / computer-use / chrome-profile / Figma: parent keeps it (`rig pick stay`). Docs/skills-only: `rig pick mini`. Do not spawn a clicker.
- Locate / trace / codebase gather: cheap same-CLI (`rig pick explore` or `mini`) only if the parent cannot name the files after a short check. If the implement brief already lists files, do not also spawn explore. Include SSH/remote too. Codex explorer is `gpt-5.3-codex-mini` low. Grok explore is `grok-4.5`. Claude Code explore is `claude-haiku-4-5-20251001` low. Cursor explore is `composer-2.5-fast` (run-worker, `--mode=ask`).
- Mechanical bulk: `rig pick bulk`. Codex `gpt-5.6-luna` low. Claude Code `claude-haiku-4-5-20251001` low. Cursor `composer-2.5-fast`.
- Write code / fix bugs / SSH / remote debug: `rig pick implement --case "<task>"`. Grok child `grok-4.6` high if Grok is effective. If Grok is the live parent: Claude Code `claude-sonnet-5` if effective, else native `parent_writes` (this parent writes; no second grok-4.6 session). Same ladder if OpenCode, OMP, Pi, or agy is live. Last-resort children: opencode, omp, pi, agy, codex, then cursor. Do not auto-spawn Cursor on fallback (`rig pick --exclude`).
- Hard / architecture / security / multi-file: `rig pick hard`. Same ladder. Native hard is `parent_writes`.
- Review: different vendor than the writer. `rig pick review`. Claude Code review is `claude-opus-5` high. Cursor review is `claude-opus-5-thinking-high`. If no other vendor, `spawn=none` — do not self-review.
- No extra CLIs: cheap same-CLI. Record them. That is success.
- Never spawn Sol, Astra, or Fable as a child. Never pass `gpt-5.6-sol`, `gpt-6-astra`, `gpt-5.6-sol-high`, or `claude-fable-5` to a worker. Opus is allowed.

Writer does not review its own diff.

## Stage-gated parallel

Until implement+verify is **ok**: one child. Do not fan out gather/QA/fix/seed as teammates on the same write.

After that job is `ok` (tests in the implement brief passed), the parent MAY start **at most**:

1. one **read-only** `review` (different vendor; no patches)
2. one **seed/bulk** whose brief lists files **disjoint** from the review set (no hunt)

Those two may run at the same time. Then **one** wait on both ids:

```bash
rig job wait <review-id> <seed-id>     # exit 2 = ASK on one of them; allow/deny that id; wait the same ids again
```

MCP: `rig_job_wait` with `ids: ["review-id", "seed-id"]`. Wakes on first ASK. Exit 0 only if every id is ok. Do not kill the other job. Do not spawn a second writer on the same files. Do not spawn explore/fix/QA as extra teammates. If seed is part of the reviewed tree, run seed first, then review — not in parallel.

## Fail classes

- `ask` / `running` — allow/deny or wait. Never kill. Never replace.
- spawn never started (binary 127, refuse, native tool error, empty argv, auth/FS before first token) — one re-pick `--exclude <dead worker>`. Same brief, new job id. Last-resort: opencode, omp, pi, agy, codex. Do not auto-spawn Cursor. One fallback per user task.
- child ran and the patch failed / timeout / stale — escalate. Do not vendor-shop. Timeout does not unlock grok.
- native implement/hard (`parent_writes`) — parent writes; not a spawn fail.

## Record cheap same-CLI workers

Codex `explorer` / `worker` / `bulk` / `reviewer`, Grok `explore`, and OpenCode/OMP/Pi/agy `explore` / `worker` / `bulk` do not go through `run-worker.sh`. Still write a job so `.rig/jobs` and `rig status` show them. Prefer MCP `rig_job_start` / `rig_job_finish` / `rig_job_record` when present:

```bash
id=$(rig job start --worker codex --role explorer)
# spawn the native cheap agent, wait for it
rig job finish "$id" --status ok --summary "one-line result"
```

Native implement/hard (`parent_writes`): this parent writes, then:

```bash
rig job record --worker grok --role worker --status ok --summary "one-line result"
```

`--worker` is the CLI that did the work (`codex` if Codex spawned explorer; live parent if `parent_writes`). `--role` is `explorer`, `worker`, `bulk`, `reviewer`, or `parent`.

## Call another CLI

1. Write `.rig/jobs/<id>/brief.md`. Start with: you are a worker, not the orchestrator; do not spawn codex, grok, claude, cursor, opencode, omp, pi, or agy; do only the files and changes in the brief; do not hunt extra updates; print a short summary; stop. Implement briefs MUST list files to modify, what to change, what not to change, and acceptance. If the parent used a skill the writer must follow, put the absolute `SKILL.md` path in the brief (not a slash command name). Parent already did Figma / computer-use / chrome: put artifacts; tell the child not to use those tools. Do not spawn "go find and fix". Explore/gather briefs may say what to find; they do not need a file-edit list.
2. MCP `rig_pick` with `role` (bash fallback: `pick=$(rig pick implement --case "<task>" --json)`) then start the wrapper **in the background**. Do not block this turn on `run-worker.sh` (that deadlocks when Claude asks for permission). Do not spawn via MCP:
   `RIG_LIVE=1 RIG_ROLE=<kind> RIG_MODEL=<model> RIG_EFFORT=<effort> "${RIG_HOME:-$HOME/.rig}/scripts/run-worker.sh" <worker> <id> .rig/jobs/<id>/brief.md`
3. One blocking wait until ASK or `result.json`. After implement+verify ok, one wait on the review+seed ids together. Do not parse a TUI. Prefer MCP `rig_job_wait` with no timeout; bash fallback is `rig job wait <id>` with no `--timeout` (`rig job wait id1 id2` for a panel). If MCP wait errors or the host drops the tool, bash `rig job wait` once (no `--timeout`). Do not poll. Do not go back to a 30s poll loop.
   - exit 2 / status `ask`: **you** answer. `rig job show` then `rig job allow <id>` or `rig job deny <id>` (MCP: `rig_job_allow` / `rig_job_deny`). Safe worker work (read/edit/test/ssh gather/git) → allow. Destructive/prod/secrets → deny or ask the user. Then wait **once** more (no timeout; same ids).
   - exit 0: child finished ok
   - spawn never started: **one** re-pick with `--exclude <dead worker>` (MCP `rig_pick` `exclude`, bash `rig pick --exclude`). Same brief, new job id. Last-resort: opencode, omp, pi, agy, codex. Do not auto-spawn Cursor (`spawn=none` → tell the user). One fallback per task. Do not retry as Sol, Astra, or Fable. Do not spawn a worker whose harness flag is false. Timeout does not unlock grok.
   - child ran and the patch failed / timeout / stale: escalate. Do not vendor-shop.
   - exit 124: only if you passed `--timeout` and the job was still running when the cap hit. Do not pass a timeout in the normal path.
4. NEVER kill, close, finish, or replace a job that is `ask` or `running`. The child is waiting on you. Spawning another worker because Claude asked is a failure. The same Claude job continues after you allow.

Live child: `RIG_LIVE=1`. Default wrapper is dry-run.

Codex sandbox must allow writing `$HOME/.grok` (and `$HOME/.claude` / `$HOME/.cursor` / `$HOME/.opencode` / `$HOME/.omp` / `$HOME/.pi` / `$HOME/.gemini` if used) plus outbound network, or the child fails with `FS_PERMISSION_DENIED` creating a session. `rig setup` adds those writable roots.

## After a run

Overwrite `.rig/STATE.md` with job id, worker, status, summary.
If there is one standing fact, save it. Do not edit MEMORY.md by hand:

```bash
rig memory add "one standing fact"
```

Skip if there is no fact. The command drops duplicates and caps the file at about 120 lines. No transcripts.

## Child commands

Exact argv lives in `run-worker.sh`. Pass `RIG_MODEL` and `RIG_EFFORT` from `rig pick`. Defaults if unset: Codex `gpt-5.6-luna` low, Grok `grok-4.6` high, Claude Code `claude-sonnet-5` medium, Cursor `composer-2.5`. Explore/mini: Codex `gpt-5.3-codex-mini` low, Claude Code `claude-haiku-4-5-20251001` low, Cursor `composer-2.5-fast`. Hard/review Claude Code: `claude-opus-5` high. Cursor hard `cursor-grok-4.6-high`; Cursor review `claude-opus-5-thinking-high`. Cursor has no `--effort` flag. Haiku cheap jobs record `low` on the board but do not pass `--effort` into Claude Code (Haiku print-mode hangs). OpenCode / OMP / Pi / agy pins are preferences; Rig lists models from that CLI and picks one that exists (cache `~/.rig/cache/model-catalogs.json`). Never Sol/Astra/Fable. OpenCode preference: cheap `openai/gpt-5.4-mini` `--variant minimal`, implement `openai/gpt-5.6-luna` `--variant high`, hard/review `openai/gpt-5.6-terra` `--variant max`. OMP/Pi preference: cheap `grok-4.5` `--thinking low`, implement/hard `grok-4.6` `--thinking high`, review `claude-opus-5` `--thinking high`. agy preference: cheap `gemini-3.8-flash-low` `--effort low`, implement `gemini-3.8-flash-high` `--effort high`, hard/review `gemini-3.1-pro-high` `--effort high`.

Cursor child is print-mode `stream-json` with `--force --trust`. Do not use `--worktree` (edits must land in this repo). Binary is `cursor-agent`, not a random `agent`. Grok Bot.app cannot be spawned.

OpenCode child is `opencode run --format json --dir $REPO --auto`. OMP child is `omp -p --mode json --cwd $REPO --approval-mode write` (not `--auto-approve`). Pi child is `pi -p --mode json --approve`. agy child is `agy -p` with `--output-format json --mode accept-edits --print-timeout ${TIMEOUT_SECS}s --disable-slash-commands`. No `--dangerously-skip-permissions`. Headless shell uses a scoped `permissions.allow` `command(*)` for the job, restored after. JSON `denied_actions` is a fail even if agy exits 0. If both OMP and Pi are effective, pick OMP.

Claude Code child is print-mode `stream-json` (not buffered `json`), `acceptEdits`, print prompt last argv, no `--bare` (that drops OAuth), no `--dangerously-skip-permissions` (org managed settings can disable bypass). Anthropic remote deny rules like `Bash(eval $(wget*))` are invalid nested parens; they print to stderr and are skipped. `rig jobs` / `rig tui` hide that noise.

A Claude child with no TTY cannot click Allow. When it needs permission, the job status becomes `ask` and `rig job wait` exits 2. The parent answers — that is the interaction. Do not ignore it. Do not close the job. Do not spawn Grok/Codex/Cursor/OpenCode/OMP/Pi/agy instead. The work timeout pauses during `ask` and restarts after allow, so a slow allow does not kill the child. There is no Claude-style `rig job allow` loop for agy.

```bash
rig job wait <id>                  # one blocking wait; no --timeout; exit 2 = ASK
rig job wait <id1> <id2>           # wait-all after implement ok (review + seed)
rig job allow <id>                 # safe worker work — child continues
rig job deny <id> --reason "..."   # destructive / prod / secrets
```

Safe → allow: read, edit, test, ssh/gather, git status/diff/add/commit. Ask the user only for force-push, prod deploy, rm -rf outside the repo, or secrets. Prefer MCP: `rig_job_wait` / `rig_job_allow` / `rig_job_deny`. Bash fallback if MCP is missing. TUI: `y` / `n`.
