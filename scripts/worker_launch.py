#!/usr/bin/env python3
"""Parent-only wrapper launch: admit, write the brief, detach installed run-worker.sh."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import admission  # noqa: E402
import child_mcp  # noqa: E402
import harness as rig_harness  # noqa: E402
import jobs as rig_jobs  # noqa: E402
import route as rig_route  # noqa: E402

JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
LAUNCH_WORKERS = frozenset({"grok", "codex", "claude", "opencode", "omp", "pi", "agy"})
LAUNCH_KEYS = frozenset({
    "id", "case", "role", "worker", "model", "effort", "access", "files", "brief",
    "queue_id", "reservation_id", "attempt_id", "owner_token", "owner_session",
    "writer_job_id", "writer_snapshot_id", "writer_cli", "writer_model",
    "writer_provider", "review_mode", "live",
})
WRAPPER_ENV = (
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
    "RIG_HOME", "RIG_PARENT", "RIG_SKIP_MODEL_CATALOG", "RIG_SKIP_UPDATE_CHECK",
    "RIG_THREAD", "RIG_OWNER_SESSION", "GROK_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
    "OPENCODE_CONFIG", "OMP_MCP", "PI_CODING_AGENT_DIR", "PI_AGENT_DIR", "AGY_MCP",
)
TERMINAL_STATUSES = frozenset({"ok", "fail", "timeout", "cancelled"})
STRING_LAUNCH_KEYS = LAUNCH_KEYS - {"files"}


class LaunchError(ValueError):
    pass


def installed_wrapper() -> Path:
    home = Path(os.environ.get("RIG_HOME") or (Path.home() / ".rig")).expanduser().resolve()
    path = (home / "scripts" / "run-worker.sh").resolve()
    if not path.is_file():
        raise LaunchError(f"installed run-worker.sh missing: {path}")
    try:
        path.relative_to(home)
    except ValueError as error:
        raise LaunchError("wrapper path escaped install root") from error
    return path


def _require_string(value, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LaunchError(f"{name} must be a string")
    if "\0" in value:
        raise LaunchError(f"{name} contains NUL")
    return value


def _job_id(raw: str) -> str:
    job_id = (raw or "").strip() or rig_jobs.new_job_id()
    if job_id in {".", ".."} or not JOB_ID_RE.fullmatch(job_id):
        raise LaunchError(f"invalid job id '{job_id}'")
    return job_id


def _files(files) -> list[str]:
    if files is None:
        return []
    if not isinstance(files, list) or any(not isinstance(item, str) or not item or "\0" in item for item in files):
        raise LaunchError("files must be a JSON array of nonempty paths")
    return files


def _access(role: str, access: str) -> str:
    value = (access or "").strip() or ("read" if role.lower() in {"review", "reviewer", "explore", "explorer"} else "write")
    if value not in {"read", "write"}:
        raise LaunchError("access must be read|write")
    return value


def _wrapper_env(extra: dict[str, str]) -> dict[str, str]:
    env = {}
    for key in WRAPPER_ENV:
        if key in os.environ and os.environ[key] != "":
            env[key] = os.environ[key]
    env.update(extra)
    env.pop("LD_PRELOAD", None)
    env.pop("DYLD_INSERT_LIBRARIES", None)
    return env


def _append_log(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        data = text if text.endswith("\n") else text + "\n"
        stream.write(data.encode("utf-8", errors="replace"))
        stream.flush()
        os.fsync(stream.fileno())


def _drop(repo, record, owner, rationale: str):
    try:
        admission.release(
            repo,
            reservation_id=record["reservation_id"],
            attempt_id=record["attempt_id"],
            owner_token=record["owner_token"],
            owner=owner,
            rationale=rationale,
            mode="launch_failed",
        )
    except (admission.AdmissionError, OSError, TypeError, ValueError):
        pass


def _fail_job(job_dir: Path, job_id: str, worker: str, role: str, summary: str, record=None, **fields) -> None:
    now = rig_jobs.iso_now()
    rig_jobs.write_job_files(
        job_dir, job_id, worker, role, "fail", 1, now, now, summary,
        kind="wrapper", executor_kind="wrapper", execution_mode="not_started",
        reservation=record, capture_evidence=False, **{k: v for k, v in fields.items() if k in {"model", "effort", "files", "writer_job_id", "writer_snapshot_id"}},
    )
    result = job_dir / "result.json"
    meta = rig_jobs._read_meta_dict(job_dir)
    tmp = result.with_name(result.name + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2) + "\n")
    tmp.replace(result)


def _result_status(job_dir: Path) -> str:
    path = Path(job_dir) / "result.json"
    if not path.is_file():
        return ""
    try:
        obj = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ""
    if not isinstance(obj, dict):
        return ""
    status = str(obj.get("status") or "")
    return status if status in TERMINAL_STATUSES else ""


def _attach_wrapper_pid(job_dir: Path, pid: int) -> str:
    """Write wrapper.pid sidecar and return observed status only (no post-spawn meta patch)."""
    Path(job_dir).mkdir(parents=True, exist_ok=True)
    (Path(job_dir) / "wrapper.pid").write_text(str(int(pid)) + "\n")
    meta = rig_jobs._read_meta_dict(job_dir)
    status = str(meta.get("status") or "")
    if status in TERMINAL_STATUSES:
        return status
    done = _result_status(job_dir)
    if done:
        return done
    return status or "running"


def _spawn_wrapper(argv: list[str], *, cwd: str, env: dict[str, str], log) -> subprocess.Popen:
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def launch(repo, **kwargs) -> dict:
    unknown = sorted(set(kwargs) - LAUNCH_KEYS)
    if unknown:
        raise LaunchError(f"unknown launch argument: {unknown[0]}")
    for key in STRING_LAUNCH_KEYS:
        if key in kwargs:
            _require_string(kwargs.get(key), key)
    if "files" in kwargs:
        _files(kwargs.get("files"))
    repo = rig_jobs.repo_root(repo)
    harness_file = rig_harness.harness_path(repo)
    if not harness_file.is_file():
        raise LaunchError(f"missing {harness_file} — run: rig init")
    brief = _require_string(kwargs.get("brief"), "brief")
    if not brief.strip():
        raise LaunchError("brief is required")
    case = _require_string(kwargs.get("case"), "case")
    role = _require_string(kwargs.get("role"), "role").strip() or rig_route.classify("", case)
    worker = _require_string(kwargs.get("worker"), "worker").strip()
    model = _require_string(kwargs.get("model"), "model").strip()
    effort = _require_string(kwargs.get("effort"), "effort").strip()
    live_parent = kwargs.get("live") if kwargs.get("live") is not None else rig_harness.live_parent()
    if live_parent is not None and not isinstance(live_parent, str):
        raise LaunchError("live must be a string")
    live_parent = (live_parent or "").strip()
    effective = rig_harness.effective_workers(repo, live_parent)
    if not worker:
        choice = rig_route.pick(
            live_parent, effective, role, case,
            writer_job_id=_require_string(kwargs.get("writer_job_id"), "writer_job_id"),
            writer_cli=_require_string(kwargs.get("writer_cli"), "writer_cli"),
            writer_model=_require_string(kwargs.get("writer_model"), "writer_model"),
            writer_provider=_require_string(kwargs.get("writer_provider"), "writer_provider"),
            review_mode=_require_string(kwargs.get("review_mode"), "review_mode").strip() or "standalone",
            repo=repo,
        )
        if choice.get("spawn") != "run-worker" or not choice.get("worker"):
            raise LaunchError(choice.get("reason") or "no eligible wrapper worker")
        worker = choice["worker"]
        if not model:
            model, effort = choice.get("model") or "", choice.get("effort") or ""
    if worker == "cursor":
        raise LaunchError(child_mcp.CURSOR_REASON)
    if worker not in LAUNCH_WORKERS:
        raise LaunchError("worker must be grok|codex|claude|opencode|omp|pi|agy")
    ready, reason = child_mcp.worker_mcp_ready(worker)
    if not ready:
        raise LaunchError(reason)
    try:
        rig_harness.assert_spawn_allowed(repo, worker, live_parent)
    except SystemExit as error:
        raise LaunchError(str(error) or "worker is off") from error
    if worker == live_parent:
        raise LaunchError(f"worker '{worker}' equals live parent")
    if not rig_harness.find_worker_bin(worker):
        raise LaunchError(f"binary '{worker}' not on PATH")
    if worker not in effective:
        raise LaunchError(f"worker '{worker}' is not effective for this parent")
    if not model:
        model, effort = rig_route.resolved_model_for(worker, rig_route.classify(role, case))
    blocked = rig_route.assert_child_model(model)
    if blocked:
        raise LaunchError(blocked)
    job_id = _job_id(_require_string(kwargs.get("id"), "id"))
    listed = _files(kwargs.get("files"))
    access = _access(role, _require_string(kwargs.get("access"), "access"))
    review_mode = _require_string(kwargs.get("review_mode"), "review_mode").strip() or "standalone"
    if review_mode not in {"standalone", "independent"}:
        raise LaunchError("review_mode must be standalone|independent")
    writer_job_id = _require_string(kwargs.get("writer_job_id"), "writer_job_id")
    writer_snapshot_id = _require_string(kwargs.get("writer_snapshot_id"), "writer_snapshot_id")
    writer_cli = _require_string(kwargs.get("writer_cli"), "writer_cli")
    writer_model = _require_string(kwargs.get("writer_model"), "writer_model")
    writer_provider = _require_string(kwargs.get("writer_provider"), "writer_provider")
    if review_mode == "independent" or writer_job_id or role.lower() in {"review", "reviewer"}:
        try:
            context, problem = rig_route._writer_context(
                writer_job_id=writer_job_id, writer_cli=writer_cli, writer_model=writer_model,
                writer_provider=writer_provider, review_mode=review_mode, repo=repo,
            )
        except ValueError as error:
            raise LaunchError(str(error)) from error
        if review_mode == "independent" and problem:
            raise LaunchError(problem)
        independence, rejection = rig_route._review_model(model, context)
        if rejection and review_mode == "independent":
            raise LaunchError(rejection)
        writer_snapshot_id = writer_snapshot_id or context.get("writer_snapshot_id") or ""
        writer_cli = writer_cli or context.get("writer_cli") or ""
        writer_model = writer_model or context.get("writer_model") or ""
        writer_provider = writer_provider or context.get("writer_provider") or ""
    wrapper = installed_wrapper()
    owner_session = _require_string(kwargs.get("owner_session"), "owner_session").strip()
    if not owner_session:
        owner_session = next((os.environ.get(key, "") for key in (
            "RIG_OWNER_SESSION", "RIG_THREAD", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "GROK_SESSION_ID",
        ) if os.environ.get(key)), "") or f"launch-{job_id}"
    owner = admission.caller_owner("parent", owner_session=owner_session)
    queue_id = _require_string(kwargs.get("queue_id"), "queue_id")
    reservation_id = _require_string(kwargs.get("reservation_id"), "reservation_id")
    attempt_id = _require_string(kwargs.get("attempt_id"), "attempt_id")
    owner_token = _require_string(kwargs.get("owner_token"), "owner_token")
    job_dir = repo / ".rig" / "jobs" / job_id
    launcher_log = job_dir / "launcher.log"
    brief_path = job_dir / "brief.md"
    record = None
    credentials = None

    def abort_setup(message: str, error: BaseException) -> None:
        try:
            _append_log(launcher_log, message)
            _fail_job(job_dir, job_id, worker, role, message, record=record, model=model, effort=effort, files=listed)
        except OSError:
            pass
        if record is not None:
            _drop(repo, record, owner, message)
        raise LaunchError(message) from error

    with admission.transaction(repo):
        try:
            record = admission.reserve(
                repo, job_id=job_id, worker=worker, role=role, model=model, files=listed,
                access=access, owner=owner, owner_session=owner_session,
                queue_id=queue_id, reservation_id=reservation_id, attempt_id=attempt_id,
                owner_token=owner_token, writer_job_id=writer_job_id,
                writer_snapshot_id=writer_snapshot_id,
            )
        except admission.AdmissionError as error:
            raise LaunchError(str(error)) from error
        listed = record.get("declared_files", listed)
        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            credentials = admission.write_credentials(repo, record)
            tmp = job_dir / "brief.md.tmp"
            tmp.write_text(brief if brief.endswith("\n") else brief + "\n")
            tmp.replace(brief_path)
            now = rig_jobs.iso_now()
            rig_jobs.write_job_files(
                job_dir, job_id, worker, role, "running", 0, now, "", "",
                kind="wrapper", thread=rig_jobs.current_thread(repo), files=listed,
                model=model, effort=effort, executor_kind="wrapper", execution_mode="live",
                writer_job_id=writer_job_id, writer_snapshot_id=writer_snapshot_id,
                reservation=record, capture_evidence=False,
            )
            child_mcp.mark_unknown(job_dir)
            spec = child_mcp.write_job_mcp(job_dir, job_id, repo, worker)
            if not spec["ready"]:
                raise LaunchError(spec["reason"] or "worker MCP is not ready")
            stdout_log = job_dir / "stdout.log"
            if not stdout_log.exists():
                stdout_log.write_text("")
        except FileExistsError as error:
            _drop(repo, record, owner, "job directory already exists")
            raise LaunchError("job directory already exists") from error
        except (OSError, LaunchError, SystemExit) as error:
            abort_setup(str(error) or "launch setup failed", error)
        extra = {
            "RIG_LIVE": "1",
            "RIG_ROLE": role,
            "RIG_MODEL": model,
            "RIG_EFFORT": effort,
            "RIG_ACCESS": access,
            "RIG_JOB_FILES_JSON": json.dumps(listed),
            "RIG_QUEUE_ID": record.get("queue_id") or "",
            "RIG_RESERVATION_ID": record["reservation_id"],
            "RIG_ATTEMPT_ID": record["attempt_id"],
            "RIG_OWNER_TOKEN": record["owner_token"],
            "RIG_OWNER_SESSION": owner_session,
            "RIG_WRITER_JOB_ID": writer_job_id,
            "RIG_WRITER_SNAPSHOT_ID": writer_snapshot_id,
            "RIG_WRITER_CLI": writer_cli,
            "RIG_WRITER_MODEL": writer_model,
            "RIG_WRITER_PROVIDER": writer_provider,
            "RIG_REVIEW_MODE": review_mode,
            "RIG_HOME": str(Path(os.environ.get("RIG_HOME") or (Path.home() / ".rig"))),
            "RIG_JOB_ID": job_id,
            "RIG_JOB_DIR": str(job_dir),
            "RIG_REPO": str(repo),
        }
        extra = {key: value for key, value in extra.items() if value != ""}
        env = _wrapper_env(extra)
        log = open(launcher_log, "ab")
        try:
            proc = _spawn_wrapper(
                [str(wrapper), worker, job_id, str(brief_path)],
                cwd=str(repo),
                env=env,
                log=log,
            )
        except OSError as error:
            log.close()
            message = f"could not start wrapper: {error}"
            abort_setup(message, error)
        log.close()
        status = _attach_wrapper_pid(job_dir, proc.pid)
        result = {
            "job_id": job_id,
            "worker": worker,
            "role": role,
            "wrapper_pid": proc.pid,
            "status": status,
            "reservation_id": record["reservation_id"],
            "attempt_id": record["attempt_id"],
            "credentials_path": str(credentials),
        }
    return result


def main(argv: list[str]) -> int:
    print("worker_launch.py is the MCP rig_job_launch helper; do not invoke it as a CLI", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
