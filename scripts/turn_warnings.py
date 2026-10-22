#!/usr/bin/env python3
"""One-line warnings for what is about to go wrong with the next message, shown above the reply box in rig shell.

They are the cheap, known causes of a turn that fails or answers oddly: an MCP server that is not signed in, a project's
own codex config that codex ignores because it does not trust the project, a parent that is signed out. `current(repo)`
answers at once from a cache; the checks that run a command (`codex mcp list`, `claude auth status`) refresh in the
background every few minutes, so asking never waits on them.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

REFRESH_SECONDS = 300
_cache: dict[str, tuple[float, list[str]]] = {}
_running: set[str] = set()
_lock = threading.Lock()


def compute(repo) -> list[str]:
    """The warnings for this project, now. Runs the parent's own commands, so it can take seconds."""
    import shell_doctor
    repo = Path(repo).resolve()
    out: list[str] = []
    try:
        for row in shell_doctor.codex_servers_signed_out(repo):
            name = row["id"].removeprefix("codex-mcp-")
            out.append(f"{name} is not signed in · type /mcp login {name}")
        row = shell_doctor.signed_out(repo)
        if row:
            out.append("claude is not signed in · type /login")
        out.extend(untrusted_project_config(repo))
    except Exception:  # noqa: BLE001 - a check that breaks is never a reason to warn about the check
        pass
    return out


def untrusted_project_config(repo: Path) -> list[str]:
    """The project has its own .codex/config.toml and codex does not trust the project: codex then ignores that file."""
    import harness
    try:
        if harness.parse_harness(harness.harness_path(repo))["parent"] != "codex":
            return []
    except (OSError, ValueError, KeyError):
        return []
    if not (repo / ".codex" / "config.toml").is_file():
        return []
    import child_mcp
    try:
        projects = child_mcp._load_toml(Path.home() / ".codex" / "config.toml").get("projects") or {}
    except (OSError, ValueError, AttributeError):
        projects = {}  # no user config: nothing trusts the project
    if str((projects.get(str(repo)) or {}).get("trust_level") or "") == "trusted":
        return []
    return ["this project's .codex/config.toml is ignored: codex does not trust the project yet · open it in codex once and choose trust"]


def _refresh(key: str, repo) -> None:
    try:
        value = compute(repo)
    finally:
        with _lock:
            _running.discard(key)
    with _lock:
        _cache[key] = (time.monotonic(), value)


def current(repo) -> list[str]:
    """The cached warnings; a stale or missing entry starts a background refresh and answers with what is there."""
    key = str(Path(repo).resolve())
    with _lock:
        at, value = _cache.get(key, (0.0, []))
        stale = time.monotonic() - at > REFRESH_SECONDS
        start = stale and key not in _running
        if start:
            _running.add(key)
    if start:
        threading.Thread(target=_refresh, args=(key, repo), daemon=True).start()
    return list(value)
