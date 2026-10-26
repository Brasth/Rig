# Plan: DeepSeek Harness (`dsh`) as a Rig worker

Status: 2026-10-26. Steps 1 and 2 are built on the unverified assumptions (below); Step 0 is the script `scripts/dsh-probe.sh`, to be run once with the real `dsh` and a key; Steps 3 and 4 wait for its result. The adapter ships as `examples/adapters/deepseek` and is installed by `rig dsh-worker install`, because the runtime installer copies only the built-in adapter folders. Written from Rig's code and from DeepSeek's published README
and CLI reference; the parts marked **unverified** need one run with the real `dsh` binary and a DeepSeek key, which
the environment this was written in does not have.

## 1. What DeepSeek Harness is, as far as the worker question goes

DeepSeek Harness is DeepSeek's open-source agent harness (`deepseek-ai/deepseek-harness`, MIT, npm package
`@deepseek-ai/dsh`, command `dsh`). It is in developer preview and the README says plainly that there will be
compatibility-breaking changes. Its design is "everything is a plugin": tools, sessions, sub-agents and providers are
plugins composed into a *profile* (a directory holding `package.json` with the bundle list and a `cordis.patch.yml`
overlay), under `$DSH_HOME/profiles/<name>` (default `~/.dsh`).

What matters for Rig, from the official CLI reference:

| Need of a Rig worker | What `dsh` offers | Confidence |
| --- | --- | --- |
| Run one task non-interactively and exit | `dsh --profile headless "task"`: one fresh persisted session, prints the final answer to stdout, exits 0 for completed, else 1. Reasoning goes to stderr under `dsh: reasoning:`. | official |
| Take the brief | Positional argument only. No stdin, no file flag documented. | official |
| Work in the job's checkout | No `--cwd`; the workspace is the invoking directory. Rig already runs workers with `cwd = work`. | official |
| Reach Rig's job-scoped MCP (`rig`, `rig-ask`) | The CLI ships `@deepseek-ai/dsh-mcp-client` "as a dependency for patch layers"; no server is enabled by default. Configured through a `cordis.patch.yml` insert (server name, stdio command/args/cwd), per profile or through `--patch <file>` on the command line. | plugin: official; config keys: third-party, **unverified** |
| Permissions for unattended work | New sessions default to the `workspace-write` preset (writes confined to the workspace and temp roots). `DSH_PERMISSION_MODE` sets the process fallback. No `--yes` flag documented; whether headless ever blocks on an approval is **unverified**. | official / unverified |
| Credentials | `DEEPSEEK_API_KEY` from the environment, else `$DSH_HOME/.credentials.yaml`, the invoking dir's `.env`, `$DSH_HOME/.env`. `DEEPSEEK_BASE_URL` for another endpoint is third-party. | official |
| Pick a model / effort | No `--model` flag. Model and provider are profile configuration. | official (absence) |
| Structured output | None documented (no `--json`). `--profile sdk` is JSON-RPC over stdio and `--profile acp` is ACP, both heavier than a worker needs. | official |
| Resume a session | Not documented for headless. | official (absence) |
| Telemetry | Default-on session-log upload; off with `DSH_TELEMETRY_MODE=DISABLED` and a config switch. | official |

Two consequences shape the plan:

1. **MCP delivery is a YAML patch, not a JSON file path.** Every other Rig worker takes Rig's `mcp.json` by flag, env
   or a private config dir. `dsh` wants a `cordis.patch.yml` that inserts the MCP-client plugin with the server's
   command and args. Rig has to write that file per job, the way it already writes a private `mimocode.json` for mimo.
2. **No model flag.** Smart routing's model/effort choice cannot be passed on the command line; it has to be written
   into the per-job profile too, or left to the profile's default.

## 2. Where it fits in Rig

Rig has two ways to add a worker:

- **Adapter package** (`adapters/<name>/adapter.json`, no code): the command is an argv template, MCP is `flag` or `env`
  naming `{mcp_path}`, output is `plain` or `jsonl`, models pass through, smart routing never picks it. Checked by
  `rig adapters check`.
- **Built-in worker** (code in `worker_commands.py`, `child_mcp.py`, `container_modes.py`, `route.py`, ...): bespoke MCP
  delivery, a model catalog, routing, container rules, doctor checks.

`dsh` does not fit the adapter manifest as it stands: the manifest cannot express "write a YAML patch from the MCP
payload" nor "set `DSH_HOME` to a job-private directory". Two honest options:

**Option A: wrapper + adapter (fastest, lowest risk).** Ship a small launcher, `rig-dsh`, beside `bin/rig`. The
adapter manifest names it as the binary. The wrapper reads Rig's `{mcp_path}` JSON, writes a job-private `DSH_HOME`
with a `rig` profile (cloned from `headless`, plus a `cordis.patch.yml` inserting `rig` and `rig-ask` MCP servers, and
the model if one was given), sets `DSH_PERMISSION_MODE`, and runs `dsh --profile rig "<brief>"`. Rig's code is untouched;
the conformance suite already covers argv, MCP payload, live stand-in handshake, cancel, container refusal.
Costs: the wrapper is a second program to keep in step with `dsh`'s patch format; smart routing still never picks it.

**Option B: built-in worker `deepseek`.** Add it to `BUILTIN_WORKERS`, `worker_commands.py` (argv), `child_mcp.py`
(a `write_dsh_job_home` like mimo's private config dir, `DSH_HOME` in env), `container_modes.py`, `route.py` (a
provider `deepseek` already exists for review independence; add `("deepseek", kind)` models), `detect-binaries.sh`,
doctor, the Settings and Who-does-what screens (they read `harness.WORKERS`, so they follow). Costs: ten or so files,
the byte-pinned argv matrix grows, and it bakes a developer-preview CLI's patch format into Rig's core.

**Recommendation: A first, B only when `dsh` leaves developer preview.** A is a day of work plus a verification run and
gives `worker = "deepseek"` on launches; B is justified once the patch format and headless behaviour have held still
for a few releases and routing to DeepSeek is wanted by default.

## 3. Steps (Option A)

### Step 0: verify the unknowns with the real binary (needs a machine with Node 22+/24, pnpm, and a DeepSeek key)

Record the exact outputs in `docs/journals/` so later steps rest on facts, not the README:

1. `dsh --version`; `npx @deepseek-ai/dsh --profile headless "say hi"`: confirm stdout is only the answer, exit code 0.
2. Does headless ever wait for an approval under `workspace-write`? Try a task that writes a file and one that runs a
   shell command. Note the effect of `DSH_PERMISSION_MODE`.
3. MCP: make a profile `rig` from `headless`, add a `cordis.patch.yml` inserting `@deepseek-ai/dsh-mcp-client` with a
   stdio server (`python3 .../child_mcp.py ...` as Rig's `mcp.json` names it). Confirm the tool shows up as
   `mcp__rig__rig_job_inbox` and is callable. Confirm whether `--patch <file>` on the command line is enough instead of
   editing the profile (that decides whether the wrapper needs a whole `DSH_HOME` per job or only a patch file).
4. Model selection: which config row sets the model, and whether a patch can override it per run.
5. `--dump-config` with the profile, to learn the row ids the patch must target.
6. Argument length: a 100 KB brief as a positional argument (macOS `ARG_MAX` is 256 KB per arg list; briefs are
   usually a few KB). If it fails, the wrapper writes the brief to a file and the task text becomes "read and follow
   `<file>`".
7. Telemetry: confirm `DSH_TELEMETRY_MODE=DISABLED` suppresses uploads; Rig should set it for job runs (the brief and the
   repository's files are the user's, not telemetry).

Exit: a journal entry with the commands and outputs, and go/no-go on each row of the table above.

### Step 1: the `rig-dsh` wrapper (Python, in `scripts/`, exposed as `bin/rig-dsh`)

- Input: the same values the adapter command template has: `{work}`, `{brief_file}`, `{mcp_path}`, `{model}`,
  `{effort}`, `{job_dir}`.
- Writes `<job_dir>/dsh-home/` as `DSH_HOME`: `profiles/rig/package.json` (bundles = headless's plus the MCP client)
  and `profiles/rig/cordis.patch.yml` with two inserts (`rig`, `rig-ask`) built from the servers in `{mcp_path}`
  (command, args, env, cwd), plus the model row when a model was given. If Step 0 shows `--patch` is enough, write only
  the patch file and keep the user's `DSH_HOME`.
- Credentials: never copied. `DEEPSEEK_API_KEY` is inherited from the environment (the window app already imports the
  login shell's environment, so a key exported there reaches jobs).
- Env it sets: `DSH_TELEMETRY_MODE=DISABLED`, `DSH_PERMISSION_MODE` per mode (`workspace-write` on host; the same in a
  container, where the container is the boundary).
- Runs `dsh --profile rig "<brief text>"` with `cwd = work`, passes stdout and stderr through, returns `dsh`'s exit code.
  The `plain` decoder then takes the last non-empty stdout line as the summary, which is `dsh`'s final answer.
- Refuses with a clear line when `dsh` is not on PATH, when Node is too old, or when the profile cannot be made.

Tests: unit tests for the patch rendering (given a Rig `mcp.json`, the YAML names the same command/args/env and
nothing else), for the env it sets, and for the refusals.

### Step 2: the adapter package `adapters/deepseek/adapter.json`

```json
{
  "schema": 1,
  "name": "deepseek",
  "binary": "rig-dsh",
  "provider": "deepseek",
  "command": ["{binary}", "--work", "{work}", "--brief-file", "{brief_file}", "--job-dir", "{job_dir}", "{model_args}", "{effort_args}", "{mcp}", "{permissions}"],
  "model_args": ["--model", "{model}"],
  "effort_args": ["--effort", "{effort}"],
  "mcp": {"mode": "flag", "argv": ["--mcp-config", "{mcp_path}"]},
  "permissions": {"host": ["--mode", "host"], "container": ["--mode", "container"]},
  "container": {"supported": true, "credential_paths": [".dsh"]},
  "output": {"decoder": "plain"}
}
```

Plus `adapters/deepseek/DEEPSEEK.worker.md` (the worker rules file the other adapters carry) and an entry in
`docs/adapters.md`. `rig adapters check adapters/deepseek` must pass with the stand-in; the repository test suite runs
the same checks. The user enables it with `[workers] deepseek = true` and launches with `worker = "deepseek"`.

Open point: `binary` must be a name on PATH; `bin/rig-dsh` is on PATH wherever `rig` is (installed `~/.rig/bin`, or
the app's bundled `rig/bin`).

### Step 3: setup and doctor

- `rig setup`: offer to install `dsh` (`npm install -g @deepseek-ai/dsh`, or document `npx`), and to add the
  `@deepseek-ai/dsh-mcp-client` plugin to a `rig` profile if Step 0 shows a private `DSH_HOME` cannot carry it without a
  `pnpm` install (then `dsh plugin --profile rig add @deepseek-ai/dsh-mcp-client` once, at setup, not per job).
- `rig doctor`: `dsh` found, version in range, `DEEPSEEK_API_KEY` or `.credentials.yaml` present, the MCP client
  resolvable, and a short headless smoke under `--smoke` only, because it costs tokens.
- Window app: the agents list (`src/main/agents.ts`) gets `deepseek` with `bin: 'dsh'`, key variable `DEEPSEEK_API_KEY`
  (so it shows Ready without a browser sign-in), and an install command. Settings > Agents then shows it like the
  others; "Who does what" keeps saying routing does not pick it (true for an adapter).

### Step 4: documentation and release note

`docs/usage.md` worker table, `docs/adapters.md` (the first real adapter shipped in-tree), release note. State the
limits: adapter worker, chosen by name, no resume, model only through the profile, developer-preview CLI.

### Later: Option B items, if wanted

- Smart routing: `("deepseek", kind)` rows in `route.MODELS` once model names are known; provider `deepseek` is
  already recognised for review independence. Then "tests -> deepseek" can appear in the plan-limits note.
- Parent role: `dsh --profile acp` or `sdk` could drive a Rig chat the way claude and codex do; that is a new stream
  decoder in `rig_chat.py`, out of scope for "worker".
- Container mode: a `dc` feature installing Node and `dsh` in the sandbox image, credential path `.dsh`.

## 4. Risks

- **Developer preview.** The patch format and the headless contract may change under us. Mitigation: the wrapper is
  one file, pinned to a `dsh` version range checked at start; `rig doctor` says when the installed `dsh` is outside it.
- **MCP config is third-party knowledge until Step 0.** If the plugin cannot be configured per run, the adapter cannot
  meet Rig's rule that every worker reaches the job-scoped MCP, and the plan stops at Step 0 with a clear answer.
- **Headless approvals.** If `dsh` blocks on an approval prompt with no TTY, the job hangs until Rig's timeout.
  Step 0 item 2 decides whether `DSH_PERMISSION_MODE` avoids it; if not, `dsh` is not usable unattended yet.
- **Telemetry.** Default-on upload of session logs is a surprise for a worker that reads private repositories; Rig
  disables it for its jobs and says so in the docs.

## 5. Effort

Step 0 half a day with a key and a machine; Steps 1 and 2 one to two days; Steps 3 and 4 one day. Option B later:
three to five days plus the argv matrix and routing tests.

## Sources

- Official repository and README: https://github.com/deepseek-ai/deepseek-harness
- Official CLI README: https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.md
- Official CLI reference: https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.md
- Third-party (MCP patch example, unverified): https://zens.ink/journal/deepseek-harness-mcp-setup/
- Third-party (CLI and profiles, `DEEPSEEK_BASE_URL`): https://dsh.hicyou.com/en/docs/user-guide/cli
- Rig's own adapter design: `docs/journals/261020-phase-4-design.md`, `docs/adapters.md`
