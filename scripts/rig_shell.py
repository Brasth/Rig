#!/usr/bin/env python3
"""`rig shell`: the Rig board (rig-shell, shell/) as the design's one screen, in tmux or in this terminal.

By default tmux holds a single window with the board, which runs the parent CLI in its middle column, so the
session survives a detach and looks the same as `--no-tmux`. `--layout split` keeps the earlier arrangement: the
parent unchanged in its own native tmux pane, a narrow board and a log pane that follows the most relevant job.
This does not use the companion launcher, so no F8/F9 popups are bound in these sessions.
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


def parent_pane_plan(repo: Path, parent: str = "") -> dict:
    """The command the board runs for the parent CLI in an embedded pane, or why there is none. Decided here so the
    Go board never works out which CLI is the parent or where it lives. ``parent`` names one of PANE_PARENTS
    (from `rig shell --parent`); it is a name, never a command."""
    repo = Path(repo).resolve()
    try:
        argv = parent_command(repo, parent)
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


def check_parent_name(parent: str) -> None:
    """The board layout runs the parent through the server, so only a name it allows can be asked for. Whether that
    CLI is installed is the board's to report, so a missing one never stops the board from opening."""
    if parent and parent not in PANE_PARENTS:
        raise ShellError(f"parent {parent!r} cannot run in the board; use one of {', '.join(PANE_PARENTS)}")


def board_env(repo: Path, parent: str = "", theme: str = "", layout: str = "app") -> list[str]:
    """What the full-window board needs to open as the design's Session screen, with the conversation with the
    parent in its middle column (the chat, on for this board). ``parent`` is a name from PANE_PARENTS; ``theme`` draws
    this board in one of ui_theme.THEMES without saving it. ``layout`` "app" is the design's screens (one conversation,
    the agents panel on Tab, Ctrl-K), "board" the jobs board with the conversation in its middle column."""
    pairs = ["RIG_SHELL_START=Session", "RIG_EXPERIMENT_CHAT=1"]
    if layout == "app":
        pairs.append("RIG_SHELL_LAYOUT=app")
    if parent:
        pairs.append(f"RIG_SHELL_PARENT={parent}")
    if theme:
        import ui_theme
        if theme not in ui_theme.THEMES:
            raise ShellError(f"--theme is one of {', '.join(ui_theme.THEMES)}, not {theme}")
        pairs.append(f"RIG_SHELL_THEME={theme}")
    return pairs


def board_plan(repo: Path, shell_bin: str, parent: str = "", *, width: int = 200, height: int = 50, theme: str = "",
               layout: str = "app") -> list[list[str]]:
    """The default layout: one tmux window holding the board, which is the whole design screen (workspace, the
    parent in its middle column, jobs, evidence). tmux only hosts it, so the session survives a detach."""
    repo = Path(repo).resolve()
    session = session_name(repo)
    board = shlex.join(pane_env(repo) + board_env(repo, parent, theme, layout) + [shell_bin, "--repo", str(repo)])
    return [
        ["new-session", "-d", "-s", session, "-n", "rig", "-x", str(width), "-y", str(height), "-c", str(repo), board],
        ["set-option", "-t", session, "status", "off"],
    ]


def tmux(binary: str, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([binary, *server_args(), *args], capture_output=True, text=True, check=check)


def session_exists(binary: str, session: str) -> bool:
    return tmux(binary, ["has-session", "-t", session], check=False).returncode == 0


def layout_steps(repo: Path, layout: str, parent: str, parent_cmd: str, *, width: int = 200, height: int = 50,
                 theme: str = "") -> list[list[str]]:
    """The tmux commands for a layout, after checking the parent can run (so a bad --parent fails here, clearly)."""
    if layout == "split":
        return plan(repo, parent_command(repo, parent, parent_cmd), shell_binary(), width=width, height=height)
    check_parent_name(parent)
    if parent_cmd:
        raise ShellError("--parent-cmd runs an arbitrary command, which only the split layout's own pane can do; "
                         "use --layout split, or --parent NAME")
    return board_plan(repo, shell_binary(), parent, width=width, height=height, theme=theme, layout=layout)


def start(repo: Path, *, parent: str = "", parent_cmd: str = "", attach: bool = True, layout: str = "app", theme: str = "") -> str:
    repo = Path(repo).resolve()
    harness.assert_project_enabled(repo)
    binary = tmux_binary()
    session = session_name(repo)
    if not session_exists(binary, session):
        width, height = shutil.get_terminal_size((200, 50))
        steps = layout_steps(repo, layout, parent, parent_cmd, width=width, height=height, theme=theme)
        try:
            for step in steps:
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


def recent_path() -> Path:
    root = os.environ.get("RIG_HOME", "").strip()
    return (Path(root) if root else Path.home() / ".rig") / "recent.json"


def remember(repo: Path, keep: int = 20) -> None:
    """Put a project at the top of the launcher's recent list (~/.rig/recent.json). Never fails rig shell."""
    import json
    path = recent_path()
    try:
        rows = json.loads(path.read_text()) if path.exists() else []
        rows = [row for row in rows if isinstance(row, dict) and row.get("path") != str(repo)]
        rows.insert(0, {"path": str(repo), "at": time.time()})
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(rows[:keep], indent=1) + "\n")
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        pass


def launcher(start: Path) -> int:
    """The design's "Open a project", where there is no Rig project to open: rig-shell picks one, then reopens rig shell."""
    command = [shell_binary(), "--launcher", "--repo", str(start)]
    os.execvpe(command[0], command, {**os.environ, "RIG_BIN": rig_binary()})
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rig shell", description="The Rig board: the parent, workspace, jobs and evidence on one screen")
    parser.add_argument("command", nargs="?", choices=["start", "logs", "kill", "print"], default="start")
    parser.add_argument("--repo", default="")
    parser.add_argument("--parent", default="", help="parent CLI to run in the parent pane")
    parser.add_argument("--parent-cmd", default="", help="exact command for the parent pane (overrides --parent)")
    parser.add_argument("--theme", default="", help="draw this board in a theme without saving it: rig, light, dark, "
                                                        "material or dracula (the Settings tab saves one)")
    parser.add_argument("--detach", action="store_true", help="create the session without attaching")
    parser.add_argument("--no-tmux", action="store_true",
                        help="run the same board in this terminal without tmux (no detach)")
    parser.add_argument("--layout", choices=["app", "board", "split"], default="app",
                        help="app: the design's screens, one conversation with the agents panel on Tab (default); "
                             "board: the jobs board, the parent in its middle column; "
                             "split: the parent in its own native tmux pane beside a narrow board and a log pane")
    args = parser.parse_args(argv)
    try:
        import jobs
        repo = jobs.repo_root(args.repo or None)
        if args.command == "logs":
            return follow_logs(repo)
        if args.command == "kill":
            print("killed" if kill(repo) else "no rig shell session for this repository")
            return 0
        if args.command == "start" and args.layout == "app" and not harness.harness_path(repo).is_file():
            return launcher(repo)  # nothing set up here: open a project instead of failing
        if args.command == "start":
            remember(repo)
        if args.no_tmux and args.command in ("start", "print"):
            if args.parent_cmd:
                raise ShellError("--parent-cmd needs the split layout's tmux pane; use --parent NAME without tmux")
            check_parent_name(args.parent)  # a bad --parent fails here, before the screen is taken over
            command = [shell_binary(), "--repo", str(repo)]
            extra = board_env(repo, args.parent, args.theme, args.layout)
            if args.command == "print":
                print(shlex.join(pane_env(repo) + extra + command))
                return 0
            os.execvpe(command[0], command, {**os.environ, "RIG_SHELL_ACTIVE": "1", "RIG_REPO": str(repo), "RIG_BIN": rig_binary(),
                                             **dict(pair.split("=", 1) for pair in extra)})
        if args.command == "print":
            steps = layout_steps(repo, args.layout, args.parent, args.parent_cmd, theme=args.theme)
            print("\n".join(shlex.join(["tmux", *server_args(), *step]) for step in steps))
            return 0
        session = start(repo, parent=args.parent, parent_cmd=args.parent_cmd, attach=not args.detach, layout=args.layout,
                        theme=args.theme)
        print(f"rig shell: session {session} is ready (attach with: tmux {' '.join(server_args())} attach -t {session})".replace("  ", " "))
        return 0
    except (ShellError, ValueError, SystemExit) as error:
        print(f"rig shell: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
