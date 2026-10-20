#!/usr/bin/env python3
"""A stand-in worker CLI for adapter conformance checks. It is installed under the adapter's binary name.

It behaves like a real worker CLI would at the points Rig depends on: it finds the job-scoped MCP config from its own
argv or environment (never from Rig internals), starts the MCP server that file names, completes the child handshake
(first call: rig_job_inbox), records what it was given, and ends with output the adapter's decoder can read.
It is not a model and edits nothing unless RIG_STANDIN_EDIT names a file.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def find_mcp_config():
    candidates = list(sys.argv[1:]) + list(os.environ.values())
    for value in candidates:
        for part in str(value).split("="):
            if part.endswith("mcp.json") and os.path.isfile(part):
                return part
    return ""


def handshake(path):
    payload = json.loads(Path(path).read_text())
    servers = payload.get("mcpServers") or {}
    server = servers.get("rig")
    if not server:
        raise SystemExit("standin: the MCP config has no rig server")
    env = {**os.environ, **(server.get("env") or {})}
    proc = subprocess.Popen([server["command"], *server.get("args", [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)

    def send(obj):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **obj}) + "\n")
        proc.stdin.flush()

    def recv():
        line = proc.stdout.readline()
        if not line:
            raise SystemExit("standin: child MCP handshake: no response")
        return json.loads(line)

    send({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                       "clientInfo": {"name": "adapter-standin", "version": "1"}}})
    recv()
    send({"method": "notifications/initialized"})
    send({"id": 2, "method": "tools/call", "params": {"name": "rig_job_inbox", "arguments": {}}})
    reply = recv()
    proc.stdin.close()
    proc.wait(timeout=10)
    return sorted(servers), reply


def main():
    job_dir = Path(os.environ.get("RIG_JOB_DIR", "."))
    config = find_mcp_config()
    record = {"argv": sys.argv[1:], "cwd": os.getcwd(), "mcp_config": config, "env_keys": sorted(k for k in os.environ if k.startswith("RIG_"))}
    if config:
        record["servers"], reply = handshake(config)
        record["handshake"] = "result" in reply and "error" not in reply
    else:
        record["handshake"] = False
    (job_dir / "standin.json").write_text(json.dumps(record, indent=1) + "\n")
    if (job_dir / "standin-mode").exists() and (job_dir / "standin-mode").read_text().strip() == "hang":
        child = subprocess.Popen(["sleep", "300"])
        (job_dir / "standin-pids").write_text(f"{os.getpid()} {child.pid}")
        time.sleep(300)
    edit = os.environ.get("RIG_STANDIN_EDIT")
    if edit:
        Path(edit).write_text(Path(edit).read_text() + "worker line\n")
    # The checker sets the exact last line its adapter's output decoder reads.
    sys.stdout.write(os.environ.get("RIG_STANDIN_OUTPUT", "adapter standin done") + "\n")


if __name__ == "__main__":
    main()
