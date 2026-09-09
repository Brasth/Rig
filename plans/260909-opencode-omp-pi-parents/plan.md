---
title: "OpenCode, OMP, and Pi as parents"
description: "One cook. Three parents. Reverses worker-only lock. Claude and Cursor stay never parent."
status: completed
priority: P1
effort: "1d"
tags: [parents, opencode, omp, pi]
created: 2026-09-09
---

# OpenCode, OMP, and Pi as parents

One cook. Three parents. Reverses the worker-only lock from `plans/260909-opencode-omp-pi-workers/`.

Claude Code and Cursor stay **never** the parent. Codex and Grok stay parents. OpenCode, OMP, and Pi become parents **when you open that CLI**. `rig use` records the preferred default; opening the CLI is what makes it live.

## Phases

| Phase | File | Status |
|---|---|---|
| 1 | [phase-01-identity.md](phase-01-identity.md) | completed |
| 2 | [phase-02-mcp-doctor.md](phase-02-mcp-doctor.md) | completed |

## Locked decisions

- **One cook** for all three as parents.
- **Default preferred parent stays `codex`.** Template and existing `.rig/harness.toml` `parent =` are never flipped.
- **Live parent** already detected (`opencode|opencode-*`, `omp|omp-*`, exact `pi`). When live, that name is **off as a worker**.
- **Claude and Cursor remain never `rig use` targets.** Grok Bot.app is still not a parent or worker.
- **`rig parent sol|astra` stays Codex-only.** No profiles / statusline for these three.
- **Existing worker flags never flipped.** Worker spawn argv already shipped. Do not change `run-worker.sh` argv.
- Native cheap same-CLI when live in `{codex, grok, opencode, omp, pi}` for explore/mini/bulk and implement fallback. Empty MODELS for native on opencode/omp/pi.
- NATIVE roles: `explore` / `worker` / `bulk`. Do not ship agent toml. Do not call `run-worker.sh` to spawn the same CLI as a child of itself.
- MCP: write **user-global** files only. Do not write project `mcp.json` / `opencode.json`.
- Do not run interactive `opencode mcp add` / `omp /mcp` / `pi /mcp`.
- Launcher is existing `scripts/rig-mcp.sh` (absolute path).
- OpenCode 1.17: `"mcp": { "rig": { "type": "local", "command": [launcher], "enabled": true } }`. If config already has `mcp.servers`, write `mcp.servers.rig` instead.
- OMP: `~/.omp/mcp.json` `{ "mcpServers": { "rig": { "command": launcher } } }`.
- Pi: `~/.pi/agent/mcp.json` same mcpServers shape. Do **not** auto-install `pi-mcp-adapter`.
- bash 3.2: no ASCII apostrophe in the AGENTS heredoc.
- Never Sol/Astra/Fable as a child.
- If both OMP and Pi effective as workers of a *different* parent, pick OMP.

## Acceptance

- `rig use opencode|omp|pi|grok|codex` works; `rig use claude|cursor` exits 2.
- Existing harness `parent = "codex"` unchanged after `rig init`.
- OpenCode parent + Grok worker on → implement is Grok child.
- OpenCode parent + no other workers → implement/explore are native OpenCode (empty model).
- Grok parent + OpenCode worker on → still native Grok for implement if Claude off.
- Setup merges MCP into the three user configs; does not overwrite plugins or other servers; does not write repo `mcp.json`.
- Doctor lists OpenCode/OMP/Pi MCP. Pi without adapter prints the install hint.
- README/usage/skill/AGENTS: five possible parents; Claude/Cursor never.
- No Sol/Astra/Fable. No interactive MCP CLIs. No statusline for these three.

## Out of scope

- Claude or Cursor as parent.
- OpenCode/OMP/Pi Codex-style named agent toml / statusline / `rig parent` profiles.
- Auto-install `pi-mcp-adapter` into Pi `settings.json`.
- Auto-flip preferred parent or worker flags on existing harness.
- Live spawn or live parent session in this cook.
- Generic “any CLI” parent.
- Grok Bot.app.
