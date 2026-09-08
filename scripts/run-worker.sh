#!/usr/bin/env bash
# run-worker.sh <grok|codex|claude> <job-id> <brief-file>
set -euo pipefail

_DETECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/detect-binaries.sh"
# shellcheck source=detect-binaries.sh
source "$_DETECT"

usage() {
  echo "usage: run-worker.sh <grok|codex|claude> <job-id> <brief-file>" >&2
  exit 2
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage
[[ $# -eq 3 ]] || usage

WORKER="$1"
JOB_ID="$2"
BRIEF_IN="$3"
ROLE="${RIG_ROLE:-worker}"
TIMEOUT_SECS="${RIG_TIMEOUT:-1200}"

case "$WORKER" in
  grok|codex|claude) ;;
  *)
    echo "run-worker: unknown worker '$WORKER'" >&2
    exit 2
    ;;
esac

if [[ ! "$JOB_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "run-worker: invalid job id '$JOB_ID'" >&2
  exit 2
fi

if [[ ! -f "$BRIEF_IN" ]]; then
  echo "run-worker: brief file not found: $BRIEF_IN" >&2
  exit 2
fi

REPO="$(repo_root)"
HARNESS="$(harness_path)"
if [[ ! -f "$HARNESS" ]]; then
  echo "run-worker: missing $HARNESS — run: rig init" >&2
  exit 1
fi
parse_harness "$HARNESS"

JOB_DIR="$REPO/.rig/jobs/$JOB_ID"
mkdir -p "$JOB_DIR"
BRIEF="$JOB_DIR/brief.md"

# Copy via a temp file. Never `{ cat f; } > f` — that truncates then
# concatenates forever (rig run writes this same path).
_BRIEF_SRC="$(mktemp "$JOB_DIR/brief.src.XXXXXX")"
cp "$BRIEF_IN" "$_BRIEF_SRC"
{
  if ! command grep -q "You are a worker, not the orchestrator" "$_BRIEF_SRC" 2>/dev/null; then
    printf '%s\n\n' "$WORKER_PREAMBLE"
  fi
  cat "$_BRIEF_SRC"
} > "$BRIEF"
rm -f "$_BRIEF_SRC"

LIVE="$(live_parent)"
FLAG="$(worker_flag "$WORKER")"
BIN="$(find_bin "$WORKER")"
STARTED="$(iso_now)"

write_json() {
  local status="$1" exit_code="$2" summary="$3" ended="$4"
  RESULT_OUT="$JOB_DIR/result.json"
  RESULT_META="$JOB_DIR/meta.json"
  RESULT_STATE="$(repo_root)/.rig/STATE.md"
  RESULT_JOB_ID="$JOB_ID" \
  RESULT_WORKER="$WORKER" \
  RESULT_ROLE="$ROLE" \
  RESULT_STATUS="$status" \
  RESULT_EXIT="$exit_code" \
  RESULT_STARTED="$STARTED" \
  RESULT_ENDED="$ended" \
  RESULT_SUMMARY="$summary" \
  RESULT_FILES="${RESULT_FILES:-}" \
  RESULT_NEXT="${RESULT_NEXT:-}" \
  RESULT_OUT="$RESULT_OUT" \
  RESULT_META="$RESULT_META" \
  RESULT_REPO="$REPO" \
  RESULT_BIN="$BIN" \
  RESULT_STATE="$RESULT_STATE" \
  python3 - <<'PY'
import json, os, pathlib
files = [f for f in os.environ.get("RESULT_FILES", "").split("\n") if f]
obj = {
    "job_id": os.environ["RESULT_JOB_ID"],
    "worker": os.environ["RESULT_WORKER"],
    "role": os.environ["RESULT_ROLE"],
    "status": os.environ["RESULT_STATUS"],
    "exit_code": int(os.environ["RESULT_EXIT"]),
    "started_at": os.environ["RESULT_STARTED"],
    "ended_at": os.environ["RESULT_ENDED"],
    "summary": os.environ["RESULT_SUMMARY"],
    "files_changed": files,
    "next": os.environ.get("RESULT_NEXT", ""),
}
path = pathlib.Path(os.environ["RESULT_OUT"])
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(obj, indent=2) + "\n")
tmp.replace(path)
meta = dict(obj)
meta["repo"] = os.environ.get("RESULT_REPO", "")
meta["bin"] = os.environ.get("RESULT_BIN", "")
mpath = pathlib.Path(os.environ["RESULT_META"])
mtmp = mpath.with_name(mpath.name + ".tmp")
mtmp.write_text(json.dumps(meta, indent=2) + "\n")
mtmp.replace(mpath)
state = pathlib.Path(os.environ["RESULT_STATE"])
state.parent.mkdir(parents=True, exist_ok=True)
state.write_text(
    "# STATE\n\nOverwritten each run.\n\n"
    f"- last_job: {obj['job_id']}\n"
    f"- worker: {obj['worker']}\n"
    f"- status: {obj['status']}\n"
    f"- summary: {obj['summary']}\n"
)
PY
}

write_meta() {
  local status="$1"
  META_OUT="$JOB_DIR/meta.json"
  python3 - "$META_OUT" "$JOB_ID" "$WORKER" "$ROLE" "$status" "$STARTED" "$REPO" "$BIN" "${CHILD:-}" "${SESSION_ID:-}" "$JOB_DIR" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
obj = {
    "job_id": sys.argv[2],
    "worker": sys.argv[3],
    "role": sys.argv[4],
    "status": sys.argv[5],
    "started_at": sys.argv[6],
    "repo": sys.argv[7],
    "bin": sys.argv[8],
}
pid, session_id, job_dir = sys.argv[9], sys.argv[10], sys.argv[11]
if pid:
    obj["pid"] = int(pid)
if session_id:
    obj["session_id"] = session_id
    obj["open"] = f"grok -r {session_id}"
obj["watch"] = f"tail -f {job_dir}/stdout.log"
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(obj, indent=2) + "\n")
tmp.replace(path)
PY
}

write_watch() {
  local watch="$JOB_DIR/WATCH.md"
  {
    echo "# Rig job $JOB_ID"
    echo
    echo "The child is headless. Codex cannot show its TUI."
    echo
    echo "- worker: \`$WORKER\`"
    echo "- status: running"
    [[ -n "${CHILD:-}" ]] && echo "- pid: \`$CHILD\`"
    if [[ -n "${SESSION_ID:-}" ]]; then
      echo "- session: \`$SESSION_ID\`"
      echo "- open Grok: \`cd $REPO && grok -r $SESSION_ID\`"
      echo "- dashboard: \`grok dashboard\`"
    fi
    echo "- live log: \`rig job log $JOB_ID -f\`"
    echo "- board: \`rig tui\`  or  \`rig jobs\`"
    echo "- status: \`rig status\`"
  } > "$watch"
}

shell_join() {
  local out="" a
  for a in "$@"; do
    out+="$(printf '%q ' "$a")"
  done
  printf '%s\n' "${out% }"
}

BRIEF_TEXT="$(cat "$BRIEF")"
SESSION_ID=""
CMD=()
case "$WORKER" in
  grok)
    SESSION_ID="$(uuidgen | tr 'A-Z' 'a-z')"
    CMD=(grok --no-auto-update --prompt-file "$BRIEF" --cwd "$REPO" --output-format streaming-json --session-id "$SESSION_ID" --always-approve --max-turns 40)
    ;;
  codex)
    CMD=(codex exec --ephemeral -s workspace-write -C "$REPO" -c 'model="gpt-5.6-terra"' "$BRIEF_TEXT")
    ;;
  claude)
    CMD=(claude -p "$BRIEF_TEXT" --model haiku --output-format json --permission-mode acceptEdits --allowedTools "Read,Grep,Glob,Bash,Edit")
    ;;
esac
CMD_STR="$(shell_join "${CMD[@]}")"

write_meta "running"

refuse() {
  local reason="$1"
  echo "run-worker: refuse: $reason" >&2
  write_json "fail" 1 "$reason" "$(iso_now)"
  exit 1
}

if [[ "$WORKER" == "$LIVE" ]]; then
  refuse "worker '$WORKER' equals live parent"
fi
if [[ "$FLAG" != "true" ]]; then
  refuse "worker '$WORKER' is off in .rig/harness.toml"
fi

if [[ -z "$BIN" ]]; then
  echo "run-worker: binary '$WORKER' not on PATH"
  echo "would run: $CMD_STR"
  write_json "fail" 127 "binary '$WORKER' not on PATH; would run: $CMD_STR" "$(iso_now)"
  exit 127
fi

# Default is dry-run. Live child only when RIG_LIVE=1.
if [[ "${RIG_LIVE:-0}" != "1" ]]; then
  echo "run-worker: dry-run ($WORKER job=$JOB_ID)"
  echo "would run: $CMD_STR"
  write_json "ok" 0 "dry-run" "$(iso_now)"
  exit 0
fi

snapshot_files() {
  git -C "$REPO" status --porcelain 2>/dev/null | awk '{print $NF}' | sort -u || true
}

BEFORE="$(snapshot_files)"
LOG="$JOB_DIR/stdout.log"
: > "$LOG"

kill_tree() {
  local p="$1" kids
  kids="$(pgrep -P "$p" 2>/dev/null || true)"
  local k
  for k in $kids; do
    kill_tree "$k"
  done
  kill -TERM "$p" 2>/dev/null || true
}

set +e
"${CMD[@]}" >"$LOG" 2>&1 &
CHILD=$!
write_meta "running"
write_watch
ELAPSED=0
TIMED_OUT=0
while kill -0 "$CHILD" 2>/dev/null; do
  if [[ "$ELAPSED" -ge "$TIMEOUT_SECS" ]]; then
    TIMED_OUT=1
    kill_tree "$CHILD"
    sleep 1
    kill -KILL "$CHILD" 2>/dev/null || true
    break
  fi
  sleep 1
  ELAPSED=$((ELAPSED + 1))
done
wait "$CHILD" 2>/dev/null
CHILD_RC=$?
set -e

ENDED="$(iso_now)"
AFTER="$(snapshot_files)"
RESULT_FILES="$(comm -13 <(printf '%s\n' "$BEFORE") <(printf '%s\n' "$AFTER") | sed '/^$/d' || true)"

summary_from_log() {
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$LOG" <<'PY'
import json, pathlib, sys
raw = pathlib.Path(sys.argv[1]).read_text(errors="replace")
text = raw[-2000:] if len(raw) > 2000 else raw
for line in reversed(raw.splitlines()):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue
    for k in ("result", "message", "text", "summary"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            print(v.strip()[:2000])
            raise SystemExit
    if isinstance(obj.get("content"), str) and obj["content"].strip():
        print(obj["content"].strip()[:2000])
        raise SystemExit
print(text.strip()[:2000])
PY
    return
  fi
  tail -c 2000 "$LOG" | tr '\n' ' '
}

SUMMARY="$(summary_from_log)"
if [[ "$TIMED_OUT" == "1" ]]; then
  write_json "timeout" 124 "${SUMMARY:-timeout after ${TIMEOUT_SECS}s}" "$ENDED"
  exit 124
fi
if [[ "$CHILD_RC" -eq 0 ]]; then
  write_json "ok" 0 "${SUMMARY:-ok}" "$ENDED"
  exit 0
fi
write_json "fail" "$CHILD_RC" "${SUMMARY:-child exited $CHILD_RC}" "$ENDED"
exit "$CHILD_RC"
