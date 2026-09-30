<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->
## Child commands

Exact argv lives in `run-worker.sh`. Pass `RIG_MODEL` and `RIG_EFFORT` from MCP `rig_pick`. Defaults if unset: Codex `gpt-6-luna` low, Grok `grok-4.7` high, Claude Code `claude-sonnet-5-5` medium, Cursor `composer-2.5`. Explore: Codex `gpt-6-luna` low; mini writes: Codex `gpt-6-luna` low. Cheap Claude: `claude-haiku-4-5-20251001` low, Cursor `composer-2.5-fast`. Hard/review Claude Code: `claude-opus-5-5` high. Cursor hard `cursor-grok-4.6-high`; Cursor review `claude-opus-5-5-thinking-high`. Cursor has no `--effort` flag. Haiku cheap jobs record `low` on the board but do not pass `--effort` into Claude Code (Haiku print-mode hangs). OpenCode / OMP / Pi / agy / Devin smart profiles require exact selectors or declared aliases confirmed by the CLI catalog (cache `~/.rig/cache/model-catalogs.json`); legacy mode retains its older resolver. Never Sol/Astra/Fable. OpenCode preference: cheap `openai/gpt-5.4-mini` `--variant minimal`, implement `openai/gpt-6-luna` `--variant high`, hard/review `openai/gpt-5.6-terra` `--variant max`. OMP/Pi preference: cheap `grok-4.5` `--thinking low`, implement/hard `grok-4.6` `--thinking high`, review `claude-opus-5-5` `--thinking high`. agy preference: cheap `gemini-3.8-flash-low` `--effort low`, implement `gemini-3.8-flash-high` `--effort high`, hard/review `gemini-3.1-pro-high` `--effort high`. Devin (child-only): exact `swe-2-medium` explore/mini/bulk, `swe-2-high` implement, `swe-2-max` hard/review. Never swe aliases, SWE-1.x, Fusion, or defaults.

Cursor child is print-mode `stream-json` with `--force --trust`. Do not use `--worktree` (edits must land in this repo). Binary is `cursor-agent`, not a random `agent`. Grok Bot.app cannot be spawned.

OpenCode child is `opencode run --format json --dir $REPO --auto`. OMP child is `omp -p --mode json --cwd $REPO --approval-mode write` (not `--auto-approve`). Pi child is `pi -p --mode json --approve`. agy child is `agy -p` with `--output-format json --mode accept-edits --print-timeout ${TIMEOUT_SECS}s --disable-slash-commands`. Devin child is `devin --print --prompt-file $BRIEF --model $MODEL --permission-mode accept-edits --respect-workspace-trust true`. No `--dangerously-skip-permissions`, `--permission-mode dangerous`, Fusion, or `devin cloud`. Devin injects job-scoped Rig stdio MCP into `.devin/mcp_config.local.json` (restore existing or remove created) and refuses a second Devin job in the same repo. Headless shell uses a scoped `permissions.allow` `command(*)` for the job, restored after. JSON `denied_actions` is a fail even if agy exits 0. Default preferences put OMP before Pi.

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
