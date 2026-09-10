#!/usr/bin/env python3
"""Live parent, harness flags, and effective workers (same rules as detect-binaries.sh)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

WORKERS = ("grok", "claude", "codex", "cursor", "opencode", "omp", "pi", "agy")
PARENTS = frozenset({"grok", "codex", "claude", "cursor", "opencode", "omp", "pi", "agy"})

# Missing harness file and missing keys are all false so pick cannot
# invent a Grok/Claude child. `rig init` still writes PATH-based flags.
_DEFAULT_WORKERS = {name: "false" for name in WORKERS}


def harness_path(repo: Path) -> Path:
    return repo / ".rig" / "harness.toml"


def parse_harness(path: Path) -> dict:
    out = {"parent": "codex", "workers": dict(_DEFAULT_WORKERS)}
    if not path.is_file():
        return out
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    section = ""
    for raw in text.splitlines():
        line = raw.split("\r", 1)[0]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
            section = stripped[1:-1]
            continue
        if "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        key = key.strip()
        val = val.split("#", 1)[0].strip()
        val = val.strip('"')
        if section == "" and key == "parent":
            out["parent"] = val
        elif section == "workers" and key in out["workers"]:
            out["workers"][key] = val
    return out


def preferred_parent(repo: Path) -> str:
    return parse_harness(harness_path(repo))["parent"]


def find_worker_bin(name: str) -> str:
    if name in {"grok", "claude", "codex", "opencode", "omp", "pi", "agy"}:
        return shutil.which(name) or ""
    if name != "cursor":
        return shutil.which(name) or ""
    path = shutil.which("cursor-agent")
    if path:
        return path
    path = shutil.which("agent")
    if not path:
        return ""
    try:
        real = os.readlink(path)
    except OSError:
        real = ""
    if "cursor-agent" in path or "cursor-agent" in real:
        return path
    return ""


def _ps_field(pid: int, field: str) -> str:
    try:
        proc = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return proc.stdout or ""


def _ps_comm(pid: int) -> str:
    return _ps_field(pid, "comm").replace(" ", "").strip()


def _ps_ppid(pid: int) -> str:
    return _ps_field(pid, "ppid").replace(" ", "").strip()


def _ps_command(pid: int) -> str:
    return _ps_field(pid, "command").rstrip("\n")


def _comm_parent(comm: str, pid: int) -> str:
    comm = comm.rsplit("/", 1)[-1]
    comm = comm.split(":", 1)[0]
    if comm == "grok" or comm.startswith("grok-"):
        return "grok"
    if comm == "codex" or comm.startswith("codex-"):
        return "codex"
    if comm == "claude" or comm.startswith("claude-"):
        return "claude"
    if comm == "cursor-agent" or comm.startswith("cursor-agent-"):
        return "cursor"
    if comm == "opencode" or comm.startswith("opencode-"):
        return "opencode"
    if comm == "omp" or comm.startswith("omp-"):
        return "omp"
    if comm == "pi" or comm.startswith("pi-"):
        return "pi"
    if comm == "agy" or comm.startswith("agy-"):
        return "agy"
    if comm == "agent" or comm.startswith("agent-"):
        if "cursor-agent" in _ps_command(pid):
            return "cursor"
    return ""


def live_parent(start_pid: int | None = None) -> str:
    forced = (os.environ.get("RIG_PARENT") or "").strip()
    if forced in PARENTS:
        return forced
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE") == "1":
        return "claude"
    pid = os.getppid() if start_pid is None else int(start_pid)
    for _ in range(8):
        if not pid or pid in (0, 1):
            break
        name = _comm_parent(_ps_comm(pid), pid)
        if name:
            return name
        raw = _ps_ppid(pid)
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            break
    return ""


def assert_spawn_allowed(repo: Path, worker: str, live: str | None = None) -> None:
    """Refuse a disabled child. Native same-CLI (worker == live parent) is allowed."""
    worker = (worker or "").strip()
    if not worker or worker == "parent":
        return
    if live is None:
        live = live_parent()
    live = (live or "").strip()
    if worker == live:
        return
    flags = parse_harness(harness_path(repo))["workers"]
    if flags.get(worker) == "true":
        return
    raise SystemExit(f"rig job: worker {worker} is off in harness")


def effective_workers(repo: Path, live: str | None = None) -> list[str]:
    if live is None:
        live = live_parent()
    flags = parse_harness(harness_path(repo))["workers"]
    out: list[str] = []
    for name in WORKERS:
        if flags.get(name) != "true":
            continue
        if not find_worker_bin(name):
            continue
        if name == live:
            continue
        out.append(name)
    return out


def format_status(repo: Path, live: str | None = None) -> str:
    import jobs as rig_jobs
    import memory as rig_memory

    if live is None:
        live = live_parent()
    harness = parse_harness(harness_path(repo))
    pref = harness["parent"]
    effs = effective_workers(repo, live)
    jobs_root = repo / ".rig" / "jobs"
    n_jobs = 0
    if jobs_root.is_dir():
        n_jobs = sum(1 for p in jobs_root.iterdir() if p.is_dir())
    lines = [
        f"parent live={live or '(none)'} preferred={pref}",
        f"effective: {' '.join(effs) if effs else 'none'}",
        f"jobs: {n_jobs}",
    ]
    thread = rig_jobs.current_thread(repo)
    if thread:
        lines.append(f"thread: {thread}")
    lines.append(f"memory: {rig_memory.fact_count(repo)} facts")
    state = repo / ".rig" / "STATE.md"
    if state.is_file():
        try:
            body = state.read_text(errors="replace")
        except OSError:
            body = ""
        marked = [
            ln
            for ln in body.splitlines()
            if ln.startswith("- last_job:")
            or ln.startswith("- worker:")
            or ln.startswith("- status:")
            or ln.startswith("- summary:")
        ]
        if marked:
            lines.append("state:")
            lines.extend(f"  {ln}" for ln in marked)
    lines.append(rig_jobs.format_table(rig_jobs.list_jobs(repo)))
    return "\n".join(lines)
