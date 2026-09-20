# GUI test HOW (parent)

Stay. This is the HOW for `../SKILL.md`. Capture / act / confirm and the AX-then-px ladder stay in `skills/computer-use/SKILL.md`. Desktop recipe: `skills/computer-use/references/desktop-drive.md`. Logged-in Chrome: `skills/computer-use/references/logged-in-browser.md`. Video: `record-video.md`.

## Write the plan first

One row per step. Empty expected = not a test.

| # | window | click / type | expected (AX or visible text) | evidence png |
| --- | --- | --- | --- | --- |
| 1 | Calculator | click 1, +, 2, = | result `3` | `.rig/cu-evidence/calc-eq.png` |

Do not improvise extra clicks. Destructive/prod/secrets → stop and ask.

## Run

0. Start video (`rig_cu_record` action=start; `record-video.md`) into `.rig/cu-evidence/<run>/`.
1. Capture the **on-screen** window (keep `window_id` from `launch_app` / `list_windows`; never `0`).
2. **Click** (or type) via `rig_cu_act` on a **fresh** `element_token` or browser `ref`.
3. Confirm. Pass only if expected text is in the tree **or** the screenshot clearly shows it.
4. Fail the step if `effect` is refused/unverifiable **and** the PNG did not change.
5. Recapture. Next click.
6. Stop video (`rig_cu_record` action=stop). Keep `recording.mp4`.

Logged-in site: `profile_key` + url. Evidence PNGs and mp4 under `.rig/cu-evidence`.

## Report

- pass/fail per step
- Paths to before/after PNGs
- Path to `recording.mp4`
- Honest leftover (overlay window, grant missing, canvas needs px, video unavailable)

Paste `brief_block` into a worker brief if they must fix code. They still must not click.
