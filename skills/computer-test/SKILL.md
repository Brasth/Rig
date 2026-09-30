---
name: computer-test
description: >
  Rig-only GUI testing, after an enabled Rig project, parent Rig MCP, and
  an opted-in available backend are confirmed. Generic GUI/computer-use
  requests do not select Rig; use host instructions when Rig is off or absent.
  Parent-only real GUI testing with Cua Driver: click the live UI
  (capture → act → confirm), pass/fail from AX text and screenshots, and
  record a session video (recording.mp4). Stay. Never spawn a clicker.
  Never Playwright as computer-use. Children never click.
user-invocable: true
---

# Computer test (parent only)

## Scope and opt-in first

Generic computer-use requests do not select Rig. First check that `.rig/harness.toml` exists and the project is enabled (`[project] enabled=false` disables Rig; an existing legacy harness without that section stays enabled), parent Rig MCP is available, and the selected backend is opted in and available. Global skill installation or a tool name alone is not opt-in. If Rig is uninitialized, disabled, or unavailable, or its backend is not opted in, use an available host-native computer/browser capability under its own instructions. Do not initialize, enable, install, unlock, or repair Rig merely because the user mentioned computer use. If the user explicitly requests Rig, explain the blocker and ask before setup instead of silently switching providers. Once a Rig backend is selected, preserve its permission, grant, freshness, child-isolation, and no-bypass rules; a denial is never a reason to switch tools. The Rig-specific routing and fallback rules below apply only after this selection gate.

Read the selection gate in `skills/computer-use/SKILL.md` before applying the Rig loop below.

Stay. Real GUI testing on **this computer** — a native app or a named Chrome profile — via Rig MCP `rig_cu_capture` / `rig_cu_act` / `rig_cu_confirm` / `rig_cu_record`. Same loop as `skills/computer-use/SKILL.md`. Do not spawn a clicker. Do not call `computer_use(...)`. Do not shell cua-driver. Children never click.

Driver must be effective (`[computer-use] enabled=true` + `cua-driver` on PATH). Else chrome-devtools only. Never Figma MCP or Playwright as the computer-use fallback.

Read `references/gui-test.md` before the first click. Record the run: `references/record-video.md`.

## When

- Click through a real UI and assert what appeared
- “Test this on the real Mac / Windows / Linux”
- Drive Calculator, Settings, a shipped desktop app
- Logged-in browser flow (named Chrome profile)
- Visual check of a local HTML page vs Figma
- Form fill + confirm the result is on screen
- Record a video of the test (clicks + result)

Not: unit tests, `pytest` without a GUI, or a worker Playwright suite. Those stay ordinary test jobs.

## Bar

A test **passes** only when confirm evidence shows the expected AX/text **and** the screenshot matches the expected window. “I clicked” is not a pass. Unverifiable effect is not a pass unless the screenshot clearly changed as expected.

A GUI test run also **records video**: `recording.mp4` under `.rig/cu-evidence/<run>/`. Clicks without that mp4 (or an honest leftover why video failed) are incomplete evidence.

## Click the UI

Every click is `rig_cu_act` on a **fresh** `element_token` or browser `ref` from the last capture. AX/ref first; px only after `degraded` / `escalate_px` on that snapshot. Confirm after each click. Do not fire a burst of clicks on a stale tree.

## Loop

1. Write the steps (click/type + expected) **before** clicking.
2. Start video: MCP `rig_cu_record` `action=start` with output under `.rig/cu-evidence/<run>/` (see `references/record-video.md`). Daemon must already be serving. Do not shell cua-driver.
3. Capture. Record `snapshot_id`. Fresh token/ref only.
4. Act (click/type/key). AX/ref first; px only after `degraded` / `escalate_px`.
5. Confirm. Read `effect` and the new PNG.
6. Mark the step pass/fail. Evidence under `.rig/cu-evidence` (not `/tmp`).
7. Recapture before the next act. Stale tokens do not click.
8. Stop video: MCP `rig_cu_record` `action=stop` even if a step failed. Report `recording.mp4`.

Named Chrome: `chrome-profile` then existing-profile bind. Grant is human. Isolated Driver Chrome is not the logged-in path.

## Hard no

- Passwords, 2FA, payment, OS permission dialogs
- `[workers].cua` / Playwright as CU / Hermes `computer_use`
- Shelling `cua-driver` for capture, act, confirm, or recording (use Rig MCP)
- Passing cua-driver, chrome-devtools, chrome-profile, or `rig_cu_*` to children
