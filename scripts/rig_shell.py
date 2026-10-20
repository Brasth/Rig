#!/usr/bin/env python3
"""`rig shell`: lay out the parent agent, the board and a log pane in tmux.

Rig owns the layout, not the parent: the parent CLI runs unchanged in its own
pane. The board pane is rig-shell (shell/), the log pane follows the most
relevant job through the local API. This does not use the companion launcher,
so no F8/F9 popups are bound in these sessions.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harness  # noqa: E402

ROOT = HERE.parent
# Parents that are command-line programs and can live in a pane. Cursor is a
# desktop host; Devin has no pane contract here.
PANE_PARENTS = ("codex", "grok", "claude", "opencode", "omp", "pi", "agy")
PARENT_PANE_PERCENT, LOG_PANE_PERCENT = 40, 25


class ShellError(ValueError):
    """The shell cannot be started; the message says what to do."""


def tmux_binary() -> str:
    binary = shutil.which("tmux")
    if not binary:
        raise ShellError("tmux 3.3+ is required for the full layout; rig shell --no-tmux runs the board and logs in this terminal without it")
    try:
        import re
        out = subprocess.run([binary, "-V"], capture_output=True, text=True, timeout=5).stdout
        match = re.search(r"tmux (\d+)\.(\d+)", out)
        if not match or tuple(map(int, match.groups())) < (3, 3):
            raise ShellError(f"tmux 3.3+ is required for rig shell (found {out.strip() or 'unknown'})")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ShellError(f"could not run tmux: {error}") from error
    return binary


def shell_binary() -> str:
    """The rig-shell executable: env override, installed copy, PATH, then a local build."""
    candidates = [os.environ.get("RIG_SHELL_BIN", ""),
                  str(Path(os.environ.get("RIG_HOME") or Path.home() / ".rig") / "bin" / "rig-shell"),
                  shutil.which("rig-shell") or "",
                  str(ROOT / "shell" / "bin" / "rig-shell")]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise ShellError("rig-shell is not built; run: (cd shell && go build -o bin/rig-shell ./cmd/rig-shell)")


def core_version() -> str:
    """The commit the installed runtime was built from (RIG_HOME/VERSION is "v1 <commit>")."""
    try:
        parts = (Path(os.environ.get("RIG_HOME") or Path.home() / ".rig") / "VERSION").read_text().split()
    except OSError:
        return ""
    return parts[1] if len(parts) > 1 else ""


def shell_status() -> dict:
    """Is rig-shell present, and was it built from the same commit as the installed core?"""
    try:
        path = shell_binary()
    except ShellError:
        return {"found": False, "path": "", "version": "", "core": core_version(), "state": "missing"}
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL).stdout.split()
        version = out[1] if len(out) > 1 else ""
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    core = core_version()
    if not version:
        state = "unreadable"
    elif version == "dev" or not core:
        state = "unverified"  # a local build or a runtime with no recorded commit
    else:
        state = "match" if version.startswith(core) or core.startswith(version) else "mismatch"
    return {"found": True, "path": path, "version": version, "core": core, "state": state}


def rig_binary() -> str:
    return os.environ.get("RIG_BIN") or str(ROOT / "bin" / "rig")


def parent_command(repo: Path, parent: str = "", override: str = "") -> list[str]:
    if override:
        return shlex.split(override)
    name = parent or harness.preferred_parent(repo)
    if not name:
        raise ShellError('no parent set; pass --parent NAME or add parent = "NAME" above the first section of .rig/harness.toml')
    if name not in PANE_PARENTS:
        raise ShellError(f"parent {name!r} cannot run in a pane; use one of {', '.join(PANE_PARENTS)}")
    binary = harness.find_worker_bin(name)
    if not binary:
        raise ShellError(f"parent CLI {name!r} is not on PATH")
    return [binary]


def pane_env_pairs(repo: Path) -> list[str]:
    """The extra environment the board gives a program it runs in a pane (KEY=VALUE)."""
    return ["RIG_SHELL_ACTIVE=1", f"RIG_REPO={Path(repo).resolve()}", f"RIG_BIN={rig_binary()}"]


def parent_pane_plan(repo: Path) -> dict:
    """The command the board runs for the parent CLI in an embedded pane, or why there is none. Decided here so the
    Go board never works out which CLI is the parent or where it lives."""
    repo = Path(repo).resolve()
    try:
        argv = parent_command(repo)
    except ShellError as error:
        return {"available": False, "reason": str(error), "argv": [], "dir": str(repo), "env": [], "label": "parent"}
    return {"available": True, "reason": "", "argv": argv, "dir": str(repo), "env": pane_env_pairs(repo), "label": Path(argv[0]).name}


def session_name(repo: Path) -> str:
    return "rig-shell-" + hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()[:8]


def server_args(env=None) -> list[str]:
    """Own private server unless already inside tmux; RIG_SHELL_TMUX_SOCKET isolates tests."""
    env = os.environ if env is None else env
    if env.get("RIG_SHELL_TMUX_SOCKET"):
        return ["-L", env["RIG_SHELL_TMUX_SOCKET"], "-f", "/dev/null"]
    return [] if env.get("TMUX") else ["-L", "rig-shell", "-f", "/dev/null"]


def pane_env(repo: Path) -> list[str]:
    return ["env", "RIG_SHELL_ACTIVE=1", f"RIG_REPO={repo}", f"RIG_BIN={rig_binary()}"]


def plan(repo: Path, parent_cmd: list[str], shell_bin: str, *, width: int = 200, height: int = 50) -> list[list[str]]:
    """The tmux commands that build the layout. Pure: nothing is executed here."""
    repo = Path(repo).resolve()
    session = session_name(repo)
    target = f"{session}:rig"
    env = pane_env(repo)
    parent = shlex.join(env + parent_cmd)
    board = shlex.join(env + [shell_bin, "--repo", str(repo)])
    logs = shlex.join(env + [sys.executable, str(HERE / "rig_shell.py"), "logs", "--repo", str(repo)])
    return [
        ["new-session", "-d", "-s", session, "-n", "rig", "-x", str(width), "-y", str(height), "-c", str(repo), parent],
        ["split-window", "-h", "-t", f"{target}.0", "-l", f"{PARENT_PANE_PERCENT}%", "-c", str(repo), board],
        ["split-window", "-v", "-t", f"{target}.1", "-l", f"{LOG_PANE_PERCENT}%", "-c", str(repo), logs],
        ["select-pane", "-t", f"{target}.0", "-T", "parent"],
        ["select-pane", "-t", f"{target}.1", "-T", "board"],
        ["select-pane", "-t", f"{target}.2", "-T", "log"],
        ["set-option", "-t", session, "mouse", "on"],
        ["set-option", "-t", session, "status", "off"],
        ["set-option", "-t", target, "pane-border-status", "top"],
        ["set-option", "-t", target, "pane-border-format", " #{pane_title} "],
        ["select-pane", "-t", f"{target}.0"],
    ]


def tmux(binary: str, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([binary, *server_args(), *args], capture_output=True, text=True, check=check)


def session_exists(binary: str, session: str) -> bool:
    return tmux(binary, ["has-session", "-t", session], check=False).returncode == 0


def start(repo: Path, *, parent: str = "", parent_cmd: str = "", attach: bool = True) -> str:
    repo = Path(repo).resolve()
    harness.assert_project_enabled(repo)
    binary = tmux_binary()
    session = session_name(repo)
    if not session_exists(binary, session):
        command = parent_command(repo, parent, parent_cmd)
        board = shell_binary()
        width, height = shutil.get_terminal_size((200, 50))
        try:
            for step in plan(repo, command, board, width=width, height=height):
                tmux(binary, step)
        except subprocess.CalledProcessError as error:
            tmux(binary, ["kill-session", "-t", session], check=False)
            raise ShellError(f"tmux failed: {' '.join(error.cmd[-3:])}: {error.stderr.strip()}") from error
    if attach:
        verb = ["switch-client"] if os.environ.get("TMUX") else ["attach-session"]
        os.execvp(binary, [binary, *server_args(), *verb, "-t", session])
    return session


def kill(repo: Path) -> bool:
    binary = tmux_binary()
    session = session_name(Path(repo).resolve())
    if not session_exists(binary, session):
        return False
    tmux(binary, ["kill-session", "-t", session])
    return True


# ---- log pane -------------------------------------------------------------

_LIVE = ("Needs input", "Running", "Checking", "Reserved")


def pick_job(rows: list[dict]) -> dict | None:
    """The job worth watching: first needing input, then running, else newest."""
    for label in _LIVE:
        for row in rows:
            if row.get("label") == label:
                return row
    return rows[0] if rows else None


def log_frame(repo: Path, lines: int = 30) -> str:
    import local_api
    try:
        snap = local_api.call(repo, "board.snapshot", {"rows": 50}, timeout=3)
        if "error" in snap:
            return "log: " + snap["error"]["message"]
        job = pick_job(snap["result"]["view"]["jobs"])
        if job is None:
            return "No jobs yet. Output of the active job appears here."
        got = local_api.call(repo, "jobs.log", {"id": job["id"], "lines": lines}, timeout=3)
        body = got.get("result", {}).get("text") or got.get("error", {}).get("message", "")
        return f"{job['label']} · {job['id']} · {job['worker']}\n{'─' * 40}\n{body}"
    except (ConnectionError, OSError, ValueError, KeyError):
        return "Rig API is not running; the board pane starts it."


def follow_frame(repo: Path, job_id: str = "", lines: int = 60) -> dict:
    """The tail of one job's log as plain text, for the board's Logs tab. With no id: the job worth watching."""
    repo = Path(repo).resolve()
    label = worker = ""
    if not job_id:
        import board_view
        import tui_runtime
        import ui_snapshot
        snap = tui_runtime.load_snapshot(repo, start=0, rows=50, selected_id=None)
        view = board_view.build(snap.jobs, snap.pending, ui_snapshot.collect_workflows(repo), snap.slots, snap.cap, repo)
        job = pick_job(view["jobs"])
        if job is None:
            return {"job": None, "text": "", "error": False}
        job_id, label, worker = job["id"], job["label"], job["worker"]
    import local_api
    import rig_mcp
    outcome = rig_mcp.call_tool("rig_job_log", {"id": job_id, "repo": str(repo), "lines": lines})
    text = "\n".join(part.get("text", "") for part in outcome.get("content", []) if isinstance(part, dict))
    return {"job": {"id": job_id, "label": label, "worker": worker}, "text": local_api.scrub(text),
            "error": bool(outcome.get("isError"))}


def follow_logs(repo: Path, interval: float = 2.0) -> int:
    import local_api
    try:
        local_api.ensure(repo)
    except OSError:
        pass
    last = None
    try:
        while True:
            frame = log_frame(repo)
            if frame != last:
                sys.stdout.write("\x1b[2J\x1b[H" + frame + "\n")
                sys.stdout.flush()
                last = frame
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rig shell", description="Parent, board and logs in one tmux layout")
    parser.add_argument("command", nargs="?", choices=["start", "logs", "kill", "print"], default="start")
    parser.add_argument("--repo", default="")
    parser.add_argument("--parent", default="", help="parent CLI to run in the parent pane")
    parser.add_argument("--parent-cmd", default="", help="exact command for the parent pane (overrides --parent)")
    parser.add_argument("--detach", action="store_true", help="create the session without attaching")
    parser.add_argument("--no-tmux", action="store_true",
                        help="run the board (with its Logs tab and dc exec shell) in this terminal; no parent pane, no tmux")
    args = parser.parse_args(argv)
    try:
        import jobs
        repo = jobs.repo_root(args.repo or None)
        if args.command == "logs":
            return follow_logs(repo)
        if args.command == "kill":
            print("killed" if kill(repo) else "no rig shell session for this repository")
            return 0
        if args.no_tmux and args.command in ("start", "print"):
            command = [shell_binary(), "--repo", str(repo)]
            if args.command == "print":
                print(shlex.join(pane_env(repo) + ["RIG_SHELL_START=Session"] + command))
                return 0
            # Without tmux there is no parent pane beside the board, so it opens on the Session tab, where the parent runs.
            os.execvpe(command[0], command, {**os.environ, "RIG_SHELL_ACTIVE": "1", "RIG_REPO": str(repo), "RIG_BIN": rig_binary(),
                                             "RIG_SHELL_START": "Session"})
        if args.command == "print":
            steps = plan(repo, parent_command(repo, args.parent, args.parent_cmd), shell_binary())
            print("\n".join(shlex.join(["tmux", *server_args(), *step]) for step in steps))
            return 0
        session = start(repo, parent=args.parent, parent_cmd=args.parent_cmd, attach=not args.detach)
        print(f"rig shell: session {session} is ready (attach with: tmux {' '.join(server_args())} attach -t {session})".replace("  ", " "))
        return 0
    except (ShellError, ValueError, SystemExit) as error:
        print(f"rig shell: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
