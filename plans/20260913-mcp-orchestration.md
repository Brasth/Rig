# MCP orchestration + tmux companion (2026-09-13)

## Approved intent

Require parent MCP orchestration for wrapper children:

1. `rig_session`
2. `rig_queue_claim` when draining
3. `rig_job_launch` (brief/files/access/worker/model/effort + owner credentials / review provenance)
4. `rig_queue_spawned` when claimed
5. `rig_job_wait` → message / allow / deny
6. `rig_job_requirements` / `rig_job_check` / `rig_job_accept`

Children MUST call `rig_job_inbox` first (handshake connected/time/protocol 1). Fail exact `child MCP handshake missing` without success while preserving evidence and ownership. Permission bootstrap does not count. Legacy/unknown jobs are not retroactively failed. Cursor remains excluded (no safe scoped MCP). Other CLIs need configured MCP + runtime handshake.

CLI/TUI remain human use and MCP recovery. Shell `run-worker.sh` is internal/human fallback, not the agent default. No separate native subagents without scoped MCP. `parent_writes` is explicit parent work; read-only parent stays if no capable child; independent review unavailable without a different vendor.

Durable `.rig/jobs/<id>/` files: `launcher.log` (prechild), `stdout.log`, `activity.json`, `meta.json`, `result.json`, inbox/ask/reply, evidence. Detached wrapper survives parent/MCP shutdown. `stdout.log` prune only after successful decoded activity; failures retain. Cancellation/transport recovery and queue credential privacy unchanged (`rig job wait ID --timeout 0`, `stop-unconfirmed`, `native-cancel-required`, idempotency/owner flags, credentials receipt examples).

Tmux: session-local mouse; wheel in main parent pane controls history (WheelUp → copy-mode -e + five lines; WheelDown consumed outside mode; live bottom exits copy-mode). Keyboard Up/Down unchanged; F8/F9/popups unchanged; no global/root changes. Requires updated runtime/restart. Manual mouse/trackpad Codex+Grok private/nested/alternate-screen/detach acceptance remains NOT RUN here.

## Implementation / tests status

- Implementation present in working tree: admission/harness/jobs/rig_mcp/route/run-worker/detect-binaries, new `child_mcp.py` / `worker_launch.py`, launch9 + child6 tests.
- This task completes documentation, generated parent protocol, doctor readiness fixtures, and protocol-doc assertions.
- Full suite before review fixes: **669 tests OK** (`/tmp/rig-mcp-final-suite.log`).
- Independent review (Anthropic via OMP): **completed** with 3 findings — (1) HIGH docs API contradiction: MCP flows must prepare brief TEXT and pass `brief` to `rig_job_launch` (tool creates dir/brief; do not precreate); (2) LOW readiness must honor `disabled=true` and reject malformed enabled/disabled without TypeError; (3) LOW `_simple_toml` must accept root keys before `[mcp_servers.rig]` when tomllib/tomli are absent. All three addressed with targeted regressions.
- Full suite after review fixes: **671 tests OK** in 142.077s (`/tmp/rig-mcp-reviewed-suite.log`).
- Physical wheel acceptance and runtime install: **NOT RUN**. Manual mouse/trackpad Codex+Grok acceptance remains NOT RUN.

## Safe rollout

1. Stop new admissions.
2. Finish or explicitly cancel workers; confirm termination; close/reconcile held scopes/reservations.
3. Install **all** launchers at the same revision.
4. Fully restart every parent/MCP session before admitting new work.
5. Mixed-version admission writers are unsupported.

## Rollback

Install the prior revision. Existing jobdata remains readable. Preserve pending queue text, worker/cap values, memory, and unrelated AGENTS content. Never clear blocked ownership by deleting reservation files.

## Commits (authorized)

1. `fix(ui): route companion wheel input to tmux scrollback` — only `scripts/ui_launch.py` + `tests/test_ui_launch.py`
2. `feat(mcp): require job-scoped orchestration and child handshake` — scoped MCP implementation/docs/tests from this work

No push / install / runtime restart / global skill sync in this session.
