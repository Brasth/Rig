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

**Parents:** Intended: Codex on Astra. Also supported: Grok, OpenCode, OMP, Pi, or agy (open that CLI). **Never the parent:** Claude Code, Cursor, Devin, and MiMo. **Effective workers:** Grok, Claude, OpenCode, OMP, Pi, agy, Codex, opt-in Devin (SWE-2 only), and opt-in MiMo Code. Cursor integration is disabled pending scoped MCP. Missing worker binary → that worker is off. Smart routing scores eligible task fit by default; Jev can be enabled per project with a global Keychain key. If no eligible worker exists, parent fallback preserves its actual model. Never spawn Astra, Sol, or Fable as a child.

**Try the demo:** [failing tests through parent verify](https://youtu.be/KuhHMH--oGk).

## Navigation

- [Overall flow](docs/rig-flow.md)
- [Install](docs/usage.md#install) · [Per project](docs/usage.md#per-project-setup) · [Configure](docs/usage.md#configure-agents) · [Computer-use](#computer-use) · [BrowserSkill](#browserskill)
- [Smart routing](docs/smart-routing.md)
- [Adaptive workflows](#adaptive-workflows)
- [Everyday prompts](docs/usage.md#how-your-prompt-is-handled) · [Queue](docs/usage.md#how-the-queue-works)
- [Terminal companion](docs/usage.md#optional-terminal-companion) · [Watch](docs/usage.md#watch-jobs-memory)
- [Verification](#verification-and-cancellation) · [Cancellation](docs/rig-flow.md#closing-cancelling-and-stopping)
- [Troubleshooting](docs/usage.md#troubleshooting) · [Docs](docs/usage.md)

## Computer-use

Install and `rig update` **ask** to install Cua Driver for parent computer-use (default **No**). Piped install with no TTY skips unless `RIG_INSTALL_CUA_DRIVER=1`. Decline is remembered. Missing Driver does not fail Rig. Skip this run with `RIG_SKIP_CUA_DRIVER=1`. Per project: `[computer-use] enabled` in `.rig/harness.toml` (default false). `rig computer-use setup` installs/upgrades the binary; parents use Rig MCP. Vendor skill installation is not required. Effective on requires machine opt-in **and** the binary **and** the repo flag.

Every legal parent then uses Rig MCP `rig_cu_capture` / `rig_cu_act` / `rig_cu_confirm` / `rig_cu_record`. Capture → one act on a fresh 30s snapshot → mandatory confirm. AX token first; px only after `degraded` / `escalate_px` on that snapshot. Named Chrome profile: parent `chrome-profile` open, then Driver existing-profile bind. Isolated profile is not the Figma path. Existing-profile grant is human (`cua-driver serve --grant existing-profile`); Rig never silent-grants. Fallback is chrome-devtools only. Never Figma MCP or Playwright as computer-use fallback. Figma MCP remains parent file/node. Never the Hermes `computer_use` skill. Children never receive cua-driver or chrome-devtools MCP. Children never receive chrome-profile or `rig_cu_*`. Do not spawn a clicker.

```bash
rig computer-use              # machine + this-repo + MCP + effective
rig computer-use setup        # install/upgrade binary; parents use Rig MCP
rig computer-use on           # this repo [computer-use] enabled=true
rig computer-use off          # this repo enabled=false; does not uninstall the binary
rig computer-use doctor       # also folded into rig doctor
```

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

## Adaptive workflows

When `[orchestration] mode = "adaptive"` (default), the parent decomposes eligible work into a DAG of at most `max_nodes` (default 12) and owns the graph, briefs, and acceptance. `single` keeps one-job behavior. Queue and worker caps remain authoritative. Writers must be file AND resource disjoint. Children never spawn children. Durable files live under `.rig/workflows/<id>/` with `owner-credentials.json` (mode 0600). The parent uses `rig_workflow_advance` / `rig_workflow_wait`. Independent review unavailable stays explicit. In `rig tui`, Tab Jobs/Queue/Workflows. No estimated progress, savings, or ETA.

Parent orchestration is MCP (`rig_session`, `rig_job_launch`, `rig_workflow_create` / `rig_workflow_advance` / `rig_workflow_wait`, allow/deny, requirements/check/accept). Shell `run-worker.sh` is human/internal fallback. Children never spawn or message children.

## Verification and cancellation

Execution `ok` means the worker exited successfully. **Verified** means the parent accepted the current scoped content against declared requirements.

Esc / Stop on **this wait** records durable cancellation for attached attempts and returns promptly. After explicit cancellation, do not re-wait, re-pick, or drain automatically.

`stop-unconfirmed` and `native-cancel-required` mean execution is not confirmed stopped. Unconfirmed work keeps its slot and files. Transport failure alone preserves workers: one bounded `rig job wait ID --timeout 0`, then inspect or reconcile.
