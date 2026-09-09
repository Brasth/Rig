---
phase: 1
title: "Identity: rig use, pick, AGENTS, docs"
status: completed
priority: P1
effort: "4h"
dependencies: []
---

# Phase 1: Identity

`rig use`, pick-native, AGENTS MUST, docs. No MCP writes.

## File ownership

- `bin/rig` (`cmd_use`, usage, `cmd_init` AGENTS heredoc)
- `scripts/route.py`
- `skills/delegate-harness/SKILL.md`
- `skills/rig-jobs/SKILL.md`
- `templates/harness.toml`
- `README.md`
- `docs/usage.md`
- `tests/test_cli.py`
- `tests/test_route.py`
- `AGENTS.md` (MUST block matches init heredoc)

## Requirements

- `rig use` accepts `codex|grok|opencode|omp|pi`. Reject `claude` and `cursor` (exit 2).
- Usage / help: `rig use` list; Stay in Codex, Grok, OpenCode, OMP, or Pi.
- Template comments may list new parent names; `parent = "codex"` stays the default.
- `choose_worker`: `live in {codex, grok, opencode, omp, pi}` for native explore/mini/bulk, implement/hard fallback, and final native.
- NATIVE entries for those three: `explore` / `worker` / `bulk`. MODELS stay empty.
- AGENTS.md MUST: these three CAN be parent; Claude/Cursor cannot; `rig use` list includes them; never spawn last-resort just because PATH has them; OMP beats Pi as workers; omit --model unless RIG_MODEL; no ASCII apostrophe in the heredoc.
- Docs / skill: five possible parents; never parent Claude/Cursor; fully quit OpenCode/OMP/Pi once after setup; `/rig` works after MCP.

## Tests

- `rig use opencode` writes `parent = "opencode"`; `rig use claude` and `rig use cursor` exit 2.
- OpenCode live + Grok effective → implement is Grok child.
- OpenCode live + no other effective → implement is native OpenCode.
- OpenCode live + explore → native OpenCode, not Cursor/Codex.
- OMP live + Pi effective as worker → not Pi over native OMP; worker pick still omp-before-pi when both are children of a different parent.
- Grok live + those three effective → still native Grok for implement if Claude off.
- Existing `same_cli_beats` tests stay.

## Done when

`rig use` + pick tables + docs/AGENTS match. No MCP writes in this phase (phase 2).
