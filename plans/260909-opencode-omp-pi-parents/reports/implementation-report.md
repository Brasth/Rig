## Phase Implementation Report

### Executed Phase
- Phase: phase-01-identity + phase-02-mcp-doctor
- Plan: plans/260909-opencode-omp-pi-parents/
- Status: completed

### Files Modified
- bin/rig (use, setup skill links, doctor MCP/watch, AGENTS heredoc)
- scripts/route.py (NATIVE_PARENTS + NATIVE roles)
- scripts/install-ui.py (JSONC merge, OpenCode/OMP/Pi MCP)
- skills/delegate-harness/SKILL.md
- skills/rig-jobs/SKILL.md
- templates/harness.toml (comment parents; default still codex)
- README.md, docs/usage.md, AGENTS.md
- tests/test_cli.py, tests/test_route.py, tests/test_install_ui.py
- plans/260909-opencode-omp-pi-parents/*

### Tasks Completed
- [x] rig use accepts codex|grok|opencode|omp|pi; claude/cursor exit 2
- [x] pick native when live in {codex, grok, opencode, omp, pi}
- [x] AGENTS MUST: three can be parent; Claude/Cursor never; no apostrophe
- [x] setup merges user-global MCP JSON; skill links; no project mcp.json
- [x] doctor MCP rows + Pi adapter hint
- [x] python3 -m unittest discover -s tests

### Tests Status
- Type check: n/a (bash/python)
- Unit tests: pass (115)
- Integration tests: n/a

### Issues Encountered
JSONC trailing commas after comment strip required an extra strip pass.

### Next Steps
On device: `./install.sh`, then `rig init` in each project, then new OpenCode/OMP/Pi thread. Existing harness flags stay off until `rig workers …=on`. Pi `/rig` needs `pi install npm:pi-mcp-adapter`.
