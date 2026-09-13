#!/usr/bin/env python3
"""Shared MCP fixtures for launch/child tests. Matches `rig setup` config shapes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stub_path(extra: Path | None = None) -> str:
    parts = [
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        str(Path(sys.executable).resolve().parent),
    ]
    if extra:
        parts.insert(0, str(extra))
    return ":".join(parts)


def fake_bin(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def seed_installed_mcp(home: Path, *, launcher: Path | None = None) -> Path:
    """Write setup-shaped Rig MCP configs under home so tests match doctor."""
    home = Path(home)
    script = Path(launcher) if launcher is not None else (home / "rig-mcp.sh")
    script.parent.mkdir(parents=True, exist_ok=True)
    if not script.exists():
        script.write_text("#!/bin/sh\nexec python3 -u \"$0\" \"$@\"\n")
        script.chmod(0o755)
    marker = str(script)
    grok = home / ".grok"
    grok.mkdir(parents=True, exist_ok=True)
    (grok / "config.toml").write_text(
        f'[mcp_servers.rig]\ncommand = "{marker}"\nargs = []\nenabled = true\n'
    )
    codex = home / ".codex"
    codex.mkdir(parents=True, exist_ok=True)
    (codex / "config.toml").write_text(
        f'[mcp_servers.rig]\ncommand = "{marker}"\nargs = []\nenabled = true\n'
    )
    oc = home / ".config" / "opencode"
    oc.mkdir(parents=True, exist_ok=True)
    (oc / "opencode.json").write_text(json.dumps({
        "mcp": {"rig": {"type": "local", "command": [marker], "enabled": True}},
    }, indent=2) + "\n")
    omp = home / ".omp"
    omp.mkdir(parents=True, exist_ok=True)
    (omp / "mcp.json").write_text(json.dumps({"mcpServers": {"rig": {"command": marker}}}, indent=2) + "\n")
    pi = home / ".pi" / "agent"
    pi.mkdir(parents=True, exist_ok=True)
    (pi / "mcp.json").write_text(json.dumps({"mcpServers": {"rig": {"command": marker}}}, indent=2) + "\n")
    (pi / "settings.json").write_text(json.dumps({"packages": ["pi-mcp-adapter"]}, indent=2) + "\n")
    agy = home / ".gemini" / "config"
    agy.mkdir(parents=True, exist_ok=True)
    (agy / "mcp_config.json").write_text(json.dumps({"mcpServers": {"rig": {"command": marker}}}, indent=2) + "\n")
    return script


def child_inbox_handshake(*, root: Path | None = None, env: dict | None = None) -> str:
    """Real child JSON-RPC tools/call rig_job_inbox against scripts/rig_mcp.py."""
    root = Path(root) if root is not None else ROOT
    mcp = root / "scripts" / "rig_mcp.py"
    merged = os.environ.copy()
    if env:
        merged.update(env)
    proc = subprocess.Popen(
        [sys.executable, "-u", str(mcp)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=merged,
    )
    assert proc.stdin is not None and proc.stdout is not None

    def send(obj: dict) -> None:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **obj}) + "\n")
        proc.stdin.flush()

    def recv() -> dict:
        line = proc.stdout.readline()
        if not line:
            err = (proc.stderr.read() if proc.stderr else "") or ""
            raise RuntimeError(f"child MCP handshake: no response ({err.strip()})")
        return json.loads(line)

    try:
        send({
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "fake-worker", "version": "1"},
            },
        })
        recv()
        send({"method": "notifications/initialized"})
        send({"id": 2, "method": "tools/call", "params": {"name": "rig_job_inbox", "arguments": {}}})
        out = recv()
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    result = out.get("result") or out
    if result.get("isError"):
        content = result.get("content") or []
        text = content[0].get("text") if content and isinstance(content[0], dict) else "inbox failed"
        raise RuntimeError(str(text))
    content = result.get("content") or []
    if content and isinstance(content[0], dict):
        return str(content[0].get("text") or "")
    return ""


def inbox_handshake_prelude(root: Path | None = None) -> str:
    """Python source a fake live worker runs before work (real JSON-RPC inbox)."""
    root = Path(root) if root is not None else ROOT
    mcp = str(root / "scripts" / "rig_mcp.py")
    return (
        "def _rig_inbox_handshake():\n"
        "    import json, os, subprocess, sys\n"
        f"    mcp = {mcp!r}\n"
        "    proc = subprocess.Popen([sys.executable, '-u', mcp], stdin=subprocess.PIPE, "
        "stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy())\n"
        "    def send(obj):\n"
        "        proc.stdin.write(json.dumps({'jsonrpc': '2.0', **obj}) + '\\n')\n"
        "        proc.stdin.flush()\n"
        "    def recv():\n"
        "        line = proc.stdout.readline()\n"
        "        if not line:\n"
        "            raise SystemExit('child MCP handshake: no response')\n"
        "        return json.loads(line)\n"
        "    send({'id': 1, 'method': 'initialize', 'params': {"
        "'protocolVersion': '2025-03-26', 'capabilities': {}, "
        "'clientInfo': {'name': 'fake-worker', 'version': '1'}}})\n"
        "    recv()\n"
        "    send({'method': 'notifications/initialized'})\n"
        "    send({'id': 2, 'method': 'tools/call', 'params': "
        "{'name': 'rig_job_inbox', 'arguments': {}}})\n"
        "    out = recv()\n"
        "    try:\n"
        "        proc.stdin.close()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        proc.wait(timeout=5)\n"
        "    except Exception:\n"
        "        proc.kill()\n"
        "    result = out.get('result') or out\n"
        "    if result.get('isError'):\n"
        "        content = result.get('content') or []\n"
        "        text = content[0].get('text') if content and isinstance(content[0], dict) else 'inbox failed'\n"
        "        raise SystemExit(str(text))\n"
        "_rig_inbox_handshake()\n"
    )
