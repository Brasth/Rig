#!/usr/bin/env bash
# run-worker.sh <grok|codex|claude|cursor|opencode|omp|pi|agy> <job-id> <brief-file>
set -euo pipefail

_DETECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/detect-binaries.sh"
# shellcheck source=detect-binaries.sh
source "$_DETECT"

usage() {
  echo "usage: run-worker.sh <grok|codex|claude|cursor|opencode|omp|pi|agy> <job-id> <brief-file>" >&2
  exit 2
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage
[[ $# -eq 3 ]] || usage

WORKER="$1"
JOB_ID="$2"
BRIEF_IN="$3"
ROLE="${RIG_ROLE:-worker}"
TIMEOUT_SECS="${RIG_TIMEOUT:-1200}"
ROUTE_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/route.py"
EVIDENCE_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/change_evidence.py"
ADMISSION_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/admission.py"
EXECUTION_MODE="dry_run"
[[ "${RIG_LIVE:-0}" == "1" ]] && EXECUTION_MODE="live"
OWNER_CREDENTIALS=""
ADMISSION_META_JSON="{}"
ADMISSION_ACTIVATED=0
ADMISSION_FINISHED=0
LAUNCH_COMMITTED=0
AGY_PERMS_MERGED=0

case "$WORKER" in
  grok|codex|claude|cursor|opencode|omp|pi|agy) ;;
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
JOB_FILES_JSON="$(python3 - "$JOB_DIR/meta.json" <<'PY'
import json, os, pathlib, sys
if "RIG_JOB_FILES_JSON" in os.environ:
    try:
        files = json.loads(os.environ["RIG_JOB_FILES_JSON"])
    except json.JSONDecodeError as exc:
        raise SystemExit(f"run-worker: invalid RIG_JOB_FILES_JSON: {exc}")
elif os.environ.get("RIG_JOB_FILES"):
    files = os.environ["RIG_JOB_FILES"].replace(",", " ").split()
else:
    try:
        files = json.loads(pathlib.Path(sys.argv[1]).read_text()).get("files", [])
    except (OSError, ValueError, AttributeError):
        files = []
if not isinstance(files, list) or any(not isinstance(f, str) or not f or "\0" in f for f in files):
    raise SystemExit("run-worker: file scope must be a JSON array of nonempty paths")
print(json.dumps(files))
PY
)"
export RIG_JOB_FILES_JSON="$JOB_FILES_JSON"
BRIEF="$JOB_DIR/brief.md"

prepare_brief() {
  mkdir -p "$JOB_DIR"
  # Never overwrite a live brief before admission authorizes this attempt.
  local source
  source="$(mktemp "$JOB_DIR/brief.src.XXXXXX")"
  cp "$BRIEF_IN" "$source"
  {
    if ! command grep -q "You are a worker, not the orchestrator" "$source" 2>/dev/null; then
      printf '%s\n\n' "$WORKER_PREAMBLE"
    fi
    cat "$source"
  } > "$BRIEF"
  rm -f "$source"
}

LIVE="$(live_parent)"
FLAG="$(worker_flag "$WORKER")"
BIN="$(find_worker_bin "$WORKER")"
STARTED="$(iso_now)"

admission_call() {
  python3 - "$ADMISSION_PY" "$1" "$REPO" "$JOB_ID" "$WORKER" "$ROLE" "${MODEL:-}" \
    "$JOB_FILES_JSON" "$$" "$OWNER_CREDENTIALS" "$EXECUTION_MODE" "${PROVENANCE_JSON:-\{\}}" "${2:-}" <<'PY'
import json, os, pathlib, sys, uuid
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
import admission
operation, repo, job_id, worker, role, model = sys.argv[2:8]
files, wrapper_pid, credential_path, execution_mode, provenance, value = (
    json.loads(sys.argv[8]), int(sys.argv[9]), sys.argv[10], sys.argv[11], json.loads(sys.argv[12]), sys.argv[13]
)
owner = admission.caller_owner("wrapper", owner_pid=wrapper_pid, owner_session=os.environ.get("RIG_OWNER_SESSION", ""))
access = os.environ.get("RIG_ACCESS") or ("read" if role.lower() in {"review", "reviewer", "explore", "explorer"} else "write")
try:
    if operation == "reserve":
        supplied = {key: os.environ.get("RIG_" + key.upper(), "") for key in ("reservation_id", "attempt_id", "owner_token")}
        with admission.transaction(repo):
            record = admission.reserve(
                repo, job_id=job_id, worker=worker, role=role, model=model, files=files, access=access, owner=owner,
                queue_id=os.environ.get("RIG_QUEUE_ID", ""), writer_job_id=provenance.get("writer_job_id", ""),
                writer_snapshot_id=provenance.get("writer_snapshot_id", ""), dry_run=execution_mode == "dry_run", **supplied,
            )
            if execution_mode == "dry_run":
                # Register the fresh ID while preview and registration share the lock.
                record["preview_id"] = uuid.uuid4().hex
                path = pathlib.Path(repo) / ".rig" / "jobs" / job_id / "meta.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                preview = {"job_id": job_id, "preview_id": record["preview_id"], "execution_mode": "dry_run",
                           "executor_kind": "wrapper", "status": "reserved", "ownership_established": False}
                temporary = path.with_name("meta.json.tmp")
                with temporary.open("w") as stream:
                    json.dump(preview, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(path)
        keys = ("reservation_id", "attempt_id", "job_id", "queue_id", "access", "owner", "scope_unknown", "preview_id")
        result = {key: record[key] for key in keys if key in record}
        result["protected_files"] = record.get("files", [])
        result["ownership_established"] = execution_mode == "live"
        if execution_mode == "live":
            result["credentials_path"] = str(admission.write_credentials(repo, record))
    else:
        if not credential_path:
            raise admission.AdmissionError("explicit wrapper credentials are required")
        saved = json.loads(pathlib.Path(credential_path).read_text())
        credentials = admission.credentials(saved)
        if operation == "activate":
            process = json.loads(pathlib.Path(value).read_text())
            result = admission.activate(repo, **credentials, job_id=job_id, worker=worker, files=files,
                                        access=access, owner=owner, process=process)
        elif operation == "commit":
            result = admission.commit_launch(repo, **credentials, owner=owner)
        elif operation == "observe":
            result = admission.observe_process(repo, **credentials, owner=owner)
        elif operation == "stopped":
            record = admission.assert_owned(repo, **credentials, owner=owner)
            stopped, reason = admission._external_stopped(record)
            result = {"stopped": stopped}
            if not stopped:
                print("run-worker: execution liveness needs reconciliation: " + reason, file=sys.stderr)
        elif operation == "finish":
            result = admission.finish(repo, **credentials, status=value, owner=owner, completion={"kind": "wrapper"})
            if result.get("needs_reconciliation"):
                print("run-worker: execution liveness needs reconciliation; file protection remains held", file=sys.stderr)
        elif operation == "release":
            result = admission.release(repo, **credentials, owner=owner, mode="launch_failed", rationale=value)
        elif operation == "close":
            result = admission.release(repo, **credentials, owner=owner, mode="close", rationale=value)
        else:
            raise admission.AdmissionError("unknown wrapper admission operation")
        result = {key: result[key] for key in ("reservation_id", "attempt_id", "stage", "slot_held", "stopped") if key in result}
    print(json.dumps(result))
except (OSError, ValueError, admission.AdmissionError) as error:
    raise SystemExit(f"run-worker: admission refused: {error}")
PY
}

kill_tree() {
  local p="$1" kids k
  kids="$(pgrep -P "$p" 2>/dev/null || true)"
  for k in $kids; do
    kill_tree "$k"
  done
  kill -TERM "$p" 2>/dev/null || true
}

agy_restore_settings() {
  if [[ "${AGY_PERMS_MERGED:-0}" != "1" ]]; then
    return 0
  fi
  AGY_PERMS_MERGED=0
  local args=(restore --job-dir "$JOB_DIR")
  [[ -n "${AGY_SETTINGS:-}" ]] && args+=(--settings "$AGY_SETTINGS")
  python3 "$AGY_PERMS_PY" "${args[@]}" || true
}

wrapper_cleanup() {
  local rc=$?
  trap - EXIT
  trap '' INT TERM
  set +e
  # The durable gate also covers interruption before the shell records commit success.
  [[ -f "${LAUNCH_GATE:-}" ]] && LAUNCH_COMMITTED=1
  if [[ -n "$OWNER_CREDENTIALS" && ( "$ADMISSION_FINISHED" != "1" || "$LAUNCH_COMMITTED" != "1" ) ]]; then
    if [[ -n "${CHILD:-}" ]]; then
      kill_tree "$CHILD"
      sleep 1
      kill -KILL "$CHILD" 2>/dev/null || true
      wait "$CHILD" 2>/dev/null || true
    fi
    if [[ "$LAUNCH_COMMITTED" == "1" ]]; then
      local outcome="fail" summary="wrapper interrupted before recording completion"
      if [[ -f "$JOB_DIR/cancel.json" || "$rc" == "130" || "$rc" == "143" ]]; then
        outcome="cancelled"
        summary="cancelled by parent"
        rc=130
      fi
      complete_execution
      write_json "$outcome" "$rc" "$summary" "$(iso_now)"
    elif ! admission_call release "wrapper stopped before the launch barrier committed" >/dev/null; then
      local outcome="fail"
      [[ -f "$JOB_DIR/cancel.json" ]] && outcome="cancelled"
      admission_call finish "$outcome" >/dev/null || true
    fi
  fi
  [[ -f "$JOB_DIR/cancel.json" ]] && rc=130
  agy_restore_settings
  exit "$rc"
}

complete_execution() {
  local completion
  RESULT_FILES_JSON="[]"
  # Capture final content only after the recorded process tree is confirmed stopped.
  admission_call observe >/dev/null || true
  if ! completion="$(admission_call stopped)"; then
    EVIDENCE_ERROR="could not confirm stopped execution; final evidence deferred"
    return 0
  fi
  if ! python3 - "$completion" <<'PY'
import json, sys
raise SystemExit(0 if json.loads(sys.argv[1]).get("stopped") is True else 1)
PY
  then
    EVIDENCE_ERROR="execution liveness needs reconciliation; final evidence deferred"
    return 0
  fi
  if python3 "$EVIDENCE_PY" finish --repo "$REPO" --job-dir "$JOB_DIR" >/dev/null; then
    RESULT_FILES_JSON="$(python3 - "$JOB_DIR/change-evidence.json" <<'PY'
import json, pathlib, sys
evidence = json.loads(pathlib.Path(sys.argv[1]).read_text())
# Concurrent observations remain in the sidecar without attribution to the job.
print(json.dumps(evidence.get("scoped_changed_paths", [])))
PY
)"
  else
    EVIDENCE_ERROR="could not capture complete after-change evidence"
  fi
}

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
  RESULT_FILES_JSON="${RESULT_FILES_JSON:-[]}" \
  RESULT_EVIDENCE_ERROR="${EVIDENCE_ERROR:-}" \
  RESULT_EXECUTION_MODE="$EXECUTION_MODE" \
  RESULT_PROVENANCE="${PROVENANCE_JSON:-\{\}}" \
  RESULT_ADMISSION="$ADMISSION_META_JSON" \
  RESULT_ADMISSION_SCRIPT="$ADMISSION_PY" \
  RESULT_CREDENTIALS="$OWNER_CREDENTIALS" \
  RESULT_ACTIVATED="$ADMISSION_ACTIVATED" \
  RESULT_WRAPPER_PID="$$" \
  RESULT_NEXT="${RESULT_NEXT:-}" \
  RESULT_MODEL="${MODEL:-}" \
  RESULT_EFFORT="${EFFORT:-}" \
  RESULT_THREAD="${PARENT_THREAD:-}" \
  RESULT_OUT="$RESULT_OUT" \
  RESULT_META="$RESULT_META" \
  RESULT_REPO="$REPO" \
  RESULT_BIN="$BIN" \
  RESULT_STATE="$RESULT_STATE" \
  python3 - <<'PY'
import contextlib, json, os, pathlib, sys
from datetime import datetime
files = json.loads(os.environ.get("RESULT_FILES_JSON", "[]"))
keep = ("thread", "session_id", "pid", "open", "watch", "kind", "doing", "files")
old = {}
mpath = pathlib.Path(os.environ["RESULT_META"])
if mpath.is_file():
    try:
        loaded = json.loads(mpath.read_text())
        if isinstance(loaded, dict):
            old = loaded
    except Exception:
        old = {}
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
    "model": os.environ.get("RESULT_MODEL", ""),
    "effort": os.environ.get("RESULT_EFFORT", ""),
    "execution_mode": os.environ["RESULT_EXECUTION_MODE"],
    "executor_kind": "wrapper",
    "model_source": "selected",
    "model_inferred": False,
    "parent_writes": False,
}
obj.update(json.loads(os.environ.get("RESULT_PROVENANCE", "{}")))
obj.update({key: value for key, value in json.loads(os.environ["RESULT_ADMISSION"]).items() if key != "credentials_path"})
if os.environ.get("RESULT_EVIDENCE_ERROR"):
    obj["evidence_error"] = os.environ["RESULT_EVIDENCE_ERROR"]
for key in keep:
    if not obj.get(key) and old.get(key) not in (None, ""):
        obj[key] = old[key]
thread = os.environ.get("RESULT_THREAD", "")
if thread:
    obj["thread"] = thread
started = os.environ.get("RESULT_STARTED") or ""
ended = os.environ.get("RESULT_ENDED") or ""
if started and ended:
    try:
        start_dt = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ")
        end_dt = datetime.strptime(ended, "%Y-%m-%dT%H:%M:%SZ")
        obj["elapsed_s"] = max(0, int((end_dt - start_dt).total_seconds()))
    except ValueError:
        pass
sys.path.insert(0, str(pathlib.Path(os.environ["RESULT_ADMISSION_SCRIPT"]).parent))
import admission
credential_path = os.environ["RESULT_CREDENTIALS"]
repo = pathlib.Path(os.environ["RESULT_REPO"])
with admission.transaction(repo):
    if credential_path:
        credentials = admission.credentials(json.loads(pathlib.Path(credential_path).read_text()))
        owner = admission.caller_owner("wrapper", owner_pid=int(os.environ["RESULT_WRAPPER_PID"]),
                                       owner_session=os.environ.get("RIG_OWNER_SESSION", ""))
        admission.assert_owned(repo, **credentials, owner=owner)
    elif json.loads(mpath.read_text()).get("preview_id") != obj.get("preview_id") or not obj.get("preview_id"):
        raise SystemExit("run-worker: dry-run execution ID changed before result registration")
    if (mpath.parent / "cancel.json").exists():
        obj.update(status="cancelled", exit_code=130)
    if credential_path and os.environ["RESULT_ACTIVATED"] == "1":
        completion = admission.finish(repo, **credentials, status=obj["status"], owner=owner, completion={"kind": "wrapper"})
        if completion.get("needs_reconciliation"):
            print("run-worker: child liveness needs reconciliation; ownership remains held", file=sys.stderr)
    path = pathlib.Path(os.environ["RESULT_OUT"])
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as stream:
        stream.write(json.dumps(obj, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)
    meta = dict(obj)
    meta["repo"] = os.environ.get("RESULT_REPO", "")
    meta["bin"] = os.environ.get("RESULT_BIN", "")
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
  ADMISSION_FINISHED="$ADMISSION_ACTIVATED"
  if [[ -f "$JOB_DIR/cancel.json" && "$status" != "cancelled" ]]; then
    return 130
  fi
}

write_meta() {
  local status="$1"
  META_OUT="$JOB_DIR/meta.json"
  python3 - "$META_OUT" "$JOB_ID" "$WORKER" "$ROLE" "$status" "$STARTED" "$REPO" "$BIN" "${CHILD:-}" "${SESSION_ID:-}" "$JOB_DIR" "${MODEL:-}" "${EFFORT:-}" "${PARENT_THREAD:-}" "$EXECUTION_MODE" "${PROVENANCE_JSON:-\{\}}" "$JOB_FILES_JSON" "$ADMISSION_META_JSON" "$OWNER_CREDENTIALS" "$ADMISSION_PY" "$$" <<'PY'
import contextlib, json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
old = {}
if path.is_file():
    try:
        loaded = json.loads(path.read_text())
        if isinstance(loaded, dict):
            old = loaded
    except Exception:
        old = {}
obj = {
    "job_id": sys.argv[2],
    "worker": sys.argv[3],
    "role": sys.argv[4],
    "status": sys.argv[5],
    "started_at": sys.argv[6],
    "repo": sys.argv[7],
    "bin": sys.argv[8],
    "execution_mode": sys.argv[15],
    "executor_kind": "wrapper",
    "model_source": "selected",
    "model_inferred": False,
    "parent_writes": False,
}
obj.update(json.loads(sys.argv[16]))
obj.update({key: value for key, value in json.loads(sys.argv[18]).items() if key != "credentials_path"})
pid, session_id, job_dir = sys.argv[9], sys.argv[10], sys.argv[11]
model, effort, thread = sys.argv[12], sys.argv[13], sys.argv[14]
if pid:
    obj["pid"] = int(pid)
if session_id:
    obj["session_id"] = session_id
    obj["open"] = f"grok -r {session_id}"
if model:
    obj["model"] = model
if effort:
    obj["effort"] = effort
if thread:
    obj["thread"] = thread
obj["files"] = json.loads(sys.argv[17])
obj["watch"] = f"tail -f {job_dir}/stdout.log"
for key, val in old.items():
    if key not in obj and val not in (None, ""):
        obj[key] = val
sys.path.insert(0, str(pathlib.Path(sys.argv[20]).parent))
import admission
with admission.transaction(sys.argv[7]):
    if sys.argv[19]:
        credentials = admission.credentials(json.loads(pathlib.Path(sys.argv[19]).read_text()))
        owner = admission.caller_owner("wrapper", owner_pid=int(sys.argv[21]), owner_session=os.environ.get("RIG_OWNER_SESSION", ""))
        admission.assert_owned(sys.argv[7], **credentials, owner=owner)
        if (path.parent / "cancel.json").exists():
            raise SystemExit("run-worker: cancelled before launch registration")
    elif json.loads(path.read_text()).get("preview_id") != obj.get("preview_id") or not obj.get("preview_id"):
        raise SystemExit("run-worker: dry-run execution ID changed before metadata registration")
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as stream:
        stream.write(json.dumps(obj, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
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
    [[ -n "${MODEL:-}" ]] && echo "- model: \`$MODEL\`"
    [[ -n "${EFFORT:-}" ]] && echo "- effort: \`$EFFORT\`"
    echo "- status: running"
    [[ -n "${CHILD:-}" ]] && echo "- pid: \`$CHILD\`"
    if [[ -n "${SESSION_ID:-}" ]]; then
      echo "- session: \`$SESSION_ID\`"
      echo "- open Grok: \`cd $REPO && grok -r $SESSION_ID\`"
      echo "- dashboard: \`grok dashboard\`"
    fi
    echo "- live log: \`rig job log $JOB_ID -f\`"
    echo "- if it asks: \`rig job allow $JOB_ID\`  or  \`rig job deny $JOB_ID\`"
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

if [[ -z "${RIG_MODEL:-}" && -f "$ROUTE_PY" ]]; then
  eval "$(python3 "$ROUTE_PY" env --worker "$WORKER" --role "$ROLE")"
  ROLE="${RIG_ROLE:-$ROLE}"
fi
MODEL="${RIG_MODEL:-}"
EFFORT="${RIG_EFFORT:-}"
launch_provenance() {
  python3 "$ROUTE_PY" allow --json --model "$MODEL" --role "$ROLE" --repo "$REPO" \
    --writer-job-id "${RIG_WRITER_JOB_ID:-}" --writer-cli "${RIG_WRITER_CLI:-}" \
    --writer-model "${RIG_WRITER_MODEL:-}" --writer-provider "${RIG_WRITER_PROVIDER:-}" \
    --review-mode "${RIG_REVIEW_MODE:-standalone}"
}
PROVENANCE_JSON="$(launch_provenance)" || exit 1
ADMISSION_META_JSON="$(admission_call reserve)" || exit 1
OWNER_CREDENTIALS="$(python3 - "$ADMISSION_META_JSON" <<'PY'
import json, sys
print(json.loads(sys.argv[1]).get("credentials_path", ""))
PY
)"
trap wrapper_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ -n "$OWNER_CREDENTIALS" ]]; then
  echo "run-worker: owner credentials: $OWNER_CREDENTIALS" >&2
fi
prepare_brief
if [[ "$ROLE" == "review" || "$ROLE" == "reviewer" ]]; then
  PROVENANCE_JSON="$(python3 - "$ROUTE_PY" "$REPO" "$JOB_DIR" "$BRIEF" "$PROVENANCE_JSON" <<'PY'
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
import change_evidence
root, job_dir, brief = map(pathlib.Path, sys.argv[2:5])
provenance = json.loads(sys.argv[5])
writer_id = provenance.get("writer_job_id")
accepted_snapshot = provenance.get("writer_snapshot_id")
if writer_id and accepted_snapshot:
    writer_dir = change_evidence.job_directory(root, root / ".rig" / "jobs" / writer_id)
    writer = change_evidence.read_json(writer_dir / "meta.json") or {}
    files = change_evidence.normalize_files(root, writer.get("files", []))
    if change_evidence.snapshot(root, files)["snapshot_id"] != accepted_snapshot:
        raise SystemExit("content_changed: writer scope no longer matches the accepted reviewer snapshot")
    context = {"writer_job_id": writer_id, "writer_snapshot_id": accepted_snapshot, "writer_files": files}
    section = (
        "\n\n## Accepted writer context\n\n"
        "The parent accepted this scoped content for review. Paths below are literal repository paths. "
        "Inspect this snapshot and report findings to the parent; review exit zero does not accept the implementation.\n\n"
        "```json\n" + json.dumps(context, indent=2) + "\n```\n"
    )
    review_brief = job_dir / "review-brief.md"
    review_brief.write_bytes(brief.read_bytes() + section.encode("utf-8"))
    provenance.update(writer_files=files, review_brief=str(review_brief))
print(json.dumps(provenance))
PY
)" || exit 1
  BRIEF="$(python3 - "$PROVENANCE_JSON" "$BRIEF" <<'PY'
import json, sys
print(json.loads(sys.argv[1]).get("review_brief") or sys.argv[2])
PY
)"
fi
BRIEF_TEXT="$(cat "$BRIEF")"
PARENT_THREAD="${RIG_THREAD:-}"
if [[ -z "$PARENT_THREAD" ]]; then
  JOBS_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jobs.py"
  if [[ -f "$JOBS_PY" ]]; then
    PARENT_THREAD="$(python3 "$JOBS_PY" thread --repo "$REPO" 2>/dev/null || true)"
  fi
fi

SESSION_ID=""
CMD=()
case "$WORKER" in
  grok)
    SESSION_ID="$(uuidgen | tr 'A-Z' 'a-z')"
    CMD=(grok --no-auto-update --prompt-file "$BRIEF" --cwd "$REPO" --output-format streaming-json --session-id "$SESSION_ID" --always-approve --max-turns 40)
    [[ -n "$MODEL" ]] && CMD+=(-m "$MODEL")
    [[ -n "$EFFORT" ]] && CMD+=(--effort "$EFFORT")
    ;;
  codex)
    CMD=(codex exec --ephemeral -s workspace-write -C "$REPO")
    [[ -n "$MODEL" ]] && CMD+=(-m "$MODEL")
    [[ -n "$EFFORT" ]] && CMD+=(-c "model_reasoning_effort=\"$EFFORT\"")
    CMD+=("$BRIEF_TEXT")
    ;;
  claude)
    # Print-mode must stream. json buffers until exit, so Anthropic's
    # invalid remote deny rules (Bash(eval $(wget*))) fill the TUI as
    # "doing" and the job looks stuck. -p is boolean; prompt is last
    # after --. Do not use --bare (drops OAuth), empty --setting-sources=
    # (can skip user auth), or --dangerously-skip-permissions (org
    # policy can disable bypass).
    CLAUDE_WORKER_MD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../adapters/claude/CLAUDE.worker.md"
    CLAUDE_MCP_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rig_mcp.py"
    CLAUDE_MCP="$JOB_DIR/mcp.json"
    CLAUDE_PY=""
    for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
      if [[ -x "$c" ]]; then
        CLAUDE_PY="$c"
        break
      fi
    done
    if [[ -z "$CLAUDE_PY" ]]; then
      CLAUDE_PY="$(command -v python3 || true)"
    fi
    python3 - "$CLAUDE_MCP" "$CLAUDE_MCP_PY" "$JOB_DIR" "$CLAUDE_PY" "$JOB_ID" "$REPO" <<'PY'
import json, pathlib, sys
path, script, job_dir, py = map(pathlib.Path, sys.argv[1:5])
job_id, repo = sys.argv[5], sys.argv[6]
path.write_text(
    json.dumps(
        {
            "mcpServers": {
                "rig-ask": {
                    "command": str(py),
                    "args": [str(script)],
                    "env": {
                        "RIG_JOB_DIR": str(job_dir),
                        "RIG_JOB_ID": job_id,
                        "RIG_REPO": repo,
                    },
                }
            }
        },
        indent=2,
    )
    + "\n"
)
PY
    CMD=(
      claude -p
      --model "${MODEL:-claude-sonnet-5}"
      --output-format stream-json
      --verbose
      --permission-mode acceptEdits
      --allowedTools "Read,Grep,Glob,Bash,Edit,Write"
      --tools "Bash,Edit,Read,Grep,Glob,Write"
      --mcp-config "$CLAUDE_MCP"
      --permission-prompt-tool mcp__rig-ask__permission_prompt
      --strict-mcp-config
      --disable-slash-commands
      --no-session-persistence
    )
    if [[ -f "$CLAUDE_WORKER_MD" ]]; then
      CMD+=(--append-system-prompt-file "$CLAUDE_WORKER_MD")
    fi
    # Haiku print-mode hangs on --effort. Still stamp effort in meta.json.
    if [[ -n "$EFFORT" ]]; then
      case "$MODEL" in
        *[Hh][Aa][Ii][Kk][Uu]*) ;;
        *) CMD+=(--effort "$EFFORT") ;;
      esac
    fi
    CMD+=(-- "$BRIEF_TEXT")
    ;;
  cursor)
    CURSOR_BIN="${BIN:-cursor-agent}"
    CMD=(
      "$CURSOR_BIN" -p "$BRIEF_TEXT"
      --workspace "$REPO"
      --output-format stream-json
      --stream-partial-output
      --force
      --trust
    )
    [[ -n "$MODEL" ]] && CMD+=(--model "$MODEL")
    case "$ROLE" in
      explore|mini) CMD+=(--mode=ask) ;;
    esac
    ;;
  opencode)
    CMD=(
      opencode run
      --format json
      --dir "$REPO"
      --title "rig $JOB_ID"
      --auto
    )
    [[ -n "$MODEL" ]] && CMD+=(-m "$MODEL")
    [[ -n "$EFFORT" ]] && CMD+=(--variant "$EFFORT")
    CMD+=("$BRIEF_TEXT")
    ;;
  omp)
    OMP_WORKER_MD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../adapters/omp/OMP.worker.md"
    CMD=(
      omp -p
      --mode json
      --cwd "$REPO"
      --no-session
      --approval-mode write
    )
    if [[ -f "$OMP_WORKER_MD" ]]; then
      CMD+=(--append-system-prompt "$OMP_WORKER_MD")
    fi
    [[ -n "$MODEL" ]] && CMD+=(--model "$MODEL")
    [[ -n "$EFFORT" ]] && CMD+=(--thinking "$EFFORT")
    CMD+=("$BRIEF_TEXT")
    ;;
  pi)
    PI_WORKER_MD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../adapters/pi/PI.worker.md"
    CMD=(
      pi -p
      --mode json
      --no-session
      --approve
    )
    if [[ -f "$PI_WORKER_MD" ]]; then
      CMD+=(--append-system-prompt "$PI_WORKER_MD")
    fi
    [[ -n "$MODEL" ]] && CMD+=(--model "$MODEL")
    [[ -n "$EFFORT" ]] && CMD+=(--thinking "$EFFORT")
    CMD+=("$BRIEF_TEXT")
    ;;
  agy)
    AGY_WORKER_MD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../adapters/agy/AGY.worker.md"
    AGY_PERMS_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/agy_job_permissions.py"
    AGY_PROMPT="$BRIEF_TEXT"
    if [[ -f "$AGY_WORKER_MD" ]]; then
      AGY_PROMPT="$(cat "$AGY_WORKER_MD")"$'\n\n'"$BRIEF_TEXT"
    fi
    CMD=(
      agy -p "$AGY_PROMPT"
      --output-format json
      --mode accept-edits
      --print-timeout "${TIMEOUT_SECS}s"
      --disable-slash-commands
    )
    [[ -n "$MODEL" ]] && CMD+=(--model "$MODEL")
    [[ -n "$EFFORT" ]] && CMD+=(--effort "$EFFORT")
    ;;
esac
CMD_STR="$(shell_join "${CMD[@]}")"

write_meta "reserved"

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

if [[ "$WORKER" == "agy" ]]; then
  AGY_PERMS_PY="${AGY_PERMS_PY:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/agy_job_permissions.py}"
  agy_merge_args=(merge --job-dir "$JOB_DIR")
  [[ -n "${AGY_SETTINGS:-}" ]] && agy_merge_args+=(--settings "$AGY_SETTINGS")
  AGY_PERMS_MERGED=1
  python3 "$AGY_PERMS_PY" "${agy_merge_args[@]}"
fi

if ! python3 "$EVIDENCE_PY" begin --repo "$REPO" --job-dir "$JOB_DIR" --files-json "$JOB_FILES_JSON" >/dev/null; then
  refuse "could not capture before-change evidence"
fi
# Refresh acceptance immediately before an independent reviewer starts.
if [[ "${RIG_REVIEW_MODE:-standalone}" == "independent" ]]; then
  CURRENT_PROVENANCE="$(launch_provenance)" || refuse "writer provenance changed before review start"
  PROVENANCE_JSON="$(python3 - "$PROVENANCE_JSON" "$CURRENT_PROVENANCE" <<'PY'
import json, sys
prepared, current = map(json.loads, sys.argv[1:3])
for key in ("writer_job_id", "writer_snapshot_id", "writer_cli", "writer_model", "writer_provider"):
    if prepared.get(key) != current.get(key):
        raise SystemExit("writer context changed since the reviewer brief was prepared")
for key in ("writer_files", "review_brief"):
    if key in prepared:
        current[key] = prepared[key]
print(json.dumps(current))
PY
)" || refuse "writer provenance changed before review start"
fi
LOG="$JOB_DIR/stdout.log"
: > "$LOG"

ATTEMPT_ID="$(python3 - "$ADMISSION_META_JSON" <<'PY'
import json, sys
print(json.loads(sys.argv[1])["attempt_id"])
PY
)"
LAUNCH_GATE="$JOB_DIR/launch-$ATTEMPT_ID.json"
LAUNCH_READY="$JOB_DIR/launch-ready-$ATTEMPT_ID.json"
RIG_JOB_ID="$JOB_ID" RIG_JOB_DIR="$JOB_DIR" RIG_REPO="$REPO" RIG_ROLE="$ROLE" \
python3 - "$ADMISSION_PY" "$REPO" "$$" "$LAUNCH_GATE" "$LAUNCH_READY" "${CMD[@]}" >"$LOG" 2>&1 <<'PY' &
import json, os, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
import admission
repo, wrapper_pid, gate, ready = pathlib.Path(sys.argv[2]), int(sys.argv[3]), pathlib.Path(sys.argv[4]), pathlib.Path(sys.argv[5])
os.setsid()
wrapper = admission.process_identity(wrapper_pid)
process = admission.process_identity(os.getpid())
process.update(gated=True, gate_path=str(gate))
temporary = ready.with_suffix(".tmp")
with temporary.open("w") as stream:
    json.dump(process, stream)
    stream.flush()
    os.fsync(stream.fileno())
temporary.replace(ready)
while not gate.is_file():
    try:
        os.kill(wrapper_pid, 0)
    except ProcessLookupError:
        raise SystemExit("wrapper stopped before the launch barrier committed")
    current = admission.process_identity(wrapper_pid)
    if wrapper.get("start_id") and current.get("start_id") != wrapper["start_id"]:
        raise SystemExit("wrapper stopped before the launch barrier committed")
    time.sleep(0.02)
os.chdir(repo)
# Child notifications do not need parent admission credentials.
os.environ.pop("RIG_OWNER_TOKEN", None)
os.execvp(sys.argv[6], sys.argv[6:])
PY
CHILD=$!
for _READY_POLL in {1..250}; do
  [[ -f "$LAUNCH_READY" ]] && break
  kill -0 "$CHILD" 2>/dev/null || break
  sleep 0.02
done
if [[ ! -f "$LAUNCH_READY" ]]; then
  refuse "child did not reach the launch barrier"
fi
admission_call activate "$LAUNCH_READY" >/dev/null || refuse "could not activate the reserved child"
ADMISSION_ACTIVATED=1
write_meta "running"
write_watch
admission_call commit >/dev/null || refuse "could not commit the launch barrier"
LAUNCH_COMMITTED=1
set +e
ELAPSED=0
TIMED_OUT=0
CANCELLED=0
ASK_NOTIFIED=0
IN_ASK=0
while kill -0 "$CHILD" 2>/dev/null; do
  if ! admission_call observe >/dev/null; then
    echo "run-worker: could not observe child ownership; stopping execution" >&2
    kill_tree "$CHILD"
    sleep 1
    kill -KILL "$CHILD" 2>/dev/null || true
    break
  fi
  if [[ -f "$JOB_DIR/cancel.json" ]]; then
    CANCELLED=1
    kill_tree "$CHILD"
    sleep 1
    kill -KILL "$CHILD" 2>/dev/null || true
    break
  fi
  if [[ -f "$JOB_DIR/ask.json" && ! -f "$JOB_DIR/ask-reply.json" ]]; then
    IN_ASK=1
    if [[ "$ASK_NOTIFIED" -eq 0 ]]; then
      echo "run-worker: ASK — parent must answer: rig job allow $JOB_ID" >&2
      echo "run-worker: do not kill this job; do not spawn another worker" >&2
      ASK_NOTIFIED=1
    fi
  else
    if [[ "$IN_ASK" -eq 1 ]]; then
      # Parent answered. Restart the work clock so a slow allow cannot
      # immediately timeout a job that already used most of RIG_TIMEOUT.
      ELAPSED=0
      IN_ASK=0
    fi
    ASK_NOTIFIED=0
    if [[ "$ELAPSED" -ge "$TIMEOUT_SECS" ]]; then
      TIMED_OUT=1
      kill_tree "$CHILD"
      sleep 1
      kill -KILL "$CHILD" 2>/dev/null || true
      break
    fi
    ELAPSED=$((ELAPSED + 1))
  fi
  sleep 1
done
wait "$CHILD" 2>/dev/null
CHILD_RC=$?
set -e

ENDED="$(iso_now)"
complete_execution

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
    for k in ("result", "message", "text", "summary", "response"):
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

agy_restore_settings

SUMMARY="$(summary_from_log)"
JOBS_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jobs.py"
if [[ -f "$JOBS_PY" ]]; then
  python3 "$JOBS_PY" persist --dir "$JOB_DIR" || true
fi
if [[ "$CANCELLED" == "1" || -f "$JOB_DIR/cancel.json" ]]; then
  write_json "cancelled" 130 "${SUMMARY:-cancelled by parent}" "$ENDED"
  exit 130
fi
if [[ "$TIMED_OUT" == "1" ]]; then
  write_json "timeout" 124 "${SUMMARY:-timeout after ${TIMEOUT_SECS}s}" "$ENDED"
  exit 124
fi
if [[ "$CHILD_RC" -eq 0 ]]; then
  if [[ "$WORKER" == "agy" ]]; then
    AGY_DENIED="$(python3 "$AGY_PERMS_PY" denied-actions "$LOG" 2>/dev/null || true)"
    if [[ -n "$AGY_DENIED" ]]; then
      write_json "fail" 1 "${SUMMARY:-denied_actions: $AGY_DENIED}" "$ENDED"
      exit 1
    fi
  fi
  write_json "ok" 0 "${SUMMARY:-ok}" "$ENDED"
  if [[ -f "$JOB_DIR/activity.json" ]]; then
    rm -f "$LOG"
  fi
  exit 0
fi
write_json "fail" "$CHILD_RC" "${SUMMARY:-child exited $CHILD_RC}" "$ENDED"
exit "$CHILD_RC"
