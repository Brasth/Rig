# Rig

[![M8ven Score](https://m8ven.ai/badge/mcp/brasth/rig)](https://m8ven.ai/mcp/brasth/rig)

Stop babysitting AI agents. Rig is a task harness: you chat with a **parent agent**; it briefs **worker agents** over MCP, then verifies results before merge.

[![Watch the Rig demo](https://img.youtube.com/vi/KuhHMH--oGk/maxresdefault.jpg)](https://youtu.be/KuhHMH--oGk)

## Setup & use

**Prerequisites:** Install and sign in to your preferred agents (Codex, Grok, Claude Code, Cursor, OpenCode, OMP, Pi, or agy). Example: use Codex as parent and Claude as worker.

```bash
curl -fsSL https://raw.githubusercontent.com/Brasth/Rig/main/install.sh | bash
cd your-repo
rig init
rig workers claude=on
rig doctor
```

Then open your parent agent with Rig MCP in the repo (new thread needed) and type: `Fix the failing tests in tests/test_cli.py`. Enabling a worker does not install its CLI; it just permits Rig to spawn that agent if available. Open a new parent thread for Rig MCP to load.

## Everyday commands

| Command | Purpose |
| --- | --- |
| `rig status` | Show parent and available workers |
| `rig jobs` | List running and recent tasks |
| `rig tui` | Open task board and queue manager |
| `rig workers claude=on` / `rig workers claude=off` | Allow/disallow a worker |
| `rig off` / `rig on` | Disable/enable Rig for this repo (new parent thread needed) |
| `rig update` | Update to latest version |

## Lifecycle

`rig off` disables Rig for this repository, preserving `.rig` history, queue, memory and settings; it refuses while work or a workflow still holds ownership. `rig on` restores the managed instructions (it does not resume stopped jobs). Both need a new parent thread. See [repository lifecycle](docs/usage.md).

## Learn more

- [How Rig works](docs/rig-flow.md) — parent, workers, queue overview
- [Usage guide](docs/usage.md) — install, setup, daily workflows
- [Task preparation](docs/task-preparation.md) — structure work before delegating
- [Smart routing](docs/smart-routing.md) — how Rig picks the right worker (parent uses MCP `rig_job_launch` / `rig_job_wait` with selected worker/model)
- [Safe updates](docs/safe-updates.md) — pinning, rollback, and recovery

## Success vs. verification

Execution **ok** = worker finished. **Verified** = parent accepted result (ran checks, confirmed behavior). Only verified tasks count as done.

Press Esc/Stop to cancel: After explicit cancellation, do not re-wait, re-pick, or drain automatically. If a wait loses transport, inspect once with `rig job wait ID --timeout 0`. `stop-unconfirmed` and `native-cancel-required` keep ownership until stop is confirmed. See [cancellation details](docs/usage.md#scenarios).

<details>
<summary>Advanced behavior: adaptive workflows, computer-use, BrowserSkill, acceptance contracts</summary>

**Adaptive workflows:** `[orchestration] mode = "adaptive"` (default) decomposes eligible work into a DAG of at most `max_nodes` stages. Writers file AND resource disjoint; children never spawn children. Workflow files under `.rig/workflows/<id>/` with `owner-credentials.json` (mode 0600). Set `mode = "single"` to restore one-child. Parent uses `rig_workflow_advance` / `rig_workflow_wait`. No estimated progress, savings, or ETA. Independent review unavailable stays explicit. [Details](docs/usage.md#adaptive-workflows).

**Desktop and browser access:** Generic computer-use requests do not select Rig. First check that `.rig/harness.toml` exists and the project is enabled (`[project] enabled=false` disables Rig), parent Rig MCP is available, and the selected backend (`[computer-use] enabled` or `[browser-skill] enabled`) is opted in. Global skill installation or a tool name alone is not opt-in. Otherwise use an available host-native computer/browser capability. Do not initialize, enable, install, unlock, or repair Rig merely because the user mentioned computer use. If the user explicitly requests Rig, explain the blocker and ask before setup. Once a backend is selected, a denial is never a reason to switch tools. Computer-use: MCP `rig_cu_capture` / `rig_cu_act` (AX token first; px after `escalate_px`) / `rig_cu_confirm` / `rig_cu_record`; Chrome profile via parent `chrome-profile` then Driver `existing-profile` bind (human grant via `cua-driver serve --grant existing-profile`). BrowserSkill: `rig_bsk_status` / `rig_bsk_session` / `rig_bsk_observe` / `rig_bsk_act` / `rig_bsk_confirm` (`bsk session start --json`; retain `session_id`; nonempty `status.browsers` is connected). Never run `bsk install-skill`. Children never receive `bsk` or `rig_bsk_*`. Children never receive cua-driver or chrome-devtools MCP. Never Figma MCP or Playwright as computer-use fallback. Never the Hermes `computer_use` skill. [Setup](docs/usage.md#computer-use-parent).

**Acceptance contracts and workflow recipes:** Declare checks and criteria upfront. [Contracts](docs/acceptance-contracts.md). [Recipes](docs/workflow-recipes.md): templates for bugfix, research→implement, UI validation.

</details>
