# Rig

[![M8ven Score](https://m8ven.ai/badge/mcp/brasth/rig)](https://m8ven.ai/mcp/brasth/rig)

Stop babysitting coding agents.

Rig is a CLI harness. You talk to a parent agent; it scopes work, briefs workers over MCP, then verifies results. Done means accepted checks, not exit 0 vibes.

**New: Adaptive workflows** (default). The parent can decompose eligible work into a DAG of disjoint workers, own the graph, briefs, and acceptance, and only mark verified after parent checks. Children never spawn children. See [Adaptive workflows](docs/usage.md#adaptive-workflows).

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

- [Overall flow](docs/rig-flow.md)
- [Install](docs/usage.md#install) · [Per project](docs/usage.md#per-project-setup) · [Configure](docs/usage.md#configure-agents) · [BrowserSkill](#browserskill)
- [Smart routing](docs/smart-routing.md)
- [Adaptive workflows](docs/usage.md#adaptive-workflows)
- [Everyday prompts](docs/usage.md#how-your-prompt-is-handled) · [Queue](docs/usage.md#how-the-queue-works)
- [Terminal companion](docs/usage.md#optional-terminal-companion) · [Watch](docs/usage.md#watch-jobs-memory)
- [Verification](docs/usage.md#protected-writes-and-parent-acceptance) · [Cancellation](docs/rig-flow.md#closing-cancelling-and-stopping)
- [Troubleshooting](docs/usage.md#troubleshooting) · [Docs](docs/usage.md)

## BrowserSkill

Install and `rig update` **ask** to install BrowserSkill for parent logged-in browser (default **No**). Piped install with no TTY skips unless `RIG_INSTALL_BROWSER_SKILL=1`. Decline is remembered. Missing BrowserSkill does not fail Rig. Skip this run with `RIG_SKIP_BROWSER_SKILL=1`. The human installs the Chrome/Edge extension. Never run `bsk install-skill`. Children never receive `bsk` or `rig_bsk_*`.

Parent-only BrowserSkill uses Rig MCP `rig_bsk_status` / `rig_bsk_session` / `rig_bsk_observe` / `rig_bsk_act` / `rig_bsk_confirm` (`bsk session start --json`, optional `--no-focus`; retain `session_id`; `--session` on every scoped command; `session stop` with positional ID; observe → one click/fill/press; `rig_bsk_navigate` plus explicit tab list/borrow/return) when `[browser-skill] enabled=true`, machine opt-in, `bsk` on PATH, and the extension is connected. nonempty `status.browsers` is connected. Website + real cookies → BSK. Native / canvas px → Driver. Neither effective → chrome-devtools. One backend per turn.

```bash
rig browser-skill              # machine + this-repo + extension + effective
rig browser-skill setup        # install/upgrade bsk CLI; reprints store URLs; does not enable the repo flag
rig browser-skill on           # this repo [browser-skill] enabled=true
rig browser-skill off          # this repo enabled=false; does not uninstall the binary
rig browser-skill doctor       # also folded into rig doctor
```

`rig setup --browser-skill` / `--no-browser-skill` forwards to the BrowserSkill installer. `rig init` backfills `[browser-skill] enabled = false`. Details: [Usage — install](docs/usage.md#install).
