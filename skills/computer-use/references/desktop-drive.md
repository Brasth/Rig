# Drive a desktop app (parent)

Stay. Native macOS/Windows/Linux apps through Cua Driver — Calculator, Settings, your shipped `.app`. Same loop as `../SKILL.md`. Real pass/fail: `skills/computer-test/SKILL.md`.

## Bind

1. `rig_cu_capture` with `bundle_id` or `app_name` (Driver `launch_app`). Do not `open` the app (foreground).
2. Keep `windows[].window_id` from launch (prefer on-screen). `window_id` 0 is an empty tree — refuse.
3. If the later snapshot drops the id, `list_windows(pid)` and recapture.

AX first. Px only after `degraded` / `escalate_px` on **that** snapshot. Canvas/games may be px; chrome of the app is often AX.

## Useful loops

- **Smoke a calculator-style UI:** click digits/operators, confirm the result string in AX.
- **Open a document window:** launch, wait for a titled window, confirm the title.
- **Menu path:** native menu via Driver when AX exposes it; recapture after.
- **Evidence pack:** each confirm PNG + `brief_block` for a worker (they do not click).

Screenshots: `.rig/cu-evidence`. Children never receive cua-driver.

## Hard no

Passwords, 2FA, payment, OS permission sheets. Do not kill unrelated apps.
