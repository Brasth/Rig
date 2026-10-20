# echo-agent: the example adapter

A worked example of a worker shipped as an adapter package alone: this directory and nothing else. It describes a
made-up CLI, `echo-agent`, that takes `run --cwd DIR --prompt-file FILE`, an optional `--model`, an MCP config file
through `--mcp-config`, and `--auto-approve` for non-interactive work. Its last stdout line is JSON with a `result`.

To add your own worker, copy this directory to `adapters/<name>/` in the Rig runtime, or into any directory listed in `RIG_ADAPTER_DIRS`,
edit `adapter.json`, enable it in `.rig/harness.toml` under `[workers]` (`<name> = true`), and run
`rig adapters check <directory>`. The schema and its rules are in `docs/adapters.md`.
