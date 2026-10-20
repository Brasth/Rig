#!/usr/bin/env python3
"""The board's Settings tab: what this repository's .rig/harness.toml makes effective, ready to render.

Read-only by design. The board shows what Rig will do and says where to change it; it has no write path to
configuration, so a setting can only be changed where it is reviewed and versioned.
"""
from __future__ import annotations

from pathlib import Path

import harness

VIEW_VERSION = 1


def _line(text: str, tone: str = "") -> dict:
    return {"text": text, "tone": tone}


def _row(key: str, value: object, tone: str = "") -> dict:
    return _line(f"{key:<11}{value}", tone)


def view(repo: Path) -> dict:
    repo = Path(repo).resolve()
    path = harness.harness_path(repo)
    parsed = harness.parse_harness(path)
    project = parsed["project"]
    attention = False
    sections = []

    state_tone = "" if project["enabled"] else "attention"
    lines = [_row("file", ".rig/harness.toml" if path.is_file() else ".rig/harness.toml (not created: run rig init)"),
             _row("state", project["state"] + (f" ({project['error']})" if project.get("error") else ""), state_tone)]
    attention = attention or not project["enabled"]
    lines.append(_row("parent", parsed["parent"] or "not set (pass --parent to rig shell)"))
    sections.append({"title": "This project", "lines": lines})

    flags = parsed["workers"]
    on = [name for name in harness.WORKERS if flags.get(name) == "true"]
    off = [name for name in harness.WORKERS if flags.get(name) != "true"]
    lines = [_line("● " + name, "ok") for name in on] or [_line("no worker is enabled", "attention")]
    attention = attention or not on
    if off:
        lines.append(_line("off: " + ", ".join(off)))
    lines.append(_line("Models come from rig pick; the parent's model is chosen in its own CLI."))
    sections.append({"title": "Who does what", "lines": lines})

    routing = parsed["routing"]
    sections.append({"title": "Routing", "lines": [
        _row("mode", routing["mode"]), _row("engine", routing["engine"] or "local"),
        _row("policy", routing["local_policy"] or "scored-v1"), _row("objective", routing["objective"] or "balanced")]})

    orchestration = parsed["orchestration"]
    sections.append({"title": "Orchestration", "lines": [
        _row("mode", orchestration["mode"]), _row("max nodes", orchestration["max_nodes"])]})

    queue = parsed["queue"]
    lines = [_row("running", f"{queue['max_running']} at once"),
             _row("per worker", queue["max_per_worker"] or "no limit")]
    lines += [_row("  " + name, f"{limit} at once") for name, limit in sorted(queue["per_worker"].items())]
    sections.append({"title": "Queue", "lines": lines})

    sandbox = parsed["sandbox"]
    lines = [_row("provider", sandbox["provider"])]
    lines += [_row("  " + name, "credentials mounted read-only" if choice == "mount-ro" else "no credentials mounted")
              for name, choice in sorted(sandbox["credentials"].items())]
    if sandbox["error"]:
        lines.append(_line("! " + sandbox["error"], "attention"))
        attention = True
    sections.append({"title": "Sandbox", "lines": lines})

    worktrees = parsed["worktrees"]
    lines = [_row("mode", worktrees["mode"]), _row("container", worktrees["container"])]
    if worktrees["error"]:
        lines.append(_line("! " + worktrees["error"], "attention"))
        attention = True
    sections.append({"title": "Worktrees", "lines": lines})

    tools = [_row("mcp", parsed["mcp"]["profile"]),
             _row("computer", "on" if parsed["computer_use"]["enabled"] else "off"),
             _row("chat", "on (experiment)" if parsed["experiments"]["chat"] else "off")]
    if parsed["mcp"]["error"]:
        tools.append(_line("! " + parsed["mcp"]["error"], "attention"))
        attention = True
    sections.append({"title": "Tools", "lines": tools})

    sections.append({"title": "Changing these", "lines": [
        _line("Read-only here. Edit .rig/harness.toml; the board picks it up on refresh."),
        _line("Machine-wide opt-ins (rig setup) live in ~/.rig/.")]})
    return {"version": VIEW_VERSION, "attention": attention, "sections": sections}


__all__ = ["VIEW_VERSION", "view"]
