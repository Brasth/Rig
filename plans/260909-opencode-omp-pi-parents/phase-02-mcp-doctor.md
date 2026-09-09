---
phase: 2
title: "MCP, skill links, doctor"
status: completed
priority: P1
effort: "4h"
dependencies: [1]
---

# Phase 2: MCP, skill links, doctor

User-global JSON MCP + skill symlinks + doctor rows. No interactive MCP CLIs. No project `mcp.json`.

## File ownership

- `scripts/install-ui.py`
- `bin/rig` (`cmd_setup`, `cmd_doctor`)
- `tests/test_install_ui.py`
- `tests/test_cli.py`

## Requirements

JSON merge (new helpers next to the TOML ones):

- Create parent dirs and empty `{}` if the file is missing.
- Parse JSON. If JSONC, strip `//` and `/* */` on read; write JSON (indent 2, trailing newline). Keep sibling keys (`plugin`, `model`, other MCP servers).
- OpenCode: set `mcp.rig` (or `mcp.servers.rig` if `mcp.servers` already exists) to `{ "type": "local", "command": [abs_launcher], "enabled": true }`.
- OMP / Pi: set `mcpServers.rig` to `{ "command": abs_launcher }`. Do not set `--auto-approve`. Do not copy into project `.mcp.json`.
- Refresh launcher path if the server already exists. Idempotent.
- Paths (env overrides for tests):
  - OpenCode: `OPENCODE_CONFIG` or `$HOME/.config/opencode/opencode.json`
  - OMP: `OMP_MCP` or `$HOME/.omp/mcp.json`
  - Pi: `PI_CODING_AGENT_DIR/mcp.json` or `PI_AGENT_DIR/mcp.json` or `$HOME/.pi/agent/mcp.json`

Setup skill links (`ensure_skill_link`):

- `~/.config/opencode/skill/{delegate-harness,rig-jobs}`
- `~/.omp/agent/skills/{delegate-harness,rig-jobs}`
- `~/.pi/agent/skills/{delegate-harness,rig-jobs}`

Doctor:

- MCP rows for `opencode`, `omp`, `pi` in addition to grok/codex.
- OpenCode present if JSON has `mcp.rig` or `mcp.servers.rig` with the launcher path.
- OMP/Pi present if `mcpServers.rig.command` contains `rig-mcp`.
- Pi extra: if `settings.json` packages lacks `pi-mcp-adapter`, print `pi MCP adapter missing — pi install npm:pi-mcp-adapter`.
- Skill rows include the three new dirs.
- Watch line: `/rig` in Grok, Codex, OpenCode, OMP, or Pi.

## Tests (temp HOME, never real home)

- Missing OpenCode json → created with `mcp.rig`.
- Existing OpenCode json with `plugin` array → plugins kept, `mcp.rig` added.
- Existing `mcp.servers` → write `mcp.servers.rig`, do not also write top-level `mcp.rig`.
- OMP/Pi missing file → created `mcpServers.rig`.
- Existing `mcpServers.other` → kept.
- Doctor with stub files reports present; without → `missing — run: rig setup`.
- Existing grok/codex MCP regex still passes.
- `python3 -m unittest discover -s tests`.

## Done when

Setup writes the three MCP files without clobber; doctor sees them; tests green. No `RIG_LIVE=1`. No `opencode mcp add`.
