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
    }


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
