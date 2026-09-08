#!/usr/bin/env python3
"""Point Grok/Codex at the Rig jobs statusline and MCP. Called from rig setup."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def section_value(text: str, section: str, key: str) -> str | None:
    current = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]") and not s.startswith("[["):
            current = s[1:-1]
            continue
        if current != section or s.startswith("#") or not s:
            continue
        body = s.split("#", 1)[0].strip()
        if body.startswith(key + "=") or body.startswith(key + " ="):
            return body.split("=", 1)[1].strip().strip('"')
    return None


def set_key(path: Path, section: str, key: str, value: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    current = None
    out = []
    replaced = False
    seen = False
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]") and not s.startswith("[["):
            if current == section and not replaced:
                out.append(f"{key} = {value}")
                replaced = True
            current = s[1:-1]
            if current == section:
                seen = True
            out.append(line)
            continue
        if current == section:
            body = s.split("#", 1)[0].strip()
            if body.startswith(key + "=") or body.startswith(key + " ="):
                out.append(f"{key} = {value}")
                replaced = True
                continue
        out.append(line)
    if current == section and not replaced:
        out.append(f"{key} = {value}")
        replaced = True
    if not replaced:
        if not seen:
            if out and out[-1].strip():
                out.append("")
            out.append(f"[{section}]")
        out.append(f"{key} = {value}")
    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def install_statusline(rig_home: Path, grok_home: Path) -> str:
    src = rig_home / "scripts" / "rig-statusline.sh"
    dest = grok_home / "rig-statusline.sh"
    if src.is_file():
        grok_home.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        dest.chmod(0o755)
    cfg = grok_home / "config.toml"
    if not cfg.is_file():
        return "skip grok statusline (no config.toml)"
    text = cfg.read_text()
    typ = section_value(text, "ui.status_line", "type") or ""
    cmd = section_value(text, "ui.status_line", "command") or ""
    ours = str(dest)
    if typ == "command" and cmd and "rig-statusline" not in cmd:
        return f"keep {cfg} [ui.status_line] (custom command)"
    set_key(cfg, "ui.status_line", "type", '"command"')
    set_key(cfg, "ui.status_line", "command", f'"{ours}"')
    set_key(cfg, "ui.status_line", "refresh_interval", "5")
    return f"set {cfg} [ui.status_line] command = rig-statusline (restart Grok)"


def install_mcp(cfg: Path, script: Path, label: str) -> str:
    if not cfg.is_file():
        return f"skip {label} mcp (no config)"
    text = cfg.read_text()
    if section_value(text, "mcp_servers.rig", "command"):
        set_key(cfg, "mcp_servers.rig", "args", f'["{script}"]')
        return f"keep {label} mcp_servers.rig (refreshed args)"
    set_key(cfg, "mcp_servers.rig", "command", '"python3"')
    set_key(cfg, "mcp_servers.rig", "args", f'["{script}"]')
    set_key(cfg, "mcp_servers.rig", "enabled", "true")
    return f"set {label} [mcp_servers.rig]  (new session to load tools)"


def main() -> int:
    rig_home = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RIG_HOME", Path.home() / ".rig"))
    grok_home = Path(os.environ.get("GROK_HOME") or (Path.home() / ".grok"))
    mcp = rig_home / "scripts" / "rig_mcp.py"
    print(install_statusline(rig_home, grok_home))
    print(install_mcp(grok_home / "config.toml", mcp, "grok"))
    print(install_mcp(Path.home() / ".codex" / "config.toml", mcp, "codex"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
