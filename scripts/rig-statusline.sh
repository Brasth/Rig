#!/usr/bin/env bash
# Grok TUI status line: cwd/model plus which Rig worker is running and what it is doing.
set -euo pipefail
# Only a parent launched inside the Rig shell UI suppresses its duplicate HUD.
[[ "${RIG_UI_ACTIVE:-}" == "1" ]] && exit 0
JOBS="${RIG_HOME:-$HOME/.rig}/scripts/jobs.py"
if [[ ! -f "$JOBS" ]]; then
  HERE="$(cd "$(dirname "$0")" && pwd)"
  JOBS="$HERE/jobs.py"
fi
exec python3 "$JOBS" hud
