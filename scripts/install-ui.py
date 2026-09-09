#!/usr/bin/env python3
"""Point parent CLIs at the Rig jobs statusline and MCP. Called from rig setup."""
from __future__ import annotations

import json
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


def ensure_cfg(path: Path) -> bool:
    """Create an empty config file if missing. Return True if it already existed."""
    if path.is_file():
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return False


def install_statusline(rig_home: Path, grok_home: Path) -> str:
    src = rig_home / "scripts" / "rig-statusline.sh"
    dest = grok_home / "rig-statusline.sh"
    if src.is_file():
        grok_home.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        dest.chmod(0o755)
    cfg = grok_home / "config.toml"
    ensure_cfg(cfg)
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
    created = not ensure_cfg(cfg)
    launcher = script.with_name("rig-mcp.sh")
    if not launcher.is_file():
        launcher = script
    text = cfg.read_text()
    existed = bool(section_value(text, "mcp_servers.rig", "command"))
    set_key(cfg, "mcp_servers.rig", "command", f'"{launcher}"')
    set_key(cfg, "mcp_servers.rig", "args", "[]")
    set_key(cfg, "mcp_servers.rig", "enabled", "true")
    set_key(cfg, "mcp_servers.rig", "startup_timeout_sec", "8")
    if existed:
        return f"keep {label} mcp_servers.rig (refreshed launcher)"
    extra = " (created config.toml)" if created else ""
    return f"set {label} [mcp_servers.rig]{extra}  (fully quit {label} to load tools)"


def mcp_launcher(script: Path) -> Path:
    launcher = script.with_name("rig-mcp.sh")
    if not launcher.is_file():
        launcher = script
    return launcher.resolve()


def strip_jsonc(text: str) -> str:
    """Strip // and /* */ comments. Strings stay intact."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    escape = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                i += 2
                while i < n and text[i] not in "\n\r":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i = i + 2 if i + 1 < n else n
                continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_trailing_commas(text: str) -> str:
    """Drop JSONC trailing commas before } or ]. Strings stay intact."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    escape = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def load_json_object(path: Path) -> tuple[dict, bool]:
    """Return (object, existed). Missing or empty file → ({}, False)."""
    if not path.is_file():
        return {}, False
    raw = path.read_text()
    if not raw.strip():
        return {}, False
    try:
        data = json.loads(strip_trailing_commas(strip_jsonc(raw)))
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, True


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def install_opencode_mcp(cfg: Path, script: Path) -> str:
    created = not cfg.is_file()
    data, existed_file = load_json_object(cfg)
    launcher = str(mcp_launcher(script))
    entry = {"type": "local", "command": [launcher], "enabled": True}
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
        data["mcp"] = mcp
    servers = mcp.get("servers")
    if isinstance(servers, dict):
        existed = "rig" in servers
        servers["rig"] = entry
        key = "mcp.servers.rig"
    else:
        existed = "rig" in mcp
        mcp["rig"] = entry
        key = "mcp.rig"
    write_json(cfg, data)
    if existed:
        return f"keep opencode {key} (refreshed launcher)"
    extra = " (created opencode.json)" if created or not existed_file else ""
    return f"set opencode {key}{extra}  (fully quit opencode to load tools)"


def install_mcp_servers_json(cfg: Path, script: Path, label: str) -> str:
    created = not cfg.is_file()
    data, existed_file = load_json_object(cfg)
    launcher = str(mcp_launcher(script))
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    existed = "rig" in servers
    servers["rig"] = {"command": launcher}
    write_json(cfg, data)
    if existed:
        return f"keep {label} mcpServers.rig (refreshed launcher)"
    extra = f" (created {cfg.name})" if created or not existed_file else ""
    return f"set {label} mcpServers.rig{extra}  (fully quit {label} to load tools)"


def install_omp_mcp(cfg: Path, script: Path) -> str:
    return install_mcp_servers_json(cfg, script, "omp")


def install_pi_mcp(cfg: Path, script: Path) -> str:
    return install_mcp_servers_json(cfg, script, "pi")


def install_agy_mcp(cfg: Path, script: Path) -> str:
    return install_mcp_servers_json(cfg, script, "agy")


def refresh_codex_agents() -> str:
    agents = Path.home() / ".codex" / "agents"
    wanted = {
        "explorer.toml": ("gpt-5.3-codex-mini", "low"),
        "worker.toml": ("gpt-5.6-luna", "low"),
        "bulk.toml": ("gpt-5.6-luna", "low"),
    }
    changed = []
    for name, (model, effort) in wanted.items():
        path = agents / name
        if not path.is_file():
            continue
        text = path.read_text()
        if "You are a Rig" not in text:
            continue
        lines = text.splitlines()
        out = []
        has_effort = False
        for line in lines:
            s = line.strip()
            if s.startswith("model ="):
                line = f'model = "{model}"'
            if s.startswith("model_reasoning_effort"):
                line = f'model_reasoning_effort = "{effort}"'
                has_effort = True
            out.append(line)
        if not has_effort:
            inserted = []
            for line in out:
                inserted.append(line)
                if line.strip().startswith("model ="):
                    inserted.append(f'model_reasoning_effort = "{effort}"')
            out = inserted
        new = "\n".join(out) + "\n"
        if new != text:
            path.write_text(new)
            changed.append(name)
    if changed:
        return "updated ~/.codex/agents " + ", ".join(changed)
    return "keep ~/.codex/agents models"


def main() -> int:
    rig_home = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RIG_HOME", Path.home() / ".rig"))
    grok_home = Path(os.environ.get("GROK_HOME") or (Path.home() / ".grok"))
    mcp = rig_home / "scripts" / "rig_mcp.py"
    print(install_statusline(rig_home, grok_home))
    print(install_mcp(grok_home / "config.toml", mcp, "grok"))
    print(install_mcp(Path.home() / ".codex" / "config.toml", mcp, "codex"))
    print(refresh_codex_agents())
    oc = Path(
        os.environ.get("OPENCODE_CONFIG")
        or (Path.home() / ".config" / "opencode" / "opencode.json")
    )
    print(install_opencode_mcp(oc, mcp))
    omp = Path(os.environ.get("OMP_MCP") or (Path.home() / ".omp" / "mcp.json"))
    print(install_omp_mcp(omp, mcp))
    pi_dir = Path(
        os.environ.get("PI_CODING_AGENT_DIR")
        or os.environ.get("PI_AGENT_DIR")
        or (Path.home() / ".pi" / "agent")
    )
    print(install_pi_mcp(pi_dir / "mcp.json", mcp))
    agy = Path(
        os.environ.get("AGY_MCP")
        or (Path.home() / ".gemini" / "config" / "mcp_config.json")
    )
    print(install_agy_mcp(agy, mcp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
