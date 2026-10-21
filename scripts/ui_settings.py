#!/usr/bin/env python3
"""The board's Settings screen, as the design's Settings sections draw it: what can be changed from the board, its
current values, and the one place each is kept.

Only real settings are offered. The board's own (``[ui]`` theme and bell) are kept for all repos in
``~/.rig/config.toml`` or for this repo in ``.rig/harness.toml``; the project's (its parent, whether Rig is on, which
helpers it may use, how many at once, routing's objective, the sandbox and the worktrees) only in ``.rig/harness.toml``.
Nothing here creates a harness: a new one would turn Rig on for the project."""
from __future__ import annotations

import json
import os
import re
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
    "ui.format_on_save": ("ui", "format_on_save", _BOOL, ("all",)),
    # the editor's keys that clash in some terminals: each one of a few chords, kept for all repos
    "keys.next_occurrence": ("keys", "next_occurrence", ("ctrl+d", "ctrl+n", "alt+d"), ("all",)),
    "keys.permission_mode": ("keys", "permission_mode", ("shift+tab", "alt+m", "ctrl+y"), ("all",)),
    "keys.find_in_chat": ("keys", "find_in_chat", ("ctrl+/", "ctrl+f", "alt+f"), ("all",)),
    "keys.terminal": ("keys", "terminal", ("ctrl+`", "ctrl+j", "alt+t"), ("all",)),
    "keys.rename": ("keys", "rename", ("f2", "ctrl+r", "alt+r"), ("all",)),
    "keys.definition": ("keys", "definition", ("f12", "ctrl+]", "alt+g"), ("all",)),
    "keys.references": ("keys", "references", ("f7", "alt+u", "ctrl+u"), ("all",)),
    "ui.notify_needs": ("ui", "notify_needs", _BOOL, ("all", "repo")),
    "ui.notify_applied": ("ui", "notify_applied", _BOOL, ("all", "repo")),
    "ui.notify_done": ("ui", "notify_done", _BOOL, ("all", "repo")),
    "ui.desktop": ("ui", "desktop", _BOOL, ("all", "repo")),
}
for _name in harness.WORKERS:
    SETTINGS[f"workers.{_name}"] = ("workers", _name, _BOOL, ("repo",))
# the model each parent talks with in the conversation, passed to its own --model; empty means its own setting
MODEL_PARENTS = ("claude", "codex", "grok", "opencode", "omp", "pi")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,99}$")
for _name in MODEL_PARENTS:
    SETTINGS[f"parent_model.{_name}"] = ("parent_model", _name, None, ("all", "repo"))
# how much each parent reasons, passed to its own reasoning flag (claude --effort, codex model_reasoning_effort, grok
# --effort, opencode --variant, omp and pi --thinking); empty means its own setting. The levels are the ones each CLI
# documents, offered in /model; any other word of the same shape is accepted, since CLIs add levels (opencode's are
# the provider's own).
EFFORT_LEVELS = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("minimal", "low", "medium", "high", "xhigh"),
    "grok": ("low", "medium", "high"),
    "opencode": ("minimal", "low", "medium", "high", "max"),
    "omp": ("off", "minimal", "low", "medium", "high", "xhigh"),
    "pi": ("off", "minimal", "low", "medium", "high", "xhigh"),
}
EFFORT_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,19}$")
for _name in MODEL_PARENTS:
    SETTINGS[f"parent_effort.{_name}"] = ("parent_effort", _name, None, ("all", "repo"))


class SettingsError(ValueError):
    pass


def _read_key(path: Path, section: str, key: str, keep_case: bool = False) -> str:
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
            value = value.split("#", 1)[0].strip().strip('"\'')
            return value if keep_case else value.lower()
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
    if section in ("parent_model", "parent_effort"):
        return json.dumps(value)  # always a string, quoted and escaped
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
        "format_on_save": _ui(repo, "format_on_save", "false") == "true",
        "keys": {name: _read_key(ui_theme.global_path(), "keys", name) or SETTINGS[f"keys.{name}"][2][0]
                 for name in ("next_occurrence", "find_in_chat", "terminal", "rename", "definition", "references", "permission_mode")},
        "notify_needs": _ui(repo, "notify_needs", "true") == "true", "notify_applied": _ui(repo, "notify_applied", "false") == "true",
        "notify_done": _ui(repo, "notify_done", "true") == "true",
        "desktop": _ui(repo, "desktop", "false") == "true",
        "routing_table": _routing_table(parsed, installed, parsed["parent"] or "claude"),
        "models": {name: (route_model(name) or "auto") for name in harness.WORKERS},
        "parent_models": {name: parent_model_info(repo, name) for name in MODEL_PARENTS},
        "parent_efforts": {name: parent_effort_info(repo, name) for name in MODEL_PARENTS},
        "runs_today": _runs_today(repo),
        **_facts(repo),
    }


def chosen_model(repo, parent: str) -> tuple[str, str]:
    """The model chosen for the parent in the conversation, and where: this repo's, else the one for all repos."""
    here = _read_key(harness.harness_path(Path(repo)), "parent_model", parent, keep_case=True)
    if here:
        return here, "repo"
    everywhere = _read_key(ui_theme.global_path(), "parent_model", parent, keep_case=True)
    return (everywhere, "all") if everywhere else ("", "default")


def chosen_effort(repo, parent: str) -> tuple[str, str]:
    """The reasoning level chosen for the parent in the conversation, and where: this repo's, else all repos'."""
    here = _read_key(harness.harness_path(Path(repo)), "parent_effort", parent)
    if here:
        return here, "repo"
    everywhere = _read_key(ui_theme.global_path(), "parent_effort", parent)
    return (everywhere, "all") if everywhere else ("", "default")


def parent_effort_info(repo, parent: str) -> dict:
    effort, scope = chosen_effort(repo, parent)
    return {"effort": effort, "scope": scope, "levels": list(EFFORT_LEVELS.get(parent, ()))}


def own_model(parent: str) -> str:
    """The model the parent CLI's own settings name, as it writes it; empty when they name none."""
    try:
        if parent == "claude":
            model = json.loads((Path.home() / ".claude" / "settings.json").read_text()).get("model")
            return model.strip() if isinstance(model, str) else ""
        if parent == "codex":
            import child_mcp
            model = child_mcp._load_toml(Path.home() / ".codex" / "config.toml").get("model")
            return model.strip() if isinstance(model, str) else ""
    except (OSError, ValueError, AttributeError, UnicodeError):
        pass
    return ""


def model_suggestions(parent: str) -> list[str]:
    """Models to offer for a parent: claude's own aliases, and the models codex's own config names (its profiles too).
    Any other name can be typed; nothing here asks a provider."""
    out = []
    if parent == "claude":
        out = ["opus", "sonnet", "haiku"]
    elif parent == "codex":
        try:
            import child_mcp
            config = child_mcp._load_toml(Path.home() / ".codex" / "config.toml")
            out = [config.get("model")] + [p.get("model") for p in (config.get("profiles") or {}).values() if isinstance(p, dict)]
        except (OSError, ValueError, AttributeError, UnicodeError):
            out = []
    seen, result = set(), []
    for name in out:
        if isinstance(name, str) and name.strip() and name.strip() not in seen:
            seen.add(name.strip())
            result.append(name.strip())
    return result


def parent_model_info(repo, parent: str) -> dict:
    model, scope = chosen_model(repo, parent)
    return {"model": model, "scope": scope, "own": own_model(parent), "suggest": model_suggestions(parent)}


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
    if allowed is None and section == "parent_effort":
        # a reasoning level, as the parent CLI writes it; empty or "default" goes back to its own setting
        value = "" if str(value).strip().lower() in ("", "default") else str(value).strip().lower()
        if value and not EFFORT_NAME.match(value):
            raise SettingsError(f"{value!r} is not a reasoning level")
    elif allowed is None:
        # a model's name, as the parent CLI writes it: kept as typed; empty or "default" goes back to its own setting
        value = "" if str(value).strip().lower() in ("", "default") else str(value).strip()
        if value and not MODEL_NAME.match(value):
            raise SettingsError(f"{value!r} is not a model name")
    else:
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
