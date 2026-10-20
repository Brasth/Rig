# Adapter packages: adding a worker CLI

A worker CLI can be added to Rig as a directory, with no change to Rig's code. The ten built-in workers (`grok`, `claude`, `codex`, `cursor`, `opencode`, `omp`, `pi`, `agy`, `devin`, `mimo`) stay in code and cannot be redefined.

## Where adapters live

Rig finds `<name>/adapter.json` under the runtime's `adapters/` directory, under `~/.config/rig/adapters/` (which updates never touch), and under every directory in `RIG_ADAPTER_DIRS` (path-separated). The directory name must equal the adapter name. A broken adapter is skipped and reported by `rig doctor` and `rig adapters list`; it never stops the others.

A complete example is in [`examples/adapters/echo-agent`](../examples/adapters/echo-agent). To use one, copy it to a directory above, enable it in `.rig/harness.toml`, and choose it explicitly:

```toml
[workers]
echo-agent = true
```

An adapter worker is chosen by name (`worker = "echo-agent"` on a launch); smart routing never picks it, and Rig has no model catalog for it, so the model you pass is used as given. Continuing a job with a native session (resume) is not supported.

## Trust

An adapter names the program Rig runs and the arguments it gets, so installing one is as trusted as installing any program you run: only add adapters you have read. The validator refuses secrets in argv and paths for `binary`, but it cannot tell a harmless executable from a harmful one. A project still has to opt in by listing the worker under `[workers]`.

## The manifest

```json
{
  "schema": 1,
  "name": "acme",
  "binary": "acme-agent",
  "provider": "acme",
  "command": ["{binary}", "run", "--cwd", "{work}", "--prompt-file", "{brief_file}", "{model_args}", "{mcp}", "{permissions}"],
  "model_args": ["--model", "{model}"],
  "effort_args": ["--effort", "{effort}"],
  "mcp": {"mode": "flag", "argv": ["--mcp-config", "{mcp_path}"]},
  "permissions": {"host": ["--yes"], "container": ["--yes"]},
  "container": {"supported": true, "credential_paths": [".acme"]},
  "output": {"decoder": "jsonl", "result_field": "result"}
}
```

Unknown keys are errors. Required: `schema` (1), `name`, `command`, `mcp`.

| Key | Meaning |
| --- | --- |
| `name` | Lowercase letters, digits and dashes, starting with a letter, 2 to 24 characters. |
| `binary` | The executable name on `PATH` (default: the name). A name, never a path. |
| `command` | The argv as a list, never a shell string. Placeholders expand inside an element: `{binary}` (the resolved path), `{work}`, `{brief_file}`, `{brief_text}` (always one argument), `{model}`, `{effort}`, `{job_dir}`, `{job_id}`, `{session_id}`, `{mcp_path}`. The whole-element tokens `{model_args}`, `{effort_args}`, `{mcp}` and `{permissions}` splice a list in; a token you leave out is appended at the end, so the MCP and permission flags cannot be forgotten. |
| `model_args`, `effort_args` | Added only when a model or effort was selected. |
| `mcp` | Required. Every worker must reach the job-scoped child MCP, because its first operation is `rig_job_inbox`. `{"mode": "flag", "argv": [...]}` adds argv naming `{mcp_path}`; `{"mode": "env", "env": {"NAME": "{mcp_path}"}}` sets environment variables. The file holds only the `rig` and `rig-ask` servers. A CLI that cannot take a job-scoped MCP config cannot be an adapter. |
| `permissions` | The CLI's own flags for non-interactive work, per mode (`host`, `container`). Rig adds none of its own. |
| `container` | `supported` and `credential_paths` (relative to the home directory). Container mode also needs the usual `[sandbox.workers]` credential choice. |
| `output` | `plain`: the summary is the last non-empty stdout line. `jsonl` with `result_field`: a dotted path read from the last JSON line that has it. |

Rules the validator enforces: no secrets in argv (names containing token, secret, password, api key or bearer are refused), no newlines or NUL, no unknown placeholders, `mcp` must name `{mcp_path}`.

## Checking an adapter

```sh
rig adapters check DIR          # all checks
rig adapters check DIR --no-live
rig adapters list               # what Rig found, and what is broken
rig adapters validate DIR       # the manifest only
```

`check` runs the adapter through the real launcher (`run-worker.sh`) in a throwaway repository, with a stand-in CLI installed under the adapter's binary name. The stand-in finds the MCP config from its own argv or environment, starts the server that file names, and answers `rig_job_inbox`, so a manifest whose MCP delivery does not work fails here. It checks the manifest; the dry-run argv (binary, working directory, MCP file, host permission flags, model flags only when a model is set); that the MCP payload holds only Rig servers; a live job with a real handshake, the output decoder and change evidence; that cancel stops the whole process tree; and that container mode is refused without a credential choice. The same checks run in Rig's test suite over the example and two fixture adapters.

**What this does not prove.** The stand-in is not your CLI. It shows the manifest is well formed and the launcher plumbing works; whether the real CLI honours the flags (for example whether it really reads the MCP file it is given and does not also load a user config) is for you to check once with the real binary.
