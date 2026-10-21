#!/usr/bin/env python3
"""The board's colour theme, as the design's Settings › General offers it: light, dark, material or dracula, plus
``rig``, the ADE mockup's own colours (the default). It is kept for all repos in ``~/.rig/config.toml`` or for one repo
in ``.rig/harness.toml``, both as ``[ui] theme = "..."``; the repo's choice wins."""
from __future__ import annotations

import os
from pathlib import Path

THEMES = ("rig", "light", "dark", "material", "dracula")
DEFAULT = "rig"


class ThemeError(ValueError):
    pass


def global_path() -> Path:
    root = os.environ.get("RIG_HOME", "").strip()
    return (Path(root) if root else Path.home() / ".rig") / "config.toml"


def repo_path(repo) -> Path:
    import harness
    return harness.harness_path(Path(repo).resolve())


def _read(path: Path) -> str:
    """The [ui] theme in one TOML file, or empty. A line parser like harness.py's: no TOML library is required."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]") and not line.startswith("[["):
            section = line[1:-1].strip()
            continue
        if section != "ui" or "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key.strip() == "theme":
            value = value.split("#", 1)[0].strip().strip('"\'').lower()
            return value if value in THEMES else ""
    return ""


def read(repo) -> dict:
    """{theme, scope}: scope is "repo", "all" or "default", saying where the theme in force comes from."""
    here = _read(repo_path(repo))
    if here:
        return {"theme": here, "scope": "repo", "themes": list(THEMES)}
    everywhere = _read(global_path())
    if everywhere:
        return {"theme": everywhere, "scope": "all", "themes": list(THEMES)}
    return {"theme": DEFAULT, "scope": "default", "themes": list(THEMES)}


def _write(path: Path, theme: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    out, section, done = [], "", False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
            if section == "ui" and not done:
                out.append(f'theme = "{theme}"')
                done = True
            section = stripped[1:-1].strip()
        elif section == "ui" and not stripped.startswith("#") and stripped.partition("=")[0].strip() == "theme":
            if not done:
                out.append(f'theme = "{theme}"')
                done = True
            continue
        out.append(line)
    if not done:
        if section == "ui":
            out.append(f'theme = "{theme}"')
        else:
            if out and out[-1].strip():
                out.append("")
            out += ["[ui]", f'theme = "{theme}"']
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(out) + "\n")
    os.replace(temporary, path)


def save(repo, theme: str, scope: str = "all") -> dict:
    """Keep ``theme`` for all repos (``scope`` "all") or for this one ("repo"). A repo choice is only written to an
    existing harness: saving a theme never creates one, which would turn Rig on for the project."""
    theme = str(theme or "").strip().lower()
    if theme not in THEMES:
        raise ThemeError(f"the theme is one of {', '.join(THEMES)}, not {theme or 'none'}")
    if scope == "repo":
        path = repo_path(repo)
        if not path.is_file():
            raise ThemeError("this project has no .rig/harness.toml; run rig init first, or save the theme for all repos")
    elif scope == "all":
        path = global_path()
    else:
        raise ThemeError("scope is all or repo")
    _write(path, theme)
    return {**read(repo), "saved": str(path)}
