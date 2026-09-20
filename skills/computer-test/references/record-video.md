# Record a GUI test video (parent)

Stay. Use Rig MCP **`rig_cu_record`**, not `cua-driver recording` and not `cua-driver start_recording`. Clicks still use `rig_cu_capture` / `rig_cu_act` / `rig_cu_confirm`. Driver writes **`recording.mp4`** (H.264, 30 fps) plus per-click turn folders.

Do not shell cua-driver for start/stop. Children never receive `rig_cu_*` or cua-driver. Do not record passwords, 2FA, or payment.

## When

Computer-test runs **record video by default**. Also when the user says record / screen recording / mp4. Opt out only if they say screenshots only (`record_video: false`).

## Start / stop

Daemon must already be up (human: `cua-driver serve`; existing-profile grant if Chrome). Output stays in the repo. Parent never shells cua-driver for recording:

1. MCP `rig_cu_record` `action=start` with `output_dir` under `.rig/cu-evidence/<run>/` (omit to get `gui-test-<stamp>`). Rig passes `record_video: true` unless the user opts out — Driver’s raw tool default is off.
2. capture → act → confirm
3. MCP `rig_cu_record` `action=stop` even on fail/cancel so the mp4 finalizes.

Not `/tmp`, not `~/cua-trajectories`, not Desktop. Paths outside `.rig/cu-evidence` are refused.

macOS 15+: ScreenCaptureKit via the daemon (needs Screen Recording TCC). Windows/Linux: ffmpeg on PATH; if missing, turns still write PNG/JSON and the hint/`last_error` explains — say so, do not fake an mp4.

Always `rig_cu_record` stop on fail/cancel so the mp4 finalizes.

## What you get

- `recording.mp4` — the click-through
- `turn-NNNNN/` — before/after PNG, `click.png` marker, `action.json`

Pass/fail still comes from confirm AX + PNG. The video is the watchable proof. Put the mp4 path in `brief_block`; workers do not click or start recording.

If Driver recording is unavailable, stitch confirm PNGs with ffmpeg into `.rig/cu-evidence/<run>/recording.mp4` and mark `source=png-stitch`.
