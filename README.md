# Rig

[![M8ven Score](https://m8ven.ai/badge/mcp/brasth/rig)](https://m8ven.ai/mcp/brasth/rig)

Stop babysitting coding agents.

Rig is a CLI harness. You talk to a parent agent; it scopes work, briefs workers over MCP, then verifies results. Done means accepted checks, not exit 0 vibes.

**New: Adaptive workflows** (default). The parent can decompose eligible work into a DAG of disjoint workers, own the graph, briefs, and acceptance, and only mark verified after parent checks. Children never spawn children. See [Adaptive workflows](#adaptive-workflows) and [How an adaptive workflow moves](#how-an-adaptive-workflow-moves).

[![Watch the Rig demo](https://img.youtube.com/vi/KuhHMH--oGk/maxresdefault.jpg)](https://youtu.be/KuhHMH--oGk)

Failing tests → Codex parent → Rig TUI → Grok worker over MCP → parent verifies → green.

- Parent scopes files (no dumping the whole chat as the child prompt)
- Worker runs from a brief over MCP (adaptive: parallel disjoint nodes under parent control)
- Parent verifies before you merge

## Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/Brasth/Rig/main/install.sh | bash
cd your-repo
rig init
```

Configure the preferred parent and workers in `.rig/harness.toml` (preferred). Then open a parent CLI and type a normal prompt, for example `fix the failing tests in tests/test_cli.py`. Do not use `rig run` for normal work.

**Parents:** Intended: Codex on Astra. Also supported: Grok, OpenCode, OMP, Pi, or agy (open that CLI). **Never the parent:** Claude Code, Cursor, and Devin. **Effective workers:** Grok, Claude, OpenCode, OMP, Pi, agy, Codex, and opt-in Devin (SWE-2 only). Cursor integration is disabled pending scoped MCP. Missing worker binary → that worker is off. If no eligible worker exists, parent fallback preserves its actual model. Never spawn Astra, Sol, or Fable as a child.

**Try the demo:** [failing tests through parent verify](https://youtu.be/KuhHMH--oGk).

## Navigation

- [Overall flow](#overall-flow)
- [Install](#install) · [Per project](#per-project) · [Configure](#configure) · [FAQ](#faq)
- [Smart routing](#smart-routing)
- [Adaptive workflows](#adaptive-workflows) · [How an adaptive workflow moves](#how-an-adaptive-workflow-moves)
- [Everyday prompts and queue](#everyday-prompts-and-queue)
- [Optional terminal companion](#optional-terminal-companion) · [Watch](#watch)
- [Verification and cancellation](#verification-and-cancellation)
- [Troubleshooting](#troubleshooting) · [Docs](#docs)
