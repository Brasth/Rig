#!/usr/bin/env python3
"""The board's Settings screen, as the design's Settings sections draw it: what can be changed from the board, its
current values, and the one place each is kept.

Only real settings are offered. The board's own (``[ui]`` theme and bell) are kept for all repos in
``~/.rig/config.toml`` or for this repo in ``.rig/harness.toml``; the project's (its parent, whether Rig is on, which
helpers it may use, how many at once, routing's objective, the sandbox and the worktrees) only in ``.rig/harness.toml``.
Nothing here creates a harness: a new one would turn Rig on for the project."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import harness
import ui_theme

OBJECTIVES = ("balanced", "quality", "cost", "speed")
_BOOL = ("true", "false")

# key -> (section, toml key, allowed values or None for a number range, where it may be kept)
SETTINGS = {
    "ui.theme": ("ui", "theme", ui_theme.THEMES, ("all", "repo")),
    "ui.bell": ("ui", "bell", _BOOL, ("all", "repo")),
    "project.enabled": ("project", "enabled", _BOOL, ("repo",)),
    "queue.max_running": ("queue", "max_running", tuple(str(n) for n in range(1, 9)), ("repo",)),
    "routing.objective": ("routing", "objective", OBJECTIVES, ("repo",)),
    "sandbox.provider": ("sandbox", "provider", ("devcontainer", "host"), ("repo",)),
    "worktrees.mode": ("worktrees", "mode", ("per-job", "off"), ("repo",)),
    "worktrees.container": ("worktrees", "container", ("shared", "per-job"), ("repo",)),
    # the board's own, from the design's Settings: what `rig shell` opens outside a project, what ship offers first,
    # the code view's tab width, and which events reach you and how
    "ui.start": ("ui", "start", ("last", "picker"), ("all",)),
    "ui.ship": ("ui", "ship", ("pr", "push"), ("all", "repo")),
    "ui.tab_size": ("ui", "tab_size", ("2", "4", "8"), ("all",)),
    "ui.notify_needs": ("ui", "notify_needs", _BOOL, ("all", "repo")),
    "ui.notify_applied": ("ui", "notify_applied", _BOOL, ("all", "repo")),
    "ui.desktop": ("ui", "desktop", _BOOL, ("all", "repo")),
}
for _name in harness.WORKERS:
    SETTINGS[f"workers.{_name}"] = ("workers", _name, _BOOL, ("repo",))


class SettingsError(ValueError):
    pass


def _read_key(path: Path, section: str, key: str) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]") and not line.startswith("[["):
            current = line[1:-1].strip()
            continue
        if current != section or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.split("#", 1)[0].strip().strip('"\'').lower()
    return ""


def write_key(path: Path, section: str, key: str, raw: str) -> None:
    """Set ``key = raw`` in ``[section]`` of a TOML file (raw is already TOML: a quoted string, a number, a boolean),
    keeping every other line; the table is added at the end when the file has none."""
    lines = path.read_text().splitlines() if path.exists() else []
    out, current, done = [], "", False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
            if current == section and not done:
                out.append(f"{key} = {raw}")
                done = True
            current = stripped[1:-1].strip()
        elif current == section and not stripped.startswith("#") and stripped.partition("=")[0].strip() == key:
            if not done:
                out.append(f"{key} = {raw}")
                done = True
            continue
        out.append(line)
    if not done:
        if current == section:
            out.append(f"{key} = {raw}")
        else:
            if out and out[-1].strip():
                out.append("")
            out += [f"[{section}]", f"{key} = {raw}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(out) + "\n")
    os.replace(temporary, path)


def _raw(section: str, value: str) -> str:
    if value in _BOOL or value.isdigit():
        return value
    return f'"{value}"'


def read(repo) -> dict:
    """Every value the Settings screen shows, and where the board's own ones come from."""
    repo = Path(repo).resolve()
    path = harness.harness_path(repo)
    parsed = harness.parse_harness(path)
    theme = ui_theme.read(repo)
    bell_here = _read_key(path, "ui", "bell")
    bell_all = _read_key(ui_theme.global_path(), "ui", "bell")
    installed = sorted(name for name in harness.WORKERS if shutil.which(name))
    try:
        text = path.read_text(errors="replace")
    except OSError:
        text = ""
    problems = [message for message in (parsed["project"].get("error"), parsed["sandbox"].get("error"),
                                        parsed["worktrees"].get("error"), parsed["mcp"].get("error")) if message]
    return {
        "theme": theme["theme"], "theme_scope": theme["scope"], "themes": list(ui_theme.THEMES),
        "bell": (bell_here or bell_all) == "true", "bell_scope": "repo" if bell_here else ("all" if bell_all else "default"),
        "parent": parsed["parent"], "enabled": bool(parsed["project"].get("enabled")),
        "workers": {name: str(value).lower() == "true" for name, value in parsed["workers"].items()},
        "installed": installed,
        "max_running": int(parsed["queue"]["max_running"]),
        "objective": parsed["routing"]["objective"] or "balanced", "routing_mode": parsed["routing"]["mode"] or "smart",
        "sandbox": parsed["sandbox"]["provider"], "worktrees": parsed["worktrees"]["mode"],
        "worktree_container": parsed["worktrees"]["container"],
        "harness": ".rig/harness.toml", "harness_text": text[:20000], "problems": problems,
        "global": str(ui_theme.global_path()).replace(str(Path.home()), "~"),
        "editor": os.environ.get("VISUAL") or os.environ.get("EDITOR") or "", "shell": os.environ.get("SHELL", ""),
        "start": _ui(repo, "start", "picker"), "ship": _ui(repo, "ship", "pr"), "tab_size": int(_ui(repo, "tab_size", "4")),
        "notify_needs": _ui(repo, "notify_needs", "true") == "true", "notify_applied": _ui(repo, "notify_applied", "false") == "true",
        "desktop": _ui(repo, "desktop", "false") == "true",
        "routing_table": _routing_table(parsed, installed, parsed["parent"] or "claude"),
        "models": {name: (route_model(name) or "auto") for name in harness.WORKERS},
        "runs_today": _runs_today(repo),
        **_facts(repo),
    }


def route_model(worker: str) -> str:
    """The model routing gives a helper for ordinary work."""
    try:
        import route
        return route.model_for(worker, "implement")[0]
    except Exception:
        return ""


def _ui(repo: Path, key: str, default: str) -> str:
    """A [ui] value: this repo's, else the one kept for all repos, else the default."""
    return (_read_key(harness.harness_path(repo), "ui", key) or _read_key(ui_theme.global_path(), "ui", key)
            or default)


# the design's "Who does what" rows: a task as you would say it, and the kind routing gives it
ROUTING_TASKS = (("plan and review", "review"), ("features", "implement"), ("hard changes", "hard"), ("tests and checks", "verify"),
                 ("small fixes, docs", "mini"), ("looking around", "explore"))


def _routing_table(parsed: dict, installed: list[str], parent: str) -> list[dict]:
    """Who routing picks for each kind of task with this project's helpers, and the model it gives them."""
    try:
        import route
    except Exception:
        return []
    enabled = [name for name, on in parsed["workers"].items() if str(on).lower() == "true" and name in installed]
    rows = []
    for task, kind in ROUTING_TASKS:
        try:
            worker, spawn = route.choose_worker(kind, enabled, parent)
        except Exception:
            continue
        if spawn in ("stay", "native") or not worker:
            worker, spawn = parent, "stay"
        model = route.model_for(worker, kind)[0] if spawn != "stay" else ""
        rows.append({"task": task, "kind": kind, "worker": worker, "parent": spawn == "stay", "model": model or "auto"})
    return rows


def _facts(repo: Path) -> dict:
    """What the Settings screen shows but does not change: versions, the GitHub account gh uses, where clones go, and
    whether this repo signs its commits."""
    version = ""
    for path in (Path(os.environ.get("RIG_HOME") or Path.home() / ".rig") / "VERSION", Path(__file__).resolve().parents[1] / "VERSION"):
        try:
            version = path.read_text().strip()
            break
        except OSError:
            continue
    try:
        import dc_client
        dc = dc_client.version()
    except Exception:
        dc = ""
    gh_user = ""
    hosts = Path(os.environ.get("GH_CONFIG_DIR") or Path.home() / ".config" / "gh") / "hosts.yml"
    try:
        in_github = False
        for line in hosts.read_text(errors="replace").splitlines():
            if line and not line.startswith(" "):
                in_github = line.strip().rstrip(":") == "github.com"
            elif in_github and line.strip().startswith("user:"):
                gh_user = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    sign = ""
    try:
        import subprocess
        sign = subprocess.run(["git", "-C", str(repo), "config", "--get", "commit.gpgsign"], capture_output=True, text=True,
                              timeout=5).stdout.strip().lower()
    except Exception:
        pass
    clone = os.environ.get("RIG_CODE_DIR") or str(Path.home() / "code")
    return {"rig_version": version, "dc_version": dc, "gh_user": shutil.which("gh") and gh_user or "", "gh": bool(shutil.which("gh")),
            "clone_dir": clone.replace(str(Path.home()), "~"), "sign_commits": sign == "true"}


def _runs_today(repo: Path) -> int:
    import time
    local = time.localtime()
    midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    count = 0
    for meta in (repo / ".rig" / "jobs").glob("*/meta.json"):
        try:
            if meta.stat().st_mtime >= midnight:
                count += 1
        except OSError:
            continue
    return count


def save(repo, key: str, value, scope: str = "repo") -> dict:
    """Change one setting. Values are checked against the setting's own list; nothing else is ever written."""
    key = str(key or "")
    if key not in SETTINGS:
        raise SettingsError(f"{key or 'that'} is not a setting the board changes")
    section, name, allowed, scopes = SETTINGS[key]
    value = str(value).strip().lower() if not isinstance(value, bool) else ("true" if value else "false")
    if value not in allowed:
        raise SettingsError(f"{key} is one of {', '.join(allowed)}, not {value or 'empty'}")
    if scope not in scopes:
        raise SettingsError(f"{key} is kept in {' or '.join(scopes)}, not {scope}")
    repo = Path(repo).resolve()
    if scope == "all":
        path = ui_theme.global_path()
    else:
        path = harness.harness_path(repo)
        if not path.is_file():
            raise SettingsError("this project has no .rig/harness.toml; run rig init first")
    write_key(path, section, name, _raw(section, value))
    return read(repo)
