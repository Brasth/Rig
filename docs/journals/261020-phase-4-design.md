# Design note: Phase 4, native panes and platform

Status: accepted for Phase 4. It records what each item will and will not do before code, because three of the five items touch things that cannot be fully verified here (real parent and worker CLIs).

## 4.2 Adapter packages (built first: the exit check "a new worker ships as an adapter package alone")

**What exists.** Ten workers are wired in by name in about a dozen places: the launch command in `run-worker.sh`, MCP delivery in `child_mcp.py`, the worker lists in `harness.py`, `worker_launch.py`, `jobs.py`, `container_modes.py`, and the shell parser in `detect-binaries.sh`.

**Decision.** A new worker is a directory `adapters/<name>/` holding `adapter.json`. Rig discovers it from the runtime's `adapters/` directory and from any directory in `RIG_ADAPTER_DIRS`. The ten built-in workers stay as code. Porting them to manifests would rewrite the launcher for no user benefit and risks every worker's byte-identical argv, so they are not manifests. Their argv construction did move out of `run-worker.sh` into `scripts/worker_commands.py`, pinned byte-for-byte to the old shell output by `tests/fixtures/worker-argv/matrix.json` (480 cases, captured by running the old function; `capture.py` regenerates it). `run-worker.sh` calls that module once and keeps only the adapter branch.

**Manifest (schema 1).** Strict: unknown keys are errors.

```json
{
  "schema": 1,
  "name": "acme",
  "binary": "acme-agent",
  "provider": "acme",
  "command": ["{binary}", "run", "--cwd", "{work}", "--prompt-file", "{brief_file}", "{model_args}", "{mcp}", "{permissions}"],
  "model_args": ["--model", "{model}"],
  "effort_args": ["--effort", "{effort}"],
  "mcp": {"mode": "flag", "argv": ["--mcp-config", "{mcp_path}", "--strict"]},
  "permissions": {"host": ["--yes"], "container": ["--yes"]},
  "container": {"supported": true, "credential_paths": [".acme"]},
  "output": {"decoder": "plain"}
}
```

- `command` is an argv list, never a shell string. `{binary}`, `{work}`, `{brief_file}`, `{brief_text}`, `{model}`, `{effort}`, `{job_dir}`, `{job_id}`, `{session_id}` expand inside an element; `{model_args}`, `{effort_args}`, `{mcp}`, `{permissions}` are whole elements that splice a list (empty when not applicable). A splice token that is missing from `command` is appended at the end, so a manifest cannot forget the MCP flags.
- `mcp` is required: a worker that cannot reach Rig's child MCP cannot do its first operation (`rig_job_inbox`), which the child rules make mandatory. `mode = "flag"` passes extra argv naming `{mcp_path}`; `mode = "env"` sets environment variables naming it. The file is the same job-scoped `rig`/`rig-ask`-only payload every worker gets. A CLI that cannot take a job-scoped MCP config is not eligible, and the manifest cannot claim isolation Rig did not verify: the conformance suite checks the payload.
- `permissions.host` and `permissions.container` are the CLI's own flags for non-interactive work in each mode; Rig does not add or infer any. Container mode requires `container.supported` and an explicit `[sandbox.workers]` credential choice like every worker.
- `output.decoder` is `plain` (the summary is the last non-empty stdout line) or `jsonl` with `result_field` (a dotted path read from the last JSON line that has it).
- No resume, no native session ids, no model catalog, no smart routing. An adapter worker is chosen explicitly (`worker = "acme"`); `rig route` never picks it on its own. Models are passed through as given.

**Conformance.** `rig adapters check DIR` and the repository test suite run the same checks over any adapter: manifest validity; the dry-run argv contains the MCP delivery and the permission flags for the mode and nothing that names a secret; the MCP payload holds only `rig` servers; a stand-in binary named `binary` completes a live job through `run-worker.sh` with a real MCP handshake and writes evidence; cancel stops it; container mode is refused without a credential choice. A shipped example adapter proves the exit check.

## 4.1 Shell without tmux, log pane, `dc exec` shell

**Decision.** The shell gains a Logs tab that follows the selected job's stdout through the existing `jobs.log` method (plain text, tail, no terminal emulation), so `rig shell --no-tmux` is the board plus logs in one terminal with nothing else installed. Key `s` runs an interactive `dc exec --no-start <workspace>` for the selected job's container by suspending the board (`tea.ExecProcess`), so the terminal is the real one and no emulator is needed. The server decides the argv (`env.shell`): the job's worktree or the repository, never `dc exec` without `--no-start`.

**Not done, deliberately.** Embedding the parent CLI in a pane without tmux needs a terminal emulator inside Bubble Tea. That is a library-sized dependency and a large test surface; the exit check only asks for plain-text streams. Without tmux the parent runs in the user's other terminal. The tmux layout is unchanged.

## 4.3 Web board, read-only

**Decision.** `rig web` serves one HTML page and a few JSON endpoints from the Python standard library on 127.0.0.1 only. It reads through the local API's read methods (`board.snapshot`, `workspace.status`, `fleet.status`, `job.evidence`, `jobs.log`) and renders the server-built views, never re-deriving state. No mutating method is reachable: the proxy is an allowlist, not a denylist. Access needs a random token (set as a cookie from the first URL), the `Host` header must be the bound address (DNS rebinding), no CORS headers, a strict CSP, and no external assets.

## 4.4 Headless parent with Rig-rendered chat (experiment)

**Decision.** Behind `RIG_EXPERIMENT_CHAT=1` (or `[experiments] chat = true`), `rig chat` sends a user message to a parent CLI in its headless mode (`claude -p --output-format stream-json`, `codex exec --json`), decodes the event stream into transcript lines stored under `.rig/chat/<session>.jsonl`, and exposes `chat.send` and `chat.transcript` through the local API; the web board shows the transcript and a box to send. The parent still gets Rig's normal parent MCP; Rig renders only text, tool names and the final result.

**Honest limit.** The stream formats come from the CLIs' documentation, not from a verified run: no real parent CLI or credentials are available here. The decoder is tested against recorded-shape fixtures and a stand-in. It stays an experiment: off by default, no change to any default path, and `rig doctor` says so.

## 4.5 Does `bin/rig` move to Go?

**Decision: no, for now.** `bin/rig` is a bash dispatcher over Python scripts; the work is in Python. A Go dispatcher would duplicate argument parsing for about eighty subcommands to save a bash startup. Revisit if the shell must call `rig` often enough for startup to matter, or if bash portability breaks a supported platform. Recorded as `docs/journals/261020-bin-rig-dispatch-decision.md`.

## Exit check

The shell runs without tmux for plain-text streams; a new worker ships as an adapter package alone and passes the conformance suite, including a live stand-in job.
