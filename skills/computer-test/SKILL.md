---
name: computer-test
description: >
  Parent-only real GUI testing with Cua Driver: click the live UI
  (capture → act → confirm), pass/fail from AX text and screenshots, and
  record a session video (recording.mp4). Stay. Never spawn a clicker.
  Never Playwright as computer-use. Children never click.
user-invocable: true
---

# Computer test (parent only)

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
