#!/usr/bin/env bash
# stdio MCP launcher. Codex/Grok must get one executable, not `python3` + args.
set -euo pipefail
export PYTHONUNBUFFERED=1
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [[ -x "$c" ]]; then
    PY="$c"
    break
  fi
done
if [[ -z "$PY" ]]; then
  PY="$(command -v python3)"
fi
exec "$PY" -u "$HERE/rig_mcp.py"
