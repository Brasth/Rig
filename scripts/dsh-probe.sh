#!/usr/bin/env bash
# dsh-probe.sh: the facts Rig needs before DeepSeek Harness (dsh) is trusted as a worker.
# Step 0 of docs/journals/261026-deepseek-harness-worker-plan.md. Run it once on a machine that has dsh and a key:
#
#   export DEEPSEEK_API_KEY=...     # yours; it is never printed or written to the report
#   bash scripts/dsh-probe.sh       # a few short prompts: it costs a few tokens
#
# It works in a throwaway folder, changes nothing else, and writes dsh-probe-report.txt in the current directory. Paste
# that file back. Each check says PASS, FAIL or UNKNOWN and shows the real output, so nothing is guessed from a README.
set -u

REPORT="$PWD/dsh-probe-report.txt"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
DSH="${DSH_BIN:-dsh}"
: > "$REPORT"

say() { printf '%s\n' "$*" | tee -a "$REPORT"; }
section() { say ""; say "=== $* ==="; }
# run CMD... with a time limit (macOS has no `timeout`); prints output, sets RC (124 = timed out)
limited() {
  local secs="$1"; shift
  "$@" >"$WORK/out" 2>"$WORK/err" &
  local pid=$!
  ( sleep "$secs"; kill -TERM "$pid" 2>/dev/null; sleep 2; kill -KILL "$pid" 2>/dev/null ) &
  local watcher=$!
  wait "$pid" 2>/dev/null; RC=$?
  kill "$watcher" 2>/dev/null; wait "$watcher" 2>/dev/null
  [[ "$RC" -eq 143 || "$RC" -eq 137 ]] && RC=124
  return 0
}
show() { say "exit=$RC"; say "--- stdout"; head -c 2000 "$WORK/out" | tee -a "$REPORT"; say ""; say "--- stderr (first 600 bytes)"; head -c 600 "$WORK/err" | sed -E 's/(sk-)[A-Za-z0-9_-]+/\1***/g' | tee -a "$REPORT"; say ""; }

section "environment"
say "date: $(date -u +%FT%TZ)  os: $(uname -sm)"
say "node: $(node --version 2>&1)   pnpm: $(pnpm --version 2>&1 | head -1)   python3: $(python3 --version 2>&1)"
say "DEEPSEEK_API_KEY set: $([[ -n "${DEEPSEEK_API_KEY:-}" ]] && echo yes || echo NO)   DSH_HOME: ${DSH_HOME:-<default ~/.dsh>}"
if ! command -v "$DSH" >/dev/null 2>&1; then
  say "dsh not found. Install it (npm install -g @deepseek-ai/dsh) or set DSH_BIN, then run again."
  exit 1
fi
say "dsh: $(command -v "$DSH")"
limited 30 "$DSH" --version; say "dsh --version: exit=$RC $(cat "$WORK/out" "$WORK/err" | head -3 | tr '\n' ' ')"
export DSH_TELEMETRY_MODE=DISABLED
cd "$WORK" || exit 1
git init -q . 2>/dev/null; echo "hello" > note.txt

section "1. headless: answer on stdout, exit code"
limited 180 "$DSH" --profile headless "Reply with exactly the word PONG and nothing else."
show
[[ "$RC" -eq 0 ]] && say "RESULT 1: PASS exit 0" || say "RESULT 1: FAIL exit $RC"
grep -q PONG "$WORK/out" && say "RESULT 1b: PASS answer reached stdout" || say "RESULT 1b: FAIL answer not on stdout"

section "2. headless: does it ask for approval (write a file, run a shell command)?"
for mode in "" workspace-write; do
  say "-- DSH_PERMISSION_MODE=${mode:-<unset>}"
  rm -f "$WORK/made.txt"
  if [[ -n "$mode" ]]; then export DSH_PERMISSION_MODE="$mode"; else unset DSH_PERMISSION_MODE; fi
  limited 240 "$DSH" --profile headless "Create a file named made.txt in the current directory containing the single line: probe. Then run the shell command 'echo shell-ok' and tell me its output."
  show
  [[ "$RC" -eq 124 ]] && say "RESULT 2 ($mode): FAIL timed out: likely waiting on an approval with no terminal"
  [[ -f "$WORK/made.txt" ]] && say "RESULT 2 ($mode): PASS file written: $(cat "$WORK/made.txt")" || say "RESULT 2 ($mode): FAIL no file written"
  grep -q "shell-ok" "$WORK/out" && say "RESULT 2 ($mode): PASS shell command ran" || say "RESULT 2 ($mode): UNKNOWN shell output not in the answer"
done
unset DSH_PERMISSION_MODE

section "3. config tree (which rows can a patch target?)"
limited 60 "$DSH" --profile headless --dump-config
say "exit=$RC; saved the first 4000 bytes below"; head -c 4000 "$WORK/out" | tee -a "$REPORT"; say ""
limited 60 "$DSH" --dump-config-schema; say "schema bytes: $(wc -c < "$WORK/out")"

section "4. MCP through a per-run --patch (the path Rig's wrapper uses)"
# a tiny stdio MCP server that exposes one tool, rig_probe_ping, and records that it was called
cat > "$WORK/mcp_probe.py" <<'PY'
import json, sys
def send(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    try: m = json.loads(line)
    except ValueError: continue
    i, method = m.get("id"), m.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "rig-probe", "version": "1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": i, "result": {"tools": [{"name": "rig_probe_ping", "description": "Call this to prove MCP works. Returns the word MCP-OK.", "inputSchema": {"type": "object", "properties": {}}}]}})
    elif method == "tools/call":
        open(sys.argv[1], "a").write("called\n")
        send({"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": "MCP-OK"}]}})
PY
cat > "$WORK/probe.patch.yml" <<PATCH
[{"insert": {"id": "rig-mcp-probe", "name": "@deepseek-ai/dsh-mcp-client",
  "config": {"serverName": "probe", "transport": "stdio", "command": "python3", "args": ["$WORK/mcp_probe.py", "$WORK/mcp_called.txt"]}}}]
PATCH
limited 240 "$DSH" --profile headless --patch "$WORK/probe.patch.yml" "Call the tool rig_probe_ping (it may be named mcp__probe__rig_probe_ping) and tell me exactly what it returned."
show
[[ -f "$WORK/mcp_called.txt" ]] && say "RESULT 4: PASS the MCP tool was called through the patch" || say "RESULT 4: FAIL the MCP tool was not called (patch shape wrong, plugin missing, or --patch not applied)"
say "If FAIL: the stderr above and the --dump-config output in section 3 show the right row ids; send both."

section "5. model: how is it chosen?"
limited 60 "$DSH" --help; say "--help (first 3000 bytes):"; head -c 3000 "$WORK/out" | tee -a "$REPORT"; say ""
say "profiles on disk:"; ls -1 "${DSH_HOME:-$HOME/.dsh}/profiles" 2>&1 | tee -a "$REPORT"

section "6. a long brief as one argument"
LONG="$(python3 -c 'print("Ignore the padding and reply with exactly the word LONG-OK. " + "padding " * 12000)')"
limited 240 "$DSH" --profile headless "$LONG"
say "argument bytes: ${#LONG}"; show
grep -q "LONG-OK" "$WORK/out" && say "RESULT 6: PASS" || say "RESULT 6: FAIL or UNKNOWN (an argument-too-long error would show above)"

section "7. telemetry switch"
say "DSH_TELEMETRY_MODE=DISABLED was set for every run above. Whether anything was uploaded cannot be seen from here;"
say "check the dsh docs/settings for the session-log upload option and turn it off for Rig jobs."

section "done"
say "Report written to $REPORT (no secrets in it: DEEPSEEK_API_KEY is never printed). Paste it back."
